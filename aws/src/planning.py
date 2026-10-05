"""planning.py — geometry + timing helpers for multi-option mission planning.

Pure stdlib (math only), no intra-package imports, so it is import-safe in both
the packaged repo (`control.planning`) and a flat Lambda/GCS zip (`planning`),
and unit-testable with no AWS, no drone, and no model.

Coordinates are latitude/longitude dicts: {"lat": float, "lon": float}. For the
small operating areas this planner deals with (a few km across at most) we treat
lon as planar x and lat as planar y for point-in-polygon and segment-crossing
tests, and use the haversine formula only where real ground distance matters
(leg lengths -> cruise time). That planar approximation is well within the slop
of a mission-planning ETA; it is NOT a survey-grade geodesy tool.

Two things this module exists to do:
  1. Keep a generated plan out of operator-drawn no-fly zones (the flight
     controller's own geofence is the last line; this is the planning line).
  2. Estimate cruise time so alternative plans can be compared.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

Coord = dict  # {"lat": float, "lon": float}

EARTH_RADIUS_M = 6_371_000.0


# --- distance ----------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two lat/lon points."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def offset_to_coord(home: Coord, north_m: float, east_m: float) -> Coord:
    """Convert a local ENU offset (metres north/east of `home`) to lat/lon via
    the equirectangular approximation. Used to bring a local-frame "nav" leg
    into the same lat/lon frame as the no-fly zones so it can be checked."""
    lat = home["lat"] + math.degrees(north_m / EARTH_RADIUS_M)
    lon = home["lon"] + math.degrees(
        east_m / (EARTH_RADIUS_M * math.cos(math.radians(home["lat"]))))
    return {"lat": lat, "lon": lon}


# --- polygon geometry (planar in lon=x, lat=y) -------------------------------

def point_in_polygon(pt: Coord, polygon: List[Coord]) -> bool:
    """Ray-casting point-in-polygon. `polygon` is an ordered ring of coords
    (first vertex need not be repeated at the end). Returns True for points
    strictly inside; boundary cases are not guaranteed and don't matter here."""
    if not polygon or len(polygon) < 3:
        return False
    x, y = pt["lon"], pt["lat"]
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]["lon"], polygon[i]["lat"]
        xj, yj = polygon[j]["lon"], polygon[j]["lat"]
        if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi):
            inside = not inside
        j = i
    return inside


def _ccw(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> float:
    return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(p1, p2, p3, p4) -> bool:
    """True if segment p1p2 properly straddles segment p3p4 (each as (x, y))."""
    d1 = _ccw(p3, p4, p1)
    d2 = _ccw(p3, p4, p2)
    d3 = _ccw(p1, p2, p3)
    d4 = _ccw(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def segment_enters_polygon(a: Coord, b: Coord, polygon: List[Coord]) -> bool:
    """True if the straight leg a->b touches the polygon at all: either endpoint
    inside, or the leg crosses any polygon edge. This is the test a transit leg
    must fail against every no-fly zone."""
    if point_in_polygon(a, polygon) or point_in_polygon(b, polygon):
        return True
    pa = (a["lon"], a["lat"])
    pb = (b["lon"], b["lat"])
    n = len(polygon)
    for i in range(n):
        e1 = polygon[i]
        e2 = polygon[(i + 1) % n]
        if _segments_cross(pa, pb, (e1["lon"], e1["lat"]), (e2["lon"], e2["lat"])):
            return True
    return False


# --- plan -> geographic legs -------------------------------------------------

def plan_geo_legs(phases: List[dict], home: Optional[Coord] = None) -> List[Coord]:
    """Extract the ordered lat/lon waypoints a plan actually flies to, from its
    typed phases. `go_to_gps` gives absolute lat/lon directly; `nav` is a local
    offset that can only be placed if `home` (the takeoff point) is known.
    VLM/objective phases have no fixed coordinate and are skipped — they route
    themselves in flight and are not checkable here (stated honestly, not
    silently)."""
    legs: List[Coord] = []
    cursor = dict(home) if home else None
    for ph in phases:
        ptype = ph.get("type")
        if ptype == "go_to_gps" and ph.get("lat") is not None and ph.get("lon") is not None:
            cursor = {"lat": float(ph["lat"]), "lon": float(ph["lon"])}
            legs.append(cursor)
        elif ptype == "nav" and cursor is not None:
            cursor = offset_to_coord(cursor, float(ph.get("north_m", 0.0)),
                                     float(ph.get("east_m", 0.0)))
            legs.append(cursor)
    return legs


def validate_plan_against_nfz(phases: List[dict], no_fly_zones: List[List[Coord]],
                              home: Optional[Coord] = None) -> List[dict]:
    """Return a list of violations, one per (leg, zone) pair where a transit leg
    enters a no-fly zone. Empty list => the checkable legs are clear. Legs whose
    coordinates can't be resolved (VLM phases, or `nav` with no home) are not
    checked and simply don't appear."""
    violations: List[dict] = []
    if not no_fly_zones:
        return violations
    legs = plan_geo_legs(phases, home=home)
    if len(legs) < 1:
        return violations
    # Build the sequence of straight segments between consecutive waypoints
    # (plus home->first if home is known), and test each against every zone.
    points = ([dict(home)] if home else []) + legs
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        for zi, zone in enumerate(no_fly_zones):
            if segment_enters_polygon(a, b, zone):
                violations.append({"leg_index": i, "zone_index": zi,
                                   "from": a, "to": b})
    return violations


# --- timing ------------------------------------------------------------------

def estimate_plan_seconds(phases: List[dict], cruise_mps: float,
                          home: Optional[Coord] = None,
                          takeoff_land_s: float = 45.0) -> float:
    """Rough wall-clock estimate for one aircraft's plan, in seconds.

    Sums straight-leg ground distance / cruise speed across resolvable
    waypoints, adds each `fly_circle`'s circumference, and adds a flat
    takeoff+land allowance. Deliberately simple: it's for RANKING alternatives
    against each other, not for fuel planning. `cruise_mps` is the airframe's
    cruise speed (e.g. 25 for the sim fixed-wing)."""
    cruise_mps = max(cruise_mps, 0.1)
    seconds = takeoff_land_s
    cursor = dict(home) if home else None
    for ph in phases:
        ptype = ph.get("type")
        if ptype == "go_to_gps" and ph.get("lat") is not None:
            nxt = {"lat": float(ph["lat"]), "lon": float(ph["lon"])}
            if cursor is not None:
                seconds += haversine_m(cursor["lat"], cursor["lon"],
                                       nxt["lat"], nxt["lon"]) / cruise_mps
            cursor = nxt
        elif ptype == "nav":
            north = float(ph.get("north_m", 0.0))
            east = float(ph.get("east_m", 0.0))
            seconds += math.hypot(north, east) / cruise_mps
            if cursor is not None:
                cursor = offset_to_coord(cursor, north, east)
        elif ptype == "fly_circle":
            radius = float(ph.get("radius_m", 0.0))
            laps = float(ph.get("laps", 1))
            seconds += (2 * math.pi * radius * laps) / cruise_mps
        elif ptype in (None, "") and ph.get("objective"):
            # VLM/objective phase: unknown geometry. Charge a nominal
            # on-station allowance so a search-heavy plan doesn't read as
            # instantaneous. Not derived from anything — a placeholder that
            # keeps ranking honest between plans with more vs fewer VLM phases.
            seconds += 90.0
    return seconds


def estimate_plan_minutes(phases: List[dict], cruise_mps: float,
                          home: Optional[Coord] = None) -> float:
    return round(estimate_plan_seconds(phases, cruise_mps, home=home) / 60.0, 1)
