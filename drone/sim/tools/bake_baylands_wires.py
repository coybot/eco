#!/usr/bin/env python3
"""Power lines of the Baylands scene: towers, poles and conductors.

Writes drone/sim/godot/assets/baylands/wires.json, read by
scripts/baylands_wires.gd (drawn, solid, and served to IPC as env_op "wires").

WHAT IS MEASURED AND WHAT IS INFERRED
  * Routes and structure positions: OpenStreetMap power=line / minor_line ways
    and their power=tower / pole nodes (ODbL), every structure then MOVED to
    where the 2020 lidar sees it (median of its upper returns; OSM is often
    2-4 m off) and its height taken from its own top (99.5th percentile).
  * Structures OSM lacks: a line is extended past its OSM end, and its long
    spans searched, for tall slim class-1 clusters in line with it (the
    north-edge marsh line east of the park is mostly this).
  * Arms: horizontal rows of returns on each structure (steel members return
    densely), with their lateral reach. Per line, the arm layout of the
    structures whose detection agrees with the line's commonest layout is
    used as is; the rest take that layout scaled to their own height.
  * CONDUCTORS ARE INFERRED. The 2020 county delivery has no conductor
    returns at all: no class 14/15, and across 14 mid-spans of the 115 kV
    line not one return of any class (withheld included) lies more than 12 m
    above the ground. Each conductor hangs from its arm tips (suspension
    insulator below the tip on tangent structures, at the tip on dead-ends)
    as a catenary with a typical design parameter for its class (c = H/w,
    see SAG), stiffened where needed so the lowest point keeps the CPUC
    GO 95 ground clearance for its voltage. Shield wires are assumed on 115 kV
    and 60 kV structures (one at the peak), none on 12 kV and wood poles.

Run on a machine with RAM to spare (it reads every lidar tile):
    uv run --no-project --with "laspy[lazrs]" --with numpy --with scipy \\
        --with pyproj python bake_baylands_wires.py --tiles DIR --rasters DIR \\
        --osm osm.json --out .../assets/baylands/wires.json --work DIR
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bake_baylands_lidar import CELL, REGION, grid_shape, utm_to_enu_affine  # noqa: E402

DATUM_LAT, DATUM_LON = 37.4125, -121.9970
M_PER_DEG_LAT = 111132.0
ROWS, COLS = grid_shape()
MARGIN = 450.0            # keep OSM structures this far outside REGION (spans crossing the edge)

# Catenary parameter c = H / w (metres): horizontal tension over weight per
# metre. Mid-span sag of a level span L is ~ L^2 / (8 c).
#   115 kV ACSR (e.g. 715 kcmil, ~1.3 kg/m) at ~15-20 % RBS: c ~ 1400 m
#       -> ~7 m sag on the line's 280 m spans, typical for a warm afternoon.
#   shield wire (7/16" EHS steel): a little tauter, c ~ 1700 m.
#   60 kV / 12 kV on steel: c ~ 1100 m.   wood-pole distribution: c ~ 500 m
#       (0.6 m on a 50 m span).
SAG = {"phase115": 1400.0, "shield": 1700.0, "phase60": 1100.0, "phase12": 1100.0, "dist": 500.0}
# Real conductor radii (m), for drawing: 115 kV ACSR ~ 13 mm, shield ~ 5.5 mm,
# 12 kV ~ 7 mm, distribution ~ 5 mm.
RADIUS = {"phase115": 0.0135, "shield": 0.0056, "phase60": 0.0110, "phase12": 0.0075, "dist": 0.0050}
# CPUC General Order 95, Table 1 (over roads/thoroughfares), rounded up a little:
# 115 kV ~ 34 ft (10.4 m), 60 kV ~ 30 ft, 0.75-22.5 kV ~ 25 ft (7.6 m), service ~ 18 ft.
CLEAR = {"phase115": 10.4, "shield": 10.4, "phase60": 9.2, "phase12": 7.6, "dist": 5.5}
# Arm layouts used where a structure's own rows are not seen: (metres below
# the top, reach from the centre line), top arm first. From the commonest
# measured layouts here (lattice: 1.3 / 4.3 / 7.3 m, 2.6 m reach).
DEFAULT_TPL = {"lattice": [(1.3, 2.6), (4.3, 2.7), (7.3, 2.6)],
               "steel_pole": [(1.0, 2.0), (4.0, 2.2), (7.0, 2.0)],
               "wood_pole": [(0.5, 1.2)]}
# Typical spans (m): a span much longer than this, or than the line's own,
# is missing structures OSM did not map.
TYPICAL_SPAN = {"phase115": 260.0, "phase60": 150.0, "phase12": 150.0, "dist": 50.0}
INSULATOR = {"phase115": 1.5, "phase60": 1.1, "phase12": 0.6, "dist": -0.25}   # string length; - = pin above arm


def geo_to_enu(lat, lon):
    m_lon = 111320.0 * math.cos(math.radians(DATUM_LAT))
    return (lon - DATUM_LON) * m_lon, (lat - DATUM_LAT) * M_PER_DEG_LAT


def in_region(e, n, margin=0.0):
    return REGION[0] - margin <= e <= REGION[2] + margin and REGION[1] - margin <= n <= REGION[3] + margin


# ------------------------------------------------------------------ points
def _tile(args):
    path, dtm_path = args
    import laspy
    dtm = np.memmap(dtm_path, np.float32, "r", shape=(ROWS, COLS))
    with laspy.open(path) as r:
        hx0, hy0 = r.header.mins[:2]
        hx1, hy1 = r.header.maxs[:2]
        p = r.read()
    coef, _ = utm_to_enu_affine(hx0, hy0, hx1, hy1)
    cls = np.asarray(p.classification).astype(np.int16)
    keep = np.isin(cls, [1, 3, 4, 5])
    x = np.asarray(p.x)[keep]; y = np.asarray(p.y)[keep]; z = np.asarray(p.z)[keep]
    cls = cls[keep]
    e = coef[0, 0] * x + coef[1, 0] * y + coef[2, 0]
    n = coef[0, 1] * x + coef[1, 1] * y + coef[2, 1]
    c = np.floor((e - REGION[0]) / CELL).astype(np.int64)
    rr = np.floor((REGION[3] - n) / CELL).astype(np.int64)
    ins = (c >= 0) & (c < COLS) & (rr >= 0) & (rr < ROWS)
    g = np.full(e.shape, np.nan)
    g[ins] = dtm[rr[ins], c[ins]]
    hag = z - g
    m = ins & (((cls == 1) & (hag > 2.5)) | ((cls != 1) & (hag > 6.0)))
    return dict(e=e[m].astype(np.float32), n=n[m].astype(np.float32), z=z[m].astype(np.float32),
                hag=hag[m].astype(np.float32), cls=cls[m].astype(np.uint8))


def load_points(tiles: Path, rasters: Path, work: Path) -> dict:
    cache = work / "wires_points.npz"
    if cache.exists():
        d = np.load(cache)
        return {k: d[k] for k in d.files}
    paths = sorted(str(p) for p in tiles.glob("*.laz"))
    parts = []
    with ProcessPoolExecutor(16) as ex:
        for r in ex.map(_tile, [(p, str(rasters / "dtm.f32")) for p in paths]):
            parts.append(r)
    out = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    np.savez(cache, **out)
    return out


class Grid:
    def __init__(self, rasters: Path):
        self.dtm = np.fromfile(rasters / "dtm.f32", np.float32).reshape(ROWS, COLS)
        self.bld = np.fromfile(rasters / "bld.f32", np.float32).reshape(ROWS, COLS)

    def _rc(self, e, n):
        c = np.clip(((np.asarray(e) - REGION[0]) / CELL).astype(int), 0, COLS - 1)
        r = np.clip(((REGION[3] - np.asarray(n)) / CELL).astype(int), 0, ROWS - 1)
        return r, c

    def ground(self, e, n):
        r, c = self._rc(e, n)
        return self.dtm[r, c]

    def solid(self, e, n):
        r, c = self._rc(e, n)
        b = self.bld[r, c]
        return np.where(np.isfinite(b), np.maximum(b, self.dtm[r, c]), self.dtm[r, c])


# -------------------------------------------------------------- structures
class Pts:
    def __init__(self, d):
        from scipy.spatial import cKDTree
        self.d = d
        self.tree = cKDTree(np.column_stack([d["e"], d["n"]]))

    def near(self, e, n, r):
        idx = np.asarray(self.tree.query_ball_point([e, n], r), dtype=np.int64)
        return idx


def measure(P: Pts, e: float, n: float, d_line: np.ndarray, hint: str, grid: Grid) -> dict | None:
    """A structure's lidar centroid, top, kind, base width and arm rows near (e, n)."""
    D = P.d
    r0 = 3.5 if hint == "wood" else 9.0
    idx = P.near(e, n, r0)
    if idx.size == 0:
        return None
    c1 = idx[D["cls"][idx] == 1]
    use = c1 if (D["hag"][c1] > 4.0).sum() >= 25 else idx
    hag = D["hag"][use]
    if use.size < 8 or np.percentile(hag, 99.5) < 6.0:
        return None
    hmax = float(np.percentile(hag, 99.5))
    up = use[hag > 0.55 * hmax]
    ce, cn = float(np.median(D["e"][up])), float(np.median(D["n"][up]))
    for _ in range(2):    # re-centre on the column itself, away from trees at its edge
        dd = np.hypot(D["e"][up] - ce, D["n"][up] - cn)
        k = up[dd < (2.0 if hint == "wood" else 5.0)]
        if k.size >= 5:
            ce, cn = float(np.median(D["e"][k])), float(np.median(D["n"][k]))
    g = float(grid.ground(ce, cn))
    ra = 3.0 if hint == "wood" else 8.0
    idx = P.near(ce, cn, ra)
    c1 = idx[D["cls"][idx] == 1]
    use = c1 if (D["hag"][c1] > 4.0).sum() >= 25 else idx
    z = D["z"][use]
    top = float(np.percentile(z, 99.5))
    h = top - g
    v = np.array([-d_line[1], d_line[0]])
    t = (D["e"][use] - ce) * v[0] + (D["n"][use] - cn) * v[1]
    a = (D["e"][use] - ce) * d_line[0] + (D["n"][use] - cn) * d_line[1]
    zh = z - g
    # Body width: the lower third (legs of a lattice tower spread wide).
    low = (zh > 0.12 * h) & (zh < 0.45 * h) & (np.abs(t) < 6) & (np.abs(a) < 6)
    if low.sum() >= 12:
        wt = np.percentile(t[low], 95) - np.percentile(t[low], 5)
        wa = np.percentile(a[low], 95) - np.percentile(a[low], 5)
        body = float(max(wt, wa))
    else:
        body = 0.0
    if hint == "wood" or (h < 15.0 and body < 1.6):
        kind = "wood_pole" if h < 17.0 else "steel_pole"
    elif body > 2.4 and h > 16.0:
        kind = "lattice"
    else:
        kind = "steel_pole"
    # Arm rows: dense, wide horizontal bands in the upper part.
    upper = zh > 0.6 * h
    bins = np.arange(0.6 * h, h + 0.5, 0.25)
    arms = []
    if upper.sum() >= 10:
        cnt, _ = np.histogram(zh[upper], bins)
        sm = np.convolve(cnt, [1, 2, 1], "same") / 4.0
        ext = np.zeros(len(cnt))
        for i in range(len(cnt)):
            m = upper & (zh >= bins[i] - 0.25) & (zh < bins[i + 1] + 0.25)
            if m.sum() >= 4:
                ext[i] = np.percentile(t[m], 97) - np.percentile(t[m], 3)
        bodyx = np.percentile(ext[ext > 0], 25) if (ext > 0).any() else 0.0
        need = max(1.6, 1.5 * bodyx) if kind != "wood_pole" else 1.4
        thr = max(3.0, 0.22 * sm.max())
        for i in range(1, len(sm) - 1):
            if sm[i] >= thr and sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1] and ext[i] >= need:
                zc = 0.5 * (bins[i] + bins[i + 1])
                if arms and zc - arms[-1][0] < 1.2:
                    if sm[i] > arms[-1][3]:
                        arms[-1] = None
                        arms.pop()
                    else:
                        continue
                m = upper & (np.abs(zh - zc) < 0.4)
                left = float(-np.percentile(t[m][t[m] < 0], 5)) if (t[m] < 0).sum() >= 2 else 0.0
                right = float(np.percentile(t[m][t[m] > 0], 95)) if (t[m] > 0).sum() >= 2 else 0.0
                arms.append([float(zc), left, right, float(sm[i])])
    return dict(e=ce, n=cn, g=g, top=top, h=h, kind=kind, body=body, arms=arms, pts=int(use.size),
                off=float(math.hypot(ce - e, cn - n)))


