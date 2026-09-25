"""A photo the operator asked for and did not get must be said out loud.

Observed on the quadcopter: "take off to 5m, go forward 5m, take picture and
return home" and a 40-waypoint photo survey both flew perfectly, captured
nothing (the camera package would not import), and came back to the chat as a
bare "Done!". The phases deliberately do not fail -- aborting at the photo
would also skip the flight home -- so the failure has to travel in the result
instead. The cloud appends a result's `stdout` to its "Done!", which is where
it lands, with no cloud change.

A perception phase with no camera is different: it cannot make progress, and
used to hover blind until its whole action budget ran out.

Run: cd eco && python3 -m pytest drone/common/tests/test_mission_media_warnings.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reasoning_loop as rl  # noqa: E402
from vehicle_class import get_class  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_sdk_or_sleep(monkeypatch):
    monkeypatch.setattr(rl, "DRONE_SDK_AVAILABLE", False)
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)
    monkeypatch.setattr(rl.MissionLoop, "_camera_problem",
                        staticmethod(lambda: "realsense D435I: Device or resource busy"))


class Backend:
    def __init__(self, frame=True):
        self.frame = frame

    def capture_frame(self):
        return np.zeros((8, 8, 3), dtype=np.uint8) if self.frame else None

    def get_pose(self):
        return (0.0, 0.0, 0.0, 0.0)

    def detect(self):
        return []

    def log_event(self, *a, **k):
        pass


class SDK:
    def __init__(self, photo):
        self.photo = photo

    def capture_photo(self, upload=True):
        return self.photo


def _loop(photo=None, frame=True):
    loop = rl.MissionLoop(backend=Backend(frame), drone_sdk=SDK(photo),
                          conversation_id="c-1", vehicle_class=get_class("quadcopter"))
    loop._media_warnings = []
    loop._photos = []
    loop._videos = []
    loop._recorder = None
    return loop


def test_a_photo_that_was_not_taken_is_reported_not_hidden():
    loop = _loop(photo=None)
    assert loop._exec_capture_photo({}) == {"success": True, "actions": 1}, \
        "the flight must carry on to return home"
    assert loop._photos == []
    assert loop._media_warnings == ["no photo was taken: realsense D435I: Device or resource busy"]


def test_a_photo_left_on_the_aircraft_is_not_sent_as_an_image():
    loop = _loop(photo="/tmp/drone_capture_x.jpg")  # upload failed
    loop._exec_capture_photo({})
    assert loop._photos == [], "a local path would render as a broken image"
    assert "not uploaded" in loop._media_warnings[0]


def test_an_uploaded_photo_is_delivered_without_a_warning():
    loop = _loop(photo="https://b.s3.amazonaws.com/c-1/still.jpg")
    loop._exec_capture_photo({})
    assert loop._photos == ["https://b.s3.amazonaws.com/c-1/still.jpg"]
    assert loop._media_warnings == []


def test_an_empty_recording_is_reported():
    loop = _loop()
    assert loop._exec_stop_recording({})["success"]
    assert loop._media_warnings == ["nothing was recorded: realsense D435I: Device or resource busy"]


def test_warnings_reach_the_chat_through_stdout():
    result = rl.MissionResult(success=True, summary="ok", phases_completed=5, total_phases=5,
                              media_warnings=["no photo was taken: camera busy"])
    d = result.to_dict()
    # conversations.response_handler sends f"Done! {result['stdout']}".
    assert d["stdout"] == "No photo was taken: camera busy."
    assert d["media_warnings"] == ["no photo was taken: camera busy"]


def test_a_clean_result_has_no_stdout():
    d = rl.MissionResult(success=True, summary="ok", phases_completed=1, total_phases=1).to_dict()
    assert "stdout" not in d


def test_a_perception_phase_with_no_camera_fails_fast_and_says_why():
    loop = _loop(frame=False)

    class VLM:
        def is_available(self):
            return True

    loop._get_vlm = lambda: VLM()
    loop._get_nav = lambda: None
    loop._get_perception = lambda: None
    mission = rl.Mission(mission_id="m", original_message="follow me",
                         phases=[{"objective": "follow the person"}])

    result = loop._exec_vlm_phase({"objective": "follow the person"}, mission)

    assert result["failed"]
    assert "no camera frames" in result["reason"] and "Device or resource busy" in result["reason"]
    assert result["actions"] < loop.MAX_CONSECUTIVE_MISSING_FRAMES, \
        "must not burn the action budget hovering blind"
