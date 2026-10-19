"""search_patterns.py — fixed-wing search geometry.

A fixed-wing can't hover or spin in place, so "search here" and "inspect that"
both have to become sequences of flyable waypoints rather than a stop-and-look
action. These functions return waypoint lists (world ENU x, y — altitude is the
caller's concern, typically the current cruise altitude) parameterized by the
vehicle's own turn radius and sense range, so the geometry is never tighter than
the airframe can actually fly and never denser/sparser than its sensor footprint
calls for.

Waypoints are consumed one at a time via the existing backend.goto() proportional
heading-hold loop (backends.py) — that loop already re-aims continuously as it
flies, so it carves a smooth curve toward each successive point rather than
needing literal straight-line segments between them. The spacing computed here
only needs to be "reasonable" for a heading-hold controller, not exact.
"""
from __future__ import annotations

import math
from typing import List, Tuple

Point = Tuple[float, float]


def turn_radius_m(vehicle_class) -> float:
    """Minimum turn radius at cruise speed: v / max_yaw_rate.

    This is the same quantity the fixed-wing scenario YAML comments size their
    obstacle spacing against (see scenarios_fw/fw_sweep.yaml) — reused here instead
    of re-deriving it so search geometry and obstacle geometry always agree.
    """
    return vehicle_class.max_speed_mps / max(vehicle_class.max_yaw_rate_radps, 1e-6)


def expanding_orbit(
    center: Point,
    vehicle_class,
    laps: int = 3,
    start_radius: float = None,
    growth_per_lap: float = None,
) -> List[Point]:
    """Outward spiral around `center`: tight first lap, widening each lap after.

    Used when a target isn't where memory says it should be — starts close (in
    case the memory fix was just imprecise) and grows outward by one sense-range
    per lap (in case the target moved). Never starts tighter than the vehicle can
    physically turn.
    """
    r_min = start_radius if start_radius is not None else max(
        turn_radius_m(vehicle_class) * 1.2, vehicle_class.sense_range_m * 0.5
    )
    growth = growth_per_lap if growth_per_lap is not None else vehicle_class.sense_range_m

    pts: List[Point] = []
    for lap in range(laps):
        radius = r_min + lap * growth
        # Angular step sized so consecutive points are at least one turn-radius
        # apart along the arc — a heading-hold controller can carve that turn.
        points_per_lap = max(6, round((2 * math.pi * radius) / (2 * turn_radius_m(vehicle_class))))
        for i in range(points_per_lap):
            angle = 2 * math.pi * i / points_per_lap
            pts.append((center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)))
    return pts


def orbit(center: Point, radius: float, vehicle_class, laps: int = 2) -> List[Point]:
    """Fixed-radius orbit around `center` — "inspect in place" for a vehicle that
    can't stop. `radius` is clamped up to the vehicle's own minimum turn radius
    (with a small margin) if given tighter than that, since a request to orbit
    inside the vehicle's turn circle is physically unflyable."""
    r = max(radius, turn_radius_m(vehicle_class) * 1.05)
    points_per_lap = max(8, round((2 * math.pi * r) / (2 * turn_radius_m(vehicle_class))))
    pts: List[Point] = []
    for lap in range(laps):
        for i in range(points_per_lap):
            angle = 2 * math.pi * i / points_per_lap
            pts.append((center[0] + r * math.cos(angle), center[1] + r * math.sin(angle)))
    return pts


