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

What this module exists to do:
  1. Keep a plan inside the operator-drawn search area and out of the no-fly
     zones - checked here, and rerouted where a leg can be (the flight
     controller's own geofence is the last line; this is the planning line).
  2. Cost a plan against the aircraft's battery, link range and the wind, and
     bound every open-ended search phase so it leaves enough to get home.
  3. Split a search area into one sector per aircraft and sweep each sector.
  4. Estimate cruise time so alternative plans can be compared.

The later sections work in a local metric frame (x east, y north, metres)
about a reference point, via to_xy()/to_ll(), rather than raw degrees.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Sequence, Tuple

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


# =============================================================================
# Local metric frame
# =============================================================================
# Everything below works in metres: x east, y north, about a reference point
# (equirectangular). Over the few-km areas planned here the error is well under
# a metre per km, far inside a waypoint's arrival tolerance.

XY = Tuple[float, float]


def to_xy(pt: Coord, ref: Coord) -> XY:
    """lat/lon -> (east_m, north_m) relative to `ref`."""
    x = math.radians(pt["lon"] - ref["lon"]) * EARTH_RADIUS_M * math.cos(math.radians(ref["lat"]))
    y = math.radians(pt["lat"] - ref["lat"]) * EARTH_RADIUS_M
    return (x, y)


def to_ll(xy: XY, ref: Coord) -> Coord:
    """(east_m, north_m) relative to `ref` -> lat/lon."""
    return offset_to_coord(ref, north_m=xy[1], east_m=xy[0])


def poly_to_xy(poly: Sequence[Coord], ref: Coord) -> List[XY]:
    return [to_xy(p, ref) for p in poly]


def _dist(a: XY, b: XY) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def signed_area(poly: Sequence[XY]) -> float:
    """Shoelace; positive when the ring is counter-clockwise."""
    n = len(poly)
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1]
                     for i in range(n))


def area_m2(poly: Sequence[XY]) -> float:
    return abs(signed_area(poly)) if len(poly) >= 3 else 0.0


def centroid(poly: Sequence[XY]) -> XY:
    a = signed_area(poly)
    if abs(a) < 1e-9:
        return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        k = x0 * y1 - x1 * y0
        cx += (x0 + x1) * k
        cy += (y0 + y1) * k
    return (cx / (6 * a), cy / (6 * a))


def inside_xy(p: XY, poly: Sequence[XY]) -> bool:
    """Ray-casting point-in-polygon in the metric frame."""
    if len(poly) < 3:
        return False
    x, y = p
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi:
            inside = not inside
        j = i
    return inside


def _crosses_boundary(a: XY, b: XY, poly: Sequence[XY]) -> bool:
    n = len(poly)
    return any(_segments_cross(a, b, poly[i], poly[(i + 1) % n]) for i in range(n))


def seg_enters(a: XY, b: XY, poly: Sequence[XY]) -> bool:
    """True if the straight leg a->b touches the polygon's interior at all."""
    if inside_xy(a, poly) or inside_xy(b, poly):
        return True
    if _crosses_boundary(a, b, poly):
        return True
    # A leg along a diagonal of the zone touches no edge properly; its midpoint does.
    return inside_xy(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), poly)


def seg_within(a: XY, b: XY, poly: Sequence[XY]) -> bool:
    """True if the straight leg a->b stays inside the polygon end to end (a
    concave area can hold both ends and still have the leg cut a corner out)."""
    if not (inside_xy(a, poly) and inside_xy(b, poly)):
        return False
    if _crosses_boundary(a, b, poly):
        return False
    return inside_xy(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), poly)


def max_dist_from(p: XY, poly: Sequence[XY]) -> float:
    return max((_dist(p, v) for v in poly), default=0.0)


# --- splitting an area into sectors -----------------------------------------

def _dot(p: XY, u: XY) -> float:
    return p[0] * u[0] + p[1] * u[1]


def clip_halfplane(poly: Sequence[XY], u: XY, c: float, keep_below: bool = True) -> List[XY]:
    """Sutherland-Hodgman against one half-plane: keep the part of `poly` with
    dot(p, u) <= c (or >= c). Correct for concave subjects too, since the clip
    region (a half-plane) is convex; a concave subject can come back as one ring
    with a zero-width bridge, which has the right area and harmless lanes."""
    sgn = 1.0 if keep_below else -1.0

    def f(p):
        return sgn * (_dot(p, u) - c)

    out: List[XY] = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        fp, fq = f(p), f(q)
        if fp <= 0:
            out.append(p)
        if (fp <= 0) != (fq <= 0):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def longest_axis(poly: Sequence[XY]) -> XY:
    """Unit vector along the polygon's length: perpendicular to the direction
    in which it is narrowest (1 degree search). Not the direction of greatest
    extent - for a rectangle that is the diagonal."""
    best, best_d = float("inf"), (1.0, 0.0)
    for k in range(180):
        th = math.radians(k)
        n = (-math.sin(th), math.cos(th))
        proj = [_dot(p, n) for p in poly]
        width = max(proj) - min(proj)
        if width < best - 1e-6:
            best, best_d = width, (math.cos(th), math.sin(th))
    return best_d


def split_polygon(poly: Sequence[XY], weights: Sequence[float],
                  overlap_m: float = 0.0) -> List[List[XY]]:
    """Cut `poly` into len(weights) strips across its longest axis, strip i
    holding weights[i]/sum(weights) of the area. Weights let a faster or
    longer-lived aircraft take a bigger share. With `overlap_m`, each strip
    reaches that far past every cut it shares with a neighbour, so an object
    on a cut is inside both strips rather than on the edge of each."""
    n = len(weights)
    if n <= 1:
        return [list(poly)]
    u = longest_axis(poly)
    proj = [_dot(p, u) for p in poly]
    lo, hi = min(proj), max(proj)
    total = area_m2(poly)
    wsum = float(sum(weights)) or 1.0
    cuts = [lo]
    acc = 0.0
    for w in weights[:-1]:
        acc += w / wsum
        target = total * acc
        a, b = cuts[-1], hi
        for _ in range(60):
            mid = (a + b) / 2
            if area_m2(clip_halfplane(poly, u, mid, True)) < target:
                a = mid
            else:
                b = mid
        cuts.append((a + b) / 2)
    cuts.append(hi)
    sectors = []
    for i in range(n):
        top = cuts[i + 1] + (overlap_m if i < n - 1 else 0.0)
        bottom = cuts[i] - (overlap_m if i > 0 else 0.0)
        s = clip_halfplane(poly, u, top, True)
        s = clip_halfplane(s, u, bottom, False)
        sectors.append(s)
    return sectors


# --- sweeping a sector ------------------------------------------------------

def _line_intervals(poly: Sequence[XY], o: XY, d: XY, n: XY) -> List[Tuple[float, float]]:
    """Intervals of t where o + t*d is inside `poly`, for the line through `o`
    along unit `d` (`n` its unit normal). Standard scanline crossing count."""
    s0 = _dot(o, n)
    ts = []
    m = len(poly)
    for i in range(m):
        p, q = poly[i], poly[(i + 1) % m]
        sp, sq = _dot(p, n) - s0, _dot(q, n) - s0
        if (sp > 0) != (sq > 0):
            r = sp / (sp - sq)
            x = (p[0] + r * (q[0] - p[0]), p[1] + r * (q[1] - p[1]))
            ts.append(_dot((x[0] - o[0], x[1] - o[1]), d))
    ts.sort()
    return [(ts[i], ts[i + 1]) for i in range(0, len(ts) - 1, 2)]


