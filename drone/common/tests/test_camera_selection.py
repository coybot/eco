"""get_camera() must return a camera that works, not one that merely exists.

The quadcopter carries a RealSense D435I and a D430, and a streaming service
(cam-split) owns the D435I's colour stream, republishing it on v4l2loopback
nodes. Auto-detection used to return whichever RealSense librealsense listed
first; start() then failed with "Device or resource busy" (D435I) or
"Couldn't resolve requests" (the D430 has no RGB sensor), and every photo
mission reported success with no photo. The loopback node that would have
worked was never considered.

These tests pin the selection rules with fake backends, so they run anywhere:
the aircraft's exact layout falls through to the loopback; an explicit setting
is honoured and never silently substituted; and the reason for having no
camera survives for the mission to report.

Run: cd eco && python3 -m pytest drone/common/tests/test_camera_selection.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # drone/, for `camera`

import camera  # noqa: E402
from camera.common import auto  # noqa: E402
from camera.common.base import Camera, CameraFrame  # noqa: E402
from camera.v4l2.camera import V4L2Camera  # noqa: E402


class FakeCamera(Camera):
    """Behaves like a backend: start() may raise, frames may lack colour."""

    CAMERA_TYPE = "fake"
    started = []  # every label start() was called on, across instances

    def __init__(self, label, start_error=None, rgb=True):
        self.label = label
        self.start_error = start_error
        self.rgb = rgb
        self.running = False
        self.start_calls = 0

    def start(self):
        FakeCamera.started.append(self.label)
        self.start_calls += 1
        if self.start_error:
            raise RuntimeError(self.start_error)
        self.running = True

    def stop(self):
        self.running = False

    def get_frame(self, timeout_ms=1000):
        if not self.running:
            return None
        rgb = np.zeros((4, 4, 3), dtype=np.uint8) if self.rgb else None
        return CameraFrame(rgb=rgb, depth=np.zeros((4, 4)))

    def is_connected(self):
        return self.running

    @property
    def resolution(self):
        return (4, 4)


@pytest.fixture
def rig(monkeypatch):
    """A configurable set of attached cameras. Returns the dict to fill in."""
    FakeCamera.started = []
    for var in ("CAMERA_TYPE", "CAMERA_SERIAL", "CAMERA_DEVICE"):
        monkeypatch.delenv(var, raising=False)
    attached = {"oakd": None, "realsense": [], "v4l2": []}
    behaviour = {}  # label -> dict(start_error=..., rgb=...)

    monkeypatch.setattr(auto, "_check_oakd_available", lambda: attached["oakd"] is not None)
    monkeypatch.setattr(auto, "_realsense_devices", lambda: list(attached["realsense"]))
    monkeypatch.setattr(auto, "_v4l2_devices", lambda: list(attached["v4l2"]))
    monkeypatch.setattr(auto, "_new_oakd", lambda *a: FakeCamera("oakd", **behaviour.get("oakd", {})))
    monkeypatch.setattr(auto, "_new_realsense",
                        lambda fps, depth, res, serial: FakeCamera(serial, **behaviour.get(serial, {})))
    monkeypatch.setattr(auto, "_new_v4l2",
                        lambda dev, fps, res: FakeCamera(str(dev), **behaviour.get(str(dev), {})))
    return attached, behaviour


def _quadcopter(attached, behaviour):
    """The aircraft as found: D435I colour stream owned by cam-split, a D430,
    and cam-split's two loopback copies."""
    attached["realsense"] = [
        {"name": "Intel RealSense D435I", "serial": "111111111111", "has_rgb": True},
        {"name": "Intel RealSense D430", "serial": "222222222222", "has_rgb": False},
    ]
    attached["v4l2"] = [
        {"device": "/dev/video10", "name": "Dummy video device (0x0000)"},
        {"device": "/dev/video11", "name": "Dummy video device (0x0001)"},
    ]
    behaviour["111111111111"] = {"start_error": "xioctl(VIDIOC_S_FMT) failed, errno=16 Device or resource busy"}


def test_the_quadcopter_falls_through_to_the_loopback_copy(rig):
    attached, behaviour = rig
    _quadcopter(attached, behaviour)

    cam = camera.get_camera(rgb_fps=15, enable_depth=False)

    assert cam is not None and cam.label == "/dev/video10"
    assert camera.last_camera_error() is None
    # The D430 is known to have no RGB sensor: it is never started for a photo.
    assert "222222222222" not in FakeCamera.started


