#!/usr/bin/env python3
"""Bake the Istanbul scene's GROUND: what istanbul_ground.gd and
istanbul_far.gd draw. Run after fetch_istanbul.py (it reads that tool's
baked roads.json, landcover.png and water.png, and its cached OSM extract).

There is no open high-resolution aerial photo of Istanbul (no NAIP
equivalent; the commercial imagery services may not be redistributed), so the
ground is DRAWN from data, the way Manhattan's streets are:

  * Streets, pavements, stone setts, tram beds, plazas, parks: signed-distance
    fields baked from OpenStreetMap (roads.json centrelines and widths, land
    use polygons), 1 m/px for the paved ways and 2 m/px for the rest, per
    1000 m tile. A signed-distance field rather than a mask because an edge
    reconstructs straight under bilinear filtering, so a 1 m texel still gives
    a kerb that is sharp at a few centimetres a pixel on screen.
  * Large-scale colour: Copernicus Sentinel-2 L2A (10 m, one clear June scene
    from the Element84 earth-search STAC), for the far ground beyond the
    street fields and to tint the drawn ground so the city is not uniform.
  * The shore: OSM coastline polylines (water on their right), each run
    marked quay (ferry landings, the Golden Horn, buildings on the water) or
    rocks (the riprap of the Marmara and Bosphorus promenades).
  * The far city: every OSM building in the terrain's reach outside the
    detailed scene, as an oriented box (Levent's towers, the Asian hills, the
    Marmara shore west).
  * CC0 ground textures from Poly Haven (asphalt, setts, pavers, slabs,
    grass, gravel, dry ground, sand, leaf litter), 512 px.

OUTPUTS (drone/sim/godot/assets/istanbul/):
  ground_s2.jpg, ground_s2.json   Sentinel-2 true colour, 10 m, wide bounds
                                   (committed; ~1.5 MB)
  ground_veg.jpg                   vegetation 0..255 from NDVI, same grid
  coast.json                       shore polylines and their kinds
  far.dat, far.json                far buildings (binary, see write_far)
  ground_tex/<name>.jpg, ground_tex.json   the CC0 textures and their sizes
  _ground/fa_<i>_<j>.png           field A per tile, 1000 x 1000, 1 m/px
                                   (gitignored, regenerate with this tool):
        R carriageway, G any paved way, B stone setts, A track bed
  _ground/fb_<i>_<j>.png           field B per tile, 500 x 500, 2 m/px:
        R green ground, G stone-slab ground (squares, courts), B vegetation,
        A shoreline (positive on land)
  _ground/tiles.json               which tiles exist
  Every SDF channel: 128 is the edge, +8 per metre inside (0.125 m a step).

    uv run --no-project --with numpy --with scipy --with pillow --with rasterio \\
        --with osmium python drone/sim/tools/fetch_istanbul_ground.py [--only s2,osm,tex,fields,far]
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import struct
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "godot" / "assets" / "istanbul"
CACHE = ASSETS / "_cache"
HI = ASSETS / "_ground"

DATUM_LAT = 41.0086
DATUM_LON = 28.9802
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))
BOUNDS = (-6000.0, -3200.0, 5000.0, 4800.0)
WIDE = (-11000.0, -9000.0, 11000.0, 11000.0)
UA = "eco-sim/1.0 (drone simulator; istanbul ground)"

TILE = 1000.0
A_RES = 1.0             # field A, m/px
B_RES = 2.0             # field B, m/px
SDF_SCALE = 8.0         # stored value per metre
MARGIN = 7.0            # how far past an edge the fields are exact (m)

#: Sentinel-2 L2A scene: 2025-06-05, MGRS 35TPF, cloud 0.07 %, covers the
#: whole wide box. Pinned so a rerun gives the same ground.
S2_ITEM = "S2B_35TPF_20250605_0_L2A"
STAC = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/"
S2_RES = 10.0

#: Poly Haven textures (CC0): name -> asset id.
TEXTURES = {
    "asphalt": "asphalt_02",
    "setts": "cobblestone_floor_08",
    "pavers": "patterned_concrete_pavers",
    "slabs": "concrete_pavers_02",
    "grass": "leafy_grass",
    "gravel": "gravel_floor",
    "dry": "dry_ground_01",
    "sand": "coast_sand_01",
    "leaves": "forest_leaves_02",
}
TEX_PX = 512

LANDCOVER = ["urban", "park", "wood", "cemetery", "plaza", "parking", "pitch",
             "beach", "courtyard", "railway", "construction", "garden", "playground"]
GREEN = {"park", "wood", "cemetery", "garden", "pitch"}
PAVED_AREAS = {"plaza", "parking"}
#: Stone-slab ground: squares, pedestrian areas, mosque and school courts.
SLAB_AREAS = {"plaza", "courtyard"}

VEHICLE = {"motorway", "motorway_link", "primary", "secondary", "street", "service"}
#: Pavement each side of a carriageway (m), when the street is wide enough.
PAVEMENT = {"motorway": 0.0, "motorway_link": 0.0, "primary": 3.0, "secondary": 2.5,
            "street": 1.4, "service": 0.0}
#: The old quarters whose lanes are granite setts (Arnavut kaldirimi) where
#: OSM has no surface tag: (lat, lon, radius m).
SETT_ZONES = [(41.0060, 28.9760, 700.0),   # Sultanahmet, Cankurtaran
              (41.0310, 28.9470, 750.0),   # Fener, Balat
              (41.0265, 28.9740, 480.0),   # Galata
              (41.0320, 28.9815, 380.0),   # Cihangir, Cukurcuma
              (41.0035, 28.9650, 420.0),   # Kumkapi
              (41.0190, 28.9570, 420.0),   # Zeyrek
              (41.0245, 29.0150, 380.0),   # Uskudar's old lanes
              (40.9880, 29.0270, 380.0)]   # Moda


def project(lon: float, lat: float) -> tuple[float, float]:
    return ((lon - DATUM_LON) * M_PER_DEG_LON, (lat - DATUM_LAT) * M_PER_DEG_LAT)


def unproject(e: float, n: float) -> tuple[float, float]:
    return (DATUM_LON + e / M_PER_DEG_LON, DATUM_LAT + n / M_PER_DEG_LAT)


def get(url: str, timeout: int = 120) -> bytes:
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as ex:                              # noqa: BLE001
            last = ex
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"{url}: {last}")


# ------------------------------------------------------------- Sentinel-2

def bake_s2() -> None:
    """True colour and vegetation over WIDE at 10 m, on the scene's own grid
    (which is a plain lat/lon grid: the ENU frame is equirectangular)."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.vrt import WarpedVRT

    item = json.loads(get(STAC + S2_ITEM))
    e0, n0, e1, n1 = WIDE
    w = int(round((e1 - e0) / S2_RES))
    h = int(round((n1 - n0) / S2_RES))
    lon_w, lat_n = unproject(e0, n1)
    tr = from_origin(lon_w, lat_n, S2_RES / M_PER_DEG_LON, S2_RES / M_PER_DEG_LAT)

    def warp(asset: str, bands) -> np.ndarray:
        href = item["assets"][asset]["href"]
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="5"):
            with rasterio.open(href) as src:
                with WarpedVRT(src, crs="EPSG:4326", transform=tr, width=w, height=h,
                               resampling=Resampling.bilinear) as vrt:
                    return vrt.read(bands).astype(np.float32)

    print(f"  sentinel-2 {S2_ITEM}: true colour", file=sys.stderr)
    tci = warp("visual", [1, 2, 3])
    print("  sentinel-2: red, nir", file=sys.stderr)
    red = warp("red", 1)
    nir = warp("nir", 1)
    # L2A processing baseline >= 04.00: reflectance = DN / 10000 - 0.1.
    r = np.clip(red / 10000.0 - 0.1, 0.0, 1.0)
    n = np.clip(nir / 10000.0 - 0.1, 0.0, 1.0)
    ndvi = (n - r) / np.maximum(n + r, 1e-3)
    veg = np.clip((ndvi - 0.18) / 0.42, 0.0, 1.0)
    # No-data (0) outside the swath: the mean colour, no vegetation.
    nodata = tci.sum(axis=0) < 1.0
    if nodata.any():
        mean = tci[:, ~nodata].mean(axis=1)
        for c in range(3):
            tci[c][nodata] = mean[c]
        veg[nodata] = 0.0
    rgb = np.clip(tci, 0, 255).astype(np.uint8).transpose(1, 2, 0)
    Image.fromarray(rgb).save(ASSETS / "ground_s2.jpg", quality=86, optimize=True)
    Image.fromarray((veg * 255).astype(np.uint8)).save(HI / "veg.jpg", quality=85, optimize=True)
    meta = {"bounds_enu": list(WIDE), "res_m": S2_RES, "size_px": [w, h], "note": "row 0 = north edge",
            "colour": "Sentinel-2 TCI (B04, B03, B02): 0..255 = reflectance 0..0.2, linear",
            "veg": "clamp((NDVI - 0.18) / 0.42) from B08, B04",
            "item": S2_ITEM, "datetime": item["properties"]["datetime"],
            "source": "Contains modified Copernicus Sentinel data 2025 (Element84 earth-search, "
                      "sentinel-2-l2a)"}
    (ASSETS / "ground_s2.json").write_text(json.dumps(meta, indent=1))
    print(f"  wrote ground_s2.jpg {w}x{h}", file=sys.stderr)


