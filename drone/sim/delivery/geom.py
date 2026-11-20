"""Scene geometry for the delivery scenarios, and the fast kernels that query it.

Three primitive types, each held as flat numpy arrays so the kernels can run compiled:
  boxes     axis-aligned (buildings, slabs, railings, walls, eaves, awnings, glass panels,
            netting, a person);
  capsules  segments with a radius (wires, branches);
  spheres   foliage clumps of a tree crown.
Every primitive carries a MATERIAL (what sensors and radios see) and a KIND (how a
collision is scored), and an ``active`` flag (things that appear or move during a run).

Kernels:
  raycast        first hit per ray with per-material visibility, range limit and
                 detection probability; a material can also be "opaque but unreadable"
                 (glass for the infrared sensors: the ray stops, no reading).
  segment_blocked  line of sight for the ranging radio (blocked by concrete and metal).
  cage_contacts  penetration of the hexacopter's cage (a 0.48 m x 0.20 m cylinder) into
                 each primitive: depth, normal, which primitive.
  rasterize      ground-truth occupancy for the corridor label.
Compiled with numba when it is installed; the same code runs as plain Python otherwise.
"""
from __future__ import annotations

import math

import numpy as np

from ._nb import njit

# materials
CONCRETE, GLASS, METAL, FOLIAGE, FABRIC, NET, WIRE_METAL, WIRE_NONMETAL, PERSON, WOOD = range(10)
N_MAT = 10
MAT_NAMES = ["concrete", "glass", "metal", "foliage", "fabric", "net", "wire_metal", "wire_nonmetal",
             "person", "wood"]
# kinds (collision scoring)
K_STRUCTURE, K_RAILING, K_GLASS, K_EAVE, K_AWNING, K_WIRE_METAL, K_WIRE_NONMETAL, K_TREE, K_PERSON, \
    K_NETTING, K_GROUND = range(11)
KIND_NAMES = ["structure", "railing", "glass", "eave", "awning", "wire_metal", "wire_nonmetal", "tree",
              "person", "netting", "ground"]


class Scene:
    def __init__(self):
        self._b, self._c, self._s = [], [], []
        self.labels = {}           # name -> (type, index)

    def box(self, lo, hi, mat, kind, name=None, active=True):
        self._b.append((np.asarray(lo, float), np.asarray(hi, float), mat, kind, active))
        if name:
            self.labels[name] = ("box", len(self._b) - 1)
        return len(self._b) - 1

    def capsule(self, p0, p1, radius, mat, kind, name=None, active=True):
        self._c.append((np.asarray(p0, float), np.asarray(p1, float), float(radius), mat, kind, active))
        if name:
            self.labels[name] = ("cap", len(self._c) - 1)
        return len(self._c) - 1

    def sphere(self, c, r, mat, kind, name=None, active=True):
        self._s.append((np.asarray(c, float), float(r), mat, kind, active))
        if name:
            self.labels[name] = ("sph", len(self._s) - 1)
        return len(self._s) - 1

    def freeze(self):
        b, c, s = self._b, self._c, self._s
        self.blo = np.array([x[0] for x in b]).reshape(-1, 3)
        self.bhi = np.array([x[1] for x in b]).reshape(-1, 3)
        self.bmat = np.array([x[2] for x in b], np.int64)
        self.bkind = np.array([x[3] for x in b], np.int64)
        self.bact = np.array([x[4] for x in b], np.bool_)
        self.c0 = np.array([x[0] for x in c]).reshape(-1, 3)
        self.c1 = np.array([x[1] for x in c]).reshape(-1, 3)
        self.crad = np.array([x[2] for x in c], float)
        self.cmat = np.array([x[3] for x in c], np.int64)
        self.ckind = np.array([x[4] for x in c], np.int64)
        self.cact = np.array([x[5] for x in c], np.bool_)
        self.sc = np.array([x[0] for x in s]).reshape(-1, 3)
        self.sr = np.array([x[1] for x in s], float)
        self.smat = np.array([x[2] for x in s], np.int64)
        self.skind = np.array([x[3] for x in s], np.int64)
        self.sact = np.array([x[4] for x in s], np.bool_)
        return self

    def arrays(self):
        return (self.blo, self.bhi, self.bmat, self.bact, self.c0, self.c1, self.crad, self.cmat, self.cact,
                self.sc, self.sr, self.smat, self.sact)

    def set_active(self, name, flag):
        t, i = self.labels[name]
        {"box": self.bact, "cap": self.cact, "sph": self.sact}[t][i] = flag

    def move_box(self, name, lo, hi):
        _, i = self.labels[name]
        self.blo[i] = lo
        self.bhi[i] = hi

    def bounds(self):
        lo = [self.blo.min(0)] if len(self.blo) else []
        hi = [self.bhi.max(0)] if len(self.bhi) else []
        return np.min(lo, 0), np.max(hi, 0)


