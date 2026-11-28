#!/usr/bin/env python3
"""Bake the Baylands ground and water data (scripts/baylands_ground.gd and
scripts/baylands_water.gd read it).

Inputs (all public domain or ODbL, see assets/baylands/CREDITS.md):
  --rasters  the 0.5 m lidar grids of bake_baylands_lidar.py + fetch_baylands
             (dtm_w.f32, bld.f32, veg.f32, inten.u8, count.u16, water_full.u8)
  --hi       assets/baylands/_hi (ortho.jpg, nir.jpg: NAIP 2024-05-20, 0.6 m)
  --assets   assets/baylands (features.json, terrain.zi16/.json; committed
             outputs are written here)
  --out      assets/baylands/_ground (gitignored outputs)

Steps (--steps, default all):
  tex     CC0 detail textures from Poly Haven -> assets/baylands/ground_tex/
  albedo  the NAIP photo with its cast shadows and slope shading taken out
          (the scene's own sun puts them back) -> _ground/albedo.jpg, 0.6 m
  classes the surface class of every metre -> _ground/cls.png (1 m) and the
          committed fallback ground_cls.png (2 m)
  aux     _ground/aux.png, 1 m RGBA: NDVI, lidar intensity, puddle depth,
          signed distance to water
  height  _ground/hres.png, 0.5 m: the lidar ground minus the 2 m terrain
          env.ground_height() interpolates (cm, +200), clamped so the drawn
          ground never rises more than 0.28 m above the collision ground
  water   water bodies (level, tidal or not, colour), the tidal flood map
          (tidal.png, 2 m) and the water surface quads (water_quads.dat)

FRAME: the scene's ENU grid, row 0 = north, the same as fetch_baylands.py.

Run on a machine with ~20 GB of RAM to spare (hoopoe):
    uv run --no-project --with numpy --with scipy --with pillow --with rasterio \\
        --with shapely --with scikit-image python bake_baylands_ground.py \\
        --rasters ~/bay/data/rasters --hi ~/bay/out/_hi \\
        --assets drone/sim/godot/assets/baylands --out ~/bay/out/_ground
"""
from __future__ import annotations

import argparse
import io
import json
import math
import struct
import sys
import time
import urllib.request
import zlib
from pathlib import Path

import numpy as np

REGION = (-2500.0, -2400.0, 2500.0, 2400.0)       # e0, n0, e1, n1 (env_baylands.BOUNDS)
UA = "eco-sim/1.0 (drone simulator; baylands ground builder)"

# Surface classes (the shader's order; keep in step with baylands_ground.gd).
SOIL, WATER, ASPHALT, CONCRETE, DG, GRAVEL, LAWN, TURF, DRYGRASS, MUD, CRACKED, MARSH, RIPRAP, \
    BUILDING, CANOPY = range(15)
CLASS_NAMES = ["soil", "water", "asphalt", "concrete", "dg_path", "gravel", "lawn", "synthetic_turf",
               "dry_grass", "mudflat", "cracked_mud", "salt_marsh", "riprap", "building", "canopy"]
PREVIEW = [(150, 120, 90), (40, 70, 140), (40, 40, 45), (200, 200, 195), (205, 175, 125), (150, 140, 120),
           (60, 160, 50), (20, 220, 120), (215, 190, 90), (100, 80, 60), (230, 215, 190), (150, 60, 90),
           (110, 110, 120), (255, 255, 255), (20, 80, 20)]

# CC0 Poly Haven textures, one per material layer (the shader's MATS order):
# name -> (asset, size on the ground in metres; None = the asset's own size).
TEXTURES = {
    "asphalt": ("asphalt_02", None),
    "concrete": ("concrete_pavement", None),
    "dg": ("gravel_floor", None),
    "gravel": ("rocky_trail", None),
    "lawn": ("leafy_grass", None),
    "drygrass": ("sparse_grass", None),
    "soil": ("brown_mud_dry", None),
    "mud": ("brown_mud_02", None),
    "cracked": ("mud_cracked_dry_03", None),
    "riprap": ("gray_rocks", 4.5),          # riprap stones are 0.3-0.6 m, the asset's ~0.15 m
    "litter": ("forest_leaves_02", None),
}
TEX_PX = 512

# Sloughs and channels whose water is the bay's (OSM waterway names).
TIDAL_WAYS = {"Guadalupe Slough", "Alviso Slough", "Guadalupe River", "Calabazas Creek",
              "San Tomas Aquino Creek", "Sunnyvale East Channel", "Sunnyvale West Channel"}
# The highest tide the tidal water surface is built to reach, m NAVD88
# (NOAA 9414575 Coyote Creek, Alviso Slough: MHHW 2.62, HAT 3.06).
TIDE_MAX = 2.7


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, file=sys.stderr, flush=True)


class Grids:
    def __init__(self, d: Path):
        self.d = d
        m = json.loads((d / "grid.json").read_text())
        self.rows, self.cols, self.cell = m["rows"], m["cols"], m["cell_m"]

    def f32(self, name):
        return np.fromfile(self.d / f"{name}.f32", np.float32).reshape(self.rows, self.cols)

    def u8(self, name):
        return np.fromfile(self.d / f"{name}.u8", np.uint8).reshape(self.rows, self.cols)

    def u16(self, name):
        return np.fromfile(self.d / f"{name}.u16", np.uint16).reshape(self.rows, self.cols)


