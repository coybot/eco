"""nav in every direction, and nav that actually moves the aircraft.

The field report: "take off to 3 m, go forward 5 m, return home" took off and
landed without moving, and the log said 4/4 phases complete. Two faults:

* With no Nav2 running, HardwareBackend used Nav2Bridge's no-ROS fallback,
  which is a simulation - it sleeps and reports success. Nothing reached the
  flight controller. It also supplied a fake pose with yaw 0, so "ahead" was
  always north.
* The planner could only say north_m/east_m, so "forward" became "north".

Run: cd eco && python3 -m pytest drone/common/tests/test_nav_directions.py -v
"""

import math
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reasoning_loop as rl  # noqa: E402
from backends import HardwareBackend  # noqa: E402
from vehicle_class import get_class  # noqa: E402


@pytest.fixture(autouse=True)
def no_sdk(monkeypatch):
    monkeypatch.setattr(rl, "DRONE_SDK_AVAILABLE", False)


class MovingBackend:
    """Goes where it is told, so chained moves can be checked."""

    def __init__(self, yaw_deg=0.0, alt=3.0):
        self.pose = (0.0, 0.0, alt, math.radians(yaw_deg))   # ENU + yaw
        self.gotos = []

    def get_pose(self):
        return self.pose

    def goto(self, n, e, a):
        self.gotos.append((round(n, 3), round(e, 3), round(a, 3)))
        self.pose = (e, n, a, self.pose[3])
        return True

    def get_battery(self):
        return {"remaining": 90.0}

    def log_event(self, *a, **k):
        pass


def _loop(backend):
    loop = rl.MissionLoop(backend=backend, conversation_id="c",
                          vehicle_class=get_class("quadcopter"))
    loop._battery_gov = None
    loop._home_yaw_rad = backend.pose[3]
    return loop


def _target(yaw_deg, phase, alt=3.0):
    loop = _loop(MovingBackend(yaw_deg, alt))
    return tuple(round(v, 3) for v in loop._nav_target(phase))


# --- every direction -------------------------------------------------------- #

@pytest.mark.parametrize("yaw,phase,expect", [
    (0, {"forward_m": 5}, (5, 0, 3)),
    (90, {"forward_m": 5}, (0, 5, 3)),          # facing east: forward is east
    (90, {"right_m": 5}, (-5, 0, 3)),           # ... and right is south
    (90, {"right_m": -5}, (5, 0, 3)),           # left is north
    (180, {"forward_m": -4}, (4, 0, 3)),        # facing south, back is north
    (0, {"bearing_deg": 45, "distance_m": 10}, (7.071, 7.071, 3)),
    (0, {"bearing_deg": 225, "distance_m": 10}, (-7.071, -7.071, 3)),
    (0, {"bearing_deg": 270, "distance_m": 6}, (0, -6, 3)),
    (0, {"up_m": 2}, (0, 0, 5)),
    (0, {"up_m": -1.5}, (0, 0, 1.5)),
    (0, {"forward_m": 5, "up_m": 2}, (5, 0, 5)),
    (0, {"forward_m": 5, "alt_m": 8}, (5, 0, 8)),  # alt_m is absolute
])
def test_directions(yaw, phase, expect):
    assert _target(yaw, phase) == pytest.approx(expect, abs=1e-3)


def test_a_move_keeps_altitude_rather_than_dropping_to_the_default():
    assert _target(0, {"forward_m": 5}, alt=12.0)[2] == 12.0


def test_north_east_alone_is_still_a_destination_from_home():
    b = MovingBackend()
    b.pose = (20.0, 20.0, 3.0, 0.0)   # already somewhere else
    loop = _loop(b)
    assert loop._nav_target({"north_m": 10, "east_m": 0, "alt_m": 5}) == (10.0, 0.0, 5.0)


def test_moves_chain_from_where_the_last_one_ended():
    b = MovingBackend(yaw_deg=0)
    loop = _loop(b)
    for phase in ({"forward_m": 5}, {"right_m": -3}, {"up_m": 2},
                  {"bearing_deg": 180, "distance_m": 5}):
        assert loop._exec_nav(phase)["success"]
    assert b.gotos == [(5, 0, 3), (5, -3, 3), (5, -3, 5), (0, -3, 5)]


def test_without_a_pose_moves_chain_from_the_last_target():
    b = MovingBackend()
    b.get_pose = lambda: None
    loop = _loop(b)
    loop._home_yaw_rad = 0.0
    loop._nav_cursor = (0.0, 0.0, 3.0)
    loop._exec_nav({"forward_m": 5})
    loop._exec_nav({"forward_m": 5})
    assert [g[0] for g in b.gotos] == [5, 10]


# --- no simulated navigation on real hardware ------------------------------ #

class FakeSDK:
    """drone_sdk stand-in that records what reaches the flight controller."""

    def __init__(self, yaw_deg=90.0):
        self.moves = []
        self.yaw = math.radians(yaw_deg)
        self.pos = (0.0, 0.0, 0.0)

    def get_local_pose(self):
        return (self.pos[1], self.pos[0], self.pos[2], self.yaw)

    def goto_offset(self, n, e, a):
        self.moves.append((round(n, 3), round(e, 3), a))
        self.pos = (n, e, a)
        return True

    def arm(self):
        return True

    def takeoff(self, alt):
        self.pos = (0.0, 0.0, alt)
        return True

    def land(self):
        self.moves.append("land")
        return True

    def get_battery(self):
        return {"remaining": 90.0}


class SimulatedNav:
    """Nav2Bridge without ROS: 'navigates' by pretending."""
    use_ros = False

    def __init__(self):
        self.calls = 0

    def navigate_to_offset(self, *a):
        self.calls += 1
        return SimpleNamespace(status="succeeded", message="simulated")

    def get_pose(self):
        return (0.0, 0.0, 1.5, 0.0)


