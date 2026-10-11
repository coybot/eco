"""'Do an arm test' from the app.

The planner had no arm-only phase, so it refused ("there's no arm-only
command"). arm_test arms, stays armed a few seconds, disarms, never takes off,
and works on both ArduPilot and PX4 because it goes through drone_sdk.

Run: cd eco && python3 -m pytest drone/common/tests/test_arm_test_phase.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backends  # noqa: E402
import mission_vocab  # noqa: E402
import reasoning_loop as rl  # noqa: E402
from vehicle_class import get_class  # noqa: E402


class FakeSdk:
    """Just the drone_sdk calls arm_test makes, with scripted behaviour."""

    def __init__(self, flying=False, arm_ok=True, drops_after=None):
        self.flying, self.arm_ok, self.drops_after = flying, arm_ok, drops_after
        self.armed, self.disarms, self.polls = False, 0, 0

    def is_flying(self):
        return self.flying

    def arm(self):
        if self.arm_ok:
            self.armed = True
        return self.arm_ok

    def is_armed(self):
        self.polls += 1
        if self.drops_after is not None and self.polls > self.drops_after:
            self.armed = False
        return self.armed

    def safe_disarm(self):
        self.disarms += 1
        self.armed = False
        return True


def _backend(sdk):
    b = backends.HardwareBackend.__new__(backends.HardwareBackend)
    b._sdk = sdk
    b._is_fixedwing = lambda: False
    return b


def test_arm_hold_disarm(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda s: None)
    sdk = FakeSdk()
    r = _backend(sdk).arm_test(0.0)
    assert r['ok'] and sdk.disarms == 1 and not sdk.armed


def test_a_refused_arm_reports_why_and_still_disarms():
    sdk = FakeSdk(arm_ok=False)
    r = _backend(sdk).arm_test(0.0)
    assert not r['ok'] and 'did not stay armed' in r['reason'] and sdk.disarms == 1


def test_an_fc_that_disarms_itself_mid_hold_is_a_failure(monkeypatch):
    """The quadcopter's battery failsafe: armed for a moment, then 'Battery Failsafe - Disarming'."""
    monkeypatch.setattr('time.sleep', lambda s: None)
    sdk = FakeSdk(drops_after=1)
    r = _backend(sdk).arm_test(5.0)
    assert not r['ok'] and 'disarmed itself' in r['reason']


def test_it_will_not_arm_a_flying_aircraft():
    sdk = FakeSdk(flying=True)
    r = _backend(sdk).arm_test(3.0)
    assert not r['ok'] and 'already flying' in r['reason']
    assert not sdk.armed


def test_an_abort_cuts_the_hold_short_but_still_disarms(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda s: None)
    sdk = FakeSdk()
    r = _backend(sdk).arm_test(10.0, should_stop=lambda: True)
    assert r['ok'] and sdk.disarms == 1


def test_fixed_wing_is_refused():
    b = _backend(FakeSdk())
    b._is_fixedwing = lambda: True
    assert not b.arm_test(3.0)['ok']


# -- the phase through the mission loop ---------------------------------------

class PhaseBackend:
    def __init__(self, result):
        self.result, self.calls = result, []

    def arm_test(self, hold_s, should_stop=None):
        self.calls.append(hold_s)
        return self.result

    def get_pose(self):
        return (0.0, 0.0, 0.0, 0.0)

    def get_battery(self):
        return {"remaining": 100.0}

    def configure_safety(self, **kw):
        return True

    def log_event(self, *a, **k):
        pass

    def __getattr__(self, name):
        raise AssertionError(f"arm_test must not call backend.{name}")


def _run(result, phase):
    backend = PhaseBackend(result)
    loop = rl.MissionLoop(backend=backend, conversation_id="c", vehicle_class=get_class("quadcopter"))
    loop._get_vlm = lambda: None
    loop._get_nav = lambda: None
    loop._get_perception = lambda: None
    loop._battery_gov = None
    res = loop.run(rl.Mission(mission_id="m", phases=[phase], conversation_id="c", original_message="do an arm test"))
    return res, backend


