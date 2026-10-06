"""Guardrails for the fleet planner: search area, no-fly zones, battery,
range, wind, aircraft choice, area split and multiple homes - for every kind
of vehicle (rover, quadcopter, fixed-wing, GPS-less micro-UAV, unknown).

Pure geometry and arithmetic: no AWS, no model.
Run: python3 -m pytest aws/src/test_planning_guardrails.py -v   (from eco/)
"""
import math

import pytest

import planning as P

REF = {"lat": 37.0, "lon": -122.0}


def ll(x, y):
    return P.to_ll((x, y), REF)


AREA = [ll(0, 0), ll(400, 0), ll(400, 300), ll(0, 300)]
NFZ = [ll(150, 100), ll(250, 100), ll(250, 200), ll(150, 200)]
NFZ_XY = P.poly_to_xy(NFZ, REF)


def vehicle(vid, vt, x, y, battery=90.0, **kw):
    return P.Vehicle(vid, P.Platform.for_vehicle(vt, kw.pop("reported", None),
                                                 kw.pop("limits", None)),
                     ll(x, y), battery_pct=battery, **kw)


def blocks(issues, kind=None):
    return [i for i in P.blocking(issues) if kind is None or i["kind"] == kind]


# --- geometry ---------------------------------------------------------------

def test_frame_round_trip_is_sub_metre():
    p = ll(1234.5, -876.5)
    x, y = P.to_xy(p, REF)
    assert abs(x - 1234.5) < 0.5 and abs(y + 876.5) < 0.5


def test_long_axis_of_a_rectangle_is_its_long_side_not_its_diagonal():
    d = P.longest_axis([(0, 0), (400, 0), (400, 300), (0, 300)])
    assert abs(abs(d[0]) - 1.0) < 1e-6


@pytest.mark.parametrize("poly", [
    [(0, 0), (1000, 0), (1000, 600), (0, 600)],
    [(0, 0), (1000, 0), (1000, 300), (300, 300), (300, 1000), (0, 1000)],   # concave L
])
def test_split_gives_each_sector_its_weighted_share(poly):
    secs = P.split_polygon(poly, [1, 1, 2])
    total = P.area_m2(poly)
    got = [P.area_m2(s) / total for s in secs]
    assert got == pytest.approx([0.25, 0.25, 0.5], abs=0.01)


def test_sweep_lanes_stay_in_the_sector_and_out_of_the_zone():
    sector = [(0, 0), (400, 0), (400, 300), (0, 300)]
    pts = P.sweep_lanes(sector, 20, [NFZ_XY], margin_m=10)
    assert pts
    assert all(P.inside_xy(p, sector) for p in pts)
    assert not any(P.inside_xy(p, NFZ_XY) for p in pts)


def test_route_goes_around_a_zone_and_refuses_when_there_is_no_way_round():
    path = P.route((0, 150), (400, 150), [NFZ_XY])
    legs = zip([(0, 150)] + path, path)
    assert all(not P.seg_enters(a, b, NFZ_XY) for a, b in legs)
    wall = [(1500, -500), (1600, -500), (1600, 1500), (1500, 1500)]
    box = [(0, 0), (1550, 0), (1550, 600), (0, 600)]
    assert P.route((500, 300), (2000, 300), [wall], keep_in=box) is None


# --- search area and no-fly zones -------------------------------------------

def _mission(*points, alt=20):
    ph = [{"type": "arm_and_takeoff", "altitude_m": alt}]
    ph += [{"type": "go_to_gps", **ll(*p), "alt_m": alt} for p in points]
    return ph + [{"type": "return_home", "alt_m": alt}, {"type": "land"}]


def test_a_leg_through_a_zone_is_rerouted_not_just_flagged():
    v = vehicle("q", "quadcopter", -20, 150)
    a = P.assess_mission(_mission((50, 150), (350, 150)), v.platform, v.home,
                         battery_pct=95, area=AREA, no_fly=[NFZ])
    assert not blocks(a["issues"], "nfz")
    assert len(a["phases"]) > 5 and any("detour" in x for x in a["adjustments"])
    pts = [P.to_xy(p, v.home) for p in a["phases"] if p.get("type") == "go_to_gps"]
    zone = P.poly_to_xy(NFZ, v.home)
    assert not any(P.seg_enters(a_, b_, zone) for a_, b_ in zip(pts, pts[1:]))


def test_a_waypoint_outside_the_search_area_blocks_the_plan():
    v = vehicle("q", "quadcopter", -20, 150)
    a = P.assess_mission(_mission((50, 150), (600, 150)), v.platform, v.home,
                         battery_pct=95, area=AREA)
    assert blocks(a["issues"], "outside_area")