def test_simulated_nav2_is_bypassed_for_the_flight_controller():
    sdk, nav = FakeSDK(), SimulatedNav()
    backend = HardwareBackend(drone_sdk=sdk, nav=nav)
    assert backend.goto(5, 0, 3) is True
    assert sdk.moves == [(5, 0, 3)] and nav.calls == 0
    assert backend.rtl(3) is True and sdk.moves[-1] == (0, 0, 3)
    assert backend.get_pose()[3] == pytest.approx(math.radians(90))


def test_real_nav2_is_still_used_when_ros_is_running():
    sdk, nav = FakeSDK(), SimulatedNav()
    nav.use_ros = True
    HardwareBackend(drone_sdk=sdk, nav=nav).goto(5, 0, 3)
    assert nav.calls == 1 and sdk.moves == []


def test_hungs_mission_flies_forward_along_the_real_heading():
    """Take off to 3 m, forward 5 m, return home, land - nose pointing east."""
    sdk = FakeSDK(yaw_deg=90)
    backend = HardwareBackend(drone_sdk=sdk, nav=SimulatedNav())
    loop = rl.MissionLoop(backend=backend, drone_sdk=sdk, conversation_id="c",
                          vehicle_class=get_class("quadcopter"))
    loop._battery_gov = None
    mission = rl.Mission.from_dict({"phases": [
        {"type": "arm_and_takeoff", "altitude_m": 3},
        {"type": "nav", "forward_m": 5, "description": "fly 5m forward"},
        {"type": "return_home", "alt_m": 3},
        {"type": "land"},
    ]})
    result = loop.run(mission)
    assert result.success, result.failure_reason
    assert sdk.moves[:2] == [(0, 5, 3), (0, 0, 3)], "5 m east (forward), then home"


def test_a_move_that_does_not_arrive_fails_the_phase():
    sdk = FakeSDK()
    sdk.goto_offset = lambda n, e, a: False
    loop = _loop(MovingBackend())
    loop.backend = HardwareBackend(drone_sdk=sdk, nav=None)
    assert loop._exec_nav({"forward_m": 5})["failed"]


# --- drone_sdk: goto_offset and takeoff really check ------------------------ #

sdk_mod = pytest.importorskip("drone_sdk")


class PosLink:
    """Position source that walks toward a target, or never moves."""

    def __init__(self, path):
        self.path = list(path)
        self.last = self.path[0]
        self.messages = {}
        self.sysid_state = {}
        self.target_system = self.target_component = 1
        self.mav = SimpleNamespace(request_data_stream_send=lambda *a: None)

    def recv_match(self, type=None, blocking=False, timeout=None):
        if self.path:
            self.last = self.path.pop(0)
        lat, lon, alt = self.last
        return SimpleNamespace(lat=int(lat * 1e7), lon=int(lon * 1e7),
                               relative_alt=int(alt * 1000), yaw=0.0)


@pytest.fixture
def sdk_env(monkeypatch):
    monkeypatch.setattr(sdk_mod, "_flight_home", (37.0, -122.0))
    monkeypatch.setattr(sdk_mod, "_battery_guard_trip", None)
    monkeypatch.setattr(sdk_mod, "_ceiling_guard_holding", None)
    monkeypatch.setattr(sdk_mod, "goto", lambda *a, **k: None)
    monkeypatch.setattr(sdk_mod.time, "sleep", lambda s: None)
    return monkeypatch


def test_goto_offset_waits_for_arrival(sdk_env):
    north5 = 37.0 + 5 / 111320.0
    link = PosLink([(37.0, -122.0, 3.0), (37.0 + 2 / 111320.0, -122.0, 3.0), (north5, -122.0, 3.0)])
    sdk_env.setattr(sdk_mod, "_connect", lambda: link)
    assert sdk_mod.goto_offset(5, 0, 3) is True


def test_goto_offset_reports_not_arriving(sdk_env):
    link = PosLink([(37.0, -122.0, 3.0)])  # stuck where it started
    sdk_env.setattr(sdk_mod, "_connect", lambda: link)
    sdk_env.setattr(sdk_mod, "_ceiling_guard_holding", 0.45)
    t0 = time.time()
    assert sdk_mod.goto_offset(5, 0, 3, timeout_s=0.05) is False
    assert time.time() - t0 < 5


def test_takeoff_that_cannot_climb_lands_and_fails(sdk_env):
    link = PosLink([(37.0, -122.0, 0.4)])   # pinned just off the ground
    sdk_env.setattr(sdk_mod, "_connect", lambda: link)
    sdk_env.setattr(sdk_mod, "_ceiling_guard_holding", 0.45)
    sdk_env.setattr(sdk_mod, "_drain_statustext", lambda **k: [])
    sdk_env.setattr(sdk_mod, "_mav_send", lambda f: None)
    sdk_env.setattr(sdk_mod, "_wait_for_mode", lambda *a, **k: True)
    sdk_env.setattr(sdk_mod, "_battery_preflight", lambda alt: (True, ""))
    sdk_env.setattr(sdk_mod, "_check_preflight_status", lambda: (True, []))
    sdk_env.setattr(sdk_mod, "is_armed", lambda: True)
    sdk_env.setattr(sdk_mod, "start_battery_guard", lambda *a, **k: None)
    sdk_env.setattr(sdk_mod, "_mav_command", lambda f, cid, timeout=3: (True, 0))
    landed = []
    sdk_env.setattr(sdk_mod, "land", lambda: landed.append(1))
    clock = iter(range(0, 10_000))
    sdk_env.setattr(sdk_mod.time, "time", lambda: next(clock))
    assert sdk_mod.takeoff(3) is False
    assert landed == [1]