# ------------------------------------------------------------- textures

def bake_textures() -> None:
    out = ASSETS / "ground_tex"
    out.mkdir(exist_ok=True)
    meta = {"source": "Poly Haven (polyhaven.com), CC0 1.0", "size_px": TEX_PX, "textures": {}}
    for name, aid in TEXTURES.items():
        info = json.loads(get(f"https://api.polyhaven.com/info/{aid}"))
        dims = info.get("dimensions", [2000, 2000])
        files = json.loads(get(f"https://api.polyhaven.com/files/{aid}"))
        url = files["Diffuse"]["1k"]["jpg"]["url"]
        blob = get(url)
        tmp = HI / f"_{aid}.jpg"
        HI.mkdir(exist_ok=True)
        tmp.write_bytes(blob)
        img = Image.open(tmp).convert("RGB").resize((TEX_PX, TEX_PX), Image.LANCZOS)
        img.save(out / f"{name}.jpg", quality=88, optimize=True)
        meta["textures"][name] = {"asset": aid, "size_m": round(float(dims[0]) / 1000.0, 3),
                                  "url": f"https://polyhaven.com/a/{aid}",
                                  "authors": list(info.get("authors", {}).keys())}
        print(f"  texture {name}: {aid} {dims[0] / 1000.0:.1f} m", file=sys.stderr)
    (ASSETS / "ground_tex.json").write_text(json.dumps(meta, indent=1))