def test_the_transit_from_a_home_outside_the_area_is_allowed():
    v = vehicle("q", "quadcopter", -50, 150)
    a = P.assess_mission(_mission((50, 150)), v.platform, v.home, battery_pct=95, area=AREA)
    assert not blocks(a["issues"], "outside_area") and not blocks(a["issues"], "leaves_area")


# --- what each kind of vehicle can physically do -----------------------------

def test_a_fixed_wing_cannot_fly_survey_rect_or_a_circle_tighter_than_it_turns():
    v = vehicle("p", "fixedwing", -50, 150)
    ph = [{"type": "arm_and_takeoff", "altitude_m": 60},
          {"type": "survey_rect", "forward_m": 50, "right_m": 50},
          {"type": "fly_circle", "radius_m": 20}, {"type": "land"}]
    kinds = [i["detail"] for i in blocks(P.assess_mission(ph, v.platform, v.home,
                                                          battery_pct=95)["issues"])]
    assert any("survey_rect" in k for k in kinds)
    assert any("turn radius" in k for k in kinds)


def test_altitude_over_the_ceiling_blocks_but_a_rover_has_no_altitude_to_check():
    q = vehicle("q", "quadcopter", -50, 150)
    a = P.assess_mission(_mission((50, 150), alt=50), q.platform, q.home, battery_pct=95)
    assert blocks(a["issues"], "ceiling")
    r = vehicle("r", "rover", -50, 150)
    a = P.assess_mission(_mission((50, 150), alt=50), r.platform, r.home, battery_pct=95,
                         wind=P.Wind(270, 14))
    assert not blocks(a["issues"], "ceiling") and not blocks(a["issues"], "wind")


def test_a_vehicle_without_gps_or_without_a_vision_model_is_held_to_what_it_has():
    cf = vehicle("cf", "crazyflie", 0, 0)
    assert blocks(P.eligibility(cf), "capability")
    blind = vehicle("b", "quadcopter", -50, 150, limits={"vlm": False})
    ph = [{"type": "arm_and_takeoff", "altitude_m": 20}, {"objective": "find the truck"},
          {"type": "land"}]
    assert blocks(P.assess_mission(ph, blind.platform, blind.home, battery_pct=95)["issues"],
                  "capability")


def test_an_unknown_vehicle_type_is_planned_conservatively_and_says_so():
    v = vehicle("x", "blimp", -50, 150)
    a = P.assess_mission(_mission((50, 150)), v.platform, v.home, battery_pct=95)
    assert any(i["kind"] == "vehicle" for i in P.warnings(a["issues"]))


# --- battery, range, wind ----------------------------------------------------

def test_an_open_search_phase_gets_the_time_limit_its_battery_can_pay_for():
    v = vehicle("q", "quadcopter", -20, 150)
    ph = [{"type": "arm_and_takeoff", "altitude_m": 20},
          {"type": "go_to_gps", **ll(100, 150), "alt_m": 20},
          {"objective": "Search for the truck"}, {"type": "return_home"}, {"type": "land"}]
    a = P.assess_mission(ph, v.platform, v.home, battery_pct=80, area=AREA)
    lim = a["phases"][2]["time_limit_s"]
    assert lim > 30
    assert a["costing"]["battery_end_pct"] >= v.platform.landing_reserve_pct - 0.5


def test_a_mission_the_battery_cannot_finish_is_blocked():
    v = vehicle("q", "quadcopter", -20, 150)
    a = P.assess_mission(_mission(*[(x, 150) for x in range(20, 400, 40)] * 3),
                         v.platform, v.home, battery_pct=40, area=AREA)
    assert blocks(a["issues"], "energy")


def test_assumed_energy_numbers_warn_and_reported_ones_do_not():
    a = vehicle("a", "quadcopter", -20, 150)
    b = vehicle("b", "quadcopter", -20, 150, reported={"hover_pct_per_min": 6.0})
    assert b.platform.source == "drone" and b.platform.hover_pct_per_min == 6.0
    wa = P.assess_mission(_mission((50, 150)), a.platform, a.home, battery_pct=95)["issues"]
    wb = P.assess_mission(_mission((50, 150)), b.platform, b.home, battery_pct=95)["issues"]
    assert any("ASSUMED" in i["detail"] for i in wa)
    assert not any("ASSUMED" in i["detail"] for i in wb)


def test_range_is_measured_from_each_vehicles_own_home():
    v = vehicle("q", "quadcopter", -20, 150, limits={"max_range_m": 200})
    a = P.assess_mission(_mission((350, 150)), v.platform, v.home, battery_pct=95, area=AREA)
    assert blocks(a["issues"], "range")
    assert blocks(P.eligibility(vehicle("far", "quadcopter", -900, 150), area=AREA), "range")


