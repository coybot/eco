"""MediaRecorder — capture that runs *while* the vehicle flies.

``video_record.record_frames`` blocks for a fixed duration, which is right for a
one-shot "take a 5 second clip" but useless for "fly this rectangle and take a
video of it": the flight and the capture have to overlap. This module wraps the
same encoder in a background thread so a mission can bracket any number of
flight phases between ``start()`` and ``stop()``.

Two things it deliberately does NOT do:

* It does not open the camera. ``frame_source`` is a callable — in practice
  ``backends.Backend.capture_frame``, which is already implemented for hardware
  (via ``PerceptionService``, the shared owner of the device, falling through to
  the SDK camera) and for the Godot sim. One recorder therefore covers quad,
  rover, fixed-wing and sim with no per-vehicle branching, and — more
  importantly — it does not fight the other two things that want the camera.
  ``drone_sdk.capture_photo`` grabs and immediately releases the device
  precisely so ``video_producer``'s WebRTC subprocess can reclaim it, and
  ``daemon.on_chat_command`` stops that subprocess before running a command.
  A recorder that opened the device itself would have to re-litigate all of it.

* It does not upload. ``stop()`` hands back bytes (video) or paths (photos) and
  the caller decides — ``drone_sdk`` on hardware, ``cloud_creds`` in the sim.

Everything is bounded. ``max_seconds`` and ``max_frames`` are both hard stops,
because the thread outlives the phase that started it: a mission that aborts
mid-pattern must not be able to fill the companion computer's disk or RAM while
nobody is looking.
"""

from __future__ import annotations

import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Union

logger = logging.getLogger(__name__)

# One RGB frame at 1280x720x3 is ~2.7 MB, so 900 frames is ~2.5 GB if the buffer
# is never flushed. The default is far below that; it exists so that a caller
# who asks for a long recording at a high frame rate gets a truncated video
# rather than an OOM-killed daemon.
DEFAULT_MAX_FRAMES = 900
DEFAULT_MAX_SECONDS = 120.0

MediaArtifact = Union[bytes, Path]


