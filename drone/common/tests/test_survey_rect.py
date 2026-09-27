"""survey_rect: one photo per cell of an area, not one per second of flight.

Observed on the quadcopter: "take off to 5 meters and photograph every square
meter of the rectangle that is 5 meters ahead of you and 5 meters to the
right, then return home" was planned as start_recording(photos, every 1 s) +
fly_rect + stop_recording. fly_rect traces the outline, so none of the inside
was photographed, and the ~100 s flight came back as 100 photos. The area has
25 square meters; the answer is 25 photos, one over each.

Run: cd eco && python3 -m pytest drone/common/tests/test_survey_rect.py -v
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mission_vocab  # noqa: E402
import reasoning_loop as rl  # noqa: E402
import search_patterns  # noqa: E402
from vehicle_class import get_class  # noqa: E402

AWS_SRC = Path(__file__).resolve().parents[3] / "aws" / "src" / "conversations.py"


@pytest.fixture(autouse=True)
def no_real_sdk_or_sleep(monkeypatch):
    monkeypatch.setattr(rl, "DRONE_SDK_AVAILABLE", False)
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)
    monkeypatch.setattr(rl.MissionLoop, "_camera_problem",
                        staticmethod(lambda: "camera unplugged"))


class Backend:
    """Arrives exactly where it is sent, `lag` pose reads later."""

    def __init__(self, lag=0):
        self.gotos = []
        self.lag = lag
        self.reads = 0

    def goto(self, north_m, east_m, alt_m):
        self.gotos.append((round(north_m, 3), round(east_m, 3), alt_m))
        self.reads = 0
        return True

    def get_pose(self):
        self.reads += 1
        if not self.gotos:
            return (0.0, 0.0, 0.0, 0.0)
        n, e, a = self.gotos[-1]
        off = 0.8 if self.reads <= self.lag else 0.0  # still drifting in
        return (e + off, n, a, 0.0)

    def get_battery(self):
        return {"remaining": 100.0}

    def log_event(self, *a, **k):
        pass


class SDK:
    def __init__(self, upload=True):
        self.upload = upload
        self.shots = []

    def capture_photo(self, upload=True):
        self.shots.append(len(self.shots))
        if not self.upload:
            return None
        return f"https://b.s3.amazonaws.com/c-1/{len(self.shots)}.jpg"


def _loop(sdk=None, heading_deg=0.0, vehicle="quadcopter"):
    backend = Backend()
    loop = rl.MissionLoop(backend=backend, drone_sdk=sdk or SDK(), conversation_id="c-1",
                          vehicle_class=get_class(vehicle))
    loop._home_yaw_rad = math.radians(heading_deg)
    loop._media_warnings = []
    loop._photos = []
    return loop, backend


THE_MISSION = {"type": "survey_rect", "forward_m": 5, "right_m": 5,
               "origin_forward_m": 5, "origin_right_m": 0, "spacing_m": 1, "altitude_m": 5}


@pytest.mark.parametrize("vehicle", ["quadcopter", "rover", "crazyflie"])
def test_every_square_meter_of_a_5x5_area_is_25_photos(vehicle):
    sdk = SDK()
    loop, backend = _loop(sdk, vehicle=vehicle)
    assert loop._exec_survey_rect(THE_MISSION) == {"success": True, "actions": 25}
    assert len(sdk.shots) == 25 and len(loop._photos) == 25
    assert loop._media_warnings == []


def test_the_photos_are_over_the_inside_of_the_area_not_its_outline():
    loop, backend = _loop(heading_deg=0.0)  # facing north: forward = north
    loop._exec_survey_rect(THE_MISSION)
    cells = {(n, e) for n, e, _ in backend.gotos}
    expected = {(5.5 + i, 0.5 + j) for i in range(5) for j in range(5)}
    assert cells == expected
    assert (7.5, 2.5) in cells, "the centre of the area"
    assert {a for _, _, a in backend.gotos} == {5}


def test_the_grid_is_placed_in_the_drones_own_frame():
    loop, backend = _loop(heading_deg=90.0)  # facing east: forward = east, right = south
    loop._exec_survey_rect(THE_MISSION)
    first_n, first_e, _ = backend.gotos[0]
    assert first_e == pytest.approx(5.5) and first_n == pytest.approx(-0.5)


def test_the_photo_waits_until_the_vehicle_is_over_the_cell():
    """goto() reports arrival at 1 m; the SITL flight shot 7 of 25 photos
    outside their own cell until the survey waited to be over it."""
    loop, backend = _loop()
    backend.lag = 3
    seen = []
    loop._take_photo = lambda: (seen.append((backend.get_pose(), backend.gotos[-1])),
                                ("https://x/1.jpg", ""))[1]
    loop._exec_survey_rect(THE_MISSION)
    assert len(seen) == 25
    assert all(abs(pose[0] - target[1]) < 0.35 for pose, target in seen), \
        "photographed while still 0.8 m off"


def test_neighbouring_photos_are_one_cell_apart():
    """Serpentine, not raster: no row-end dash back across the area."""
    loop, backend = _loop()
    loop._exec_survey_rect(THE_MISSION)
    hops = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(backend.gotos, backend.gotos[1:])]
    assert max(hops) == pytest.approx(1.0)


def test_a_side_that_is_not_whole_cells_is_still_covered():
    pts = search_patterns.photo_grid((5.5, 2.0), (0.0, 0.0), 2.0)
    assert len(pts) == 3 * 1
    # No cell is larger than asked for.
    norths = sorted(n for _, n in pts)
    assert norths[1] - norths[0] <= 2.0


def test_a_huge_grid_is_widened_to_the_cap_and_the_operator_is_told():
    sdk = SDK()
    loop, _ = _loop(sdk)
    result = loop._exec_survey_rect({"forward_m": 50, "right_m": 50, "spacing_m": 1})
    assert result["success"]
    assert 0 < len(sdk.shots) <= loop.MAX_SURVEY_PHOTOS
    assert "more than 100 photos" in loop._media_warnings[0]


def test_missing_photos_are_one_warning_and_the_survey_still_finishes():
    loop, backend = _loop(SDK(upload=False))
    assert loop._exec_survey_rect(THE_MISSION)["success"], "must carry on to return home"
    assert len(backend.gotos) == 25
    assert loop._media_warnings == ["25 of 25 survey photos were not taken: no photo was taken: camera unplugged"]


def test_a_fixed_wing_skips_it_says_why_and_still_flies_home():
    """Failing the phase would replan twice for nothing and then abort the
    mission before return_home."""
    loop, backend = _loop(vehicle="fixedwing")
    result = loop._exec_survey_rect(THE_MISSION)
    assert result == {"success": True, "actions": 0}
    assert backend.gotos == []
    assert "cannot stop in the air" in loop._media_warnings[0]


def test_the_battery_budget_prices_the_survey():
    loop, _ = _loop()
    kind, cost, _, alt = loop._phase_battery_cost(THE_MISSION, 0.0, 5.0)
    assert kind == "survey_rect" and cost > 0 and alt == 5


def test_the_planner_is_told_to_use_it_for_area_photos():
    assert "survey_rect" in mission_vocab.PHASE_SCHEMAS
    assert rl.MissionLoop._PHASE_DISPATCH["survey_rect"] == "_exec_survey_rect"
    prompt = AWS_SRC.read_text()
    assert '"type": "survey_rect"' in prompt
    assert "photograph every square meter" in prompt


def test_progress_is_published_under_the_drone_id(monkeypatch):
    """drone_sdk has no DRONE_ID; importing one silenced every progress update."""
    import drone_sdk
    monkeypatch.setattr(drone_sdk, "_drone_identity", lambda config=None: ("drone-0123456789ab", "t"))
    sent = []

    class MQTT:
        """awscrt's Connection.publish: qos is required."""
        def publish(self, topic, payload, qos, retain=False):
            sent.append(topic)

    loop, _ = _loop()
    loop.mqtt_client = MQTT()
    loop._report_progress("Survey photo 1/25")
    assert sent == ["drone/drone-0123456789ab/chat/c-1/progress"]