def test_the_camera_returned_is_already_delivering_frames(rig):
    attached, _ = rig
    attached["v4l2"] = [{"device": "/dev/video0", "name": "USB Camera"}]
    cam = camera.get_camera()
    assert cam.get_frame().rgb is not None
    # Every caller still calls start() itself; that must be harmless.
    cam.start()
    assert cam.get_frame().rgb is not None


def test_a_luxonis_is_preferred_when_present(rig):
    attached, _ = rig
    attached["oakd"] = True
    attached["realsense"] = [{"name": "Intel RealSense D435I", "serial": "1", "has_rgb": True}]
    attached["v4l2"] = [{"device": "/dev/video0", "name": "USB Camera"}]
    assert camera.get_camera().label == "oakd"


def test_a_camera_that_starts_without_colour_is_rejected(rig):
    attached, behaviour = rig
    attached["realsense"] = [{"name": "Intel RealSense D435I", "serial": "1", "has_rgb": True}]
    attached["v4l2"] = [{"device": "/dev/video0", "name": "USB Camera"}]
    behaviour["1"] = {"rgb": False}
    assert camera.get_camera().label == "/dev/video0"


def test_depth_only_is_acceptable_when_colour_is_not_required(rig):
    attached, _ = rig
    attached["realsense"] = [{"name": "Intel RealSense D430", "serial": "2", "has_rgb": False}]
    # A caller that only wants depth may still have the D430.
    # (it is skipped by the has_rgb pre-check only when colour is required)
    assert camera.get_camera(require_rgb=False).label == "2"


def test_an_explicit_device_is_used_and_never_substituted(rig, monkeypatch):
    attached, behaviour = rig
    _quadcopter(attached, behaviour)
    monkeypatch.setenv("CAMERA_DEVICE", "/dev/video11")
    assert camera.get_camera().label == "/dev/video11"

    behaviour["/dev/video11"] = {"start_error": "could not open V4L2 device /dev/video11"}
    FakeCamera.started = []
    assert camera.get_camera() is None
    assert "/dev/video11" in camera.last_camera_error()
    assert FakeCamera.started == ["/dev/video11"], "nothing else may be tried"


def test_an_explicit_serial_that_is_not_attached_says_so(rig, monkeypatch):
    attached, behaviour = rig
    _quadcopter(attached, behaviour)
    monkeypatch.setenv("CAMERA_SERIAL", "999")
    assert camera.get_camera() is None
    assert "not attached" in camera.last_camera_error()


def test_the_reason_names_every_camera_that_was_tried(rig):
    attached, behaviour = rig
    _quadcopter(attached, behaviour)
    attached["v4l2"] = []
    assert camera.get_camera() is None
    why = camera.last_camera_error()
    assert "Device or resource busy" in why
    assert "no RGB sensor" in why


def test_no_cameras_at_all(rig):
    assert camera.get_camera() is None
    assert camera.last_camera_error() == "no cameras attached"


# --- V4L2 device listing ----------------------------------------------------

def _node(root, n, name, index="0"):
    d = root / f"video{n}"
    d.mkdir()
    (d / "name").write_text(name + "\n")
    (d / "index").write_text(index + "\n")


def test_v4l2_listing_keeps_only_usable_capture_nodes(tmp_path, monkeypatch):
    import camera.v4l2.camera as v4l2_mod
    monkeypatch.setattr(v4l2_mod, "_SYSFS", str(tmp_path))
    _node(tmp_path, 0, "Intel(R) RealSense(TM) Depth Ca")   # librealsense's job
    _node(tmp_path, 2, "USB Camera")
    _node(tmp_path, 3, "USB Camera", index="1")              # UVC metadata node
    _node(tmp_path, 9, "Dummy video device (0x0000)")
    _node(tmp_path, 10, "Dummy video device (0x0001)")

    devices = V4L2Camera.list_devices()

    assert [d["device"] for d in devices] == ["/dev/video2", "/dev/video9", "/dev/video10"]
    assert [d["loopback"] for d in devices] == [False, True, True]