def test_wind_triangle():
    # 10 m/s airspeed, 4 m/s wind from the west, flying east: 14 m/s tailwind.
    assert P.ground_speed(10, (0, 0), (100, 0), P.Wind(270, 4)) == pytest.approx(14)
    assert P.ground_speed(10, (0, 0), (-100, 0), P.Wind(270, 4)) == pytest.approx(6)
    assert P.ground_speed(10, (0, 0), (0, 100), P.Wind(270, 6)) == pytest.approx(8)
    assert P.ground_speed(3, (0, 0), (-100, 0), P.Wind(270, 4)) is None


def test_wind_over_a_vehicles_limit_or_speed_rules_it_out():
    assert blocks(P.eligibility(vehicle("p", "fixedwing", 0, 0), P.Wind(0, 12)), "wind")
    assert blocks(P.eligibility(vehicle("q", "quadcopter", 0, 0), P.Wind(0, 4)), "wind")
    assert not blocks(P.eligibility(vehicle("r", "rover", 0, 0), P.Wind(0, 12)), "wind")


def test_no_wind_supplied_is_a_warning_for_aircraft_only():
    q = vehicle("q", "quadcopter", -20, 150)
    r = vehicle("r", "rover", -20, 150)
    wq = P.assess_mission(_mission((50, 150)), q.platform, q.home, battery_pct=95)["issues"]
    wr = P.assess_mission(_mission((50, 150)), r.platform, r.home, battery_pct=95)["issues"]
    assert any(i["kind"] == "wind" for i in P.warnings(wq))
    assert not any(i["kind"] == "wind" for i in wr)


def test_eligibility_reports_offline_pilot_preflight_and_low_battery():
    v = vehicle("q", "quadcopter", 0, 0, battery=20.0, online=False, pilot_control="RC",
                can_takeoff=False, takeoff_reason="GPS not ready")
    kinds = {i["kind"] for i in P.blocking(P.eligibility(v))}
    assert {"offline", "pilot", "preflight", "energy"} <= kinds


# --- choosing aircraft and splitting the area ---------------------------------

def test_choose_fleet_skips_vehicles_that_barely_help():
    fleet = [vehicle("plane", "fixedwing", -300, 150), vehicle("q1", "quadcopter", -30, 150),
             vehicle("rover", "rover", 200, -20)]
    chosen, why = P.choose_fleet(fleet, AREA)
    assert [v.drone_id for v in chosen] == ["plane"]
    assert set(why) == {"q1", "rover"}


def test_choose_fleet_uses_more_of_a_like_fleet_and_respects_a_cap():
    fleet = [vehicle(f"q{i}", "quadcopter", x, -20) for i, x in enumerate((50, 200, 350))]
    chosen, _ = P.choose_fleet(fleet, AREA)
    assert len(chosen) == 3
    chosen, why = P.choose_fleet(fleet, AREA, max_vehicles=2)
    assert len(chosen) == 2 and len(why) == 1


def test_split_gives_each_vehicle_the_strip_by_its_own_home():
    west = vehicle("west", "quadcopter", -30, 150, battery=100)
    east = vehicle("east", "quadcopter", 430, 150, battery=100)
    r = P.plan_split([east, west], AREA, "search", target="truck", no_fly=[NFZ])
    by = {d["drone_id"]: P.centroid(P.poly_to_xy(d["sector"], REF))[0] for d in r["per_drone"]}
    assert by["west"] < 200 < by["east"]
    for d in r["per_drone"]:
        assert d["phases"][-1] == {"type": "land"}
        assert not P.blocking(d["assessment"]["issues"])


def test_a_mixed_fleet_search_has_the_vehicle_without_vision_sweep_instead():
    a = vehicle("a", "quadcopter", -30, 150, battery=100)
    b = vehicle("b", "rover", 430, 150, battery=100, limits={"vlm": False})
    r = P.plan_split([a, b], AREA, "search", target="truck")
    styles = {d["drone_id"]: d["style"] for d in r["per_drone"]}
    assert styles == {"a": "search", "b": "sweep"}


def test_a_sweep_too_big_for_the_batteries_is_cut_back_and_reports_coverage():
    fleet = [vehicle("q1", "quadcopter", -30, 150, 95), vehicle("q2", "quadcopter", 430, 150, 90)]
    full = P.plan_split(fleet, AREA, "sweep", target="truck", no_fly=[NFZ])
    assert any(blocks(d["assessment"]["issues"], "energy") for d in full["per_drone"])
    part = P.plan_split(fleet, AREA, "sweep", target="truck", no_fly=[NFZ], partial=True)
    assert 0 < part["coverage"] < 1
    for d in part["per_drone"]:
        assert not P.blocking(d["assessment"]["issues"])
