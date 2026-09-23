"""MP4 recording helpers, shared by the sim host and the real aircraft.

Records a clip by polling a frame source (the drone camera, the engine IPC, or a
vantage camera) and encoding the frames to an MP4 with PyAV (``av``, already in
``drone/common/requirements.txt``). The resulting bytes are uploaded via
``drone_sdk.upload_video_bytes`` on hardware or ``cloud_creds.upload_mp4_to_s3``
in the sim, so the app receives a ``video_urls`` entry exactly like
``image_urls``.

These functions record for a fixed duration and block while doing it, which
suits a one-shot "take a 5 second clip" verb. For capture that has to run
*concurrently* with flight — recording while the aircraft flies a pattern — see
``media_recorder.MediaRecorder``, which wraps ``encode_mp4`` in a background
thread.

Lives in ``drone/common/`` (it used to be in ``drone/sim/``) because install
scripts copy ``drone/common/*.py`` flat into ``~/drone-api/``, so a recorder
under ``drone/sim/`` could never reach real hardware. ``drone/sim/video_record.py``
is now a shim re-exporting this module.

Kept dependency-light and importable without ``av`` (the encode call imports it
lazily) so unit tests can exercise the polling/timing logic on a laptop.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

import numpy as np


def record_frames(grab_rgb: Callable[[], Optional[np.ndarray]], seconds: float = 5.0,
                  fps: int = 15, max_frames: int = 900) -> list[np.ndarray]:
    """Poll ``grab_rgb`` at ~``fps`` for ``seconds`` and return RGB frames.

    ``grab_rgb`` returns an (H,W,3) uint8 RGB array or None (skipped). Caps at
    ``max_frames`` so a stuck source can't grow unbounded.
    """
    frames: list[np.ndarray] = []
    interval = 1.0 / max(1, fps)
    deadline = time.time() + max(0.1, float(seconds))
    while time.time() < deadline and len(frames) < max_frames:
        t = time.time()
        rgb = grab_rgb()
        if rgb is not None and getattr(rgb, "size", 0):
            frames.append(np.ascontiguousarray(rgb[:, :, :3]))
        dt = interval - (time.time() - t)
        if dt > 0:
            time.sleep(dt)
    return frames


def encode_mp4(frames: list[np.ndarray], fps: int = 15) -> bytes:
    """Encode RGB frames to H.264 MP4 bytes (faststart, yuv420p)."""
    if not frames:
        return b""
    import io
    import av  # lazy: only needed when actually encoding

    h, w = frames[0].shape[:2]
    # H.264 needs even dimensions
    w -= w % 2
    h -= h % 2
    buf = io.BytesIO()
    container = av.open(buf, mode="w", format="mp4")
    stream = container.add_stream("h264", rate=fps)
    stream.width, stream.height, stream.pix_fmt = w, h, "yuv420p"
    stream.options = {"movflags": "faststart"}
    for f in frames:
        frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(f[:h, :w, :3]),
                                           format="rgb24")
        for pkt in stream.encode(frame):
            container.mux(pkt)
    for pkt in stream.encode():  # flush
        container.mux(pkt)
    container.close()
    return buf.getvalue()


def record_mp4(grab_rgb: Callable[[], Optional[np.ndarray]], seconds: float = 5.0,
               fps: int = 15) -> bytes:
    """Convenience: record then encode in one call."""
    return encode_mp4(record_frames(grab_rgb, seconds, fps), fps)