# ---------------------------------------------------------------------------- kernels

@njit(cache=True)
def _ray_box(o, d, lo, hi):
    tmin, tmax = -1e30, 1e30
    for k in range(3):
        if abs(d[k]) < 1e-12:
            if o[k] < lo[k] or o[k] > hi[k]:
                return 1e30
        else:
            t1 = (lo[k] - o[k]) / d[k]
            t2 = (hi[k] - o[k]) / d[k]
            if t1 > t2:
                t1, t2 = t2, t1
            if t1 > tmin:
                tmin = t1
            if t2 < tmax:
                tmax = t2
    if tmax < max(tmin, 0.0):
        return 1e30
    return tmin if tmin > 0.0 else 0.0


@njit(cache=True)
def _ray_sphere(o, d, c, r):
    ox, oy, oz = o[0] - c[0], o[1] - c[1], o[2] - c[2]
    b = ox * d[0] + oy * d[1] + oz * d[2]
    cc = ox * ox + oy * oy + oz * oz - r * r
    disc = b * b - cc
    if disc < 0.0:
        return 1e30
    sq = math.sqrt(disc)
    t = -b - sq
    if t < 0.0:
        t = -b + sq
        if t < 0.0:
            return 1e30
        return 0.0
    return t


@njit(cache=True)
def _ray_capsule(o, d, p0, p1, r):
    # closest approach between the ray and the segment; thin capsules -> hit at the closest point
    u = p1 - p0
    w0 = o - p0
    a = d[0] * d[0] + d[1] * d[1] + d[2] * d[2]
    b = d[0] * u[0] + d[1] * u[1] + d[2] * u[2]
    c = u[0] * u[0] + u[1] * u[1] + u[2] * u[2]
    dd = d[0] * w0[0] + d[1] * w0[1] + d[2] * w0[2]
    e = u[0] * w0[0] + u[1] * w0[1] + u[2] * w0[2]
    den = a * c - b * b
    if den < 1e-12:
        sc, tc = 0.0, e / c if c > 0 else 0.0
    else:
        sc = (b * e - c * dd) / den
        tc = (a * e - b * dd) / den
    if tc < 0.0:
        tc = 0.0
        sc = -dd / a
    elif tc > 1.0:
        tc = 1.0
        sc = (b - dd) / a
    if sc < 0.0:
        return 1e30
    dx = w0[0] + sc * d[0] - tc * u[0]
    dy = w0[1] + sc * d[1] - tc * u[1]
    dz = w0[2] + sc * d[2] - tc * u[2]
    dist2 = dx * dx + dy * dy + dz * dz
    if dist2 > r * r:
        return 1e30
    back = math.sqrt(max(0.0, r * r - dist2))
    t = sc - back
    return t if t > 0.0 else 0.0


@njit(cache=True)
def raycast(orig, dirs, maxr, u, blo, bhi, bmat, bact, c0, c1, crad, cmat, cact, sc, sr, smat, sact,
            mat_mode, mat_maxr, mat_pdet, min_cap_radius):
    """First hit per ray.

    mat_mode[m]: 0 transparent, 1 visible, 2 opaque-unreadable (ray stops, reading invalid).
    mat_maxr[m]: hits farther than this are ignored for material m (as if transparent).
    mat_pdet[m]: a visible hit counts only if u[ray] < pdet (weak reflectors).
    Capsules thinner than min_cap_radius are transparent.
    Returns (range, material, kind_index_type, index); range = 1e30 when nothing.
    """
    n = orig.shape[0]
    rng = np.full(n, 1e30)
    mat = np.full(n, -1, np.int64)
    typ = np.full(n, -1, np.int64)
    idx = np.full(n, -1, np.int64)
    for i in range(n):
        o = orig[i]
        d = dirs[i]
        best = maxr[i]
        for j in range(blo.shape[0]):
            m = bmat[j]
            if not bact[j] or mat_mode[m] == 0:
                continue
            t = _ray_box(o, d, blo[j], bhi[j])
            if t < best and t <= mat_maxr[m] and (mat_mode[m] == 2 or u[i] < mat_pdet[m]):
                best = t
                rng[i] = t
                mat[i] = m
                typ[i] = 0
                idx[i] = j
        for j in range(c0.shape[0]):
            m = cmat[j]
            if not cact[j] or mat_mode[m] == 0 or crad[j] < min_cap_radius:
                continue
            t = _ray_capsule(o, d, c0[j], c1[j], crad[j])
            if t < best and t <= mat_maxr[m] and (mat_mode[m] == 2 or u[i] < mat_pdet[m]):
                best = t
                rng[i] = t
                mat[i] = m
                typ[i] = 1
                idx[i] = j
        for j in range(sc.shape[0]):
            m = smat[j]
            if not sact[j] or mat_mode[m] == 0:
                continue
            t = _ray_sphere(o, d, sc[j], sr[j])
            if t < best and t <= mat_maxr[m] and (mat_mode[m] == 2 or u[i] < mat_pdet[m]):
                best = t
                rng[i] = t
                mat[i] = m
                typ[i] = 2
                idx[i] = j
    return rng, mat, typ, idx