def lawnmower(
    bounds: Tuple[Point, Point],
    vehicle_class,
    heading_deg: float = 0.0,
    lane_spacing: float = None,
) -> List[Point]:
    """Boustrophedon coverage of a rectangular area given as (min_xy, max_xy) in
    world ENU. `heading_deg` rotates the lane direction (0 = lanes run along
    +x/east); rotation is about the area's own center so the covered footprint
    doesn't drift outside bounds.

    Lane spacing defaults to 0.5x sense_range_m, NOT sense_range_m itself or a
    multiple of it. sense_range_m is a maximum forward detection *range*
    (fixedwing_manager.gd's DETECT_RANGE), not a lateral swath — the sensor is a
    narrow forward-looking cone (60 deg H-FOV in that same file), so a lane's
    actual cross-track detection footprint at max range is much smaller than
    sense_range_m itself. Confirmed empirically: an earlier 2x-sense_range
    spacing left one of three ground-truth props (water_tower, dead center of
    the search bounds) completely unswept between lanes. VehicleClass has no
    FOV field (deliberately — it's meant to stay geometry-only, see its own
    docstring), so this errs toward over-coverage with a plain fraction of
    sense_range_m rather than hardcoding a specific sensor's FOV here.
    """
    (min_x, min_y), (max_x, max_y) = bounds
    spacing = lane_spacing if lane_spacing is not None else 0.5 * vehicle_class.sense_range_m
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    theta = math.radians(heading_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    def rotate(px: float, py: float) -> Point:
        dx, dy = px - cx, py - cy
        return (cx + dx * cos_t - dy * sin_t, cy + dx * sin_t + dy * cos_t)

    half_h = (max_y - min_y) / 2.0
    n_lanes = max(1, math.ceil((2 * half_h) / spacing) + 1)
    pts: List[Point] = []
    for i in range(n_lanes):
        lane_y = min_y + i * spacing
        if lane_y > max_y:
            break
        if i % 2 == 0:
            pts.append(rotate(min_x, lane_y))
            pts.append(rotate(max_x, lane_y))
        else:
            pts.append(rotate(max_x, lane_y))
            pts.append(rotate(min_x, lane_y))
    return pts


def rectangle(
    size: Point,
    origin: Point,
    vehicle_class,
    heading_deg: float = 0.0,
    waypoints_per_side: int = 1,
) -> List[Point]:
    """Perimeter of a rectangle, as flyable world-ENU waypoints.

    `size` is (forward_m, right_m) and `origin` is (forward_m, right_m) of the
    near corner — both in the *body* frame whose +forward axis is `heading_deg`
    (compass degrees, 0 = north/+y, increasing clockwise). Returns points in
    world ENU (x=east, y=north), the same convention orbit()/lawnmower() use, so
    the caller feeds them straight to backend.goto(north_m, east_m, alt).

    Body frame is the whole point: every other positional phase is
    north/east-from-home, which cannot express "10 meters ahead and 5 to the
    right". Rotation happens here, once, rather than at each call site.

    Corners are arcs of the vehicle's own minimum turn radius, not points. For a
    quad or rover that radius is sub-metre so the arcs collapse to the corners
    themselves and the output is the plain rectangle; for a fixed-wing (~42 m)
    they are the difference between a flyable circuit and four waypoints that
    each time out. This mirrors orbit()'s radius clamp, and exists for the same
    reason recorded there: an unflyable geometry surfaces as a misleading
    "blocked waypoint", never as "you asked for something the airframe can't do".

    A side shorter than twice the turn radius cannot contain even one corner
    arc, so the side is clamped up to 2*r — at which point the rectangle has
    degenerated into a circle of that radius, which is the honest answer for
    "fly a 10 m box" on an airframe that needs 42 m to turn.
    """
    # A vehicle that can stop takes corners sharply; only one that must keep
    # flying needs them arced. See VehicleClass.can_hover.
    r = 0.0 if vehicle_class.can_hover else turn_radius_m(vehicle_class)
    fwd, right = float(size[0]), float(size[1])
    # A zero/negative side is a degenerate rectangle; treat it as a point-ish
    # box rather than emitting NaNs or a reversed traversal.
    fwd, right = max(abs(fwd), 1e-3), max(abs(right), 1e-3)

    # Corner arcs need r of straight run on each side of the corner, so a side
    # must be at least 2*r to hold the two arcs that meet on it.
    min_side = 2.0 * r
    if r > 1e-3 and (fwd < min_side or right < min_side):
        fwd = max(fwd, min_side)
        right = max(right, min_side)

    # Corner radius can never exceed half the shorter side, or opposing arcs
    # would overlap and the traversal would double back.
    rc = min(r, min(fwd, right) / 2.0)

    o_f, o_r = float(origin[0]), float(origin[1])
    # Body-frame corners, counter-clockwise starting at the near corner so the
    # traversal is consistent regardless of sign conventions upstream.
    corners = [
        (o_f, o_r),
        (o_f + fwd, o_r),
        (o_f + fwd, o_r + right),
        (o_f, o_r + right),
    ]

    n_mid = max(0, int(waypoints_per_side) - 1)
    body_pts: List[Point] = []
    for i, c in enumerate(corners):
        nxt = corners[(i + 1) % 4]
        prv = corners[(i - 1) % 4]
        if rc > 1e-3:
            # Replace the sharp corner with entry/exit points rc back along each
            # adjoining side, plus one mid-arc point so a heading-hold
            # controller carves the turn instead of overshooting it.
            body_pts.append(_along(c, prv, rc))
            body_pts.append(_arc_mid(c, prv, nxt, rc))
            body_pts.append(_along(c, nxt, rc))
        else:
            body_pts.append(c)
        for k in range(1, n_mid + 1):
            t = k / (n_mid + 1)
            start = _along(c, nxt, rc) if rc > 1e-3 else c
            end = _along(nxt, c, rc) if rc > 1e-3 else nxt
            body_pts.append((start[0] + (end[0] - start[0]) * t,
                             start[1] + (end[1] - start[1]) * t))

    pts = [_body_to_enu(f, rt, heading_deg) for f, rt in body_pts]
    # When a side is exactly 2*rc the two corner arcs meet, so the exit point of
    # one corner and the entry point of the next coincide. Flying to a waypoint
    # you are already standing on is at best wasted time and at worst a goto()
    # that never reports arrival, so collapse them.
    out: List[Point] = []
    for pt in pts:
        if not out or math.hypot(pt[0] - out[-1][0], pt[1] - out[-1][1]) > 1e-6:
            out.append(pt)
    if len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= 1e-6:
        out.pop()
    return out


def photo_grid(
    size: Point,
    origin: Point,
    spacing_m: float,
    heading_deg: float = 0.0,
) -> List[Point]:
    """One point per spacing_m x spacing_m cell of a rectangle, at each cell's
    centre, in serpentine order. The survey counterpart of rectangle(): same
    body-frame `size`/`origin` and the same world-ENU output.

    Coverage is a count of cells, not of seconds. "Photograph every square
    meter of the 5 x 5 m rectangle" is 25 photos wherever the aircraft is and
    however fast it flies. Planned instead as a timed photo every second while
    tracing the rectangle's outline, it came back as 100 photos of the
    perimeter and none of the inside (observed on the quadcopter).

    A side that is not a whole number of cells is split into equal cells
    slightly smaller than spacing_m, never larger, so no strip is left out.
    """
    fwd, right = abs(float(size[0])), abs(float(size[1]))
    spacing = max(float(spacing_m), 1e-3)
    # The epsilon keeps 5.0 / 1.0 from rounding up to 6 cells.
    n_f = max(1, math.ceil(fwd / spacing - 1e-9))
    n_r = max(1, math.ceil(right / spacing - 1e-9))
    step_f, step_r = fwd / n_f, right / n_r
    o_f, o_r = float(origin[0]), float(origin[1])
    body_pts: List[Point] = []
    for i in range(n_f):
        cols = range(n_r) if i % 2 == 0 else range(n_r - 1, -1, -1)
        for j in cols:
            body_pts.append((o_f + (i + 0.5) * step_f, o_r + (j + 0.5) * step_r))
    return [_body_to_enu(f, rt, heading_deg) for f, rt in body_pts]


MAX_SURVEY_PHOTOS = 100


def capped_photo_grid(size: Point, origin: Point, spacing_m: float,
                      heading_deg: float = 0.0,
                      max_photos: int = MAX_SURVEY_PHOTOS) -> Tuple[List[Point], float]:
    """photo_grid(), with the spacing widened until it is at most max_photos
    points. Returns (points, spacing actually used). A hundred photos is
    already more than a chat can usefully show, and each is a stop that
    spends battery."""
    spacing = max(float(spacing_m or 1.0), 0.1)
    while True:
        pts = photo_grid(size, origin, spacing, heading_deg=heading_deg)
        if len(pts) <= max_photos:
            return pts, spacing
        spacing *= 1.1


def plan_route(points: List[Point], start: Point = None, end: Point = None,
               time_budget_s: float = 2.0) -> List[int]:
    """The order to visit `points` in (indices into it), shortest first.

    `start` is where the aircraft is now; `end`, if given, is where it goes
    straight after (home, when a return follows). Both count toward the length:
    a grid's serpentine is the shortest way through the cells alone, but it
    finishes at the far corner of an odd-row grid, and the flight home from
    there was the longest leg of the mission. With the trip home counted the
    route comes back down the grid instead.

    Visiting order is a travelling-salesman path, so this is a heuristic, not
    an exact answer: several seed routes (the given order, its reverse,
    nearest-neighbour from the start), each improved by 2-opt and Or-opt
    until neither shortens it, best kept. For a full grid it reaches the
    unit-step optimum; for 100 points it takes well under a second.
    `time_budget_s` bounds it on a slow companion computer: when it runs out
    the best route so far is returned, which is never worse than the given
    order.
    """
    import time as _time

    n = len(points)
    if n <= 1:
        return list(range(n))
    deadline = _time.monotonic() + time_budget_s
    # Indices n and n+1 are the start and end anchors. A missing anchor is a
    # point zero distance from everything, so the route may begin/end anywhere.
    pts = list(points) + [start, end]
    d = [[0.0] * (n + 2) for _ in range(n + 2)]
    for i in range(n + 2):
        for j in range(i + 1, n + 2):
            if pts[i] is None or pts[j] is None:
                continue
            d[i][j] = d[j][i] = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])

    def length(order):
        seq = [n] + order + [n + 1]
        return sum(d[seq[k]][seq[k + 1]] for k in range(len(seq) - 1))

    def improve(order):
        seq = [n] + list(order) + [n + 1]   # seq[0], seq[-1] never move
        m = len(seq)
        better = True
        while better and _time.monotonic() < deadline:
            better = False
            # 2-opt: reverse seq[i..j].
            for i in range(1, m - 2):
                a, b = seq[i - 1], seq[i]
                for j in range(i + 1, m - 1):
                    c, e = seq[j], seq[j + 1]
                    if d[a][c] + d[b][e] < d[a][b] + d[c][e] - 1e-9:
                        seq[i:j + 1] = reversed(seq[i:j + 1])
                        b = seq[i]
                        better = True
            # Or-opt: move a run of 1-3 points elsewhere, either way round.
            for seg in (1, 2, 3):
                i = 1
                while i + seg <= m - 1:
                    s0, s1 = seq[i], seq[i + seg - 1]
                    p, q = seq[i - 1], seq[i + seg]
                    gain = d[p][s0] + d[s1][q] - d[p][q]
                    moved = False
                    for k in range(0, m - 1):
                        if i - 1 <= k <= i + seg - 1:
                            continue
                        x, y = seq[k], seq[k + 1]
                        fwd = d[x][s0] + d[s1][y] - d[x][y]
                        rev = d[x][s1] + d[s0][y] - d[x][y]
                        if min(fwd, rev) < gain - 1e-9:
                            run = seq[i:i + seg]
                            if rev < fwd:
                                run.reverse()
                            rest = seq[:i] + seq[i + seg:]
                            at = k + 1 if k < i else k + 1 - seg
                            seq = rest[:at] + run + rest[at:]
                            better = moved = True
                            break
                    if not moved:
                        i += 1
        return seq[1:-1]

    given = list(range(n))
    seeds = [given, given[::-1]]
    if start is not None:
        left, here, nn = set(given), n, []
        while left:
            nxt = min(left, key=lambda k: d[here][k])
            nn.append(nxt)
            left.discard(nxt)
            here = nxt
        seeds.append(nn)
    best = min(seeds, key=length)
    for seed in seeds:
        if _time.monotonic() >= deadline:
            break
        cand = improve(seed)
        if length(cand) < length(best) - 1e-9:
            best = cand
    return best