def _subtract(intervals, holes, pad):
    out = list(intervals)
    for h0, h1 in holes:
        h0, h1 = h0 - pad, h1 + pad
        nxt = []
        for a, b in out:
            if h1 <= a or h0 >= b:
                nxt.append((a, b))
                continue
            if a < h0:
                nxt.append((a, h0))
            if h1 < b:
                nxt.append((h1, b))
        out = nxt
    return out


def sweep_lanes(sector: Sequence[XY], spacing_m: float,
                no_fly: Sequence[Sequence[XY]] = (), margin_m: float = 10.0,
                min_len_m: float = 1.0, edge_inset_m: float = 2.0) -> List[XY]:
    """Serpentine lawnmower over `sector`: lanes `spacing_m` apart along the
    sector's longest axis, each lane clipped to the sector and cut back
    `margin_m` short of every no-fly zone. Returns the ordered lane endpoints;
    joining consecutive ones safely is route()'s job (the gap a zone leaves in
    a lane must be flown around, not straight through). Lane ends sit
    `edge_inset_m` inside the boundary."""
    if area_m2(sector) < 1.0 or spacing_m <= 0:
        return []
    d = longest_axis(sector)
    n = (-d[1], d[0])
    proj = [_dot(p, n) for p in sector]
    lo, hi = min(proj), max(proj)
    lanes = []
    k = 0
    off = lo + spacing_m / 2
    if off > hi:
        off = (lo + hi) / 2
    while off <= hi + 1e-9:
        # Nudge off exact vertices so the crossing count stays even.
        o = (n[0] * (off + 1e-6), n[1] * (off + 1e-6))
        ivs = _line_intervals(sector, o, d, n)
        holes = []
        for z in no_fly:
            holes.extend(_line_intervals(z, o, d, n))
        # A zone that grazes the lane without crossing it still needs the margin.
        # Pull lane ends off the boundary, so they test as inside it.
        ivs = [(a + edge_inset_m, b - edge_inset_m) for a, b in ivs]
        ivs = _subtract(ivs, holes, margin_m)
        ivs = [(a, b) for a, b in ivs if b - a >= min_len_m]
        if k % 2:
            ivs = [(b, a) for a, b in reversed(ivs)]
        for a, b in ivs:
            lanes.append((o[0] + a * d[0], o[1] + a * d[1]))
            lanes.append((o[0] + b * d[0], o[1] + b * d[1]))
        k += 1
        off += spacing_m
    return lanes


# --- routing around no-fly zones ---------------------------------------------

def _offset_vertices(poly: Sequence[XY], margin_m: float, outward: bool = True) -> List[XY]:
    """Each vertex pushed `margin_m` along its bisector, out of (or into) the
    polygon. Reflex vertices go the wrong way; callers drop any node that lands
    inside an obstacle, so that costs a node, never a bad route."""
    n = len(poly)
    if n < 3:
        return []
    ccw = signed_area(poly) > 0
    out = []
    for i in range(n):
        p0, p1, p2 = poly[i - 1], poly[i], poly[(i + 1) % n]
        normals = []
        for a, b in ((p0, p1), (p1, p2)):
            ex, ey = b[0] - a[0], b[1] - a[1]
            ln = math.hypot(ex, ey) or 1.0
            # Right-hand normal is outward for a counter-clockwise ring.
            nx, ny = (ey / ln, -ex / ln) if ccw else (-ey / ln, ex / ln)
            normals.append((nx, ny))
        bx, by = normals[0][0] + normals[1][0], normals[0][1] + normals[1][1]
        bl = math.hypot(bx, by) or 1.0
        sgn = 1.0 if outward else -1.0
        # Divide by cos(half-angle) so the offset clears both edges by margin_m.
        cos_half = max(0.35, bl / 2)
        k = sgn * margin_m / cos_half
        out.append((p1[0] + k * bx / bl, p1[1] + k * by / bl))
    return out


def leg_ok(a: XY, b: XY, no_fly: Sequence[Sequence[XY]],
           keep_in: Optional[Sequence[XY]] = None) -> bool:
    if any(seg_enters(a, b, z) for z in no_fly):
        return False
    return keep_in is None or seg_within(a, b, keep_in)


def route(a: XY, b: XY, no_fly: Sequence[Sequence[XY]],
          keep_in: Optional[Sequence[XY]] = None, margin_m: float = 10.0) -> Optional[List[XY]]:
    """Shortest path a -> b that enters no zone (and stays inside `keep_in`
    when given), over a visibility graph of zone vertices pushed `margin_m`
    clear. Returns the points to fly after `a` (ending with `b`), or None when
    there is no such path - which callers must treat as "cannot fly this",
    never as "fly it straight"."""
    if leg_ok(a, b, no_fly, keep_in):
        return [b]
    nodes: List[XY] = []
    for z in no_fly:
        nodes.extend(_offset_vertices(z, margin_m, outward=True))
    if keep_in is not None:
        nodes.extend(_offset_vertices(keep_in, margin_m, outward=False))
    nodes = [p for p in nodes
             if not any(inside_xy(p, z) for z in no_fly)
             and (keep_in is None or inside_xy(p, keep_in))]
    pts = [a] + nodes + [b]
    goal = len(pts) - 1
    best = {0: 0.0}
    prev: Dict[int, int] = {}
    heap = [(0.0, 0)]
    done = set()
    while heap:
        d, i = heapq.heappop(heap)
        if i in done:
            continue
        done.add(i)
        if i == goal:
            break
        for j in range(1, len(pts)):
            if j in done or j == i:
                continue
            nd = d + _dist(pts[i], pts[j])
            if nd < best.get(j, float("inf")) and leg_ok(pts[i], pts[j], no_fly, keep_in):
                best[j] = nd
                prev[j] = i
                heapq.heappush(heap, (nd, j))
    if goal not in prev:
        return None
    path = [goal]
    while path[-1] != 0:
        path.append(prev[path[-1]])
    return [pts[k] for k in reversed(path[:-1])]


# =============================================================================
# Platforms: what a vehicle can do, what it costs to fly, how far it may go
# =============================================================================
# Every check below keys off these capabilities - never off the vehicle-type
# string - so a rover, a quadcopter, a fixed-wing or a GPS-less micro-UAV each
# get the rules that follow from what they physically are, and a new type is
# one profile here.
#
# The energy numbers are the cost model the vehicle itself flies by
# (drone/common/battery.py's BatteryGovernor): percent of pack per minute of
# hover (a fixed-wing: of cruise; a rover: of driving), scaled per manoeuvre. A
# vehicle reports its own numbers in the heartbeat (`energyModel`); the
# per-type defaults below are used only when it has not, and every result built
# on them says so - they are ASSUMPTIONS, not measurements of any airframe.

