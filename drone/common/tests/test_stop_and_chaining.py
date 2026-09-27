"""The operator's stop ends a mission at once, and chained moves do not drift.

From the real chat log: mid-flight "Stop" reached nothing that could stop a
mission, and "go forward 4m then right 3m" chained each leg from wherever the
previous one was declared "arrived" (up to 1 m short), ending 1.2 m off.

Run: cd eco && python3 -m pytest drone/common/tests/test_stop_and_chaining.py -v
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reasoning_loop as rl  # noqa: E402
from vehicle_class import get_class  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_sdk_or_sleep(monkeypatch):
    monkeypatch.setattr(rl, "DRONE_SDK_AVAILABLE", False)
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)


class ShortArrival:
    """goto() 'arrives' short of the target, as a 1 m arrival tolerance allows."""
    def __init__(self, short_m=0.9, facing_deg=0.0):
        self.pos = [0.0, 0.0, 0.0]     # east, north, up
        self.short = short_m
        self.yaw = math.radians(facing_deg)
        self.gotos = []
        self.stopped = False
        self.on_goto = None

    def get_pose(self):
        return (self.pos[0], self.pos[1], self.pos[2], self.yaw)

    def get_battery(self):
        return {"remaining": 100.0}

    def takeoff(self, alt):
        self.pos[2] = alt
        return True

    def goto(self, north_m, east_m, alt_m):
        self.gotos.append((round(north_m, 3), round(east_m, 3)))
        if self.on_goto and self.on_goto(len(self.gotos)) is False:
            return False
        de, dn = east_m - self.pos[0], north_m - self.pos[1]
        d = math.hypot(de, dn)
        k = max(0.0, d - self.short) / d if d else 0.0
        self.pos = [self.pos[0] + de * k, self.pos[1] + dn * k, alt_m]
        return True

    def rtl(self, alt_m=None):
        return self.goto(0.0, 0.0, alt_m or self.pos[2])

    def land(self, heading_deg=None):
        self.pos[2] = 0.0
        return True

    def abort(self):
        self.stopped = True

    def clear_abort(self):
        self.stopped = False

    def configure_safety(self, **kw):
        return True

    def log_event(self, *a, **k):
        pass


def _run(backend, phases):
    loop = rl.MissionLoop(backend=backend, conversation_id="c-1", vehicle_class=get_class("quadcopter"))
    loop._battery_gov = None
    return loop, loop.run(rl.Mission(mission_id="m", phases=phases, conversation_id="c-1",
                                     original_message="test"))


FWD_RIGHT = [{"type": "arm_and_takeoff", "altitude_m": 5},
             {"type": "nav", "forward_m": 4}, {"type": "nav", "right_m": 3},
             {"type": "return_home"}, {"type": "land"}]


def test_chained_moves_aim_at_the_planned_points_not_the_arrivals():
    b = ShortArrival(short_m=0.9)            # facing north: forward = north, right = east
    loop, res = _run(b, FWD_RIGHT)
    assert res.success, res.failure_reason
    assert b.gotos[:2] == [(4.0, 0.0), (4.0, 3.0)], "the second leg must aim at (4, 3), not (3.1, 3)"


def test_a_move_after_anything_else_starts_from_where_the_aircraft_is():
    b = ShortArrival(short_m=0.9)
    phases = [{"type": "arm_and_takeoff", "altitude_m": 5},
              {"type": "nav", "forward_m": 4},
              {"type": "return_home"},
              {"type": "nav", "right_m": 3}]
    loop, res = _run(b, phases)
    home_arrival = b.pos  # not used; the point is where leg 2 aims from
    # After return_home the aircraft stopped 0.9 m short of home; the new leg is
    # measured from there, not from the old nav target (4, 0).
    n, e = b.gotos[2]
    assert abs(n) < 1.0 and abs(e - 3.0) < 1.0


def test_a_stop_ends_the_mission_without_a_retry():
    b = ShortArrival()
    holder = {}

    def stop_on_second_leg(k):
        if k == 2:
            holder["loop"].abort()   # what the daemon does on the operator's stop
            return False             # goto_offset returns False once the stop lands
    b.on_goto = stop_on_second_leg
    loop = rl.MissionLoop(backend=b, conversation_id="c-1", vehicle_class=get_class("quadcopter"))
    loop._battery_gov = None
    holder["loop"] = loop
    res = loop.run(rl.Mission(mission_id="m", phases=FWD_RIGHT, conversation_id="c-1", original_message="t"))
    assert not res.success and res.failure_reason == "Stopped on your command"
    assert len(b.gotos) == 2, "no replan, no return_home, nothing after the stop"
    assert b.stopped, "the backend was told, so a move in progress ends now"


def test_a_new_mission_starts_clean_after_a_stop():
    b = ShortArrival()
    loop = rl.MissionLoop(backend=b, conversation_id="c-1", vehicle_class=get_class("quadcopter"))
    loop._battery_gov = None
    loop.abort()
    res = loop.run(rl.Mission(mission_id="m2", phases=FWD_RIGHT, conversation_id="c-1", original_message="t"))
    assert res.success and not b.stopped