@njit(cache=True)
def segment_blocked(a, b, blo, bhi, bmat, bact, blocking):
    """True if the segment a-b passes through an active box whose material blocks."""
    d = b - a
    L = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
    if L < 1e-9:
        return False
    dn = d / L
    for j in range(blo.shape[0]):
        if not bact[j] or not blocking[bmat[j]]:
            continue
        t = _ray_box(a, dn, blo[j], bhi[j])
        if t < L - 1e-6:
            return True
    return False


@njit(cache=True)
def _support(nb, rad, hh):
    return rad * math.sqrt(nb[0] * nb[0] + nb[1] * nb[1]) + hh * abs(nb[2])


@njit(cache=True)
def cage_contacts(c, R, rad, hh, blo, bhi, bact, c0, c1, crad, cact, sc, sr, sact, out_depth, out_n, out_typ, out_idx):
    """Penetration of a cylinder (radius rad, half-height hh, axis = body z) centred at c.

    For each primitive: q = its closest point to the centre; n = (c - q)/|c - q|;
    depth = support(n) - |c - q| (support of the cylinder along n). Writes up to
    out_depth.size contacts; returns their count."""
    k = 0
    cap = out_depth.shape[0]
    reach = rad + hh + 0.05
    for j in range(blo.shape[0]):
        if not bact[j]:
            continue
        q0 = min(max(c[0], blo[j, 0]), bhi[j, 0])
        q1 = min(max(c[1], blo[j, 1]), bhi[j, 1])
        q2 = min(max(c[2], blo[j, 2]), bhi[j, 2])
        dx, dy, dz = c[0] - q0, c[1] - q1, c[2] - q2
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist > reach:
            continue
        if dist < 1e-6:
            # centre inside the box: push out through the nearest face
            best, ax, sg = 1e30, 0, 1.0
            for a in range(3):
                d1 = c[a] - blo[j, a]
                d2 = bhi[j, a] - c[a]
                if d1 < best:
                    best, ax, sg = d1, a, -1.0
                if d2 < best:
                    best, ax, sg = d2, a, 1.0
            n = np.zeros(3)
            n[ax] = sg
            nb = R.T @ n
            depth = _support(nb, rad, hh) + best
        else:
            n = np.array([dx, dy, dz]) / dist
            nb = R.T @ n
            depth = _support(nb, rad, hh) - dist
        if depth > 0.0 and k < cap:
            out_depth[k] = depth
            out_n[k] = n
            out_typ[k] = 0
            out_idx[k] = j
            k += 1
    for j in range(c0.shape[0]):
        if not cact[j]:
            continue
        u = c1[j] - c0[j]
        uu = u[0] * u[0] + u[1] * u[1] + u[2] * u[2]
        t = 0.0
        if uu > 0:
            t = ((c[0] - c0[j, 0]) * u[0] + (c[1] - c0[j, 1]) * u[1] + (c[2] - c0[j, 2]) * u[2]) / uu
            t = min(max(t, 0.0), 1.0)
        q = c0[j] + t * u
        dv = c - q
        dist = math.sqrt(dv[0] * dv[0] + dv[1] * dv[1] + dv[2] * dv[2])
        if dist > reach + crad[j] or dist < 1e-9:
            continue
        n = dv / dist
        nb = R.T @ n
        depth = _support(nb, rad, hh) + crad[j] - dist
        if depth > 0.0 and k < cap:
            out_depth[k] = depth
            out_n[k] = n
            out_typ[k] = 1
            out_idx[k] = j
            k += 1
    for j in range(sc.shape[0]):
        if not sact[j]:
            continue
        dv = c - sc[j]
        dist = math.sqrt(dv[0] * dv[0] + dv[1] * dv[1] + dv[2] * dv[2])
        if dist > reach + sr[j] or dist < 1e-9:
            continue
        n = dv / dist
        nb = R.T @ n
        depth = _support(nb, rad, hh) + sr[j] - dist
        if depth > 0.0 and k < cap:
            out_depth[k] = depth
            out_n[k] = n
            out_typ[k] = 2
            out_idx[k] = j
            k += 1
    return k