def test_the_phase_runs_and_never_flies():
    res, backend = _run({'ok': True, 'reason': ''}, {"type": "arm_test", "hold_s": 2})
    assert res.success and backend.calls == [2.0]
    assert any("Arm test passed" in f for f in res.findings), "the operator's reply is built from findings"


def test_the_hold_is_capped_and_defaults_to_three_seconds():
    _, backend = _run({'ok': True, 'reason': ''}, {"type": "arm_test", "hold_s": 999})
    assert backend.calls == [rl.MissionLoop.ARM_TEST_MAX_HOLD_S]
    _, backend = _run({'ok': True, 'reason': ''}, {"type": "arm_test"})
    assert backend.calls == [3.0]


def test_a_failed_arm_test_fails_the_mission_with_the_reason():
    res, _ = _run({'ok': False, 'reason': 'battery failsafe'}, {"type": "arm_test"})
    assert not res.success and 'battery failsafe' in (res.failure_reason or '')


def test_vocab_knows_the_phase():
    assert "arm_test" in mission_vocab.PHASE_SCHEMAS
    assert "arm_test" in rl.MissionLoop._PHASE_DISPATCH
    assert "arm_test" in rl.MissionLoop._BATTERY_FREE_PHASES   # a ground arm costs no flight energy


# -- takeoff: arm last, and only a still-armed aircraft has taken off -----------

def test_hardware_takeoff_does_not_arm_before_the_preflight_checks():
    """A separate arm() first left the PX4 test aircraft armed through ~10 s of checks and
    PX4 auto-disarmed it (COM_DISARM_PRFLT) as the takeoff command went out."""
    calls = []

    class Sdk:
        def arm(self):
            calls.append('arm')
            return True

        def takeoff(self, alt):
            calls.append(('takeoff', alt))
            return True

    b = _backend(Sdk())
    assert b.takeoff(1.0) is True
    assert calls == [('takeoff', 1.0)]


def test_a_failed_takeoff_is_not_retried():
    """A replan re-armed the aircraft, and return_home/land then went to a disarmed one."""
    class B(PhaseBackend):
        def takeoff(self, alt):
            self.calls.append(('takeoff', alt))
            return False

    backend = B({'ok': True})
    loop = rl.MissionLoop(backend=backend, conversation_id="c", vehicle_class=get_class("quadcopter"))
    loop._get_vlm = lambda: None
    loop._get_nav = lambda: None
    loop._get_perception = lambda: None
    loop._battery_gov = None
    res = loop.run(rl.Mission(mission_id="m", conversation_id="c", original_message="take off 1m then land",
                              phases=[{"type": "arm_and_takeoff", "altitude_m": 1},
                                      {"type": "return_home"}, {"type": "land"}]))
    assert not res.success
    assert backend.calls == [('takeoff', 1)], "one attempt, and nothing after it"


def _fake_takeoff_env(monkeypatch, armed_bits, landed_state=2, autopilot='ardupilot', params=None, sent=None):
    import types
    import drone_sdk as sdk
    import px4_compat
    monkeypatch.setattr(sdk, '_autopilot', px4_compat.AUTOPILOT_PX4 if autopilot == 'px4' else px4_compat.AUTOPILOT_ARDUPILOT)
    msgs = {'HEARTBEAT': types.SimpleNamespace(base_mode=armed_bits),
            'EXTENDED_SYS_STATE': types.SimpleNamespace(landed_state=landed_state),
            # 25.0 m AMSL here; home recorded (stale) 6 m lower, as on the test aircraft indoors
            'GLOBAL_POSITION_INT': types.SimpleNamespace(alt=25000, relative_alt=6000)}
    sent = sent if sent is not None else []

    def mav_command(fn, ack, timeout=3):
        class Mav:
            def command_long_send(self, *a):
                sent.append(a)
        fn(types.SimpleNamespace(mav=Mav(), target_system=1, target_component=1))
        return True, 0
    monkeypatch.setattr(sdk, '_get_param', lambda n, timeout=2.0: (params or {}).get(n))
    for name, val in {
        '_connect_locked': lambda: None, '_pilot_refuses': lambda *a: False,
        'is_flying': lambda: False, '_set_mode_confirmed': lambda *a, **k: True,
        '_battery_preflight': lambda *a: (True, ''), '_check_preflight_status': lambda: (True, []),
        '_fresh_position': lambda *a, **k: (37.0, -122.0, 1.3),   # the drifting indoor altitude
        '_position_relative': lambda: (37.0, -122.0, 1.3), 'is_armed': lambda: True,
        '_mav_command': mav_command, 'start_battery_guard': lambda *a, **k: None,
        '_start_pilot_watch': lambda: None, '_drain_statustext': lambda *a, **k: [],
        '_fc_cached': lambda t, max_age=1.5: msgs.get(t),
        '_disarm_if_on_ground': lambda: None,
    }.items():
        monkeypatch.setattr(sdk, name, val)
    monkeypatch.setattr(sdk, '_pilot_control', None)
    monkeypatch.setattr(sdk, 'land', lambda: None)
    clock = [1000.0]
    monkeypatch.setattr('time.time', lambda: clock[0])
    monkeypatch.setattr('time.sleep', lambda s: clock.__setitem__(0, clock[0] + max(s, 0.01)))
    return sdk