# ------------------------------------------------------------------- OSM
def osm_lines(osm_path: Path):
    d = json.loads(osm_path.read_text())
    nodes = {e["id"]: e for e in d["elements"] if e["type"] == "node"}
    lines = []
    for w in d["elements"]:
        t = w.get("tags", {})
        if w["type"] != "way" or t.get("power") not in ("line", "minor_line"):
            continue
        pts = []
        for nid in w["nodes"]:
            nd = nodes.get(nid)
            if nd is None:
                continue
            e, n = geo_to_enu(nd["lat"], nd["lon"])
            pts.append(dict(osm=nid, e=e, n=n, tags=nd.get("tags", {})))
        inside = [i for i, p in enumerate(pts) if in_region(p["e"], p["n"])]
        if not inside:
            continue
        i0, i1 = max(inside[0] - 1, 0), min(inside[-1] + 1, len(pts) - 1)
        pts = [p for p in pts[i0:i1 + 1] if in_region(p["e"], p["n"], MARGIN)]
        if len(pts) < 2:
            continue
        v = t.get("voltage", "")
        volt = int(v.split(";")[0]) if v.split(";")[0].isdigit() else 0
        lines.append(dict(osm=w["id"], name=t.get("name", ""), voltage=volt, power=t["power"],
                          cables=int(t["cables"]) if t.get("cables", "").isdigit() else 0, pts=pts))
    return lines


