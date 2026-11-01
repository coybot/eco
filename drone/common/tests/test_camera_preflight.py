"""A dead camera is found out on the ground, and named when it is.

Observed on the quadcopter: "scan the rectangle ... a photo every meter" flew
the whole 16-photo survey and came back with "16 of 16 survey photos were not
taken: could not open V4L2 device /dev/video11". The loopback nodes had been
numbered 12 and 13 that boot and /dev/video11 had become a RealSense node.
Nothing said so, and nothing stopped the aircraft taking off with no camera.

Run: cd eco && python3 -m pytest drone/common/tests/test_camera_preflight.py -v
"""

import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import reasoning_loop as rl  # noqa: E402
from camera.v4l2 import camera as v4l2  # noqa: E402
from vehicle_class import get_class  # noqa: E402


# --- why a V4L2 node will not open ------------------------------------------

@pytest.fixture
def node(tmp_path):
    """A fake /sys/class/video4linux and /dev: node(name, loopback=False)."""
    sysfs = tmp_path / "sys"
    dev = tmp_path / "dev"
    sysfs.mkdir()
    dev.mkdir()

    def make(num, name, loopback=False):
        d = sysfs / f"video{num}"
        d.mkdir()
        (d / "name").write_text(name + "\n")
        parent = tmp_path / ("v4l2loopback-000" if loopback else "usb-3610000.xhci-2.2")
        parent.mkdir(exist_ok=True)
        os.symlink(parent, d / "device")
        (dev / f"video{num}").write_text("")
        return str(dev / f"video{num}")

    make.sysfs = str(sysfs)
    make.dev = dev
    return make


def test_a_node_that_does_not_exist_points_at_the_module_and_the_numbering(tmp_path):
    why = v4l2.explain_open_failure(str(tmp_path / "video11"), sysfs=str(tmp_path))
    assert "does not exist" in why and "v4l2loopback" in why


def test_a_dangling_symlink_says_the_camera_is_gone(node):
    link = node.dev / "eco-color"
    os.symlink(node.dev / "video99", link)
    why = v4l2.explain_open_failure(str(link), sysfs=node.sysfs)
    assert "symlink" in why and "no longer exists" in why


def test_a_realsense_node_is_not_mistaken_for_a_colour_feed(node):
    path = node(11, "Intel(R) RealSense(TM) Depth Ca")
    why = v4l2.explain_open_failure(path, sysfs=node.sysfs)
    assert "RealSense" in why and "stable name" in why


def test_a_loopback_nobody_writes_to_blames_the_feeding_service(node):
    # Its card_label says nothing about being a loopback: the platform device does.
    path = node(21, "eco-color", loopback=True)
    why = v4l2.explain_open_failure(path, sysfs=node.sysfs)
    assert "nothing is writing" in why and "cam-split" in why


def test_start_raises_with_the_reason(node, monkeypatch):
    path = node(11, "Intel(R) RealSense(TM) Depth Ca")

    class Cap:
        def __init__(self, *a):
            pass

        def isOpened(self):
            return False

        def release(self):
            pass

    monkeypatch.setitem(sys.modules, "cv2", types.SimpleNamespace(VideoCapture=Cap, CAP_V4L2=200, CAP_ANY=0))
    real = v4l2.explain_open_failure
    monkeypatch.setattr(v4l2, "explain_open_failure", lambda device: real(device, sysfs=node.sysfs))

    with pytest.raises(RuntimeError) as err:
        v4l2.V4L2Camera(path).start()
    assert f"could not open V4L2 device {path}" in str(err.value)
    assert "RealSense" in str(err.value)


# --- the mission does not take off without a camera --------------------------

class Backend:
    def __init__(self):
        self.gotos = []

    def get_pose(self):
        return (0.0, 0.0, 0.0, 0.0)

    def goto(self, north_m, east_m, alt_m):
        self.gotos.append((north_m, east_m, alt_m))
        return True

    def log_event(self, *a, **k):
        pass


class SDK:
    def __init__(self, answer):
        self.answer = answer
        self.asked = 0

    def camera_ready(self):
        self.asked += 1
        return self.answer


def _mission(*phase_types):
    return rl.Mission(mission_id="m", original_message="scan the area",
                      phases=[{"type": t, "objective": t} for t in phase_types])


def _loop(sdk):
    return rl.MissionLoop(backend=Backend(), drone_sdk=sdk, conversation_id="c-1",
                          vehicle_class=get_class("quadcopter"))


@pytest.fixture(autouse=True)
def no_real_sdk_or_sleep(monkeypatch):
    monkeypatch.setattr(rl, "DRONE_SDK_AVAILABLE", False)
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)


def test_a_survey_with_a_dead_camera_never_leaves_the_ground():
    sdk = SDK((False, "v4l2 /dev/video11: could not open V4L2 device /dev/video11: RealSense node"))
    loop = _loop(sdk)

    result = loop.run(_mission("arm_and_takeoff", "survey_rect", "return_home", "land"))

    assert not result.success
    assert result.phases_completed == 0
    assert loop._get_backend().gotos == []
    assert "no working camera" in result.failure_reason
    assert "/dev/video11" in result.failure_reason
    assert "did not take off" in result.failure_reason


@pytest.mark.parametrize("phase", ["capture_photo", "survey_rect", "start_recording"])
def test_every_phase_that_needs_a_camera_triggers_the_check(phase):
    sdk = SDK((False, "unplugged"))
    assert "unplugged" in _loop(sdk)._camera_preflight(_mission("arm_and_takeoff", phase))


def test_a_mission_with_no_camera_phases_does_not_ask():
    sdk = SDK((False, "unplugged"))
    assert _loop(sdk)._camera_preflight(_mission("arm_and_takeoff", "nav", "land")) is None
    assert sdk.asked == 0, "a flight with no photos must fly with no camera"


def test_a_working_camera_lets_the_mission_go():
    assert _loop(SDK((True, "")))._camera_preflight(_mission("survey_rect")) is None


def test_a_backend_with_no_camera_check_is_left_alone():
    assert _loop(object())._camera_preflight(_mission("survey_rect")) is None
