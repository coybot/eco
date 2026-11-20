"""Occupancy grid in the beacon frame, obstacle margins, A* planner, scan matching.

Grid (addendum section 3): 0.2 m cells, each UNSEEN (0), EMPTY (1) or OCCUPIED (2..6,
a confidence count). Occupied cells carry a class -- solid, tree or wire -- from the
sensor's class guess, which sets the margin: 1 m from solids, 2 m from wires, 3 m from
trees. Margins are kept as inflation counts (a sphere stamped around every occupied
cell; unstamped when the cell is cleared), so the planner's "blocked" test is O(1).

Update rules (confidence: radar-only cells 2-3, infrared/contact cells 4-6):
  * infrared zone with a reading: cells along the ray -> EMPTY -- a cell held only by
    radar (or by one infrared hit) is cleared, a confirmed one loses one count -- and
    the cells are marked "seen through", after which radar may not re-mark them (inside
    its 4 m the infrared is the better sensor; the radar's 3 deg angle error at 10 m is
    0.5 m and otherwise leaves phantom obstacles at every balcony edge). Glass never
    gets a valid infrared reading, so radar keeps marking glass. End cell -> OCCUPIED;
  * infrared zone with no reading, radar beam with no return: cells up to the range
    limit -> EMPTY only if UNSEEN (never clears an obstacle: glass gives no infrared
    reading, and a radar misses foliage 65 % of the time);
  * radar return: cells along the beam centre to the return -> EMPTY if UNSEEN; the
    return cell -> OCCUPIED with the radar's class guess. Radar "empty" evidence stops at
    8 m (the shortest range at which glass and foliage are still detected).
  * contact: the touched point -> OCCUPIED, class wire (2 m margin: something the sensors
    did not see).

Approach box: inside a small box at the railing (from the entry point to the balcony),
solids get a 0.4 m margin instead of 1 m, otherwise no balcony could be entered
(a balcony is less than 2 m from its own railing, floor, slab above and walls).

Planner: A* on the 26-connected grid, start to goal. Blocked and occupied cells are
impassable; UNSEEN cells are allowed in the PLAN at extra cost (``unseen_cost``), but the
vehicle only flies along the plan while the cells ahead are seen EMPTY -- it stops at the
edge of what it has seen, looks, and replans.
Compiled with numba when installed.
"""
from __future__ import annotations

import math

import numpy as np

from ._nb import njit

UNSEEN, EMPTY, OCC = 0, 1, 2
C_SOLID, C_TREE, C_WIRE = 0, 1, 2


def _sphere_offsets(r_cells):
    r = int(math.ceil(r_cells))
    g = np.mgrid[-r:r + 1, -r:r + 1, -r:r + 1].reshape(3, -1).T
    keep = (g ** 2).sum(1) <= r_cells ** 2 + 1e-9
    return g[keep].astype(np.int64)


@njit(cache=True)
def _stamp(infl, i, j, k, offs, sgn):
    nx, ny, nz = infl.shape
    for q in range(offs.shape[0]):
        a, b, c = i + offs[q, 0], j + offs[q, 1], k + offs[q, 2]
        if 0 <= a < nx and 0 <= b < ny and 0 <= c < nz:
            infl[a, b, c] += sgn


