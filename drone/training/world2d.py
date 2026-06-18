"""Tiny 2D obstacle world + analytic depth-fan ray-casting for BC data generation.

Obstacles are axis-aligned boxes (AABBs) in the world XY plane, treated as floor-to-ceiling
columns — the forward depth fan only senses horizontally, so a quad must route *around* them
in XY (it cannot see over/under with a forward camera). This matches the sim/Orin perception.

`depth_fan` returns the same DEPTH_RAYS-length range vector the policy consumes, computed by
ray-casting from the vehicle pose; the Isaac eval computes the identical vector by sampling the
real depth camera, so training and deployment see the same representation.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

try:
    from .contract import DEPTH_ANGLES, DEPTH_MAX
except ImportError:  # imported as a flat module (eval scripts add training/ to sys.path)
    from contract import DEPTH_ANGLES, DEPTH_MAX


@dataclass
class Box:
    cx: float
    cy: float
    half_x: float
    half_y: float

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return (abs(x - self.cx) <= self.half_x + margin and
                abs(y - self.cy) <= self.half_y + margin)


def _ray_box_2d(ox: float, oy: float, dx: float, dy: float, box: Box) -> float:
    """Distance along unit ray (dx,dy) from (ox,oy) to AABB, or inf if no forward hit."""
    tmin, tmax = -math.inf, math.inf
    for o, d, c, h in ((ox, dx, box.cx, box.half_x), (oy, dy, box.cy, box.half_y)):
        lo, hi = c - h, c + h
        if abs(d) < 1e-9:
            if o < lo or o > hi:
                return math.inf
        else:
            t1 = (lo - o) / d
            t2 = (hi - o) / d
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax:
                return math.inf
    if tmax < 0:
        return math.inf
    return tmin if tmin >= 0 else 0.0


def depth_fan(
    x: float, y: float, yaw: float,
    boxes: list[Box],
    max_range: float = DEPTH_MAX,
    noise_std: float = 0.0,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Cast DEPTH_RAYS rays across the FOV from pose (x,y,yaw); return clipped ranges (m)."""
    out = np.full(len(DEPTH_ANGLES), max_range, dtype=np.float32)
    for i, a in enumerate(DEPTH_ANGLES):
        world_a = yaw + a
        dx, dy = math.cos(world_a), math.sin(world_a)
        best = max_range
        for b in boxes:
            t = _ray_box_2d(x, y, dx, dy, b)
            if t < best:
                best = t
        if noise_std > 0 and rng is not None:
            best = best + float(rng.normal(0.0, noise_std))
        out[i] = min(max(best, 0.0), max_range)
    return out


def min_dist_to_boxes(x: float, y: float, boxes: list[Box]) -> float:
    """Nearest distance from (x,y) to any box surface (0 if inside one)."""
    best = math.inf
    for b in boxes:
        ddx = max(abs(x - b.cx) - b.half_x, 0.0)
        ddy = max(abs(y - b.cy) - b.half_y, 0.0)
        best = min(best, math.hypot(ddx, ddy))
    return best
