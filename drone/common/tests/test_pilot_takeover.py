"""The pilot's transmitter wins over coybot.

Flying the quadcopter: landing it by hand mid-mission fought coybot. ArduCopter
ignores the sticks in GUIDED, and when the pilot flipped the mode switch,
coybot's next stop/hold put the aircraft straight back into GUIDED (the battery
guard would have forced RTL). Now moving a stick or changing the mode hands the
aircraft to the pilot and coybot refuses to command it until it lands.

Run: cd eco && python3 -m pytest drone/common/tests/test_pilot_takeover.py -v
"""

import sys
import time
from pathlib import Path

import pytest
from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as mavlink

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drone_sdk  # noqa: E402
import pilot_override as po  # noqa: E402
import reasoning_loop as rl  # noqa: E402
from vehicle_class import get_class  # noqa: E402

ARMED = mavlink.MAV_MODE_FLAG_SAFETY_ARMED | mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
MODES = {'STABILIZE': 0, 'ALT_HOLD': 2, 'GUIDED': 4, 'LOITER': 5, 'RTL': 6, 'LAND': 9, 'BRAKE': 17}
REST = {1: 1500, 2: 1500, 3: 1000, 4: 1500}   # sticks centred, throttle down


# ---------------------------------------------------------------- the decision

def test_the_first_reading_is_where_the_sticks_rest_not_a_takeover():
    w = po.PilotWatch()
    assert w.on_rc({1: 1500, 2: 1500, 3: 1350, 4: 1500}, 0) is None
    assert w.on_rc({1: 1500, 2: 1500, 3: 1350, 4: 1500}, 0.1) is None


def test_a_stick_held_over_the_deadband_is_the_pilot():
    w = po.PilotWatch()
    w.on_rc(REST, 0)
    pushed = {**REST, 2: 1300}
    assert w.on_rc(pushed, 0.1) is None, "one reading could be a glitch"
    why = w.on_rc(pushed, 0.2)
    assert why and "pitch" in why and "-200" in why


def test_jitter_and_a_single_glitch_are_not():
    w = po.PilotWatch()
    w.on_rc(REST, 0)
    assert w.on_rc({**REST, 1: 1560, 4: 1450}, 0.1) is None     # inside the deadband
    assert w.on_rc({**REST, 3: 1400}, 0.2) is None              # one frame
    assert w.on_rc(REST, 0.3) is None
    assert w.on_rc({**REST, 3: 1400}, 0.4) is None              # streak restarted


def test_a_receiver_without_signal_is_ignored():
    w = po.PilotWatch()
    w.on_rc(REST, 0)
    for t, bad in enumerate(({1: 0, 2: 0, 3: 0, 4: 0}, {**REST, 3: 65535}, {1: 1500})):
        assert w.on_rc(bad, t) is None
    assert w.on_rc({**REST, 3: 1200}, 5) is None, "invalid frames must not count toward the streak"


def test_a_mode_coybot_asked_for_is_its_own():
    w = po.PilotWatch()
    w.on_mode('STABILIZE', 0)
    w.requested('GUIDED', 1.0)
    assert w.on_mode('GUIDED', 2.0) is None
    w.requested('LAND', 10.0)
    assert w.on_mode('LAND', 11.0) is None


def test_a_mode_nobody_asked_for_is_the_pilot_or_a_failsafe():
    w = po.PilotWatch()
    w.requested('GUIDED', 0.0)
    w.on_mode('GUIDED', 0.5)
    why = w.on_mode('LOITER', 30.0)
    assert why and "GUIDED to LOITER" in why
    w.requested('RTL', 0.0)
    assert w.on_mode('RTL', 40.0), "asked for long ago is not asked for now"


def test_only_a_switch_into_guided_hands_back():
    w = po.PilotWatch()
    w.on_mode('LOITER', 0)
    assert not w.handed_back('LOITER')
    assert w.handed_back('GUIDED')
    w.on_mode('GUIDED', 1)
    assert not w.handed_back('GUIDED')


# ------------------------------------------------ drone_sdk over a real parser

def _enc():
    return mavlink.MAVLink(None, srcSystem=1, srcComponent=1)


def _hb(mode, armed=True):
    e = _enc()
    return e.heartbeat_encode(mavlink.MAV_TYPE_QUADROTOR, mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
                              ARMED if armed else mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                              MODES[mode], mavlink.MAV_STATE_ACTIVE).pack(e)