def substations(osm_path: Path):
    """(centre, radius) of every power=substation area."""
    d = json.loads(osm_path.read_text())
    nodes = {e["id"]: e for e in d["elements"] if e["type"] == "node"}
    out = []
    for w in d["elements"]:
        if w["type"] == "way" and w.get("tags", {}).get("power") == "substation":
            xy = np.array([geo_to_enu(nodes[i]["lat"], nodes[i]["lon"]) for i in w["nodes"] if i in nodes])
            if len(xy):
                c = xy.mean(axis=0)
                out.append((c, float(np.max(np.linalg.norm(xy - c, axis=1)))))
    return out


def wire_class(line) -> str:
    v = line["voltage"]
    if v >= 100000:
        return "phase115"
    if v >= 40000:
        return "phase60"
    if v >= 5000:
        return "phase12"
    return "dist"


# -------------------------------------------------------- lidar-only chains
def tall_clusters(D) -> np.ndarray:
    """Slim, tall class-1 columns (e, n, hag top): structure candidates."""
    from scipy import ndimage
    m = (D["cls"] == 1) & (D["hag"] > 3.0)
    E, N, H = D["e"][m], D["n"][m], D["hag"][m]
    ci = ((E - REGION[0])).astype(int); ri = ((REGION[3] - N)).astype(int)
    W, Hh = int(REGION[2] - REGION[0]), int(REGION[3] - REGION[1])
    ok = (ci >= 0) & (ci < W) & (ri >= 0) & (ri < Hh)
    occ = np.zeros((Hh, W), bool)
    occ[ri[ok], ci[ok]] = True
    lab, nl = ndimage.label(occ, structure=np.ones((3, 3)))
    idx = lab[ri[ok], ci[ok]]
    cnt = np.bincount(idx, minlength=nl + 1)
    cells = np.bincount(lab.ravel(), minlength=nl + 1)
    top = np.zeros(nl + 1); np.maximum.at(top, idx, H[ok])
    lo = np.full(nl + 1, 99.0); np.minimum.at(lo, idx, H[ok])
    se = np.bincount(idx, E[ok], nl + 1); sn = np.bincount(idx, N[ok], nl + 1)
    k = np.arange(nl + 1)
    sel = (k > 0) & (cnt >= 15) & (top >= 12.0) & (top - lo >= 6.0) & (lo <= 5.0) & (cells <= 140)
    return np.column_stack([se[sel] / cnt[sel], sn[sel] / cnt[sel], top[sel]])


