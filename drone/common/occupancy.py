"""Online occupancy mapping + A* for deploy-ready autonomy.

On the vehicle there is no known obstacle geometry — only the forward depth grid (the same
DEPTH_ROWS x DEPTH_COLS rays the policy sees, sampled from the RealSense depth image). This module
accumulates those rays into a sparse odom-frame voxel map as the drone moves, and plans a global
A* route over that *estimate*. The HierarchicalPlanner feeds the next path segment to the policy;
the policy still does local depth-grid avoidance. Nothing here needs the true course geometry, so
it runs identically in sim (depth from world3d.depth_grid) and on hardware (depth from RealSense).

Design choices that matter for partial-observability:
- Hits only ever ADD occupancy (never cleared) — a wall seen once stays known after the drone
  ducks past it; A* keeps routing around the whole obstacle, not just what's currently in view.
- Occupancy is inflated by the drone radius + margin at query time so A* keeps clearance and so a
  thin "front face" of voxels stands in for the obstacle's unseen depth.
- Replanning every tick is cheap at this scale and lets the route update as new geometry appears.
"""
from __future__ import annotations

import heapq
import math

import numpy as np

try:
    from .contract import RAY_DIRS, DEPTH_MAX
except ImportError:  # flat-file deploy / sim
    from contract import RAY_DIRS, DEPTH_MAX

_RAY = np.asarray(RAY_DIRS, dtype=np.float64)   # (R,3) body fwd/left/up


class OccupancyMap:
    """Sparse voxel occupancy in the odom/world frame, accumulated from forward depth grids."""

    def __init__(self, res: float = 0.4, hit_margin: float = 0.3, zmin: float = 0.0,
                 zmax: float = 6.0):
        self.res = res
        self.hit_thresh = DEPTH_MAX - hit_margin   # ranges >= this are "no obstacle"
        self.zmin = zmin
        self.zmax = zmax
        self._occ: set[tuple] = set()               # occupied voxel indices
        self._inflated_cache = None                 # (radius_vox, frozenset)

    def reset(self) -> None:
        self._occ.clear()
        self._inflated_cache = None

    def _vox(self, p) -> tuple:
        r = self.res
        return (int(math.floor(p[0] / r)), int(math.floor(p[1] / r)), int(math.floor(p[2] / r)))

    def integrate(self, pos, yaw, depth_grid) -> int:
        """Cast the depth grid from (pos, yaw); mark the world hit point of every ray that hit.

        `depth_grid`: DEPTH_RAYS ranges (m). Returns the number of new voxels added.
        """
        d = np.asarray(depth_grid, dtype=np.float64).reshape(-1)
        c, s = math.cos(yaw), math.sin(yaw)
        # body (fwd,left,up) -> world dirs (yaw about z; up stays world-up)
        bf, bl, bu = _RAY[:, 0], _RAY[:, 1], _RAY[:, 2]
        wx = c * bf - s * bl
        wy = s * bf + c * bl
        wz = bu
        hit = d < self.hit_thresh
        before = len(self._occ)
        ox, oy, oz = float(pos[0]), float(pos[1]), float(pos[2])
        r = self.res
        for i in np.nonzero(hit)[0]:
            rng = d[i]
            hxp = ox + wx[i] * rng
            hyp = oy + wy[i] * rng
            hzp = oz + wz[i] * rng
            if hzp < self.zmin or hzp > self.zmax:
                continue
            self._occ.add((int(math.floor(hxp / r)), int(math.floor(hyp / r)),
                           int(math.floor(hzp / r))))
        if len(self._occ) != before:
            self._inflated_cache = None   # invalidate inflation
        return len(self._occ) - before

    def _inflated(self, radius_vox: int) -> frozenset:
        if self._inflated_cache and self._inflated_cache[0] == radius_vox:
            return self._inflated_cache[1]
        offs = [(dx, dy, dz)
                for dx in range(-radius_vox, radius_vox + 1)
                for dy in range(-radius_vox, radius_vox + 1)
                for dz in range(-radius_vox, radius_vox + 1)
                if dx * dx + dy * dy + dz * dz <= radius_vox * radius_vox]
        inf = set()
        for (vx, vy, vz) in self._occ:
            for (dx, dy, dz) in offs:
                inf.add((vx + dx, vy + dy, vz + dz))
        fs = frozenset(inf)
        self._inflated_cache = (radius_vox, fs)
        return fs

    @property
    def n_voxels(self) -> int:
        return len(self._occ)


def astar_occupancy(start, goal, occ: OccupancyMap, inflate: float = 0.55,
                    zmin: float = 0.4, zmax: float = 6.0, pad: float = 2.0):
    """A* from start to goal over an OccupancyMap estimate. Returns world-frame waypoints or None.

    Grid spacing = occ.res; obstacles inflated by `inflate` (drone radius + margin). z clamped to
    [zmin, zmax]. Mirrors planner3d.astar_path but blocks on the accumulated voxel map.
    """
    res = occ.res
    sx, sy, sz = start
    gx, gy, gz = goal
    lo = (min(sx, gx) - pad, min(sy, gy) - pad, max(zmin, min(sz, gz) - pad))
    hi = (max(sx, gx) + pad, max(sy, gy) + pad, min(zmax, max(sz, gz) + pad))

    def to_idx(p):
        return (round((p[0] - lo[0]) / res), round((p[1] - lo[1]) / res),
                round((p[2] - lo[2]) / res))

    def to_pt(i):
        return (lo[0] + i[0] * res, lo[1] + i[1] * res, lo[2] + i[2] * res)

    dims = (int((hi[0] - lo[0]) / res) + 1, int((hi[1] - lo[1]) / res) + 1,
            int((hi[2] - lo[2]) / res) + 1)

    def in_bounds(i):
        return 0 <= i[0] < dims[0] and 0 <= i[1] < dims[1] and 0 <= i[2] < dims[2]

    radius_vox = max(0, int(round(inflate / res)))
    blocked_set = occ._inflated(radius_vox)

    def blocked(i):
        return occ._vox(to_pt(i)) in blocked_set

    s_i, g_i = to_idx(start), to_idx(goal)
    if not (in_bounds(s_i) and in_bounds(g_i)):
        return None
    if blocked(g_i):          # if the goal voxel is (spuriously) blocked, try a smaller inflation
        smaller = occ._inflated(max(0, radius_vox - 1))
        if occ._vox(to_pt(g_i)) in smaller:
            return None
        blocked_set = smaller
        radius_vox = max(0, radius_vox - 1)

    _NBRS = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
             if not (dx == 0 and dy == 0 and dz == 0)]

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
        for dd in _NBRS:
            nb = (cur[0] + dd[0], cur[1] + dd[1], cur[2] + dd[2])
            if not in_bounds(nb) or nb in seen or blocked(nb):
                continue
            step = res * math.sqrt(dd[0] ** 2 + dd[1] ** 2 + dd[2] ** 2)
            ng = gc + step
            if ng < gscore.get(nb, math.inf):
                gscore[nb] = ng
                came[nb] = cur
                heapq.heappush(openq, (ng + h(nb), ng, nb))
    return None
