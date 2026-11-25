#!/usr/bin/env python3
"""Bake the ship routes for the Manhattan photoreal scene's moving world
(scripts/life_ships.gd).

Inputs, both already in the repo, so this fetches nothing:
  * assets/manhattan/roads.json  `ferries`: NYC's Centerline ferry routes
    (CSCL rw_type 14, NYC Open Data, public domain), as polylines.
  * assets/manhattan/water.png   the land/water mask (OpenStreetMap coastline,
    ODbL; NHD, public domain), for the river channels.

Output: assets/manhattan/ships.json, in the DATASET frame of buildings.json
(metres east/north of the Battery Park datum, turned 29 deg onto the street
grid; see fetch_manhattan.py):

  {"routes": [{"name", "kind", "pts": [[e, n], ...], "loop": bool}, ...],
   "anchorages": [{"name", "e", "n", "hdg"}, ...]}

  kind   ferry        a ferry line between two terminals (sail it end to end,
                      dwell, sail back)
         si_ferry     the Staten Island ferry, Whitehall <-> St George
         channel_n/s  one direction of a river's fairway (keep right: the
                      northbound lane is the east half of the Hudson, etc.)
         harbour      the Upper Bay fairway from the Battery to the Narrows
         sightseeing  a harbour tour loop from the Hudson piers

How the ferry lines are built. CSCL draws each ferry line as many short
pieces, clipped at the borough boundary, and the lines share pieces where
they run together. They are welded into a graph; every degree-1 vertex is a
terminal (a pier, or a clip point out in the water) and a route is the
shortest path between two terminals. Of all terminal pairs, the longest
distinct paths are kept (a path that mostly retraces one already kept is
dropped), which gives the real lines without a hand list of piers.

How the channels are found. The street grid runs along the dataset north axis,
so each river crosses a row of the mask at roughly a right angle. In each row
the island's middle is the median east of the street nodes there; the first
water run west of it is the Hudson, the first east of it the East River. The
lanes sit at fixed fractions of that run and are smoothed along the river.
Rows where the run is under MIN_CHANNEL_M wide (the Harlem River, Spuyten
Duyvil) are skipped: nothing big goes there.

    python3 bake_manhattan_ships.py
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "godot" / "assets" / "manhattan"

DATUM_LAT = 40.7005
DATUM_LON = -74.0170
GRID_ROTATION_DEG = 29.0
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))

WELD_M = 3.0
ROW_STEP_M = 120.0
MIN_CHANNEL_M = 260.0
MAX_FERRY_ROUTES = 12
CHANNEL_N0 = 600.0


def project(lat: float, lon: float) -> tuple[float, float]:
    x = (lon - DATUM_LON) * M_PER_DEG_LON
    y = (lat - DATUM_LAT) * M_PER_DEG_LAT
    t = math.radians(GRID_ROTATION_DEG)
    return (round(x * math.cos(t) - y * math.sin(t), 1),
            round(x * math.sin(t) + y * math.cos(t), 1))


class Mask:
    def __init__(self, png: Path, meta: dict) -> None:
        self.a = np.asarray(Image.open(png).convert("L")) > 127
        self.e0, self.n0, self.e1, self.n1 = meta["bounds_enu"]
        self.res = float(meta["res_m"])

    def water(self, e: float, n: float) -> bool:
        x = int((e - self.e0) / self.res)
        y = int((self.n1 - n) / self.res)
        if x < 0 or y < 0 or y >= self.a.shape[0] or x >= self.a.shape[1]:
            return True           # past the mask is the open harbour (as env.is_water)
        return bool(self.a[y, x])

    def clear(self, pts: list, margin: float = 0.0) -> bool:
        """Every point, and the straight legs between them, on water."""
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            steps = max(1, int(math.dist(a, b) / 8.0))
            for k in range(steps + 1):
                t = k / steps
                e = a[0] + (b[0] - a[0]) * t
                n = a[1] + (b[1] - a[1]) * t
                if not self.water(e, n):
                    return False
        return True


# ------------------------------------------------------------------ ferries
def ferry_routes(polys: list, mask: Mask) -> list:
    key = lambda p: (round(p[0] / WELD_M), round(p[1] / WELD_M))
    ids: dict = {}
    pos: list = []
    adj: dict = {}

    def node(p) -> int:
        k = key(p)
        if k not in ids:
            ids[k] = len(pos)
            pos.append((float(p[0]), float(p[1])))
            adj[ids[k]] = {}
        return ids[k]

    for f in polys:
        prev = None
        for p in f:
            c = node(p)
            if prev is not None and prev != c:
                d = math.dist(pos[prev], pos[c])
                adj[prev][c] = d
                adj[c][prev] = d
            prev = c
    ends = [i for i in adj if len(adj[i]) == 1]

    def dijkstra(src: int):
        dist = {src: 0.0}
        back = {}
        q = [(0.0, src)]
        while q:
            d, u = heapq.heappop(q)
            if d > dist[u]:
                continue
            for v, w in adj[u].items():
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    back[v] = u
                    heapq.heappush(q, (nd, v))
        return dist, back

    pairs = []
    for i, a in enumerate(ends):
        dist, back = dijkstra(a)
        for b in ends[i + 1:]:
            if b in dist and dist[b] > 1500.0:
                path = [b]
                while path[-1] != a:
                    path.append(back[path[-1]])
                pairs.append((dist[b], path[::-1]))
    pairs.sort(key=lambda t: -t[0])
    used: set = set()
    out = []
    for length, path in pairs:
        edges = {tuple(sorted((path[k], path[k + 1]))) for k in range(len(path) - 1)}
        shared = len(edges & used) / max(1, len(edges))
        if shared > 0.5:
            continue
        used |= edges
        pts = simplify([pos[k] for k in path], 6.0)
        out.append({"name": "ferry_%d" % len(out), "kind": "ferry",
                    "pts": [[round(p[0], 1), round(p[1], 1)] for p in pts], "loop": False,
                    "length_m": round(length)})
        if len(out) >= MAX_FERRY_ROUTES:
            break
    return out


def simplify(pts: list, tol: float) -> list:
    """Douglas-Peucker."""
    if len(pts) < 3:
        return pts
    a, b = np.array(pts[0]), np.array(pts[-1])
    ab = b - a
    L = np.linalg.norm(ab) or 1.0
    best, bi = 0.0, 0
    for i in range(1, len(pts) - 1):
        q = np.array(pts[i]) - a
        d = abs(ab[0] * q[1] - ab[1] * q[0]) / L
        if d > best:
            best, bi = d, i
    if best <= tol:
        return [pts[0], pts[-1]]
    return simplify(pts[:bi + 1], tol)[:-1] + simplify(pts[bi:], tol)


# ------------------------------------------------------------------ channels
def channels(nodes: np.ndarray, mask: Mask) -> list:
    rows = []
    # From just north of the Battery: south of it the "first water west of
    # the island" is the open Upper Bay, which the harbour legs cover.
    n = max(float(np.min(nodes[:, 1])), CHANNEL_N0)
    nmax = float(np.max(nodes[:, 1]))
    while n < nmax:
        band = nodes[np.abs(nodes[:, 1] - n) < ROW_STEP_M * 0.5]
        if len(band) >= 6:
            mid = float(np.median(band[:, 0]))
            rows.append((n, run(mask, mid, n, -1), run(mask, mid, n, +1)))
        n += ROW_STEP_M
    out = []
    for side, name in ((1, "hudson"), (2, "east_river")):
        segs = [[]]
        for r in rows:
            span = r[side]
            if span is None or span[1] - span[0] < MIN_CHANNEL_M:
                if segs[-1]:
                    segs.append([])
                continue
            segs[-1].append((r[0], span))
        seg = max(segs, key=len)
        if len(seg) < 6:
            continue
        ns = np.array([s[0] for s in seg])
        w0 = smooth(np.array([s[1][0] for s in seg]))
        w1 = smooth(np.array([s[1][1] for s in seg]))
        for kind, frac in (("channel_n", 0.62), ("channel_s", 0.38)):
            e = w0 + (w1 - w0) * frac
            pts = [[round(float(a), 1), round(float(b), 1)] for a, b in zip(e, ns)
                   if mask.water(float(a), float(b))]
            if kind == "channel_s":
                pts = pts[::-1]
            pts = simplify([tuple(p) for p in pts], 10.0)
            out.append({"name": "%s_%s" % (name, kind[-1]), "kind": kind,
                        "pts": [list(p) for p in pts], "loop": False})
    return out


def run(mask: Mask, e: float, n: float, step: int):
    """The first water run at least MIN_CHANNEL_M wide from `e` going `step`
    (-1 west, +1 east): (e0, e1). Narrower runs are passed over, because the
    first water west of the island's middle is as often Central Park's
    Reservoir or a slip between piers as the river."""
    x = e
    lim = 6000.0
    while abs(x - e) < lim:
        while not mask.water(x, n):
            x += step * 8.0
            if abs(x - e) > lim:
                return None
        a = x
        while mask.water(x, n) and abs(x - e) < lim:
            x += step * 8.0
        if abs(x - a) >= MIN_CHANNEL_M:
            return (min(a, x), max(a, x))
    return None


def smooth(v: np.ndarray, k: int = 7) -> np.ndarray:
    """Median then mean along the river: the median drops the rows where a
    pier head or a slip moved the shore by a pier's length, the mean takes
    the steps out of what is left."""
    if len(v) < k:
        return v
    pad = np.pad(v, (k // 2, k // 2), mode="edge")
    med = np.array([np.median(pad[i:i + k]) for i in range(len(v))])
    pad = np.pad(med, (k // 2, k // 2), mode="edge")
    return np.convolve(pad, np.ones(k) / k, mode="valid")


# ------------------------------------------------------------------ harbour
#: Hand waypoints (lat, lon) for the open harbour, where there is no channel
#: to find in a mask that ends at the Battery. Each leg is checked against the
#: mask; past its south edge everything counts as water, as in the scene.
HARBOUR = {
    "si_ferry": ("si_ferry", [(40.7008, -74.0128), (40.6975, -74.0180), (40.6880, -74.0300),
                              (40.6700, -74.0480), (40.6550, -74.0640), (40.6445, -74.0722)]),
    "upper_bay_s": ("harbour", [(40.7020, -74.0200), (40.6900, -74.0280), (40.6700, -74.0420),
                                (40.6400, -74.0480), (40.6100, -74.0430)]),
    "upper_bay_n": ("harbour", [(40.6100, -74.0390), (40.6400, -74.0420), (40.6700, -74.0360),
                                (40.6880, -74.0220), (40.6960, -74.0180)]),
    # Circle-the-harbour tour from the W 42nd St piers: down the Hudson, round
    # the statue, back up.
    "tour": ("sightseeing", [(40.7620, -74.0050), (40.7400, -74.0150), (40.7150, -74.0230),
                             (40.6990, -74.0260), (40.6900, -74.0420), (40.6870, -74.0470),
                             (40.6930, -74.0480), (40.7050, -74.0300), (40.7300, -74.0190),
                             (40.7550, -74.0090)]),
}

def repair(pts: list, mask: Mask) -> list:
    """Hand waypoints onto water: a point on land moves to the nearest water
    within 300 m, and a leg that crosses land gets a midpoint pushed sideways
    until both halves are clear. The waypoints are rounded lat/lon, and a
    few metres either way puts one on a pier."""
    out = []
    for p in pts:
        if not mask.water(*p):
            best = None
            for r in range(8, 300, 8):
                for k in range(16):
                    q = [p[0] + r * math.cos(k * math.pi / 8), p[1] + r * math.sin(k * math.pi / 8)]
                    if mask.water(*q):
                        best = q
                        break
                if best:
                    break
            p = best or p
        out.append([round(p[0], 1), round(p[1], 1)])
    fixed = [out[0]]
    for b in out[1:]:
        a = fixed[-1]
        if not mask.clear([a, b]):
            d = (b[0] - a[0], b[1] - a[1])
            L = math.hypot(*d) or 1.0
            nrm = (-d[1] / L, d[0] / L)
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            for off in [s * o for o in range(50, 1200, 50) for s in (1, -1)]:
                m = [round(mid[0] + nrm[0] * off, 1), round(mid[1] + nrm[1] * off, 1)]
                if mask.clear([a, m]) and mask.clear([m, b]):
                    fixed.append(m)
                    break
        fixed.append(b)
    return fixed


ANCHORAGES = [("stapleton_1", 40.6320, -74.0550, 20.0), ("stapleton_2", 40.6260, -74.0500, 35.0),
              ("stapleton_3", 40.6380, -74.0600, 10.0), ("gravesend", 40.5900, -74.0200, 200.0)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--assets", default=str(ASSETS))
    a = ap.parse_args()
    assets = Path(a.assets)
    roads = json.loads((assets / "roads.json").read_text())
    mask = Mask(assets / "water.png", json.loads((assets / "water.json").read_text()))
    nodes = np.array(roads["nodes"], dtype=float)

    routes = ferry_routes(roads.get("ferries", []), mask)
    routes += channels(nodes, mask)
    for name, (kind, ll) in HARBOUR.items():
        pts = repair([list(project(la, lo)) for la, lo in ll], mask)
        if not mask.clear(pts):
            print("skipped: route %s still crosses land in the mask" % name)
            continue
        routes.append({"name": name, "kind": kind, "pts": pts, "loop": kind == "sightseeing"})
    anch = []
    for name, la, lo, hdg in ANCHORAGES:
        e, n = project(la, lo)
        anch.append({"name": name, "e": e, "n": n, "hdg": hdg})

    doc = {"source": "ferry lines: NYC Open Data Centerline (CSCL) via roads.json, public domain; "
                     "channels: water.png (OpenStreetMap coastline ODbL, USGS NHD public domain); "
                     "harbour legs: hand waypoints",
           "frame": "dataset frame of buildings.json (Battery Park datum, 29 deg grid)",
           "routes": routes, "anchorages": anch}
    out = assets / "ships.json"
    out.write_text(json.dumps(doc, separators=(",", ":")))
    for r in routes:
        L = sum(math.dist(r["pts"][i], r["pts"][i + 1]) for i in range(len(r["pts"]) - 1))
        print("%-14s %-12s %3d pts %6.0f m" % (r["name"], r["kind"], len(r["pts"]), L))
    print("wrote", out, out.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
