#!/usr/bin/env python3
"""Measure every tree, shrub and rush stand of the Baylands scene from the lidar.

Step 3 of the Baylands pipeline (after fetch_baylands.py and
bake_baylands_lidar.py). Writes the committed vegetation file that
scripts/baylands_nature.gd draws and makes solid:

    drone/sim/godot/assets/baylands/trees.dat    (layout below)
    drone/sim/godot/assets/baylands/trees.json   (species, counts, quality)

HOW A TREE IS MEASURED
  * Canopy height model (CHM): the 0.5 m highest-vegetation grid (classes 3-5)
    minus the bare-earth grid, both from bake_baylands_lidar.py.
  * Tree tops: local maxima of the lightly smoothed CHM, the search window
    growing with height (Popescu's crown-width rule, widened for open-grown
    urban trees). Crowns: marker-controlled watershed of the CHM, cut where the
    canopy drops below 30 % of the tree's height or 1.2 m.
  * Per tree: total height (the CHM maximum), position (between the top and the
    crown's centroid: the trunk of a leaning broadleaf is under its crown's
    middle, a conifer's under its top), the crown outline as 8 radii (the 95th
    percentile reach in each 45-degree sector), and the crown BASE from the
    raw points: the vertical profile of the tree's class 3-5 returns, read
    down from the top until the first 1 m gap.
  * Shrubs (tops 1-3 m) the same way with a fixed 1 m window.
  * Rush/tule stands: 0.6-3.5 m vegetation (not a shrub crown) within 12 m of
    fresh or brackish water (creeks, sloughs, the lagoon, small ponds: not the
    salt ponds or the bay) or in an OSM marsh, one clump per 1.5 m cell.

SPECIES are not in any public data here; they are CLASSIFIED, plausibly for
the South Bay, from crown shape (height, width, crown-base fraction,
pointedness), the NAIP 2024 colour and near-infrared (NDVI, how blue-grey the
crown is), how open the crown is to the lidar (ground returns under it) and
context (distance to water, roads, freeways; OSM land use; OSM-tagged trees).
The rules are in classify(); trees.json records how many of each.

trees.dat layout (little-endian), origin = REGION's south-west corner:
    "BLT1", u32 n_trees, u32 n_shrubs, u32 n_rush, f32 quant (0.1 m)
    trees  x n: u16 e, u16 n (quant), u16 height (cm), u16 crown base (cm),
                u8[8] crown radii (0.1 m; sector k centred on 45k degrees
                counter-clockwise from east), u8 species, u8 shade (128 = 1.0)
    shrubs x n: u16 e, u16 n, u8 height (0.05 m), u8 radius (0.05 m),
                u8 species, u8 shade
    rush   x n: u16 e, u16 n, u8 height (0.05 m), u8 density (0-255)

SOURCES: USGS 3DEP lidar CA_SantaClaraCounty_2020 (public domain), USDA NAIP
2024 (public domain), OpenStreetMap (ODbL). See assets/baylands/CREDITS.md.

Run on a machine with RAM to spare (~25 GB peak; never on a laptop):
    uv run --no-project --with "laspy[lazrs]" --with numpy --with scipy \
        --with scikit-image --with pillow --with pyproj \
        python bake_baylands_trees.py --rasters ~/bay/data/rasters \
        --copc ~/bay/data/copc --hi ~/bay/out/_hi --features ~/bay/out/features.json \
        --osm ~/bay/data/osm.json --buildings drone/sim/godot/assets/baylands/buildings.dat \
        --out drone/sim/godot/assets/baylands --preview /tmp/prev
"""
from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

# Must match env_baylands.gd and bake_baylands_lidar.py.
DATUM_LAT, DATUM_LON = 37.4125, -121.9970
REGION = (-2500.0, -2400.0, 2500.0, 2400.0)
CELL = 0.5
M_PER_DEG_LAT = 111132.0
ROWS, COLS = 9600, 10000
QUANT = 0.1

TREE_MIN_H = 3.0
SHRUB_MIN_H = 1.0
NB = 90                      # crown-profile bins (0.5 m): 0-45 m
CTX = 2.0                    # context grids' cell (m)

SPECIES = ["eucalyptus", "live_oak", "monterey_pine", "monterey_cypress", "redwood",
           "fan_palm", "date_palm", "london_plane", "pistache", "raywood_ash",
           "willow", "evergreen", "deciduous", "sycamore"]
SHRUBS = ["coyote_brush", "oleander", "landscape", "tule"]
SP = {s: i for i, s in enumerate(SPECIES)}
SH = {s: i for i, s in enumerate(SHRUBS)}


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def grid(path: Path, name: str, dtype=np.float32) -> np.ndarray:
    return np.fromfile(path / name, dtype).reshape(ROWS, COLS)


