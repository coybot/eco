"""The fly_rect / start_recording / stop_recording phases, end to end.

The unit tests next door cover the geometry (test_search_patterns_rectangle)
and the recorder's bounds (test_media_recorder) in isolation. What is only
visible here is the wiring between them, where the interesting failures are:

* The body->ENU rotation has to survive the trip from the phase dict to
  backend.goto(north, east, alt). A transposition in the executor produces a
  perfectly valid rectangle in the wrong place, and nothing errors.
* A mission that aborts mid-pattern must still deliver what it recorded —
  that footage is often what explains the abort. MissionLoop._cleanup is the
  only place that can guarantee it, and it is easy to break silently because
  the happy path never exercises it.
* The happy path stops the recorder twice (the stop_recording phase, then
  _cleanup). Uploading twice would double-bill and double-post.

Run: cd eco && python3 -m pytest drone/common/tests/test_mission_recording_phases.py -v
"""

import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import video_record  # noqa: E402
import reasoning_loop as rl  # noqa: E402
from vehicle_class import get_class  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_codec_or_sdk(monkeypatch):
    """CI installs neither PyAV nor a flight controller."""
    monkeypatch.setattr(video_record, "encode_mp4", lambda frames, fps=15: b"MP4DATA")
    monkeypatch.setattr(rl, "DRONE_SDK_AVAILABLE", False)


class FakeBackend:
    def __init__(self, yaw_rad=0.0):
        self.gotos = []
        self._yaw = yaw_rad
        self.fail_from = None

    def get_pose(self):
        return (0.0, 0.0, 0.0, self._yaw)

    def capture_frame(self):
        return np.zeros((8, 8, 3), dtype=np.uint8)

    def goto(self, north_m, east_m, alt_m):
        self.gotos.append((round(north_m, 3), round(east_m, 3), alt_m))
        if self.fail_from is not None and len(self.gotos) >= self.fail_from:
            return False
        return True

    def get_battery(self):
        return {"remaining": 100.0}

    def detect(self):
        return []

    def log_event(self, *a, **k):
        pass


class FakeSDK:
    def __init__(self):
        self.videos = []
        self.photos = []

    def upload_video_bytes(self, data, conversation_id, label="clip"):
        self.videos.append((len(data), conversation_id))
        return f"https://b.s3.amazonaws.com/{conversation_id}/clip.mp4"

    def upload_media(self, path, conversation_id):
        self.photos.append((path, conversation_id))
        return f"https://b.s3.amazonaws.com/{conversation_id}/still.jpg"


def _loop(heading_deg=0.0, vehicle="quadcopter"):
    backend = FakeBackend(math.radians(heading_deg))
    sdk = FakeSDK()
    loop = rl.MissionLoop(backend=backend, drone_sdk=sdk, conversation_id="c-1",
                          vehicle_class=get_class(vehicle))
    loop._home_yaw_rad = math.radians(heading_deg)
    return loop, backend, sdk


# --- fly_rect ------------------------------------------------------------- #

def test_rectangle_waypoints_reach_the_backend_as_north_east_alt():
    loop, backend, _ = _loop()
    assert loop._exec_fly_rect({"forward_m": 10, "right_m": 5, "altitude_m": 4}) == {
        "success": True, "actions": 4
    }
    assert backend.gotos == [(0.0, 0.0, 4), (10.0, 0.0, 4), (10.0, 5.0, 4), (0.0, 5.0, 4)]


def test_ahead_follows_the_start_heading_not_north():
    """Facing east, "10 ahead and 5 right" is 10m east and 5m south. Getting
    this wrong flies a correct rectangle in the wrong place."""
    loop, backend, _ = _loop(heading_deg=90)
    loop._exec_fly_rect({"forward_m": 10, "right_m": 5, "altitude_m": 4})
    norths = [g[0] for g in backend.gotos]
    easts = [g[1] for g in backend.gotos]
    assert max(easts) == pytest.approx(10.0), "forward should go east at heading 90"
    assert min(norths) == pytest.approx(-5.0), "right should go south at heading 90"


def test_missing_heading_falls_back_to_north_rather_than_failing():
    """A GPS-denied rover with no pose should still fly the box."""
    loop, backend, _ = _loop()
    loop._home_yaw_rad = None
    assert loop._exec_fly_rect({"forward_m": 10, "right_m": 5})["success"]
    assert max(g[0] for g in backend.gotos) == pytest.approx(10.0)