@dataclass
class Platform:
    vehicle_type: str
    # capabilities
    ground: bool = False              # drives: no altitude, no wind effect, no takeoff/landing
    can_hover: bool = True            # False: must keep moving (no hold-in-place, no photo stops)
    gps: bool = True                  # False: cannot fly lat/lon plans at all
    ceiling_m: float = 120.0          # highest altitude it may be planned to, m AGL (0 = ground)
    min_turn_radius_m: float = 0.0    # 0 = turns in place
    vlm: bool = True                  # False: cannot fly open-ended objective phases
    default_alt_m: float = 20.0       # search altitude when the tasking names none, m AGL
    default_spacing_m: float = 20.0   # sweep lane spacing when the tasking names none, m
    # energy (% of pack)
    hover_pct_per_min: float = 7.0
    cruise_mult: float = 1.1
    climb_mult: float = 1.5
    descend_mult: float = 0.7
    cruise_speed_mps: float = 3.0
    climb_rate_mps: float = 1.0
    descent_rate_mps: float = 0.7
    takeoff_overhead_pct: float = 1.0
    landing_overhead_pct: float = 1.0
    # limits
    landing_reserve_pct: float = 20.0  # must still be in the pack at the end
    min_takeoff_pct: float = 35.0
    max_wind_mps: float = 8.0          # sustained wind it may be dispatched into, m/s
    max_range_m: float = 500.0         # furthest it may go from its own home, m
    source: str = "assumed"            # "drone" when the energy model came from the vehicle

    _ENERGY_KEYS = ("hover_pct_per_min", "cruise_mult", "climb_mult", "descend_mult",
                    "cruise_speed_mps", "climb_rate_mps", "descent_rate_mps",
                    "takeoff_overhead_pct", "landing_overhead_pct",
                    "landing_reserve_pct", "min_takeoff_pct")
    _LIMIT_KEYS = ("max_wind_mps", "max_range_m", "landing_reserve_pct",
                   "min_takeoff_pct", "ceiling_m")

    @classmethod
    def for_vehicle(cls, vehicle_type: Optional[str], reported: Optional[dict] = None,
                    limits: Optional[dict] = None) -> "Platform":
        """The profile for `vehicle_type`, overlaid with the energy model the
        vehicle reported (`reported`, heartbeat `energyModel`) and then the
        operator's limits (`limits`). An unknown type falls back to the
        quadcopter profile and is flagged as such by `known_type`."""
        vt = (vehicle_type or "").lower() or "quadcopter"
        base = dict(_PLATFORMS.get(vt, _PLATFORMS["quadcopter"]))
        src = "assumed"
        if reported:
            got = {}
            for k in cls._ENERGY_KEYS:
                try:
                    if reported.get(k) is not None:
                        got[k] = float(reported[k])
                except (TypeError, ValueError):
                    pass
            if "hover_pct_per_min" in got:
                src = "drone"
            base.update(got)
        if limits:
            for k in cls._LIMIT_KEYS:
                try:
                    if limits.get(k) is not None:
                        base[k] = float(limits[k])
                except (TypeError, ValueError):
                    pass
        if limits and limits.get("vlm") is not None:
            base["vlm"] = bool(limits["vlm"])
        p = cls(vehicle_type=vt, **base, source=src)
        p.known_type = vt in _PLATFORMS
        return p

    def pct_per_s(self, mult: float) -> float:
        return self.hover_pct_per_min / 60.0 * mult

    def describe(self) -> str:
        bits = ["ground vehicle" if self.ground else
                ("hovers" if self.can_hover else "cannot hover")]
        if not self.gps:
            bits.append("no GPS")
        if self.min_turn_radius_m:
            bits.append(f"min turn radius {self.min_turn_radius_m:.0f} m")
        if not self.ground:
            bits.append(f"ceiling {self.ceiling_m:.0f} m AGL")
        bits.append(f"cruise {self.cruise_speed_mps:g} m/s")
        return ", ".join(bits)


def _turn_radius(speed_mps: float, bank_deg: float = 30.0) -> float:
    """Coordinated-turn radius v^2 / (g tan(bank)), m."""
    return speed_mps ** 2 / (9.81 * math.tan(math.radians(bank_deg)))


# Per-type profiles. Capabilities and speeds mirror drone/common/vehicle_class.py
# (quadcopter 3 m/s, ceiling 30 m; rover 1.5 m/s max, ground; fixed-wing 25 m/s
# cruise, stall 12 m/s, ceiling 120 m; crazyflie 1 m/s, ceiling 2.2 m, no GPS).
# The quadcopter energy numbers match battery.py's defaults. Every other energy
# number, the wind and range limits, and the fixed-wing's 30 degree bank are
# placeholders until the vehicle reports its own model or the operator sets
# limits.
_PLATFORMS = {
    "quadcopter": dict(default_alt_m=20.0, default_spacing_m=20.0, ceiling_m=30.0, hover_pct_per_min=7.0, cruise_mult=1.1,
                       cruise_speed_mps=3.0, climb_rate_mps=1.0, descent_rate_mps=0.7,
                       max_wind_mps=8.0, max_range_m=500.0),
    "fixedwing": dict(default_alt_m=80.0, default_spacing_m=80.0, can_hover=False, ceiling_m=120.0, min_turn_radius_m=_turn_radius(25.0),
                      hover_pct_per_min=2.5, cruise_mult=1.0, climb_mult=1.5, descend_mult=0.6,
                      cruise_speed_mps=25.0, climb_rate_mps=3.0, descent_rate_mps=2.0,
                      min_takeoff_pct=50.0, max_wind_mps=10.0, max_range_m=5000.0),
    "rover": dict(default_alt_m=0.0, default_spacing_m=10.0, ground=True, ceiling_m=0.0, hover_pct_per_min=1.0, cruise_mult=1.0,
                  climb_mult=1.0, descend_mult=1.0, cruise_speed_mps=1.0,
                  takeoff_overhead_pct=0.0, landing_overhead_pct=0.0,
                  landing_reserve_pct=15.0, min_takeoff_pct=20.0,
                  max_wind_mps=15.0, max_range_m=500.0),
    "crazyflie": dict(default_alt_m=1.0, default_spacing_m=1.5, gps=False, ceiling_m=2.2, hover_pct_per_min=15.0, cruise_mult=1.1,
                      cruise_speed_mps=1.0, climb_rate_mps=0.5, descent_rate_mps=0.3,
                      max_wind_mps=1.0, max_range_m=20.0),
}


# Phase types a platform cannot fly at all, keyed by the capability that rules
# them out. survey_rect stops over every cell; hold and the photo stops need to
# stay put; a ground vehicle neither takes off nor lands (its arm/stop phases
# still use those names - see MISSION_VEHICLE_NOTES - so those stay allowed).
_NEEDS_HOVER = {"survey_rect"}
_NEEDS_GPS = {"go_to_gps"}