def test_altitude_alone_is_not_a_takeoff_if_the_fc_disarmed(monkeypatch):
    """PX4 test aircraft: PX4 auto-disarmed on the ground, the indoor altitude drifted to
    1.3 m, and takeoff() said "Reached 1.3m"."""
    sdk = _fake_takeoff_env(monkeypatch, armed_bits=0)
    assert sdk.takeoff(1.0) is False


def test_a_real_climb_still_succeeds(monkeypatch):
    sdk = _fake_takeoff_env(monkeypatch, armed_bits=0x80)
    assert sdk.takeoff(1.0) is True


def test_drifting_altitude_on_the_ground_is_not_a_takeoff(monkeypatch):
    """PX4 test aircraft, second try: armed, PX4 never climbed, GPS height drifted to 0.9 m -> "Reached 0.9m"."""
    sdk = _fake_takeoff_env(monkeypatch, armed_bits=0x80, landed_state=1)   # FC: ON_GROUND
    assert sdk.takeoff(1.0) is False


def test_px4_takeoff_target_is_above_where_it_is_not_above_a_stale_home(monkeypatch):
    sent = []
    sdk = _fake_takeoff_env(monkeypatch, armed_bits=0x80, autopilot='px4',
                            params={'MIS_TAKEOFF_ALT': 0.5}, sent=sent)
    assert sdk.takeoff(1.0) is True
    takeoff = [a for a in sent if a[2] == 22][0]
    assert takeoff[-1] == pytest.approx(25.0 + 1.0)     # current AMSL + 1 m, not home (19 m) + 1 m


def test_px4_refuses_a_takeoff_lower_than_its_minimum_instead_of_climbing_higher(monkeypatch):
    """PX4 clamps a takeoff up to MIS_TAKEOFF_ALT (5 m on the test aircraft): '1 m' would have been 5 m indoors."""
    sent = []
    sdk = _fake_takeoff_env(monkeypatch, armed_bits=0x80, autopilot='px4',
                            params={'MIS_TAKEOFF_ALT': 5.0}, sent=sent)
    assert sdk.takeoff(1.0) is False
    assert sent == [], "refused before arming or sending anything"


def test_px4_takeoff_enters_an_armable_mode_before_arming(monkeypatch):
    """A PX4 left in Land by the previous flight cannot arm (red LED)."""
    sdk = _fake_takeoff_env(monkeypatch, armed_bits=0x80, autopilot='px4', params={'MIS_TAKEOFF_ALT': 0.5})
    modes = []
    monkeypatch.setattr(sdk, '_set_mode_confirmed', lambda m, *a, **k: modes.append(m) or True)
    assert sdk.takeoff(1.0) is True
    assert modes and modes[0] == 'GUIDED'


