"""Guards the SEARCH_AREA fixed-centre fix: an explicit local coordinate in a
phase objective must be parsed out so the expanding-orbit search pins to it
instead of the drone's (drifting) pose. A fixed-wing can't hover, so a
pose-centred search drifts downwind forever — observed live drifting past
east=2500 before this fix.

Run: python3 -m pytest drone/common/tests/test_search_anchor.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # drone/common

from reasoning_loop import _parse_local_coord


def test_parses_anchor_from_injected_objective():
    obj = ("Search the southern half of the target area for the pickup truck, "
           "routing around anything in the way, around local coordinates "
           "east=400, north=0 (metres, local frame), within 120 m of there")
    assert _parse_local_coord(obj) == (400.0, 0.0)


def test_parses_negative_and_spaced():
    assert _parse_local_coord("go to east = 250 , north = -70") == (250.0, -70.0)


def test_parses_metric_suffix_spelling():
    assert _parse_local_coord("nav east_m=10.5 north_m=-3") == (10.5, -3.0)


def test_none_when_absent():
    assert _parse_local_coord("search the area for the pickup truck") is None
    assert _parse_local_coord("") is None
    assert _parse_local_coord("only east=5 given") is None  # needs both