@njit(cache=True)
def _set_occ(state, cls, infl_s, infl_ss, infl_w, infl_t, offs_s, offs_ss, offs_w, offs_t, i, j, k, c, gain, veto):
    # gain 1 = radar (confidence capped at 3, refused where infrared has seen through the cell);
    # gain >= 2 = infrared or contact (confidence 4..6, lifts the veto: something is there now)
    s = state[i, j, k]
    if gain == 1:
        if veto[i, j, k]:
            return 0
        cap = 3
    else:
        veto[i, j, k] = 0
        cap = 6
    if s >= 2:
        if gain >= 2 and s < 4:
            state[i, j, k] = 4
        else:
            state[i, j, k] = min(max(cap, s), s + gain) if s < cap else s
        old = cls[i, j, k]
        if c != old and (c == 1 or (c == 2 and old == 0)):        # upgrade to the larger margin
            _unstamp_cls(cls, infl_s, infl_ss, infl_w, infl_t, offs_s, offs_ss, offs_w, offs_t, i, j, k)
            cls[i, j, k] = c
            _stamp_cls(cls, infl_s, infl_ss, infl_w, infl_t, offs_s, offs_ss, offs_w, offs_t, i, j, k)
        return 0
    state[i, j, k] = 2 if gain == 1 else 4
    cls[i, j, k] = c
    _stamp_cls(cls, infl_s, infl_ss, infl_w, infl_t, offs_s, offs_ss, offs_w, offs_t, i, j, k)
    return 1


@njit(cache=True)
def _stamp_cls(cls, infl_s, infl_ss, infl_w, infl_t, offs_s, offs_ss, offs_w, offs_t, i, j, k):
    c = cls[i, j, k]
    if c == 0:
        _stamp(infl_s, i, j, k, offs_s, 1)
        _stamp(infl_ss, i, j, k, offs_ss, 1)
    elif c == 1:
        _stamp(infl_t, i, j, k, offs_t, 1)
    else:
        _stamp(infl_w, i, j, k, offs_w, 1)


@njit(cache=True)
def _unstamp_cls(cls, infl_s, infl_ss, infl_w, infl_t, offs_s, offs_ss, offs_w, offs_t, i, j, k):
    c = cls[i, j, k]
    if c == 0:
        _stamp(infl_s, i, j, k, offs_s, -1)
        _stamp(infl_ss, i, j, k, offs_ss, -1)
    elif c == 1:
        _stamp(infl_t, i, j, k, offs_t, -1)
    else:
        _stamp(infl_w, i, j, k, offs_w, -1)


@njit(cache=True)
def _integrate(state, cls, infl_s, infl_ss, infl_w, infl_t, offs_s, offs_ss, offs_w, offs_t,
               lo, cell, orig, dirs, lens, hit, can_clear, hcls, free_len, gain, veto):
    nx, ny, nz = state.shape
    new_occ = 0
    step = 0.5 * cell
    for r in range(orig.shape[0]):
        L = min(lens[r], free_len[r])
        n = int(L / step)
        lastc = -1
        for s in range(n):
            t = s * step
            if hit[r] and t > lens[r] - cell:
                break
            px = orig[r, 0] + dirs[r, 0] * t
            py = orig[r, 1] + dirs[r, 1] * t
            pz = orig[r, 2] + dirs[r, 2] * t
            i = int(math.floor((px - lo[0]) / cell))
            j = int(math.floor((py - lo[1]) / cell))
            k = int(math.floor((pz - lo[2]) / cell))
            if i < 0 or j < 0 or k < 0 or i >= nx or j >= ny or k >= nz:
                break
            cid = (i * ny + j) * nz + k
            if cid == lastc:
                continue
            lastc = cid
            st = state[i, j, k]
            if can_clear[r]:
                veto[i, j, k] = 1                 # infrared saw through this cell
            if st == 0:
                state[i, j, k] = 1
            elif st >= 2 and can_clear[r]:
                if st <= 4:                       # radar-only, or a single infrared hit: clear
                    _unstamp_cls(cls, infl_s, infl_ss, infl_w, infl_t, offs_s, offs_ss, offs_w, offs_t, i, j, k)
                    state[i, j, k] = 1
                else:
                    state[i, j, k] = st - 1
        if hit[r]:
            px = orig[r, 0] + dirs[r, 0] * lens[r]
            py = orig[r, 1] + dirs[r, 1] * lens[r]
            pz = orig[r, 2] + dirs[r, 2] * lens[r]
            i = int(math.floor((px - lo[0]) / cell))
            j = int(math.floor((py - lo[1]) / cell))
            k = int(math.floor((pz - lo[2]) / cell))
            if 0 <= i < nx and 0 <= j < ny and 0 <= k < nz:
                new_occ += _set_occ(state, cls, infl_s, infl_ss, infl_w, infl_t, offs_s, offs_ss, offs_w, offs_t,
                                    i, j, k, hcls[r], gain, veto)
    return new_occ