class MediaRecorder:
    """Background capture. Not reusable: build one per recording."""

    def __init__(
        self,
        frame_source: Callable[[], Any],
        mode: str = "video",
        fps: float = 6.0,
        interval_s: float = 3.0,
        max_seconds: float = DEFAULT_MAX_SECONDS,
        max_frames: int = DEFAULT_MAX_FRAMES,
        out_dir: Optional[Path] = None,
    ) -> None:
        if mode not in ("video", "photos"):
            raise ValueError(f"mode must be 'video' or 'photos', got {mode!r}")
        self.mode = mode
        self.fps = max(float(fps), 0.1)
        self.interval_s = max(float(interval_s), 0.1)
        self.max_seconds = max(float(max_seconds), 0.0)
        self.max_frames = max(int(max_frames), 1)
        self._frame_source = frame_source
        self._out_dir = Path(out_dir) if out_dir else None

        self._frames: List[Any] = []
        self._photo_paths: List[Path] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._stopped = False
        self._started_at: Optional[float] = None
        self._artifacts: List[MediaArtifact] = []
        self.error: Optional[str] = None

    # -- lifecycle ------------------------------------------------------- #

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("MediaRecorder already started; build a new one")
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=self._run, name="media-recorder", daemon=True
        )
        self._thread.start()
        logger.info(
            "Recording started (mode=%s fps=%s interval=%ss max=%ss)",
            self.mode, self.fps, self.interval_s, self.max_seconds,
        )

    def stop(self) -> List[MediaArtifact]:
        """Stop, encode, and return artifacts. Idempotent.

        Idempotency matters more than it looks: ``stop_recording`` is a mission
        phase, but ``MissionLoop._cleanup`` also stops any live recorder so an
        aborted mission still delivers what it shot. A normal mission therefore
        calls this twice, and the second call must not re-encode or raise.
        """
        with self._lock:
            if self._stopped:
                return list(self._artifacts)
            self._stopped = True

        self._stop_event.set()
        if self._thread is not None:
            # Generous but finite: a slow frame_source can block one poll, but
            # never the whole shutdown.
            self._thread.join(timeout=10.0)
            if self._thread.is_alive():
                logger.warning("Recorder thread did not exit within 10s")

        self._artifacts = self._finalize()
        logger.info("Recording stopped: %d artifact(s)", len(self._artifacts))
        return list(self._artifacts)

    @property
    def is_recording(self) -> bool:
        return self._thread is not None and not self._stopped

    @property
    def elapsed_s(self) -> float:
        return 0.0 if self._started_at is None else time.time() - self._started_at

    # -- internals ------------------------------------------------------- #

    def _run(self) -> None:
        period = 1.0 / self.fps if self.mode == "video" else self.interval_s
        deadline = self._started_at + self.max_seconds if self.max_seconds else None
        next_tick = time.time()
        while not self._stop_event.is_set():
            now = time.time()
            if deadline is not None and now >= deadline:
                logger.info("Recording hit max_seconds=%.1f", self.max_seconds)
                break
            if len(self._frames) + len(self._photo_paths) >= self.max_frames:
                logger.info("Recording hit max_frames=%d", self.max_frames)
                break

            try:
                self._capture_one()
            except Exception as e:
                # One bad frame must not end the recording — a camera hiccup
                # mid-pattern should cost a frame, not the whole clip.
                logger.warning("Frame capture failed: %s", e)

            next_tick += period
            # If capture ran long, don't spin trying to catch up on a backlog we
            # can never serve; just resync to now.
            sleep_for = next_tick - time.time()
            if sleep_for < 0:
                next_tick = time.time()
                sleep_for = 0.0
            if self._stop_event.wait(timeout=sleep_for):
                break

    def _capture_one(self) -> None:
        frame = self._frame_source()
        rgb = _extract_rgb(frame)
        if rgb is None:
            return
        if self.mode == "video":
            self._frames.append(rgb)
        else:
            self._photo_paths.append(self._write_jpeg(rgb))

    def _write_jpeg(self, rgb) -> Path:
        import cv2

        if self._out_dir is not None:
            self._out_dir.mkdir(parents=True, exist_ok=True)
            path = self._out_dir / f"capture_{len(self._photo_paths):04d}.jpg"
        else:
            path = Path(tempfile.mktemp(suffix=".jpg", prefix="drone_rec_"))
        # Same conversion and quality as drone_sdk.capture_photo, so a burst
        # still and a one-off still are the same picture.
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(path), bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return path

    def _finalize(self) -> List[MediaArtifact]:
        if self.mode == "photos":
            return list(self._photo_paths)
        if not self._frames:
            return []
        try:
            from video_record import encode_mp4
        except ImportError:  # pragma: no cover - packaging fallback
            from drone.common.video_record import encode_mp4
        try:
            data = encode_mp4(self._frames, fps=int(round(self.fps)) or 1)
        except Exception as e:
            self.error = f"encode failed: {e}"
            logger.error("MP4 encode failed: %s", e)
            return []
        finally:
            # Release the buffer whatever happened — these are the biggest
            # allocations in the daemon and the recorder object may outlive the
            # phase that owns it.
            self._frames = []
        return [data] if data else []


def _extract_rgb(frame: Any):
    """Pull an HxWx3 RGB array out of whatever the backend handed back.

    ``HardwareBackend.capture_frame`` returns a ``CameraFrame`` (``.rgb``),
    ``SimBackend.capture_frame`` returns the array directly, and either can
    return None when the camera is momentarily unavailable.
    """
    if frame is None:
        return None
    rgb = getattr(frame, "rgb", frame)
    if rgb is None:
        return None
    if getattr(rgb, "ndim", 0) != 3 or rgb.shape[2] < 3:
        return None
    return rgb
