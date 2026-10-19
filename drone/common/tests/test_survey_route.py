"""The order a survey visits its cells in, and the heading it holds while doing it.

Run: cd eco && python3 -m pytest drone/common/tests/test_survey_route.py -v
"""

import itertools
import math
import random
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import search_patterns as sp  # noqa: E402

HOME = (0.0, 0.0)


def _len(pts, order, start=None, end=None):
    seq = ([start] if start else []) + [pts[i] for i in order] + ([end] if end else [])
    return sum(math.dist(a, b) for a, b in zip(seq, seq[1:]))


def _brute(pts, start, end):
    return min(_len(pts, p, start, end) for p in itertools.permutations(range(len(pts))))


@pytest.mark.parametrize("size", [(2, 2), (3, 3), (2, 3), (3, 2), (1, 5)])
@pytest.mark.parametrize("end", [None, HOME])
def test_small_grids_get_the_shortest_route(size, end):
    pts = sp.photo_grid(size, (0, 0), 1.0)
    order = sp.plan_route(pts, start=HOME, end=end)
    assert sorted(order) == list(range(len(pts)))
    assert _len(pts, order, HOME, end) == pytest.approx(_brute(pts, HOME, end))


@pytest.mark.parametrize("size", [(10, 10), (4, 25), (6, 7)])
def test_a_big_grid_back_to_home_is_unit_steps_and_ends_beside_home(size):
    """Lower bound: in from home to the corner cell, a 1 m step per cell, and
    out from the cell next to it. An even row or column count reaches it."""
    pts = sp.photo_grid(size, (0, 0), 1.0)
    order = sp.plan_route(pts, start=HOME, end=HOME)
    bound = math.hypot(0.5, 0.5) + (len(pts) - 1) + math.hypot(0.5, 1.5)
    assert _len(pts, order, HOME, HOME) == pytest.approx(bound)


def test_without_an_end_a_full_grid_is_never_longer_than_its_serpentine():
    pts = sp.photo_grid((9, 11), (0, 0), 1.0)
    order = sp.plan_route(pts, start=HOME)
    assert _len(pts, order, HOME) <= _len(pts, range(len(pts)), HOME) + 1e-9


def test_scattered_points_are_close_to_the_best_and_never_worse_than_given():
    rnd = random.Random(7)
    for _ in range(20):
        pts = [(rnd.uniform(-10, 10), rnd.uniform(-10, 10)) for _ in range(8)]
        order = sp.plan_route(pts, start=HOME, end=HOME)
        got = _len(pts, order, HOME, HOME)
        assert got <= _len(pts, range(8), HOME, HOME) + 1e-9
        assert got <= 1.05 * _brute(pts, HOME, HOME)


def test_a_hundred_points_plan_quickly_and_stop_at_the_budget():
    rnd = random.Random(3)
    pts = [(rnd.uniform(0, 30), rnd.uniform(0, 30)) for _ in range(100)]
    t = time.monotonic()
    order = sp.plan_route(pts, start=HOME, end=HOME, time_budget_s=0.05)
    assert time.monotonic() - t < 0.5
    assert sorted(order) == list(range(100))
    assert _len(pts, order, HOME, HOME) <= _len(pts, range(100), HOME, HOME)


def test_trivial_inputs():
    assert sp.plan_route([]) == []
    assert sp.plan_route([(3.0, 4.0)], start=HOME, end=HOME) == [0]


# --- the heading reaches the flight controller ------------------------------

pymavlink = pytest.importorskip("pymavlink")
import drone_sdk as sdk  # noqa: E402
import px4_compat  # noqa: E402
from backends import HardwareBackend  # noqa: E402


class _Mav:
    def __init__(self):
        self.sent = []

    def set_position_target_global_int_send(self, *a):
        self.sent.append(a)


@pytest.fixture
def ardupilot(monkeypatch):
    conn = type("C", (), {})()
    conn.mav = _Mav()
    monkeypatch.setattr(sdk, "_autopilot", px4_compat.AUTOPILOT_ARDUPILOT)
    monkeypatch.setattr(sdk, "_mav_send", lambda fn: fn(conn))
    monkeypatch.setattr(sdk, "_connect_locked", lambda: None)
    return conn.mav.sent