@pytest.mark.parametrize('autopilot', ['px4', 'ardupilot'])
def test_landing_leaves_land_mode_once_disarmed(monkeypatch, autopilot):
    import drone_sdk
    real_land = drone_sdk.land                      # the fixture stubs land() out
    sdk = _fake_takeoff_env(monkeypatch, armed_bits=0, autopilot=autopilot)
    switched = []
    monkeypatch.setattr(sdk, '_mav_send', lambda fn: None)
    monkeypatch.setattr(sdk, '_wait_for_disarm', lambda timeout=30: True)
    monkeypatch.setattr(sdk, 'is_armed', lambda: False)
    monkeypatch.setattr(sdk, '_switch_mode', lambda m, timeout=8, yield_to_pilot=False: switched.append(m) or True)
    real_land()
    assert switched == ['GUIDED']


def test_takeoff_waits_for_the_armed_heartbeat_instead_of_one_stale_read(monkeypatch):
    """PX4 test aircraft: PX4 armed, but the heartbeat 0.3 s after the ACK was still the
    old disarmed one, so takeoff gave up and PX4 auto-disarmed 10 s later."""
    import types
    sdk = _fake_takeoff_env(monkeypatch, armed_bits=0x80)
    monkeypatch.setattr(sdk, 'is_armed', lambda: False)            # not armed when takeoff starts
    monkeypatch.setattr(sdk, '_arm_checked', lambda: (True, 0))
    monkeypatch.setattr(sdk, '_pump', lambda s=0.2: __import__('time').sleep(s))   # advances the fake clock
    reads = {'n': 0}
    es = types.SimpleNamespace(landed_state=2)

    def cached(t, max_age=1.5):
        if t == 'HEARTBEAT':
            reads['n'] += 1
            return types.SimpleNamespace(base_mode=0x80 if reads['n'] > 3 else 0)   # armed on the 4th read
        return es if t == 'EXTENDED_SYS_STATE' else None
    monkeypatch.setattr(sdk, '_fc_cached', cached)
    assert sdk.takeoff(1.0) is True


def test_an_arm_that_never_shows_is_disarmed_before_giving_up(monkeypatch):
    import types
    sdk = _fake_takeoff_env(monkeypatch, armed_bits=0)
    monkeypatch.setattr(sdk, 'is_armed', lambda: False)
    monkeypatch.setattr(sdk, '_arm_checked', lambda: (True, 0))
    monkeypatch.setattr(sdk, '_pump', lambda s=0.2: __import__('time').sleep(s))   # advances the fake clock
    disarms = []
    monkeypatch.setattr(sdk, '_disarm_if_on_ground', lambda: disarms.append(1))
    assert sdk.takeoff(1.0) is False
    assert disarms == [1]


def test_safe_disarm_trusts_the_land_detector_over_drifting_altitude(monkeypatch):
    """After a flight PX4's relative altitude read ~2 m on the ground; the
    altitude rule refused and the arm test left the aircraft armed."""
    import drone_sdk as sdk
    disarmed = []
    monkeypatch.setattr(sdk, 'disarm', lambda: disarmed.append(1) or True)
    monkeypatch.setattr(sdk, '_fc_says_on_ground', lambda: True)
    assert sdk.safe_disarm() is True and disarmed == [1]
    monkeypatch.setattr(sdk, '_fc_says_on_ground', lambda: False)
    with pytest.raises(RuntimeError):
        sdk.safe_disarm()


def test_takeoff_waits_for_the_position_estimate_before_arming(monkeypatch):
    """ArduCopter SITL, 20 s after boot: "PreArm: Need Position Estimate" failed the takeoff."""
    sdk = _fake_takeoff_env(monkeypatch, armed_bits=0x80)
    monkeypatch.setattr(sdk, 'is_armed', lambda: False)
    tries = []

    def arm():
        tries.append(1)
        return (len(tries) >= 3, 0 if len(tries) >= 3 else 1)
    monkeypatch.setattr(sdk, '_arm_checked', arm)
    monkeypatch.setattr(sdk, '_fc_said', lambda frag, since: frag == 'position estimate')
    monkeypatch.setattr(sdk, '_pump', lambda s=0.2: __import__('time').sleep(s))
    assert sdk.takeoff(1.0) is True
    assert len(tries) == 3
