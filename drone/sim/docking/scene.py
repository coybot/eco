"""Street scene: two rows of apartment buildings, balcony pads, the mothership's route.

World frame ENU. The street runs along +x; building faces are at y = +/- half_width_m.
Buildings are seeded boxes (team_world.Box compatible, so the existing ray casting and
collision code can use them). Each balcony pad is a thin slab sticking out of a
street-facing facade at a floor level; its top surface is where a quadcopter lands.

The mothership route is a racetrack above the rooftops: a straight leg along the street
centreline (east), a turn, a parallel return leg (west), a turn back. Turn radius is set
from the cruise airspeed and a bank limit. Altitude = tallest roof + clearance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

try:
    from team_world import Box
except ImportError:  # pragma: no cover
    Box = None


@dataclass
class Pad:
    pad_id: str
    center_top_m: np.ndarray        # centre of the top surface (landing point)
    size_m: tuple                   # (along street x, protrusion y, thickness z)
    facade_normal: np.ndarray       # unit vector out of the facade, into the street

    def contains_xy(self, p: np.ndarray, margin_m: float = 0.0) -> bool:
        hx = self.size_m[0] / 2 + margin_m
        hy = self.size_m[1] / 2 + margin_m
        return abs(p[0] - self.center_top_m[0]) <= hx and abs(p[1] - self.center_top_m[1]) <= hy


@dataclass
class Building:
    center_m: np.ndarray
    half_m: np.ndarray

    @property
    def height_m(self) -> float:
        return float(self.center_m[2] + self.half_m[2])


@dataclass
class Route:
    altitude_m: float
    leg_start_x_m: float
    leg_end_x_m: float
    turn_radius_m: float
    leg_y_m: float = 0.0
    direction: int = -1     # turn direction after the east leg: -1 = right turn (clockwise)

    @property
    def return_y_m(self) -> float:
        return self.leg_y_m + self.direction * 2 * self.turn_radius_m

    def perimeter_m(self) -> float:
        return 2 * (self.leg_end_x_m - self.leg_start_x_m) + 2 * math.pi * self.turn_radius_m


@dataclass
class StreetScene:
    buildings: list = field(default_factory=list)
    pads: list = field(default_factory=list)
    half_width_m: float = 10.0
    route: Route | None = None

    def boxes(self):
        out = [Box(b.center_m.astype(np.float32), b.half_m.astype(np.float32)) for b in self.buildings]
        for p in self.pads:
            c = p.center_top_m - np.array([0, 0, p.size_m[2] / 2])
            out.append(Box(c.astype(np.float32), (np.array(p.size_m) / 2).astype(np.float32)))
        return out

    def max_roof_m(self) -> float:
        return max(b.height_m for b in self.buildings)

    def pad(self, pad_id: str) -> Pad:
        return next(p for p in self.pads if p.pad_id == pad_id)

    def sphere_hits_building(self, p: np.ndarray, r_m: float, ignore_pad: Pad | None = None) -> bool:
        for b in self.buildings:
            d = np.maximum(np.abs(p - b.center_m) - b.half_m, 0.0)
            if float(d @ d) < r_m * r_m:
                return True
        for pd in self.pads:
            if pd is ignore_pad:
                continue
            c = pd.center_top_m - np.array([0, 0, pd.size_m[2] / 2])
            d = np.maximum(np.abs(p - c) - np.array(pd.size_m) / 2, 0.0)
            if float(d @ d) < r_m * r_m:
                return True
        return False


def make_street(seed: int = 7, n_per_side: int = 6, half_width_m: float = 10.0,
                floor_height_m: float = 3.0, floors_range=(5, 10), width_range_m=(16.0, 24.0),
                gap_range_m=(3.0, 6.0), depth_m: float = 15.0, pads_per_building: int = 2,
                pad_size_m=(1.2, 0.9, 0.12), route_clearance_m: float = 15.0,
                cruise_airspeed_m_s: float = 23.0, max_bank_deg: float = 25.0) -> StreetScene:
    rng = np.random.default_rng(seed)
    sc = StreetScene(half_width_m=half_width_m)
    for side in (+1, -1):
        x = 0.0
        for i in range(n_per_side):
            w = float(rng.uniform(*width_range_m))
            floors = int(rng.integers(floors_range[0], floors_range[1] + 1))
            h = floors * floor_height_m
            cy = side * (half_width_m + depth_m / 2)
            b = Building(np.array([x + w / 2, cy, h / 2]), np.array([w / 2, depth_m / 2, h / 2]))
            sc.buildings.append(b)
            # pads on the street-facing facade, on distinct floors 2 .. top-1
            fl = rng.choice(np.arange(2, floors), size=min(pads_per_building, floors - 2), replace=False)
            for j, f in enumerate(sorted(fl)):
                px = x + w * (j + 1) / (pads_per_building + 1)
                face_y = side * half_width_m
                pz = f * floor_height_m + 0.0          # slab top at the floor level
                center_top = np.array([px, face_y - side * pad_size_m[1] / 2, pz])
                sc.pads.append(Pad(f"{'N' if side > 0 else 'S'}{i}_{j}", center_top, tuple(pad_size_m),
                                   np.array([0.0, -side, 0.0])))
            x += w + float(rng.uniform(*gap_range_m))
    street_len = max(b.center_m[0] + b.half_m[0] for b in sc.buildings)
    R = cruise_airspeed_m_s ** 2 / (9.80665 * math.tan(math.radians(max_bank_deg)))
    R = 10.0 * math.ceil(R / 10.0)
    sc.route = Route(altitude_m=10.0 * math.ceil((sc.max_roof_m() + route_clearance_m) / 10.0),
                     leg_start_x_m=-150.0, leg_end_x_m=street_len + 150.0, turn_radius_m=R)
    return sc


class RouteFollower:
    """Path manager for the racetrack: returns the active segment for a position.

    Legs: 0 east leg (line, course 0), 1 turn (orbit), 2 west leg (line, course pi),
    3 turn. Switching uses half-plane tests at the leg ends, so it is robust to overshoot.
    """

    def __init__(self, route: Route):
        self.r = route
        self.leg = 0
        self.laps = 0
        self.d = route.direction

    def update(self, pos_m: np.ndarray):
        r = self.r
        if self.leg == 0 and pos_m[0] >= r.leg_end_x_m:
            self.leg = 1
        elif self.leg == 1 and abs(pos_m[1] - r.return_y_m) < abs(pos_m[1] - r.leg_y_m) and pos_m[0] <= r.leg_end_x_m:
            self.leg = 2
        elif self.leg == 2 and pos_m[0] <= r.leg_start_x_m:
            self.leg = 3
        elif self.leg == 3 and abs(pos_m[1] - r.leg_y_m) < abs(pos_m[1] - r.return_y_m) and pos_m[0] >= r.leg_start_x_m:
            self.leg = 0
            self.laps += 1
        ym = (r.leg_y_m + r.return_y_m) / 2
        if self.leg == 0:
            return ("line", np.array([r.leg_start_x_m, r.leg_y_m]), 0.0)
        if self.leg == 2:
            return ("line", np.array([r.leg_end_x_m, r.return_y_m]), math.pi)
        # both turns go the same way round (direction -1 = clockwise = right turns)
        if self.leg == 1:
            return ("orbit", np.array([r.leg_end_x_m, ym]), r.turn_radius_m, self.d)
        return ("orbit", np.array([r.leg_start_x_m, ym]), r.turn_radius_m, self.d)
