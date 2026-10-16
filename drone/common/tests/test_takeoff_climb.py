"""drone_sdk.takeoff judges the climb from where it began, not from home.

PX4 climbs `altitude_m` above where the aircraft IS, but the altitude relative
to home on the ground is not zero (+1.1 m disarmed, -1.9 m mid-run on a
GPS-height EKF), so comparing it to the target rejected a real climb and
accepted a drifting estimate.

Run: cd eco && python3 -m pytest drone/common/tests/test_takeoff_climb.py -v
"""

import sys
from pathlib import Path

import pytest

pytest.importorskip("pymavlink")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drone_sdk as sdk  # noqa: E402


def test_a_real_climb_counts_whatever_the_home_offset():
    # on the ground the estimate says -1.9 m from home; a 3 m climb ends at +1.1
    assert sdk._climb_reached(1.1, -1.9, 3.0)
    # same climb with a +1.2 m offset
    assert sdk._climb_reached(4.2, 1.2, 3.0)


def test_drift_on_the_ground_is_not_a_climb():
    # estimate wandered +0.9 m with the props barely turning, asked for 3 m
    assert not sdk._climb_reached(0.9, 0.0, 3.0)
    # home-relative altitude already above the target on the ground: no climb
    assert not sdk._climb_reached(3.0, 3.0, 3.0)


def test_close_enough_threshold_matches_the_old_target():
    # 1 m: needs >= 0.85 m; 10 m: needs >= 9.5 m (the 0.5 m slack caps it)
    assert sdk._climb_reached(0.85, 0.0, 1.0) and not sdk._climb_reached(0.84, 0.0, 1.0)
    assert sdk._climb_reached(9.5, 0.0, 10.0) and not sdk._climb_reached(9.4, 0.0, 10.0)
