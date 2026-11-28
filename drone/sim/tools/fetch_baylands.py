#!/usr/bin/env python3
"""Build Baylands Park, Sunnyvale for the Godot sim, from measured public data.

The scene exists to train autopilots that then fly the real field, so the
rule here is MEASURED over invented: ground, roofs, trees, poles and wires
come from the 2020 county lidar; footprints, paths and fences from
OpenStreetMap; the ground's colour from the USDA NAIP orthophoto.

Steps (each skips its output if present; --force redoes it):
  lidar     download the lidar tiles covering REGION (NOAA Digital Coast's
            COPC mirror of USGS 3DEP CA_SantaClaraCounty_2020; ~3.5 GB)
  rasters   bake_baylands_lidar.py: 0.5 m grids into WORK/rasters
  water     water.png (2 m mask) + flat water surfaces in the ground model
  terrain   terrain.zi16 (2 m, committed) + _hi/*.z* (0.5 m grids, gitignored)
  osm       OSM extract -> WORK/osm.json
  buildings buildings.dat/.json: OSM footprints + lidar-only footprints,
            heights from the lidar
  features  features.json (paths, fences, power, parking, amenities, park)
  ortho     ortho.jpg (1 m, committed) + _hi/ortho.jpg (native, gitignored)

    uv run --no-project --with "laspy[lazrs]" --with numpy --with scipy \\
        --with pyproj --with pillow --with shapely --with rasterio \\
        python fetch_baylands.py --work ~/bay/data --out ../godot/assets/baylands

UNITS: metres. The NOAA mirror is UTM 10N metres + NAVD88 metres; the USGS
rockyweb copy of the same survey is in US survey feet, so do not swap the
source without converting (bake_baylands_lidar.py refuses feet).

FRAME: ENU metres around DATUM (the middle of Baylands Park), flat-earth
formula identical to env_baylands.gd's geo_to_sim(); Godot = (e, y, -n).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATUM_LAT, DATUM_LON = 37.4125, -121.9970
REGION = (-2500.0, -2400.0, 2500.0, 2400.0)        # e0, n0, e1, n1
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))
CELL = 0.5                                          # rasters
TERRAIN_RES = 2.0                                   # committed terrain
UA = "eco-sim/1.0 (drone simulator; baylands dataset builder)"

NOAA = "https://noaa-nos-coastal-lidar-pds.s3.amazonaws.com/laz/geoid18/9329/"
OVERPASS = "https://overpass-api.de/api/interpreter"


def enu(lat, lon):
    return ((np.asarray(lon) - DATUM_LON) * M_PER_DEG_LON, (np.asarray(lat) - DATUM_LAT) * M_PER_DEG_LAT)


def geo(e, n):
    return (DATUM_LAT + np.asarray(n) / M_PER_DEG_LAT, DATUM_LON + np.asarray(e) / M_PER_DEG_LON)


def bbox_ll(pad: float = 0.0):
    e0, n0, e1, n1 = REGION
    s, w = geo(e0 - pad, n0 - pad)
    n, e = geo(e1 + pad, n1 + pad)
    return float(s), float(w), float(n), float(e)


def _get(url: str, data: bytes | None = None, timeout: int = 180, tries: int = 5) -> bytes:
    last = ""
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:                    # noqa: BLE001 - retry anything
            last = str(exc)
            print(f"  {url[:60]}... attempt {k}: {last}", flush=True)
            time.sleep(min(60, 4 * 2 ** k))
    raise RuntimeError(f"{url}: {last}")


# --------------------------------------------------------------- rasters

class Grids:
    """The 0.5 m lidar grids (bake_baylands_lidar.py), row 0 = north."""

    def __init__(self, d: Path):
        self.d = d
        self.meta = json.loads((d / "grid.json").read_text())
        self.rows, self.cols = self.meta["rows"], self.meta["cols"]

    def f32(self, name):
        return np.fromfile(self.d / f"{name}.f32", np.float32).reshape(self.rows, self.cols)

    def u8(self, name):
        return np.fromfile(self.d / f"{name}.u8", np.uint8).reshape(self.rows, self.cols)


def step_lidar(work: Path, force: bool) -> None:
    tiles = work / "copc"
    tiles.mkdir(parents=True, exist_ok=True)
    mm = work / "minmax_ca2020_santa_clara_m9329.csv"
    if not mm.exists():
        mm.write_bytes(_get(NOAA + "minmax_ca2020_santa_clara_m9329.csv"))
    from pyproj import Transformer
    t = Transformer.from_crs(4326, 26910, always_xy=True)
    s, w, n, e = bbox_ll(pad=100.0)
    xs, ys = t.transform([w, e, w, e], [s, s, n, n])
    X0, X1, Y0, Y1 = min(xs), max(xs), min(ys), max(ys)
    names = []
    for line in mm.read_text().splitlines()[1:]:
        name = line.split(",")[0].strip()
        # The mirror's CSV writes numpy reprs into its last columns: take numbers.
        nums = [float(v) for v in re.findall(r"-?\d+\.\d+|-?\d+", line[len(name):].replace("float64", ""))]
        x0, x1, y0, y1 = nums[:4]
        if x1 > X0 and x0 < X1 and y1 > Y0 and y0 < Y1:
            names.append(name)
    todo = [nm for nm in names if force or not (tiles / nm).exists()]
    print(f"lidar: {len(names)} tiles cover the region, {len(todo)} to download")
    if todo:
        (work / "copc_urls.txt").write_text("".join(NOAA + nm + "\n" for nm in todo))
        subprocess.run(f"cd {tiles} && xargs -P 8 -n 1 curl -s -S -O -C - --retry 5 < {work / 'copc_urls.txt'}",
                       shell=True, check=True)


def step_rasters(work: Path, force: bool) -> None:
    out = work / "rasters"
    if (out / "grid.json").exists() and not force:
        return
    subprocess.run([sys.executable, str(HERE / "bake_baylands_lidar.py"), "--tiles", str(work / "copc"),
                    "--out", str(out)], check=True)


def _zi16(path: Path, arr: np.ndarray, scale: float) -> None:
    q = np.clip(np.round(arr / scale), -32768, 32767).astype("<i2")
    path.write_bytes(zlib.compress(q.tobytes(), 9))


def step_terrain(g: Grids, out: Path, force: bool) -> None:
    if (out / "terrain.json").exists() and not force:
        return
    wpath = g.d / "dtm_w.f32"
    dtm = np.fromfile(wpath, np.float32).reshape(g.rows, g.cols) if wpath.exists() else g.f32("dtm")
    k = int(TERRAIN_RES / CELL)
    # Block MEAN (not a point sample) so a 2 m post is the ground it stands for.
    coarse = dtm[: dtm.shape[0] // k * k, : dtm.shape[1] // k * k]
    coarse = coarse.reshape(coarse.shape[0] // k, k, coarse.shape[1] // k, k).mean(axis=(1, 3))
    _zi16(out / "terrain.zi16", coarse, 0.01)
    e0, n0, e1, n1 = REGION
    (out / "terrain.json").write_text(json.dumps({
        "bounds_enu": [e0, n0, e1, n1], "res_m": TERRAIN_RES,
        "size_px": [coarse.shape[1], coarse.shape[0]], "scale_m": 0.01,
        "min_m": round(float(coarse.min()), 2), "max_m": round(float(coarse.max()), 2),
        "encoding": "zlib int16 centimetres above NAVD88, row 0 = north edge, posts at cell centres",
        "hi": "_hi/<name>.zf32 / .zu8: zlib float32 / uint8, 0.5 m, row 0 north (gitignored; "
              "this tool writes them): dtm dsm bld veg wire tower other inten water",
        "source": "USGS 3DEP CA_SantaClaraCounty_2020 lidar, ground class (public domain)",
    }, indent=1))
    hi = out / "_hi"
    hi.mkdir(exist_ok=True)
    (hi / "dtm.zf32").write_bytes(zlib.compress(dtm.astype("<f4").tobytes(), 6))
    for name in ("dsm", "bld", "veg", "wire", "tower", "other"):
        (hi / f"{name}.zf32").write_bytes(zlib.compress(g.f32(name).astype("<f4").tobytes(), 6))
    (hi / "inten.zu8").write_bytes(zlib.compress(g.u8("inten").tobytes(), 6))
    wf = g.d / "water_full.u8"
    (hi / "water.zu8").write_bytes(zlib.compress((np.fromfile(wf, np.uint8) if wf.exists() else g.u8("water")).tobytes(), 6))
    (hi / "grid.json").write_text(json.dumps(g.meta, indent=1))
    print(f"terrain: {coarse.shape[1]}x{coarse.shape[0]} @ {TERRAIN_RES} m, "
          f"{coarse.min():.2f}..{coarse.max():.2f} m; {(out / 'terrain.zi16').stat().st_size / 1e6:.1f} MB")


# ------------------------------------------------------------------- OSM

OSM_QUERY = """[out:json][timeout:180];
(
  way["building"]({b});
  way["building:part"]({b});
  way["highway"]({b});
  way["barrier"]({b}); node["barrier"]({b});
  way["power"]({b}); node["power"]({b});
  way["landuse"]({b}); relation["landuse"]({b});
  way["natural"]({b}); relation["natural"]({b});
  way["leisure"]({b}); relation["leisure"]({b});
  way["amenity"]({b}); node["amenity"]({b});
  way["waterway"]({b}); way["water"]({b}); relation["water"]({b});
  way["man_made"]({b}); node["man_made"]({b});
  node["highway"]({b});
  node["natural"]({b});
  way["railway"]({b});
  node["tourism"]({b}); way["tourism"]({b});
  node["leisure"]({b});
  way["aeroway"]({b}); node["aeroway"]({b});
);
out body geom;"""


def step_osm(work: Path, force: bool) -> dict:
    p = work / "osm.json"
    if not p.exists() or force:
        s, w, n, e = bbox_ll(pad=50.0)
        q = OSM_QUERY.replace("{b}", f"{s:.5f},{w:.5f},{n:.5f},{e:.5f}")
        p.write_bytes(_get(OVERPASS, urllib.parse.urlencode({"data": q}).encode(), timeout=300))
    d = json.loads(p.read_text())
    print(f"osm: {len(d['elements'])} elements")
    return d


def _ring_from(geom) -> list[tuple[float, float]] | None:
    if not geom or len(geom) < 4:
        return None
    e, n = enu([p["lat"] for p in geom], [p["lon"] for p in geom])
    pts = list(zip(map(float, e), map(float, n)))
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts if len(pts) >= 3 else None


def _ccw(pts):
    a = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))
    return pts if a > 0 else pts[::-1]


# -------------------------------------------------------------- buildings

KINDS = ["commercial", "industrial", "warehouse", "residential", "public", "shelter",
         "restroom", "utility", "tank", "roof", "other"]


def _kind(tags: dict) -> int:
    b = tags.get("building", "yes")
    m = {"commercial": 0, "office": 0, "retail": 0, "industrial": 1, "warehouse": 2,
         "house": 3, "residential": 3, "apartments": 3, "public": 4, "civic": 4, "school": 4,
         "government": 4, "shelter": 5, "pavilion": 5, "toilets": 6, "service": 7,
         "transformer_tower": 7, "storage_tank": 8, "roof": 9, "carport": 9}
    if tags.get("amenity") == "toilets":
        return 6
    if tags.get("man_made") == "storage_tank":
        return 8
    return m.get(b, 10)


def _num(v) -> float | None:
    try:
        return float(str(v).split()[0])
    except (TypeError, ValueError):
        return None


def step_buildings(g: Grids, osm: dict, out: Path, force: bool) -> None:
    if (out / "buildings.json").exists() and not force:
        return
    from shapely.geometry import Polygon, shape
    from rasterio import features
    from rasterio.transform import Affine
    from scipy import ndimage
    dtm = g.f32("dtm"); bld = g.f32("bld"); dsm = g.f32("dsm")
    e0, n0, e1, n1 = REGION
    tf = Affine(CELL, 0, e0, 0, -CELL, n1)          # pixel -> ENU
    recs = []                                        # (ring, h, kind, flags, levels, name)
    names: list[str] = []
    polys = []

    def heights(poly):
        r0 = max(0, int((n1 - poly.bounds[3]) / CELL) - 2); r1 = min(g.rows, int((n1 - poly.bounds[1]) / CELL) + 3)
        c0 = max(0, int((poly.bounds[0] - e0) / CELL) - 2); c1 = min(g.cols, int((poly.bounds[2] - e0) / CELL) + 3)
        if r1 <= r0 or c1 <= c0:
            return float("nan"), float("nan")
        sub = tf * Affine.translation(c0, r0)
        m = features.geometry_mask([poly], (r1 - r0, c1 - c0), sub, invert=True)
        if m.sum() < 2:
            m = features.geometry_mask([poly], (r1 - r0, c1 - c0), sub, invert=True, all_touched=True)
        roof = bld[r0:r1, c0:c1][m]
        roof = roof[np.isfinite(roof)]
        ground = dtm[r0:r1, c0:c1][m]
        gnd = float(np.percentile(ground, 10)) if ground.size else 0.0
        if roof.size >= 3:
            return float(np.percentile(roof, 98)) - gnd, float(np.percentile(roof, 20)) - gnd
        surf = dsm[r0:r1, c0:c1][m]
        surf = surf[np.isfinite(surf)]
        if surf.size >= 3 and np.percentile(surf, 90) - gnd > 2.0:
            return float(np.percentile(surf, 95)) - gnd, float(np.percentile(surf, 30)) - gnd
        return float("nan"), float("nan")

    for el in osm["elements"]:
        t = el.get("tags", {})
        if "building" not in t or el["type"] != "way":
            continue
        ring = _ring_from(el.get("geometry"))
        if ring is None:
            continue
        poly = Polygon(ring)
        if not poly.is_valid or poly.area < 4:
            continue
        c = poly.centroid
        if not (e0 < c.x < e1 and n0 < c.y < n1):
            continue
        h, h_low = heights(poly)
        flags = 0
        if math.isfinite(h) and h > 1.5:
            flags |= 1
        else:
            lv = _num(t.get("building:levels"))
            h = _num(t.get("height")) or (3.5 * lv if lv else 4.0)
            h_low = h
            flags |= 2
        if h - h_low > 1.8:
            flags |= 8                                 # pitched / stepped roof
        nm = t.get("name")
        ni = -1
        if nm:
            ni = len(names)
            names.append(nm)
        recs.append((_ccw(ring), h, _kind(t), flags, int(_num(t.get("building:levels")) or 0), ni))
        polys.append(poly)
    n_osm = len(recs)

    # Lidar-only buildings: class-6 blobs OSM does not cover (sheds, newer
    # buildings, utility huts), traced at 0.5 m and simplified to ~0.5 m.
    covered = features.geometry_mask(polys, (g.rows, g.cols), tf, invert=True, all_touched=True) if polys \
        else np.zeros((g.rows, g.cols), bool)
    m = np.isfinite(bld) & (np.nan_to_num(bld, nan=-1e9) - dtm > 2.0)
    m = ndimage.binary_opening(m, iterations=1)
    m &= ~ndimage.binary_dilation(covered, iterations=2)
    for geom, _ in features.shapes(m.astype(np.uint8), mask=m, transform=tf):
        poly = shape(geom).simplify(0.5)
        if poly.geom_type != "Polygon" or poly.area < 12:
            continue
        ring = list(poly.exterior.coords)[:-1]
        if len(ring) < 3 or len(ring) > 400:
            continue
        h, h_low = heights(poly)
        if not math.isfinite(h) or h < 2.0:
            continue
        recs.append((_ccw([(float(x), float(y)) for x, y in ring]), h, 10,
                     1 | 4 | (8 if h - h_low > 1.8 else 0), 0, -1))

    Q = 0.2
    body = bytearray()
    verts = bytearray()
    nv = 0
    for ring, h, kind, flags, levels, ni in recs:
        body += struct.pack("<IHHHBBBBH", nv, len(ring), int(min(h, 6000) * 10), 0, kind, flags,
                            min(levels, 255), 0, 65535 if ni < 0 else ni)
        for x, y in ring:
            verts += struct.pack("<hh", int(round(x / Q)), int(round(y / Q)))
        nv += len(ring)
    head = b"IST1" + struct.pack("<IIf", len(recs), nv, Q)
    (out / "buildings.dat").write_bytes(head + bytes(body) + bytes(verts))
    (out / "buildings.json").write_text(json.dumps({
        "count": len(recs), "from_osm": n_osm, "lidar_only": len(recs) - n_osm, "vertices": nv, "q_m": Q,
        "kinds": KINDS, "colours": [], "names": names,
        "flags": {"1": "height from lidar", "2": "height from OSM tags/levels (no lidar roof)",
                  "4": "footprint traced from lidar (not in OSM)", "8": "pitched or stepped roof"},
        "format": "IST1 (env_baylands._load_buildings): h = roof (98th pct of lidar building "
                  "returns) above the footprint's ground (10th pct)",
    }, ensure_ascii=False))
    print(f"buildings: {n_osm} OSM + {len(recs) - n_osm} lidar-only, {nv} vertices")


# --------------------------------------------------------------- features

def _water_polys(osm: dict):
    from shapely.geometry import Polygon
    polys = []
    for el in osm["elements"]:
        t = el.get("tags", {})
        is_w = t.get("natural") == "water" or "water" in t or t.get("waterway") == "riverbank"
        if not is_w:
            continue
        rings = [el.get("geometry")] if el["type"] == "way" else \
            [m.get("geometry") for m in el.get("members", []) if m.get("role") == "outer"]
        for geom in rings:
            ring = _ring_from(geom)
            if ring:
                p = Polygon(ring)
                if p.is_valid and p.area > 50:
                    polys.append(p)
    return polys


def step_water(g: Grids, osm: dict, out: Path, force: bool) -> None:
    """Water: lidar water returns, OSM water areas, and the large holes in the
    point cloud (still water absorbs the laser, so the salt ponds and sloughs
    came back with no points at all). Each water body gets a FLAT surface at
    its shoreline's level (10th percentile of the ground in a 2-4 m ring
    around it), written into rasters/dtm_w.f32, which the terrain step uses."""
    if (out / "water.json").exists() and (g.d / "dtm_w.f32").exists() and not force:
        return
    from PIL import Image
    from rasterio import features
    from rasterio.transform import Affine
    from scipy import ndimage
    e0, n0, e1, n1 = REGION
    count = np.fromfile(g.d / "count.u16", np.uint16).reshape(g.rows, g.cols)
    w = g.u8("water").astype(bool)
    polys = _water_polys(osm)
    tf = Affine(CELL, 0, e0, 0, -CELL, n1)
    if polys:
        w |= features.geometry_mask(polys, (g.rows, g.cols), tf, invert=True)
    # Holes of >= 400 m2 (1600 cells) with no returns at all are water.
    holes = ndimage.binary_closing(count == 0, iterations=2)
    lab, nl = ndimage.label(holes)
    sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, nl + 1))
    big = np.zeros(nl + 1, bool)
    big[1:] = sizes >= 1600
    w |= big[lab]
    w = ndimage.binary_opening(w, iterations=1)
    dtm = g.f32("dtm").copy()
    lab, nl = ndimage.label(w)
    ring = ndimage.binary_dilation(w, iterations=8) & ~ndimage.binary_dilation(w, iterations=4)
    rlab = ndimage.grey_dilation(lab, size=(17, 17)) * ring
    levels = {}
    for i, sl in enumerate(ndimage.find_objects(rlab), start=1):
        if sl is None:
            continue
        v = dtm[sl][rlab[sl] == i]
        if v.size:
            levels[i] = float(np.percentile(v, 10))
    lev = np.zeros(nl + 1, np.float32)
    for i, v in levels.items():
        lev[i] = v
    # A body with no ring (touching the region edge only) keeps its mean.
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        if i not in levels and sl is not None:
            lev[i] = float(np.median(dtm[sl][lab[sl] == i]))
    dtm[w] = lev[lab[w]]
    dtm.tofile(g.d / "dtm_w.f32")
    w.astype(np.uint8).tofile(g.d / "water_full.u8")
    k = int(2.0 / CELL)
    c = w[: w.shape[0] // k * k, : w.shape[1] // k * k].reshape(w.shape[0] // k, k, w.shape[1] // k, k).mean(axis=(1, 3))
    Image.fromarray((c * 255).astype(np.uint8)).save(out / "water.png", optimize=True)
    (out / "water.json").write_text(json.dumps({"bounds_enu": [e0, n0, e1, n1], "res_m": 2.0,
        "encoding": "8-bit share of water, row 0 = north", "outside_is_water": False,
        "bodies": nl, "levels_m": sorted({round(float(v), 2) for v in lev[1:]})[:40],
        "source": "lidar water returns (class 9), lidar no-return holes >= 400 m2, OSM water areas; "
                  "each body flat at its shore's 10th-percentile ground (NAVD88 m)"}, indent=1))
    print(f"water: {c.mean() * 100:.1f}% of the region, {nl} bodies ({len(polys)} OSM water areas)")


def step_features(osm: dict, out: Path, force: bool) -> None:
    if (out / "features.json").exists() and not force:
        return
    e0, n0, e1, n1 = REGION
    keep = {"paths": [], "roads": [], "fences": [], "power_lines": [], "power_points": [],
            "areas": [], "points": [], "rail": [], "waterways": []}

    def line(geom):
        e, n = enu([p["lat"] for p in geom], [p["lon"] for p in geom])
        return [[round(float(a), 2), round(float(b), 2)] for a, b in zip(e, n)]

    for el in osm["elements"]:
        t = el.get("tags", {})
        if el["type"] == "node":
            e, n = enu(el["lat"], el["lon"])
            if not (e0 - 50 < e < e1 + 50 and n0 - 50 < n < n1 + 50):
                continue
            rec = {"p": [round(float(e), 2), round(float(n), 2)], "tags": t}
            (keep["power_points"] if "power" in t else keep["points"]).append(rec)
            continue
        if "building" in t:
            continue
        geoms = [el.get("geometry")] if el["type"] == "way" else \
            [m.get("geometry") for m in el.get("members", []) if m.get("role") in ("outer", "")]
        for geom in geoms:
            if not geom or len(geom) < 2:
                continue
            rec = {"id": el["id"], "pts": line(geom), "tags": t}
            hw = t.get("highway")
            if hw in ("footway", "path", "cycleway", "track", "pedestrian", "steps", "bridleway"):
                keep["paths"].append(rec)
            elif hw:
                keep["roads"].append(rec)
            elif "barrier" in t:
                keep["fences"].append(rec)
            elif t.get("power") in ("line", "minor_line", "cable"):
                keep["power_lines"].append(rec)
            elif "railway" in t:
                keep["rail"].append(rec)
            elif "waterway" in t and t["waterway"] != "riverbank":
                keep["waterways"].append(rec)
            elif any(k in t for k in ("landuse", "natural", "leisure", "amenity", "man_made", "water",
                                      "power", "tourism", "aeroway")):
                keep["areas"].append(rec)
    (out / "features.json").write_text(json.dumps(keep, ensure_ascii=False, separators=(",", ":")))
    print("features: " + ", ".join(f"{k} {len(v)}" for k, v in keep.items()))


# ------------------------------------------------------------------ ortho

def naip_urls(year: str = "2024") -> list[str]:
    """Signed COG URLs of the NAIP quarter-quads covering the region, from the
    Microsoft Planetary Computer STAC (anonymous SAS token, no account)."""
    s, w, n, e = bbox_ll(pad=30.0)
    body = json.dumps({"collections": ["naip"], "bbox": [w, s, e, n], "datetime": f"{year}-01-01/{year}-12-31",
                       "limit": 50}).encode()
    req = urllib.request.Request("https://planetarycomputer.microsoft.com/api/stac/v1/search", data=body,
                                 headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        items = json.loads(r.read())["features"]
    tok = json.loads(_get("https://planetarycomputer.microsoft.com/api/sas/v1/token/naip"))["token"]
    urls = [it["assets"]["image"]["href"] + "?" + tok for it in items]
    print(f"naip {year}: {len(urls)} quarter-quads: " + ", ".join(it["id"] for it in items))
    return urls


def step_ortho(out: Path, force: bool, src: str | None) -> None:
    """NAIP for the region, warped into the scene's grid. `src`: a text file of
    GeoTIFF/COG URLs (one per line); default: the 2024 NAIP from the Planetary
    Computer."""
    if (out / "ortho.json").exists() and not force:
        return
    urls = [u.strip() for u in Path(src).read_text().splitlines() if u.strip()] if src else naip_urls()
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, reproject
    from PIL import Image
    e0, n0, e1, n1 = REGION
    res = 0.6
    W, H = int((e1 - e0) / res), int((n1 - n0) / res)
    # The scene's frame is linear in lat/lon (fixed metres per degree), so a
    # regular lat/lon grid IS a regular ENU grid: warp to EPSG:4326 with the
    # pixel size of `res` metres east and north. Exact, no projection guess.
    crs = CRS.from_epsg(4326)
    lat_n, lon_w = geo(e0, n1)
    dst = np.zeros((4, H, W), np.uint8)
    tf = Affine(res / M_PER_DEG_LON, 0, float(lon_w), 0, -res / M_PER_DEG_LAT, float(lat_n))
    files = []
    for url in urls:
        with rasterio.open(url) as s:
            files.append(url.split("?")[0].split("/")[-1])
            for b in range(min(4, s.count)):
                tmp = np.zeros((H, W), np.uint8)
                reproject(rasterio.band(s, b + 1), tmp, dst_transform=tf, dst_crs=crs,
                          resampling=Resampling.lanczos, src_nodata=0, dst_nodata=0)
                dst[b] = np.where(tmp > 0, tmp, dst[b])
    hi = out / "_hi"
    hi.mkdir(exist_ok=True)
    rgb = np.moveaxis(dst[:3], 0, -1)
    Image.fromarray(rgb).save(hi / "ortho.jpg", quality=92)
    Image.fromarray(dst[3]).save(hi / "nir.jpg", quality=92)
    Image.fromarray(rgb).resize((int(e1 - e0), int(n1 - n0)), Image.LANCZOS).save(out / "ortho.jpg", quality=86)
    (out / "ortho.json").write_text(json.dumps({"bounds_enu": [e0, n0, e1, n1], "res_m": 1.0,
        "hi": {"file": "_hi/ortho.jpg", "res_m": res, "nir": "_hi/nir.jpg"}, "files": files,
        "source": "USDA NAIP (public domain)"}, indent=1))
    print(f"ortho: {W}x{H} @ {res} m; committed 1 m: {(out / 'ortho.jpg').stat().st_size / 1e6:.1f} MB")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--work", required=True, help="work folder (several GB): tiles, rasters, OSM")
    ap.add_argument("--out", required=True, help="assets/baylands")
    ap.add_argument("--steps", default="lidar,rasters,terrain,osm,water,buildings,features,ortho")
    ap.add_argument("--naip", help="text file of NAIP GeoTIFF/COG URLs covering the region")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    work, out = Path(a.work).expanduser(), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    steps = a.steps.split(",")
    if "lidar" in steps:
        step_lidar(work, a.force)
    if "rasters" in steps:
        step_rasters(work, a.force)
    g = Grids(work / "rasters") if (work / "rasters" / "grid.json").exists() else None
    osm = step_osm(work, a.force) if any(s in steps for s in ("osm", "water", "buildings", "features")) else None
    if g and "water" in steps:
        step_water(g, osm, out, a.force)
    if g and "terrain" in steps:
        step_terrain(g, out, a.force)
    if g and "buildings" in steps:
        step_buildings(g, osm, out, a.force)
    if "features" in steps:
        step_features(osm, out, a.force)
    if "ortho" in steps:
        step_ortho(out, a.force, a.naip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