def rc_to_en(r, c):
    return REGION[0] + (np.asarray(c) + 0.5) * CELL, REGION[3] - (np.asarray(r) + 0.5) * CELL


def disk(r_cells: float) -> np.ndarray:
    k = int(math.ceil(r_cells))
    y, x = np.mgrid[-k:k + 1, -k:k + 1]
    return (x * x + y * y) <= r_cells * r_cells + 0.25


# ------------------------------------------------------------ segmentation

def window_radius(h: np.ndarray) -> np.ndarray:
    """Local-maximum search radius (m) for a top of height h (m). Wider than
    forest crown-width rules: the street and yard trees here are open-grown,
    with crowns as wide as they are tall."""
    return np.clip(1.2 + 0.12 * h, 1.5, 5.0)


def find_tops(s: np.ndarray, min_h: float, max_h: float, radii: list[float], rad_of) -> np.ndarray:
    """Boolean grid of local maxima of s in [min_h, max_h) with a height-dependent window."""
    want = rad_of(s)
    tops = np.zeros(s.shape, bool)
    cand = (s >= min_h) & (s < max_h)
    for i, r in enumerate(radii):
        lo = radii[i - 1] if i else 0.0
        sel = cand & (want > lo) & (want <= r + 1e-6)
        if not sel.any():
            continue
        mx = ndi.maximum_filter(s, footprint=disk(r / CELL))
        tops |= sel & (s >= mx)
    # Plateaus: keep one pixel per connected flat top.
    lab, n = ndi.label(tops, structure=np.ones((3, 3)))
    if n:
        idx = ndi.maximum_position(s + np.random.default_rng(1).random(s.shape).astype(np.float32) * 1e-4,
                                   lab, np.arange(1, n + 1))
        tops = np.zeros(s.shape, bool)
        rr, cc = np.array(idx).T
        tops[rr, cc] = True
    return tops


def segment(chm: np.ndarray, tops: np.ndarray, floor_h: float, frac: float) -> tuple[np.ndarray, int]:
    from skimage.segmentation import watershed
    markers, n = ndi.label(tops)
    mask = chm >= floor_h
    lab = watershed(-chm, markers, mask=mask)
    # Cut each crown where the canopy drops below frac of its own top.
    top_h = ndi.maximum(chm, lab, np.arange(n + 1))
    top_h[0] = 0
    cut = chm < np.asarray(top_h, np.float32)[lab] * frac
    lab[cut] = 0
    return lab.astype(np.int32), n


def crown_stats(lab: np.ndarray, chm: np.ndarray, n: int):
    """Per label: top (r, c, h), centroid, 8 sector radii, area."""
    rr, cc = np.nonzero(lab)
    ll = lab[rr, cc]
    order = np.argsort(ll, kind="stable")
    rr, cc, ll = rr[order], cc[order], ll[order]
    hh = chm[rr, cc]
    starts = np.searchsorted(ll, np.arange(1, n + 2))
    area = np.diff(starts) * CELL * CELL
    tops = ndi.maximum_position(chm, lab, np.arange(1, n + 1))
    tr, tc = np.array(tops, dtype=np.float64).T if n else (np.zeros(0), np.zeros(0))
    th = chm[tr.astype(int), tc.astype(int)] if n else np.zeros(0)
    w = np.maximum(hh, 0.1).astype(np.float64)
    cnt_w = np.bincount(ll, weights=w, minlength=n + 1)[1:]
    cr = np.bincount(ll, weights=rr * w, minlength=n + 1)[1:] / np.maximum(cnt_w, 1e-9)
    ccn = np.bincount(ll, weights=cc * w, minlength=n + 1)[1:] / np.maximum(cnt_w, 1e-9)
    return dict(rr=rr, cc=cc, ll=ll, starts=starts, area=area, tr=tr, tc=tc, th=th, cr=cr, cc_=ccn)


def sector_radii(st: dict, pr: np.ndarray, pc: np.ndarray) -> np.ndarray:
    """8 crown radii (m) around each tree's position (pr, pc) in cells."""
    n = pr.size
    ll = st["ll"] - 1
    dy = -(st["rr"] - pr[ll]) * CELL           # north positive
    dx = (st["cc"] - pc[ll]) * CELL
    d = np.hypot(dx, dy) + CELL * 0.5
    sec = (np.round(np.arctan2(dy, dx) / (math.pi / 4)).astype(int)) % 8
    key = ll * 8 + sec
    out = np.zeros(n * 8)
    # 95th percentile per (tree, sector): sort by key then distance.
    o = np.lexsort((d, key))
    key, d = key[o], d[o]
    b = np.searchsorted(key, np.arange(n * 8 + 1))
    cnt = np.diff(b)
    has = cnt > 0
    pick = b[:-1] + np.floor((cnt - 1) * 0.95).astype(int)
    out[has] = d[pick[has]]
    out = out.reshape(n, 8)
    mean = np.where((out > 0).any(1), out.sum(1) / np.maximum((out > 0).sum(1), 1), CELL)
    # An empty sector (a crown pressed against its neighbour) keeps half the mean reach.
    out = np.where(out > 0, out, (mean * 0.5)[:, None])
    return np.maximum(out, 0.4)