def _along(frm: Point, toward: Point, dist: float) -> Point:
    """Point `dist` from `frm` along the segment toward `toward`."""
    dx, dy = toward[0] - frm[0], toward[1] - frm[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return frm
    t = min(dist / length, 1.0)
    return (frm[0] + dx * t, frm[1] + dy * t)


def _arc_mid(corner: Point, prv: Point, nxt: Point, rc: float) -> Point:
    """Midpoint of the rounded corner: pulled diagonally inward from the corner
    so the three corner points describe a turn rather than a spike."""
    a = _along(corner, prv, rc)
    b = _along(corner, nxt, rc)
    mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    # Pull toward the true arc: the arc's midpoint sits between the chord
    # midpoint and the corner, at (1 - 1/sqrt(2)) of the way for a 90 deg turn.
    k = 1.0 - math.sqrt(0.5)
    return (mx + (corner[0] - mx) * k, my + (corner[1] - my) * k)


def _body_to_enu(forward_m: float, right_m: float, heading_deg: float) -> Point:
    """Body (forward, right) -> world ENU (east, north) for a given compass
    heading. Heading 0 means forward = north, right = east; heading grows
    clockwise, which is the compass/MAVLink convention, NOT the maths one."""
    th = math.radians(heading_deg)
    north = forward_m * math.cos(th) - right_m * math.sin(th)
    east = forward_m * math.sin(th) + right_m * math.cos(th)
    return (east, north)