def block(a: np.ndarray, k: int, how: str = "mean") -> np.ndarray:
    h, w = a.shape[0] // k * k, a.shape[1] // k * k
    b = a[:h, :w].reshape(h // k, k, w // k, k)
    if how == "max":
        return np.nanmax(b, axis=(1, 3))
    if how == "nanmean":
        return np.nanmean(b, axis=(1, 3))
    return b.mean(axis=(1, 3))


def load_terrain(assets: Path) -> np.ndarray:
    m = json.loads((assets / "terrain.json").read_text())
    w, h = m["size_px"]
    q = np.frombuffer(zlib.decompress((assets / "terrain.zi16").read_bytes()), "<i2").reshape(h, w)
    return q.astype(np.float32) * float(m.get("scale_m", 0.01))


def img_arr(path: Path) -> np.ndarray:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    return np.asarray(Image.open(path))


def save_png(path: Path, arr: np.ndarray) -> None:
    from PIL import Image
    Image.fromarray(arr).save(path, optimize=True)
    log(f"  wrote {path.name} {arr.shape[1]}x{arr.shape[0]} {path.stat().st_size / 1e6:.1f} MB")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


# =================================================================== textures

def step_tex(assets: Path) -> None:
    from PIL import Image
    out = assets / "ground_tex"
    out.mkdir(exist_ok=True)
    meta = {"source": "Poly Haven (polyhaven.com), CC0 1.0", "size_px": TEX_PX, "textures": {}}
    for name, (aid, size) in TEXTURES.items():
        info = json.loads(_get(f"https://api.polyhaven.com/info/{aid}"))
        files = json.loads(_get(f"https://api.polyhaven.com/files/{aid}"))
        dims = info.get("dimensions", [2000, 2000])
        for key, suffix in (("Diffuse", ""), ("nor_gl", "_n")):
            img = Image.open(io.BytesIO(_get(files[key]["1k"]["jpg"]["url"]))).convert("RGB")
            img.resize((TEX_PX, TEX_PX), Image.LANCZOS).save(out / f"{name}{suffix}.jpg", quality=88, optimize=True)
        meta["textures"][name] = {"asset": aid, "size_m": round(size or float(dims[0]) / 1000.0, 3),
                                  "url": f"https://polyhaven.com/a/{aid}",
                                  "authors": list(info.get("authors", {}).keys())}
        log(f"  texture {name}: {aid}")
    (assets / "ground_tex.json").write_text(json.dumps(meta, indent=1))


# ===================================================================== albedo

def sun_vec(az_deg: float, el_deg: float):
    az, el = math.radians(az_deg), math.radians(el_deg)
    return math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)   # e, n, up


def cast_shadow(top: np.ndarray, ground: np.ndarray, cell: float, az: float, el: float,
                reach: float, step: float | None = None) -> np.ndarray:
    """Cells whose ground is hidden from the sun by an occluder (top, NaN = none):
    walks toward the sun, keeping the highest occluder top minus the climb."""
    de, dn, _ = sun_vec(az, el)
    tan_el = math.tan(math.radians(el))
    h = np.nan_to_num(top, nan=-1e4).astype(np.float32)
    best = np.full(h.shape, -1e4, np.float32)
    step = step or cell
    d = step
    while d <= reach:
        # The occluder at (e + d de', n + d dn') shades (e, n); de' = de/cos(el).
        dx = int(round(d * de / math.cos(math.radians(el)) / cell))
        dy = int(round(-d * dn / math.cos(math.radians(el)) / cell))      # rows grow southward
        src = h[max(dy, 0):h.shape[0] + min(dy, 0), max(dx, 0):h.shape[1] + min(dx, 0)]
        dst = best[max(-dy, 0):h.shape[0] + min(-dy, 0), max(-dx, 0):h.shape[1] + min(-dx, 0)]
        np.maximum(dst, src - d * tan_el, out=dst)
        d += step
    return best > ground + 0.3


