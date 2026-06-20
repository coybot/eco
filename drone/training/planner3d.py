"""Privileged 3D A* path planner — the oracle BC expert.

During dataset generation we KNOW the obstacle geometry, so the teacher can plan a genuinely
collision-free path (over / under / around / through a hole) with full knowledge, then follow
it smoothly. The policy never sees this — it only sees the depth grid and learns to reproduce
the planner's behavior from local perception. This sidesteps the impossibility of a single
reactive heuristic that handles every obstacle type.
"""
from __future__ import annotations

import heapq
import math

import numpy as np

try:
    from .world3d import Box3D
except ImportError:
    from world3d import Box3D


def _occupied(pt, boxes, inflate):
    x, y, z = pt
    for b in boxes:
        if (abs(x - b.cx) <= b.hx + inflate and abs(y - b.cy) <= b.hy + inflate
                and abs(z - b.cz) <= b.hz + inflate):
            return True
    return False


# 26-connected neighborhood
_NBRS = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
         if not (dx == 0 and dy == 0 and dz == 0)]


def astar_path(start, goal, boxes, res=0.5, inflate=0.35, zmin=0.4, zmax=6.0, pad=2.0):
    """Return a list of world-frame waypoints start->goal avoiding inflated boxes, or None.

    Grid is axis-aligned at `res` spacing over the bounding box of start+goal (+pad), z clamped
    to [zmin, zmax]. Obstacles inflated by `inflate` (drone radius + margin).
    """
    sx, sy, sz = start
    gx, gy, gz = goal
    lo = (min(sx, gx) - pad, min(sy, gy) - pad, max(zmin, min(sz, gz) - pad))
    hi = (max(sx, gx) + pad, max(sy, gy) + pad, min(zmax, max(sz, gz) + pad))

    def to_idx(p):
        return (round((p[0] - lo[0]) / res), round((p[1] - lo[1]) / res), round((p[2] - lo[2]) / res))

    def to_pt(i):
        return (lo[0] + i[0] * res, lo[1] + i[1] * res, lo[2] + i[2] * res)

    dims = (int((hi[0] - lo[0]) / res) + 1, int((hi[1] - lo[1]) / res) + 1,
            int((hi[2] - lo[2]) / res) + 1)

    def in_bounds(i):
        return 0 <= i[0] < dims[0] and 0 <= i[1] < dims[1] and 0 <= i[2] < dims[2]

    s_i, g_i = to_idx(start), to_idx(goal)
    if not (in_bounds(s_i) and in_bounds(g_i)):
        return None

    def blocked(i):
        return _occupied(to_pt(i), boxes, inflate)

    if blocked(g_i):   # nudge goal out of inflation if needed (rare)
        inflate2 = inflate * 0.5
        if _occupied(to_pt(g_i), boxes, inflate2):
            return None

    def h(i):
        return res * math.sqrt((i[0] - g_i[0]) ** 2 + (i[1] - g_i[1]) ** 2 + (i[2] - g_i[2]) ** 2)

    openq = [(h(s_i), 0.0, s_i)]
    came = {}
    gscore = {s_i: 0.0}
    seen = set()
    while openq:
        _, gc, cur = heapq.heappop(openq)
        if cur == g_i:
            path = [to_pt(cur)]
            while cur in came:
                cur = came[cur]
                path.append(to_pt(cur))
            return path[::-1]
        if cur in seen:
            continue
        seen.add(cur)
        for d in _NBRS:
            nb = (cur[0] + d[0], cur[1] + d[1], cur[2] + d[2])
            if not in_bounds(nb) or nb in seen or blocked(nb):
                continue
            step = res * math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
            ng = gc + step
            if ng < gscore.get(nb, math.inf):
                gscore[nb] = ng
                came[nb] = cur
                heapq.heappush(openq, (ng + h(nb), ng, nb))
    return None


class PathFollower:
    """Pure-pursuit follower over an A* path, returning a world-frame velocity direction."""

    def __init__(self, path, lookahead=1.5):
        self.path = [np.asarray(p, dtype=float) for p in path]
        self.la = lookahead
        self.i = 0

    def target(self, pos):
        """Advance along the path and return a lookahead world point to steer toward."""
        pos = np.asarray(pos, dtype=float)
        # advance the index past points we're already near
        while self.i < len(self.path) - 1 and np.linalg.norm(self.path[self.i] - pos) < self.la:
            self.i += 1
        return self.path[min(self.i, len(self.path) - 1)]
