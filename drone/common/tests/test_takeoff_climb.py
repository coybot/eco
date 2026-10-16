"""drone_sdk.takeoff judges the climb in AMSL from where it began, never relative to home.

PX4 climbs `altitude_m` above where the aircraft IS, and home does not hold
still: PX4 v1.17 re-publishes home with a different altitude mid-flight (SITL
moved it 584.13 -> 585.59 -> 584.53 -> 585.49 m in one 22 s flight), so the
altitude above home jumped ~1 m second to second. A real 1 m climb read as
0.5 m and the takeoff was abandoned. The test aircraft showed the same
(+1.1 m disarmed, -1.9 m mid-run).

Run: cd eco && python3 -m pytest drone/common/tests/test_takeoff_climb.py -v
"""

import sys
from pathlib import Path

import pytest

pytest.importorskip("pymavlink")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import drone_sdk as sdk  # noqa: E402
from test_arm_test_phase import _fake_takeoff_env  # noqa: E402


def test_a_real_climb_counts_whatever_the_starting_altitude():
    assert sdk._climb_reached(585.13, 584.13, 1.0)
    assert sdk._climb_reached(26.0, 23.0, 3.0)


def test_drift_on_the_ground_is_not_a_climb():
    # estimate wandered +0.9 m with the props barely turning, asked for 3 m
    assert not sdk._climb_reached(584.9, 584.0, 3.0)
    assert not sdk._climb_reached(584.0, 584.0, 3.0)


def test_close_enough_threshold_matches_the_old_target():
    # 1 m: needs >= 0.85 m; 10 m: needs >= 9.5 m (the 0.5 m slack caps it)
    assert sdk._climb_reached(0.85, 0.0, 1.0) and not sdk._climb_reached(0.84, 0.0, 1.0)
    assert sdk._climb_reached(9.5, 0.0, 10.0) and not sdk._climb_reached(9.4, 0.0, 10.0)


def _climb(monkeypatch, amsl, rel):
    """takeoff(1 m) against an FC whose AMSL and above-home readings follow these lists."""
    sdk_ = _fake_takeoff_env(monkeypatch, armed_bits=0x80, autopilot='px4', params={'MIS_TAKEOFF_ALT': 0.5})
    a, r = iter(amsl), iter(rel)
    monkeypatch.setattr(sdk_, '_fresh_amsl', lambda *x, **k: next(a, amsl[-1]))
    monkeypatch.setattr(sdk_, '_fresh_position', lambda *x, **k: (37.0, -122.0, next(r, rel[-1])))
    return sdk_.takeoff(1.0)


def test_home_moving_mid_climb_does_not_hide_a_real_climb(monkeypatch):
    """PX4 SITL: climbed 584.13 -> 585.12 m AMSL while home jumped and the
    above-home reading went 0.06, -1.20, -1.32, -0.10, 0.07, -0.73, 0.55."""
    assert _climb(monkeypatch,
                  amsl=[584.13, 584.23, 584.28, 584.38, 584.55, 584.87, 585.05],
                  rel=[0.06, -1.20, -1.32, -0.10, 0.07, -0.73, 0.55]) is True


def test_home_moving_down_on_the_ground_is_not_a_climb(monkeypatch):
    """Above-home leaps +1.4 m while the aircraft has not left the ground (AMSL flat)."""
    assert _climb(monkeypatch, amsl=[584.13] * 3, rel=[0.0, 1.4, 1.4]) is False


def test_a_slow_px4_climb_is_not_landed_while_still_climbing(monkeypatch):
    """PX4 SITL: 1 m takeoff, ~3 s on the ground, then Hold creeps the last part;
    0.85 m came 6.4-9.2 s after arming and some takeoffs were landed at 10 s."""
    import time
    sdk_ = _fake_takeoff_env(monkeypatch, armed_bits=0x80, autopilot='px4', params={'MIS_TAKEOFF_ALT': 0.5})
    t0 = time.time()      # the fixture's fake clock
    # nothing for 3 s, then 0.07 m/s: 0.85 m at ~15 s
    monkeypatch.setattr(sdk_, '_fresh_amsl',
                        lambda *a, **k: 584.0 + max(0.0, time.time() - t0 - 3.0) * 0.07)
    assert sdk_.takeoff(1.0) is True


@pytest.mark.parametrize("rel_now,target",[(1.5, 2.5), (-1.9, -0.9), (0.0, 1.0)])
def test_ardupilot_takeoff_target_is_above_where_it_is(monkeypatch, rel_now, target):
    """ArduCopter's NAV_TAKEOFF altitude is above home. ArduCopter SITL with home
    1.5 m below: '1 m' was rejected as already reached; 1.9 m above: it climbed 2.9 m."""
    sent = []
    sdk_ = _fake_takeoff_env(monkeypatch, armed_bits=0x80, sent=sent)
    monkeypatch.setattr(sdk_, '_fresh_position', lambda *a, **k: (37.0, -122.0, rel_now))
    assert sdk_.takeoff(1.0) is True
    takeoff = [a for a in sent if a[2] == 22][0]
    assert takeoff[-1] == pytest.approx(target)


def _rejected_takeoff(monkeypatch, amsl_after):
    """ArduCopter (no land state) rejects the takeoff; AMSL after that is `amsl_after`."""
    real_disarm_if_on_ground = sdk._disarm_if_on_ground
    sdk_ = _fake_takeoff_env(monkeypatch, armed_bits=0x80)
    monkeypatch.setattr(sdk_, '_disarm_if_on_ground', real_disarm_if_on_ground)
    monkeypatch.setattr(sdk_, '_mav_command', lambda fn, ack, timeout=3: (ack != 22, 4))
    monkeypatch.setattr(sdk_, '_fc_says_on_ground', lambda: None)
    monkeypatch.setattr(sdk_, '_fresh_position', lambda *a, **k: (37.0, -122.0, 1.5))   # home 1.5 m below
    amsl = iter([25.0])
    monkeypatch.setattr(sdk_, '_fresh_amsl', lambda *a, **k: next(amsl, amsl_after))
    calls = []
    monkeypatch.setattr(sdk_, 'disarm', lambda: calls.append('disarm') or True)

    def guarded():
        calls.append('safe_disarm')
        raise RuntimeError("safe_disarm() refused: drone is 1.5m above ground")
    monkeypatch.setattr(sdk_, 'safe_disarm', guarded)
    assert sdk_.takeoff(1.0) is False
    return calls


def test_a_rejected_takeoff_does_not_leave_it_armed_on_the_ground(monkeypatch):
    """ArduCopter SITL: takeoff rejected, then safe_disarm() refused on the 1.5 m
    above-home reading and the aircraft sat armed on the ground."""
    assert _rejected_takeoff(monkeypatch, amsl_after=25.0) == ['disarm']


def test_a_rejected_takeoff_that_somehow_climbed_is_not_force_disarmed(monkeypatch):
    assert _rejected_takeoff(monkeypatch, amsl_after=27.0) == ['safe_disarm']