def phase_capability_issues(phases: List[dict], platform: Platform) -> List[dict]:
    """Phases this platform physically cannot fly, or altitudes above its
    ceiling. Each issue is a dict with kind/phase_index/detail."""
    out = []
    for i, ph in enumerate(phases or []):
        t = ph.get("type")
        if not t and ph.get("objective") and not platform.vlm:
            out.append({"kind": "capability", "phase_index": i,
                        "detail": "an open-ended objective needs an on-board vision model; "
                                  "this vehicle has none"})
        if t in _NEEDS_HOVER and not platform.can_hover:
            out.append({"kind": "capability", "phase_index": i,
                        "detail": f"{t} needs to stop in place; a {platform.vehicle_type} cannot"})
        if t in _NEEDS_GPS and not platform.gps:
            out.append({"kind": "capability", "phase_index": i,
                        "detail": f"{t} needs GPS; a {platform.vehicle_type} has none"})
        if not platform.ground:
            for key in ("altitude_m", "alt_m"):
                if ph.get(key) is not None:
                    try:
                        alt = float(ph[key])
                    except (TypeError, ValueError):
                        continue
                    if alt > platform.ceiling_m + 1e-6:
                        out.append({"kind": "ceiling", "phase_index": i,
                                    "detail": f"{alt:g} m is above the {platform.ceiling_m:g} m "
                                              f"ceiling for a {platform.vehicle_type}"})
        if t == "fly_circle" and platform.min_turn_radius_m:
            r = float(ph.get("radius_m", 10.0))
            if r < platform.min_turn_radius_m:
                out.append({"kind": "capability", "phase_index": i,
                            "detail": f"circle radius {r:g} m is tighter than the "
                                      f"{platform.min_turn_radius_m:.0f} m minimum turn radius"})
    return out


# --- wind --------------------------------------------------------------------

@dataclass
class Wind:
    from_deg: float    # where it blows FROM, degrees clockwise from north
    speed_mps: float

    @classmethod
    def parse(cls, d) -> Optional["Wind"]:
        if not d:
            return None
        try:
            return cls(float(d.get("from_deg", d.get("direction_deg", 0.0))),
                       float(d.get("speed_mps", 0.0)))
        except (TypeError, ValueError, AttributeError):
            return None


def ground_speed(speed_mps: float, a: XY, b: XY, wind: Optional[Wind]) -> Optional[float]:
    """Ground speed going a->b at `speed_mps` (airspeed) through `wind` (the
    wind triangle). None when the crosswind is more than it can crab into, or
    the headwind leaves it effectively standing still."""
    if wind is None or wind.speed_mps <= 0:
        return speed_mps
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy)
    if ln < 1e-6:
        return speed_mps
    te, tn = dx / ln, dy / ln
    to = math.radians(wind.from_deg + 180.0)
    we, wn = wind.speed_mps * math.sin(to), wind.speed_mps * math.cos(to)
    along = we * te + wn * tn
    cross = we * tn - wn * te
    if abs(cross) >= speed_mps:
        return None
    gs = math.sqrt(speed_mps ** 2 - cross ** 2) + along
    return gs if gs > 0.3 else None


# --- costing one vehicle's mission -----------------------------------------

@dataclass
class Costing:
    pct_used: float = 0.0
    seconds: float = 0.0
    max_dist_from_home_m: float = 0.0
    open_phases: List[int] = field(default_factory=list)    # objective phases with no time limit
    infeasible_legs: List[int] = field(default_factory=list)  # wind it cannot make headway in
    unresolved: List[int] = field(default_factory=list)      # no checkable geometry


# Nominal durations for phases whose length is not in the phase itself, s.
_NOMINAL_S = {"look_around": 12.0, "capture_photo": 2.0,
              "start_recording": 0.0, "stop_recording": 0.0}


def cost_mission(phases: List[dict], platform: Platform, home: Coord,
                 wind: Optional[Wind] = None, start: Optional[Coord] = None,
                 start_alt_m: float = 0.0,
                 area: Optional[Sequence[Coord]] = None) -> Costing:
    """Walk one vehicle's phases and add up battery %, time and the furthest
    distance from its home.

    `start` is where it is now (default: its home); `start_alt_m` > 1 means it
    is already airborne (a retask). After an open-ended objective phase the
    vehicle could be anywhere in the area, so the next leg is costed from the
    area's furthest point from that leg's end - the worst case, the only safe
    assumption. A vehicle that cannot hover pays its turns: each change of
    heading costs an arc of its minimum turn radius, and holding costs cruise,
    not hover. A ground vehicle ignores altitude and wind."""
    p = platform
    c = Costing()
    w = None if p.ground else wind
    v = max(p.cruise_speed_mps, 0.1)
    area_xy = poly_to_xy(area, home) if area else None
    pos: Optional[XY] = to_xy(start, home) if start else (0.0, 0.0)
    alt = 0.0 if p.ground else max(0.0, start_alt_m)
    landed = alt <= 1.0
    heading: Optional[float] = None

    def spend(seconds, mult=None):
        c.seconds += seconds
        c.pct_used += p.pct_per_s(p.cruise_mult if mult is None else mult) * seconds

    def climb(dz):
        if p.ground or abs(dz) < 1e-6:
            return
        if dz > 0:
            spend(dz / max(p.climb_rate_mps, 0.1), p.climb_mult)
        else:
            spend(-dz / max(p.descent_rate_mps, 0.1), p.descend_mult)

    def turn_to(new_heading):
        nonlocal heading
        if p.min_turn_radius_m and heading is not None:
            dth = abs((new_heading - heading + math.pi) % (2 * math.pi) - math.pi)
            spend(p.min_turn_radius_m * dth / v)
        heading = new_heading

    def leg(i, target: XY, new_alt=None):
        nonlocal pos, alt
        frm = pos
        if frm is None:
            if area_xy:
                frm = max(area_xy, key=lambda q: _dist(q, target))
            else:
                frm = (0.0, 0.0)
                c.unresolved.append(i)
        d = _dist(frm, target)
        if d > 1e-6:
            turn_to(math.atan2(target[1] - frm[1], target[0] - frm[0]))
            gs = ground_speed(v, frm, target, w)
            if gs is None:
                c.infeasible_legs.append(i)
                gs = max(v - (w.speed_mps if w else 0.0), 0.3)
            spend(d / gs)
        if new_alt is not None:
            climb(new_alt - alt)
            alt = new_alt
        pos = target
        c.max_dist_from_home_m = max(c.max_dist_from_home_m, _dist((0.0, 0.0), target))

    def unknown_move(i, dist):
        nonlocal pos, heading
        gs = max(v - (w.speed_mps if w else 0.0), 0.3)
        spend(dist / gs)
        c.unresolved.append(i)
        pos, heading = None, None

    for i, ph in enumerate(phases or []):
        t = ph.get("type")
        if t == "arm_and_takeoff":
            if p.ground:
                landed = False
                continue
            target = float(ph.get("altitude_m", 5.0))
            if landed:
                c.pct_used += p.takeoff_overhead_pct
            climb(target - alt)
            alt = target
            landed = False
        elif t == "go_to_gps" and ph.get("lat") is not None and ph.get("lon") is not None:
            new_alt = None if p.ground else float(ph.get("alt_m", alt or 15.0))
            leg(i, to_xy({"lat": float(ph["lat"]), "lon": float(ph["lon"])}, home), new_alt)
        elif t == "nav":
            new_alt = None if p.ground or ph.get("alt_m") is None else float(ph["alt_m"])
            if pos is not None and ("north_m" in ph or "east_m" in ph):
                leg(i, (pos[0] + float(ph.get("east_m", 0.0)),
                        pos[1] + float(ph.get("north_m", 0.0))), new_alt)
            else:
                # Body-relative or bearing moves depend on the heading at the
                # time: cost them into a full headwind, flag them unchecked.
                unknown_move(i, float(ph.get("distance_m") or math.hypot(
                    float(ph.get("forward_m") or 0.0), float(ph.get("right_m") or 0.0))))
        elif t == "fly_circle":
            r = max(float(ph.get("radius_m", 10.0)), p.min_turn_radius_m)
            spend((r + 2 * math.pi * r * float(ph.get("laps", 1))) / v)
            c.max_dist_from_home_m = max(c.max_dist_from_home_m, r)
        elif t in ("fly_rect", "survey_rect"):
            fwd = float(ph.get("forward_m", 10.0))
            right = float(ph.get("right_m", 10.0))
            if t == "fly_rect":
                spend((2 * (fwd + right) + 4 * p.min_turn_radius_m * math.pi / 2) / v)
            else:
                sp = max(float(ph.get("spacing_m", 1.0)), 0.1)
                cells = math.ceil(fwd / sp) * math.ceil(right / sp)
                spend((fwd * math.ceil(right / sp) + right) / v)
                spend(1.5 * cells, 1.0)   # one photo stop per cell
            c.max_dist_from_home_m = max(c.max_dist_from_home_m, math.hypot(
                fwd + float(ph.get("origin_forward_m", 0.0)),
                right + float(ph.get("origin_right_m", 0.0))))
            c.unresolved.append(i)
        elif t == "hold":
            # A hovering vehicle holds at hover cost, a ground one is parked,
            # anything else circles at cruise.
            mult = 0.1 if p.ground else (1.0 if p.can_hover else p.cruise_mult)
            spend(float(ph.get("seconds") or 0.0), mult)
        elif t in _NOMINAL_S:
            spend(_NOMINAL_S[t], 1.0 if p.can_hover else p.cruise_mult)
        elif t == "return_home":
            leg(i, (0.0, 0.0), None if p.ground else float(ph.get("alt_m", alt)))
        elif t == "land":
            if not p.ground:
                climb(-alt)
                alt = 0.0
                c.pct_used += p.landing_overhead_pct
            landed = True
        elif not t and ph.get("objective"):
            lim = ph.get("time_limit_s")
            if lim:
                spend(float(lim))
            else:
                c.open_phases.append(i)
            pos, heading = None, None
    # Not ending stopped: it will come home on its own, and that trip has to be
    # in the budget too.
    if not landed:
        frm = pos if pos is not None else (
            max(area_xy, key=lambda q: _dist(q, (0.0, 0.0))) if area_xy else (0.0, 0.0))
        gs = ground_speed(v, frm, (0.0, 0.0), w) or max(v - (w.speed_mps if w else 0.0), 0.3)
        spend(_dist(frm, (0.0, 0.0)) / gs)
        climb(-alt)
        if not p.ground:
            c.pct_used += p.landing_overhead_pct
    return c