def extend_chain(a, b, cands, used, blocked, med_span, med_h):
    """Walk on from the structure at b (arriving from a) to lidar structures in
    line with it: slim class-1 columns of the line's height, about a span on,
    within 40 degrees of straight ahead, clear of every other line."""
    added = []
    a = np.asarray(a, float); b = np.asarray(b, float)
    free = np.array([i not in used for i in range(len(cands))])
    d0 = (b - a) / max(np.linalg.norm(b - a), 1e-6)
    for _ in range(30):
        d = (b - a) / max(np.linalg.norm(b - a), 1e-6)
        rel = cands[:, :2] - b
        dist = np.linalg.norm(rel, axis=1)
        cosang = (rel @ d) / np.maximum(dist, 1e-6)
        cos0 = (rel @ d0) / np.maximum(dist, 1e-6)
        ok = free & (dist > 0.3 * med_span) & (dist < 2.3 * med_span) & (cosang > math.cos(math.radians(40))) \
            & (cos0 > math.cos(math.radians(50))) \
            & (cands[:, 2] > 0.7 * med_h) & (cands[:, 2] < 1.4 * med_h) & ~blocked
        if not ok.any():
            break
        score = np.where(ok, np.abs(dist - med_span) / med_span + 2.0 * (1 - cosang), np.inf)
        j = int(np.argmin(score))
        used.add(j)
        free[j] = False
        added.append(dict(osm=0, e=float(cands[j, 0]), n=float(cands[j, 1]), tags={}, lidar_only=True))
        a, b = b, cands[j, :2].copy()
        if not in_region(b[0], b[1]):
            break
    return added


def near_segments(pts_xy: np.ndarray, segs: list, r: float) -> np.ndarray:
    """True where a point lies within r of any segment ((a, b) pairs)."""
    out = np.zeros(len(pts_xy), bool)
    for a, b in segs:
        a = np.asarray(a); b = np.asarray(b); ab = b - a
        L2 = max(float(ab @ ab), 1e-9)
        t = np.clip(((pts_xy - a) @ ab) / L2, 0, 1)
        q = a + np.outer(t, ab)
        out |= np.linalg.norm(pts_xy - q, axis=1) < r
    return out


