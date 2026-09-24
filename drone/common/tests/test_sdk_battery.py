"""drone_sdk's battery wiring against a fake MAVLink link.

battery.py decides; these check the SDK actually asks it, at the right
moments, and reads the FC's units correctly (mV, cA, mAh). Also that arming
no longer skips the FC's own pre-arm checks by default - the other half of
how a flat pack got airborne.

Run: cd eco && python3 -m pytest drone/common/tests/test_sdk_battery.py -v
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pymavlink")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import battery as bt  # noqa: E402
import drone_sdk as sdk  # noqa: E402
from pymavlink import mavutil  # noqa: E402

ARMED = mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED


class Msg(SimpleNamespace):
    def get_type(self):
        return self._type


def _msg(kind, **fields):
    return Msg(_type=kind, _timestamp=time.time(), **fields)


class FakeLink:
    """Just enough of a pymavlink connection for _read_battery_raw."""

    def __init__(self, volts, amps, consumed, fc_pct, armed):
        self.target_system = self.target_component = 1
        self.mav = SimpleNamespace(request_data_stream_send=lambda *a: None)
        sys_status = _msg("SYS_STATUS", voltage_battery=int(volts * 1000),
                          current_battery=int(amps * 100) if amps is not None else -1,
                          battery_remaining=fc_pct)
        self.messages = {
            "SYS_STATUS": sys_status,
            "BATTERY_STATUS": _msg("BATTERY_STATUS", current_consumed=consumed),
            "HEARTBEAT": _msg("HEARTBEAT", base_mode=ARMED if armed else 0),
        }
        self._queue = [sys_status]

    def recv_match(self, blocking=False, timeout=None, type=None):
        return self._queue.pop(0) if self._queue else None


@pytest.fixture
def fresh(monkeypatch):
    """A clean SDK battery state and a quadcopter config, per test."""
    monkeypatch.setattr(sdk, "_battery_cfg", None)
    monkeypatch.setattr(sdk, "_battery_guard_trip", None)
    monkeypatch.setattr(sdk, "_load_config", lambda: {"battery": {"cells": 4}})
    monkeypatch.setattr(sdk, "start_battery_guard", lambda *a, **k: None)
    monkeypatch.setattr(sdk, "_position_relative", lambda: (37.0, -122.0, 0.0))
    return monkeypatch


def _link(fresh, **kw):
    link = FakeLink(**kw)
    fresh.setattr(sdk, "_connect", lambda: link)
    return link


def test_units_and_the_frozen_counter(fresh):
    """15.0 V at rest on a 4S is ~25%, whatever the FC's 87% says."""
    _link(fresh, volts=15.0, amps=0.4, consumed=310, fc_pct=87, armed=False)
    b = sdk.get_battery()
    assert b["voltage"] == pytest.approx(15.0)
    assert b["current"] == pytest.approx(0.4)
    assert b["consumed_mah"] == 310
    assert b["fc_remaining"] == 87
    assert b["remaining"] == pytest.approx(bt.pct_from_cell_voltage(3.75), abs=0.5)
    assert b["source"] == "voltage_rest"


def test_unreadable_pack_reports_minus_one(fresh):
    _link(fresh, volts=0.0, amps=None, consumed=-1, fc_pct=87, armed=False)
    b = sdk.get_battery()
    assert b["remaining"] == -1
    assert b["warnings"]


def test_takeoff_refused_on_a_low_pack_never_arms(fresh):
    _link(fresh, volts=14.9, amps=0.4, consumed=0, fc_pct=87, armed=False)
    armed_calls = []
    fresh.setattr(sdk, "_drain_statustext", lambda **k: [])
    fresh.setattr(sdk, "_mav_send", lambda f: None)
    fresh.setattr(sdk, "_wait_for_mode", lambda *a, **k: True)
    fresh.setattr(sdk, "is_armed", lambda: False)
    fresh.setattr(sdk, "_arm_checked", lambda: armed_calls.append(1) or (True, 0))
    assert sdk.takeoff(5) is False
    assert armed_calls == []


def test_takeoff_refused_disarms_an_already_armed_quad_on_the_ground(fresh):
    _link(fresh, volts=14.9, amps=0.4, consumed=0, fc_pct=87, armed=False)
    disarmed = []
    fresh.setattr(sdk, "_drain_statustext", lambda **k: [])
    fresh.setattr(sdk, "_mav_send", lambda f: None)
    fresh.setattr(sdk, "_wait_for_mode", lambda *a, **k: True)
    fresh.setattr(sdk, "is_armed", lambda: True)
    fresh.setattr(sdk, "safe_disarm", lambda: disarmed.append(1))
    assert sdk.takeoff(5) is False
    assert disarmed == [1]