def _rc(**ch):
    vals = dict(REST)
    vals.update({int(k[2:]): v for k, v in ch.items()})
    e = _enc()
    chans = [vals.get(n, 1500) for n in range(1, 19)]
    return e.rc_channels_encode(0, 16, *chans, 255).pack(e)


class Link(mavutil.mavfile):
    """pymavlink's own parser, cache and hooks over replayed FC traffic;
    records everything coybot sends back."""

    def __init__(self):
        self.data = bytearray()
        self.sent = []
        mavutil.mavfile.__init__(self, None, "replay", source_system=255)
        self.target_system, self.target_component = 1, 1

    def feed(self, *packets):
        for p in packets:
            self.data += p
        while self.recv_match(blocking=False) is not None:
            pass

    def recv(self, n=None):
        n = n or len(self.data)
        chunk, self.data[:] = bytes(self.data[:n]), self.data[n:]
        return chunk

    def write(self, buf):
        self.sent.append(buf)


@pytest.fixture
def fc(monkeypatch):
    link = Link()
    link.message_hooks.append(drone_sdk._on_message)
    monkeypatch.setattr(drone_sdk, "_connect", lambda: link)
    monkeypatch.setattr(drone_sdk, "_ensure_streams", lambda m: None)
    monkeypatch.setattr(drone_sdk, "_fc_heartbeat", None)
    monkeypatch.setattr(drone_sdk, "_pilot_watch", po.PilotWatch())
    monkeypatch.setattr(drone_sdk, "_pilot_control", None)
    monkeypatch.setattr(drone_sdk, "_pilot_handover", False)
    monkeypatch.setattr(drone_sdk, "_pilot_watch_on", True)   # as after coybot's takeoff
    link.feed(_hb('GUIDED'))
    return link


def test_moving_the_sticks_gives_the_pilot_control(fc):
    fc.feed(_rc(), _rc(ch2=1250))
    assert drone_sdk.pilot_control() is None
    fc.feed(_rc(ch2=1250))
    assert "pitch stick" in drone_sdk.pilot_control()
    assert drone_sdk._pilot_handover, "the watch thread still has to switch to LOITER"


def test_flipping_the_mode_switch_gives_the_pilot_control(fc):
    fc.feed(_hb('LAND'))
    assert "GUIDED to LAND" in drone_sdk.pilot_control()
    assert not drone_sdk._pilot_handover, "the pilot chose a mode: leave it"


def test_coybots_own_mode_changes_are_not_a_takeover(fc):
    drone_sdk._request_mode(fc, 'LAND')
    fc.feed(_hb('LAND'), _hb('LAND'))
    assert drone_sdk.pilot_control() is None


def test_once_the_pilot_has_it_coybot_commands_nothing(fc, monkeypatch):
    monkeypatch.setattr(drone_sdk, "_wait_for_disarm", lambda timeout=0: pytest.fail("land() ran"))
    fc.feed(_hb('LOITER'))
    fc.sent.clear()
    assert drone_sdk.goto(1.0, 2.0, 5.0) is False
    assert drone_sdk.set_velocity(1, 0, 0) is False
    assert drone_sdk.set_yaw(90) is False
    assert drone_sdk.land() is False
    assert drone_sdk.hold_here() is False
    assert drone_sdk.brake() is False
    assert drone_sdk._set_mode_confirmed('GUIDED') is False
    assert drone_sdk.takeoff(5) is False
    assert fc.sent == [], "not one byte to the flight controller"


def test_landing_and_disarming_gives_coybot_the_next_flight(fc):
    fc.feed(_hb('LAND'))
    assert drone_sdk.pilot_control()
    fc.feed(_hb('LAND', armed=False))
    assert drone_sdk.pilot_control() is None
    assert not drone_sdk._pilot_watch_on, "the watch ends with the flight"


def test_the_transmitter_selecting_guided_hands_back(fc):
    fc.feed(_hb('LOITER'))
    assert drone_sdk.pilot_control()
    fc.feed(_hb('LOITER'), _hb('GUIDED'))
    assert drone_sdk.pilot_control() is None
    fc.feed(_rc(ch3=1500), _rc(ch3=1500))
    assert drone_sdk.pilot_control() is None, "the sticks rest where the pilot left them"