# ---------------------------------------------------------------- A*
@njit(cache=True)
def _heap_push(hf, hi, n, f, idx):
    i = n
    hf[i] = f
    hi[i] = idx
    while i > 0:
        p = (i - 1) // 2
        if hf[p] <= hf[i]:
            break
        hf[p], hf[i] = hf[i], hf[p]
        hi[p], hi[i] = hi[i], hi[p]
        i = p
    return n + 1


@njit(cache=True)
def _heap_pop(hf, hi, n):
    f, idx = hf[0], hi[0]
    n -= 1
    hf[0] = hf[n]
    hi[0] = hi[n]
    i = 0
    while True:
        l = 2 * i + 1
        if l >= n:
            break
        r = l + 1
        m = l if (r >= n or hf[l] <= hf[r]) else r
        if hf[i] <= hf[m]:
            break
        hf[m], hf[i] = hf[i], hf[m]
        hi[m], hi[i] = hi[i], hi[m]
        i = m
    return f, idx, n


@njit(cache=True)
def _astar(state, infl_s, infl_ss, infl_w, infl_t, abox, start, goal, unseen_cost, relax_center, relax_r2,
           gbuf, pbuf, gen, cur_gen, hf, hi, max_exp, w_h):
    nx, ny, nz = state.shape
    sidx = (start[0] * ny + start[1]) * nz + start[2]
    gidx = (goal[0] * ny + goal[1]) * nz + goal[2]
    gen[sidx] = cur_gen
    gbuf[sidx] = 0.0
    pbuf[sidx] = -1
    n = _heap_push(hf, hi, 0, 0.0, sidx)
    exp = 0
    found = False
    while n > 0 and exp < max_exp:
        f, idx, n = _heap_pop(hf, hi, n)
        if idx == gidx:
            found = True
            break
        i = idx // (ny * nz)
        j = (idx // nz) % ny
        k = idx % nz
        g0 = gbuf[idx]
        hcur = w_h * math.sqrt((i - goal[0]) ** 2 + (j - goal[1]) ** 2 + (k - goal[2]) ** 2)
        if f > g0 + hcur + 1e-6:
            continue                       # stale entry
        exp += 1
        for di in range(-1, 2):
            a = i + di
            if a < 0 or a >= nx:
                continue
            for dj in range(-1, 2):
                b = j + dj
                if b < 0 or b >= ny:
                    continue
                for dk in range(-1, 2):
                    if di == 0 and dj == 0 and dk == 0:
                        continue
                    c = k + dk
                    if c < 0 or c >= nz:
                        continue
                    st = state[a, b, c]
                    if st >= 2:
                        continue
                    inbox = abox[0] <= a <= abox[1] and abox[2] <= b <= abox[3] and abox[4] <= c <= abox[5]
                    blocked = infl_w[a, b, c] > 0 or infl_t[a, b, c] > 0 or \
                        (infl_ss[a, b, c] > 0 if inbox else infl_s[a, b, c] > 0)
                    stepc = math.sqrt(di * di + dj * dj + dk * dk)
                    if blocked:
                        d2 = (a - relax_center[0]) ** 2 + (b - relax_center[1]) ** 2 + (c - relax_center[2]) ** 2
                        if d2 > relax_r2:
                            continue
                        stepc *= 10.0
                    if st == 0:
                        stepc *= 1.0 + unseen_cost
                    nidx = (a * ny + b) * nz + c
                    ng = g0 + stepc
                    if gen[nidx] != cur_gen or ng < gbuf[nidx]:
                        gen[nidx] = cur_gen
                        gbuf[nidx] = ng
                        pbuf[nidx] = idx
                        h = w_h * math.sqrt((a - goal[0]) ** 2 + (b - goal[1]) ** 2 + (c - goal[2]) ** 2)
                        if n < hf.shape[0]:
                            n = _heap_push(hf, hi, n, ng + h, nidx)
    if not found:
        return np.zeros((0, 3), np.int64), exp
    # walk back
    cnt = 0
    idx = gidx
    while idx != -1:
        cnt += 1
        idx = pbuf[idx]
    out = np.zeros((cnt, 3), np.int64)
    idx = gidx
    q = cnt - 1
    while idx != -1:
        out[q, 0] = idx // (ny * nz)
        out[q, 1] = (idx // nz) % ny
        out[q, 2] = idx % nz
        q -= 1
        idx = pbuf[idx]
    return out, exp


@njit(cache=True)
def _match_score(state, lo, cell, pts, off):
    nx, ny, nz = state.shape
    s = 0
    for p in range(pts.shape[0]):
        i = int(math.floor((pts[p, 0] + off[0] - lo[0]) / cell))
        j = int(math.floor((pts[p, 1] + off[1] - lo[1]) / cell))
        k = int(math.floor((pts[p, 2] + off[2] - lo[2]) / cell))
        if 0 <= i < nx and 0 <= j < ny and 0 <= k < nz and state[i, j, k] >= 4:
            s += 1
    return s


class OccupancyGrid:
    def __init__(self, lo_m, hi_m, cfg, approach_box_m=None):
        self.cell = cfg["grid_cell_m"]
        self.lo = np.asarray(lo_m, float)
        self.shape = tuple(int(x) for x in np.ceil((np.asarray(hi_m) - self.lo) / self.cell))
        self.state = np.zeros(self.shape, np.int8)
        self.cls = np.zeros(self.shape, np.int8)
        self.infl_s = np.zeros(self.shape, np.int16)
        self.infl_ss = np.zeros(self.shape, np.int16)
        self.infl_w = np.zeros(self.shape, np.int16)
        self.infl_t = np.zeros(self.shape, np.int16)
        self.veto = np.zeros(self.shape, np.uint8)
        c = self.cell
        self.offs_s = _sphere_offsets(cfg["margin_solid_m"] / c)
        self.offs_ss = _sphere_offsets(0.4 / c)
        self.offs_w = _sphere_offsets(cfg["margin_wire_m"] / c)
        self.offs_t = _sphere_offsets(cfg["margin_tree_m"] / c)
        N = int(np.prod(self.shape))
        self.gbuf = np.zeros(N, np.float64)
        self.pbuf = np.zeros(N, np.int32)
        self.gen = np.zeros(N, np.int32)
        self.cur_gen = 0
        self.hf = np.zeros(1_500_000, np.float64)
        self.hi = np.zeros(1_500_000, np.int64)
        self.abox = np.array([0, -1, 0, -1, 0, -1], np.int64)
        if approach_box_m is not None:
            a = self.idx(approach_box_m[0])
            b = self.idx(approach_box_m[1])
            self.abox = np.array([a[0], b[0], a[1], b[1], a[2], b[2]], np.int64)
        self.n_occ_cells = 0

    # --------------------------------------------------------------- indexing
    def idx(self, p):
        q = np.floor((np.asarray(p, float) - self.lo) / self.cell).astype(np.int64)
        return np.clip(q, 0, np.array(self.shape) - 1)

    def inside(self, p):
        q = np.floor((np.asarray(p, float) - self.lo) / self.cell)
        return bool(np.all(q >= 0) and np.all(q < np.array(self.shape)))

    def center(self, ijk):
        return self.lo + (np.asarray(ijk, float) + 0.5) * self.cell

    # --------------------------------------------------------------- updates
    def integrate(self, orig, dirs, lens, hit, can_clear, hcls, free_len, gain=2):
        self.n_occ_cells += _integrate(self.state, self.cls, self.infl_s, self.infl_ss, self.infl_w, self.infl_t,
                                       self.offs_s, self.offs_ss, self.offs_w, self.offs_t, self.lo, self.cell,
                                       np.ascontiguousarray(orig, float), np.ascontiguousarray(dirs, float),
                                       np.asarray(lens, float), np.asarray(hit, np.bool_),
                                       np.asarray(can_clear, np.bool_), np.asarray(hcls, np.int64),
                                       np.asarray(free_len, float), int(gain), self.veto)

    def mark_occupied(self, p, c=C_WIRE):
        if not self.inside(p):
            return
        i, j, k = self.idx(p)
        _set_occ(self.state, self.cls, self.infl_s, self.infl_ss, self.infl_w, self.infl_t, self.offs_s,
                 self.offs_ss, self.offs_w, self.offs_t, i, j, k, c, 4, self.veto)
        self.state[i, j, k] = 6

    # --------------------------------------------------------------- queries
    def in_abox(self, ijk):
        a = self.abox
        return a[0] <= ijk[0] <= a[1] and a[2] <= ijk[1] <= a[3] and a[4] <= ijk[2] <= a[5]

    def blocked(self, ijk):
        i, j, k = ijk
        if self.state[i, j, k] >= 2 or self.infl_w[i, j, k] > 0 or self.infl_t[i, j, k] > 0:
            return True
        return (self.infl_ss if self.in_abox(ijk) else self.infl_s)[i, j, k] > 0

    def seen_empty(self, p):
        if not self.inside(p):
            return False
        i, j, k = self.idx(p)
        return self.state[i, j, k] == EMPTY

    def nearest_occupied_m(self, p, radius_m=5.0):
        """Distance to the nearest occupied cell within radius (inf if none)."""
        r = int(radius_m / self.cell)
        i, j, k = self.idx(p)
        sl = (slice(max(0, i - r), i + r + 1), slice(max(0, j - r), j + r + 1), slice(max(0, k - r), k + r + 1))
        sub = self.state[sl]
        occ = np.argwhere(sub >= 2)
        if len(occ) == 0:
            return math.inf
        base = np.array([sl[0].start, sl[1].start, sl[2].start])
        d = (occ + base - np.array([i, j, k])) * self.cell
        return float(np.sqrt((d ** 2).sum(1)).min())

    def plan(self, start_m, goal_m, unseen_cost, max_exp=600_000, relax_r_m=2.5, w_h=1.0):
        s = self.idx(start_m)
        g = self.idx(goal_m)
        self.cur_gen += 1
        if self.cur_gen > 2_000_000_000:
            self.gen[:] = 0
            self.cur_gen = 1
        rc = int(relax_r_m / self.cell)
        path, exp = _astar(self.state, self.infl_s, self.infl_ss, self.infl_w, self.infl_t, self.abox, s, g,
                           unseen_cost, s, rc * rc, self.gbuf, self.pbuf, self.gen, self.cur_gen, self.hf, self.hi,
                           max_exp, w_h)
        self.last_expansions = exp
        if len(path) == 0:
            return None
        return self.lo + (path + 0.5) * self.cell

    def match(self, pts_m, search_m, step_m=None):
        """Offset (3,) that best lays these obstacle points onto confident occupied cells."""
        step = step_m or self.cell
        n = int(round(search_m / step))
        best, best_off, s0 = -1, np.zeros(3), None
        for a in range(-n, n + 1):
            for b in range(-n, n + 1):
                for c in range(-n, n + 1):
                    off = np.array([a, b, c], float) * step
                    s = _match_score(self.state, self.lo, self.cell, pts_m, off)
                    if a == 0 and b == 0 and c == 0:
                        s0 = s
                    if s > best or (s == best and np.linalg.norm(off) < np.linalg.norm(best_off)):
                        best, best_off = s, off
        return best_off, best, s0