# ------------------------------------------------------------------- OSM

def in_rect(r, e, n) -> bool:
    return r[0] <= e <= r[2] and r[1] <= n <= r[3]


def extract_osm() -> dict:
    """From the cached BBBike extract: land-use polygons in the detailed
    scene, the coastline in the wide box, and buildings in the wide box
    outside the detailed scene. Cached as _ground/osm_ground.json."""
    f = HI / "osm_ground.json"
    if f.exists():
        return json.loads(f.read_text())
    import osmium
    sys.path.insert(0, str(HERE))
    import fetch_istanbul as fi                              # noqa: PLC0415
    core_g = (BOUNDS[0] - 50, BOUNDS[1] - 50, BOUNDS[2] + 50, BOUNDS[3] + 50)

    def ring_pts(ring) -> list[list[float]]:
        out = []
        for nd in ring:
            e, n = project(nd.lon, nd.lat)
            out.append([round(e, 2), round(n, 2)])
        return out

    class H(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.areas, self.coast, self.far = [], [], []

        def way(self, w):
            if w.tags.get("natural") != "coastline":
                return
            try:
                pts = ring_pts(w.nodes)
            except osmium.InvalidLocationError:
                return
            if any(in_rect(WIDE, e, n) for e, n in pts):
                self.coast.append(pts)

        def area(self, a):
            t = {tg.k: tg.v for tg in a.tags}
            is_b = "building" in t and t.get("building") != "no"
            cls = None if is_b else fi.lc_class(t)
            if not is_b and cls is None:
                return
            try:
                outers = [ring_pts(r) for r in a.outer_rings()]
            except osmium.InvalidLocationError:
                return
            if not outers or len(outers[0]) < 4:
                return
            ring = outers[0]
            ce = sum(p[0] for p in ring) / len(ring)
            cn = sum(p[1] for p in ring) / len(ring)
            if is_b:
                if in_rect(WIDE, ce, cn) and not in_rect(BOUNDS, ce, cn):
                    self.far.append({"ring": ring, "h": fi.num(t.get("height")),
                                     "lv": fi.num(t.get("building:levels")),
                                     "kind": fi.kind_of(t)})
                return
            rings = [r for r in outers if any(in_rect(core_g, e, n) for e, n in r[:: max(1, len(r) // 16)])]
            if rings:
                try:
                    inners = [ring_pts(r) for o in a.outer_rings() for r in a.inner_rings(o)]
                except osmium.InvalidLocationError:
                    inners = []
                self.areas.append({"cls": cls, "outer": rings, "inner": inners})

    h = H()
    t0 = time.time()
    h.apply_file(str(CACHE / "Istanbul.osm.pbf"), locations=True, idx="flex_mem")
    print(f"  osm: {len(h.areas)} areas, {len(h.coast)} coastline ways, {len(h.far)} far buildings "
          f"({time.time() - t0:.0f} s)", file=sys.stderr)
    doc = {"areas": h.areas, "coast": h.coast, "far": h.far}
    HI.mkdir(exist_ok=True)
    f.write_text(json.dumps(doc))
    return doc



# ------------------------------------------------------------------ coast

#: The Bosphorus and the Golden Horn are walled in dressed stone (rihtim)
#: nearly all along; the Marmara shores (Kennedy Caddesi, Yenikapi's
#: reclaimed land, Salacak's south, Kadikoy - Moda) are rock armour. Rocks
#: inside these e, n boxes, except at a ferry landing or in a harbour.
MARMARA_BOXES = [(-12000.0, -12000.0, 1500.0, 350.0),
                 (1500.0, -12000.0, 12000.0, -1400.0)]
#: Harbours inside them, walled whatever the box says: Haydarpasa port and
#: Kadikoy's harbour.
QUAY_BOXES = [(2700.0, -2300.0, 4200.0, -700.0)]
PIER_REACH = 220.0


def simplify(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker."""
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        p, q = pts[a], pts[b]
        d = q - p
        L = math.hypot(*d) + 1e-9
        seg = pts[a + 1:b]
        dist = np.abs((seg[:, 0] - p[0]) * d[1] - (seg[:, 1] - p[1]) * d[0]) / L
        k = int(np.argmax(dist))
        if dist[k] > tol:
            m = a + 1 + k
            keep[m] = True
            stack += [(a, m), (m, b)]
    return pts[keep]


def coast_lines(osm: dict, box) -> list[np.ndarray]:
    """Coastline runs inside box (water on the right, as OSM draws it)."""
    out = []
    for pts in osm["coast"]:
        run = []
        for e, n in pts:
            if in_rect(box, e, n):
                run.append((e, n))
            elif run:
                if len(run) >= 2:
                    out.append(np.array(run))
                run = []
        if len(run) >= 2:
            out.append(np.array(run))
    return out


def bake_coast(osm: dict) -> None:
    roads = json.loads((ASSETS / "roads.json").read_text())
    piers = np.array([p for pr in roads["piers"] for p in pr["pts"]] or [[1e9, 1e9]])
    box = (BOUNDS[0] - 1500, BOUNDS[1] - 1500, BOUNDS[2] + 1500, BOUNDS[3] + 1500)
    lines = []
    total = {"quay": 0.0, "rocks": 0.0}
    for run in coast_lines(osm, box):
        run = simplify(run, 0.4)
        # Kind per segment, then split the run where it changes.
        kinds = []
        for a, b in zip(run[:-1], run[1:]):
            m = (a + b) * 0.5
            near_pier = np.min(np.hypot(piers[:, 0] - m[0], piers[:, 1] - m[1])) < PIER_REACH
            harbour = any(in_rect(q, m[0], m[1]) for q in QUAY_BOXES)
            marmara = any(in_rect(q, m[0], m[1]) for q in MARMARA_BOXES)
            kinds.append("rocks" if marmara and not (near_pier or harbour) else "quay")
        start = 0
        for k in range(1, len(kinds) + 1):
            if k == len(kinds) or kinds[k] != kinds[start]:
                pts = run[start:k + 1]
                L = float(np.sum(np.hypot(*np.diff(pts, axis=0).T)))
                total[kinds[start]] += L
                lines.append({"kind": kinds[start], "pts": [[round(float(e), 1), round(float(n), 1)] for e, n in pts]})
                start = k
    doc = {"frame": "ENU metres, datum Hagia Sophia", "note": "water on the right of each run",
           "source": "OpenStreetMap natural=coastline, (c) OpenStreetMap contributors, ODbL 1.0",
           "lines": lines}
    (ASSETS / "coast.json").write_text(json.dumps(doc, separators=(",", ":")))
    print(f"  coast: {len(lines)} runs, {total['quay'] / 1000:.1f} km quay, {total['rocks'] / 1000:.1f} km rocks",
          file=sys.stderr)


# ----------------------------------------------------------------- fields

def in_sett_zone(e: float, n: float) -> bool:
    for lat, lon, r in SETT_ZONES:
        ze, zn = project(lon, lat)
        if (e - ze) ** 2 + (n - zn) ** 2 < r * r:
            return True
    return False


def road_segments() -> dict[str, np.ndarray]:
    """Segments [ax, ay, bx, by, half-width] for each street channel."""
    doc = json.loads((ASSETS / "roads.json").read_text())
    nodes = np.array(doc["nodes"], np.float64)
    out = {"R": [], "G": [], "B": [], "A": []}
    for a, b, cls, oneway, lanes, width, _u, _nm, flags in doc["edges"]:
        if flags & 15:
            continue            # bridges, tunnels, other levels: not on the ground
        pa, pb = nodes[a], nodes[b]
        m = (pa + pb) * 0.5
        stone_zone = in_sett_zone(m[0], m[1])
        W = float(width)
        if cls in VEHICLE:
            stone = bool(flags & 16) or (stone_zone and cls in ("street", "service") and W <= 8.0)
            hw = max(W, 3.0) * 0.5
            pw = PAVEMENT[cls]
            if cls == "street" and (W < 6.0 or stone):
                pw = 0.0
            seg = [pa[0], pa[1], pb[0], pb[1]]
            out["R"].append(seg + [hw])
            out["G"].append(seg + [hw + pw])
            if stone:
                out["B"].append(seg + [hw])
        else:
            stone = bool(flags & 48) or (stone_zone and cls in ("pedestrian", "steps"))
            hw = max(W * 0.5, 0.9 if cls != "pedestrian" else 2.0)
            seg = [pa[0], pa[1], pb[0], pb[1]]
            out["G"].append(seg + [hw])
            if stone:
                out["B"].append(seg + [hw])
    for r in doc["rails"]:
        if r["tunnel"] or r["bridge"] or r["type"] not in ("tram", "rail", "narrow_gauge"):
            continue
        hw = 1.35 if r["type"] == "tram" else 2.1
        for p, q in zip(r["pts"], r["pts"][1:]):
            out["A"].append([p[0], p[1], q[0], q[1], hw])
    return {k: np.array(v, np.float64).reshape(-1, 5) for k, v in out.items()}


def area_polys(osm: dict) -> dict[str, list]:
    out = {"green": [], "paved": [], "slab": []}
    for ar in osm["areas"]:
        c = ar["cls"]
        k = "green" if c in GREEN else ("paved" if c in PAVED_AREAS else None)
        if k:
            out[k].append((ar["outer"], ar["inner"]))
        if c in SLAB_AREAS:
            out["slab"].append((ar["outer"], ar["inner"]))
    return out


_G: dict = {}           # worker globals (fork)


def _pick(segs: np.ndarray, r, m: float) -> np.ndarray:
    if len(segs) == 0:
        return segs
    lo_x = np.minimum(segs[:, 0], segs[:, 2]) - segs[:, 4] - m
    hi_x = np.maximum(segs[:, 0], segs[:, 2]) + segs[:, 4] + m
    lo_y = np.minimum(segs[:, 1], segs[:, 3]) - segs[:, 4] - m
    hi_y = np.maximum(segs[:, 1], segs[:, 3]) + segs[:, 4] + m
    sel = (hi_x >= r[0]) & (lo_x <= r[2]) & (hi_y >= r[1]) & (lo_y <= r[3])
    return segs[sel]


def stamp(field: np.ndarray, xs: np.ndarray, ys: np.ndarray, segs: np.ndarray) -> None:
    """field = max(field, half-width - distance to segment): the union of
    the segments' round-capped buffers as a signed distance, exact for each."""
    res = xs[1] - xs[0]
    H, W = field.shape
    for ax, ay, bx, by, hw in segs:
        m = hw + MARGIN
        i0 = max(0, int((min(ax, bx) - m - xs[0]) / res))
        i1 = min(W, int((max(ax, bx) + m - xs[0]) / res) + 2)
        j0 = max(0, int((ys[0] - (max(ay, by) + m)) / res))
        j1 = min(H, int((ys[0] - (min(ay, by) - m)) / res) + 2)
        if i0 >= i1 or j0 >= j1:
            continue
        px = xs[i0:i1][None, :]
        py = ys[j0:j1][:, None]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy + 1e-9
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / L2, 0.0, 1.0)
        d = np.hypot(px - ax - t * dx, py - ay - t * dy)
        sub = field[j0:j1, i0:i1]
        np.maximum(sub, hw - d, out=sub)


def poly_sdf(polys: list, x0: float, y0: float, size: float, out_res: float) -> np.ndarray | None:
    """Signed distance (m, + inside) of the union of polygons over the tile
    [x0, x0 + size] x [y0, y0 + size], at out_res, via a 0.5 m raster."""
    from scipy import ndimage
    fine = 0.5
    pad = 16.0
    n = int(round((size + 2 * pad) / fine))
    ox, oy = x0 - pad, y0 + size + pad          # NW corner
    img = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(img)
    any_ = False
    box = (x0 - pad, y0 - pad, x0 + size + pad, y0 + size + pad)
    for outers, inners in polys:
        for ring, fill in [(r, 1) for r in outers] + [(r, 0) for r in inners]:
            a = np.asarray(ring)
            if a[:, 0].max() < box[0] or a[:, 0].min() > box[2] or a[:, 1].max() < box[1] or a[:, 1].min() > box[3]:
                continue
            pts = [((e - ox) / fine, (oy - nn) / fine) for e, nn in ring]
            if len(pts) >= 3:
                d.polygon(pts, fill=fill)
                any_ = any_ or fill == 1
    if not any_:
        return None
    m = np.asarray(img, bool)
    din = ndimage.distance_transform_edt(m)
    dout = ndimage.distance_transform_edt(~m)
    sdf = np.where(m, din - 0.5, -(dout - 0.5)) * fine
    k = int(round(out_res / fine))
    p = int(round(pad / fine))
    sdf = sdf[p:p + int(round(size / fine)), p:p + int(round(size / fine))]
    h = sdf.shape[0] // k
    return sdf[:h * k, :h * k].reshape(h, k, h, k).mean(axis=(1, 3))


def enc(sdf: np.ndarray) -> np.ndarray:
    return np.clip(np.round(128.0 + sdf * SDF_SCALE), 0, 255).astype(np.uint8)


def coast_sdf(xs: np.ndarray, ys: np.ndarray, segs: np.ndarray, water_sign: np.ndarray) -> np.ndarray:
    """Distance to the coastline, + on land (left of the runs), with the
    water mask's sign beyond 16 m of any line."""
    res = xs[1] - xs[0]
    H, W = len(ys), len(xs)
    M = 16.0
    dist = np.full((H, W), M, np.float64)
    side = water_sign.astype(np.float64).copy()
    for ax, ay, bx, by in segs:
        i0 = max(0, int((min(ax, bx) - M - xs[0]) / res))
        i1 = min(W, int((max(ax, bx) + M - xs[0]) / res) + 2)
        j0 = max(0, int((ys[0] - (max(ay, by) + M)) / res))
        j1 = min(H, int((ys[0] - (min(ay, by) - M)) / res) + 2)
        if i0 >= i1 or j0 >= j1:
            continue
        px = xs[i0:i1][None, :]
        py = ys[j0:j1][:, None]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy + 1e-9
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / L2, 0.0, 1.0)
        d = np.hypot(px - ax - t * dx, py - ay - t * dy)
        s = np.where(dx * (py - ay) - dy * (px - ax) > 0.0, 1.0, -1.0)
        sub = dist[j0:j1, i0:i1]
        sd = side[j0:j1, i0:i1]
        closer = d < sub
        sub[closer] = d[closer]
        sd[closer] = s[closer]
    return dist * side


def _tile(job) -> str:
    i, j = job
    g = _G
    x0 = BOUNDS[0] + i * TILE
    y0 = BOUNDS[1] + j * TILE
    r = (x0, y0, x0 + TILE, y0 + TILE)
    # Field A, 1 m.
    na = int(TILE / A_RES)
    xs = x0 + (np.arange(na) + 0.5) * A_RES
    ys = y0 + TILE - (np.arange(na) + 0.5) * A_RES
    chans = []
    for k in "RGBA":
        f = np.full((na, na), -MARGIN, np.float64)
        stamp(f, xs, ys, _pick(g["segs"][k], r, MARGIN))
        if k == "G":
            ps = poly_sdf(g["polys"]["paved"], x0, y0, TILE, A_RES)
            if ps is not None:
                np.maximum(f, ps, out=f)
        chans.append(enc(f))
    Image.fromarray(np.dstack(chans), "RGBA").save(HI / f"fa_{i}_{j}.png", optimize=False, compress_level=6)
    # Field B, 2 m.
    nb = int(TILE / B_RES)
    xs = x0 + (np.arange(nb) + 0.5) * B_RES
    ys = y0 + TILE - (np.arange(nb) + 0.5) * B_RES
    green = poly_sdf(g["polys"]["green"], x0, y0, TILE, B_RES)
    if green is None:
        green = np.full((nb, nb), -MARGIN)
    slab = poly_sdf(g["polys"]["slab"], x0, y0, TILE, B_RES)
    if slab is None:
        slab = np.full((nb, nb), -MARGIN)
    from scipy import ndimage
    vx = (xs - WIDE[0]) / S2_RES - 0.5
    vy = (WIDE[3] - ys) / S2_RES - 0.5
    VY, VX = np.meshgrid(vy, vx, indexing="ij")
    veg = ndimage.map_coordinates(g["veg"], [VY, VX], order=1, mode="nearest")
    wx = ((xs - WIDE[0]) / 4.0).astype(int).clip(0, g["water"].shape[1] - 1)
    wy = ((WIDE[3] - ys) / 4.0).astype(int).clip(0, g["water"].shape[0] - 1)
    wsign = np.where(g["water"][wy[:, None], wx[None, :]] > 127, -1.0, 1.0)
    cs = g["coast"]
    if len(cs):
        sel = ((np.maximum(cs[:, 0], cs[:, 2]) > x0 - 20) & (np.minimum(cs[:, 0], cs[:, 2]) < x0 + TILE + 20)
               & (np.maximum(cs[:, 1], cs[:, 3]) > y0 - 20) & (np.minimum(cs[:, 1], cs[:, 3]) < y0 + TILE + 20))
        cs = cs[sel]
    coast = coast_sdf(xs, ys, cs, wsign)
    fb = np.dstack([enc(green), enc(slab), np.clip(veg, 0, 255).astype(np.uint8), enc(coast)])
    Image.fromarray(fb, "RGBA").save(HI / f"fb_{i}_{j}.png", compress_level=6)
    return f"{i},{j}"


def bake_fields(osm: dict, jobs: int) -> None:
    t0 = time.time()
    water = np.asarray(Image.open(ASSETS / "water.png").convert("L"))
    veg = np.asarray(Image.open(HI / "veg.jpg").convert("L")).astype(np.float32)
    coast = []
    for run in coast_lines(osm, (BOUNDS[0] - 100, BOUNDS[1] - 100, BOUNDS[2] + 100, BOUNDS[3] + 100)):
        for a, b in zip(run[:-1], run[1:]):
            coast.append([a[0], a[1], b[0], b[1]])
    _G.update({"segs": road_segments(), "polys": area_polys(osm), "veg": veg,
               "water": water, "coast": np.array(coast, np.float64).reshape(-1, 4)})
    print("  fields: " + ", ".join(f"{k} {len(v)} segments" for k, v in _G["segs"].items()), file=sys.stderr)
    tiles = []
    nx = int(round((BOUNDS[2] - BOUNDS[0]) / TILE))
    ny = int(round((BOUNDS[3] - BOUNDS[1]) / TILE))
    for j in range(ny):
        for i in range(nx):
            x0 = BOUNDS[0] + i * TILE
            y0 = BOUNDS[1] + j * TILE
            c0 = int((x0 - WIDE[0]) / 4.0)
            r0 = int((WIDE[3] - (y0 + TILE)) / 4.0)
            land = (water[r0:r0 + 250, c0:c0 + 250] < 128).mean()
            if land > 0.0005:
                tiles.append((i, j))
    ctx = mp.get_context("fork")
    with ctx.Pool(jobs) as pool:
        for k, done in enumerate(pool.imap_unordered(_tile, tiles)):
            if k % 10 == 0:
                print(f"  fields: {k + 1}/{len(tiles)} ({time.time() - t0:.0f} s)", file=sys.stderr)
    doc = {"tile_m": TILE, "origin": [BOUNDS[0], BOUNDS[1]], "grid": [nx, ny],
           "a_res": A_RES, "b_res": B_RES, "sdf_scale": SDF_SCALE,
           "tiles": [[i, j] for i, j in tiles],
           "a": "R carriageway, G paved way, B stone setts, A track bed (SDF, 128 = edge)",
           "b": "R green (SDF), G slab ground (SDF), B vegetation 0..255, A shoreline (SDF, + land)"}
    (HI / "tiles.json").write_text(json.dumps(doc))
    print(f"  fields: {len(tiles)} tiles in {time.time() - t0:.0f} s", file=sys.stderr)


# -------------------------------------------------------------------- far

LEAN_AREA = 220
LEAN_H = 18
FAR_KINDS = ["residential", "commercial", "mosque", "church", "historic", "industrial",
             "public", "wooden", "tower", "small", "ruin", "bazaar", "palace", "station"]


def min_rect(pts: np.ndarray) -> tuple[float, float, float, float, float]:
    """Minimum-area bounding rectangle: centre e, n, width (along yaw), depth, yaw deg."""
    best = None
    c = pts.mean(axis=0)
    q = pts - c
    for a, b in zip(pts, np.roll(pts, -1, axis=0)):
        d = b - a
        L = math.hypot(*d)
        if L < 0.5:
            continue
        u = d / L
        v = np.array([-u[1], u[0]])
        pu = q @ u
        pv = q @ v
        area = (pu.max() - pu.min()) * (pv.max() - pv.min())
        if best is None or area < best[0]:
            mid = c + u * (pu.max() + pu.min()) * 0.5 + v * (pv.max() + pv.min()) * 0.5
            best = (area, mid[0], mid[1], pu.max() - pu.min(), pv.max() - pv.min(),
                    math.degrees(math.atan2(u[1], u[0])))
    if best is None:
        return (c[0], c[1], 1.0, 1.0, 0.0)
    return best[1:]


def bake_far(osm: dict) -> None:
    import rasterio
    f = next(CACHE.glob("GHS_BUILT_H_ANBH_*R5_C21.tif"))
    lon0, lat0 = unproject(WIDE[0], WIDE[1])
    lon1, lat1 = unproject(WIDE[2], WIDE[3])
    with rasterio.open(f) as src:
        win = rasterio.windows.from_bounds(lon0 - 0.01, lat0 - 0.01, lon1 + 0.01, lat1 + 0.01, src.transform)
        ghsl = src.read(1, window=win).astype(np.float32)
        gtr = src.window_transform(win)
    ghsl[ghsl < 0] = 0.0
    rows = []
    for b in osm["far"]:
        pts = np.array(b["ring"][:-1] if b["ring"][0] == b["ring"][-1] else b["ring"], np.float64)
        if len(pts) < 3:
            continue
        x, y = pts[:, 0], pts[:, 1]
        area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
        if area < 30.0:
            continue
        ce, cn, w, d, yaw = min_rect(pts)
        h = b["h"]
        if h is None or h <= 0 or h > 400:
            lv = b["lv"]
            if lv is not None and 0 < lv < 90:
                h = lv * 3.1 + 1.5
            else:
                lon, lat = unproject(ce, cn)
                c, r = ~gtr * (lon, lat)
                r, c = int(r), int(c)
                g = float(ghsl[r, c]) if 0 <= r < ghsl.shape[0] and 0 <= c < ghsl.shape[1] else 0.0
                h = g if g > 2.0 else 9.0
                # GHSL is a 100 m average: a small footprint is a low house,
                # a big one in a tall cell takes the cell's height.
                h = min(h * (0.8 if area < 150 else 1.15), 3.1 * 14)
        kind = FAR_KINDS.index(b["kind"]) if b["kind"] in FAR_KINDS else 0
        rows.append((ce, cn, w, d, yaw % 180.0, h, kind))
    rows.sort(key=lambda r: (int(r[1] // 2000), int(r[0] // 2000)))
    blob = bytearray(b"ISF1" + struct.pack("<I", len(rows)))
    for ce, cn, w, d, yaw, h, kind in rows:
        blob += struct.pack("<hhHHHHB", int(round(ce * 2)), int(round(cn * 2)),
                            min(65535, int(round(w * 10))), min(65535, int(round(d * 10))),
                            int(round(yaw * 100)) % 18000, min(65535, int(round(h * 10))), kind)
    (HI / "far_all.dat").write_bytes(bytes(blob))
    # The committed set: what makes the skyline (tall or big), the small
    # houses between left to the Sentinel ground's own roofs.
    lean = [r for r in rows if r[2] * r[3] >= LEAN_AREA or r[5] >= LEAN_H]
    blob2 = bytearray(b"ISF1" + struct.pack("<I", len(lean)))
    for ce, cn, w, d, yaw, h, kind in lean:
        blob2 += struct.pack("<hhHHHHB", int(round(ce * 2)), int(round(cn * 2)),
                             min(65535, int(round(w * 10))), min(65535, int(round(d * 10))),
                             int(round(yaw * 100)) % 18000, min(65535, int(round(h * 10))), kind)
    (ASSETS / "far.dat").write_bytes(bytes(blob2))
    hs = np.array([r[5] for r in rows])
    meta = {"kinds": FAR_KINDS,
            "record": "<hhHHHHB: e*2, n*2 (0.5 m), w*10, d*10 (dm), yaw*100 (centideg, 0..180), h*10 (dm), kind",
            "frame": "ENU metres, datum Hagia Sophia; boxes: w along yaw (deg from east, ccw), d across",
            "source": "OpenStreetMap buildings (ODbL 1.0) outside the detailed scene; heights from OSM, else "
                      "JRC GHSL GHS-BUILT-H R2023A (CC BY 4.0)",
            "tall_ge_40m": int((hs >= 40).sum()), "count_all": len(rows), "count_lean": len(lean),
            "files": "far.dat (committed): footprint box >= %d m2 or >= %d m tall; _ground/far_all.dat: every one"
                     % (LEAN_AREA, LEAN_H)}
    (ASSETS / "far.json").write_text(json.dumps(meta, indent=1))
    print(f"  far: {len(rows)} buildings ({len(lean)} committed, {len(blob2) / 1e6:.1f} MB), "
          f"{int((hs >= 40).sum())} >= 40 m", file=sys.stderr)


# ------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="s2,tex,osm,coast,fields,far")
    ap.add_argument("--jobs", type=int, default=max(1, mp.cpu_count() - 2))
    a = ap.parse_args(argv)
    steps = a.only.split(",")
    HI.mkdir(exist_ok=True)
    if "s2" in steps:
        bake_s2()
    if "tex" in steps:
        bake_textures()
    osm = extract_osm() if any(s in steps for s in ("osm", "coast", "fields", "far")) else None
    if "coast" in steps:
        bake_coast(osm)
    if "fields" in steps:
        bake_fields(osm, a.jobs)
    if "far" in steps:
        bake_far(osm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