def test_a_goto_in_progress_stops_when_the_pilot_takes_over(fc, monkeypatch):
    positions = iter([(0.0, 0.0, 5.0)] * 3)
    monkeypatch.setattr(drone_sdk, "_offset_origin", lambda: (0.0, 0.0))
    monkeypatch.setattr(drone_sdk, "_battery_gates_apply", lambda: False)
    monkeypatch.setattr(drone_sdk.time, "sleep", lambda s: fc.feed(_hb('LOITER')))

    def pos(timeout=1.0):
        return next(positions, (0.0, 0.0, 5.0))
    monkeypatch.setattr(drone_sdk, "_fresh_position", pos)
    assert drone_sdk.goto_offset(20.0, 0.0, 5.0, timeout_s=60) is False
    assert drone_sdk.pilot_control()


@pytest.mark.parametrize("mode,refuse,expect", [
    ('GUIDED', (), 'LOITER'),
    ('BRAKE', (), 'LOITER'),
    ('GUIDED', ('LOITER',), 'ALT_HOLD'),   # no position estimate
    ('ALT_HOLD', (), None),                # the sticks already fly it
])
def test_grabbing_the_sticks_hands_the_pilot_a_mode_they_fly(monkeypatch, mode, refuse, expect):
    asked = []
    monkeypatch.setattr(drone_sdk, "get_flight_mode", lambda: mode)

    def switch(target, timeout=8, yield_to_pilot=False):
        asked.append(target)
        return target not in refuse
    monkeypatch.setattr(drone_sdk, "_switch_mode", switch)
    drone_sdk._hand_to_pilot()
    assert (asked[-1] if asked else None) == expect


def test_the_battery_guard_does_not_override_the_pilot(monkeypatch):
    class Gov:
        def in_flight(self, pct, dist, alt):
            return type("V", (), {"ok": False, "action": "return_home", "reason": "5% left"})()
    modes = []
    monkeypatch.setattr(drone_sdk, "_battery_models", lambda: (None, type("E", (), {"last_flight_consumed_mah": None})(), Gov()))
    monkeypatch.setattr(drone_sdk, "_position_relative", lambda: (0.0, 0.0, 5.0))
    monkeypatch.setattr(drone_sdk, "_pilot_control", {"reason": "the pilot moved the roll stick", "since": 0})
    monkeypatch.setattr(drone_sdk, "_switch_mode", lambda m, *a, **k: modes.append(m) or True)
    readings = iter([True, True, True, False])
    monkeypatch.setattr(drone_sdk, "get_battery", lambda: {"armed": next(readings), "remaining": 5})
    monkeypatch.setattr(drone_sdk, "_battery_guard_trip", None)
    drone_sdk._battery_guard_stop.clear()
    monkeypatch.setattr(drone_sdk._battery_guard_stop, "wait", lambda s: None)
    drone_sdk._battery_guard_loop(0)
    assert modes == [], "no RTL/LAND forced on the pilot"


# ------------------------------------------------------------ the mission loop

class Backend:
    def __init__(self):
        self.pilot = None
        self.gotos = 0

    def get_pose(self):
        return (0.0, 0.0, 5.0, 0.0)

    def get_battery(self):
        return {"remaining": 100.0}

    def takeoff(self, alt):
        return True

    def goto(self, n, e, a):
        self.gotos += 1
        self.pilot = "the pilot moved the throttle stick on the transmitter (+400 us)"
        return False

    def pilot_control(self):
        return self.pilot

    def land(self, heading_deg=None):
        return True

    def rtl(self, alt_m=None):
        return True

    def abort(self):
        pass

    def clear_abort(self):
        pass

    def configure_safety(self, **kw):
        return True

    def log_event(self, *a, **k):
        pass


def test_a_mission_ends_when_the_pilot_takes_over_without_replanning(monkeypatch):
    monkeypatch.setattr(rl, "DRONE_SDK_AVAILABLE", False)
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)
    backend = Backend()
    loop = rl.MissionLoop(backend=backend, conversation_id="c-1", vehicle_class=get_class("quadcopter"))
    loop._battery_gov = None
    replans = []
    monkeypatch.setattr(loop, "_replan", lambda *a: replans.append(a))
    result = loop.run(rl.Mission(mission_id="m", original_message="fly", conversation_id="c-1",
                                 phases=[{"type": "arm_and_takeoff", "altitude_m": 5},
                                         {"type": "nav", "forward_m": 10},
                                         {"type": "land"}]))
    assert not result.success
    assert result.summary == "The pilot took control"
    assert "throttle stick" in result.failure_reason
    assert replans == [] and backend.gotos == 1