def test_a_blocked_waypoint_fails_the_phase_with_its_index():
    loop, backend, _ = _loop()
    backend.fail_from = 3
    result = loop._exec_fly_rect({"forward_m": 10, "right_m": 5})
    assert result["failed"] and "Waypoint 3" in result["reason"]


def test_min_clearance_alt_raises_the_leg_altitude():
    loop, backend, _ = _loop()
    loop._exec_fly_rect({"forward_m": 10, "right_m": 5, "altitude_m": 4,
                         "min_clearance_alt": 12})
    assert all(g[2] == 12 for g in backend.gotos)


# --- the recording bracket ------------------------------------------------ #

def test_record_around_a_pattern_uploads_one_clip():
    loop, backend, sdk = _loop()
    assert loop._exec_start_recording({"mode": "video", "fps": 30, "max_seconds": 5})["success"]
    time.sleep(0.2)
    loop._exec_fly_rect({"forward_m": 10, "right_m": 5})
    assert loop._exec_stop_recording({})["success"]
    assert len(sdk.videos) == 1
    assert loop._videos == ["https://b.s3.amazonaws.com/c-1/clip.mp4"]
    assert backend.gotos, "the aircraft should have flown while recording"


def test_cleanup_delivers_footage_from_an_aborted_mission():
    loop, _, sdk = _loop()
    loop._exec_start_recording({"mode": "video", "fps": 30, "max_seconds": 5})
    time.sleep(0.2)
    loop._cleanup()  # stands in for the abort path's finally block
    assert len(sdk.videos) == 1
    assert loop._videos, "footage from an aborted mission must not be dropped"


def test_the_normal_path_does_not_upload_twice():
    """stop_recording runs, then _cleanup runs. Both stop the recorder."""
    loop, _, sdk = _loop()
    loop._exec_start_recording({"mode": "video", "fps": 30, "max_seconds": 5})
    time.sleep(0.15)
    loop._exec_stop_recording({})
    loop._cleanup()
    assert len(sdk.videos) == 1
    assert len(loop._videos) == 1


def test_stop_without_start_is_not_a_failure():
    loop, _, sdk = _loop()
    assert loop._exec_stop_recording({})["success"]
    assert sdk.videos == []


def test_a_second_start_does_not_abort_the_flight():
    """A planner emitting two start_recording phases still wants one
    recording; failing the phase would abort a flight over a planning slip."""
    loop, _, _ = _loop()
    loop._exec_start_recording({"mode": "video", "max_seconds": 5})
    assert loop._exec_start_recording({"mode": "video", "max_seconds": 5})["success"]
    loop._cleanup()


def test_an_empty_recording_does_not_fail_the_mission():
    """The flight itself succeeded; aborting here would discard every phase
    after this one."""
    loop, backend, sdk = _loop()
    # Camera unavailable for the whole recording — swap the source BEFORE
    # starting, or the recorder buffers a frame in the interim and the test
    # stops testing what it claims to.
    backend.capture_frame = lambda: None
    loop._exec_start_recording({"mode": "video", "fps": 30, "max_seconds": 5})
    time.sleep(0.15)
    assert loop._exec_stop_recording({})["success"]
    assert sdk.videos == []


def test_an_sdk_that_cannot_upload_is_reported_not_crashed():
    """The sim daemon injects a shim duck-typing only capture_photo/look_around."""
    loop, _, _ = _loop()
    loop.drone_sdk = object()
    loop._exec_start_recording({"mode": "video", "fps": 30, "max_seconds": 5})
    time.sleep(0.15)
    assert loop._exec_stop_recording({})["success"]
    assert loop._videos == []


# --- the contract that keeps cloud and device in step --------------------- #

def test_every_new_phase_has_an_executor():
    """reasoning_loop asserts this at import time; pinning it here makes the
    intent explicit rather than relying on a module-level side effect."""
    for phase in ("fly_rect", "start_recording", "stop_recording"):
        assert phase in rl.MissionLoop._PHASE_DISPATCH
        assert hasattr(rl.MissionLoop, rl.MissionLoop._PHASE_DISPATCH[phase])


def test_mission_result_carries_videos_separately_from_photos():
    """The daemon publishes these as image_urls and video_urls; collapsing
    them would lose the distinction the app renders on."""
    result = rl.MissionResult(success=True, summary="", phases_completed=1,
                              total_phases=1, photos=["a.jpg"], videos=["b.mp4"])
    assert result.to_dict()["videos"] == ["b.mp4"]
    assert result.to_dict()["photos"] == ["a.jpg"]