def pushpull(img: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Fill the invalid pixels of img (H x W x C) smoothly from the valid ones:
    a mask-weighted image pyramid down to 1 px, then back up, each level
    filling its holes from the coarser one (no streaks, unlike a nearest fill)."""
    from scipy import ndimage
    levels = []
    a = img * valid[..., None]
    wgt = valid.astype(np.float32)
    while min(wgt.shape) > 1:
        levels.append((a, wgt))
        h, w = (wgt.shape[0] + 1) // 2 * 2, (wgt.shape[1] + 1) // 2 * 2
        ap = np.zeros((h, w, a.shape[2]), np.float32)
        ap[:a.shape[0], :a.shape[1]] = a
        wp = np.zeros((h, w), np.float32)
        wp[:wgt.shape[0], :wgt.shape[1]] = wgt
        a = ap.reshape(h // 2, 2, w // 2, 2, -1).sum(axis=(1, 3))
        wgt = wp.reshape(h // 2, 2, w // 2, 2).sum(axis=(1, 3))
    cur = a / np.maximum(wgt, 1e-6)[..., None]
    for a, wgt in reversed(levels):
        up = np.repeat(np.repeat(cur, 2, axis=0), 2, axis=1)[:wgt.shape[0], :wgt.shape[1]]
        for ch in range(up.shape[2]):
            up[..., ch] = ndimage.uniform_filter(up[..., ch], 3)
        have = np.minimum(wgt, 1.0)[..., None]
        cur = a / np.maximum(wgt, 1e-6)[..., None] * have + up * (1.0 - have)
    return cur


def step_albedo(g: Grids, hi: Path, out: Path) -> dict:
    """De-shadow and de-shade the NAIP photo. Returns the fitted sun."""
    from PIL import Image
    from scipy import ndimage
    dtm = g.f32("dtm_w")
    bld = g.f32("bld")
    veg = g.f32("veg")
    occ = np.fmax(np.where(bld - dtm > 1.5, bld, np.nan), np.where(veg - dtm > 1.5, veg, np.nan))
    water = g.u8("water_full").astype(bool)
    rgb = img_arr(hi / "ortho.jpg").astype(np.float32) / 255.0
    H6, W6 = rgb.shape[:2]
    log(f"albedo: photo {W6}x{H6}")
    # White balance on the asphalt (dark to the lidar, no NDVI, grey to the
    # eye): the survey's colour balance leaves it faintly violet.
    nir0 = img_arr(hi / "nir.jpg")[::4, ::4].astype(np.float32) / 255.0
    sub = rgb[::4, ::4]
    i6 = g.u8("inten")[np.clip(((np.arange(sub.shape[0]) * 4 + 0.5) * 1.2).astype(int), 0, g.rows - 1)][
        :, np.clip(((np.arange(sub.shape[1]) * 4 + 0.5) * 1.2).astype(int), 0, g.cols - 1)]
    mx, mn = sub.max(axis=2), sub.min(axis=2)
    nd0 = (nir0 - sub[..., 0]) / (nir0 + sub[..., 0] + 1e-3)
    grey = (i6 > 0) & (i6 < 40) & (nd0 < 0.0) & ((mx - mn) / np.maximum(mx, 1e-3) < 0.12) & (mx > 0.3)
    wb = np.ones(3, np.float32)
    if grey.sum() > 10000:
        med = np.median(sub[grey], axis=0)
        wb = np.clip(med.mean() / med, 0.92, 1.08).astype(np.float32)
        rgb *= wb
    log(f"albedo: white balance {np.round(wb, 3).tolist()} from {int(grey.sum())} asphalt samples")
    del nir0, sub, i6, mx, mn, nd0, grey

    # 1. The sun at the photo's moment, fitted: the (azimuth, elevation) whose
    #    cast shadows best match the photo's dark ground, on 1 m cells of the
    #    business park and the park (tall buildings, lone trees).
    k = 2
    y1 = Image.fromarray((rgb @ np.array([0.299, 0.587, 0.114], np.float32) * 255).astype(np.uint8))
    y1 = np.asarray(y1.resize((g.cols // k, g.rows // k), Image.BOX)).astype(np.float32) / 255.0
    d1 = block(dtm, k)
    o1 = block(np.nan_to_num(occ, nan=-1e4), k, "max")
    o1 = np.where(o1 > -1e3, o1, np.nan)
    w1 = block(water.astype(np.float32), k) > 0.2
    valid = ~(np.isfinite(o1)) & ~w1
    loc = ndimage.uniform_filter(np.where(valid, y1, 0), 25) / np.maximum(ndimage.uniform_filter(valid * 1.0, 25), 1e-3)
    dark = (y1 < 0.62 * loc) & valid
    # Two windows: around the business park west of the park, and the park itself.
    wins = [(slice(1700, 3100), slice(300, 2000)), (slice(2200, 3200), slice(2200, 3400))]
    best = (-1, 0, 0)
    for az in range(100, 265, 10):
        for el in range(45, 81, 5):
            tp = fp = fn = 0
            for rs, cs in wins:
                s = cast_shadow(o1[rs, cs], d1[rs, cs], 1.0, az, el, 40.0) & valid[rs, cs]
                dk = dark[rs, cs]
                tp += int((s & dk).sum())
                fp += int((s & ~dk).sum())
                fn += int((~s & dk).sum())
            f1 = 2 * tp / max(2 * tp + fp + fn, 1)
            if f1 > best[0]:
                best = (f1, az, el)
    f1, az0, el0 = best
    for az in range(az0 - 8, az0 + 9, 2):
        for el in range(el0 - 4, el0 + 5, 2):
            tp = fp = fn = 0
            for rs, cs in wins:
                s = cast_shadow(o1[rs, cs], d1[rs, cs], 1.0, az, el, 40.0) & valid[rs, cs]
                dk = dark[rs, cs]
                tp += int((s & dk).sum())
                fp += int((s & ~dk).sum())
                fn += int((~s & dk).sum())
            f = 2 * tp / max(2 * tp + fp + fn, 1)
            if f > best[0]:
                best = (f, az, el)
    f1, az, el = best
    log(f"albedo: photo sun azimuth {az} deg, elevation {el} deg (shadow match F1 {f1:.2f})")

    # 2. Cast shadows at 0.5 m with that sun, resampled onto the photo's grid.
    sh = cast_shadow(occ, dtm, g.cell, az, el, 45.0)
    sh &= ~np.isfinite(occ)            # roofs and crowns are lit
    sh &= ~water
    sh = ndimage.binary_opening(sh, iterations=1)
    rr = (np.arange(H6, dtype=np.float32) + 0.5) * (0.6 / g.cell) - 0.5
    cc = (np.arange(W6, dtype=np.float32) + 0.5) * (0.6 / g.cell) - 0.5
    ri = np.clip(np.round(rr).astype(int), 0, g.rows - 1)
    ci = np.clip(np.round(cc).astype(int), 0, g.cols - 1)
    m6 = sh[ri][:, ci].astype(np.float32)
    lit_occ = np.isfinite(occ)[ri][:, ci]
    wat6 = water[ri][:, ci]
    del sh
    # Relight: per channel, the ratio of lit to shadowed ground of the same
    # kind just either side of each shadow's edge (vegetation and not, by
    # NDVI, which a shadow barely changes).
    nir = img_arr(hi / "nir.jpg").astype(np.float32) / 255.0
    ndvi = (nir - rgb[..., 0]) / (nir + rgb[..., 0] + 1e-3)
    core = ndimage.binary_erosion(m6 > 0.5, iterations=2)
    ring = ndimage.binary_dilation(m6 > 0.5, iterations=5) & ~ndimage.binary_dilation(m6 > 0.5, iterations=2) \
        & ~lit_occ & ~wat6
    bins = [(-1, 0.1), (0.1, 0.3), (0.3, 1.0)]
    ratio = np.ones((len(bins), 3), np.float32)
    for i, (lo, hi_) in enumerate(bins):
        sel = (ndvi >= lo) & (ndvi < hi_)
        a, b = sel & ring, sel & core
        if a.sum() > 1000 and b.sum() > 1000:
            ratio[i] = np.clip(np.median(rgb[a], axis=0) / np.maximum(np.median(rgb[b], axis=0), 0.02), 1.0, 4.0)
    log(f"albedo: shadow relight ratios {np.round(ratio, 2).tolist()}")
    soft = ndimage.gaussian_filter(m6, 0.8)
    bidx = np.digitize(ndvi, [0.1, 0.3])
    gain = ratio[bidx]                                   # H6 x W6 x 3
    out_rgb = rgb * (1.0 + (gain - 1.0) * soft[..., None])
    del gain, core, ring
    # ... but never brighter than the sunlit ground around it (~19 m): where
    # the lidar's shadow and the photo's disagree (trees lean in the photo,
    # crowns moved since 2020) a lit pixel must not turn white.
    lum = np.array([0.299, 0.587, 0.114], np.float32)
    Y0 = rgb @ lum
    lit = (m6 < 0.1) & ~lit_occ & ~wat6
    ylit = ndimage.uniform_filter(np.where(lit, Y0, 0.0).astype(np.float32), 31) / \
        np.maximum(ndimage.uniform_filter(lit.astype(np.float32), 31), 1e-3)
    Y1 = out_rgb @ lum
    cap = np.where(soft > 0.02, np.minimum(1.0, ylit * 1.02 / np.maximum(Y1, 1e-3)), 1.0)
    out_rgb = np.maximum(rgb, out_rgb * cap[..., None])
    del Y0, Y1, ylit, cap, lit
    # 3. Slope shading: the photo's sun lit slopes facing it brighter; divide
    #    that out (flat ground unchanged).
    ds = ndimage.gaussian_filter(dtm, 2.0)
    gy, gx = np.gradient(ds, g.cell)                     # gy: per row (southward)
    se, sn, su = sun_vec(az, el)
    nrm = np.sqrt(gx * gx + gy * gy + 1.0)
    ndl = (-gx * se + gy * sn + su) / nrm                # north slope = -gy
    amb = 0.35
    f = (amb + np.clip(ndl, 0, 1)) / (amb + su)
    f6 = np.clip(f[ri][:, ci], 0.6, 1.25)
    f6[wat6 | lit_occ] = 1.0
    out_rgb /= f6[..., None]
    del f, ds, gx, gy, nrm, ndl
    # 4. Tree crowns: the trees are drawn in 3D over the ground, so the
    #    photo's crowns (1 m beyond the lidar's, for the photo's lean) are
    #    filled with the nearest ground beside them, softened.
    can = ndimage.binary_dilation(veg - dtm > 2.5, iterations=2)
    can6 = can[ri][:, ci] & ~wat6
    del can
    fill = pushpull(out_rgb, ~can6)
    out_rgb[can6] = fill[can6]
    del fill
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(out_rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)).save(out / "albedo.jpg", quality=90)
    log(f"albedo: wrote albedo.jpg ({(out / 'albedo.jpg').stat().st_size / 1e6:.1f} MB), "
        f"{m6.mean() * 100:.1f}% of the photo was in cast shadow")
    sun = {"azimuth_deg": az, "elevation_deg": el, "f1": round(f1, 3), "shadow_ratio": ratio.round(3).tolist(),
           "white_balance": wb.round(4).tolist()}
    (out / "albedo.json").write_text(json.dumps(sun, indent=1))
    return sun


# ==================================================================== classes

def osm_masks(assets: Path, shape, res: float) -> dict:
    """OSM ways and areas rasterised on the 1 m grid."""
    from rasterio import features
    from rasterio.transform import Affine
    from shapely.geometry import LineString, Polygon
    feats = json.loads((assets / "features.json").read_text())
    tf = Affine(res, 0, REGION[0], 0, -res, REGION[3])
    shapes: dict[str, list] = {k: [] for k in ("road", "parking", "path_asphalt", "path_concrete", "path_unpaved",
                                               "green", "turf", "wetland", "pond", "track")}
    road_w = {"motorway": 15.0, "trunk": 14.0, "primary": 16.0, "secondary": 14.0, "tertiary": 11.0,
              "motorway_link": 7.0, "trunk_link": 7.0, "primary_link": 7.0, "secondary_link": 7.0,
              "tertiary_link": 6.0, "residential": 10.0, "unclassified": 8.0, "service": 5.0}
    for r in feats["roads"]:
        t = r["tags"]
        hw = t.get("highway")
        if hw not in road_w or t.get("bridge") == "yes" or t.get("layer", "0") not in ("0", "-1") and t.get("tunnel"):
            continue
        w = road_w[hw]
        try:
            if "lanes" in t:
                w = max(w * 0.5, float(str(t["lanes"]).split(";")[0]) * 3.6 + (2.0 if hw.startswith("motorway") else 1.0))
            if "width" in t:
                w = float(str(t["width"]).split()[0])
        except ValueError:
            pass
        if hw == "service" and t.get("service") == "parking_aisle":
            w = 6.5
        if len(r["pts"]) >= 2:
            shapes["road"].append(LineString(r["pts"]).buffer(w / 2, cap_style=2))
    for p in feats["paths"]:
        t = p["tags"]
        if len(p["pts"]) < 2 or t.get("bridge") == "yes":
            continue
        s = t.get("surface", "")
        w = float(t["width"]) if str(t.get("width", "")).replace(".", "", 1).isdigit() else \
            (3.0 if t.get("highway") in ("cycleway", "track") else 2.0)
        key = "path_asphalt" if s == "asphalt" else "path_concrete" if s in ("concrete", "paved", "paving_stones") \
            else "path_unpaved"
        shapes[key].append(LineString(p["pts"]).buffer(w / 2))
    for a in feats["areas"]:
        t = a["tags"]
        pts = a["pts"]
        if len(pts) < 4 or pts[0] != pts[-1]:
            continue
        try:
            poly = Polygon(pts)
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception:
            continue
        if t.get("amenity") == "parking" and t.get("parking") not in ("underground", "multi-storey"):
            shapes["parking"].append(poly)
        if t.get("leisure") == "pitch" and t.get("surface") in ("artificial_turf", "artificial_grass"):
            shapes["turf"].append(poly)
        if t.get("landuse") in ("grass", "recreation_ground", "village_green") or \
                t.get("leisure") in ("park", "garden", "pitch", "golf_course", "dog_park") and \
                t.get("surface") not in ("asphalt", "sand", "paved", "acrylic", "concrete"):
            shapes["green"].append(poly)
        if t.get("natural") == "wetland":
            shapes["wetland"].append(poly)
        if t.get("natural") == "water" or t.get("landuse") in ("reservoir", "basin"):
            shapes["pond"].append(poly)
        if t.get("leisure") == "track":
            shapes["track"].append(poly)
    out = {}
    for k, v in shapes.items():
        out[k] = features.rasterize(((s, 1) for s in v), out_shape=shape, transform=tf, fill=0,
                                    dtype=np.uint8).astype(bool) if v else np.zeros(shape, bool)
        log(f"  osm {k}: {len(v)} shapes, {out[k].mean() * 100:.2f}%")
    return out


def mode_filter(cls: np.ndarray, n: int, size: int = 3) -> np.ndarray:
    from scipy import ndimage
    best = np.zeros(cls.shape, np.float32)
    out = cls.copy()
    for c in range(n):
        f = ndimage.uniform_filter((cls == c).astype(np.float32), size)
        m = f > best + 1e-6
        out[m] = c
        best[m] = f[m]
    return out


def step_classes(g: Grids, hi: Path, assets: Path, out: Path) -> None:
    from PIL import Image
    from scipy import ndimage
    Image.MAX_IMAGE_PIXELS = None
    k = 2                                   # 0.5 m -> 1 m
    R, C = g.rows // k, g.cols // k
    alb = Image.open(out / "albedo.jpg") if (out / "albedo.jpg").exists() else Image.open(hi / "ortho.jpg")
    rgb = np.asarray(alb.resize((C, R), Image.BOX)).astype(np.float32) / 255.0
    nir = np.asarray(Image.open(hi / "nir.jpg").resize((C, R), Image.BOX)).astype(np.float32) / 255.0
    r_, g_, b_ = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    ndvi = (nir - r_) / (nir + r_ + 1e-3)
    Y = 0.299 * r_ + 0.587 * g_ + 0.114 * b_
    mx = rgb.max(axis=2)
    sat = (mx - rgb.min(axis=2)) / np.maximum(mx, 1e-3)
    dtm05 = g.f32("dtm_w")
    dtm = block(dtm05, k)
    bldh = block(np.nan_to_num(g.f32("bld") - dtm05, nan=0.0), k, "max")
    vegh = block(np.nan_to_num(g.f32("veg") - dtm05, nan=0.0), k, "max")
    wat = block(g.u8("water_full").astype(np.float32), k) > 0.5
    inten05 = g.u8("inten").astype(np.float32)
    inten = block(np.where(inten05 > 0, inten05, np.nan), k, "nanmean")
    has_i = np.isfinite(inten)
    inten = np.nan_to_num(inten, nan=-1.0)       # -1 = no ground return (under trees, roofs)
    # Rough ground (riprap): the lidar ground's own bumpiness at 0.5 m.
    hp = dtm05 - ndimage.gaussian_filter(dtm05, 3.0)
    rough = block(np.sqrt(ndimage.uniform_filter(hp * hp, 5)), k)
    del hp
    gy, gx = np.gradient(ndimage.gaussian_filter(dtm, 1.0), 1.0)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    # Levee crests and berms: ground well above its surroundings, flattish.
    crest = (dtm - ndimage.uniform_filter(dtm, 41) > 0.9) & (slope < 9.0)
    # The nearest water's level and the distance to it.
    dist, (iy, ix) = ndimage.distance_transform_edt(~wat, return_indices=True)
    rel = dtm - dtm[iy, ix]
    margin = (dist < 35.0) & (rel < 0.45) & ~wat
    osm = osm_masks(assets, (R, C), 1.0)
    log("classes: rules")

    cls = np.full((R, C), SOIL, np.uint8)
    veg = (ndvi > 0.03) & ~((sat < 0.12) & (ndvi < 0.08))
    cls[veg] = DRYGRASS
    # Lawns: watered green over a whole area (15 m), not single lush pixels in
    # a meadow (those stay annual grass, greener, and turn with the season).
    nd15 = ndimage.uniform_filter(ndvi, 15)
    lush = veg & (ndvi > 0.3) & ((nd15 > 0.42) | (osm["green"] & (nd15 > 0.32)))
    cls[lush] = LAWN
    # Synthetic turf: green to the eye, no chlorophyll to the NIR band.
    turf = ((g_ > r_ * 1.04) & (g_ > b_ * 1.04) & (ndvi < 0.12) & (Y < 0.5)) | osm["turf"]
    turf = ndimage.binary_opening(turf, iterations=2)
    cls[turf] = TURF
    bare = ~veg & ~turf
    # NAIP's stretch puts asphalt at a mid grey (median Y 0.59 on the main
    # roads); the lidar's 1064 nm intensity is what separates it: asphalt
    # ~30, concrete ~100, decomposed granite ~120 (medians over OSM-tagged
    # ways, 0-255 stretch).
    tan = (r_ > g_ * 1.02) & (g_ > b_ * 1.02) & (sat > 0.15)
    grey = sat < 0.15
    lo_i = has_i & (inten < 58)
    hi_i = has_i & (inten >= 58)
    paved_osm = osm["road"] | osm["parking"]
    cls[bare & grey & lo_i] = ASPHALT
    cls[bare & grey & hi_i & (Y > 0.6)] = CONCRETE
    cls[bare & ~has_i & grey & (Y < 0.7)] = ASPHALT
    cls[bare & paved_osm & ~tan & ~(hi_i & (Y > 0.78))] = ASPHALT
    cls[bare & osm["path_asphalt"]] = ASPHALT
    cls[bare & osm["path_concrete"] & ~lo_i] = CONCRETE
    unp = bare & osm["path_unpaved"] & ~(grey & lo_i)
    cls[unp] = DG
    crest_ok = bare & crest & ~paved_osm & ~osm["path_concrete"] & ~(grey & lo_i)
    cls[crest_ok] = GRAVEL
    # Water's edge: mudflat where bare, salt marsh where vegetated.
    cls[margin & bare & ~paved_osm & ~np.isin(cls, (ASPHALT, CONCRETE, GRAVEL))] = MUD
    # Pickleweed is green in May (NDVI > 0.28); the annual grass the OSM
    # wetland outlines also hold was already curing.
    cls[margin & veg & ~lush] = MARSH
    cls[osm["wetland"] & veg & (ndvi > 0.28)] = MARSH
    # Dry pond floors (mapped as water, no water in the lidar or photo).
    cls[osm["pond"] & ~wat & bare & ~paved_osm & ~(cls == ASPHALT) & (rel < 0.6) & (slope < 4.0)] = CRACKED
    # Riprap: rough, sloping, bare ground near the water.
    rip = (rough > 0.07) & (slope > 12.0) & (dist < 14.0) & ~veg & ~wat
    rip = ndimage.binary_opening(rip, iterations=1)
    cls[rip] = RIPRAP
    cls = mode_filter(cls, 13, 3)
    # Under trees: the ground beside them (the photo is filled the same way),
    # leaf litter where that is grass or bare ground.
    can = vegh > 2.5
    best = np.zeros(cls.shape, np.float32)
    near = np.full(cls.shape, DRYGRASS, np.uint8)
    for c in range(13):
        f = ndimage.uniform_filter(((cls == c) & ~can).astype(np.float32), 11)
        m = f > best + 1e-6
        near[m] = c
        best[m] = f[m]
    cls[can] = np.where(np.isin(near[can], (LAWN, DRYGRASS, SOIL, MARSH, DG)), CANOPY, near[can])
    cls[bldh > 2.0] = BUILDING
    cls[wat] = WATER
    save_png(out / "cls.png", cls)
    # Committed fallback, 2 m (the class of each 2 x 2 block's NW metre after a mode pass).
    c2 = mode_filter(cls, 15, 2)[::2, ::2]
    save_png(assets / "ground_cls.png", c2)
    hist = np.bincount(cls.ravel(), minlength=15) / cls.size * 100
    log("classes: " + ", ".join(f"{n} {h:.1f}%" for n, h in zip(CLASS_NAMES, hist)))
    pal = np.array(PREVIEW, np.uint8)
    Image.fromarray(pal[cls]).resize((C // 4, R // 4), Image.NEAREST).save(out / "cls_preview.png")
    (out / "classes.json").write_text(json.dumps({"classes": CLASS_NAMES, "res_m": 1.0,
        "bounds_enu": list(REGION), "row0": "north"}, indent=1))


# ======================================================================== aux

def step_aux(g: Grids, hi: Path, out: Path) -> None:
    from PIL import Image
    from scipy import ndimage
    from skimage.morphology import reconstruction
    Image.MAX_IMAGE_PIXELS = None
    k = 2
    R, C = g.rows // k, g.cols // k
    alb = Image.open(out / "albedo.jpg") if (out / "albedo.jpg").exists() else Image.open(hi / "ortho.jpg")
    red = np.asarray(alb.resize((C, R), Image.BOX))[..., 0].astype(np.float32) / 255.0
    nir = np.asarray(Image.open(hi / "nir.jpg").resize((C, R), Image.BOX)).astype(np.float32) / 255.0
    ndvi = (nir - red) / (nir + red + 1e-3)
    inten05 = g.u8("inten").astype(np.float32)
    inten = block(np.where(inten05 > 0, inten05, np.nan), k, "nanmean")
    inten = np.nan_to_num(inten, nan=float(np.nanmedian(inten)))
    dtm = block(g.f32("dtm_w"), k)
    wat = block(g.u8("water_full").astype(np.float32), k) > 0.5
    # Puddles: how deep each low spot fills before it spills (1 m ground).
    seed = dtm.copy()
    seed[1:-1, 1:-1] = dtm.max()
    filled = reconstruction(seed, dtm, method="erosion")
    # ... but only hollows a few metres across: a whole car park or meadow
    # that sits a few cm low (the lidar cannot see its drains) is not a pond.
    closed = ndimage.grey_closing(dtm, size=(7, 7))
    depth = np.clip(np.minimum(filled, closed) - dtm, 0.0, 0.3)
    depth[wat] = 0.0
    sd = ndimage.distance_transform_edt(wat) - ndimage.distance_transform_edt(~wat)
    aux = np.stack([np.clip((ndvi + 0.2) * 255.0, 0, 255), np.clip(inten, 0, 255), depth * 800.0,
                    np.clip(128.0 + sd * 4.0, 0, 255)], axis=-1)
    save_png(out / "aux.png", (aux + 0.5).astype(np.uint8))


# ===================================================================== height

def step_height(g: Grids, assets: Path, out: Path) -> None:
    from scipy import ndimage
    t2 = load_terrain(assets)
    th, tw = t2.shape
    dtm = g.f32("dtm_w")
    rows, cols = dtm.shape
    # env.ground_height(): posts at the 2 m cells' centres, bilinear, clamped.
    fy = (np.arange(rows, dtype=np.float64) + 0.5) * (g.cell / 2.0) - 0.5
    fx = (np.arange(cols, dtype=np.float64) + 0.5) * (g.cell / 2.0) - 0.5
    fy = np.clip(fy, 0, th - 1)
    fx = np.clip(fx, 0, tw - 1)
    y0 = np.minimum(fy.astype(int), th - 2)
    x0 = np.minimum(fx.astype(int), tw - 2)
    ty = (fy - y0).astype(np.float32)[:, None]
    tx = (fx - x0).astype(np.float32)[None, :]
    a = t2[y0][:, x0]
    b = t2[y0][:, x0 + 1]
    c = t2[y0 + 1][:, x0]
    d = t2[y0 + 1][:, x0 + 1]
    gh = (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty
    del a, b, c, d
    res = dtm - gh
    wat = g.u8("water_full").astype(bool)
    res[wat] = 0.0
    lo, hi_ = -2.0, 0.28
    log(f"height: residual percentiles (land) 0.1/1/50/99/99.9: "
        f"{np.round(np.percentile(res[~wat], [0.1, 1, 50, 99, 99.9]), 3).tolist()}; "
        f"{(res > hi_).mean() * 100:.3f}% clamped at +{hi_} m")
    q = np.clip(np.round(res * 100.0) + 200.0, 0, 228).astype(np.uint8)
    save_png(out / "hres.png", q)
    (out / "hres.json").write_text(json.dumps({"res_m": g.cell, "bounds_enu": list(REGION), "row0": "north",
        "encoding": "metres = (v - 200) / 100, added to env.ground_height(); clamped to [-2.0, +0.28]"}, indent=1))


# ====================================================================== water

def quads(mask: np.ndarray, max_s: int = 64):
    """Aligned square blocks covering mask exactly (a quadtree, greedy from the largest)."""
    H, W = mask.shape
    used = np.zeros_like(mask)
    out = []
    s = max_s
    while s >= 1:
        h, w = H // s * s, W // s * s
        full = mask[:h, :w].reshape(h // s, s, w // s, s).all(axis=(1, 3))
        free = ~used[:h, :w].reshape(h // s, s, w // s, s).any(axis=(1, 3))
        ys, xs = np.nonzero(full & free)
        for y, x in zip(ys, xs):
            out.append((x * s, y * s, s))
            used[y * s:(y + 1) * s, x * s:(x + 1) * s] = True
        s //= 2
    rest = mask & ~used                     # the ragged right/bottom edge
    for y, x in zip(*np.nonzero(rest)):
        out.append((x, y, 1))
    return out


def step_water(g: Grids, hi: Path, assets: Path, out: Path) -> None:
    from PIL import Image
    from rasterio import features
    from rasterio.transform import Affine
    from scipy import ndimage
    from shapely.geometry import LineString, Polygon
    Image.MAX_IMAGE_PIXELS = None
    w05 = g.u8("water_full").astype(bool)
    dtm05 = g.f32("dtm_w")
    feats = json.loads((assets / "features.json").read_text())
    tf05 = Affine(g.cell, 0, REGION[0], 0, -g.cell, REGION[3])
    # Tidal water: the water within 60 m of a tidal slough's or channel's
    # centre line that is not inside a mapped pond (the lidar's water mask
    # joins some ponds to the slough beside them through low levee gaps),
    # in pieces whose level is a tide's (< 1.6 m), not an upstream pool's.
    lines = [LineString(w["pts"]).buffer(60.0) for w in feats["waterways"]
             if w["tags"].get("name") in TIDAL_WAYS and len(w["pts"]) >= 2]
    near = features.rasterize(((s, 1) for s in lines), out_shape=w05.shape, transform=tf05, fill=0,
                              dtype=np.uint8).astype(bool)
    ponds = []
    for ar in feats["areas"]:
        t = ar["tags"]
        pts = ar["pts"]
        if len(pts) < 4 or pts[0] != pts[-1]:
            continue
        is_pond = t.get("landuse") in ("reservoir", "basin") or t.get("water") in ("pond", "salt_pond", "reservoir") \
            or (t.get("natural") == "water" and t.get("water") not in ("river", "stream", "canal", "ditch", "tidal"))
        if is_pond:
            try:
                ponds.append(Polygon(pts).buffer(0))
            except Exception:
                pass
    pond05 = features.rasterize(((s, 1) for s in ponds), out_shape=w05.shape, transform=tf05, fill=0,
                                dtype=np.uint8).astype(bool) if ponds else np.zeros_like(w05)
    tw05 = w05 & near & ~pond05
    tl, ntl = ndimage.label(tw05)
    tlev = ndimage.median(dtm05, tl, np.arange(1, ntl + 1))
    tarea = ndimage.sum(np.ones_like(tl, np.float32), tl, np.arange(1, ntl + 1)) * g.cell * g.cell
    ok = np.zeros(ntl + 1, bool)
    ok[1:] = (tlev < 1.6) & (tarea >= 200.0)
    tw05 = ok[tl]
    del tl, near, pond05
    lab, nl = ndimage.label(w05 & ~tw05)    # everything else: ponds, pools, basins
    idx = np.arange(1, nl + 1)
    level = ndimage.median(dtm05, lab, idx)
    area = ndimage.sum(np.ones_like(lab, np.float32), lab, idx) * g.cell * g.cell
    # The photo's colour of each body (its interior, away from the banks).
    alb = Image.open(out / "albedo.jpg") if (out / "albedo.jpg").exists() else Image.open(hi / "ortho.jpg")
    k = 2
    R1, C1 = g.rows // k, g.cols // k
    rgb1 = np.asarray(alb.resize((C1, R1), Image.BOX)).astype(np.float32)
    lab1 = lab[::k, ::k]
    inner = ndimage.binary_erosion(lab1 > 0, iterations=4)
    cols = np.zeros((nl + 1, 3), np.float32)
    for c in range(3):
        cols[1:, c] = ndimage.median(rgb1[..., c], np.where(inner, lab1, 0), idx)
        fb = ndimage.median(rgb1[..., c], lab1, idx)
        cols[1:, c] = np.where(np.isfinite(cols[1:, c]) & (cols[1:, c] > 0), cols[1:, c], np.nan_to_num(fb))
    tin = ndimage.binary_erosion(tw05[::k, ::k], iterations=3)
    tcol = np.median(rgb1[tin], axis=0) if tin.any() else np.array([90, 95, 80])
    # 2 m cells (the env's water grid): the body of each, water where it is
    # half water (is_water's rule).
    k2 = 4
    R2, C2 = g.rows // k2, g.cols // k2
    share = block(w05.astype(np.float32), k2)
    lab2 = block(lab.astype(np.float32), k2, "max").astype(np.int32)
    lab2[share < 0.5] = 0
    t2 = load_terrain(assets)
    tw2 = block(tw05.astype(np.float32), k2) >= 0.5
    lab2[tw2] = 0
    # The tidal flood map: the tide at which each 2 m cell joins the bay's
    # water through ground lower than that tide. Only within 60 m of the
    # tidal water, and never over built or watered ground (levees, flood
    # walls and gates the 2 m grid cannot see keep the business park dry).
    allowed = ndimage.distance_transform_edt(~tw2) * 2.0 <= 60.0
    if (out / "cls.png").exists():
        c1 = np.asarray(Image.open(out / "cls.png"))
        dev = np.isin(c1, (ASPHALT, CONCRETE, BUILDING, LAWN, TURF)).astype(np.float32)
        allowed &= block(dev, 2) < 0.5
    allowed &= (lab2 == 0)
    spill = np.full((R2, C2), np.inf, np.float32)
    for t in np.arange(-0.6, TIDE_MAX + 0.001, 0.05):
        wet = tw2 | ((t2 < t) & allowed)
        l, _ = ndimage.label(wet)
        keep = np.unique(l[tw2])
        keep = keep[keep > 0]
        reach = np.isin(l, keep) & ~np.isfinite(spill)
        spill[reach] = t
    spill[tw2] = -0.6
    enc = np.where(np.isfinite(spill), np.clip((spill + 1.0) * 60.0, 0, 254), 255).astype(np.uint8)
    save_png(assets / "tidal.png", enc)
    tidal = np.zeros(nl + 1, bool)
    # Surface quads: the tidal water up to TIDE_MAX (body 0, drawn at the
    # tide); every other body at its level, reaching 4 m under its banks.
    bodies = [{"id": 0, "tidal": True, "level": None, "name": "tidal sloughs and channels",
               "colour": [int(v) for v in tcol], "area_m2": float(tw05.sum() * g.cell * g.cell)}]
    qrecs = []
    tidal_cover = np.isfinite(spill) & (spill <= TIDE_MAX)
    # Small squares (<= 8 m): a tidal vertex the tide cannot reach sinks out
    # of sight, and a big square would take its whole side down with it.
    for (x, y, s) in quads(tidal_cover, 4):
        qrecs.append((x, y, s, 0))
    big = [i for i in range(1, nl + 1) if not tidal[i] and area[i - 1] >= 100.0]
    taken = tidal_cover.copy()
    lab2d = ndimage.grey_dilation(lab2, size=(5, 5))
    for i in sorted(big, key=lambda i: -area[i - 1]):
        m = ndimage.binary_dilation(lab2 == i, iterations=2) & ~taken & ((lab2 == 0) | (lab2 == i)) & \
            ((lab2d == i) | (lab2 == i))
        if not m.any():
            continue
        bid = len(bodies)
        bodies.append({"id": bid, "tidal": False, "level": round(float(level[i - 1]), 3),
                       "colour": [int(v) for v in cols[i]], "area_m2": round(float(area[i - 1]), 1)})
        y0, x0 = np.nonzero(m)
        sl = (slice(y0.min(), y0.max() + 1), slice(x0.min(), x0.max() + 1))
        for (x, y, s) in quads(m[sl]):
            qrecs.append((x + sl[1].start, y + sl[0].start, s, bid))
        taken |= m
    body = bytearray(b"BWQ1" + struct.pack("<II", len(qrecs), len(bodies)))
    for x, y, s, b in qrecs:
        body += struct.pack("<HHHH", x, y, s, b)
    (assets / "water_quads.dat").write_bytes(bytes(body))
    meta = {"cell_m": 2.0, "bounds_enu": list(REGION), "row0": "north",
            "format": "BWQ1, u32 quads, u32 bodies, then per quad u16 col, row (2 m cells from the NW corner), "
                      "u16 size (cells), u16 body",
            "tide_max_m": TIDE_MAX,
            "tidal_png": "tidal.png: 2 m, the tide (m NAVD88) at which the cell joins the bay = v / 60 - 1; "
                         "255 = never (behind levees higher than tide_max)",
            "tide_datums_navd88_m": {"MLLW": -0.08, "MSL": 1.40, "MHHW": 2.62, "HAT": 3.06,
                                     "station": "NOAA 9414575 Coyote Creek, Alviso Slough (station datum taken as NAVD88)"},
            "bodies": bodies}
    (assets / "water_bodies.json").write_text(json.dumps(meta, indent=1))
    log(f"water: {nl} pond bodies, tidal water {tw05.sum() * g.cell ** 2 / 1e6:.2f} km2, "
        f"{len(bodies) - 1} drawn ponds, {len(qrecs)} quads; tidal reach at {TIDE_MAX} m: "
        f"{tidal_cover.sum() * 4 / 1e6:.2f} km2")
    for b in bodies[:12]:
        log(f"   body {b['id']}: level {b['level']} area {b['area_m2'] / 1e4:.1f} ha colour {b['colour']}")
    for i in np.argsort(-area)[:15]:
        log(f"   label {i + 1}: area {area[i] / 1e4:.1f} ha level {level[i]:.2f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rasters", required=True)
    ap.add_argument("--hi", required=True)
    ap.add_argument("--assets", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", default="tex,albedo,classes,aux,height,water")
    a = ap.parse_args(argv)
    rasters, hi, assets, out = (Path(p).expanduser() for p in (a.rasters, a.hi, a.assets, a.out))
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gdignore").touch()
    g = Grids(rasters)
    steps = a.steps.split(",")
    if "tex" in steps:
        step_tex(assets)
    if "albedo" in steps:
        step_albedo(g, hi, out)
    if "classes" in steps:
        step_classes(g, hi, assets, out)
    if "aux" in steps:
        step_aux(g, hi, out)
    if "height" in steps:
        step_height(g, assets, out)
    if "water" in steps:
        step_water(g, hi, assets, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