# ------------------------------------------------------------ point pass

def utm_to_enu_affine(x0, y0, x1, y1):
    from pyproj import Transformer
    to_ll = Transformer.from_crs(26910, 4326, always_xy=True)
    m_lon = 111320.0 * math.cos(math.radians(DATUM_LAT))
    ux, uy = np.meshgrid(np.linspace(x0, x1, 6), np.linspace(y0, y1, 6))
    lon, lat = to_ll.transform(ux.ravel(), uy.ravel())
    e = (np.asarray(lon) - DATUM_LON) * m_lon
    n = (np.asarray(lat) - DATUM_LAT) * M_PER_DEG_LAT
    A = np.column_stack([ux.ravel(), uy.ravel(), np.ones(ux.size)])
    coef, *_ = np.linalg.lstsq(A, np.column_stack([e, n]), rcond=None)
    return coef


_W = {}


def _init(lab_path, dtm_path, nlab):
    _W["lab"] = np.load(lab_path, mmap_mode="r")
    _W["dtm"] = np.load(dtm_path, mmap_mode="r")
    _W["n"] = nlab


def _points(path):
    """Per tree label: histogram of veg-return heights above ground (0.5 m bins),
    ground returns under the crown, first-return veg count."""
    import laspy
    with laspy.open(path) as r:
        hx0, hy0 = r.header.mins[:2]
        hx1, hy1 = r.header.maxs[:2]
        p = r.read()
    coef = utm_to_enu_affine(hx0, hy0, hx1, hy1)
    cls = np.asarray(p.classification).astype(np.int16)
    keep = np.isin(cls, [2, 3, 4, 5])
    x = np.asarray(p.x)[keep]; y = np.asarray(p.y)[keep]; z = np.asarray(p.z)[keep].astype(np.float32)
    cls = cls[keep]
    e = coef[0, 0] * x + coef[1, 0] * y + coef[2, 0]
    n = coef[0, 1] * x + coef[1, 1] * y + coef[2, 1]
    c = np.floor((e - REGION[0]) / CELL).astype(np.int64)
    rr = np.floor((REGION[3] - n) / CELL).astype(np.int64)
    ins = (c >= 0) & (c < COLS) & (rr >= 0) & (rr < ROWS)
    c, rr, z, cls = c[ins], rr[ins], z[ins], cls[ins]
    nl = _W["n"]
    hist = np.zeros((nl + 1) * NB, np.int64)
    gnd = np.zeros(nl + 1, np.int64)
    if c.size == 0:
        return hist, gnd
    lab = np.asarray(_W["lab"][rr, c])
    sel = lab > 0
    lab, rr, c, z, cls = lab[sel], rr[sel], c[sel], z[sel], cls[sel]
    hag = z - np.asarray(_W["dtm"][rr, c])
    veg = cls != 2
    b = np.clip((hag[veg] / 0.5).astype(np.int64), 0, NB - 1)
    hist += np.bincount(lab[veg] * NB + b, minlength=(nl + 1) * NB)
    gnd += np.bincount(lab[~veg], minlength=nl + 1)
    return hist, gnd