@njit(cache=True)
def nearest_distance(p, blo, bhi, bact, bmat, c0, c1, crad, cact, sc, sr, sact, skip_mat):
    """Distance from p to the nearest active surface (ground truth), ignoring materials in skip_mat."""
    best = 1e30
    for j in range(blo.shape[0]):
        if not bact[j] or skip_mat[bmat[j]]:
            continue
        dx = max(blo[j, 0] - p[0], 0.0, p[0] - bhi[j, 0])
        dy = max(blo[j, 1] - p[1], 0.0, p[1] - bhi[j, 1])
        dz = max(blo[j, 2] - p[2], 0.0, p[2] - bhi[j, 2])
        d = math.sqrt(dx * dx + dy * dy + dz * dz)
        if d < best:
            best = d
    for j in range(c0.shape[0]):
        if not cact[j]:
            continue
        u = c1[j] - c0[j]
        uu = u[0] * u[0] + u[1] * u[1] + u[2] * u[2]
        t = 0.0
        if uu > 0:
            t = ((p[0] - c0[j, 0]) * u[0] + (p[1] - c0[j, 1]) * u[1] + (p[2] - c0[j, 2]) * u[2]) / uu
            t = min(max(t, 0.0), 1.0)
        dv = p - (c0[j] + t * u)
        d = math.sqrt(dv[0] * dv[0] + dv[1] * dv[1] + dv[2] * dv[2]) - crad[j]
        if d < best:
            best = d
    for j in range(sc.shape[0]):
        if not sact[j]:
            continue
        dv = p - sc[j]
        d = math.sqrt(dv[0] * dv[0] + dv[1] * dv[1] + dv[2] * dv[2]) - sr[j]
        if d < best:
            best = d
    return best


@njit(cache=True)
def rasterize(origin, cell, shape, blo, bhi, bact, c0, c1, crad, cact, sc, sr, sact, include_inactive):
    """Ground-truth occupancy (uint8 0/1) on a grid. Thin things fill at least the cells
    they pass through."""
    g = np.zeros((shape[0], shape[1], shape[2]), np.uint8)
    for j in range(blo.shape[0]):
        if not (bact[j] or include_inactive):
            continue
        i0 = max(0, int(math.floor((blo[j, 0] - origin[0]) / cell)))
        i1 = min(shape[0] - 1, int(math.floor((bhi[j, 0] - origin[0]) / cell)))
        j0 = max(0, int(math.floor((blo[j, 1] - origin[1]) / cell)))
        j1 = min(shape[1] - 1, int(math.floor((bhi[j, 1] - origin[1]) / cell)))
        k0 = max(0, int(math.floor((blo[j, 2] - origin[2]) / cell)))
        k1 = min(shape[2] - 1, int(math.floor((bhi[j, 2] - origin[2]) / cell)))
        for a in range(i0, i1 + 1):
            for b in range(j0, j1 + 1):
                for c in range(k0, k1 + 1):
                    g[a, b, c] = 1
    for j in range(c0.shape[0]):
        if not (cact[j] or include_inactive):
            continue
        u = c1[j] - c0[j]
        L = math.sqrt(u[0] * u[0] + u[1] * u[1] + u[2] * u[2])
        ns = max(2, int(L / (0.5 * cell)) + 1)
        rr = int(math.ceil(crad[j] / cell))
        for s in range(ns):
            p = c0[j] + u * (s / (ns - 1))
            ci = int(math.floor((p[0] - origin[0]) / cell))
            cj = int(math.floor((p[1] - origin[1]) / cell))
            ck = int(math.floor((p[2] - origin[2]) / cell))
            for a in range(ci - rr, ci + rr + 1):
                for b in range(cj - rr, cj + rr + 1):
                    for c in range(ck - rr, ck + rr + 1):
                        if 0 <= a < shape[0] and 0 <= b < shape[1] and 0 <= c < shape[2]:
                            g[a, b, c] = 1
    for j in range(sc.shape[0]):
        if not (sact[j] or include_inactive):
            continue
        r = sr[j]
        ci0 = int(math.floor((sc[j, 0] - r - origin[0]) / cell))
        ci1 = int(math.floor((sc[j, 0] + r - origin[0]) / cell))
        cj0 = int(math.floor((sc[j, 1] - r - origin[1]) / cell))
        cj1 = int(math.floor((sc[j, 1] + r - origin[1]) / cell))
        ck0 = int(math.floor((sc[j, 2] - r - origin[2]) / cell))
        ck1 = int(math.floor((sc[j, 2] + r - origin[2]) / cell))
        for a in range(max(0, ci0), min(shape[0], ci1 + 1)):
            for b in range(max(0, cj0), min(shape[1], cj1 + 1)):
                for c in range(max(0, ck0), min(shape[2], ck1 + 1)):
                    x = origin[0] + (a + 0.5) * cell - sc[j, 0]
                    y = origin[1] + (b + 0.5) * cell - sc[j, 1]
                    z = origin[2] + (c + 0.5) * cell - sc[j, 2]
                    if x * x + y * y + z * z <= (r + 0.5 * cell) ** 2:
                        g[a, b, c] = 1
    return g
