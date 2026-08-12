"""Unit tests for planning.py — the geometry + timing helpers behind the
multi-option, no-fly-zone-aware mission planner. No AWS, no drone, no model.

Run: python3 -m pytest control/test_planning.py -v
"""
import math

from control import planning


def _sq(lat0, lon0, size=0.01):
    """A square ring, size degrees on a side, SW corner at (lat0, lon0)."""
    return [
        {"lat": lat0, "lon": lon0},
        {"lat": lat0, "lon": lon0 + size},
        {"lat": lat0 + size, "lon": lon0 + size},
        {"lat": lat0 + size, "lon": lon0},
    ]


# --- distance ---------------------------------------------------------------

def test_haversine_known_distance():
    # ~1 deg of latitude is ~111 km.
    d = planning.haversine_m(37.0, -122.0, 38.0, -122.0)
    assert 110_000 < d < 112_000


def test_offset_to_coord_roundtrips_back():
    home = {"lat": 37.5, "lon": -122.0}
    c = planning.offset_to_coord(home, north_m=1000.0, east_m=500.0)
    # north offset increases latitude; east offset increases longitude
    assert c["lat"] > home["lat"]
    assert c["lon"] > home["lon"]
    back = planning.haversine_m(home["lat"], home["lon"], c["lat"], c["lon"])
    assert abs(back - math.hypot(1000.0, 500.0)) < 25.0  # within 25 m


# --- point in polygon -------------------------------------------------------

def test_point_inside_and_outside():
    zone = _sq(37.0, -122.0)
    assert planning.point_in_polygon({"lat": 37.005, "lon": -121.995}, zone)
    assert not planning.point_in_polygon({"lat": 37.05, "lon": -121.5}, zone)


def test_degenerate_polygon_is_never_inside():
    assert not planning.point_in_polygon({"lat": 0, "lon": 0}, [])
    assert not planning.point_in_polygon({"lat": 0, "lon": 0},
                                         [{"lat": 0, "lon": 0}, {"lat": 1, "lon": 1}])


# --- segment vs polygon -----------------------------------------------------

def test_segment_crossing_zone_detected():
    zone = _sq(37.0, -122.0)
    a = {"lat": 37.005, "lon": -122.02}   # west of zone
    b = {"lat": 37.005, "lon": -121.98}   # east of zone -> passes through
    assert planning.segment_enters_polygon(a, b, zone)


def test_segment_clear_of_zone():
    zone = _sq(37.0, -122.0)
    a = {"lat": 37.05, "lon": -122.02}    # well north, doesn't touch
    b = {"lat": 37.05, "lon": -121.98}
    assert not planning.segment_enters_polygon(a, b, zone)


def test_segment_with_endpoint_inside_detected():
    zone = _sq(37.0, -122.0)
    a = {"lat": 37.005, "lon": -121.995}  # inside
    b = {"lat": 37.05, "lon": -121.5}     # outside
    assert planning.segment_enters_polygon(a, b, zone)


# --- plan-level validation --------------------------------------------------

def _plan(*coords):
    phases = [{"type": "arm_and_takeoff", "altitude_m": 30}]
    for c in coords:
        phases.append({"type": "go_to_gps", "lat": c[0], "lon": c[1], "alt_m": 40})
    phases += [{"type": "return_home"}, {"type": "land"}]
    return phases


def test_plan_clear_of_nfz_has_no_violations():
    zone = _sq(37.0, -122.0)
    phases = _plan((37.05, -121.9), (37.06, -121.8))  # north of the zone
    assert planning.validate_plan_against_nfz(phases, [zone]) == []


def test_plan_crossing_nfz_flagged():
    zone = _sq(37.0, -122.0)
    # a leg from west of the zone to east of the zone crosses it
    phases = _plan((37.005, -122.02), (37.005, -121.98))
    v = planning.validate_plan_against_nfz(phases, [zone])
    assert len(v) >= 1
    assert v[0]["zone_index"] == 0


def test_vlm_phases_are_not_falsely_validated():
    zone = _sq(37.0, -122.0)
    phases = [{"type": "arm_and_takeoff", "altitude_m": 30},
              {"objective": "search the area for the pickup truck",
               "success": "pickup truck located"},
              {"type": "return_home"}, {"type": "land"}]
    # No resolvable geo legs -> nothing to validate -> no violations.
    assert planning.validate_plan_against_nfz(phases, [zone]) == []


# --- timing -----------------------------------------------------------------

def test_estimate_time_scales_with_distance():
    near = _plan((37.001, -122.0))
    far = _plan((37.05, -122.0))
    t_near = planning.estimate_plan_seconds(near, cruise_mps=25.0,
                                            home={"lat": 37.0, "lon": -122.0})
    t_far = planning.estimate_plan_seconds(far, cruise_mps=25.0,
                                           home={"lat": 37.0, "lon": -122.0})
    assert t_far > t_near > 0


def test_nav_leg_contributes_time():
    phases = [{"type": "arm_and_takeoff", "altitude_m": 30},
              {"type": "nav", "north_m": 2500.0, "east_m": 0.0, "alt_m": 40},
              {"type": "return_home"}, {"type": "land"}]
    secs = planning.estimate_plan_seconds(phases, cruise_mps=25.0)
    # 2500 m at 25 m/s = 100 s of cruise, plus takeoff/land allowance.
    assert secs >= 100.0