def test_arm_does_not_force_past_prearm_checks_by_default(fresh):
    sent = []
    fresh.setattr(sdk, "_send_arm", lambda force: sent.append(force) or (False, 4))
    fresh.setattr(sdk, "_drain_statustext",
                  lambda **k: ["PreArm: Battery 1 below minimum arming voltage"])
    ok, _ = sdk._arm_checked()
    assert not ok and sent == [False]


def test_force_arm_only_when_configured(fresh):
    fresh.setattr(sdk, "_load_config", lambda: {"allow_force_arm": True})
    sent = []
    fresh.setattr(sdk, "_send_arm", lambda force: sent.append(force) or (force, 0 if force else 4))
    fresh.setattr(sdk, "_drain_statustext", lambda **k: [])
    ok, _ = sdk._arm_checked()
    assert ok and sent == [False, True]


def test_recording_in_the_air_on_a_low_pack_is_refused(fresh):
    # Loaded 3.55 V/cell + sag allowance -> ~26%: under the low-battery line.
    _link(fresh, volts=14.2, amps=15.0, consumed=3000, fc_pct=87, armed=True)
    fresh.setattr(sdk, "_position_relative", lambda: (37.0, -122.0, 8.0))
    assert sdk.start_recording(max_seconds=60) is False


def test_recording_on_the_ground_is_free(fresh, monkeypatch):
    _link(fresh, volts=14.2, amps=0.3, consumed=0, fc_pct=87, armed=False)
    started = []

    class Rec:
        is_recording = True

        def __init__(self, *a, **k):
            pass

        def start(self):
            started.append(1)

    import media_recorder
    monkeypatch.setattr(media_recorder, "MediaRecorder", Rec)
    monkeypatch.setattr(sdk, "_recorder", None)
    assert sdk.start_recording(max_seconds=5) is True and started == [1]


def test_goto_that_strands_the_aircraft_is_refused(fresh):
    _link(fresh, volts=14.9, amps=18.0, consumed=2500, fc_pct=87, armed=True)
    fresh.setattr(sdk, "_flight_home", (37.0, -122.0))
    fresh.setattr(sdk, "_position_relative", lambda: (37.0, -122.0, 8.0))
    sent = []
    fresh.setattr(sdk, "_mav_send", lambda f: sent.append(f))
    # ~1.1 km north and back on a pack that is already low.
    assert sdk.goto(37.01, -122.0, 8) is False
    assert sent == []


def test_configure_battery_monitoring_leaves_board_pins_alone(fresh):
    written = {}
    fresh.setattr(sdk, "_set_param", lambda n, v: written.setdefault(n, v) is not None)
    fresh.setattr(sdk, "_get_param", lambda n: 4.0)
    sdk.configure_battery_monitoring(n_cells=4, capacity_mah=5000)
    for pin in ("BATT_VOLT_PIN", "BATT_CURR_PIN", "BATT_VOLT_MULT", "BATT_AMP_PERVLT"):
        assert pin not in written
    assert "BATT_MONITOR" not in written, "an enabled monitor is left as the board has it"
    assert written["BATT_ARM_VOLT"] == pytest.approx(4 * bt.cell_voltage_for_pct(35), abs=0.01)
    assert written["BATT_LOW_MAH"] == 1250 and written["BATT_CRT_MAH"] == 500


def test_configure_battery_monitoring_writes_pins_only_when_given(fresh):
    written = {}
    fresh.setattr(sdk, "_set_param", lambda n, v: written.setdefault(n, v) is not None)
    fresh.setattr(sdk, "_get_param", lambda n: 0.0)
    sdk.configure_battery_monitoring(fc_pins={"volt_pin": 10, "curr_pin": 11})
    assert written["BATT_VOLT_PIN"] == 10 and written["BATT_CURR_PIN"] == 11
    assert written["BATT_MONITOR"] == 4
    assert "BATT_VOLT_MULT" not in written


def test_battery_gates_skip_non_multirotors(fresh):
    fresh.setattr(sdk, "_load_config", lambda: {"vehicle_type": "fixedwing"})
    assert sdk.check_battery_for("goto", 99.0) == (True, "")
