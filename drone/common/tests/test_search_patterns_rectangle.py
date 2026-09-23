"""Rectangle geometry for the fly_rect phase.

Two failures this guards against, both of which are silent in flight rather
than loud at the API:

1. A mirrored or rotated box. "10 meters ahead and 5 to the right" is resolved
   body-frame, and the rotation lives in two places that must agree -
   search_patterns._body_to_enu here and MissionLoop._body_to_home_offset in
   reasoning_loop. If they disagree by a sign or by swapping sin/cos, the
   aircraft flies a real, valid rectangle in the wrong place, and nothing
   errors.

2. An unflyable box on a fixed-wing. A side shorter than twice the airframe's
   turn radius cannot hold its two corner arcs. Left unclamped this surfaces
   as a string of "blocked waypoint" timeouts with the real cause invisible -
   exactly the failure _exec_fly_circle's radius clamp was added for.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from search_patterns import rectangle, turn_radius_m  # noqa: E402
from vehicle_class import get_class  # noqa: E402

QUAD = get_class("quadcopter")
ROVER = get_class("rover")
FIXEDWING = get_class("fixedwing")


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def _extents(pts):
    """(north_extent, east_extent), rounded past the rotation's float noise -
    cos(90 deg) is 6.1e-17, not 0, so exact equality on a rotated box fails on
    arithmetic rather than on geometry."""
    min_e, max_e, min_n, max_n = _bbox(pts)
    return round(max_n - min_n, 9), round(max_e - min_e, 9)


# --- hovering vehicles: a plain rectangle, no rounding -------------------- #

def test_quad_rectangle_is_four_sharp_corners():
    pts = rectangle((10.0, 5.0), (0.0, 0.0), QUAD)
    assert len(pts) == 4, "a vehicle that can stop needs no corner arcs"
    assert sorted(pts) == sorted([(0.0, 0.0), (0.0, 10.0), (5.0, 10.0), (5.0, 0.0)])


def test_forward_is_north_and_right_is_east_at_heading_zero():
    # Points are (east, north). forward_m=10 must produce 10m of north extent
    # and right_m=5 must produce 5m of east extent - not the other way round.
    assert _extents(rectangle((10.0, 5.0), (0.0, 0.0), QUAD)) == (10.0, 5.0)


def test_heading_90_swaps_north_and_east():
    """Facing east, "ahead" is east. The extents swap; the shape does not."""
    assert _extents(rectangle((10.0, 5.0), (0.0, 0.0), QUAD, heading_deg=90)) == (5.0, 10.0)


def test_heading_is_clockwise_not_counterclockwise():
    """Compass convention, not the maths one: at heading 90 the forward axis
    points east (+x), not west. A sign flip here mirrors every box."""
    pts = rectangle((10.0, 1e-3), (0.0, 0.0), QUAD, heading_deg=90)
    assert max(p[0] for p in pts) > 9.0, "forward must go +east at heading 90"


def test_right_is_clockwise_from_forward():
    """At heading 0, +right is +east. If right went west instead, every
    rectangle would be mirrored across the flight path."""
    pts = rectangle((1e-3, 5.0), (0.0, 0.0), QUAD)
    assert max(p[0] for p in pts) > 4.0
    assert min(p[0] for p in pts) >= -1e-6


def test_origin_offsets_translate_without_resizing():
    pts = rectangle((10.0, 5.0), (20.0, -4.0), QUAD)
    min_e, max_e, min_n, max_n = _bbox(pts)
    assert (min_n, max_n) == (20.0, 30.0)
    assert (min_e, max_e) == (-4.0, 1.0)


def test_waypoints_per_side_adds_intermediate_points():
    pts = rectangle((10.0, 5.0), (0.0, 0.0), QUAD, waypoints_per_side=3)
    assert len(pts) == 12  # 4 corners x 3 points per side
    # The shape is unchanged - the extra points lie on the perimeter.
    assert _extents(pts) == (10.0, 5.0)


def test_rover_matches_quad():
    """Both can stop, so both get the same plain rectangle. The rover is
    planar but rectangle() returns no altitude, so there is nothing to differ."""
    assert rectangle((8.0, 3.0), (0.0, 0.0), ROVER) == rectangle((8.0, 3.0), (0.0, 0.0), QUAD)


# --- fixed-wing: arcs, clamping, and flyability --------------------------- #

def test_fixedwing_rounds_corners():
    pts = rectangle((300.0, 200.0), (0.0, 0.0), FIXEDWING)
    assert len(pts) > 4, "a vehicle that cannot stop needs corner arcs"


def test_fixedwing_small_rectangle_is_clamped_up_not_flown_as_asked():
    """A 10x5m box is unflyable for a ~42m turn radius. Clamping up to a
    circle-ish 2r box is the honest answer; flying it as asked is a string of
    timeouts."""
    r = turn_radius_m(FIXEDWING)
    min_e, max_e, min_n, max_n = _bbox(rectangle((10.0, 5.0), (0.0, 0.0), FIXEDWING))
    assert (max_n - min_n) >= 2 * r - 1e-6
    assert (max_e - min_e) >= 2 * r - 1e-6


def test_fixedwing_large_rectangle_keeps_its_requested_size():
    """Clamping must only ever enlarge an unflyable box, never resize a
    perfectly good one."""
    min_e, max_e, min_n, max_n = _bbox(rectangle((300.0, 200.0), (0.0, 0.0), FIXEDWING))
    assert abs((max_n - min_n) - 300.0) < 1e-6
    assert abs((max_e - min_e) - 200.0) < 1e-6


def test_no_segment_is_tighter_than_the_turn_radius_can_carve():
    """Consecutive waypoints closer together than the airframe can turn
    between are the whole reason corner arcs exist."""
    pts = rectangle((300.0, 200.0), (0.0, 0.0), FIXEDWING)
    r = turn_radius_m(FIXEDWING)
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        assert d > 0.1 * r, f"waypoints {i} and {i+1} are {d:.1f}m apart (r={r:.1f}m)"


def test_no_duplicate_consecutive_waypoints():
    """When a side is exactly 2r the corner arcs meet and the exit of one
    coincides with the entry of the next. Flying to where you already are is a
    goto() that may never report arrival."""
    r = turn_radius_m(FIXEDWING)
    for size in ((10.0, 5.0), (2 * r, 2 * r), (300.0, 200.0)):
        pts = rectangle(size, (0.0, 0.0), FIXEDWING)
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            assert math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-6, f"duplicate at {i} for {size}"


# --- degenerate input ------------------------------------------------------ #

def test_zero_and_negative_sides_do_not_produce_nans():
    for size in ((0.0, 0.0), (-10.0, 5.0), (10.0, -5.0)):
        pts = rectangle(size, (0.0, 0.0), QUAD)
        assert pts, f"no points for {size}"
        for x, y in pts:
            assert math.isfinite(x) and math.isfinite(y), f"non-finite point for {size}"


def test_negative_sides_are_treated_as_magnitudes():
    """A planner emitting right_m=-5 means "5 metres, to the left" at worst and
    a sign slip at best; either way it must not invert the traversal."""
    assert _bbox(rectangle((10.0, -5.0), (0.0, 0.0), QUAD)) == _bbox(
        rectangle((10.0, 5.0), (0.0, 0.0), QUAD)
    )


# --- the cross-module agreement that actually matters --------------------- #

def test_rotation_matches_the_mission_loop_helper():
    """search_patterns._body_to_enu and MissionLoop._body_to_home_offset are
    two implementations of the same rotation, in two modules that cannot
    import each other cleanly. If they drift, the rectangle phase flies a
    correctly-shaped box in the wrong orientation and nothing reports an error.
    """
    from search_patterns import _body_to_enu

    for heading_deg in (0.0, 37.0, 90.0, 180.0, 275.0, 359.0):
        yaw = math.radians(heading_deg)
        for forward, right in ((10.0, 5.0), (0.0, 7.0), (-3.0, 2.0)):
            east, north = _body_to_enu(forward, right, heading_deg)
            # The reasoning_loop formula, inlined so this test does not need
            # to import the whole mission loop (ROS, VLM, MAVLink...).
            exp_north = forward * math.cos(yaw) - right * math.sin(yaw)
            exp_east = forward * math.sin(yaw) + right * math.cos(yaw)
            assert abs(north - exp_north) < 1e-9
            assert abs(east - exp_east) < 1e-9