# ------------------------------------------------------------- catenary
def catenary(a, b, c, step=8.0):
    """Points along the catenary from a to b (ENU + height) with parameter c."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    hv = b[:2] - a[:2]
    L = float(np.linalg.norm(hv))
    if L < 0.5:
        return np.array([a, b])
    dz = b[2] - a[2]
    x0 = L / 2 - c * math.asinh(dz / (2 * c * math.sinh(L / (2 * c))))
    k = max(2, int(math.ceil(L / step)))
    s = np.linspace(0, L, k + 1)
    z = a[2] + c * (np.cosh((s - x0) / c) - np.cosh(x0 / c))
    xy = a[:2] + np.outer(s / L, hv)
    return np.column_stack([xy, z])


# ------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tiles", required=True)
    ap.add_argument("--rasters", required=True)
    ap.add_argument("--osm", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    a = ap.parse_args(argv)
    work = Path(a.work); work.mkdir(parents=True, exist_ok=True)
    grid = Grid(Path(a.rasters))
    D = load_points(Path(a.tiles), Path(a.rasters), work)
    P = Pts(D)
    cands = tall_clusters(D)
    print(f"{len(D['e'])} points above ground; {len(cands)} tall slim class-1 columns")

    lines = osm_lines(Path(a.osm))
    print(f"{len(lines)} OSM power lines reach the region")
    # Structure table, shared by every line through a node.
    S: list[dict] = []
    by_osm: dict[int, int] = {}
    used = set()

    def struct_index(p):
        if p["osm"] and p["osm"] in by_osm:
            return by_osm[p["osm"]]
        S.append(dict(osm=p["osm"], e0=p["e"], n0=p["n"], tags=p["tags"], lidar_only=p.get("lidar_only", False),
                      lines=[]))
        if p["osm"]:
            by_osm[p["osm"]] = len(S) - 1
        return len(S) - 1

    def nearest_cand(e, n, r=6.0):
        dd = np.hypot(cands[:, 0] - e, cands[:, 1] - n)
        j = int(np.argmin(dd)) if len(dd) else -1
        return j if j >= 0 and dd[j] < r else -1

    # Mark candidates OSM already has.
    for ln in lines:
        for p in ln["pts"]:
            j = nearest_cand(p["e"], p["n"], 10.0)
            if j >= 0:
                used.add(j)
    report = Counter()
    # Where a line may NOT be walked on: ends shared with another line
    # (junctions), ends at a substation, and candidates beside another line.
    node_use = Counter(p["osm"] for ln in lines for p in ln["pts"])
    subs = substations(Path(a.osm))
    all_segs = [((ln["pts"][i]["e"], ln["pts"][i]["n"]), (ln["pts"][i + 1]["e"], ln["pts"][i + 1]["n"]))
                for ln in lines for i in range(len(ln["pts"]) - 1)]
    all_xy = np.array([(p["e"], p["n"]) for ln in lines for p in ln["pts"]])

    def blocked_for(own_segs):
        others = [sg for sg in all_segs if sg not in own_segs]
        b = near_segments(cands[:, :2], others, 20.0)
        dd = np.min(np.linalg.norm(cands[:, None, :2] - all_xy[None], axis=2), axis=1)
        return b | (dd < 25.0)

    def at_substation(p):
        return any(np.linalg.norm(np.asarray(c) - [p["e"], p["n"]]) < r + 40.0 for c, r in subs)

    def stats(pts):
        spans = [math.hypot(pts[i + 1]["e"] - pts[i]["e"], pts[i + 1]["n"] - pts[i]["n"]) for i in range(len(pts) - 1)]
        hs = [cands[j, 2] for p in pts if in_region(p["e"], p["n"]) for j in [nearest_cand(p["e"], p["n"], 10.0)] if j >= 0]
        return (float(np.median(spans)) if spans else 100.0), (float(np.median(hs)) if hs else 0.0)

    branches = []
    for li, ln in enumerate(lines):
        pts = ln["pts"]
        med_span, med_h = stats(pts)
        if med_h > 0 and (ln["power"] == "line" or ln["voltage"] >= 5000):
            # Gaps: a span far longer than the line's own is missing a structure.
            own0 = [((pts[i]["e"], pts[i]["n"]), (pts[i + 1]["e"], pts[i + 1]["n"])) for i in range(len(pts) - 1)]
            blocked0 = blocked_for(set(own0))
            n_sp = sum(1 for i in range(len(pts) - 1) if in_region(pts[i]["e"], pts[i]["n"]))
            gap = 1.6 * (med_span if n_sp >= 4 else min(med_span, TYPICAL_SPAN[wire_class(ln)]))
            filled = [pts[0]]
            for i in range(len(pts) - 1):
                A, B = np.array([pts[i]["e"], pts[i]["n"]]), np.array([pts[i + 1]["e"], pts[i + 1]["n"]])
                L = np.linalg.norm(B - A)
                if L > gap:
                    u = (B - A) / L
                    rel = cands[:, :2] - A
                    sa = rel @ u; tt = rel @ np.array([-u[1], u[0]])
                    ok = (sa > 0.1 * L) & (sa < 0.9 * L) & (np.abs(tt) < 15) & (cands[:, 2] > 0.6 * med_h) \
                        & (cands[:, 2] < 1.4 * med_h) & ~blocked0
                    for j in np.where(ok)[0][np.argsort(sa[ok])]:
                        if int(j) not in used:
                            used.add(int(j))
                            filled.append(dict(osm=0, e=float(cands[j, 0]), n=float(cands[j, 1]), tags={}, lidar_only=True))
                            report["gap_structures"] += 1
                filled.append(pts[i + 1])
            pts = filled
            own = [((pts[i]["e"], pts[i]["n"]), (pts[i + 1]["e"], pts[i + 1]["n"])) for i in range(len(pts) - 1)]
            blocked = blocked_for(set(own))
            ends = []
            for fwd in (True, False):
                q = pts if fwd else pts[::-1]
                if len(q) < 2:
                    continue
                end = q[-1]
                if not in_region(end["e"], end["n"]):
                    continue
                last = math.hypot(q[-1]["e"] - q[-2]["e"], q[-1]["n"] - q[-2]["n"])
                if len(q) >= 3 and last < 0.4 * med_span:
                    # A short drop to a pole at the end (a tap): the tower line
                    # itself may run on, straight, from the one before.
                    br = extend_chain((q[-3]["e"], q[-3]["n"]), (q[-2]["e"], q[-2]["n"]), cands, used, blocked,
                                      med_span, med_h)
                    if br:
                        branches.append(dict(ln, osm=0, name=f"lidar: on from OSM {ln['osm']}",
                                             pts=[q[-2]] + br, branch_of=ln["osm"]))
                        print(f"  line {ln['osm']}: branch of {len(br)} lidar structures on from its tower")
                        report["chain_structures"] += len(br)
                    continue
                if at_substation(end):
                    continue
                if node_use[end["osm"]] > 1:
                    # A junction: walk on only where no other line already leaves it that way.
                    here = np.array([end["e"], end["n"]])
                    ahead = here - np.array([q[-2]["e"], q[-2]["n"]])
                    ahead /= max(np.linalg.norm(ahead), 1e-6)
                    taken = False
                    for other in lines:
                        if other is ln:
                            continue
                        for i, p in enumerate(other["pts"]):
                            if p["osm"] != end["osm"]:
                                continue
                            for o in (i - 1, i + 1):
                                if 0 <= o < len(other["pts"]):
                                    v = np.array([other["pts"][o]["e"], other["pts"][o]["n"]]) - here
                                    if v @ ahead > math.cos(math.radians(45)) * np.linalg.norm(v):
                                        taken = True
                    if taken:
                        continue
                ext = extend_chain((q[-2]["e"], q[-2]["n"]), (end["e"], end["n"]), cands, used, blocked, med_span, med_h)
                if ext:
                    print(f"  line {ln['osm']}: +{len(ext)} lidar structures past its OSM {'end' if fwd else 'start'}")
                    report["chain_structures"] += len(ext)
                ends.append((fwd, ext))
            for fwd, ext in ends:
                pts = pts + ext if fwd else ext[::-1] + pts
        ln["pts"] = pts
    lines += branches
    for li, ln in enumerate(lines):
        ln["si"] = [struct_index(p) for p in ln["pts"]]
        for k in ln["si"]:
            S[k]["lines"].append(li)

    # Line direction at each structure (bisector of its spans), then measure.
    for k, s in enumerate(S):
        dirs = []
        for li in s["lines"]:
            si = lines[li]["si"]
            for j, q in enumerate(si):
                if q != k:
                    continue
                for o in (j - 1, j + 1):
                    if 0 <= o < len(si):
                        v = np.array([S[si[o]]["e0"] - s["e0"], S[si[o]]["n0"] - s["n0"]])
                        if np.linalg.norm(v) > 1:
                            dirs.append(v / np.linalg.norm(v) * (1 if o > j else -1))
        if dirs:
            d = np.sum(dirs, axis=0)
            if np.linalg.norm(d) < 1e-3:
                d = dirs[0]
            d = d / np.linalg.norm(d)
        else:
            d = np.array([1.0, 0.0])
        s["dir"] = d
        # Deviation angle: a corner structure is a dead-end (strain) structure.
        s["angle"] = 0.0
        if len(dirs) >= 2:
            s["angle"] = math.degrees(math.acos(float(np.clip(np.dot(dirs[0], dirs[1]), -1, 1))))
        wood = s["tags"].get("material") == "wood" or (
            all(lines[li]["power"] == "minor_line" and lines[li]["voltage"] < 5000 for li in s["lines"])
            and s["tags"].get("power", "pole") != "tower" and not s["lidar_only"])
        s["hint"] = "wood" if wood else "steel"
        m = measure(P, s["e0"], s["n0"], d, s["hint"], grid) if in_region(s["e0"], s["n0"], -5) else None
        s["m"] = m

    # A lidar-only structure must measure like its line (a parking-lot light
    # is a slim column too): the walk stops at the first one that does not.
    for ln in lines:
        si = ln["si"]
        hs = [S[k]["m"]["h"] for k in si if S[k]["m"] is not None and not S[k]["lidar_only"]]
        if not hs:
            continue
        mh = float(np.median(hs))
        bad = [i for i, k in enumerate(si) if S[k]["lidar_only"] and (S[k]["m"] is None or S[k]["m"]["h"] < 0.7 * mh)]
        osm_i = [i for i, k in enumerate(si) if not S[k]["lidar_only"]]
        lo, hi = 0, len(si)
        for i in bad:
            if osm_i and i < osm_i[0]:
                lo = max(lo, i + 1)
            elif osm_i and i > osm_i[-1]:
                hi = min(hi, i)
        if lo or hi < len(si):
            report["chain_structures_rejected"] += lo + len(si) - hi
            ln["si"] = si[lo:hi]
    keep = sorted({k for ln in lines for k in ln["si"]})
    remap = {k: i for i, k in enumerate(keep)}
    S[:] = [S[k] for k in keep]
    for ln in lines:
        ln["si"] = [remap[k] for k in ln["si"]]
    for s_ in S:
        s_["lines"] = [li for li, ln in enumerate(lines) if any(S[k] is s_ for k in ln["si"])]

    # Per-line templates from structures whose arm count is the line's commonest.
    out_struct = []
    for k, s in enumerate(S):
        s["out"] = None
    for li, ln in enumerate(lines):
        ms = [S[k]["m"] for k in ln["si"] if S[k]["m"] is not None]
        kinds = Counter(m["kind"] for m in ms)
        ln["kind"] = kinds.most_common(1)[0][0] if kinds else ("wood_pole" if wire_class(ln) == "dist" else "lattice")
        hs = [m["h"] for m in ms if m["kind"] == ln["kind"]]
        ln["med_h"] = float(np.median(hs)) if hs else (11.0 if ln["kind"] == "wood_pole" else 30.0)
        arm_sets = [m["arms"] for m in ms if m["kind"] == ln["kind"] and m["arms"]]
        counts = Counter(min(len(x), 4) for x in arm_sets)
        want = ln["cables"] // 2 if ln["cables"] >= 6 else 0
        n_arm = want if want and counts.get(want) else (counts.most_common(1)[0][0] if counts else 0)
        if ln["kind"] == "wood_pole":
            n_arm = 1
        if ln.get("branch_of") is not None:
            n_arm = 3       # the marsh line's towers show three arm rows (see their points)
        n_arm = min(n_arm, 3)
        if wire_class(ln) == "phase115" and n_arm < 3:
            n_arm = 3       # 115 kV double circuits here carry three arm levels a side
        if n_arm == 0:
            n_arm = 3 if wire_class(ln) == "phase115" else 1
        ln["n_arm"] = n_arm
        # Template: offsets below the top and reaches, median over agreeing structures.
        agree = [m for m in ms if m["kind"] == ln["kind"] and len(m["arms"]) == n_arm]
        if agree:
            offs = np.array([[m["top"] - x[0] for x in sorted(m["arms"], key=lambda r: -r[0])[:n_arm]] for m in agree])
            reach = np.array([[0.5 * (x[1] + x[2]) if min(x[1], x[2]) > 0.3 else max(x[1], x[2])
                               for x in sorted(m["arms"], key=lambda r: -r[0])[:n_arm]] for m in agree])
            ln["tpl"] = [(float(np.median(offs[:, i])), float(np.median(reach[:, i]))) for i in range(n_arm)]
        else:
            ln["tpl"] = DEFAULT_TPL[ln["kind"]][:n_arm]
        if ln["kind"] == "wood_pole" and ln["tpl"][0][0] > 1.6:
            ln["tpl"] = DEFAULT_TPL["wood_pole"]
        if ln.get("branch_of") is not None:
            par = next(x for x in lines if x["osm"] == ln["branch_of"])
            ln["tpl"], agree = par["tpl"], par.get("agree", agree)
        ln["agree"] = agree
        if any(off > 0.45 * ln["med_h"] for off, _ in ln["tpl"]):
            ln["tpl"] = DEFAULT_TPL[ln["kind"]][:n_arm]     # rows that deep are not arms
            agree = []
        ln["tpl_measured"] = bool(agree)
        print(f"  line {ln['osm']} {ln['name'][:28]!r} {ln['voltage']} V: {len(ln['si'])} structures, "
              f"{ln['kind']} x{n_arm} arms, tpl {[(round(o, 1), round(r, 1)) for o, r in ln['tpl']]} "
              f"from {len(agree)} towers, med h {ln['med_h']:.1f}")

    # Resolve every structure once, with the layout of the line that owns it first.
    for k, s in enumerate(S):
        ln = lines[s["lines"][0]]
        m = s["m"]
        g = float(m["g"]) if m else float(grid.ground(s["e0"], s["n0"]))
        top = float(m["top"]) if m else g + ln["med_h"]
        kind = ln["kind"]
        if m is not None:
            kind = m["kind"]
            # A sparse lattice tower (few returns on its legs) reads as a pole.
            if kind == "steel_pole" and ln["kind"] == "lattice" and m["h"] > 0.7 * ln["med_h"]:
                kind = "lattice"
        n_arm, tpl = ln["n_arm"], ln["tpl"]
        if kind != ln["kind"]:
            n_arm, tpl = (1, [(0.5, 1.2)]) if kind == "wood_pole" else (n_arm, DEFAULT_TPL[kind][:n_arm])
        own = sorted(m["arms"], key=lambda r: -r[0])[:n_arm] if m else []
        measured_arms = m is not None and len(own) == n_arm and (kind == ln["kind"] or kind == "wood_pole")
        if kind == "wood_pole" and own and top - (own[0][0] + g) > 1.6:
            measured_arms = False      # a "crossarm" deep below the top is a tree or a sign
        arms = []
        for i in range(n_arm):
            off, reach = tpl[i] if i < len(tpl) else tpl[-1]
            if measured_arms:
                z, l, r = own[i][0] + g, own[i][1], own[i][2]
                rr = 0.5 * (l + r) if min(l, r) > 0.3 else max(l, r)
                if not (0.4 * reach < rr < 1.8 * reach):
                    rr = reach
                arms.append([z, rr])
            else:
                arms.append([top - off, reach])
        cls = wire_class(ln)
        shield = cls in ("phase115", "phase60") and kind != "wood_pole"
        dead = s["angle"] > 12.0 or s["tags"].get("line_attachment") == "anchor" or \
            any(S[q] is s for li in s["lines"] for q in (lines[li]["si"][0], lines[li]["si"][-1]))
        base_w = 0.0
        if kind == "lattice":
            base_w = float(np.clip(m["body"], 4.0, 9.0)) if m and m["body"] > 0 else 6.0
        s["out"] = dict(e=round(m["e"] if m else s["e0"], 2), n=round(m["n"] if m else s["n0"], 2),
                        g=round(g, 2), top=round(top, 2), type=kind,
                        yaw=round(math.atan2(s["dir"][1], s["dir"][0]), 4), base_w=round(base_w, 2),
                        arms=[[round(z, 2), round(r, 2)] for z, r in arms], shield=shield, dead_end=bool(dead),
                        src=("lidar" if m else "osm") + ("+osm" if s["osm"] and m else "") if not s["lidar_only"] else "lidar-chain",
                        arms_src="lidar" if measured_arms else ("line-template" if ln["tpl_measured"] else "assumed"),
                        osm=s["osm"], off=round(m["off"], 2) if m else None, pts=m["pts"] if m else 0,
                        cls=cls)
        out_struct.append(s["out"])
        report["structures"] += 1
        report["src_" + s["out"]["src"]] += 1
        report["arms_" + s["out"]["arms_src"]] += 1

    # Conductors.
    wires = []
    low_clear = []
    for li, ln in enumerate(lines):
        cls = wire_class(ln)
        for j in range(len(ln["si"]) - 1):
            A, B = S[ln["si"][j]]["out"], S[ln["si"][j + 1]]["out"]
            if A is B:
                continue
            L = math.hypot(B["e"] - A["e"], B["n"] - A["n"])
            if L < 3.0 or L > 600.0:
                continue

            def attach(st, side, lvl, nlev):
                arms = st["arms"]
                i = min(len(arms) - 1, int(round(lvl * (len(arms) - 1) / max(nlev - 1, 1)))) if nlev > 1 else 0
                z, reach = arms[i]
                yaw = st["yaw"]
                v = np.array([-math.sin(yaw), math.cos(yaw)])   # left of travel
                ins = INSULATOR[cls if st["type"] != "wood_pole" else "dist"]
                if st["type"] == "wood_pole":
                    off = [-reach, 0.35, reach][lvl] if nlev == 3 else 0.0
                    zz = z - ins
                else:
                    off = side * reach
                    zz = z if st["dead_end"] else z - ins
                return [st["e"] + v[0] * off, st["n"] + v[1] * off, zz]

            # Travel direction for this span flips "left" if the structure's yaw points back.
            d = np.array([B["e"] - A["e"], B["n"] - A["n"]]) / L
            sa = 1 if np.dot(d, [math.cos(A["yaw"]), math.sin(A["yaw"])]) >= 0 else -1
            sb = 1 if np.dot(d, [math.cos(B["yaw"]), math.sin(B["yaw"])]) >= 0 else -1
            phases = []
            if A["type"] == "wood_pole" or B["type"] == "wood_pole" or cls == "dist":
                for p in range(3):
                    pa = attach(A, 0, p, 3) if A["type"] == "wood_pole" else attach(A, [-1, 1, 1][p], 0, 1)
                    pb = attach(B, 0, p, 3) if B["type"] == "wood_pole" else attach(B, [-1, 1, 1][p], 0, 1)
                    phases.append(("dist" if cls == "dist" else cls, pa, pb))
            else:
                nl = max(len(A["arms"]), len(B["arms"]))
                for side in (-1, 1):
                    for lvl in range(nl):
                        pa = attach(A, side * sa, lvl, nl)
                        pb = attach(B, side * sb, lvl, nl)
                        phases.append((cls, pa, pb))
                if A["shield"] and B["shield"]:
                    phases.append(("shield", [A["e"], A["n"], A["top"]], [B["e"], B["n"], B["top"]]))
            for kind, pa, pb in phases:
                c = SAG[kind]
                for _ in range(12):
                    pl = catenary(pa, pb, c)
                    clear = float(np.min(pl[:, 2] - grid.ground(pl[:, 0], pl[:, 1])))
                    if clear >= CLEAR[kind] or c > 6 * SAG[kind]:
                        break
                    c *= 1.25
                if c != SAG[kind]:
                    report["sag_stiffened"] += 1
                sag = float(np.max(np.linspace(pa[2], pb[2], len(pl)) - pl[:, 2]))
                if clear < CLEAR[kind]:
                    low_clear.append((ln["osm"], j, kind, round(clear, 1)))
                over_b = float(np.min(pl[:, 2] - grid.solid(pl[:, 0], pl[:, 1])))
                if over_b < 3.0:
                    report["wires_within_3m_of_a_roof"] += 1
                wires.append(dict(a=[round(x, 2) for x in pa], b=[round(x, 2) for x in pb], c=round(c, 1),
                                  r=RADIUS[kind], kind=kind, line=li, s=[ln["si"][j], ln["si"][j + 1]],
                                  sag=round(sag, 2), clear=round(clear, 1)))
                report["wires_" + kind] += 1
            report["spans"] += 1

    offs = [s["off"] for s in out_struct if s["off"] is not None and s["src"] != "lidar-chain"]
    print("report:", dict(report))
    print(f"OSM -> lidar structure offset: median {np.median(offs):.2f} m, p90 {np.percentile(offs, 90):.2f} m")
    if low_clear:
        print(f"{len(low_clear)} conductors still below GO 95 clearance (min 6 shown): {sorted(low_clear, key=lambda x: x[3])[:6]}")
    meta = dict(
        about="Power lines of the Baylands scene: structures measured from the 2020 USGS lidar at OSM routes "
              "(and lidar-only chains); conductors inferred (the lidar delivery holds no conductor returns), "
              "catenaries z(s) between a and b with parameter c = H/w metres. ENU metres about GEO_DATUM, "
              "heights metres above NAVD88. Made by drone/sim/tools/bake_baylands_wires.py.",
        sources=["OpenStreetMap power=line/minor_line, tower/pole nodes (ODbL)",
                 "USGS 3DEP CA_SantaClaraCounty_2020 lidar (public domain)"],
        sag_c=SAG, clearance_m=CLEAR,
        lines=[dict(osm=ln["osm"], name=ln["name"], voltage=ln["voltage"], kind=ln["kind"], cls=wire_class(ln),
                    structures=ln["si"]) for ln in lines],
        structures=[{k: v for k, v in s.items() if k not in ("pts",)} for s in out_struct],
        wires=wires,
        report=dict(report),
    )
    Path(a.out).write_text(json.dumps(meta, separators=(",", ":")))
    print(f"wrote {a.out}: {len(out_struct)} structures, {len(wires)} conductors, {Path(a.out).stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
