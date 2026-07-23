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
    obstacle spacing against (see scenarios/fw_sweep.yaml) — reused here instead
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