# =============================================================================
# Assessing (and repairing) one vehicle's mission
# =============================================================================
# Issues carry a severity: "block" means the plan must not be dispatched,
# "warn" means it may be, but only after the operator acknowledges the warning
# (see conversations.plan_select_handler).

def _issue(kind, severity, detail, phase_index=None):
    d = {"kind": kind, "severity": severity, "detail": detail}
    if phase_index is not None:
        d["phase_index"] = phase_index
    return d


def _absolute_target(ph, pos: Optional[XY], home: Coord) -> Optional[XY]:
    t = ph.get("type")
    if t == "go_to_gps" and ph.get("lat") is not None and ph.get("lon") is not None:
        return to_xy({"lat": float(ph["lat"]), "lon": float(ph["lon"])}, home)
    if t == "nav" and pos is not None and ("north_m" in ph or "east_m" in ph):
        return (pos[0] + float(ph.get("east_m", 0.0)), pos[1] + float(ph.get("north_m", 0.0)))
    return None


def _waypoint(xy: XY, home: Coord, ref_phase: dict, note: str) -> dict:
    ll = to_ll(xy, home)
    wp = {"type": "go_to_gps", "lat": round(ll["lat"], 7), "lon": round(ll["lon"], 7),
          "description": note}
    for key in ("alt_m", "min_clearance_alt"):
        if ref_phase.get(key) is not None:
            wp[key] = ref_phase[key]
    return wp


def reroute_phases(phases: List[dict], home: Coord, area_xy: Optional[List[XY]],
                   nfz_xy: List[List[XY]], start: Optional[XY] = None,
                   margin_m: float = 10.0) -> Tuple[List[dict], List[dict]]:
    """Walk the checkable legs; where one enters a no-fly zone, or leaves the
    area between two points inside it, insert detour waypoints. Waypoints
    outside the area are not fixable - the plan was for somewhere else - and
    neither is a leg with no way round. Returns (phases, issues)."""
    out: List[dict] = []
    issues: List[dict] = []
    pos: Optional[XY] = start if start is not None else (0.0, 0.0)
    ends = [(0.0, 0.0)] + ([start] if start is not None else [])

    def transit(q):   # a leg touching home or the start is getting there or back
        return any(_dist(q, e) < 1.0 for e in ends)

    for i, ph in enumerate(phases or []):
        t = ph.get("type")
        target = _absolute_target(ph, pos, home)
        if t == "return_home":
            target = (0.0, 0.0)
        if target is None:
            if t in ("nav", "fly_circle", "fly_rect", "survey_rect") or (not t and ph.get("objective")):
                pos = None   # it ends somewhere this cannot know; legs from here are unchecked
            out.append(ph)
            continue
        is_home = _dist(target, (0.0, 0.0)) < 1.0
        if area_xy and not is_home and not inside_xy(target, area_xy):
            issues.append(_issue("outside_area", "block",
                                 "waypoint is outside the search area", i))
        zone = next((k for k, z in enumerate(nfz_xy) if inside_xy(target, z)), None)
        if zone is not None:
            issues.append(_issue("nfz", "block", f"waypoint is inside no-fly zone {zone}", i))
            out.append(ph)
            pos = target
            continue
        if pos is not None and any(inside_xy(pos, z) for z in nfz_xy):
            out.append(ph)   # already reported: the leg out of a zone is not a new fault
            pos = target
            continue
        if pos is not None:
            keep = area_xy if (area_xy and not transit(pos) and not transit(target)
                               and inside_xy(pos, area_xy) and inside_xy(target, area_xy)) else None
            if not leg_ok(pos, target, nfz_xy, keep):
                path = route(pos, target, nfz_xy, keep, margin_m)
                if path is None:
                    crosses = any(seg_enters(pos, target, z) for z in nfz_xy)
                    issues.append(_issue("nfz" if crosses else "leaves_area", "block",
                                         "no route to this waypoint that avoids the no-fly "
                                         "zones" + (" and stays in the area" if keep else ""), i))
                else:
                    for k, q in enumerate(path[:-1]):
                        out.append(_waypoint(q, home, ph, f"detour {k + 1} around a no-fly zone"))
        out.append(ph)
        pos = target
    return out, issues


