"""On PX4, every altitude above home is measured from the ground the takeoff began from.

PX4 v1.17 moves its home altitude by ~1 m mid-flight (HomePosition altitude
correction; SITL: 584.13 -> 585.59 -> 584.53 -> 585.49 m in one 22 s flight).
"return home at 1 m" was converted to AMSL with that moving home and peaked
2.1 m above the ground in SITL. ArduPilot's home holds still and is kept.

Run: cd eco && python3 -m pytest drone/common/tests/test_px4_altitude_anchor.py -v
"""

import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("pymavlink")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import drone_sdk as sdk  # noqa: E402
import px4_compat  # noqa: E402
from test_arm_test_phase import _fake_takeoff_env  # noqa: E402


def _gpi(alt_amsl, rel):
    return types.SimpleNamespace(lat=int(37e7), lon=int(-122e7), alt=int(alt_amsl * 1000),
                                 relative_alt=int(rel * 1000))


@pytest.fixture
def px4(monkeypatch):
    monkeypatch.setattr(sdk, '_autopilot', px4_compat.AUTOPILOT_PX4)
    return monkeypatch


def test_px4_height_is_from_the_takeoff_ground_not_its_moving_home(px4):
    px4.setattr(sdk, '_flight_ground_amsl', 584.13)
    # 1.0 m up, while PX4's home has jumped 1.1 m up: relative_alt reads -0.1
    assert sdk._rel_alt_m(_gpi(585.13, -0.10)) == pytest.approx(1.0)


def test_before_any_takeoff_px4_home_is_used(px4):
    assert sdk._rel_alt_m(_gpi(585.13, 0.4)) == pytest.approx(0.4)


def test_ardupilot_keeps_its_own_home(monkeypatch):
    monkeypatch.setattr(sdk, '_autopilot', px4_compat.AUTOPILOT_ARDUPILOT)
    monkeypatch.setattr(sdk, '_flight_ground_amsl', 584.13)
    assert sdk._rel_alt_m(_gpi(585.13, 1.7)) == pytest.approx(1.7)


def test_a_px4_goto_altitude_is_above_the_takeoff_ground(px4):
    """return_home at 1 m with PX4's home 1.1 m above the takeoff ground."""
    px4.setattr(sdk, '_flight_ground_amsl', 584.13)
    px4.setattr(sdk, '_fc_cached', lambda t, max_age=1.5: _gpi(585.13, -0.10))
    sent = []
    px4.setattr(sdk._px4, 'send_reposition', lambda conn, lat, lon, amsl: sent.append(amsl))
    px4.setattr(sdk, '_mav_send', lambda fn: fn(None))
    px4.setattr(sdk, '_connect_locked', lambda: None)
    sdk._send_position_target(37.0, -122.0, 1.0)
    assert sent == [pytest.approx(585.13)]          # not PX4 home (585.23) + 1 = 586.23


def test_a_px4_takeoff_records_the_ground_it_began_from(monkeypatch):
    s = _fake_takeoff_env(monkeypatch, armed_bits=0x80, autopilot='px4', params={'MIS_TAKEOFF_ALT': 0.5})
    assert s.takeoff(1.0) is True
    assert s._flight_ground_amsl == pytest.approx(25.0)    # the fixture's AMSL at takeoff


def test_an_ardupilot_takeoff_records_nothing(monkeypatch):
    s = _fake_takeoff_env(monkeypatch, armed_bits=0x80)
    assert s.takeoff(1.0) is True
    assert s._flight_ground_amsl is None