class BackloggedLink:
    """A MAVLink link with fixes queued up unread, oldest first, parsed into
    pymavlink's per-system cache as they are read (as the real one does)."""

    target_system = 1
    target_component = 1

    def __init__(self, fixes):
        import types
        self.queue = [types.SimpleNamespace(lat=int(lat * 1e7), lon=int(lon * 1e7),
                                            relative_alt=5000, get_type=lambda: "GLOBAL_POSITION_INT")
                      for lat, lon in fixes]
        self.sysid_state = {1: types.SimpleNamespace(messages={})}

    def recv_match(self, type=None, blocking=False, timeout=None):
        import time as _t
        if not self.queue:
            return None
        msg = self.queue.pop(0)
        msg._timestamp = _t.time()  # stamped when parsed, not when sent
        self.sysid_state[1].messages[msg.get_type()] = msg
        return msg


def test_a_position_read_skips_the_backlog_to_the_newest_fix(monkeypatch):
    """SITL: a survey leg reported "still 1.2 m from" a point the aircraft had
    been hovering on for 17 s, because each read took the oldest queued fix."""
    import drone_sdk
    link = BackloggedLink([(37.0, -122.0), (37.00001, -122.0), (37.00002, -122.0)])
    monkeypatch.setattr(drone_sdk, "_connect", lambda: link)
    monkeypatch.setattr(drone_sdk, "_ensure_streams", lambda m: None)
    lat, _, _ = drone_sdk._fresh_position()
    assert lat == pytest.approx(37.00002, abs=1e-7), "read the oldest queued fix"