def assess_mission(phases: List[dict], platform: Platform, home: Coord, *,
                   battery_pct: Optional[float],
                   area: Optional[Sequence[Coord]] = None,
                   no_fly: Sequence[Sequence[Coord]] = (),
                   wind: Optional[Wind] = None,
                   start: Optional[Coord] = None, start_alt_m: float = 0.0,
                   margin_m: float = 10.0, min_search_s: float = 30.0) -> dict:
    """Check one vehicle's mission against everything the planner knows, and
    repair what can be repaired: detours around no-fly zones, and a time limit
    on every open-ended search phase sized so the vehicle still gets home with
    its reserve. Returns {"phases", "issues", "adjustments", "costing"}."""
    issues: List[dict] = []
    adjustments: List[str] = []
    p = platform

    if not getattr(p, "known_type", True):
        issues.append(_issue("vehicle", "warn",
                             f"unknown vehicle type {p.vehicle_type!r}: planned with "
                             f"quadcopter assumptions"))
    issues += [dict(x, severity="block") for x in phase_capability_issues(phases, p)]

    area_xy = poly_to_xy(area, home) if area else None
    nfz_xy = [poly_to_xy(z, home) for z in (no_fly or []) if len(z) >= 3]
    start_xy = to_xy(start, home) if start else None
    repaired, geo = reroute_phases(phases, home, area_xy, nfz_xy, start_xy, margin_m)
    issues += geo
    if len(repaired) > len(phases):
        adjustments.append(f"added {len(repaired) - len(phases)} detour waypoint(s) "
                           f"around no-fly zones")

    cost = cost_mission(repaired, p, home, wind, start, start_alt_m, area)

    # Bound every open-ended search phase by what the battery can pay for.
    if cost.open_phases:
        if battery_pct is None:
            issues.append(_issue("energy", "warn",
                                 "battery level unknown: search phases left without a "
                                 "time limit"))
        else:
            spare = battery_pct - cost.pct_used - p.landing_reserve_pct
            rate = p.pct_per_s(p.cruise_mult)
            each = spare / len(cost.open_phases) / rate if rate > 0 else 0.0
            if each < min_search_s:
                issues.append(_issue("energy", "block",
                                     f"battery {battery_pct:.0f}% leaves "
                                     f"{max(each, 0):.0f} s for each search phase after the "
                                     f"flying and the {p.landing_reserve_pct:.0f}% reserve "
                                     f"(need at least {min_search_s:.0f} s)"))
            else:
                limit = int(each // 5 * 5)
                for k in cost.open_phases:
                    repaired[k] = dict(repaired[k], time_limit_s=limit)
                adjustments.append(f"limited each search phase to {limit} s so it gets home "
                                   f"with the {p.landing_reserve_pct:.0f}% reserve")
                cost = cost_mission(repaired, p, home, wind, start, start_alt_m, area)

    end_pct = None
    if battery_pct is not None:
        end_pct = battery_pct - cost.pct_used
        if end_pct < p.landing_reserve_pct - 1e-6:
            issues.append(_issue("energy", "block",
                                 f"needs ~{cost.pct_used:.0f}% of the pack; {battery_pct:.0f}% "
                                 f"would end at {end_pct:.0f}%, under the "
                                 f"{p.landing_reserve_pct:.0f}% reserve"))
    elif not cost.open_phases:
        issues.append(_issue("energy", "warn",
                             f"battery level unknown: needs ~{cost.pct_used:.0f}% of a full "
                             f"pack, not checked against the real charge"))
    if p.source != "drone":
        issues.append(_issue("energy", "warn",
                             f"energy costed with ASSUMED {p.vehicle_type} defaults; the "
                             f"vehicle has not reported its own model"))

    furthest = cost.max_dist_from_home_m
    if area_xy and (cost.open_phases or cost.unresolved):
        furthest = max(furthest, max_dist_from((0.0, 0.0), area_xy))
    if start_xy is not None:
        furthest = max(furthest, _dist((0.0, 0.0), start_xy))
    if furthest > p.max_range_m + 1e-6:
        issues.append(_issue("range", "block",
                             f"goes up to {furthest:.0f} m from its home; the limit for this "
                             f"{p.vehicle_type} is {p.max_range_m:.0f} m"))

    if not p.ground:
        if wind is None:
            issues.append(_issue("wind", "warn", "wind not supplied: not checked"))
        else:
            if wind.speed_mps > p.max_wind_mps + 1e-6:
                issues.append(_issue("wind", "block",
                                     f"wind {wind.speed_mps:g} m/s is over the "
                                     f"{p.max_wind_mps:g} m/s limit for this {p.vehicle_type}"))
            if cost.infeasible_legs:
                issues.append(_issue("wind", "block",
                                     f"{len(cost.infeasible_legs)} leg(s) cannot make headway at "
                                     f"{p.cruise_speed_mps:g} m/s in a {wind.speed_mps:g} m/s wind "
                                     f"from {wind.from_deg:.0f} deg", cost.infeasible_legs[0]))

    if cost.unresolved:
        issues.append(_issue("geometry", "warn",
                             f"{len(set(cost.unresolved))} phase(s) have no fixed position to "
                             f"check; the on-board geofence keeps them in the area"))

    return {
        "phases": repaired,
        "issues": issues,
        "adjustments": adjustments,
        "costing": {
            "est_minutes": round(cost.seconds / 60.0, 1),
            "battery_used_pct": round(cost.pct_used, 1),
            "battery_end_pct": None if end_pct is None else round(end_pct, 1),
            "max_dist_from_home_m": round(furthest),
            "energy_model": p.source,
        },
    }


def blocking(issues: Sequence[dict]) -> List[dict]:
    return [x for x in issues if x.get("severity") == "block"]


def warnings(issues: Sequence[dict]) -> List[dict]:
    return [x for x in issues if x.get("severity") == "warn"]


# =============================================================================
# The fleet: who can go, and splitting the area between them
# =============================================================================

@dataclass
class Vehicle:
    drone_id: str
    platform: Platform
    home: Coord                         # its own home: where it returns and lands
    battery_pct: Optional[float] = None
    position: Optional[Coord] = None    # where it is now, if known
    altitude_m: float = 0.0             # > 1 = airborne now
    online: bool = True
    can_takeoff: Optional[bool] = None
    takeoff_reason: Optional[str] = None
    pilot_control: Optional[str] = None
    mission_running: Optional[str] = None

    @property
    def airborne(self) -> bool:
        return not self.platform.ground and self.altitude_m > 1.0


def _poly_distance(p: XY, poly: Sequence[XY]) -> float:
    """Distance from p to the polygon (0 when inside)."""
    if inside_xy(p, poly):
        return 0.0
    best = float("inf")
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ab = (b[0] - a[0], b[1] - a[1])
        ln2 = ab[0] ** 2 + ab[1] ** 2 or 1e-12
        t = max(0.0, min(1.0, ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / ln2))
        best = min(best, _dist(p, (a[0] + t * ab[0], a[1] + t * ab[1])))
    return best


def eligibility(v: Vehicle, wind: Optional[Wind] = None,
                area: Optional[Sequence[Coord]] = None) -> List[dict]:
    """Why this vehicle should not be sent at all (severity "block"), or what
    the operator should know before it is (severity "warn")."""
    p = v.platform
    out = []
    if not v.online:
        out.append(_issue("offline", "block", "no heartbeat in the last 60 s"))
    if v.pilot_control:
        out.append(_issue("pilot", "block", f"the pilot has it ({v.pilot_control})"))
    if not p.gps:
        out.append(_issue("capability", "block",
                          f"a {p.vehicle_type} has no GPS and cannot fly a lat/lon plan"))
    if not v.airborne:
        if v.can_takeoff is False:
            out.append(_issue("preflight", "block",
                              f"cannot take off: {v.takeoff_reason or 'preflight check failed'}"))
        if v.battery_pct is not None and v.battery_pct < p.min_takeoff_pct:
            out.append(_issue("energy", "block",
                              f"battery {v.battery_pct:.0f}% is under the "
                              f"{p.min_takeoff_pct:.0f}% needed to start"))
    if not p.ground and wind is not None:
        if wind.speed_mps > p.max_wind_mps:
            out.append(_issue("wind", "block",
                              f"wind {wind.speed_mps:g} m/s is over its {p.max_wind_mps:g} m/s limit"))
        elif wind.speed_mps >= p.cruise_speed_mps:
            out.append(_issue("wind", "block",
                              f"wind {wind.speed_mps:g} m/s is at or over its "
                              f"{p.cruise_speed_mps:g} m/s cruise speed"))
    if area:
        gap = _poly_distance((0.0, 0.0), poly_to_xy(area, v.home))
        if gap > p.max_range_m:
            out.append(_issue("range", "block",
                              f"the area is {gap:.0f} m from its home; its limit is "
                              f"{p.max_range_m:.0f} m"))
    if v.mission_running:
        out.append(_issue("busy", "warn",
                          f"already flying mission {v.mission_running}; this replaces it"))
    return out


def _search_rate_m2_per_s(v: Vehicle, spacing_m: float) -> float:
    """Area it covers per second: its speed times the lane spacing."""
    return max(v.platform.cruise_speed_mps, 0.1) * max(spacing_m, 0.1)


def _transit_s(v: Vehicle, area_ref: Coord, area_xy_ref: List[XY]) -> float:
    start = v.position if (v.airborne and v.position) else v.home
    return _poly_distance(to_xy(start, area_ref), area_xy_ref) / max(v.platform.cruise_speed_mps, 0.1)


def finish_time(vehicles: List[Vehicle], area_ref: Coord, area_xy_ref: List[XY],
                spacing: Dict[str, float]) -> Tuple[float, List[float]]:
    """Earliest time T at which `vehicles` together cover the area if each
    starts covering when it arrives: sum(rate_i * (T - transit_i)+) = area.
    Returns (T, each vehicle's share of the area in m^2)."""
    total = area_m2(area_xy_ref)
    rates = [_search_rate_m2_per_s(v, spacing[v.drone_id]) for v in vehicles]
    trans = [_transit_s(v, area_ref, area_xy_ref) for v in vehicles]

    def covered(T):
        return sum(r * max(T - t, 0.0) for r, t in zip(rates, trans))

    lo, hi = 0.0, max(trans) + total / max(min(rates), 1e-9) + 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if covered(mid) < total:
            lo = mid
        else:
            hi = mid
    T = hi
    return T, [r * max(T - t, 0.0) for r, t in zip(rates, trans)]


def choose_fleet(vehicles: List[Vehicle], area: List[Coord],
                 spacing_m: Optional[float] = None, min_gain: float = 0.10,
                 max_vehicles: Optional[int] = None) -> Tuple[List[Vehicle], Dict[str, str]]:
    """Pick which of `vehicles` to send: start from the one that would finish
    the area alone soonest, then keep adding whichever helps most while it cuts
    the finish time by at least `min_gain`. Returns (chosen, {drone_id: why
    not} for the rest)."""
    if not vehicles:
        return [], {}
    ref = {"lat": sum(q["lat"] for q in area) / len(area),
           "lon": sum(q["lon"] for q in area) / len(area)}
    axy = poly_to_xy(area, ref)
    sp = {v.drone_id: spacing_m or v.platform.default_spacing_m for v in vehicles}
    alone = {v.drone_id: finish_time([v], ref, axy, sp)[0] for v in vehicles}
    chosen = [min(vehicles, key=lambda v: alone[v.drone_id])]
    T = alone[chosen[0].drone_id]
    rest = [v for v in vehicles if v is not chosen[0]]
    why: Dict[str, str] = {}
    while rest and (max_vehicles is None or len(chosen) < max_vehicles):
        best, best_T = None, T
        for v in rest:
            t = finish_time(chosen + [v], ref, axy, sp)[0]
            if t < best_T:
                best, best_T = v, t
        if best is None or best_T > T * (1 - min_gain):
            break
        chosen.append(best)
        rest.remove(best)
        T = best_T
    for v in rest:
        gain = 1 - finish_time(chosen + [v], ref, axy, sp)[0] / T if T > 0 else 0.0
        why[v.drone_id] = (f"would speed the job up by only {max(gain, 0) * 100:.0f}% "
                           f"(needs {min_gain * 100:.0f}%)" if max_vehicles is None
                           or len(chosen) < max_vehicles else
                           f"plan limited to {max_vehicles} vehicle(s)")
    return chosen, why


def _sector_entry(home_xy: XY, sector_xy: List[XY], nfz_xy: Sequence[Sequence[XY]],
                  spacing_m: float, margin_m: float) -> Optional[XY]:
    """Where to go into the sector: its centroid if that is clear of every
    zone, otherwise the clear sweep-lane point nearest home (lane points are by
    construction inside the sector and out of the zones). None if neither."""
    c = centroid(sector_xy)
    if inside_xy(c, sector_xy) and not any(inside_xy(c, z) for z in nfz_xy):
        return c
    pts = sweep_lanes(sector_xy, max(spacing_m, 1.0), nfz_xy, margin_m)
    return min(pts, key=lambda q: _dist(q, home_xy)) if pts else None


def build_sector_mission(v: Vehicle, sector: List[Coord], style: str, *,
                         target: str, no_fly: Sequence[Sequence[Coord]] = (),
                         area: Optional[Sequence[Coord]] = None,
                         altitude_m: Optional[float] = None, spacing_m: Optional[float] = None,
                         margin_m: float = 10.0, lane_fraction: float = 1.0) -> List[dict]:
    """One vehicle's phases for one sector.

    style "search": fly to the sector and let the on-board vision model hunt
    for `target` inside it (the mission carries the sector as its geofence).
    style "sweep": fly serpentine lanes over the sector, recording, with every
    joining leg routed around the no-fly zones - full coverage, no vision model
    needed, and the only style a vehicle without one can fly. `lane_fraction`
    < 1 flies only that leading share of the sweep (a partial sweep that fits
    the battery)."""
    p = v.platform
    home = v.home
    alt = None if p.ground else min(altitude_m or p.default_alt_m, p.ceiling_m)
    sp = spacing_m or p.default_spacing_m
    sector_xy = poly_to_xy(sector, home)
    nfz_xy = [poly_to_xy(z, home) for z in no_fly if len(z) >= 3]
    area_xy = poly_to_xy(area, home) if area else None
    phases: List[dict] = []
    first = {"type": "arm_and_takeoff"}
    if alt is not None:
        first["altitude_m"] = alt
    phases.append(first)

    def goto(xy, note):
        wp = {"type": "go_to_gps", **{k: round(x, 7) for k, x in to_ll(xy, home).items()},
              "description": note}
        if alt is not None:
            wp["alt_m"] = alt
        phases.append(wp)

    def fly(frm, to, note, keep=None):
        path = route(frm, to, nfz_xy, keep, margin_m)
        if path is None:
            return False
        for q in path[:-1]:
            goto(q, "around a no-fly zone")
        goto(to, note)
        return True

    # A retask catches it in the air: it starts from where it is.
    pos = to_xy(v.position, home) if (v.airborne and v.position) else (0.0, 0.0)
    if style == "sweep":
        lanes = sweep_lanes(sector_xy, sp, nfz_xy, margin_m)
        if lane_fraction < 1.0:
            lanes = lanes[:max(2, int(len(lanes) * lane_fraction) // 2 * 2)]
        if not lanes:
            return []
        if not fly(pos, lanes[0], "start of sweep"):
            return []
        phases.append({"type": "start_recording"})
        pos = lanes[0]
        for k, q in enumerate(lanes[1:], 1):
            keep = area_xy if area_xy and inside_xy(pos, area_xy) and inside_xy(q, area_xy) else None
            if not fly(pos, q, f"sweep point {k + 1}/{len(lanes)}", keep):
                return []
            pos = q
        phases.append({"type": "stop_recording"})
    else:
        entry = _sector_entry(pos, sector_xy, nfz_xy, sp, margin_m)
        if entry is None or not fly(pos, entry, "into the assigned sector"):
            return []
        phases.append({"objective": f"Search the assigned sector for the {target}, "
                                    f"staying inside it and routing around anything in the way",
                       "success": f"{target} located and its position reported"})
    phases.append({"type": "return_home"} if p.ground else
                  {"type": "return_home", "alt_m": alt})
    phases.append({"type": "land"})
    return phases


def plan_split(vehicles: List[Vehicle], area: List[Coord], style: str, *,
               target: str, no_fly: Sequence[Sequence[Coord]] = (),
               wind: Optional[Wind] = None, altitude_m: Optional[float] = None,
               spacing_m: Optional[float] = None, margin_m: float = 10.0,
               rebalance_rounds: int = 4, partial: bool = False) -> dict:
    """Split `area` between `vehicles` and build each one's sector mission.

    Strips are cut across the area's long axis and handed out in the order the
    vehicles' homes lie along it, so each gets the strip nearest its own home.
    Strip sizes are set so all of them finish together (each covers at its own
    speed x lane spacing, from when it arrives - see finish_time); a vehicle
    whose strip its battery cannot pay for is shrunk and the rest re-split, a
    few rounds at most. In a "search" plan a vehicle with no on-board vision
    model sweeps its strip instead. With `partial`, a sweep the batteries
    cannot finish is cut back to what they can (see _trim_to_battery) and the
    result carries "coverage". Returns {"per_drone": [{drone_id, phases,
    sector, style, assessment}], "sectors", "axis_deg", "finish_min"}."""
    if not vehicles or not area or len(area) < 3:
        return {"per_drone": [], "sectors": []}
    ref = {"lat": sum(q["lat"] for q in area) / len(area),
           "lon": sum(q["lon"] for q in area) / len(area)}
    area_xy = poly_to_xy(area, ref)
    u = longest_axis(area_xy)
    order = sorted(vehicles, key=lambda v: _dot(to_xy(v.home, ref), u))
    sp = {v.drone_id: spacing_m or v.platform.default_spacing_m for v in order}
    T, shares = finish_time(order, ref, area_xy, sp)
    weights = [max(a, 1.0) for a in shares]
    result = None
    for _ in range(max(1, rebalance_rounds)):
        sectors_xy = split_polygon(area_xy, weights)
        per_drone = []
        short = {}
        for v, sec_xy in zip(order, sectors_xy):
            sector = [to_ll(q, ref) for q in sec_xy]
            own = style if (style != "search" or v.platform.vlm) else "sweep"
            phases = build_sector_mission(v, sector, own, target=target, no_fly=no_fly,
                                          area=area, altitude_m=altitude_m,
                                          spacing_m=sp[v.drone_id], margin_m=margin_m)
            a = assess_mission(phases, v.platform, v.home, battery_pct=v.battery_pct,
                               area=area, no_fly=no_fly, wind=wind,
                               start=v.position if v.airborne else None,
                               start_alt_m=v.altitude_m if v.airborne else 0.0,
                               margin_m=margin_m)
            if not phases:
                a["issues"].append(_issue("geometry", "block",
                                          "no route into this sector that avoids the no-fly zones"))
            per_drone.append({"drone_id": v.drone_id, "phases": a["phases"], "sector": sector,
                              "style": own, "assessment": a})
            # Only a sweep has a fixed size to rebalance; a search is bounded
            # by its time limit instead.
            if own == "sweep" and v.battery_pct is not None:
                need = a["costing"]["battery_used_pct"]
                have = v.battery_pct - v.platform.landing_reserve_pct
                if need > have > 0:
                    short[v.drone_id] = have / need
        result = {"per_drone": per_drone, "sectors": [d["sector"] for d in per_drone],
                  "axis_deg": round(math.degrees(math.atan2(u[0], u[1])) % 180, 1),
                  "finish_min": round(max(d["assessment"]["costing"]["est_minutes"]
                                          for d in per_drone), 1)}
        if not short or len(order) == 1 or len(short) == len(order):
            break
        weights = [w * short.get(v.drone_id, 1.0) * (0.95 if v.drone_id in short else 1.0)
                   for v, w in zip(order, weights)]
    if style == "sweep" and partial:
        _trim_to_battery(result, order, area, target, no_fly, wind, altitude_m, sp, margin_m)
    return result


def _trim_to_battery(result, order, area, target, no_fly, wind, altitude_m, sp, margin_m):
    """For each sweep its battery cannot finish, fly the largest leading share
    of its lanes that it can, and record how much of its sector that covers."""
    by_id = {v.drone_id: v for v in order}
    covered = total = 0.0
    for d in result["per_drone"]:
        v = by_id[d["drone_id"]]
        sec_area = area_m2(poly_to_xy(d["sector"], v.home))
        total += sec_area
        d["coverage"] = 1.0
        if d["style"] != "sweep" or not any(i["kind"] == "energy"
                                            for i in blocking(d["assessment"]["issues"])):
            covered += sec_area
            continue
        lo, hi, best = 0.0, 1.0, None
        for _ in range(10):
            mid = (lo + hi) / 2
            ph = build_sector_mission(v, d["sector"], "sweep", target=target, no_fly=no_fly,
                                      area=area, altitude_m=altitude_m, spacing_m=sp[v.drone_id],
                                      margin_m=margin_m, lane_fraction=mid)
            a = assess_mission(ph, v.platform, v.home, battery_pct=v.battery_pct, area=area,
                               no_fly=no_fly, wind=wind, margin_m=margin_m)
            if ph and not blocking(a["issues"]):
                best, lo = (mid, a), mid
            else:
                hi = mid
        d["coverage"] = 0.0
        if best is not None:
            frac, a = best
            a["adjustments"].append(f"sweeps only the first {frac * 100:.0f}% of its sector: "
                                    f"that is what its battery covers")
            d.update(phases=a["phases"], assessment=a, coverage=round(frac, 2))
            covered += sec_area * frac
    result["coverage"] = round(covered / total, 2) if total else 0.0
    result["finish_min"] = round(max(d["assessment"]["costing"]["est_minutes"]
                                     for d in result["per_drone"]), 1)
