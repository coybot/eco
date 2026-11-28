#!/usr/bin/env python3
"""Bake the Baylands built environment from the measured data: roof shapes,
park and street furniture, poles, fences, bridge decks, parked cars.

Step 3 of the Baylands pipeline (after fetch_baylands.py and
bake_baylands_lidar.py). Runs where the 0.5 m lidar grids live (a big Linux box), never
on a laptop: it holds several 96 M-cell grids and the 0.6 m NAIP photo.

    uv run --no-project -q --with numpy --with scipy --with shapely \\
        --with rasterio --with pillow --with "laspy[lazrs]" --with pyproj \\
        python bake_baylands_built.py --rasters ~/bay/data/rasters \\
        --hi ~/bay/out/_hi --assets drone/sim/godot/assets/baylands \\
        --work ~/bay/bl-built/work --out drone/sim/godot/assets/baylands \\
        [--tiles ~/bay/data/copc] [--steps c1,roofs,things]

WRITES (committed, lean; read by scripts/baylands_buildings.gd and
scripts/baylands_built.gd):
    built_roofs.dat   every footprint's roof, measured from the lidar inside it
                      (format below)
    built_things.json poles, light standards, shelters, playgrounds, benches,
                      fences and walls, bridge decks, ride blobs, parked cars

MEASURED, NOT GUESSED:
  * Roofs: the highest class-6 return per 0.5 m cell inside each footprint.
    A roof is FLAT (one or more measured levels: tiers, each a traced polygon
    at its own height, a parapet where the edge ring stands above the roof,
    and rooftop units/penthouses: every cluster of cells standing >0.5 m above
    its level, as an oriented box at its measured top) or PITCHED (the
    footprint covered by up to 4 rectangles in its own principal frame, each
    hipped or gabled; the common eave and pitch fitted to the cells by least
    squares, the hip-vs-gable choice by the smaller residual).
  * Roof colour: the median NAIP 2024 colour inside the eroded footprint.
  * Poles, light standards, flagpoles, signs: class-1 (unclassified) columns,
    i.e. returns spanning low and high bands in one cell neighbourhood; height
    = the highest return. Fences: class-1 returns 0.3-3 m along OSM barriers.
  * Bridge decks: class-17 returns (deck tops), along the OSM bridge ways.
  * Parked cars are the one invented layer: stalls laid out in the OSM car
    parks along each lot's measured axis, filled to a plausible weekday
    occupancy, skipping cells the lidar or the photo say are trees, islands
    or structures.

built_roofs.dat ("BLR1", little-endian):
    u32 n (= buildings.dat count, same order)
    per building:
      u8  type        0 none (keep the base prism), 1 flat, 2 pitched,
                      3 measured surface (sloped roofs no plane model fits)
      u8  wall        facade class (WALL_* below)
      u8  r, g, b     roof colour (NAIP)
      u8  parapet     flat: parapet height above the lowest tier, 0.05 m units
      u16 z0          flat: lowest tier's roof; pitched: eave   (zq)
      u16 z1          the highest drawn point (tiers, units, ridge) (zq)
      u8  n_parts     flat: tiers above the lowest; pitched: rectangles
      u16 n_units
      parts (pitched rectangle, 12 B): s16 cx, s16 cy (0.2 m), u8 angle
          (long axis, 180/256 deg), u16 half_len, u16 half_wid (cm),
          u8 kind (0 hip, 1 gable), u16 ridge (zq)
      parts (flat tier): u8 nv, nv x (s16, s16) (0.2 m), u16 top (zq)
      part (surface, type 3): s16 e0, s16 n0 (0.1 m: the NW 1 m post), u8 w,
          u8 h, u16 base (zq), w*h u8 decimetres above base (255 = none),
          rows north -> south
      units (11 B): s16 cx, s16 cy (0.2 m), u8 half_len, u8 half_wid
          (0.1 m), u8 angle, u16 bottom (zq), u16 top (zq)
    zq = round((z + 10) * 50): metres above NAVD88 at 2 cm.

Sources: USGS 3DEP 2020 lidar (public domain), USDA NAIP 2024 (public
domain), OpenStreetMap (ODbL). See assets/baylands/CREDITS.md.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import time
import zlib
from pathlib import Path

import numpy as np

REGION = (-2500.0, -2400.0, 2500.0, 2400.0)
CELL = 0.5
ROWS, COLS = 9600, 10000

WALL_HOUSE, WALL_APART, WALL_OFFICE_GLASS, WALL_OFFICE_BAND, WALL_TILTUP, WALL_SHOP, \
    WALL_GARAGE, WALL_BLOCK, WALL_METAL, WALL_HOTEL = range(10)
DEBUG = False
KIND_NAMES = ["commercial", "industrial", "warehouse", "residential", "public", "shelter",
              "restroom", "utility", "tank", "roof", "other"]


def zq(z: float) -> int:
    return int(max(0, min(65535, round((z + 10.0) * 50.0))))


def q01(v: float) -> int:
    return int(max(-32768, min(32767, round(v / 0.1))))


def q02(v: float) -> int:
    return int(max(-32768, min(32767, round(v / 0.2))))


# ------------------------------------------------------------------ data

class Grids:
    def __init__(self, d: Path):
        self.d = d

    def f32(self, name):
        return np.memmap(self.d / f"{name}.f32", np.float32, "r", shape=(ROWS, COLS))

    def u8(self, name, d=None):
        return np.memmap((d or self.d) / f"{name}.u8", np.uint8, "r", shape=(ROWS, COLS))


def load_buildings(assets: Path):
    raw = (assets / "buildings.dat").read_bytes()
    assert raw[:4] == b"IST1"
    n, nv, q = struct.unpack("<IIf", raw[4:16])
    recs = []
    vbase = 16 + n * 16
    for i in range(n):
        first, cnt, h, mh, kind, flags, lv, col, nm = struct.unpack("<IHHHBBBBH", raw[16 + i * 16:32 + i * 16])
        v = np.frombuffer(raw, np.int16, cnt * 2, vbase + first * 4).reshape(-1, 2).astype(np.float64) * q
        recs.append({"ring": v, "h": h / 10.0, "kind": kind, "flags": flags, "levels": lv, "name": nm})
    meta = json.loads((assets / "buildings.json").read_text())
    return recs, meta


def cell_window(bounds, pad=2):
    e0, n0, e1, n1 = bounds
    r0 = max(0, int((REGION[3] - n1) / CELL) - pad)
    r1 = min(ROWS, int((REGION[3] - n0) / CELL) + pad + 1)
    c0 = max(0, int((e0 - REGION[0]) / CELL) - pad)
    c1 = min(COLS, int((e1 - REGION[0]) / CELL) + pad + 1)
    return r0, r1, c0, c1


def cell_centres(r0, r1, c0, c1):
    es = REGION[0] + (np.arange(c0, c1) + 0.5) * CELL
    ns = REGION[3] - (np.arange(r0, r1) + 0.5) * CELL
    return np.meshgrid(es, ns)


def poly_mask(ring, E, N):
    from matplotlib.path import Path as MPath
    p = MPath(ring)
    return p.contains_points(np.column_stack([E.ravel(), N.ravel()])).reshape(E.shape)


def dist_to_ring(ring, E, N):
    """Distance from each point to the polygon boundary."""
    P = np.column_stack([E.ravel(), N.ravel()])
    best = np.full(len(P), np.inf)
    for k in range(len(ring)):
        a = ring[k]; b = ring[(k + 1) % len(ring)]
        ab = b - a
        L2 = max(float(ab @ ab), 1e-9)
        t = np.clip(((P - a) @ ab) / L2, 0, 1)
        d = np.hypot(*(P - (a + t[:, None] * ab)).T)
        best = np.minimum(best, d)
    return best.reshape(E.shape)


def min_rect(pts):
    """Minimum-area rectangle: (centre, unit long axis, half_len, half_wid)."""
    from shapely.geometry import MultiPoint
    r = MultiPoint([tuple(p) for p in pts]).minimum_rotated_rectangle
    if r.geom_type != "Polygon":
        c = np.mean(pts, axis=0)
        return c, np.array([1.0, 0.0]), 0.25, 0.25
    c = np.array(r.exterior.coords[:4])
    e1 = c[1] - c[0]; e2 = c[2] - c[1]
    l1, l2 = np.hypot(*e1), np.hypot(*e2)
    if l1 >= l2:
        u = e1 / max(l1, 1e-9); hl, hw = l1 / 2, l2 / 2
    else:
        u = e2 / max(l2, 1e-9); hl, hw = l2 / 2, l1 / 2
    if u[0] < 0 or (abs(u[0]) < 1e-9 and u[1] < 0):
        u = -u
    return c.mean(axis=0), u, hl, hw


def ang_u8(u) -> int:
    a = math.degrees(math.atan2(u[1], u[0])) % 180.0
    return int(round(a / 180.0 * 256.0)) % 256


# ------------------------------------------------------------------ roofs

def rect_cover(ring, u, max_rects=4, res=0.25):
    """Greedy cover of a footprint by rectangles aligned to axis u (long axis).
    Returns [(centre_enu, half_len_along_u, half_wid)], largest first."""
    v = np.array([-u[1], u[0]])
    P = ring @ np.column_stack([u, v])            # (s, t) in the building frame
    s0, t0 = P.min(axis=0); s1, t1 = P.max(axis=0)
    ns = max(1, int(math.ceil((s1 - s0) / res))); nt = max(1, int(math.ceil((t1 - t0) / res)))
    if ns * nt > 1_500_000:
        return []
    S, T = np.meshgrid(s0 + (np.arange(ns) + 0.5) * res, t0 + (np.arange(nt) + 0.5) * res)
    from matplotlib.path import Path as MPath
    inside = MPath(P).contains_points(np.column_stack([S.ravel(), T.ravel()])).reshape(S.shape)
    total = inside.sum()
    if total == 0:
        return []
    covered = np.zeros_like(inside)
    out = []
    for _ in range(max_rects):
        # Largest all-inside rectangle that adds the most uncovered cells:
        # histogram method on `inside`, scored by new coverage (approximate:
        # score = area of the rect restricted to uncovered cells, evaluated
        # for the per-row maximal rectangles).
        best = None
        H = np.zeros(ns, np.int32)
        unc = (~covered) & inside
        cs = np.cumsum(np.cumsum(unc, 0), 1)

        def newcells(r0, r1, c0, c1):
            a = cs[r1 - 1, c1 - 1]
            if r0 > 0: a -= cs[r0 - 1, c1 - 1]
            if c0 > 0: a -= cs[r1 - 1, c0 - 1]
            if r0 > 0 and c0 > 0: a += cs[r0 - 1, c0 - 1]
            return a
        for r in range(nt):
            H = np.where(inside[r], H + 1, 0)
            stack = []
            for c in range(ns + 1):
                h = H[c] if c < ns else 0
                start = c
                while stack and stack[-1][1] >= h:
                    st, sh = stack.pop()
                    if sh > 0:
                        sc = newcells(r - sh + 1, r + 1, st, c)
                        if best is None or sc > best[0]:
                            best = (sc, r - sh + 1, r + 1, st, c)
                    start = st
                stack.append((start, h))
        if best is None or best[0] < max(16, 0.06 * total):
            break
        _, r0, r1, c0, c1 = best
        covered[r0:r1, c0:c1] = True
        sc = s0 + (c0 + c1) / 2 * res; tc = t0 + (r0 + r1) / 2 * res
        hl = (c1 - c0) * res / 2; hw = (r1 - r0) * res / 2
        centre = sc * u + tc * v
        out.append((centre, hl, hw))
        if covered.sum() >= 0.96 * total:
            break
    return out


def rect_roof_dist(E, N, centre, r, a, b, kind):
    """Inward 'run' of a rectangle roof at each point (the roof is eave +
    slope * run) and the inside mask. The ridge runs along unit vector r;
    a = half length along it, b = half width across. kind 0 hip, 1 gable."""
    v = np.array([-r[1], r[0]])
    s = (E - centre[0]) * r[0] + (N - centre[1]) * r[1]
    t = (E - centre[0]) * v[0] + (N - centre[1]) * v[1]
    inside = (np.abs(s) <= a + 0.25) & (np.abs(t) <= b + 0.25)
    run = np.clip(b - np.abs(t), 0, None)
    if kind == 0:
        run = np.minimum(run, np.clip(a - np.abs(s), 0, None))
    return run, inside


def fill_nan(z):
    from scipy import ndimage
    m = np.isfinite(z)
    if m.all() or not m.any():
        return z
    idx = ndimage.distance_transform_edt(~m, return_distances=False, return_indices=True)
    return z[tuple(idx)]


def roof_colour(ortho, ring, eroded_frac=0.6):
    e0, n0 = ring.min(axis=0); e1, n1 = ring.max(axis=0)
    H, W = ortho.shape[:2]
    res = 0.6
    c0 = max(0, int((e0 - REGION[0]) / res)); c1 = min(W, int((e1 - REGION[0]) / res) + 1)
    r0 = max(0, int((REGION[3] - n1) / res)); r1 = min(H, int((REGION[3] - n0) / res) + 1)
    if r1 <= r0 or c1 <= c0:
        return (128, 128, 128)
    es = REGION[0] + (np.arange(c0, c1) + 0.5) * res
    ns = REGION[3] - (np.arange(r0, r1) + 0.5) * res
    E, N = np.meshgrid(es, ns)
    m = poly_mask(ring, E, N)
    if m.sum() > 12:
        d = dist_to_ring(ring, E, N)
        m2 = m & (d > min(1.2, 0.25 * d[m].max()))
        if m2.sum() >= 4:
            m = m2
    px = ortho[r0:r1, c0:c1][m]
    if len(px) == 0:
        return (128, 128, 128)
    return tuple(int(x) for x in np.median(px, axis=0))


def landuse_index(features):
    from shapely.geometry import Polygon
    from shapely.strtree import STRtree
    polys, tags = [], []
    for a in features["areas"]:
        lu = a["tags"].get("landuse")
        if lu in ("residential", "commercial", "retail", "industrial") and len(a["pts"]) >= 4:
            p = Polygon(a["pts"])
            if p.is_valid and p.area > 100:
                polys.append(p); tags.append(lu)
    return STRtree(polys), polys, tags


def wall_class(rec, area, h, name, landuse):
    kind = rec["kind"]
    nm = (name or "").lower()
    if "parking" in nm or "garage" in nm:
        return WALL_GARAGE
    if "hotel" in nm or "hyatt" in nm or "hilton" in nm or "marriott" in nm or "aloft" in nm or "inn" in nm:
        return WALL_HOTEL
    if kind == 6:
        return WALL_BLOCK
    if kind in (7, 8):
        return WALL_METAL
    if kind == 3 or landuse == "residential":
        if area < 450 and h < 10.5:
            return WALL_HOUSE
        return WALL_APART
    if kind == 2:
        return WALL_TILTUP
    if landuse in ("commercial", "retail") and h < 9 and area < 4000:
        return WALL_SHOP
    if h >= 14:
        return WALL_OFFICE_GLASS if (int(abs(rec["ring"][0][0] * 13 + rec["ring"][0][1] * 7)) % 5) < 3 \
            else WALL_OFFICE_BAND
    if area > 2500 and h < 14:
        return WALL_TILTUP if (int(abs(rec["ring"][0][0] * 3 + rec["ring"][0][1] * 11)) % 4) else \
            WALL_OFFICE_BAND
    if area < 300 and h < 8 and landuse is None:
        return WALL_HOUSE if kind == 10 else WALL_SHOP
    return WALL_SHOP if h < 9 else WALL_OFFICE_BAND


def simplify_ring(geom, tol=0.4, maxv=40):
    g = geom.simplify(tol)
    while g.geom_type == "Polygon" and len(g.exterior.coords) - 1 > maxv and tol < 6:
        tol *= 1.6
        g = geom.simplify(tol)
    if g.geom_type != "Polygon" or g.is_empty:
        return None
    pts = np.array(g.exterior.coords[:-1])
    # CCW
    a = 0.5 * np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1])
    if a < 0:
        pts = pts[::-1]
    return pts


def analyse_roof(rec, g, ortho, landuse, name):
    """Returns (record bytes, stats dict)."""
    from scipy import ndimage
    ring = rec["ring"]
    e0, n0 = ring.min(axis=0); e1, n1 = ring.max(axis=0)
    r0, r1, c0, c1 = cell_window((e0, n0, e1, n1))
    E, N = cell_centres(r0, r1, c0, c1)
    m = poly_mask(ring, E, N)
    from shapely.geometry import Polygon
    poly = Polygon(ring)
    area = poly.area
    col = roof_colour(ortho, ring)
    wall = wall_class(rec, area, rec["h"], name, landuse)
    head = lambda t, p, z0_, z1_, np_, nu: struct.pack("<BBBBBBHHBH", t, wall, col[0], col[1], col[2], p,
                                                      zq(z0_), zq(z1_), np_, nu)
    if m.sum() < 3 or not (rec["flags"] & 1):
        return head(0, 0, 0, 0, 0, 0), {"type": "none"}
    z = np.array(g["bld"][r0:r1, c0:c1], np.float64)
    zin = np.where(m, z, np.nan)
    have = np.isfinite(zin)
    if have.sum() < 3:
        return head(0, 0, 0, 0, 0, 0), {"type": "none"}
    zf = fill_nan(np.where(m, z, np.nan) if have.any() else z)
    zf = np.where(m, zf, np.nan)
    vals = zf[m]
    d = dist_to_ring(ring, E, N)
    dmax = float(d[m].max())
    # ---- flat or pitched?
    zmed = ndimage.median_filter(np.nan_to_num(zf, nan=-999), 3)
    zmed = np.where(m, zmed, np.nan)
    hist_lo = np.nanpercentile(vals, 2)
    span = float(np.nanpercentile(vals, 97) - hist_lo)
    # Fraction of cells on any 0.5 m-wide level (peaks of the histogram).
    bins = np.arange(hist_lo - 0.25, np.nanmax(vals) + 0.5, 0.25)
    hcount, _ = np.histogram(vals, bins)
    win = np.convolve(hcount, np.ones(3), "same")       # +-0.375 m
    levels_frac = 0.0
    if len(win):
        cnt = win.copy(); used = 0
        for _ in range(3):
            k = int(np.argmax(cnt))
            if cnt[k] <= 0:
                break
            used += cnt[k]; cnt[max(0, k - 3):k + 4] = 0
        levels_frac = used / max(len(vals), 1)
    u = min_rect(ring)[1]
    pitched = False
    fit = None
    gy, gx = np.gradient(np.nan_to_num(zmed, nan=0.0), CELL)
    grad = np.hypot(gx, gy)
    inner = m & (d > 0.8) & np.isfinite(zmed)
    if inner.sum() > 4:
        inner &= ndimage.binary_erosion(m, iterations=2)
    sloped_frac = float(((grad > 0.12) & (grad < 1.6))[inner].mean()) if inner.sum() > 4 else 0.0
    if span > 0.7 and sloped_frac > 0.45 and dmax > 1.5 and area < 20000:
        rects = rect_cover(ring, u, max_rects=4 if area > 150 else 2)
        if rects:
            obs = m & have
            Eo, No, zo = E[obs], N[obs], z[obs]
            best = None
            vv = np.array([-u[1], u[0]])
            for kind, across in ((0, False), (1, False), (1, True)):
                runs = []
                for (cen, hl, hw) in rects:
                    if across:
                        run, ins = rect_roof_dist(Eo, No, cen, vv, hw, hl, kind)
                    else:
                        run, ins = rect_roof_dist(Eo, No, cen, u, hl, hw, kind)
                    runs.append(np.where(ins, run, -1.0))
                R = np.max(np.array(runs), axis=0)
                ok = R >= 0
                if ok.sum() < 6:
                    continue
                A = np.column_stack([np.ones(ok.sum()), R[ok]])
                # robust: two passes, trimming the worst 15% (chimneys, trees)
                coef, *_ = np.linalg.lstsq(A, zo[ok], rcond=None)
                res = np.abs(A @ coef - zo[ok])
                keep = res <= np.percentile(res, 85)
                coef, *_ = np.linalg.lstsq(A[keep], zo[ok][keep], rcond=None)
                rmse = float(np.sqrt(np.mean((A[keep] @ coef - zo[ok][keep]) ** 2)))
                if best is None or rmse < best[0]:
                    best = (rmse, kind, across, coef)
            if best is not None:
                rmse, kind, across, (eave, slope) = best
                flat_rmse = float(np.std(vals))
                maxrun = max((hl if across else hw) if kind == 1 else min(hl, hw) for _, hl, hw in rects)
                pitch_deg = math.degrees(math.atan(max(slope, 0)))
                if DEBUG:
                    print("fit", rec.get("_i"), "kind", kind, across, "eave %.2f slope %.3f pitch %.1f rmse %.2f flat %.2f maxrun %.1f rects %d"
                          % (eave, slope, pitch_deg, rmse, flat_rmse, maxrun, len(rects)))
                if 4.0 <= pitch_deg <= 55.0 and slope * maxrun > 0.4 and (rmse < 0.55 * flat_rmse or rmse < 0.25
                                                                   or (area < 250 and rmse < 0.8 * flat_rmse)):
                    pitched = True
                    fit = (rects, kind, across, eave, slope, rmse, pitch_deg)
    if not pitched and sloped_frac > 0.45 and area >= 250 and span > 0.7:
        return heightfield_roof(head, ring, E, N, m, zf, z, have), {"type": "field", "sloped": sloped_frac}
    if pitched:
        rects, kind, across, eave, slope, rmse, pitch_deg = fit
        parts = b""
        top = eave
        vv = np.array([-u[1], u[0]])
        for cen, hl, hw in rects:
            r, a, b = (vv, hw, hl) if across else (u, hl, hw)
            ridge = eave + slope * (b if kind == 1 else min(a, b))
            top = max(top, ridge)
            parts += struct.pack("<hhBHHBH", q02(cen[0]), q02(cen[1]), ang_u8(r),
                                 int(min(65535, a * 100)), int(min(65535, b * 100)), kind, zq(ridge))
        return head(2, 0, eave, top, len(rects), 0) + parts, \
            {"type": "pitched", "kind": kind + (1 if across else 0), "pitch": pitch_deg, "rmse": rmse,
             "rects": len(rects), "top": top, "eave": eave, "sloped": sloped_frac}
    # ---- flat: levels (tiers), parapet, units
    zz = np.where(m, zmed, np.nan)
    vals2 = zz[m]
    bins = np.arange(np.nanmin(vals2) - 0.25, np.nanmax(vals2) + 0.5, 0.25)
    hcount, edges = np.histogram(vals2, bins)
    win = np.convolve(hcount, np.ones(3), "same")
    cnt = win.astype(float).copy()
    levels = []
    min_cells = max(40, int(0.04 * len(vals2)))             # >= 10 m2 and 4% of the roof
    for _ in range(6):
        k = int(np.argmax(cnt))
        if cnt[k] < min_cells:
            break
        lv = 0.5 * (edges[k] + edges[k + 1])
        sel = np.abs(vals2 - lv) < 0.4
        levels.append(float(np.median(vals2[sel])))
        cnt[max(0, k - 5):k + 6] = 0
    if not levels:
        levels = [float(np.percentile(vals2, 50))]
    levels.sort()
    # Drop levels that are the parapet ring (cells mostly within 1 m of the edge).
    ring_m = m & (d < 1.0)
    keep_lv = []
    for lv in levels:
        cells = m & (np.abs(zz - lv) < 0.4)
        if cells.sum() and (cells & ring_m).sum() / cells.sum() > 0.7 and lv != levels[0]:
            continue
        keep_lv.append(lv)
    levels = keep_lv
    L0 = levels[0]
    # Cells assigned to the highest level they reach.
    assign = np.zeros(zz.shape, np.int8)
    for k, lv in enumerate(levels):
        assign[m & (zz >= lv - 0.45)] = k
    # parapet: the edge ring above the lowest level, where the ring is on L0
    ring0 = ring_m & (assign == 0)
    interior0 = m & (d >= 1.5) & (assign == 0)
    parapet = 0.0
    if ring0.sum() > 8 and interior0.sum() > 8:
        rv = np.nanpercentile(z[ring0 & have], 60) if (ring0 & have).sum() > 4 else L0
        iv = np.nanmedian(zz[interior0])
        if 0.2 < rv - iv < 2.5:
            parapet = float(rv - iv)
            L0 = float(iv)
    parts = b""
    nparts = 0
    tier_top = np.full(zz.shape, L0)
    from rasterio import features as rfeat
    from rasterio.transform import Affine
    from shapely.geometry import shape
    tf = Affine(CELL, 0, REGION[0] + c0 * CELL, 0, -CELL, REGION[3] - r0 * CELL)
    tiers = []
    for k in range(1, len(levels)):
        mk = (m & (assign >= k))
        mk = ndimage.binary_opening(mk, iterations=1)
        lab, nl = ndimage.label(mk)
        for li in range(1, nl + 1):
            comp = lab == li
            if comp.sum() < 40:
                continue
            top = float(np.median(zz[comp & (assign == k)])) if (comp & (assign == k)).any() else levels[k]
            for geom, val in rfeat.shapes(comp.astype(np.uint8), mask=comp, transform=tf):
                gp = shape(geom).intersection(poly)
                if gp.geom_type == "MultiPolygon":
                    gp = max(gp.geoms, key=lambda q: q.area)
                if gp.geom_type != "Polygon" or gp.area < 8:
                    continue
                pts = simplify_ring(gp, 0.4, 40)
                if pts is None or len(pts) < 3:
                    continue
                tiers.append((pts, top))
                tier_top[comp] = np.maximum(tier_top[comp], top)
    tiers.sort(key=lambda t: t[1])
    for pts, top in tiers[:40]:
        parts += struct.pack("<B", len(pts)) + b"".join(struct.pack("<hh", q02(p[0]), q02(p[1])) for p in pts) \
            + struct.pack("<H", zq(top))
        nparts += 1
    # Drawn height per cell so far: tier tops, the parapet on the ring.
    drawn = tier_top.copy()
    if parapet > 0:
        drawn = np.where(ring_m & (assign == 0), np.maximum(drawn, L0 + parapet), drawn)
    # Units: clusters of cells standing > 0.5 m above what is drawn there.
    above = m & have & (z > drawn + 0.5)
    above = ndimage.binary_closing(above, iterations=1) & m
    lab, nl = ndimage.label(above, structure=np.ones((3, 3)))
    units = b""
    nunits = 0
    top_all = max([L0 + parapet] + [t for _, t in tiers])
    objs = ndimage.find_objects(lab)
    for li, sl in enumerate(objs, 1):
        if sl is None:
            continue
        comp = lab[sl] == li
        zc = z[sl][comp & np.isfinite(z[sl])]
        if zc.size == 0:
            continue
        ztop = float(zc.max())
        ncell = int(comp.sum())
        hgt = ztop - float(np.median(drawn[sl][comp]))
        if ncell == 1 and hgt < 1.2:
            continue                               # a lone vent or a bird
        ee = E[sl][comp]; nn = N[sl][comp]
        pts = np.column_stack([ee, nn])
        # cells are 0.5 m squares: pad the cluster's rectangle by a half cell
        cen, uu, hl, hw = min_rect(np.vstack([pts + [dx, dy] for dx in (-0.25, 0.25) for dy in (-0.25, 0.25)]))
        if hl > 25 or hw > 25:
            continue
        bottom = float(np.median(drawn[sl][comp]))
        units += struct.pack("<hhBBBHH", q02(cen[0]), q02(cen[1]), int(min(255, math.ceil(hl * 10))),
                             int(min(255, math.ceil(hw * 10))), ang_u8(uu), zq(bottom), zq(ztop))
        nunits += 1
        top_all = max(top_all, ztop)
        if nunits >= 2000:
            break
    return head(1, int(min(255, round(parapet / 0.05))), L0, top_all, nparts, nunits) + parts + units, \
        {"type": "flat", "sloped": sloped_frac, "span": span, "levels": len(levels), "tiers": nparts, "units": nunits, "parapet": parapet,
         "top": top_all, "L0": L0}


def heightfield_roof(head, ring, E, N, m, zf, z, have):
    """A roof the planes do not fit: the measured surface itself, 1 m posts
    (median of the 0.5 m cells), u8 decimetres above its lowest post; 255 =
    outside the footprint. Part: s16 e0, s16 n0 (0.2 m, NW post), u8 w, u8 h,
    u16 base (zq), w*h u8."""
    from scipy import ndimage
    zs = ndimage.median_filter(np.where(m, zf, np.nan), 3, mode="nearest")
    zs = np.where(m, zs, np.nan)
    zs = fill_nan(zs)
    # 1 m posts at even cell corners: average 2x2 blocks
    h2, w2 = (zs.shape[0] // 2) * 2, (zs.shape[1] // 2) * 2
    blk = zs[:h2, :w2].reshape(h2 // 2, 2, w2 // 2, 2).mean(axis=(1, 3))
    mb = m[:h2, :w2].reshape(h2 // 2, 2, w2 // 2, 2).any(axis=(1, 3))
    mb = ndimage.binary_dilation(mb, iterations=1)
    if blk.shape[0] > 255 or blk.shape[1] > 255:
        return head(0, 0, 0, 0, 0, 0)
    base = float(np.nanmin(blk[mb]))
    q = np.where(mb, np.clip(np.round((blk - base) * 10), 0, 254), 255).astype(np.uint8)
    top = float(np.nanmax(blk[mb]))
    e_nw = float(E[0, 0] - 0.25 + 0.5)       # centre of the first 2x2 block
    n_nw = float(N[0, 0] + 0.25 - 0.5)
    part = struct.pack("<hhBBH", q01(e_nw), q01(n_nw), q.shape[1], q.shape[0], zq(base)) + q.tobytes()
    return head(3, 0, base, max(top, float(np.nanpercentile(z[m & have], 99.5))), 1, 0) + part


def step_roofs(args, g):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    assets = Path(args.assets)
    recs, meta = load_buildings(assets)
    names = meta.get("names", [])
    features = json.loads((assets / "features.json").read_text())
    tree, lpolys, ltags = landuse_index(features)
    from shapely.geometry import Point
    ortho = np.asarray(Image.open(Path(args.hi) / "ortho.jpg").convert("RGB"))
    out = bytearray(b"BLR1" + struct.pack("<I", len(recs)))
    stats = []
    t0 = time.time()
    for i, rec in enumerate(recs):
        if args.limit and i >= args.limit and i % 10:
            out += struct.pack("<BBBBBBHHBH", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
            continue
        c = rec["ring"].mean(axis=0)
        lu = None
        for j in tree.query(Point(c)):
            if lpolys[j].contains(Point(c)):
                lu = ltags[j]; break
        nm = names[rec["name"]] if rec["name"] != 65535 and rec["name"] < len(names) else None
        try:
            b, st = analyse_roof(rec, g, ortho, lu, nm)
        except Exception as exc:                       # noqa: BLE001 - keep the base prism
            print(f"  building {i}: {exc}", flush=True)
            b = struct.pack("<BBBBBBHHBH", 0, WALL_SHOP, 128, 128, 128, 0, 0, 0, 0, 0)
            st = {"type": "error"}
        st["i"] = i
        stats.append(st)
        out += b
        if i % 1000 == 0:
            print(f"  roofs {i}/{len(recs)} {time.time() - t0:.0f}s", flush=True)
    Path(args.out, "built_roofs.dat").write_bytes(bytes(out))
    from collections import Counter
    print("roofs:", Counter(s["type"] for s in stats), f"{len(out) / 1e6:.2f} MB")
    print("units:", sum(s.get("units", 0) for s in stats), "tiers:", sum(s.get("tiers", 0) for s in stats),
          "parapets:", sum(1 for s in stats if s.get("parapet", 0) > 0))
    Path(args.work, "roof_stats.json").write_text(json.dumps(stats))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rasters", required=True)
    ap.add_argument("--hi", required=True)
    ap.add_argument("--assets", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True, help="where the committed files are written")
    ap.add_argument("--tiles", default=str(Path.home() / "bay/data/copc"), help="lidar COPC tiles (step c1)")
    ap.add_argument("--steps", default="c1,roofs,things")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    gr = Grids(Path(a.rasters))
    g = {"bld": gr.f32("bld"), "dtm": gr.f32("dtm")}
    steps = a.steps.split(",")
    if "c1" in steps:
        step_c1(a)
    if "roofs" in steps:
        step_roofs(a, g)
    if "things" in steps:
        step_things(a, gr)
    return 0


# ------------------------------------------------------------ class-1 columns

def _utm_affine(x0, y0, x1, y1):
    """UTM 10N -> scene ENU, least-squares affine over one tile (as
    bake_baylands_lidar.utm_to_enu_affine)."""
    from pyproj import Transformer
    lat0, lon0 = 37.4125, -121.9970
    to_ll = Transformer.from_crs(26910, 4326, always_xy=True)
    m_lon = 111320.0 * math.cos(math.radians(lat0))
    ux, uy = np.meshgrid(np.linspace(x0, x1, 6), np.linspace(y0, y1, 6))
    lon, lat = to_ll.transform(ux.ravel(), uy.ravel())
    e = (np.asarray(lon) - lon0) * m_lon
    n = (np.asarray(lat) - lat0) * 111132.0
    A = np.column_stack([ux.ravel(), uy.ravel(), np.ones(ux.size)])
    coef, *_ = np.linalg.lstsq(A, np.column_stack([e, n]), rcond=None)
    return coef


def _c1_tile(args):
    path, rasters = args
    import laspy
    dtm = np.memmap(Path(rasters) / "dtm.f32", np.float32, "r", shape=(ROWS, COLS))
    with laspy.open(path) as r:
        hx0, hy0 = r.header.mins[:2]
        hx1, hy1 = r.header.maxs[:2]
        p = r.read()
    coef = _utm_affine(hx0, hy0, hx1, hy1)
    keep = np.asarray(p.classification) == 1
    try:
        keep &= ~np.asarray(p.withheld, dtype=bool)
    except Exception:                                   # noqa: BLE001 - older formats
        pass
    x = np.asarray(p.x)[keep]; y = np.asarray(p.y)[keep]; z = np.asarray(p.z)[keep].astype(np.float32)
    e = coef[0, 0] * x + coef[1, 0] * y + coef[2, 0]
    n = coef[0, 1] * x + coef[1, 1] * y + coef[2, 1]
    c = np.floor((e - REGION[0]) / CELL).astype(np.int64)
    rr = np.floor((REGION[3] - n) / CELL).astype(np.int64)
    ins = (c >= 0) & (c < COLS) & (rr >= 0) & (rr < ROWS)
    c, rr, z = c[ins], rr[ins], z[ins]
    hag = z - dtm[rr, c]
    m = hag > 0.3
    c, rr, hag = c[m], rr[m], hag[m]
    flat = rr * COLS + c
    out = {}
    for name, lo, hi in (("b0", 0.3, 2.5), ("b1", 2.5, 6.0), ("b2", 6.0, 15.0), ("b3", 15.0, 1e9)):
        mm = (hag >= lo) & (hag < hi)
        out[name] = np.unique(flat[mm], return_counts=True)
    order = np.argsort(hag)
    u, first = np.unique(flat[order], return_index=True)
    out["lo"] = (u, hag[order][first])
    return out


def step_c1(args):
    """Class-1 (unclassified) returns are the poles, light standards, signs,
    fences and rides in this delivery (there is no class 14/15). The max
    grid (other.f32) says how high; this says how far DOWN each cell's
    returns reach and in which height bands, which tells a pole (returns
    from near the ground to its top) from a wire (one height). Writes to
    --work: c1_b0..b3.u8 (returns per cell 0.3-2.5, 2.5-6, 6-15, >15 m above
    the ground) and c1_lo.f32 (lowest return above 0.3 m)."""
    from concurrent.futures import ProcessPoolExecutor
    tiles = sorted(str(p) for p in Path(args.tiles).glob("*.laz"))
    bands = {k: np.zeros(ROWS * COLS, np.uint16) for k in ("b0", "b1", "b2", "b3")}
    lo = np.full(ROWS * COLS, np.inf, np.float32)
    with ProcessPoolExecutor(12) as ex:
        for res in ex.map(_c1_tile, [(t, args.rasters) for t in tiles]):
            for b in bands:
                u, cnt = res[b]
                bands[b][u] = np.minimum(65535, bands[b][u].astype(np.int64) + cnt).astype(np.uint16)
            u, v = res["lo"]
            lo[u] = np.minimum(lo[u], v)
    W = Path(args.work)
    for b, a in bands.items():
        np.clip(a, 0, 255).astype(np.uint8).tofile(W / f"c1_{b}.u8")
    lo[~np.isfinite(lo)] = np.nan
    lo.tofile(W / "c1_lo.f32")
    print(f"c1: {len(tiles)} tiles")


# ------------------------------------------------------------------ things

def _b64(a: np.ndarray) -> str:
    import base64
    return base64.b64encode(zlib.compress(a.tobytes(), 9)).decode()


def _lines_index(lines):
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    geoms = [LineString(l) for l in lines if len(l) >= 2]
    return STRtree(geoms) if geoms else None, geoms


def _near(tree_geoms, p, r):
    from shapely.geometry import Point
    tree, geoms = tree_geoms
    if tree is None:
        return False
    pt = Point(p)
    for j in tree.query(pt.buffer(r)):
        if geoms[j].distance(pt) <= r:
            return True
    return False


def step_things(args, gr):
    """Poles, structures, fences, bridges, park furniture, parked cars."""
    from scipy import ndimage
    from shapely.geometry import Polygon, Point, LineString, shape, box
    from shapely.strtree import STRtree
    from rasterio import features as rfeat
    from rasterio.transform import Affine
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    W = Path(args.work)
    F = json.loads(Path(args.assets, "features.json").read_text())
    recs, _ = load_buildings(Path(args.assets))
    dtm = np.array(gr.f32("dtm"))
    oth = np.array(gr.f32("other"))
    hi = oth - dtm
    lo = np.fromfile(W / "c1_lo.f32", np.float32).reshape(ROWS, COLS)
    bands = [np.fromfile(W / f"c1_b{k}.u8", np.uint8).reshape(ROWS, COLS) for k in range(4)]
    bld = np.isfinite(np.array(gr.f32("bld")))
    veg = np.isfinite(np.array(gr.f32("veg")))
    tf = Affine(CELL, 0, REGION[0], 0, -CELL, REGION[3])

    def rc(e, n):
        return int((REGION[3] - n) / CELL), int((e - REGION[0]) / CELL)

    def en(r, c):
        return REGION[0] + (c + 0.5) * CELL, REGION[3] - (r + 0.5) * CELL

    def gnd(e, n):
        r, c = rc(e, n)
        return float(dtm[min(max(r, 0), ROWS - 1), min(max(c, 0), COLS - 1)])

    power = _lines_index([l["pts"] for l in F["power_lines"]])
    roads = _lines_index([l["pts"] for l in F["roads"]
                          if l["tags"].get("highway") not in ("service", "track", None)])
    allroads = _lines_index([l["pts"] for l in F["roads"]])
    pitches = [Polygon(a["pts"]) for a in F["areas"]
               if a["tags"].get("leisure") in ("pitch", "sports_centre", "stadium", "track")
               and len(a["pts"]) >= 4]
    pitches = [p for p in pitches if p.is_valid]
    pitch_tree = STRtree(pitches) if pitches else None
    footprints = [Polygon(r["ring"]) for r in recs]
    fp_tree = STRtree(footprints)

    def in_pitch(p):
        if pitch_tree is None:
            return False
        pt = Point(p)
        return any(pitches[j].buffer(25).contains(pt) for j in pitch_tree.query(pt.buffer(25)))

    out = {"source": "USGS 3DEP 2020 lidar (public domain), OpenStreetMap (ODbL); see CREDITS.md",
           "heights": "metres above NAVD88"}

    # ---------------------------------------------------------- poles
    nb = sum((ndimage.maximum_filter(b, 3) > 0).astype(np.int8) for b in bands)
    near_b = ndimage.binary_dilation(bld, iterations=3)
    near_v = ndimage.binary_dilation(veg, iterations=1)
    span = hi - lo
    vert = (span > 2.5) & (hi > 3.5) & (lo < np.maximum(2.0, 0.35 * hi)) & (nb >= 2) & ~near_b & ~near_v
    vert &= np.isfinite(lo)
    lab, n = ndimage.label(ndimage.binary_dilation(vert, iterations=1), structure=np.ones((3, 3)))
    objs = ndimage.find_objects(lab)
    hi0 = np.nan_to_num(hi, nan=0.0)
    poles = []
    blobs_mask = np.zeros((ROWS, COLS), bool)
    osm_flag = [p["p"] for p in F["points"] if p["tags"].get("man_made") == "flagpole"]
    osm_mast = [p["p"] for p in F["points"] if p["tags"].get("man_made") in ("mast", "tower")]
    used_osm = set()
    for li, sl in enumerate(objs, 1):
        if sl is None:
            continue
        comp = lab[sl] == li
        ext = max(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start) * CELL
        hsub = np.where(comp, hi0[sl], 0)
        k = np.unravel_index(np.argmax(np.where(comp & vert[sl], span[sl], -1)), comp.shape)
        r, c = sl[0].start + k[0], sl[1].start + k[1]
        e, nn = en(r, c)
        if _near(power, (e, nn), 10.0):
            continue                                     # baylands_wires' poles and towers
        top = float(hsub.max())
        if ext <= 2.5:
            # the pole's top: highest class-1 return within 2.5 m (lamp heads, arms)
            r0, r1, c0, c1 = max(0, r - 5), min(ROWS, r + 6), max(0, c - 5), min(COLS, c + 6)
            top = max(top, float(np.nanmax(np.where(np.isfinite(hi[r0:r1, c0:c1]), hi[r0:r1, c0:c1], 0))))
            g = float(dtm[r, c])
            kind = 4
            if any(math.hypot(e - q[0], nn - q[1]) < 5 for q in osm_flag):
                kind = 3
            elif any(math.hypot(e - q[0], nn - q[1]) < 6 for q in osm_mast):
                kind = 6
            elif top > 13.0 and in_pitch((e, nn)):
                kind = 2
            elif _near(allroads, (e, nn), 14.0) and top <= 14.5:
                # a wired (wood utility) pole carries spans: class-1 returns at
                # one height running away from it (> 8 m of them)
                r0, r1, c0, c1 = max(0, r - 24), min(ROWS, r + 25), max(0, c - 24), min(COLS, c + 25)
                wires = np.isfinite(hi[r0:r1, c0:c1]) & (hi[r0:r1, c0:c1] > 5.0) & (span[r0:r1, c0:c1] < 1.0)
                kind = 1 if wires.sum() > 40 else 0
            poles.append([round(e, 2), round(nn, 2), round(g + top, 2), round(g, 2), kind])
        else:
            blobs_mask[sl] |= comp
    # Elevated compact heads with no shaft in the cloud: floodlight racks and
    # big luminaires on slim poles the lidar barely touched (Twin Creeks'
    # field lights). Compact (<= 5 m, filled, not a wire fragment), 6 m up,
    # clear of trees; the pole stands under the head.
    taken = ndimage.binary_dilation(lab > 0, iterations=2)
    heads = np.isfinite(hi) & (hi > 6.0) & ~near_v & ~taken
    hl, nh = ndimage.label(ndimage.binary_dilation(heads, iterations=1), structure=np.ones((3, 3)))
    n_heads = 0
    for li, sl in enumerate(ndimage.find_objects(hl), 1):
        if sl is None:
            continue
        comp = (hl[sl] == li) & heads[sl]
        cnt = int(comp.sum())
        hh, ww = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if cnt < 4 or max(hh, ww) * CELL > 5.0 or max(hh, ww) > 3 * max(1, min(hh, ww)):
            continue
        if cnt < 0.3 * hh * ww:
            continue
        k = np.unravel_index(np.argmax(np.where(comp, hi0[sl], -1)), comp.shape)
        r, c = sl[0].start + k[0], sl[1].start + k[1]
        e, nn = en(r, c)
        if _near(power, (e, nn), 10.0):
            continue
        top = float(hi0[sl][comp].max())
        g = float(dtm[r, c])
        kind = 2 if (top > 11.0 and in_pitch((e, nn))) else (0 if _near(allroads, (e, nn), 14.0) else 4)
        poles.append([round(e, 2), round(nn, 2), round(g + top, 2), round(g, 2), kind])
        n_heads += 1
    print("pole heads without a shaft in the cloud:", n_heads)
    # OSM street lamps and flagpoles the lidar did not show (under trees, newer)
    have = np.array([[p[0], p[1]] for p in poles]) if poles else np.zeros((0, 2))
    for p in F["points"]:
        t = p["tags"]
        kind = 0 if t.get("highway") == "street_lamp" else 3 if t.get("man_made") == "flagpole" else None
        if kind is None:
            continue
        e, nn = p["p"]
        if not (REGION[0] < e < REGION[2] and REGION[1] < nn < REGION[3]):
            continue
        if len(have) and np.min(np.hypot(have[:, 0] - e, have[:, 1] - nn)) < 4.0:
            continue
        r, c = rc(e, nn)
        r0, r1, c0, c1 = max(0, r - 3), min(ROWS, r + 4), max(0, c - 3), min(COLS, c + 4)
        win = hi[r0:r1, c0:c1]
        h = float(np.nanmax(win)) if np.isfinite(win).any() else float("nan")
        if not (3.0 < h < 16.0):
            h = 8.5 if kind == 0 else 9.0               # typical: Sunnyvale cobra-head 8-9 m
        g = gnd(e, nn)
        poles.append([round(e, 2), round(nn, 2), round(g + h, 2), round(g, 2), kind])
    # Which way each pole faces: street-light arms and crossarms over the
    # nearest road, sports lights at their pitch.
    from shapely.geometry import Point as P_
    for p in poles:
        e, nn, kind = p[0], p[1], p[4]
        ang = 0.0
        if kind == 2 and pitch_tree is not None:
            js = list(pitch_tree.query(P_(e, nn).buffer(120)))
            if js:
                j = min(js, key=lambda j: pitches[j].distance(P_(e, nn)))
                cc = pitches[j].centroid
                ang = math.atan2(cc.y - nn, cc.x - e)
        elif allroads[0] is not None:
            js = list(allroads[0].query(P_(e, nn).buffer(40)))
            if js:
                j = min(js, key=lambda j: allroads[1][j].distance(P_(e, nn)))
                q = allroads[1][j].interpolate(allroads[1][j].project(P_(e, nn)))
                if q.distance(P_(e, nn)) > 0.3:
                    ang = math.atan2(q.y - nn, q.x - e)
        p.append(round(ang, 3))
    out["poles"] = poles
    out["pole_format"] = "e, n, top (NAVD88 m), ground, kind, facing (rad, ENU from east)"
    out["pole_kinds"] = ["street light", "wood utility pole", "sports light standard", "flagpole",
                         "pole (sign, camera, signal)", "", "mast"]
    from collections import Counter
    print("poles:", Counter(p[4] for p in poles))

    # ---------------------------------------------------------- structures (measured blobs)
    # Larger class-1 clusters (rides, playground frames, backstops, signs,
    # gantries): every class-1 cell within 1.5 m of the cluster, as columns
    # on a 1 m grid from the lowest to the highest return.
    blobs_mask = ndimage.binary_dilation(blobs_mask, iterations=3) & np.isfinite(hi) & (hi > 1.0)
    blabel, nbl = ndimage.label(blobs_mask, structure=np.ones((3, 3)))
    blobs = []
    for li, sl in enumerate(ndimage.find_objects(blabel), 1):
        if sl is None:
            continue
        comp = blabel[sl] == li
        if comp.sum() < 6:
            continue
        r0, c0 = sl[0].start, sl[1].start
        h2, w2 = (comp.shape[0] + 1) // 2, (comp.shape[1] + 1) // 2
        pad = lambda a, v: np.pad(a, ((0, h2 * 2 - a.shape[0]), (0, w2 * 2 - a.shape[1])), constant_values=v)
        cm = pad(comp, False)
        top = pad(np.where(comp, oth[sl], -np.inf), -np.inf).reshape(h2, 2, w2, 2).max(axis=(1, 3))
        bot = pad(np.where(comp, lo[sl] + dtm[sl], np.inf), np.inf).reshape(h2, 2, w2, 2).min(axis=(1, 3))
        g = pad(dtm[sl], np.nan)
        any_ = cm.reshape(h2, 2, w2, 2).any(axis=(1, 3))
        bot = np.where(np.isfinite(bot), bot, np.nanmin(g))
        # columns whose lowest return is within 1.2 m of the ground stand on it
        gb = np.nanmin(g.reshape(h2, 2, w2, 2), axis=(1, 3))
        bot = np.where(bot - gb < 1.2, gb, bot)
        e0, n0 = en(r0, c0)
        q_top = np.where(any_, np.clip(np.round((top + 10) * 10), 1, 65535), 0).astype(np.uint16)
        q_bot = np.where(any_, np.clip(np.round((bot + 10) * 10), 1, 65535), 0).astype(np.uint16)
        e_c, n_c = e0 - 0.25 + 0.5 * w2, n0 + 0.25 - 0.5 * h2
        blobs.append({"o": [round(e0 + 0.25, 2), round(n0 - 0.25, 2)], "w": int(w2), "h": int(h2),
                      "top": _b64(q_top), "bot": _b64(q_bot),
                      "max": round(float(top[any_].max()), 2), "c": [round(e_c, 1), round(n_c, 1)]})
    # Playground equipment (OSM playgrounds): every class-1 cell over 1 m
    # inside the playground, whatever its shape (play frames are low).
    for a in F["areas"]:
        if a["tags"].get("leisure") != "playground" or len(a["pts"]) < 4:
            continue
        pg = Polygon(a["pts"])
        if not pg.is_valid:
            continue
        r0, r1, c0, c1 = cell_window(pg.bounds, pad=1)
        E, N = cell_centres(r0, r1, c0, c1)
        inside = poly_mask(np.array(a["pts"]), E, N)
        comp = inside & np.isfinite(hi[r0:r1, c0:c1]) & (hi[r0:r1, c0:c1] > 1.0) & ~veg[r0:r1, c0:c1]
        comp &= ~blobs_mask[r0:r1, c0:c1]
        if comp.sum() < 4:
            continue
        sl = (slice(r0, r1), slice(c0, c1))
        h2, w2 = (comp.shape[0] + 1) // 2, (comp.shape[1] + 1) // 2
        pad = lambda a_, v: np.pad(a_, ((0, h2 * 2 - a_.shape[0]), (0, w2 * 2 - a_.shape[1])), constant_values=v)
        cm = pad(comp, False)
        top = pad(np.where(comp, oth[sl], -np.inf), -np.inf).reshape(h2, 2, w2, 2).max(axis=(1, 3))
        gb = pad(dtm[sl], np.nan).reshape(h2, 2, w2, 2)
        gb = np.nanmin(gb, axis=(1, 3))
        any_ = cm.reshape(h2, 2, w2, 2).any(axis=(1, 3))
        e0, n0 = en(r0, c0)
        q_top = np.where(any_, np.clip(np.round((top + 10) * 10), 1, 65535), 0).astype(np.uint16)
        q_bot = np.where(any_, np.clip(np.round((gb + 10) * 10), 1, 65535), 0).astype(np.uint16)
        cc = pg.centroid
        blobs.append({"o": [round(e0 + 0.25, 2), round(n0 - 0.25, 2)], "w": int(w2), "h": int(h2),
                      "top": _b64(q_top), "bot": _b64(q_bot), "max": round(float(top[any_].max()), 2),
                      "c": [round(cc.x, 1), round(cc.y, 1)], "play": 1})
    out["blobs"] = blobs
    out["blob_format"] = ("1 m columns: o = centre of the NW column (ENU m), w x h, rows north->south; "
                          "top/bot = zlib+base64 u16 (z + 10) * 10, 0 = empty")
    print("blobs:", len(blobs), "columns:", sum(b["w"] * b["h"] for b in blobs))

    # ---------------------------------------------------------- fences and walls (OSM, heights from lidar)
    fences = []
    for l in F["fences"]:
        bt = l["tags"].get("barrier")
        if bt not in ("fence", "wall", "retaining_wall", "guard_rail", "jersey_barrier", "yes", "gate"):
            continue
        pts = np.array(l["pts"])
        if len(pts) < 2:
            continue
        hs = []
        for k in range(len(pts) - 1):
            a, b = pts[k], pts[k + 1]
            L = float(np.hypot(*(b - a)))
            ns = max(1, int(L / 2.0))
            vals = []
            for s_ in range(ns + 1):
                p = a + (b - a) * s_ / ns
                r, c = rc(*p)
                r0, r1, c0, c1 = max(0, r - 1), min(ROWS, r + 2), max(0, c - 1), min(COLS, c + 2)
                w = hi[r0:r1, c0:c1]
                w = w[np.isfinite(w) & (w < 6.0)]
                if w.size:
                    vals.append(float(w.max()))
            hs.append(vals)
        flat = [v for vs in hs for v in vs]
        default = {"fence": 1.8, "wall": 2.0, "retaining_wall": 1.0, "guard_rail": 0.8,
                   "jersey_barrier": 0.85, "yes": 1.2, "gate": 1.5}[bt]
        h = float(np.percentile(flat, 70)) if len(flat) >= 3 and np.mean(np.array(flat) > 0.5) > 0.3 else default
        h = float(np.clip(h, 0.6, 6.5))
        if bt in ("guard_rail", "jersey_barrier"):
            h = default
        fences.append({"pts": [[round(x, 2), round(y, 2)] for x, y in pts.tolist()], "h": round(h, 2),
                       "kind": bt, "g": [round(gnd(*p), 2) for p in pts.tolist()]})
    out["fences"] = fences
    print("fences:", len(fences), "solid (>2 m):", sum(1 for f_ in fences if f_["h"] > 2.0))

    # ---------------------------------------------------------- bridge decks (class 17)
    br = np.array(gr.f32("bridge"))
    bm = ndimage.binary_closing(np.isfinite(br), iterations=2)
    bm = ndimage.binary_fill_holes(bm)
    blab, nbr = ndimage.label(bm)
    bridges = []
    for li, sl in enumerate(ndimage.find_objects(blab), 1):
        comp = blab[sl] == li
        if comp.sum() < 80:                              # < 20 m2
            continue
        z = np.where(comp, br[sl], np.nan)
        zf = fill_nan(z)
        zf = ndimage.median_filter(zf, 5)
        zf = ndimage.uniform_filter(zf, 3)
        r0, c0 = sl[0].start, sl[1].start
        h2, w2 = (comp.shape[0] + 1) // 2, (comp.shape[1] + 1) // 2
        zp = np.pad(zf, ((0, h2 * 2 - zf.shape[0]), (0, w2 * 2 - zf.shape[1])), mode="edge")
        post = zp.reshape(h2, 2, w2, 2).mean(axis=(1, 3))
        q = np.clip(np.round((post + 10) * 20), 0, 65535).astype(np.uint16)
        sub_tf = tf * Affine.translation(c0, r0)
        rings = []
        for geom, _ in rfeat.shapes(comp.astype(np.uint8), mask=comp, transform=sub_tf):
            gp = shape(geom).buffer(0.25).simplify(0.5)
            if gp.geom_type == "Polygon" and gp.area > 20:
                pts = simplify_ring(gp, 0.5, 120)
                if pts is not None:
                    rings.append([[round(x, 2), round(y, 2)] for x, y in pts.tolist()])
        if not rings:
            continue
        g_under = float(np.nanmedian(np.where(comp, dtm[sl], np.nan)))
        e0, n0 = en(r0, c0)
        bridges.append({"rings": rings, "o": [round(e0 + 0.25, 2), round(n0 - 0.25, 2)], "w": int(w2),
                        "h": int(h2), "z": _b64(q), "under": round(g_under, 2)})
    out["bridges"] = bridges
    out["bridge_format"] = "deck top on 1 m posts: zlib+base64 u16 (z + 10) * 20; o = NW post centre"
    print("bridges:", len(bridges))

    # ---------------------------------------------------------- park and street furniture (OSM points)
    furn = []
    keymap = {("amenity", "bench"): "bench", ("leisure", "picnic_table"): "table", ("amenity", "bbq"): "bbq",
              ("amenity", "waste_basket"): "bin", ("barrier", "bollard"): "bollard", ("barrier", "gate"): "gate",
              ("barrier", "lift_gate"): "lift_gate", ("amenity", "drinking_water"): "fountain",
              ("tourism", "information"): "board", ("amenity", "bicycle_parking"): "bike_rack",
              ("leisure", "fitness_station"): "fitness", ("highway", "bus_stop"): "bus_stop",
              ("man_made", "surveillance"): "camera", ("highway", "traffic_signals"): "signal"}
    for p in F["points"]:
        t = p["tags"]
        kind = None
        for (k, v), nm in keymap.items():
            if t.get(k) == v:
                kind = nm
                break
        if kind is None:
            continue
        if kind == "bus_stop" and t.get("shelter") != "yes":
            kind = "bus_sign"
        e, nn = p["p"]
        if not (REGION[0] < e < REGION[2] and REGION[1] < nn < REGION[3]):
            continue
        # face the nearest path or road
        ang = 0.0
        from shapely.geometry import Point as P_
        best = None
        for tree in (allroads,):
            if tree[0] is None:
                continue
            for j in tree[0].query(P_(e, nn).buffer(30)):
                d = tree[1][j].distance(P_(e, nn))
                if best is None or d < best[0]:
                    best = (d, j)
        if best is not None:
            ln = tree[1][best[1]]
            s_ = ln.project(P_(e, nn))
            a = ln.interpolate(max(0, s_ - 1)); b = ln.interpolate(min(ln.length, s_ + 1))
            ang = math.atan2(b.y - a.y, b.x - a.x)
        furn.append([round(e, 2), round(nn, 2), round(gnd(e, nn), 2), kind, round(ang, 3)])
    out["furniture"] = furn
    print("furniture:", Counter(f_[3] for f_ in furn))

    # playgrounds and pitches (goals): OSM areas
    plays, goals = [], []
    for a in F["areas"]:
        t = a["tags"]
        if t.get("leisure") == "playground" and len(a["pts"]) >= 4:
            pg = Polygon(a["pts"])
            if pg.is_valid and pg.area > 20:
                c = pg.centroid
                plays.append({"ring": [[round(x, 2), round(y, 2)] for x, y in a["pts"][:-1]],
                              "c": [round(c.x, 2), round(c.y, 2)], "g": round(gnd(c.x, c.y), 2)})
        if t.get("leisure") == "pitch" and t.get("sport") in ("soccer", "american_football") and len(a["pts"]) >= 4:
            pg = Polygon(a["pts"])
            if not pg.is_valid or pg.area < 1500:
                continue
            cen, u, hl, hw = min_rect(np.array(a["pts"]))
            for sgn in (-1, 1):
                p = cen + u * sgn * (hl - 0.5)
                goals.append([round(float(p[0]), 2), round(float(p[1]), 2), round(gnd(*p), 2),
                              round(math.atan2(u[1], u[0]), 3), 7.32 if hl > 40 else 5.0])
    out["playgrounds"] = plays
    out["goals"] = goals
    print("playgrounds:", len(plays), "goals:", len(goals))

    # ---------------------------------------------------------- parked cars
    out["cars"] = parked_cars(F, footprints, fp_tree, gr, args)
    Path(args.out, "built_things.json").write_text(json.dumps(out, separators=(",", ":")))
    print(f"built_things.json {Path(args.out, 'built_things.json').stat().st_size / 1e6:.2f} MB")


def parked_cars(F, footprints, fp_tree, gr, args):
    """Stalls in the OSM car parks: double-loaded rows along each lot's long
    axis (5.5 m stalls, 2.7 m wide, 7 m aisles), filled to a weekday
    occupancy (business 70%, park 45%, residential 55%), skipping stalls the
    lidar says hold a tree, building or structure, or where the 2024 photo is
    vegetation (NDVI)."""
    from shapely.geometry import Polygon, Point, box
    from shapely import affinity
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    from scipy import ndimage
    rng = np.random.default_rng(20240520)
    dtm = np.array(gr.f32("dtm"))
    veg = np.array(gr.f32("veg")) - dtm
    bldm = np.isfinite(np.array(gr.f32("bld")))
    oth = np.array(gr.f32("other")) - dtm
    rgb = np.asarray(Image.open(Path(args.hi) / "ortho.jpg").convert("RGB"))
    nir = np.asarray(Image.open(Path(args.hi) / "nir.jpg").convert("L"))
    park_poly = None
    for a in F["areas"]:
        if a["tags"].get("name") in ("Sunnyvale Baylands Park", "Twin Creeks Sports Complex"):
            p = Polygon(a["pts"])
            park_poly = p if park_poly is None else park_poly.union(p)
    resid = [Polygon(a["pts"]) for a in F["areas"] if a["tags"].get("landuse") == "residential"
             and len(a["pts"]) >= 4]
    resid = [p for p in resid if p.is_valid]
    cars = []
    for a in F["areas"]:
        t = a["tags"]
        if t.get("amenity") != "parking" or len(a["pts"]) < 4:
            continue
        if t.get("parking") in ("underground", "multi-storey", "rooftop"):
            continue
        lot = Polygon(a["pts"])
        if not lot.is_valid or lot.area < 120:
            continue
        cen = lot.centroid
        occ = 0.70
        if park_poly is not None and park_poly.buffer(30).contains(cen):
            occ = 0.45
        elif any(r.contains(cen) for r in resid):
            occ = 0.55
        c, u, hl, hw = min_rect(np.array(a["pts"]))
        ang = math.atan2(u[1], u[0])
        v = np.array([-u[1], u[0]])
        street = t.get("parking") == "street_side" or hw < 5.0
        rows = []
        if street:
            rows.append((0.0, 0))                        # one parallel row along the axis
        else:
            # double rows: [stall 5.5][stall 5.5][aisle 7] ...
            span = 2 * hw
            mod = 18.0
            k = int((span + 7.0) // mod)
            used = k * mod - 7.0 if k > 0 else 0
            t0 = -hw + max(0.0, (span - used) / 2)
            for i in range(k):
                base = t0 + i * mod
                rows.append((base + 2.75, 1))
                rows.append((base + 8.25, -1))
            if k == 0 and span >= 5.5:
                rows.append((0.0, 1))
        for (tt, facing) in rows:
            pitch = 6.5 if street else 2.7
            ns = int((2 * hl - 1.0) // pitch)
            s0 = -(ns - 1) * pitch / 2
            for i in range(ns):
                s = s0 + i * pitch
                p = c + u * s + v * tt
                if not lot.buffer(-0.8).contains(Point(p)):
                    continue
                if rng.random() > occ:
                    continue
                r = int((REGION[3] - p[1]) / CELL); cc = int((p[0] - REGION[0]) / CELL)
                if not (2 <= r < ROWS - 2 and 2 <= cc < COLS - 2):
                    continue
                win = (slice(r - 2, r + 3), slice(cc - 2, cc + 3))
                if np.nanmax(np.nan_to_num(veg[win], nan=0)) > 2.5 or bldm[win].any():
                    continue
                if np.nanmax(np.nan_to_num(oth[win], nan=0)) > 3.0:
                    continue
                pr = int((REGION[3] - p[1]) / 0.6); pc = int((p[0] - REGION[0]) / 0.6)
                if 0 <= pr < nir.shape[0] and 0 <= pc < nir.shape[1]:
                    red = float(rgb[pr, pc, 0]); ni = float(nir[pr, pc])
                    if (ni - red) / max(ni + red, 1) > 0.25:
                        continue                         # planted island
                heading = ang if street else ang + math.pi / 2 * facing
                if not street and rng.random() < 0.15:
                    heading += math.pi                   # some back in
                body = int(rng.choice(4, p=[0.45, 0.30, 0.15, 0.10]))   # sedan, suv, pickup, van
                colour = int(rng.choice(8, p=[0.24, 0.20, 0.17, 0.14, 0.10, 0.08, 0.04, 0.03]))
                g = float(dtm[r, cc])
                cars.append([round(float(p[0]), 2), round(float(p[1]), 2), round(g, 2),
                             round(heading + rng.normal(0, 0.03), 3), body, colour])
    print("cars:", len(cars))
    return cars


if __name__ == "__main__":
    sys.exit(main())
