"""Battery estimation, the flight-budget governor, and the mission-loop gates.

The field report these reproduce: the app showed a steady 87% while the pack
was flown flat, and nothing stopped a takeoff on it. Two FC behaviours cause
that - battery_remaining restarts at 100% every FC boot, and freezes when the
current sensor reads ~0 A - so the tests below feed exactly those readings in
and check the estimate follows the pack, not the counter.

Run: cd eco && python3 -m pytest drone/common/tests/test_battery.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import battery as bt  # noqa: E402
import reasoning_loop as rl  # noqa: E402
from vehicle_class import get_class  # noqa: E402

CELLS = 4


def _rest(cell_v, **kw):
    return bt.BatterySample(voltage=cell_v * CELLS, armed=False, **kw)


def _fly(cell_v, t, current_a=None, consumed_mah=None, fc_remaining=87):
    return bt.BatterySample(voltage=cell_v * CELLS, current_a=current_a,
                            consumed_mah=consumed_mah, fc_remaining=fc_remaining,
                            armed=True, t=t)


# --- the LiPo curve ------------------------------------------------------- #

def test_curve_endpoints_and_inverse():
    assert bt.pct_from_cell_voltage(4.20) == 100
    assert bt.pct_from_cell_voltage(3.27) == 0
    for pct in (10, 35, 50, 80):
        assert bt.pct_from_cell_voltage(bt.cell_voltage_for_pct(pct)) == pytest.approx(pct, abs=0.01)


def test_curve_is_monotonic():
    vs = [3.3 + i * 0.01 for i in range(90)]
    pcts = [bt.pct_from_cell_voltage(v) for v in vs]
    assert pcts == sorted(pcts)


# --- the field bug -------------------------------------------------------- #

def test_frozen_fc_counter_is_ignored_in_favour_of_voltage():
    """FC says 87% throughout; current sensor reads 0.1 A; the pack runs down.
    The estimate must fall with the voltage and say why it is not using the FC."""
    est = bt.BatteryEstimator(bt.BatteryConfig(cells=CELLS))
    est.update(_rest(4.10, fc_remaining=87, current_a=0.1, t=0))
    last = None
    for i, cell_v in enumerate([3.95, 3.85, 3.75, 3.62, 3.50]):
        # Several readings per voltage step so the smoothing settles.
        for k in range(10):
            last = est.update(_fly(cell_v, t=1 + i * 30 + k * 3, current_a=0.1))
    assert last.pct < 25, "a pack at 3.5 V/cell under load is nearly flat"
    assert last.source == "voltage_loaded"
    assert not last.current_trusted
    assert any("current sensor not reading" in w for w in last.warnings)


def test_fc_boot_reset_to_100_does_not_fool_the_ground_estimate():
    """FC just booted and claims 100%; the pack rests at 3.75 V/cell (~25%)."""
    est = bt.BatteryEstimator(bt.BatteryConfig(cells=CELLS)).update(_rest(3.75, fc_remaining=100))
    assert est.source == "voltage_rest"
    assert est.pct == pytest.approx(25, abs=1)
    assert est.fc_remaining == 100


def test_implausible_voltage_gives_no_estimate_and_names_the_parameter():
    est = bt.BatteryEstimator(bt.BatteryConfig(cells=CELLS)).update(
        bt.BatterySample(voltage=5.1, fc_remaining=87))
    assert est.pct is None
    assert any("BATT_VOLT_MULT" in w for w in est.warnings)


def test_zero_voltage_points_at_the_pin():
    est = bt.BatteryEstimator(bt.BatteryConfig(cells=CELLS)).update(
        bt.BatterySample(voltage=0.0, fc_remaining=87))
    assert est.pct is None
    assert any("BATT_VOLT_PIN" in w for w in est.warnings)


# --- in flight with a working current sensor ------------------------------ #

def test_proven_current_sensor_integrates_from_the_arm_time_anchor():
    cfg = bt.BatteryConfig(cells=CELLS, capacity_mah=5000)
    est = bt.BatteryEstimator(cfg)
    rest = est.update(_rest(4.02, consumed_mah=0, t=0))      # ~80%
    assert rest.pct == pytest.approx(80, abs=1)
    est.update(_fly(3.95, t=1, current_a=18, consumed_mah=0))
    # 1000 mAh of 5000 used -> 20 points down from the anchor. Voltage agrees.
    e = None
    for k in range(40):
        e = est.update(_fly(3.80, t=2 + k, current_a=18, consumed_mah=1000))
    assert e.source == "current"
    assert e.current_trusted
    assert e.pct == pytest.approx(60, abs=1)


def test_worn_pack_voltage_overrides_an_optimistic_current_count():
    cfg = bt.BatteryConfig(cells=CELLS, capacity_mah=5000)
    est = bt.BatteryEstimator(cfg)
    est.update(_rest(4.20, consumed_mah=0, t=0))             # anchor 100%
    est.update(_fly(4.0, t=1, current_a=20, consumed_mah=0))
    e = None
    for k in range(60):
        # Count says ~90% but the loaded pack is at ~3.60 V/cell.
        e = est.update(_fly(3.60, t=2 + k, current_a=20, consumed_mah=500))
    assert e.pct < 60
    assert any("worn" in w for w in e.warnings)


def test_one_sag_spike_does_not_stick():
    est = bt.BatteryEstimator(bt.BatteryConfig(cells=CELLS))
    est.update(_rest(4.0, t=0))
    steady = None
    for k in range(20):
        steady = est.update(_fly(3.85, t=1 + k))
    spiked = est.update(_fly(3.55, t=22))      # 1 s hard climb
    assert steady.pct - spiked.pct < 1, "a single sag spike must be rejected"
    after = None
    for k in range(20):
        after = est.update(_fly(3.85, t=23 + k))
    assert steady.pct - after.pct < 1, "and must not stick once it has passed"


def test_sustained_drop_comes_through_after_the_window():
    est = bt.BatteryEstimator(bt.BatteryConfig(cells=CELLS))
    est.update(_rest(4.0, t=0))
    for k in range(10):
        est.update(_fly(3.85, t=1 + k))
    low = None
    for k in range(30):
        low = est.update(_fly(3.60, t=12 + k))
    assert low.pct < 30


def test_estimate_never_rises_while_armed():
    est = bt.BatteryEstimator(bt.BatteryConfig(cells=CELLS))
    est.update(_rest(4.0, t=0))
    low = None
    for k in range(30):
        low = est.update(_fly(3.70, t=1 + k))
    recovered = est.update(_fly(3.90, t=40))  # throttle cut, sag recovers
    assert recovered.pct <= low.pct


def test_flight_mah_is_recorded_at_disarm_for_calibration():
    est = bt.BatteryEstimator(bt.BatteryConfig(cells=CELLS))
    est.update(_rest(4.1, consumed_mah=120, t=0))
    est.update(_fly(3.9, t=1, current_a=15, consumed_mah=120))
    est.update(_fly(3.8, t=200, current_a=15, consumed_mah=2320))
    est.update(bt.BatterySample(voltage=3.8 * CELLS, consumed_mah=2320, armed=False, t=210))
    assert est.last_flight_consumed_mah == 2200


# --- governor ------------------------------------------------------------- #

GOV = bt.BatteryGovernor(bt.BatteryConfig(cells=CELLS))


def test_takeoff_refused_below_minimum():
    v = GOV.preflight(20, 5)
    assert not v.ok and "takeoff minimum" in v.reason


def test_takeoff_allowed_on_a_healthy_pack():
    assert GOV.preflight(80, 10).ok


def test_unknown_battery_blocks_takeoff_unless_configured():
    assert not GOV.preflight(None, 5).ok
    lax = bt.BatteryGovernor(bt.BatteryConfig(allow_unknown_battery=True))
    assert lax.preflight(None, 5).ok


def test_far_from_home_on_a_low_pack_lands_rather_than_trying_to_get_back():
    # 18% left, 600 m out: the trip home alone would eat the rest.
    v = GOV.in_flight(18, dist_home_m=600, alt_m=15)
    assert v.action == "land_now"


def test_near_home_on_a_low_pack_returns():
    # Trip home from 80 m at 10 m is ~6%; 24% leaves under the 20% reserve.
    v = GOV.in_flight(24, dist_home_m=80, alt_m=10)
    assert v.action == "return_home"


def test_plenty_left_is_ok():
    assert GOV.in_flight(70, dist_home_m=50, alt_m=10).ok


def test_low_battery_mode_refuses_recording_and_climbing():
    # 30% is under low_battery_pct (35) but still clear of the reserve here.
    assert not GOV.check_action(30, 0.5, 0, 5, kind="start_recording").ok
    climb = GOV.check_action(30, GOV.climb_cost(15), 0, 20, kind="goto")
    assert not climb.ok and "10 m" in climb.reason
    assert GOV.check_action(30, GOV.transit_cost(10), 5, 5, kind="goto").ok


def test_actions_left_shrinks_to_zero_as_the_pack_drains():
    counts = [GOV.actions_left(p, 30, 10) for p in (90, 60, 40, 25)]
    assert counts == sorted(counts, reverse=True)
    assert counts[-1] == 0


def test_an_action_that_would_strand_the_aircraft_is_denied():
    # 40% now; a 5-minute excursion to 900 m out cannot also pay the trip back.
    cost = GOV.transit_cost(900)
    v = GOV.check_action(40, cost, dist_home_after_m=900, alt_after_m=10)
    assert not v.ok and "get home" in v.reason


# --- calibration arithmetic ----------------------------------------------- #

def test_voltage_correction_from_a_full_charge():
    ratio = bt.voltage_mult_correction(16.2, 16.8)
    assert ratio == pytest.approx(16.8 / 16.2)


def test_voltage_correction_refuses_a_wrong_pin_or_cell_count():
    with pytest.raises(ValueError, match="cell count"):
        bt.voltage_mult_correction(12.6, 16.8)   # reads like a 3S pack
    with pytest.raises(ValueError, match="BATT_VOLT_PIN"):
        bt.voltage_mult_correction(0.0, 16.8)


def test_current_correction_from_charger_mah():
    assert bt.current_scale_correction(2000, 2600) == pytest.approx(1.3)


def test_current_correction_refuses_a_dead_sensor():
    with pytest.raises(ValueError, match="BATT_CURR_PIN"):
        bt.current_scale_correction(12, 2600)


def test_config_reads_the_existing_yaml_names():
    cfg = bt.BatteryConfig.from_dict({"cells": 6, "capacity_mah": 10000,
                                      "min_takeoff_pct": 50, "bogus": 1})
    assert (cfg.cells, cfg.capacity_mah, cfg.min_takeoff_pct) == (6, 10000.0, 50.0)


# --- mission loop wiring -------------------------------------------------- #

class Backend:
    def __init__(self, pct, pose=(0.0, 0.0, 0.0, 0.0)):
        self.pct = pct
        self.pose = pose
        self.calls = []

    def get_battery(self):
        return {"remaining": self.pct}

    def get_pose(self):
        return self.pose

    def takeoff(self, alt):
        self.calls.append(("takeoff", alt))
        return True

    def goto(self, n, e, a):
        self.calls.append(("goto", n, e, a))
        return True

    def rtl(self, alt_m=None):
        self.calls.append(("rtl",))
        return True

    def land(self, heading_deg=None):
        self.calls.append(("land",))
        return True

    def log_event(self, *a, **k):
        pass


@pytest.fixture(autouse=True)
def no_sdk(monkeypatch):
    monkeypatch.setattr(rl, "DRONE_SDK_AVAILABLE", False)


def _loop(backend, vehicle="quadcopter", sdk=None):
    loop = rl.MissionLoop(backend=backend, drone_sdk=sdk, conversation_id="c",
                          vehicle_class=get_class(vehicle))
    loop._battery_gov = bt.BatteryGovernor(bt.BatteryConfig(cells=CELLS)) \
        if vehicle == "quadcopter" else None
    loop._home_yaw_rad = 0.0
    return loop


def test_takeoff_phase_is_refused_on_a_low_pack():
    b = Backend(pct=22)
    loop = _loop(b)
    r = loop._execute_phase({"type": "arm_and_takeoff", "altitude_m": 5}, None)
    assert r["failed"] and r["battery_abort"]
    assert b.calls == [], "nothing may be commanded"


def test_takeoff_phase_proceeds_on_a_good_pack():
    b = Backend(pct=85)
    r = _loop(b)._execute_phase({"type": "arm_and_takeoff", "altitude_m": 5}, None)
    assert r["success"] and b.calls == [("takeoff", 5)]


def test_unaffordable_leg_in_the_air_goes_home_and_lands():
    b = Backend(pct=38, pose=(0.0, 20.0, 10.0, 0.0))
    r = _loop(b)._execute_phase({"type": "nav", "north_m": 900, "east_m": 0, "alt_m": 10}, None)
    assert r["battery_abort"]
    assert b.calls == [("rtl",), ("land",)]


def test_return_home_and_land_are_never_blocked():
    b = Backend(pct=5, pose=(0.0, 300.0, 10.0, 0.0))
    loop = _loop(b)
    assert loop._execute_phase({"type": "return_home"}, None)["success"] is not False
    assert ("rtl",) in b.calls


def test_the_sdk_guard_having_fired_stops_the_mission_without_new_commands():
    class SDK:
        def battery_guard_tripped(self):
            return {"action": "return_home", "reason": "battery 24% only just covers the trip"}

        def get_battery(self):
            return {"remaining": 24}

    b = Backend(pct=24, pose=(0.0, 50.0, 10.0, 0.0))
    r = _loop(b, sdk=SDK())._execute_phase({"type": "nav", "north_m": 10, "alt_m": 10}, None)
    assert r["battery_abort"] and b.calls == []


def test_mission_does_not_replan_a_battery_abort():
    b = Backend(pct=22)
    loop = _loop(b)
    mission = rl.Mission.from_dict({"phases": [
        {"type": "arm_and_takeoff", "altitude_m": 5},
        {"type": "nav", "north_m": 10, "alt_m": 5},
    ]})
    result = loop.run(mission)
    assert not result.success
    assert "battery" in result.failure_reason
    assert b.calls == []


def test_fixed_wing_is_not_priced_with_the_quad_model():
    b = Backend(pct=22)
    loop = _loop(b, vehicle="fixedwing")
    assert loop._battery_gate_phase({"type": "arm_and_takeoff", "altitude_m": 40}) is None
