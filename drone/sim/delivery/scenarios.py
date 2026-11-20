"""Seeded balcony scenarios S1-S9 (addendum section 2).

Frame: the BEACON FRAME. Origin at the balcony beacon, axes east-north-up. The target
facade is the plane y = y_facade facing -y (the street is at negative y). Everything is
generated in a building frame, then shifted so the beacon is at the origin.

Every scenario returns a ``Scenario`` with the geometry, the delivery points (pad,
railing crossing, entry point, release/pickup point, roof fallback), the wind, the sun,
time-varying parts (a walking person, one change between entry and exit) and the
ground-truth label ``corridor_3m_bool``: does a clear corridor 3 m wide exist from the
release point to the balcony? Wrong give-ups are scored against it.

Label definition (computed from ground truth, thin and invisible things included):
  (a) free-space cells with horizontal clearance >= 1.5 m over a vertical band of
      +/-0.3 m (a 3 m wide, 0.6 m tall passage, vehicle centred) connect the release
      point to the entry point outside the railing, and
  (b) the opening from the entry point to above the pad, 3 m wide, at crossing height
      +/-0.3 m, is free.

Dimensions are randomised per seed within the ranges in each generator; the ranges are
estimates for European/US mid-rise streets.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import geom as G

SCENARIOS = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"]


@dataclass
class Scenario:
    sid: str
    seed: int
    scene: G.Scene
    pad_m: np.ndarray               # landing / drop point on the balcony floor
    beacon_railing_m: np.ndarray    # beacon position if mounted on the railing
    beacon_wall_m: np.ndarray       # ... if mounted on the back wall
    railing_y_m: float              # outer face of the railing (or glass line for a recess)
    railing_top_z_m: float
    floor_z_m: float
    balcony_x_m: tuple              # x extent of the balcony opening
    balcony_y_m: tuple              # y extent (outer edge, back wall)
    release_m: np.ndarray           # release point = pickup point (open sky above the roofline)
    roof_fallback_m: np.ndarray     # flat clear roof area
    roof_z_max_m: float
    window_lo_m: np.ndarray         # map/plan window (beacon frame)
    window_hi_m: np.ndarray
    facades: list                   # (point, outward normal, height_m) for the wind model
    street_width_m: float
    sun_bool: bool
    sun_dir: np.ndarray
    wind_mean_m_s: np.ndarray
    gust_scale_nd: float
    person: dict | None = None      # walking person on the target balcony
    change: dict | None = None      # one thing that changes between entry and exit
    corridor_3m_bool: bool = True
    info: dict = field(default_factory=dict)

    def beacon_m(self, mount: str) -> np.ndarray:
        return self.beacon_railing_m if mount == "railing" else self.beacon_wall_m


class _B:
    """Scene builder in the building frame (facade at y = 0, street at y < 0, ground z = 0)."""

    def __init__(self, rng):
        self.s = G.Scene()
        self.rng = rng
        self.s.box([-200, -200, -1.0], [200, 200, 0.0], G.CONCRETE, G.K_GROUND, name="ground")
        self.facades = []
        self.roofs = []

    def u(self, a, b):
        return float(self.rng.uniform(a, b))

    def building(self, x0, x1, y0, y1, h, facade_normal=None):
        self.s.box([x0, y0, 0], [x1, y1, h], G.CONCRETE, G.K_STRUCTURE)
        self.roofs.append(h)
        if facade_normal is not None:
            n = np.asarray(facade_normal, float)
            p = np.array([(x0 + x1) / 2, y0 if n[1] < 0 else y1, h / 2])
            self.facades.append((p, n, h))

    def balcony(self, xc, w, depth, zf, railing="metal", name_prefix=None):
        """Projecting balcony on the facade y = 0: slab, railing on three sides."""
        s = self.s
        s.box([xc - w / 2, -depth, zf - 0.2], [xc + w / 2, 0.0, zf], G.CONCRETE, G.K_STRUCTURE,
              name=(name_prefix + "_slab") if name_prefix else None)
        mat = {"metal": G.METAL, "glass": G.GLASS, "solid": G.CONCRETE}[railing]
        kind = G.K_GLASS if railing == "glass" else G.K_RAILING
        t = 0.04
        s.box([xc - w / 2, -depth, zf], [xc + w / 2, -depth + t, zf + 1.05], mat, kind)
        s.box([xc - w / 2, -depth, zf], [xc - w / 2 + t, 0.0, zf + 1.05], mat, kind)
        s.box([xc + w / 2 - t, -depth, zf], [xc + w / 2, 0.0, zf + 1.05], mat, kind)

    def wire(self, p0, p1, diam, metal, sag=0.0, nseg=4):
        p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
        pts = []
        for i in range(nseg + 1):
            f = i / nseg
            p = p0 + (p1 - p0) * f
            p[2] -= sag * 4 * f * (1 - f)
            pts.append(p)
        for a, b in zip(pts[:-1], pts[1:]):
            self.s.capsule(a, b, diam / 2, G.WIRE_METAL if metal else G.WIRE_NONMETAL,
                           G.K_WIRE_METAL if metal else G.K_WIRE_NONMETAL)

    def tree(self, x, y, crown_d, top_z, trunk_h=None):
        r = crown_d / 2
        cz = top_z - r
        trunk_h = trunk_h if trunk_h is not None else max(1.5, cz - r * 0.6)
        self.s.capsule([x, y, 0], [x, y, cz], 0.15, G.WOOD, G.K_TREE)
        # crown: a core sphere plus clumps
        self.s.sphere([x, y, cz], r * 0.75, G.FOLIAGE, G.K_TREE)
        for _ in range(int(self.rng.integers(5, 9))):
            v = self.rng.normal(size=3)
            v /= np.linalg.norm(v)
            self.s.sphere([x + v[0] * r * 0.55, y + v[1] * r * 0.55, cz + v[2] * r * 0.45],
                          r * self.u(0.35, 0.5), G.FOLIAGE, G.K_TREE)
        return np.array([x, y, cz]), r


def _floor_h(rng):
    return float(rng.uniform(2.8, 3.3))


def _base(sid, seed):
    rng = np.random.default_rng([seed, SCENARIOS.index(sid) + 101])
    return rng, _B(rng)


def _finish(b: _B, sid, seed, *, bx, bw, bd, zf, recess=False, street_w, release_xy=None,
            roof_xy=None, rng, person=None, change=None, gust_scale=1.0, wind_range=(0.0, 7.0),
            extra=None):
    """Common tail: points, window, sun, wind, shift to the beacon frame, label."""
    s = b.s
    if recess:
        rail_y, wall_y = 0.0, bd
        pad = np.array([bx, bd / 2, zf])
    else:
        rail_y, wall_y = -bd, 0.0
        pad = np.array([bx, -bd / 2, zf])
    rail_top = zf + 1.05
    beacon_r = np.array([bx, rail_y + 0.03, rail_top])
    beacon_w = np.array([bx, wall_y - 0.05, rail_top])
    roof_max = max(b.roofs)
    if release_xy is None:
        release_xy = (bx + b.u(-15, 15), -street_w / 2)
    release = np.array([release_xy[0], release_xy[1], roof_max + 6.0])
    if roof_xy is None:
        roof_xy = (bx, 6.0)
    roof_fb = np.array([roof_xy[0], roof_xy[1], _roof_height_at(b, roof_xy) + 0.3])
    lo = np.array([min(bx, release[0]) - 9.0, -street_w - 0.5, max(0.2, zf - 4.0)])
    hi = np.array([max(bx, release[0]) + 9.0, max(wall_y, 0.0) + 4.0, release[2] + 3.0])
    sun = bool(rng.random() < 0.5)
    el, az = math.radians(b.u(20, 60)), b.u(0, 2 * math.pi)
    sun_dir = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    U = b.u(*wind_range)
    wd = b.u(0, 2 * math.pi)
    wind = np.array([U * math.cos(wd), U * math.sin(wd), 0.0])
    s.freeze()
    # shift to the beacon frame (railing beacon as origin for both mounts: the frame is fixed
    # by the railing beacon; a wall beacon simply sits at a different known point)
    off = beacon_r.copy()
    s.blo -= off
    s.bhi -= off
    s.c0 -= off
    s.c1 -= off
    s.sc -= off
    sh = lambda p: np.asarray(p, float) - off
    facades = [(p - off, n, h) for (p, n, h) in b.facades]
    if person is not None:
        person = dict(person)
        person["x0_m"] -= off[0]
        person["y_m"] -= off[1]
        person["z_m"] -= off[2]
    sc = Scenario(sid=sid, seed=seed, scene=s, pad_m=sh(pad), beacon_railing_m=sh(beacon_r),
                  beacon_wall_m=sh(beacon_w), railing_y_m=rail_y - off[1], railing_top_z_m=rail_top - off[2],
                  floor_z_m=zf - off[2], balcony_x_m=(bx - bw / 2 - off[0], bx + bw / 2 - off[0]),
                  balcony_y_m=(rail_y - off[1], wall_y - off[1]), release_m=sh(release),
                  roof_fallback_m=sh(roof_fb), roof_z_max_m=roof_max - off[2], window_lo_m=sh(lo),
                  window_hi_m=sh(hi), facades=facades, street_width_m=street_w, sun_bool=sun,
                  sun_dir=sun_dir, wind_mean_m_s=wind, gust_scale_nd=gust_scale, person=person,
                  change=change, info=dict(extra or {}, recess_bool=recess, balcony_width_m=bw,
                                           balcony_depth_m=bd, floor_z_world_m=zf, ground_z_m=-off[2]))
    sc.corridor_3m_bool = corridor_label(sc)
    return sc


def _roof_height_at(b: _B, xy):
    s = b.s
    best = 0.0
    for lo, hi, mat, kind, act in s._b:
        if kind == G.K_STRUCTURE and lo[0] <= xy[0] <= hi[0] and lo[1] <= xy[1] <= hi[1]:
            best = max(best, hi[2])
    return best


# ------------------------------------------------------------------------- generators

def s1(seed):
    """Open balcony visible from above: top floor, nothing above it, wide street."""
    rng, b = _base("S1", seed)
    fh = _floor_h(rng)
    n = int(rng.integers(3, 8))
    H = n * fh
    W, D = b.u(20, 40), b.u(10, 18)
    b.building(-W / 2, W / 2, 0, D, H, facade_normal=[0, -1, 0])
    street_w = b.u(18, 32)
    if rng.random() < 0.5:
        b.building(-W / 2 - 5, W / 2 + 5, -street_w - 12, -street_w, b.u(6, 25), facade_normal=[0, 1, 0])
    bw, bd = b.u(3.0, 5.0), b.u(1.2, 2.0)
    zf = (n - 1) * fh
    b.balcony(0.0, bw, bd, zf, railing=rng.choice(["metal", "solid", "glass"]))
    return _finish(b, "S1", seed, bx=0.0, bw=bw, bd=bd, zf=zf, street_w=street_w, rng=rng)


def s2(seed, b=None, rng=None, finish=True):
    """Stacked balconies hidden from above, street 25 m, building opposite."""
    if b is None:
        rng, b = _base("S2", seed)
    fh = _floor_h(rng)
    n = int(rng.integers(5, 10))
    H = n * fh
    W, D = b.u(24, 44), b.u(10, 18)
    b.building(-W / 2, W / 2, 0, D, H, facade_normal=[0, -1, 0])
    street_w = 25.0
    b.building(-W / 2 - 10, W / 2 + 10, -street_w - 14, -street_w, b.u(12, 30), facade_normal=[0, 1, 0])
    bw, bd = b.u(3.0, 4.5), b.u(1.2, 1.8)
    gap = b.u(1.5, 4.0)
    k = int(rng.integers(1, n - 2))
    railing = rng.choice(["metal", "solid", "glass"])
    for col in (-1, 0, 1):
        xc = col * (bw + gap)
        for f in range(1, n):
            b.balcony(xc, bw, bd, f * fh, railing=railing,
                      name_prefix=("target" if (col == 0 and f == k) else None))
    out = dict(bx=0.0, bw=bw, bd=bd, zf=k * fh, street_w=street_w, fh=fh, n=n, H=H, k=k, W=W)
    if not finish:
        return out
    return _finish(b, "S2", seed, bx=0.0, bw=bw, bd=bd, zf=k * fh, street_w=street_w, rng=rng)


def _eave(b, bx, bw, bd, zf, fh, rng):
    reach = b.u(1.0, 2.0)
    z0 = zf + min(fh - 0.25, b.u(2.3, 2.7))
    fabric = rng.random() < 0.5
    b.s.box([bx - bw / 2 - 0.3, -bd - reach, z0], [bx + bw / 2 + 0.3, 0.0, z0 + (0.03 if fabric else 0.2)],
            G.FABRIC if fabric else G.CONCRETE, G.K_AWNING if fabric else G.K_EAVE)
    return reach, fabric


def s3(seed):
    """Eave or shade reaching 1-2 m past the railing."""
    rng, b = _base("S3", seed)
    fh = _floor_h(rng)
    n = int(rng.integers(3, 8))
    W, D = b.u(20, 40), b.u(10, 18)
    b.building(-W / 2, W / 2, 0, D, n * fh, facade_normal=[0, -1, 0])
    street_w = b.u(18, 30)
    bw, bd = b.u(3.0, 5.0), b.u(1.2, 2.0)
    k = int(rng.integers(1, n))
    zf = k * fh
    b.balcony(0.0, bw, bd, zf, railing=rng.choice(["metal", "solid", "glass"]))
    reach, fabric = _eave(b, 0.0, bw, bd, zf, fh, rng)
    return _finish(b, "S3", seed, bx=0.0, bw=bw, bd=bd, zf=zf, street_w=street_w, rng=rng,
                   extra=dict(eave_reach_m=reach, eave_fabric_bool=fabric))


def _wires(b, rng, zf, bx, street_w, count, along=True):
    """Across (parallel to the facade) and along (toward the facade) the approach."""
    out = []
    for i in range(count):
        d = float(rng.choice([0.003, 0.005, 0.008, 0.012, 0.020]))
        metal = bool(rng.random() < 0.55)
        z = zf + b.u(-3.0, 6.0)
        if not along or rng.random() < 0.6:          # across the street front
            y = b.u(-street_w + 1.5, -1.0)
            b.wire([bx - 40, y, z], [bx + 40, y, z], d, metal, sag=b.u(0.1, 0.8))
            out.append(("across", d, metal, z))
        else:                                        # along the approach, anchored on the facade
            x = bx + b.u(-4.0, 4.0)
            b.wire([x, -0.05, z], [x + b.u(-3, 3), -street_w + 0.5, z + b.u(-1.5, 1.5)], d, metal, sag=b.u(0.1, 0.6))
            out.append(("along", d, metal, z))
    return out


def s4(seed):
    """Wires across and along the approach, several heights, 3-20 mm, metal and non-metal."""
    rng, b = _base("S4", seed)
    fh = _floor_h(rng)
    n = int(rng.integers(3, 8))
    W, D = b.u(20, 40), b.u(10, 18)
    b.building(-W / 2, W / 2, 0, D, n * fh, facade_normal=[0, -1, 0])
    street_w = b.u(15, 25)
    b.building(-W / 2 - 5, W / 2 + 5, -street_w - 12, -street_w, b.u(8, 25), facade_normal=[0, 1, 0])
    bw, bd = b.u(3.0, 5.0), b.u(1.2, 2.0)
    k = int(rng.integers(1, n))
    zf = k * fh
    b.balcony(0.0, bw, bd, zf, railing=rng.choice(["metal", "solid", "glass"]))
    wires = _wires(b, rng, zf, 0.0, street_w, int(rng.integers(3, 9)))
    return _finish(b, "S4", seed, bx=0.0, bw=bw, bd=bd, zf=zf, street_w=street_w, rng=rng,
                   extra=dict(wires_count=len(wires),
                              wires_metal_count=sum(1 for w in wires if w[2])))


def _trees(b, rng, bx, zf, street_w, branch_near=True, bd=1.5):
    xs = bx + b.u(-6, 6) + np.arange(-3, 4) * b.u(10, 14)
    near = []
    for x in xs:
        cd, top = b.u(3, 6), b.u(8, 14)
        c, r = b.tree(float(x), b.u(-5.0, -3.5), cd, top)
        near.append((abs(x - bx), c, r))
    if branch_near:
        near.sort(key=lambda t: t[0])
        _, c, r = near[0]
        for _ in range(int(rng.integers(1, 4))):
            tgt = np.array([bx + b.u(-3.0, 3.0), -bd - b.u(0.8, 3.5), zf + b.u(-0.5, 2.5)])
            d = tgt - c
            dist = np.linalg.norm(d)
            p0 = c + d / dist * r * 0.5
            b.s.capsule(p0, tgt, b.u(0.02, 0.05), G.WOOD, G.K_TREE)
            b.s.sphere(tgt, b.u(0.2, 0.5), G.FOLIAGE, G.K_TREE)


def s5(seed):
    """Trees in front of the lower floors, branches near the balcony."""
    rng, b = _base("S5", seed)
    fh = _floor_h(rng)
    n = int(rng.integers(4, 8))
    W, D = b.u(24, 40), b.u(10, 18)
    b.building(-W / 2, W / 2, 0, D, n * fh, facade_normal=[0, -1, 0])
    street_w = b.u(16, 26)
    bw, bd = b.u(3.0, 5.0), b.u(1.2, 2.0)
    k = int(rng.integers(1, 4))
    zf = k * fh
    b.balcony(0.0, bw, bd, zf, railing=rng.choice(["metal", "solid", "glass"]))
    _trees(b, rng, 0.0, zf, street_w, bd=bd)
    return _finish(b, "S5", seed, bx=0.0, bw=bw, bd=bd, zf=zf, street_w=street_w, rng=rng)


def _recessed_building(b, rng, W, D, H, fh, bx, bw, bd, k, n, recess_all=True):
    """Building with recessed balconies (loggias) cut into the facade y = 0."""
    s = b.s
    x0, x1 = bx - bw / 2, bx + bw / 2
    # columns either side of the recess column, full height
    s.box([-W / 2, 0, 0], [x0, D, H], G.CONCRETE, G.K_STRUCTURE)
    s.box([x1, 0, 0], [W / 2, D, H], G.CONCRETE, G.K_STRUCTURE)
    s.box([x0, bd, 0], [x1, D, H], G.CONCRETE, G.K_STRUCTURE)       # behind the recesses
    hc = fh - 0.25
    # slabs between recesses (each floor's floor slab and ceiling)
    z = 0.0
    for f in range(n):
        zf = f * fh
        if f == 0 or not recess_all and f != k:
            s.box([x0, 0, zf], [x1, bd, zf + fh], G.CONCRETE, G.K_STRUCTURE)
        else:
            s.box([x0, 0, zf - 0.25], [x1, bd, zf], G.CONCRETE, G.K_STRUCTURE)
            s.box([x0, 0.0, zf], [x1, 0.02, zf + 1.05], G.GLASS, G.K_GLASS)    # glass railing
    s.box([x0, 0, n * fh - 0.25], [x1, bd, n * fh], G.CONCRETE, G.K_STRUCTURE)
    b.roofs.append(H)
    b.facades.append((np.array([0, 0, H / 2]), np.array([0, -1.0, 0]), H))


def s6(seed, narrow=False):
    """Recessed balcony with walls on three sides and a glass railing."""
    rng, b = _base("S6" if not narrow else "S9", seed)
    fh = _floor_h(rng)
    n = int(rng.integers(3, 8))
    W, D = b.u(20, 40), b.u(10, 18)
    H = n * fh
    bw = b.u(2.0, 2.8) if narrow else b.u(3.2, 5.0)
    bd = b.u(1.5, 2.5)
    k = int(rng.integers(1, n))
    _recessed_building(b, rng, W, D, H, fh, 0.0, bw, bd, k, n)
    street_w = b.u(16, 30)
    return _finish(b, "S9" if narrow else "S6", seed, bx=0.0, bw=bw, bd=bd, zf=k * fh, recess=True,
                   street_w=street_w, rng=rng, extra=dict(variant_label="narrow_recess" if narrow else "recess"))


def s7(seed):
    """Courtyard 12 m wide, closed on four sides."""
    rng, b = _base("S7", seed)
    fh = _floor_h(rng)
    n = int(rng.integers(4, 9))
    H = n * fh
    L = b.u(20, 40)
    cw = 12.0
    D = b.u(10, 15)
    b.building(-L / 2 - D, L / 2 + D, 0, D, H, facade_normal=[0, -1, 0])                # target wing
    b.building(-L / 2 - D, L / 2 + D, -cw - D, -cw, H + b.u(-3, 3), facade_normal=[0, 1, 0])  # opposite wing
    b.building(-L / 2 - D, -L / 2, -cw, 0, H)
    b.building(L / 2, L / 2 + D, -cw, 0, H)
    bw, bd = b.u(3.0, 4.5), b.u(1.0, 1.6)
    k = int(rng.integers(1, n))
    zf = k * fh
    b.balcony(0.0, bw, bd, zf, railing=rng.choice(["metal", "solid", "glass"]))
    for f in range(1, n):                        # neighbours on other floors
        if f != k and rng.random() < 0.5:
            b.balcony(0.0, bw, bd, f * fh, railing="metal")
    rx = b.u(-L / 2 + 4, L / 2 - 4)
    return _finish(b, "S7", seed, bx=0.0, bw=bw, bd=bd, zf=zf, street_w=cw, rng=rng,
                   release_xy=(rx, -cw / 2), roof_xy=(0.0, D / 2))


def s8(seed):
    """S2-S5 together, plus an awning, a walking person, gusts, and one change between
    entry and exit."""
    rng, b = _base("S8", seed)
    g = s2(seed, b=b, rng=rng, finish=False)
    bx, bw, bd, zf, fh, street_w = g["bx"], g["bw"], g["bd"], g["zf"], g["fh"], g["street_w"]
    reach, fabric = _eave(b, bx, bw, bd, zf, fh, rng)                   # S3 (shade past the railing)
    _wires(b, rng, zf, bx, street_w, int(rng.integers(2, 6)))            # S4
    _trees(b, rng, bx, min(zf, 9.0), street_w, bd=bd)                          # S5
    # awning on a neighbouring balcony one floor down
    side = 1 if rng.random() < 0.5 else -1
    xa = bx + side * (bw + b.u(1.5, 4.0))
    b.s.box([xa - bw / 2, -bd - b.u(1.0, 2.0), zf - fh + 2.3], [xa + bw / 2, 0, zf - fh + 2.33], G.FABRIC, G.K_AWNING)
    person = dict(x0_m=bx, y_m=-bd * 0.55, z_m=zf, half_w_m=0.25, height_m=1.7,
                  amp_m=max(0.0, bw / 2 - 0.5), period_s=b.u(10, 25), phase_rad=b.u(0, 2 * math.pi))
    b.s.box([bx - 0.25, -bd * 0.55 - 0.15, zf], [bx + 0.25, -bd * 0.55 + 0.15, zf + 1.7], G.PERSON, G.K_PERSON,
            name="person")
    kind = str(rng.choice(["laundry_line", "awning_extends", "parasol"]))
    if kind == "laundry_line":                  # a non-metal line appears across the balcony front
        z = zf + b.u(1.4, 2.1)
        y = -bd - b.u(0.3, 1.5)
        b.wire([bx - bw / 2 - 1, y, z], [bx + bw / 2 + 1, y, z], 0.004, False, sag=0.1)
        names = []
        nb = len(b.s._c)
        for i in range(4):
            b.s._c[nb - 4 + i] = b.s._c[nb - 4 + i][:5] + (False,)
            names.append(("cap", nb - 4 + i))
    elif kind == "awning_extends":
        i = b.s.box([bx - bw / 2 - 0.3, -bd - b.u(1.5, 2.5), zf + 2.0], [bx + bw / 2 + 0.3, -bd, zf + 2.03],
                    G.FABRIC, G.K_AWNING, active=False)
        names = [("box", i)]
    else:
        i = b.s.box([bx - 0.9, -bd * 0.5 - 0.9, zf + 1.9], [bx + 0.9, -bd * 0.5 + 0.9, zf + 2.0],
                    G.FABRIC, G.K_AWNING, active=False)
        names = [("box", i)]
    change = dict(kind_label=kind, items=names)
    return _finish(b, "S8", seed, bx=bx, bw=bw, bd=bd, zf=zf, street_w=street_w, rng=rng, person=person,
                   change=change, gust_scale=2.0, wind_range=(3.0, 9.0),
                   extra=dict(eave_reach_m=reach, change_label=kind))


def s9(seed):
    """Unserviceable: netting across the balcony, or no corridor 3 m wide."""
    rng = np.random.default_rng([seed, 909])
    v = rng.random()
    if v < 0.5:                                                        # netting
        rng2, b = _base("S9", seed)
        fh = _floor_h(rng2)
        n = int(rng2.integers(4, 9))
        W, D = b.u(20, 40), b.u(10, 18)
        b.building(-W / 2, W / 2, 0, D, n * fh, facade_normal=[0, -1, 0])
        street_w = b.u(16, 28)
        bw, bd = b.u(3.0, 5.0), b.u(1.2, 2.0)
        k = int(rng2.integers(1, n - 1))
        for f in range(1, n):
            b.balcony(0.0, bw, bd, f * fh, railing="metal")
        zf = k * fh
        b.s.box([-bw / 2, -bd - 0.02, zf + 1.05], [bw / 2, -bd, zf + fh - 0.2], G.NET, G.K_NETTING)
        b.s.box([-bw / 2 - 0.02, -bd, zf + 1.05], [-bw / 2, 0, zf + fh - 0.2], G.NET, G.K_NETTING)
        b.s.box([bw / 2, -bd, zf + 1.05], [bw / 2 + 0.02, 0, zf + fh - 0.2], G.NET, G.K_NETTING)
        return _finish(b, "S9", seed, bx=0.0, bw=bw, bd=bd, zf=zf, street_w=street_w, rng=rng2,
                       extra=dict(variant_label="netting"))
    if v < 0.8:
        return s6(seed, narrow=True)
    # light well: balcony on a 2.6-2.9 m wide gap between two wings
    rng2, b = _base("S9", seed + 7777)
    fh = _floor_h(rng2)
    n = int(rng2.integers(4, 9))
    H = n * fh
    gap = b.u(2.6, 2.9)
    D = b.u(10, 15)
    b.building(-15, 15, 0, D, H, facade_normal=[0, -1, 0])
    b.building(-15, 15, -gap - D, -gap, H + b.u(0, 6), facade_normal=[0, 1, 0])
    b.building(-20, -15, -gap - D, D, H)
    bw, bd = 1.8, 0.6
    k = int(rng2.integers(1, n - 1))
    zf = k * fh
    b.balcony(0.0, bw, bd, zf, railing="metal")
    return _finish(b, "S9", seed, bx=0.0, bw=bw, bd=bd, zf=zf, street_w=gap, rng=rng2,
                   release_xy=(b.u(-8, 8), -gap / 2), extra=dict(variant_label="light_well"))


GENERATORS = {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "S5": s5, "S6": s6, "S7": s7, "S8": s8, "S9": s9}


def make(sid: str, seed: int) -> Scenario:
    return GENERATORS[sid](seed)


# ------------------------------------------------------------------------- corridor label

def corridor_label(sc: Scenario, cell_m: float = 0.2, return_detail: bool = False):
    from scipy import ndimage

    lo = sc.window_lo_m.copy()
    hi = sc.window_hi_m.copy()
    shape = np.ceil((hi - lo) / cell_m).astype(np.int64)
    s = sc.scene
    pers = s.labels.get("person")
    if pers is not None:                       # a walking person is not part of the corridor
        s.bact[pers[1]] = False
    occ = G.rasterize(lo, cell_m, shape, s.blo, s.bhi, s.bact, s.c0, s.c1, s.crad, s.cact, s.sc, s.sr, s.sact, False)
    # walls of the window count as free (open sky continues), so pad with free space
    if pers is not None:
        s.bact[pers[1]] = True
    dist = ndimage.distance_transform_edt(occ == 0, sampling=(cell_m, cell_m, cell_m * 5.0))
    ok = dist >= 1.5
    lab, _ = ndimage.label(ok, structure=np.ones((3, 3, 3)))
    idx = lambda p: tuple(np.clip(np.floor((p - lo) / cell_m).astype(int), 0, shape - 1))
    a = lab[idx(sc.release_m)]
    rail_dir = -1.0 if not sc.info["recess_bool"] else -1.0          # street side is -y in both
    cross_z = sc.railing_top_z_m + 0.6
    entry = np.array([sc.pad_m[0], sc.railing_y_m + rail_dir * 1.6, cross_z])
    e = lab[idx(entry)]
    conn = bool(a > 0 and a == e)
    # (b) opening: 3 m wide, crossing height +/-0.3 m, entry to above the pad
    xs = (sc.pad_m[0] - 1.5, sc.pad_m[0] + 1.5)
    ys = (min(entry[1], sc.pad_m[1]), max(entry[1], sc.pad_m[1]))
    zs = (cross_z - 0.3, cross_z + 0.3)
    i0, i1 = idx(np.array([xs[0], ys[0], zs[0]])), idx(np.array([xs[1], ys[1], zs[1]]))
    opening = bool(occ[i0[0]:i1[0] + 1, i0[1]:i1[1] + 1, i0[2]:i1[2] + 1].sum() == 0)
    if return_detail:
        return conn and opening, dict(connected_bool=conn, opening_clear_bool=opening)
    return conn and opening