def crown_base(hist: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Crown base (m above ground) from each tree's return profile: read down
    from the top bin until the first gap of >= 1 m (2 bins under 2 % of the
    profile's peak), ignoring the lowest 0.5 m (grass, shrubs)."""
    n = hist.shape[0]
    out = np.zeros(n)
    peak = hist.max(1)
    for i in range(n):
        top = min(int(h[i] / 0.5), NB - 1)
        if peak[i] < 6:
            out[i] = h[i] * 0.3
            continue
        thr = max(1, peak[i] * 0.02)
        gap = 0
        cb = 0.0
        for b in range(top, 0, -1):
            if hist[i, b] < thr:
                gap += 1
                if gap >= 2:
                    cb = (b + 2) * 0.5
                    break
            else:
                gap = 0
        out[i] = cb
    return np.clip(out, 0.0, h * 0.9)


# ------------------------------------------------------------ context

def ctx_shape():
    return int((REGION[3] - REGION[1]) / CTX), int((REGION[2] - REGION[0]) / CTX)


def raster_lines(lines, width_m, shape):
    from PIL import Image, ImageDraw
    img = Image.new("L", (shape[1], shape[0]), 0)
    d = ImageDraw.Draw(img)
    for pts in lines:
        xy = [((e - REGION[0]) / CTX, (REGION[3] - n) / CTX) for e, n in pts]
        if len(xy) >= 2:
            d.line(xy, fill=255, width=max(1, int(round(width_m / CTX))))
    return np.asarray(img) > 0


def raster_polys(polys, shape):
    from PIL import Image, ImageDraw
    img = Image.new("L", (shape[1], shape[0]), 0)
    d = ImageDraw.Draw(img)
    for pts in polys:
        xy = [((e - REGION[0]) / CTX, (REGION[3] - n) / CTX) for e, n in pts]
        if len(xy) >= 3:
            d.polygon(xy, fill=255)
    return np.asarray(img) > 0


def footprints(path: Path) -> np.ndarray:
    """The scene's building footprints (buildings.dat: OSM, current, plus
    lidar-traced) rasterised on the 0.5 m grid."""
    from PIL import Image, ImageDraw
    b = path.read_bytes()
    assert b[:4] == b"IST1"
    n, nv = struct.unpack_from("<II", b, 4)
    q = struct.unpack_from("<f", b, 12)[0]
    rec = 16
    vb = rec + n * 16
    img = Image.new("L", (COLS, ROWS), 0)
    d = ImageDraw.Draw(img)
    for i in range(n):
        first, cnt = struct.unpack_from("<IH", b, rec + i * 16)
        v = np.frombuffer(b, "<i2", cnt * 2, vb + first * 4).reshape(cnt, 2) * q
        xy = [((e - REGION[0]) / CELL, (REGION[3] - nn) / CELL) for e, nn in v]
        if len(xy) >= 3:
            d.polygon(xy, fill=255)
    return np.asarray(img) > 0


def context(features: dict, water_full: np.ndarray, bld: np.ndarray):
    shape = ctx_shape()
    roads = features["roads"]
    street = [r["pts"] for r in roads if r["tags"].get("highway") in
              ("residential", "tertiary", "secondary", "primary", "unclassified", "living_street",
               "tertiary_link", "secondary_link", "primary_link")]
    freeway = [r["pts"] for r in roads if r["tags"].get("highway") in
               ("motorway", "motorway_link", "trunk", "trunk_link")]
    d_street = ndi.distance_transform_edt(~raster_lines(street, 8.0, shape)) * CTX
    d_free = ndi.distance_transform_edt(~raster_lines(freeway, 14.0, shape)) * CTX
    w = water_full[:shape[0] * 4, :shape[1] * 4].reshape(shape[0], 4, shape[1], 4).max(axis=(1, 3)) > 0
    w = ndi.binary_opening(w, iterations=1)
    d_water = ndi.distance_transform_edt(~w) * CTX
    # Salt ponds and the open bay (big water) grow no tule or cattail at their
    # edges (pickleweed, cordgrass, and on the levees coyote brush and
    # pepperweed); the creeks, sloughs, the park lagoon and small ponds do.
    lab_w, nw = ndi.label(w)
    sizes = np.bincount(lab_w.ravel(), minlength=nw + 1) * CTX * CTX
    big = (sizes > 150000.0)[lab_w] & w
    d_big = ndi.distance_transform_edt(~big) * CTX
    d_small = ndi.distance_transform_edt(~(w & ~big)) * CTX
    areas = features["areas"]

    def tagged(fn):
        return raster_polys([a["pts"] for a in areas if fn(a["tags"])], shape)
    resid = tagged(lambda t: t.get("landuse") == "residential")
    comm = tagged(lambda t: t.get("landuse") in ("commercial", "industrial", "retail", "office"))
    park = tagged(lambda t: t.get("leisure") in ("park", "nature_reserve", "recreation_ground", "golf_course")
                  or t.get("landuse") in ("grass", "meadow", "recreation_ground"))
    marsh = tagged(lambda t: t.get("natural") == "wetland" and t.get("wetland") != "saltmarsh")
    b = np.isfinite(bld)[:shape[0] * 4, :shape[1] * 4].reshape(shape[0], 4, shape[1], 4).mean(axis=(1, 3))
    bdens = ndi.uniform_filter(b, size=int(60 / CTX))       # building cover within ~60 m
    return dict(d_street=d_street, d_free=d_free, d_water=d_water, d_big=d_big, d_small=d_small,
                resid=resid, comm=comm,
                park=park, marsh=marsh, bdens=bdens)


def sample_ctx(ctx: dict, e: np.ndarray, n: np.ndarray) -> dict:
    shape = ctx_shape()
    c = np.clip(((e - REGION[0]) / CTX).astype(int), 0, shape[1] - 1)
    r = np.clip(((REGION[3] - n) / CTX).astype(int), 0, shape[0] - 1)
    return {k: v[r, c] for k, v in ctx.items()}


# ------------------------------------------------------------ photo

def naip(hi: Path):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    rgb = np.asarray(Image.open(hi / "ortho.jpg").convert("RGB"))
    nir = np.asarray(Image.open(hi / "nir.jpg").convert("L"))
    return rgb, nir


def photo_stats(st: dict, chm: np.ndarray, rgb: np.ndarray, nir: np.ndarray, n: int):
    """Mean colour and NDVI over each crown's upper part (sunlit, least mixed)."""
    rr, cc, ll = st["rr"], st["cc"], st["ll"]
    hh = chm[rr, cc]
    upper = hh >= st["th"][ll - 1] * 0.6
    rr, cc, ll = rr[upper], cc[upper], ll[upper]
    e, nn = rc_to_en(rr, cc)
    ph, pw = nir.shape
    px = np.clip(((e - REGION[0]) / 0.6).astype(int), 0, pw - 1)
    py = np.clip(((REGION[3] - nn) / 0.6).astype(int), 0, ph - 1)
    R = rgb[py, px, 0].astype(np.float64); G = rgb[py, px, 1].astype(np.float64)
    B = rgb[py, px, 2].astype(np.float64); N = nir[py, px].astype(np.float64)
    cnt = np.maximum(np.bincount(ll, minlength=n + 1)[1:], 1)
    m = lambda v: np.bincount(ll, weights=v, minlength=n + 1)[1:] / cnt
    ndvi = (N - R) / np.maximum(N + R, 1.0)
    # "Still there in 2024": vegetation in the photo within 3 m of the crown
    # pixel (the photo leans tall crowns and palms off their trunks by metres).
    r_ = rgb[..., 0].astype(np.float32)
    n_ = nir.astype(np.float32)
    veg = (n_ - r_) / np.maximum(n_ + r_, 1.0) > 0.15
    veg = ndi.binary_dilation(veg, structure=disk(5))
    return dict(R=m(R), G=m(G), B=m(B), N=m(N), ndvi=m(ndvi), green=m(veg[py, px].astype(np.float64)))


# ------------------------------------------------------------ species

def classify(t: dict, rng: np.random.Generator) -> np.ndarray:
    """South Bay species from shape, colour, openness and context. Every
    rule is a plausibility call, not an identification; see the module doc."""
    h, rad, cbf = t["h"], t["r"], t["cbf"]
    width = 2 * rad
    slender = width / np.maximum(h, 1)
    peaked = t["peak"]
    ndvi, bright = t["ndvi"], (t["R"] + t["G"] + t["B"]) / 3
    grey = t["B"] / np.maximum(t["G"], 1)               # blue-grey crowns read high
    openc = t["open"]
    dw, ds, df = t["d_water"], t["d_street"], t["d_free"]
    resid, comm, park = t["resid"], t["comm"], t["park"]
    urban = (t["bdens"] > 0.12) | resid | comm
    u = rng.random(h.size)
    sp = np.full(h.size, SP["evergreen"])
    dark = bright < np.percentile(bright, 35)
    # Order matters: later rules overwrite earlier ones.
    # Generic yard/landscape trees: half deciduous.
    sp[u < 0.45] = SP["deciduous"]
    # Coast live oak: broad, dark, low crown, open land and older yards.
    oak = (slender > 0.75) & dark & (h >= 4) & (h < 16) & (cbf < 0.35)
    sp[oak & (park | ~urban | (u < 0.5))] = SP["live_oak"]
    # Street trees: within a few metres of a street's kerb line.
    street = (ds < 9) & (h >= 4) & (h < 20) & urban
    k = rng.random(h.size)
    plane = street & ((h >= 12) | (width >= 10)) & (k < 0.7)
    sp[street] = np.where(k[street] < 0.45, SP["pistache"], SP["raywood_ash"])
    sp[plane] = SP["london_plane"]
    # Willows: low, bright, by the water.
    sp[(dw < 15) & (h < 13) & (ndvi > np.percentile(ndvi, 40))] = SP["willow"]
    # Sycamore (western / London plane hybrids) in parks and creek corridors.
    sp[(dw < 40) & (h >= 10) & (h < 22) & (slender > 0.6) & ~dark] = SP["sycamore"]
    # Dark conifers: peaked, dark crowns.
    con = dark & (peaked > 0.10) & (h >= 7)
    sp[con & (slender >= 0.45)] = SP["monterey_pine"]
    sp[con & (slender >= 0.45) & (u < 0.3) & (cbf < 0.25)] = SP["monterey_cypress"]
    # Coast redwood: tall, slender, dark, peaked; planted in rows by offices and homes.
    sp[(slender < 0.5) & (h >= 12) & (peaked > 0.08) & (bright < np.percentile(bright, 50))] = SP["redwood"]
    # Eucalyptus: the tallest crowns, open to the lidar, grey-green.
    euc = ((h >= 22) | ((h >= 15) & (openc > 0.12) & (grey > np.percentile(grey, 55)))) & (slender >= 0.3)
    sp[euc & (sp != SP["redwood"])] = SP["eucalyptus"]
    # Palms: a small crown on a bare trunk (the lidar sees only the head).
    palm = (rad <= 3.4) & (h >= 6) & (cbf >= 0.5) & (slender < 0.6)
    sp[palm & ((h >= 12) & (rad <= 2.6))] = SP["fan_palm"]
    sp[palm & ~((h >= 12) & (rad <= 2.6))] = SP["date_palm"]
    # OSM-tagged palms and needle-leaved trees win.
    sp[t["osm_palm"] & (h >= 12)] = SP["fan_palm"]
    sp[t["osm_palm"] & (h < 12)] = SP["date_palm"]
    sp[t["osm_needle"] & ~np.isin(sp, [SP["redwood"], SP["monterey_cypress"]])] = SP["monterey_pine"]
    return sp


def classify_shrubs(s: dict) -> np.ndarray:
    sp = np.full(s["h"].size, SH["landscape"])
    wild = ~(s["resid"] | s["comm"]) & (s["bdens"] < 0.08)
    sp[wild] = SH["coyote_brush"]
    sp[(s["d_free"] < 30) & (s["h"] >= 1.5)] = SH["oleander"]
    fresh = s["d_small"] <= s["d_big"]
    sp[((s["d_small"] < 10) | s["marsh"]) & fresh] = SH["tule"]
    return sp


# ------------------------------------------------------------ main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rasters", type=Path, required=True)
    ap.add_argument("--copc", type=Path, required=True)
    ap.add_argument("--hi", type=Path, required=True)
    ap.add_argument("--features", type=Path, required=True)
    ap.add_argument("--osm", type=Path, required=True)
    ap.add_argument("--buildings", type=Path, required=True, help="the scene's buildings.dat")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, default=Path("work"))
    ap.add_argument("--preview", type=Path)
    ap.add_argument("--jobs", type=int, default=16)
    a = ap.parse_args(argv)
    a.work.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20200414)

    log("loading grids")
    dtm = grid(a.rasters, "dtm.f32")
    veg = grid(a.rasters, "veg.f32")
    bld = grid(a.rasters, "bld.f32")
    water_full = grid(a.rasters, "water_full.u8", np.uint8)
    chm = np.nan_to_num(veg - dtm, nan=0.0).astype(np.float32)
    chm[chm < 0] = 0
    # Vegetation that is not above a roof by 1.5 m is the roof's overhang, not a tree top.
    roof = np.nan_to_num(bld - dtm, nan=-1.0)
    chm[(roof > 0) & (chm < roof + 1.5)] = 0
    del veg
    s = ndi.gaussian_filter(ndi.median_filter(chm, 3), 1.0)
    big = ndi.gaussian_filter(chm, 2.0)
    s = np.where(s > 15, big, s).astype(np.float32)

    log("tree tops")
    radii = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
    tops = find_tops(s, TREE_MIN_H, 99, radii, window_radius)
    lab, n = segment(s, tops, 1.2, 0.30)
    log(f"{n} tree crowns")
    st = crown_stats(lab, chm, n)
    keep_area = st["area"] >= 1.0
    # Position: halfway between the top and the crown's height-weighted centroid.
    pr = 0.5 * st["tr"] + 0.5 * st["cr"]
    pc = 0.5 * st["tc"] + 0.5 * st["cc_"]
    rad8 = sector_radii(st, pr, pc)
    h = st["th"].astype(np.float64)
    # Pointedness: how far the top stands above the crown 1.5 m around it.
    ring = ndi.uniform_filter(s, size=7)
    peak = (chm[st["tr"].astype(int), st["tc"].astype(int)] - ring[st["tr"].astype(int), st["tc"].astype(int)]) / np.maximum(h, 1)

    log("point pass (crown profiles)")
    lab_path = a.work / "lab.npy"
    dtm_path = a.work / "dtm.npy"
    np.save(lab_path, lab)
    np.save(dtm_path, dtm)
    hist = np.zeros((n + 1) * NB, np.int64)
    gnd = np.zeros(n + 1, np.int64)
    tiles = sorted(a.copc.glob("*.laz"))
    with ProcessPoolExecutor(a.jobs, initializer=_init, initargs=(lab_path, dtm_path, n)) as ex:
        for k, (hi_, g_) in enumerate(ex.map(_points, tiles)):
            hist += hi_
            gnd += g_
            if k % 20 == 0:
                log(f"  tile {k + 1}/{len(tiles)}")
    hist = hist.reshape(n + 1, NB)[1:]
    gnd = gnd[1:]
    vegpts = hist.sum(1)
    cb = crown_base(hist, h)
    openc = gnd / np.maximum(gnd + vegpts, 1)
    lab_path.unlink(); dtm_path.unlink()

    log("photo + context")
    rgb, nir = naip(a.hi)
    ph = photo_stats(st, chm, rgb, nir, n)
    feats = json.load(open(a.features))
    ctx = context(feats, water_full, bld)
    e, nn = rc_to_en(pr, pc)
    cx = sample_ctx(ctx, e, nn)
    osm_palm = np.zeros(n, bool)
    osm_needle = np.zeros(n, bool)
    osm_trees = []
    m_lon = 111320.0 * math.cos(math.radians(DATUM_LAT))
    for el in json.load(open(a.osm))["elements"]:
        tg = el.get("tags", {})
        if el["type"] == "node" and tg.get("natural") == "tree":
            te = (el["lon"] - DATUM_LON) * m_lon
            tn = (el["lat"] - DATUM_LAT) * M_PER_DEG_LAT
            osm_trees.append((te, tn, tg.get("leaf_type", "")))
    from scipy.spatial import cKDTree
    kd = cKDTree(np.column_stack([e, nn]))
    osm_hit = 0
    osm_in = 0
    for te, tn, lt in osm_trees:
        if not (REGION[0] < te < REGION[2] and REGION[1] < tn < REGION[3]):
            continue
        osm_in += 1
        d, j = kd.query((te, tn))
        if d < 4.0:
            osm_hit += 1
            if lt == "palm":
                osm_palm[j] = True
            elif lt == "needleleaved":
                osm_needle[j] = True
    log(f"OSM-mapped trees with a lidar tree within 4 m: {osm_hit}/{osm_in}")

    radm = rad8.mean(1)
    t = dict(h=h, r=radm, cbf=cb / np.maximum(h, 1), peak=peak, open=openc,
             osm_palm=osm_palm, osm_needle=osm_needle, **ph, **cx)
    sp = classify(t, rng)
    shade = np.clip(128 * (0.85 + 0.3 * (ph["G"] - np.median(ph["G"])) / 60.0), 80, 180)

    # Drop: tiny crowns, tops on water, trunks inside a building the scene
    # draws (built since the 2020 flight: OSM is current), and crowns the 2024
    # photo shows no vegetation under (felled since 2020).
    on_water = water_full[st["tr"].astype(int), st["tc"].astype(int)] > 0
    fp = footprints(a.buildings)
    in_bld = fp[np.clip(pr.round().astype(int), 0, ROWS - 1), np.clip(pc.round().astype(int), 0, COLS - 1)]
    gone = ph["green"] < 0.15
    keep = keep_area & ~on_water & (h >= TREE_MIN_H) & ~in_bld & ~gone
    log(f"trees kept {keep.sum()} of {n}: {int((in_bld & keep_area).sum())} inside buildings, "
        f"{int((gone & keep_area & ~in_bld).sum())} not green in the 2024 photo")

    log("shrubs")
    tops_s = find_tops(s, SHRUB_MIN_H, TREE_MIN_H, [1.0], lambda x: np.ones_like(x))
    tops_s &= lab == 0
    lab_s, ns = segment(s * (lab == 0), tops_s, 0.6, 0.35)
    sts = crown_stats(lab_s, chm, ns)
    hs = sts["th"].astype(np.float64)
    rs = np.sqrt(sts["area"] / math.pi)
    es, ns_ = rc_to_en(sts["cr"], sts["cc_"])
    cs = sample_ctx(ctx, es, ns_)
    sps = classify_shrubs(dict(h=hs, **cs))
    keep_s = (sts["area"] >= 0.5) & (water_full[sts["tr"].astype(int), sts["tc"].astype(int)] == 0) \
        & ~fp[np.clip(sts["cr"].round().astype(int), 0, ROWS - 1), np.clip(sts["cc_"].round().astype(int), 0, COLS - 1)]
    log(f"shrubs {keep_s.sum()}")

    log("rushes")
    # Tule/cattail: 0.6-3.5 m vegetation by open water or in a mapped marsh.
    near_w = (ctx["d_small"] < 12) & (ctx["d_small"] <= ctx["d_big"])
    mar = ctx["marsh"]
    rush_ctx = np.repeat(np.repeat(near_w | mar, 4, 0), 4, 1)
    rmask = (chm >= 0.6) & (chm < 3.5) & rush_ctx[:ROWS, :COLS] & (water_full == 0) & (lab == 0) & (lab_s == 0)
    k3 = 3
    rr_ = rmask[:ROWS // k3 * k3, :COLS // k3 * k3].reshape(ROWS // k3, k3, COLS // k3, k3)
    frac = rr_.mean(axis=(1, 3))
    hmax = (chm[:ROWS // k3 * k3, :COLS // k3 * k3] * rmask[:ROWS // k3 * k3, :COLS // k3 * k3]
            ).reshape(ROWS // k3, k3, COLS // k3, k3).max(axis=(1, 3))
    ri, ci = np.nonzero(frac >= 0.25)
    re_ = REGION[0] + (ci + rng.random(ci.size)) * k3 * CELL
    rn_ = REGION[3] - (ri + rng.random(ri.size)) * k3 * CELL
    rh = hmax[ri, ci]
    rd = frac[ri, ci]
    log(f"rush clumps {ri.size}")

    # ---------------------------------------------------------------- write
    a.out.mkdir(parents=True, exist_ok=True)
    idx = np.nonzero(keep)[0]
    out = bytearray(b"BLT1")
    out += struct.pack("<IIIf", idx.size, int(keep_s.sum()), ri.size, QUANT)
    q = lambda v: np.clip(np.round(v / QUANT), 0, 65535).astype("<u2")
    tr = np.zeros(idx.size, dtype=[("e", "<u2"), ("n", "<u2"), ("h", "<u2"), ("cb", "<u2"),
                                   ("r", "u1", 8), ("sp", "u1"), ("shade", "u1")])
    tr["e"] = q(e[idx] - REGION[0]); tr["n"] = q(nn[idx] - REGION[1])
    tr["h"] = np.round(h[idx] * 100); tr["cb"] = np.round(cb[idx] * 100)
    tr["r"] = np.clip(np.round(rad8[idx] / 0.1), 4, 255)
    tr["sp"] = sp[idx]; tr["shade"] = shade[idx]
    out += tr.tobytes()
    si = np.nonzero(keep_s)[0]
    sr = np.zeros(si.size, dtype=[("e", "<u2"), ("n", "<u2"), ("h", "u1"), ("r", "u1"), ("sp", "u1"), ("shade", "u1")])
    sr["e"] = q(es[si] - REGION[0]); sr["n"] = q(ns_[si] - REGION[1])
    sr["h"] = np.clip(np.round(hs[si] / 0.05), 0, 255); sr["r"] = np.clip(np.round(rs[si] / 0.05), 4, 255)
    sr["sp"] = sps[si]; sr["shade"] = 128
    out += sr.tobytes()
    ru = np.zeros(ri.size, dtype=[("e", "<u2"), ("n", "<u2"), ("h", "u1"), ("d", "u1")])
    ru["e"] = q(re_ - REGION[0]); ru["n"] = q(rn_ - REGION[1])
    ru["h"] = np.clip(np.round(rh / 0.05), 0, 255); ru["d"] = np.clip(rd * 255, 0, 255)
    out += ru.tobytes()
    (a.out / "trees.dat").write_bytes(bytes(out))
    counts = {s_: int((sp[idx] == i).sum()) for i, s_ in enumerate(SPECIES)}
    scounts = {s_: int((sps[si] == i).sum()) for i, s_ in enumerate(SHRUBS)}
    meta = {"species": SPECIES, "shrubs": SHRUBS, "origin": [REGION[0], REGION[1]], "quant_m": QUANT,
            "counts": counts, "shrub_counts": scounts, "trees": int(idx.size), "shrub_n": int(si.size),
            "rush_n": int(ri.size),
            "height_m": {"p50": float(np.median(h[idx])), "p99": float(np.percentile(h[idx], 99)),
                         "max": float(h[idx].max())},
            "osm_trees_matched": [osm_hit, osm_in],
            "dropped": {"inside_buildings": int((in_bld & keep_area).sum()),
                        "not_green_2024": int((gone & keep_area & ~in_bld).sum())},
            "source": "USGS 3DEP CA_SantaClaraCounty_2020 lidar (public domain), USDA NAIP 2024 "
                      "(public domain), OpenStreetMap (ODbL)",
            "tool": "drone/sim/tools/bake_baylands_trees.py"}
    (a.out / "trees.json").write_text(json.dumps(meta, indent=1))
    log(f"wrote {len(out) / 1e6:.2f} MB: {meta['trees']} trees, {meta['shrub_n']} shrubs, {meta['rush_n']} rush clumps")
    log(json.dumps(counts))
    log(json.dumps(scounts))
    if a.preview:
        np.savez_compressed(a.work / "trees_debug.npz", e=e[idx], n=nn[idx], h=h[idx], cb=cb[idx],
                            r8=rad8[idx], sp=sp[idx], peak=peak[idx], open=openc[idx],
                            ndvi=ph["ndvi"][idx], R=ph["R"][idx], G=ph["G"][idx], B=ph["B"][idx],
                            se=es[si], sn=ns_[si], sh=hs[si], sr=rs[si], ssp=sps[si],
                            re=re_, rn=rn_, rh=rh,
                            de=e[keep_area & ~keep], dn=nn[keep_area & ~keep],
                            dwhy=(in_bld * 1 + gone * 2)[keep_area & ~keep])
    return 0


if __name__ == "__main__":
    sys.exit(main())
