"""Background capture for the start_recording/stop_recording phases.

The failure mode this exists for is resource leakage, not wrong pixels. The
recorder runs on its own thread and holds the camera, and it outlives the
phase that started it: a mission that aborts mid-pattern, or generated code
that raises between start and stop, leaves it running. So the bounds
(max_seconds, max_frames), the idempotency of stop(), and the guarantee that
stop() always finalises are the things under test - not the codec.

av and cv2 are stubbed throughout: CI installs only pyyaml/numpy/pytest (see
.github/workflows/e2e-fast.yml), and the existing sim-side test of this code
(drone/sim/ishmael/tests/test_office_chair.py) likewise covers the polling
logic rather than the encoder.
"""

import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_recorder import MediaRecorder, _extract_rgb  # noqa: E402


def _frame(h=8, w=8):
    return np.zeros((h, w, 3), dtype=np.uint8)


@pytest.fixture
def stub_encoder(monkeypatch):
    """Replace video_record.encode_mp4 so no PyAV is needed. Returns the list
    of frame-batches it was handed."""
    import video_record

    calls = []

    def fake_encode(frames, fps=15):
        calls.append(list(frames))
        return b"MP4" + bytes([len(frames) % 256])

    monkeypatch.setattr(video_record, "encode_mp4", fake_encode)
    return calls


@pytest.fixture
def stub_cv2(monkeypatch, tmp_path):
    """Minimal cv2 stand-in; records what would have been written."""
    written = []

    module = types.SimpleNamespace(
        COLOR_RGB2BGR=4,
        IMWRITE_JPEG_QUALITY=1,
        cvtColor=lambda img, code: img,
        imwrite=lambda path, img, params=None: (written.append((path, params)),
                                                Path(path).write_bytes(b"jpeg"))[0],
    )
    monkeypatch.setitem(sys.modules, "cv2", module)
    return written


# --- the bounds, which is what keeps a leaked recorder harmless ----------- #

def test_max_seconds_stops_the_thread_without_stop_being_called(stub_encoder):
    r = MediaRecorder(_frame, mode="video", fps=50, max_seconds=0.2)
    r.start()
    time.sleep(0.6)
    assert not r._thread.is_alive(), "recorder must stop itself at max_seconds"
    r.stop()


def test_max_frames_caps_the_buffer(stub_encoder):
    r = MediaRecorder(_frame, mode="video", fps=200, max_seconds=30, max_frames=5)
    r.start()
    time.sleep(0.5)
    r.stop()
    assert len(stub_encoder) == 1
    assert len(stub_encoder[0]) <= 5


def test_stop_returns_encoded_bytes(stub_encoder):
    r = MediaRecorder(_frame, mode="video", fps=50, max_seconds=5)
    r.start()
    time.sleep(0.15)
    artifacts = r.stop()
    assert len(artifacts) == 1
    assert isinstance(artifacts[0], bytes)
    assert stub_encoder[0], "frames should have been captured"


def test_stop_is_idempotent_and_does_not_re_encode(stub_encoder):
    """The normal mission path calls stop() twice: once from the
    stop_recording phase and once from MissionLoop._cleanup."""
    r = MediaRecorder(_frame, mode="video", fps=50, max_seconds=5)
    r.start()
    time.sleep(0.15)
    first = r.stop()
    second = r.stop()
    assert first == second
    assert len(stub_encoder) == 1, "second stop() must not re-encode"


def test_stop_without_start_is_safe(stub_encoder):
    assert MediaRecorder(_frame).stop() == []


def test_start_twice_is_refused(stub_encoder):
    r = MediaRecorder(_frame, max_seconds=1)
    r.start()
    with pytest.raises(RuntimeError):
        r.start()
    r.stop()


def test_frame_buffer_is_released_even_when_encoding_fails(monkeypatch):
    """The frame buffer is the biggest allocation in the daemon, and the
    recorder object can outlive the phase that owns it."""
    import video_record

    def boom(frames, fps=15):
        raise RuntimeError("no codec")

    monkeypatch.setattr(video_record, "encode_mp4", boom)
    r = MediaRecorder(_frame, mode="video", fps=50, max_seconds=5)
    r.start()
    time.sleep(0.15)
    assert r.stop() == []
    assert r._frames == []
    assert "no codec" in (r.error or "")


# --- photos mode ----------------------------------------------------------- #

def test_photos_mode_honours_the_interval(stub_cv2, tmp_path):
    r = MediaRecorder(_frame, mode="photos", interval_s=0.1, max_seconds=0.45,
                      out_dir=tmp_path)
    r.start()
    time.sleep(0.7)
    artifacts = r.stop()
    # ~5 at 0.1s over 0.45s; allow slack for scheduler jitter, but it must be
    # bounded by the interval rather than free-running.
    assert 3 <= len(artifacts) <= 7, len(artifacts)
    assert all(isinstance(a, Path) for a in artifacts)


def test_photos_use_the_same_jpeg_quality_as_capture_photo(stub_cv2, tmp_path):
    r = MediaRecorder(_frame, mode="photos", interval_s=0.05, max_seconds=0.2,
                      out_dir=tmp_path)
    r.start()
    time.sleep(0.4)
    r.stop()
    assert stub_cv2, "no photo written"
    _, params = stub_cv2[0]
    assert params == [1, 85], "quality must match drone_sdk.capture_photo"


# --- robustness of the frame source --------------------------------------- #

def test_a_failing_frame_source_costs_a_frame_not_the_clip(stub_encoder):
    """A camera hiccup mid-pattern should not end the recording."""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] % 2:
            raise RuntimeError("camera busy")
        return _frame()

    r = MediaRecorder(flaky, mode="video", fps=100, max_seconds=0.3)
    r.start()
    time.sleep(0.5)
    r.stop()
    assert calls["n"] > 4
    assert stub_encoder[0], "the good frames should still have been kept"


def test_none_frames_are_skipped_not_buffered(stub_encoder):
    r = MediaRecorder(lambda: None, mode="video", fps=100, max_seconds=0.2)
    r.start()
    time.sleep(0.4)
    assert r.stop() == []


def test_invalid_mode_is_rejected_at_construction():
    with pytest.raises(ValueError):
        MediaRecorder(_frame, mode="timelapse")


# --- frame shapes the two backends actually return ------------------------ #

def test_extract_rgb_accepts_both_backend_shapes():
    arr = _frame()
    # SimBackend.capture_frame returns the array directly.
    assert _extract_rgb(arr) is arr
    # HardwareBackend.capture_frame returns a CameraFrame with .rgb.
    assert _extract_rgb(types.SimpleNamespace(rgb=arr)) is arr


def test_extract_rgb_rejects_what_cannot_be_encoded():
    assert _extract_rgb(None) is None
    assert _extract_rgb(types.SimpleNamespace(rgb=None)) is None
    assert _extract_rgb(np.zeros((8, 8), dtype=np.uint8)) is None      # no channels
    assert _extract_rgb(np.zeros((8, 8, 1), dtype=np.uint8)) is None   # mono
