"""3D obstacle world + analytic depth-grid ray-casting for BC data generation.

Obstacles are arbitrary axis-aligned rectangular prisms (3D AABBs) placed at varying altitude,
so the policy must find a path in 3D — over, under, or around. `depth_grid` ray-casts the
DEPTH_RAYS forward directions (a DEPTH_ROWS x DEPTH_COLS camera grid) and returns ranges; the
Isaac eval samples the identical grid from a rendered depth image, so train and deploy match.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

try:
    from .contract import RAY_DIRS, DEPTH_MAX, DEPTH_RAYS
except ImportError:
    from contract import RAY_DIRS, DEPTH_MAX, DEPTH_RAYS


@dataclass
class Box3D:
    cx: float
    cy: float
    cz: float
    hx: float
    hy: float
    hz: float

    @property
    def lo(self):
        return (self.cx - self.hx, self.cy - self.hy, self.cz - self.hz)

    @property
    def hi(self):
        return (self.cx + self.hx, self.cy + self.hy, self.cz + self.hz)


def _ray_box_3d(o, d, box):
    """Distance along unit ray `d` from origin `o` to a 3D AABB, or inf if no forward hit."""
    tmin, tmax = -math.inf, math.inf
    lo, hi = box.lo, box.hi
    for i in range(3):
        if abs(d[i]) < 1e-9:
            if o[i] < lo[i] or o[i] > hi[i]:
                return math.inf
        else:
            t1 = (lo[i] - o[i]) / d[i]
            t2 = (hi[i] - o[i]) / d[i]
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax:
                return math.inf
    if tmax < 0:
        return math.inf
    return tmin if tmin >= 0 else 0.0


_RAY = np.asarray(RAY_DIRS, dtype=np.float64)   # (R,3) body fwd/left/up


def depth_grid(x, y, z, yaw, boxes, max_range=DEPTH_MAX, noise_std=0.0, rng=None):
    """Cast all DEPTH_RAYS from pose (x,y,z,yaw); return clipped ranges (DEPTH_RAYS,) m.

    Vectorized over rays x boxes. Body is level (only yaw rotates the camera); RAY_DIRS are
    body-frame (fwd, left, up) — rotate fwd/left by yaw into world, up stays world-up.
    """
    if not boxes:
        out = np.full(DEPTH_RAYS, max_range, dtype=np.float32)
        if noise_std > 0 and rng is not None:
            out = np.clip(out + rng.normal(0.0, noise_std, DEPTH_RAYS), 0.0, max_range).astype(np.float32)
        return out
    c, s = math.cos(yaw), math.sin(yaw)
    bf, bl, bu = _RAY[:, 0], _RAY[:, 1], _RAY[:, 2]
    d = np.stack([c * bf - s * bl, s * bf + c * bl, bu], axis=1)   # (R,3) world dirs
    o = np.array([x, y, z], dtype=np.float64)
    lo = np.array([b.lo for b in boxes], dtype=np.float64)        # (K,3)
    hi = np.array([b.hi for b in boxes], dtype=np.float64)
    eps = 1e-9
    dd = np.where(np.abs(d) < eps, eps, d)                        # (R,3)
    # t-intervals per axis: (R,1,3) over boxes (1,K,3)
    t1 = (lo[None, :, :] - o[None, None, :]) / dd[:, None, :]     # (R,K,3)
    t2 = (hi[None, :, :] - o[None, None, :]) / dd[:, None, :]
    tmn = np.minimum(t1, t2)
    tmx = np.maximum(t1, t2)
    par = np.abs(d)[:, None, :] < eps
    inside = (o[None, None, :] >= lo[None, :, :]) & (o[None, None, :] <= hi[None, :, :])
    big = 1e9
    tmn = np.where(par, np.where(inside, -big, big), tmn)
    tmx = np.where(par, np.where(inside, big, -big), tmx)
    tmin = tmn.max(axis=2)                                        # (R,K)
    tmax = tmx.min(axis=2)
    hit = (tmax >= tmin) & (tmax >= 0)
    tcand = np.where(tmin >= 0, tmin, 0.0)
    dist = np.where(hit, tcand, max_range).min(axis=1)            # (R,)
    if noise_std > 0 and rng is not None:
        dist = dist + rng.normal(0.0, noise_std, DEPTH_RAYS)
    return np.clip(dist, 0.0, max_range).astype(np.float32)


def window_prisms(cx, cy_open=0.0, cz_open=2.0, ohy=0.8, ohz=0.7, span=8.0, tall=3.0, hx=0.5):
    """4 prisms forming a full-corridor wall at x=cx with a rectangular opening to fly through.

    Opening: y in [cy_open-ohy, cy_open+ohy], z in [cz_open-ohz, cz_open+ohz]. Jambs span the
    whole corridor (+-span) and tall enough that going over/around isn't viable — only the hole.
    """
    yl_hi = cy_open - ohy
    yr_lo = cy_open + ohy
    left = Box3D(cx, (-span + yl_hi) / 2, cz_open, hx, (yl_hi + span) / 2, tall)
    right = Box3D(cx, (yr_lo + span) / 2, cz_open, hx, (span - yr_lo) / 2, tall)
    sill = Box3D(cx, cy_open, (cz_open - ohz) / 2, hx, ohy, (cz_open - ohz) / 2)
    lintel = Box3D(cx, cy_open, cz_open + ohz + tall / 2, hx, ohy, tall / 2)
    return [left, right, sill, lintel]


def min_dist_to_boxes(x, y, z, boxes):
    """Nearest distance from (x,y,z) to any prism surface (0 if inside one)."""
    best = math.inf
    for b in boxes:
        ddx = max(abs(x - b.cx) - b.hx, 0.0)
        ddy = max(abs(y - b.cy) - b.hy, 0.0)
        ddz = max(abs(z - b.cz) - b.hz, 0.0)
        best = min(best, math.sqrt(ddx * ddx + ddy * ddy + ddz * ddz))
    return best
