"""Balcony scenarios B1-B6, generated from a seed, in the sign frame M.

  B1 typical balcony: 1.2-1.8 m deep, 2.5-4 m wide, solid railing 1.0-1.15 m, slab above.
  B2 glass railing (UWB sees through it; the down range has no reading on glass).
  B3 small balcony: 0.9-1.1 m deep, 1.5-2.0 m wide.
  B4 recessed loggia: side walls, 2.0-2.5 m deep.
  B5 high floor in strong wind: floors 18-28, mean wind 7-11 m/s.
  B6 sign off-centre, 0.4-0.6 m from a side wall/divider.

Every balcony has neighbours left and right and above/below (a stacked facade), so a lateral or
vertical error can hit a divider, the slab above or the railing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

SCENARIOS = ["B1", "B2", "B3", "B4", "B5", "B6"]
STOREY_M = 3.0


@dataclass
class Box:
    lo: np.ndarray
    hi: np.ndarray
    kind: str                      # railing, glass, slab, wall, divider, sign, ground
    glass: bool = False


@dataclass
class Scenario:
    sid: str
    seed: int
    boxes: list
    anchors: np.ndarray            # 3 x 3 sign beacons (M)
    floor_z: float
    railing_y: float
    railing_top_z: float
    slab_under_z: float
    half_width: float
    drop: np.ndarray               # drop mark on the floor (M)
    entry_z: float                 # underside height for crossing the railing
    ground_z: float
    roof_z: float
    hover_pos: np.ndarray          # mothership mean hover position (M)
    wind_mean: np.ndarray          # mean wind at 20 m AGL (M), m/s
    wind_level_label: str
    hover_level: str
    info: dict = field(default_factory=dict)


def _sign_anchors(side):
    h = side * math.sqrt(3) / 2
    pts = np.array([[-side / 2, 0.0, -h / 3], [side / 2, 0.0, -h / 3], [0.0, 0.0, 2 * h / 3]])
    return pts                      # centroid at the origin


def make(sid, seed, cfg, sign_side=None, b6_offset_m=(0.4, 0.6)):
    bm = cfg["balcony_mission"]
    rng = np.random.default_rng([seed, SCENARIOS.index(sid) + 501])
    side = sign_side or bm["sign"]["triangle_side_m"]
    hc = bm["sign"]["centre_height_above_floor_m"]
    floor_z = -hc                                   # the sign centroid is at z = 0
    floors = (18, 28) if sid == "B5" else (2, 12)
    level = int(rng.integers(floors[0], floors[1] + 1))
    ground_z = floor_z - level * STOREY_M
    n_floors_above = int(rng.integers(2, 8))
    roof_z = floor_z + (n_floors_above + 1) * STOREY_M
    depth = {"B3": (0.9, 1.1), "B4": (2.0, 2.5)}.get(sid, (1.2, 1.8))
    width = {"B3": (1.5, 2.0)}.get(sid, (2.5, 4.0))
    d = float(rng.uniform(*depth))
    w = float(rng.uniform(*width))
    rail_h = float(rng.uniform(1.0, 1.15))
    slab_t = 0.2
    ceil = float(rng.uniform(2.6, 2.9))
    # sign position along the balcony (x of the balcony centre relative to the sign)
    if sid == "B6":
        off = w / 2 - float(rng.uniform(*b6_offset_m))
        cx = -off if rng.random() < 0.5 else off
    else:
        cx = float(rng.uniform(-0.2, 0.2)) * w
    x0, x1 = cx - w / 2, cx + w / 2
    boxes = []
    glass = sid == "B2"
    t_r = 0.05
    for k in (-1, 0, 1):                            # this storey, the one below and the one above
        fz = floor_z + k * STOREY_M
        # floor slab (projecting balcony) and railing for this balcony and its two neighbours
        for j in (-1, 0, 1):
            bx0, bx1 = x0 + j * (w + 0.2), x1 + j * (w + 0.2)
            boxes.append(Box(np.array([bx0, 0.0, fz - slab_t]), np.array([bx1, d, fz]), "slab"))
            if sid == "B4":                             # loggia: no projecting railing sides, a parapet front
                boxes.append(Box(np.array([bx0, d - t_r, fz]), np.array([bx1, d, fz + rail_h]), "railing", glass))
            else:
                boxes.append(Box(np.array([bx0, d - t_r, fz]), np.array([bx1, d, fz + rail_h]), "railing", glass))
                boxes.append(Box(np.array([bx0, 0.0, fz]), np.array([bx0 + t_r, d, fz + rail_h]), "railing", glass))
                boxes.append(Box(np.array([bx1 - t_r, 0.0, fz]), np.array([bx1, d, fz + rail_h]), "railing", glass))
            # divider walls between balconies (full height), loggia side walls
            boxes.append(Box(np.array([bx1, 0.0, fz]), np.array([bx1 + 0.2, d if sid == "B4" else 0.8, fz + STOREY_M - slab_t]),
                             "divider"))
    # the facade wall (behind y = 0) over the whole height
    boxes.append(Box(np.array([-60.0, -0.4, ground_z]), np.array([60.0, 0.0, roof_z]), "wall"))
    # the sign itself (5 mm proud of the wall)
    boxes.append(Box(np.array([-side / 2 - 0.05, 0.0, -side * 0.35]), np.array([side / 2 + 0.05, 0.005, side * 0.65]), "sign"))
    boxes.append(Box(np.array([-200.0, -50.0, ground_z - 1.0]), np.array([200.0, 200.0, ground_z]), "ground"))
    # mothership mean hover position and the wind
    st = float(rng.uniform(*bm["hover_standoff_m"]))
    lat = float(rng.uniform(*bm["hover_lateral_m"]))
    hz = max(ground_z + bm["hover_height_agl_m"], roof_z + bm["hover_clear_above_roof_m"])
    if sid == "B5":
        U, lvl = float(rng.uniform(7.0, 11.0)), "strong"
    else:
        U, lvl = float(rng.uniform(1.0, 7.0)), "light-moderate"
    ang = float(rng.uniform(0, 2 * math.pi))
    wind = U * np.array([math.cos(ang), math.sin(ang), 0.0])
    hover_level = "gusty" if U > 8 else ("moderate" if U > 4 else "calm")
    return Scenario(sid=sid, seed=seed, boxes=boxes, anchors=_sign_anchors(side), floor_z=floor_z,
                    railing_y=d, railing_top_z=floor_z + rail_h, slab_under_z=floor_z + ceil, half_width=w / 2,
                    drop=np.array([0.0, bm["sign"]["drop_out_from_wall_m"], floor_z]),
                    entry_z=floor_z + bm["entry_height_above_floor_m"], ground_z=ground_z, roof_z=roof_z,
                    hover_pos=np.array([lat, st, hz]), wind_mean=wind, wind_level_label=lvl, hover_level=hover_level,
                    info=dict(depth_m=d, width_m=w, railing_m=rail_h, ceiling_m=ceil, level=level, sign_side_m=side,
                              balcony_cx_m=cx, standoff_m=st, wind_m_s=U, glass=glass))


def nearest_box(sc, p, r):
    """(distance from a sphere of radius r at p to the nearest box surface, box) -- negative = overlap."""
    best, bb = math.inf, None
    for b in sc.boxes:
        q = np.minimum(np.maximum(p, b.lo), b.hi)
        dist = float(np.linalg.norm(p - q)) - r
        if dist < best:
            best, bb = dist, b
    return best, bb


def aabb_clearance(sc, lo, hi, skip=None):
    """(signed clearance from the box [lo, hi] to the nearest scenario box, that box).
    Negative = overlap (minus the smallest penetration). ``skip(box)`` excludes boxes."""
    best, bb = math.inf, None
    for b in sc.boxes:
        if skip is not None and skip(b):
            continue
        gap = np.maximum(np.maximum(b.lo - hi, lo - b.hi), 0.0)
        if gap.any():
            d = float(np.linalg.norm(gap))
        else:
            d = -float(np.min(np.minimum(hi - b.lo, b.hi - lo)))
        if d < best:
            best, bb = d, b
    return best, bb


def segment_blocked(sc, a, b, skip_glass=True):
    """True if the straight segment a->b crosses a box (glass excluded: UWB passes)."""
    d = b - a
    for bx in sc.boxes:
        if bx.kind in ("sign", "ground") or (skip_glass and bx.glass):
            continue
        t0, t1 = 0.0, 1.0
        ok = True
        for k in range(3):
            if abs(d[k]) < 1e-12:
                if a[k] < bx.lo[k] or a[k] > bx.hi[k]:
                    ok = False
                    break
            else:
                ta, tb = (bx.lo[k] - a[k]) / d[k], (bx.hi[k] - a[k]) / d[k]
                if ta > tb:
                    ta, tb = tb, ta
                t0, t1 = max(t0, ta), min(t1, tb)
                if t0 > t1:
                    ok = False
                    break
        if ok and t1 > 1e-6 and t0 < 1 - 1e-6:
            return True
    return False


def ray_down(sc, p, max_r=8.0):
    """Distance straight down from p to the first box top (glass returns no reading) and its kind."""
    best, kind = math.inf, None
    for b in sc.boxes:
        if b.lo[0] <= p[0] <= b.hi[0] and b.lo[1] <= p[1] <= b.hi[1] and b.hi[2] <= p[2]:
            dz = p[2] - b.hi[2]
            if dz < best:
                best, kind = dz, b
    if kind is not None and kind.glass:
        return math.nan, kind
    return (best if best <= max_r else math.nan), kind