def test_without_a_heading_ardupilot_is_left_to_choose_its_yaw(ardupilot):
    sdk._send_position_target(37.0, -122.0, 2.0)
    (msg,) = ardupilot
    assert msg[4] & (1 << 10), "yaw ignored"


def test_a_held_heading_goes_out_as_the_setpoints_yaw(ardupilot):
    sdk._send_position_target(37.0, -122.0, 2.0, yaw_deg=-90.0)
    (msg,) = ardupilot
    mask, yaw = msg[4], msg[14]
    assert not mask & (1 << 10), "yaw used"
    assert mask & (1 << 11), "yaw rate still ignored"
    assert yaw == pytest.approx(math.radians(270.0))


def test_px4_gets_the_heading_in_its_reposition(monkeypatch):
    monkeypatch.setattr(sdk, "_autopilot", px4_compat.AUTOPILOT_PX4)
    monkeypatch.setattr(sdk, "_px4_amsl", lambda rel: 600.0 + rel)
    monkeypatch.setattr(sdk, "_connect_locked", lambda: None)
    monkeypatch.setattr(sdk, "_mav_send", lambda fn: fn(None))
    sent = []
    monkeypatch.setattr(sdk._px4, "send_reposition", lambda *a: sent.append(a))
    sdk._send_position_target(37.0, -122.0, 2.0, yaw_deg=45.0)
    assert sent[0][-1] == pytest.approx(math.radians(45.0))


class _OffsetSDK:
    def __init__(self):
        self.calls = []

    def goto_offset(self, n, e, a, **kw):
        self.calls.append(kw.get("yaw_deg"))
        return True


def test_the_hardware_backend_holds_the_heading_until_released():
    s = _OffsetSDK()
    b = HardwareBackend(drone_sdk=s, vehicle_class="quadcopter")
    b.goto(1, 1, 2)
    b.hold_heading(30.0)
    b.goto(1, 2, 2)
    b.goto(2, 2, 2)
    b.hold_heading(None)
    b.goto(0, 0, 2)
    assert s.calls == [None, 30.0, 30.0, None]


# --- the return lands on home, not on the last cell -------------------------

def _home_env(monkeypatch, reached, left_m):
    tols = []
    def goto_offset(n, e, a, tol_m=1.0, **kw):
        tols.append(tol_m)
        return reached
    monkeypatch.setattr(sdk, "goto_offset", goto_offset)
    monkeypatch.setattr(sdk, "_offset_origin", lambda: (37.0, -122.0))
    monkeypatch.setattr(sdk, "_fresh_position", lambda timeout=0: (37.0 + left_m / 111320.0, -122.0, 2.0))
    monkeypatch.setattr(sdk, "_battery_guard_trip", None)
    monkeypatch.setattr(sdk, "_pilot_control", None)
    sdk._abort_event.clear()
    return tols


def test_return_home_settles_closer_than_the_survey_cell_beside_home(monkeypatch):
    """A survey ending on the cell next to home is 0.7 m out: inside the 1 m a
    goto calls arrived, so the return finished at once and the aircraft landed
    on that cell (ArduCopter 4.7.1 SITL, 0.68 m from home)."""
    tols = _home_env(monkeypatch, reached=True, left_m=0.1)
    assert sdk.return_home(2.0) is True
    assert tols == [sdk.HOME_TOL_M] and sdk.HOME_TOL_M < 0.7


def test_return_home_in_wind_still_counts_within_a_metre(monkeypatch):
    _home_env(monkeypatch, reached=False, left_m=0.6)
    assert sdk.return_home(2.0) is True


def test_return_home_short_of_home_or_stopped_is_still_a_failure(monkeypatch):
    _home_env(monkeypatch, reached=False, left_m=3.0)
    assert sdk.return_home(2.0) is False
    _home_env(monkeypatch, reached=False, left_m=0.2)
    sdk._abort_event.set()
    try:
        assert sdk.return_home(2.0) is False
    finally:
        sdk._abort_event.clear()


def test_the_hardware_backend_returns_through_return_home():
    class S(_OffsetSDK):
        def return_home(self, alt_m=None):
            self.calls.append(("home", alt_m))
            return True
    s = S()
    assert HardwareBackend(drone_sdk=s, vehicle_class="quadcopter").rtl(2.0) is True
    assert s.calls == [("home", 2.0)]
