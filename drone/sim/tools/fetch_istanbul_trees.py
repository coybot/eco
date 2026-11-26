#!/usr/bin/env python3
"""Bake Istanbul's trees for the istanbul scene (scripts/istanbul_nature.gd).

Sources (all open; see ../godot/assets/istanbul/CREDITS.md):
  * Copernicus Sentinel-2 L2A (ESA), the COGs on AWS listed by Element84's
    earth-search STAC: red and near-infrared at 10 m. A summer NDVI (the
    median of the clearest July/August scenes, when Istanbul's unwatered grass
    has dried and the trees stand out) says where the canopy really is and
    how dense; a winter NDVI (January/February) says which of it is evergreen
    (cypress, pine) and which loses its leaves (plane, linden, chestnut).
    "Contains modified Copernicus Sentinel data 2025." Free, full and open.
  * OpenStreetMap (c) OpenStreetMap contributors, ODbL 1.0, from the BBBike
    extract the base scene already uses (assets/istanbul/_cache): the mapped
    natural=tree points (with species where tagged), natural=tree_row lines,
    cemeteries (by religion) and mosque precincts.
  * The base scene's own baked layers: landcover.png (OSM parks, woods,
    cemeteries, gardens, courtyards), water.png, buildings.dat, roads.json.

Output, in the scene's ENU frame (metres east/north of Hagia Sophia):

  trees.dat   columns, little-endian (14 bytes a tree):
                u32 count N, then u16 e[N], u16 n[N] (QUANT_M steps from
                ORIGIN), u8 height[N] (0.2 m), u8 crown_radius[N] (0.1 m),
                u8 species[N] (SPECIES below; istanbul_nature.gd holds the
                shape, colour and leaf season of each), u8 kind[N],
                u8 flags[N], u8 shade[N] (per-tree colour variation, 128 =
                the species colour), i8 lean_e[N], i8 lean_n[N] (0.1 m)
              kind   0 mapped (OSM natural=tree), 1 canopy (Sentinel-2),
                     2 tree row (OSM natural=tree_row), 3 courtyard plane,
                     4 shore palm
              flags  bit 0 in a cemetery, bit 1 in a mosque precinct
  trees.json  {"count", "origin", "quant_m", "species", "kinds", "source"}

How the canopy is placed. Sentinel-2 is 10 m a pixel, so it cannot see a
single crown; it sees how green each 10 x 10 m patch is. Summer NDVI is
turned into a canopy fraction (0 at the urban/dry-grass level, 1 at closed
woodland), and trees are dart-thrown at that density, big crowns first:
every 2 m cell whose fraction beats a smooth noise field (so groves and
clearings form at a crown's scale, not salt-and-pepper) may hold a trunk if
no crown is already there. Green land-use areas (parks, woods, cemeteries,
gardens, courtyards) take trees from a lower NDVI than the city blocks, whose
greenness must be strong before a tree is put among the houses. Never on
water, inside a footprint, on a carriageway or tram line, on a pitch or
playground; crowns are kept out of the facades (radius capped at the
distance to the nearest wall + 1 m, as the city prunes them).

Species by place and season: Ottoman cemeteries are cypress woods (with
stone pines and planes); mosque precincts hold great oriental planes
(cinar); the big parks (Gulhane, the Topkapi grounds, Yildiz) are planes,
horse chestnuts, lindens and Judas trees under stone pines; the streets are
planes, lindens, chestnuts, false acacias; where the winter NDVI says
evergreen, the conifers. A few Canary date palms and fan palms stand on the
shore promenades of Kadikoy, Moda and Uskudar.

    uv run --no-project --with numpy --with scipy --with pillow \\
        --with rasterio --with osmium python drone/sim/tools/fetch_istanbul_trees.py
    ... --debug <dir>    # + canopy / species maps as PNGs

Sentinel-2 windows are cached in assets/istanbul/_s2 (gitignored).
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "godot" / "assets" / "istanbul"
UA = "eco-sim/1.0 (drone simulator; istanbul trees)"

# Must match fetch_istanbul.py.
DATUM_LAT = 41.0086
DATUM_LON = 28.9802
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))
BOUNDS = (-6000.0, -3200.0, 5000.0, 4800.0)
RES = 2.0
E0, N0, E1, N1 = BOUNDS
W = int((E1 - E0) / RES)
H = int((N1 - N0) / RES)
QUANT_M = 0.4

STAC = "https://earth-search.aws.element84.com/v1/search"
SUMMER = ("2025-07-01T00:00:00Z/2025-08-31T23:59:59Z", 3)
WINTER = ("2025-01-01T00:00:00Z/2025-03-10T23:59:59Z", 2)

LC = ["urban", "park", "wood", "cemetery", "plaza", "parking", "pitch", "beach", "courtyard",
      "railway", "construction", "garden", "playground"]
L = {k: i for i, k in enumerate(LC)}

# ------------------------------------------------------------------ species
#: Index order is the trees.dat code; istanbul_nature.gd's SPECIES table
#: holds the same list (shape family, colours, leaf season).
SPECIES = ["plane", "linden", "chestnut", "judas", "acacia", "oak", "maple", "magnolia",
           "cypress", "stone_pine", "pine", "cedar", "palm_date", "palm_fan", "laurel", "olive"]
S = {k: i for i, k in enumerate(SPECIES)}
DECIDUOUS = {"plane", "linden", "chestnut", "judas", "acacia", "oak", "maple"}
#: species -> (height range m, crown radius range m) of a mature tree here.
SIZE = {
    "plane": ((16, 28), (6.0, 11.0)),
    "linden": ((12, 20), (4.0, 6.5)),
    "chestnut": ((12, 20), (4.5, 7.0)),
    "judas": ((5, 9), (2.5, 4.0)),
    "acacia": ((10, 18), (3.5, 5.5)),
    "oak": ((12, 20), (4.5, 7.5)),
    "maple": ((10, 16), (3.5, 5.5)),
    "magnolia": ((8, 14), (3.0, 5.0)),
    "cypress": ((14, 26), (1.3, 2.2)),
    "stone_pine": ((14, 22), (5.0, 8.5)),
    "pine": ((12, 20), (3.0, 5.0)),
    "cedar": ((14, 24), (4.0, 6.5)),
    "palm_date": ((7, 12), (3.0, 4.5)),
    "palm_fan": ((10, 16), (1.6, 2.4)),
    "laurel": ((6, 10), (2.5, 4.0)),
    "olive": ((5, 8), (2.5, 3.5)),
}
#: Mixes by setting: [(species, weight)]; "ev" when the winter NDVI says
#: evergreen, "de" when it says deciduous.
MIX = {
    "cemetery": {"ev": [("cypress", 0.72), ("stone_pine", 0.16), ("pine", 0.06), ("laurel", 0.06)],
                 "de": [("cypress", 0.45), ("plane", 0.2), ("judas", 0.12), ("linden", 0.08),
                        ("chestnut", 0.07), ("acacia", 0.08)]},
    "park": {"ev": [("stone_pine", 0.42), ("pine", 0.18), ("cypress", 0.14), ("cedar", 0.14),
                    ("magnolia", 0.07), ("laurel", 0.05)],
             "de": [("plane", 0.26), ("chestnut", 0.18), ("linden", 0.18), ("judas", 0.12),
                    ("oak", 0.1), ("maple", 0.08), ("acacia", 0.08)]},
    "wood": {"ev": [("stone_pine", 0.36), ("pine", 0.3), ("cypress", 0.12), ("cedar", 0.12),
                    ("laurel", 0.1)],
             "de": [("oak", 0.26), ("plane", 0.2), ("chestnut", 0.14), ("linden", 0.14),
                    ("judas", 0.1), ("acacia", 0.1), ("maple", 0.06)]},
    "courtyard": {"ev": [("cypress", 0.4), ("stone_pine", 0.2), ("magnolia", 0.15), ("laurel", 0.1),
                         ("plane", 0.15)],
                  "de": [("plane", 0.55), ("linden", 0.15), ("chestnut", 0.12), ("judas", 0.1),
                         ("acacia", 0.08)]},
    "street": {"ev": [("stone_pine", 0.3), ("magnolia", 0.2), ("cypress", 0.18), ("pine", 0.12),
                      ("laurel", 0.12), ("olive", 0.08)],
               "de": [("plane", 0.32), ("linden", 0.2), ("chestnut", 0.15), ("acacia", 0.14),
                      ("judas", 0.1), ("maple", 0.09)]},
}

#: The Hippodrome (At Meydani): its two rows of plane trees along the
#: square's long sides, from the Kaiser Wilhelm fountain past the obelisks
#: to the Serpentine Column. Centreline from OSM's Sultanahmet Meydani
#: ((c) OpenStreetMap contributors, ODbL); rows ~22 m either side.
HIPPODROME = ((41.00733, 28.97639), (41.00467, 28.97455))
HIPPO_ROWS_M = (-21.0, 21.0)
HIPPO_STEP_M = 11.0


def project(lon, lat):
    return ((np.asarray(lon) - DATUM_LON) * M_PER_DEG_LON, (np.asarray(lat) - DATUM_LAT) * M_PER_DEG_LAT)


def unproject(e, n):
    return (DATUM_LON + np.asarray(e) / M_PER_DEG_LON, DATUM_LAT + np.asarray(n) / M_PER_DEG_LAT)


def to_px(e, n):
    """ENU metres -> (col, row) on the 2 m grid, row 0 = north."""
    return ((np.asarray(e) - E0) / RES, (N1 - np.asarray(n)) / RES)


def _get(url: str, data: bytes | None = None, timeout: int = 120) -> bytes:
    for k in range(5):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA,
                                                                  "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as ex:                          # noqa: BLE001
            print(f"  retry {url[:60]} ({ex})", file=sys.stderr)
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"could not fetch {url}")


# ------------------------------------------------------------- Sentinel-2
def stac_items(window: str, best: int) -> list[dict]:
    w, s = unproject(E0, N0)
    e, n = unproject(E1, N1)
    body = {"collections": ["sentinel-2-l2a"], "bbox": [float(w), float(s), float(e), float(n)],
            "datetime": window, "query": {"eo:cloud_cover": {"lt": 5}}, "limit": 100}
    d = json.loads(_get(STAC, json.dumps(body).encode()))
    feats = [f for f in d.get("features", [])
             if float(f["properties"].get("s2:nodata_pixel_percentage", 100)) < 5.0]
    feats.sort(key=lambda f: float(f["properties"]["eo:cloud_cover"]))
    return feats[:best]


def utm_grid(crs) -> tuple[np.ndarray, np.ndarray]:
    """UTM x, y of every 2 m cell centre (computed on a 50 m lattice and
    interpolated: the map from this frame to UTM is smooth)."""
    from rasterio.warp import transform
    step = 25
    cs = np.arange(0, W + step, step, dtype=np.float64)
    rs = np.arange(0, H + step, step, dtype=np.float64)
    cc, rr = np.meshgrid(cs, rs)
    e = E0 + (cc + 0.5) * RES
    n = N1 - (rr + 0.5) * RES
    lon, lat = unproject(e, n)
    xs, ys = transform("EPSG:4326", crs, lon.ravel().tolist(), lat.ravel().tolist())
    xs = np.array(xs).reshape(cc.shape)
    ys = np.array(ys).reshape(cc.shape)
    fr = (np.arange(H) / step)[:, None] * np.ones((1, W))
    fc = np.ones((H, 1)) * (np.arange(W) / step)[None, :]
    return (ndimage.map_coordinates(xs, [fr, fc], order=1, mode="nearest"),
            ndimage.map_coordinates(ys, [fr, fc], order=1, mode="nearest"))


def s2_band(item: dict, band: str, cache: Path, grids: dict) -> np.ndarray:
    """One band of one scene sampled onto the 2 m grid (bilinear), reflectance."""
    import rasterio
    from rasterio.windows import from_bounds
    f = cache / f"{item['id']}_{band}.npz"
    href = item["assets"][band]["href"]
    if not f.exists():
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MULTIRANGE="YES",
                          CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
            with rasterio.open(href) as src:
                crs = src.crs.to_string()
                if crs not in grids:
                    grids[crs] = utm_grid(src.crs)
                gx, gy = grids[crs]
                win = from_bounds(gx.min() - 40, gy.min() - 40, gx.max() + 40, gy.max() + 40, src.transform)
                win = win.round_offsets().round_lengths()
                arr = src.read(1, window=win, boundless=True, fill_value=0)
                tf = src.window_transform(win)
        np.savez_compressed(f, a=arr, tf=np.array(tuple(tf)[:6]), crs=crs)
    z = np.load(f)
    arr, tf, crs = z["a"], z["tf"], str(z["crs"])
    if crs not in grids:
        import rasterio.crs
        grids[crs] = utm_grid(rasterio.crs.CRS.from_string(crs))
    gx, gy = grids[crs]
    # Affine (a, b, c, d, e, f): x = a*col + b*row + c, y = d*col + e*row + f.
    a, b, c, d, e, ff = tf
    col = (gx - c) / a - 0.5
    row = (gy - ff) / e - 0.5
    v = ndimage.map_coordinates(arr.astype(np.float32), [row, col], order=1, mode="nearest")
    # L2A from baseline 04.00 stores DN = 10000 * reflectance + 1000; earth-search
    # removes that offset itself where it says so (earthsearch:boa_offset_applied).
    off = 0.0 if item["properties"].get("earthsearch:boa_offset_applied", False) else 1000.0
    if float(item["properties"].get("s2:processing_baseline", "0")) < 4.0:
        off = 0.0
    return np.clip((v - off) / 10000.0, -0.1, 1.0), arr == 0


def ndvi(window: str, best: int, cache: Path, grids: dict, label: str) -> np.ndarray:
    items = stac_items(window, best)
    if not items:
        raise RuntimeError(f"no Sentinel-2 scenes for {window}")
    stack = []
    nirs = []
    for it in items:
        red, _ = s2_band(it, "red", cache, grids)
        nir, _ = s2_band(it, "nir", cache, grids)
        v = (nir - red) / np.maximum(nir + red, 1e-3)
        bad = (nir + red) < 0.02                           # nodata / deep shadow
        v[bad] = np.nan
        nir = nir.astype(np.float32)
        nir[bad] = np.nan
        stack.append(v.astype(np.float32))
        nirs.append(nir)
        print(f"  {label}: {it['id']} cloud {it['properties']['eo:cloud_cover']:.2f}%", file=sys.stderr)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out = np.nanmedian(np.stack(stack), axis=0)
        nir = np.nanmedian(np.stack(nirs), axis=0)
    return (np.nan_to_num(out, nan=0.0).astype(np.float32), np.nan_to_num(nir, nan=0.0).astype(np.float32),
            [it["id"] for it in items])


# ------------------------------------------------------------- base layers
def landcover() -> np.ndarray:
    return np.array(Image.open(ASSETS / "landcover.png").convert("L"))


def water() -> np.ndarray:
    m = json.loads((ASSETS / "water.json").read_text())
    img = np.array(Image.open(ASSETS / "water.png").convert("L")) > 127
    b = m["bounds_enu"]
    res = float(m["res_m"])
    cc = (np.arange(W) + 0.5) * RES + E0
    rr = N1 - (np.arange(H) + 0.5) * RES
    xi = ((cc - b[0]) / res).astype(int).clip(0, img.shape[1] - 1)
    yi = ((b[3] - rr) / res).astype(int).clip(0, img.shape[0] - 1)
    return img[yi[:, None], xi[None, :]]


def footprints() -> tuple[np.ndarray, list[np.ndarray]]:
    raw = (ASSETS / "buildings.dat").read_bytes()
    assert raw[:4] == b"IST1"
    n, nv, q = struct.unpack_from("<IIf", raw, 4)
    rec = np.frombuffer(raw, dtype=np.dtype([("first", "<u4"), ("cnt", "<u2"), ("h", "<u2"),
                                             ("mh", "<u2"), ("kind", "u1"), ("flags", "u1"),
                                             ("lv", "u1"), ("col", "u1"), ("name", "<u2")]),
                        count=n, offset=16)
    v = np.frombuffer(raw, dtype="<i2", count=nv * 2, offset=16 + n * 16).reshape(-1, 2).astype(np.float64) * q
    img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(img)
    rings = []
    kinds = rec["kind"].copy()
    for r in rec:
        ring = v[r["first"]:r["first"] + r["cnt"]]
        rings.append(ring)
        c, rr = to_px(ring[:, 0], ring[:, 1])
        if len(ring) >= 3:
            dr.polygon(list(zip(c.tolist(), rr.tolist())), fill=255, outline=255)
    return np.array(img) > 0, rings, kinds


def roads_mask() -> np.ndarray:
    doc = json.loads((ASSETS / "roads.json").read_text())
    img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(img)
    nodes = doc["nodes"]
    for ed in doc["edges"]:
        if ed[8] & 2:                                    # tunnel
            continue
        cls = ed[2]
        wm = float(ed[5]) or 6.0
        if cls in ("path", "steps"):
            wm = min(wm, 2.0)                            # trunks may stand beside a footpath
        a, b = nodes[ed[0]], nodes[ed[1]]
        ca, ra = to_px(*a)
        cb, rb = to_px(*b)
        dr.line([(float(ca), float(ra)), (float(cb), float(rb))], fill=255,
                width=max(1, int(round(wm / RES))))
    for r in doc.get("rails", []):
        if r.get("tunnel"):
            continue
        pts = [tuple(float(v) for v in to_px(*p)) for p in r["pts"]]
        if len(pts) >= 2:
            dr.line(pts, fill=255, width=max(1, int(round((7.0 if r["type"] == "tram" else 9.0) / RES))))
    return np.array(img) > 0


def read_osm(pbf: Path) -> dict:
    """natural=tree nodes (with tags), natural=tree_row lines, cemeteries by
    religion, mosque precincts."""
    import osmium
    out = {"trees": [], "rows": [], "mosques": [], "cemeteries": [], "grass": []}

    def inb(e, n):
        return E0 - 50 <= e <= E1 + 50 and N0 - 50 <= n <= N1 + 50

    class Hd(osmium.SimpleHandler):
        def node(self, o):
            if o.tags.get("natural") == "tree" and o.location.valid():
                e, n = project(o.location.lon, o.location.lat)
                if inb(e, n):
                    out["trees"].append((float(e), float(n), dict(o.tags)))

        def area(self, a):
            t = a.tags
            mosque = t.get("amenity") == "place_of_worship" and t.get("religion") == "muslim"
            cem = t.get("landuse") == "cemetery" or t.get("amenity") == "grave_yard"
            grass = t.get("landuse") in ("grass", "meadow", "village_green") or t.get("natural") == "grassland"
            if not (mosque or cem or grass):
                return
            try:
                for ring in a.outer_rings():
                    pts = np.array([project(nd.lon, nd.lat) for nd in ring], dtype=np.float64)
                    if len(pts) >= 3 and inb(*pts.mean(axis=0)):
                        (out["mosques"] if mosque else out["cemeteries"] if cem else out["grass"]).append(
                            (pts, t.get("religion", ""), t.get("name", "")))
            except osmium.InvalidLocationError:
                pass

        def way(self, w):
            if w.tags.get("natural") != "tree_row":
                return
            try:
                pts = np.array([project(nd.lon, nd.lat) for nd in w.nodes], dtype=np.float64)
            except osmium.InvalidLocationError:
                return
            if len(pts) >= 2 and inb(*pts[0]):
                out["rows"].append((pts, dict(w.tags)))

    Hd().apply_file(str(pbf), locations=True, idx="flex_mem")
    return out


def raster_rings(rings) -> np.ndarray:
    img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(img)
    for pts in rings:
        c, r = to_px(pts[:, 0], pts[:, 1])
        dr.polygon(list(zip(c.tolist(), r.tolist())), fill=255, outline=255)
    return np.array(img) > 0


# ------------------------------------------------------------------ placing
class Grid:
    """Spatial hash of placed crowns for spacing tests."""

    def __init__(self, cell: float = 12.0):
        self.cell = cell
        self.g: dict[tuple[int, int], list[tuple[float, float, float]]] = {}

    def ok(self, e: float, n: float, r: float, k: float) -> bool:
        cx, cy = int(e // self.cell), int(n // self.cell)
        reach = int(math.ceil((r + 12.0) / self.cell))
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                for (qe, qn, qr) in self.g.get((cx + dx, cy + dy), ()):
                    if (qe - e) ** 2 + (qn - n) ** 2 < (k * (qr + r)) ** 2:
                        return False
        return True

    def add(self, e: float, n: float, r: float) -> None:
        self.g.setdefault((int(e // self.cell), int(n // self.cell)), []).append((e, n, r))


def space_r(sp: str, rad: float) -> float:
    """The room a tree takes from its neighbours: its crown, but at least
    the 5-6 m a cypress or palm keeps between trunks in a planted rank."""
    return max(rad, 2.7) if sp in ("cypress", "palm_fan", "palm_date") else rad


def species_from_tags(t: dict) -> str | None:
    g = (t.get("genus") or t.get("species") or t.get("taxon") or "").lower()
    for key, sp in (("platanus", "plane"), ("tilia", "linden"), ("aesculus", "chestnut"),
                    ("cercis", "judas"), ("robinia", "acacia"), ("quercus", "oak"), ("acer", "maple"),
                    ("magnolia", "magnolia"), ("cupressus", "cypress"), ("pinus pinea", "stone_pine"),
                    ("pinus", "pine"), ("cedrus", "cedar"), ("phoenix", "palm_date"),
                    ("washingtonia", "palm_fan"), ("trachycarpus", "palm_fan"), ("laurus", "laurel"),
                    ("olea", "olive"), ("eriobotrya", "laurel"), ("pistacia", "olive")):
        if key in g:
            return sp
    lt = t.get("leaf_type")
    if lt == "palm":
        return "palm_date"
    if lt == "needleleaved":
        return "stone_pine"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ASSETS))
    ap.add_argument("--debug", default="", help="write mask PNGs here")
    ap.add_argument("--seed", type=int, default=1453)
    a = ap.parse_args(argv)
    out = Path(a.out)
    s2cache = ASSETS / "_s2"
    s2cache.mkdir(parents=True, exist_ok=True)
    dbg = Path(a.debug) if a.debug else None
    if dbg:
        dbg.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(a.seed)

    print("base layers", file=sys.stderr)
    lc = landcover()
    assert lc.shape == (H, W), lc.shape
    wet = water()
    occ, rings, kinds = footprints()
    road = roads_mask()
    print("osm", file=sys.stderr)
    osm = read_osm(ASSETS / "_cache" / "Istanbul.osm.pbf")
    print(f"  {len(osm['trees'])} trees, {len(osm['rows'])} tree rows, {len(osm['mosques'])} mosque areas, "
          f"{len(osm['cemeteries'])} cemetery rings", file=sys.stderr)
    # Mosque precincts: OSM's place_of_worship areas and the mosque
    # buildings, grown by 45 m (the courtyard and the garden round it).
    mosque = raster_rings([m[0] for m in osm["mosques"]] + [rings[i] for i in np.nonzero(kinds == 2)[0]])
    mosque = ndimage.binary_dilation(mosque, iterations=int(45 / RES))
    grass = raster_rings([g[0] for g in osm["grass"]])
    cem_muslim = raster_rings([c[0] for c in osm["cemeteries"] if c[1] in ("muslim", "")])

    print("sentinel-2", file=sys.stderr)
    grids: dict = {}
    summer, s_nir, s_ids = ndvi(*SUMMER, s2cache, grids, "summer")
    winter, _w_nir, w_ids = ndvi(*WINTER, s2cache, grids, "winter")
    summer[wet] = 0.0
    winter[wet] = 0.0

    green_zone = np.isin(lc, [L["park"], L["wood"], L["cemetery"], L["garden"], L["courtyard"]])
    no_tree = np.isin(lc, [L["pitch"], L["playground"], L["beach"], L["railway"]])
    bad = wet | occ | road | no_tree
    # Canopy fraction from summer NDVI: dry grass and paving sit at 0.1-0.3,
    # closed woodland at 0.6+. City blocks need more green than parks to
    # take a tree (a 10 m pixel there mixes roofs, street and one crown).
    lo = np.where(green_zone, 0.30, np.where(lc == L["plaza"], 0.36, 0.38)).astype(np.float32)
    hi = np.where(green_zone, 0.62, 0.66).astype(np.float32)
    frac = np.clip((summer - lo) / (hi - lo), 0.0, 1.0)
    # Grass, not trees. Istanbul's grass greens up in the wet winter and dries
    # in summer, the reverse of a deciduous crown: much greener in January
    # than in July is a meadow (the reclaimed shore at Yenikapi, the moat
    # gardens' open plots). A watered lawn is green in both, like an
    # evergreen, but bright in the near-infrared (a smooth sunlit sward;
    # crowns shade themselves): summer NIR above ~0.36 with winter NDVI
    # above 0.6 is a lawn (the Topkapi courts, Sultanahmet Park's beds).
    meadow = np.clip((winter - summer - 0.06) / 0.2, 0.0, 1.0)
    lawn = np.clip((s_nir - 0.36) / 0.06, 0.0, 1.0) * (winter > 0.62)
    frac *= (1.0 - meadow) * (1.0 - 0.35 * lawn)
    # Mapped lawns (landuse=grass): Sentinel-2 cannot tell a watered lawn
    # from a crown at 10 m, OSM can; a few trees still stand on them.
    frac[grass & ~np.isin(lc, [L["wood"], L["cemetery"]])] *= 0.3
    frac[bad] = 0.0
    # Evergreen: still green in winter where it is green in summer. Grass is
    # green in Istanbul's wet winter too, so it only counts where there are
    # trees at all (summer fraction).
    ever = (winter > 0.45) & (winter > summer - 0.1) & (s_nir < 0.34)
    # Clumping: a smooth noise field the fraction must beat. Sigma ~10 m:
    # groves and glades at the scale of a few crowns.
    noise = ndimage.gaussian_filter(rng.random((H, W)).astype(np.float32), 5.0)
    noise = (noise - noise.mean()) / (noise.std() + 1e-6)
    noise = 0.5 + 0.5 * np.tanh(noise * 0.9)              # ~uniform 0..1
    canopy = frac > noise * 0.85 + 0.08
    canopy &= ~bad
    canopy = ndimage.binary_opening(canopy, iterations=1)
    print(f"  canopy {canopy.sum() * RES * RES / 1e6:.2f} km^2 "
          f"(of {green_zone.sum() * RES * RES / 1e6:.2f} km^2 green land use)", file=sys.stderr)

    to_wall = ndimage.distance_transform_edt(~occ).astype(np.float32) * RES
    near_water = ndimage.distance_transform_edt(~wet).astype(np.float32) * RES
    inside = ndimage.distance_transform_edt(canopy).astype(np.float32) * RES

    trees = []      # (e, n, h, r, species, kind, flags, shade, lean e, lean n)
    grid = Grid()

    def setting(c: int, r: int) -> str:
        k = int(lc[r, c])
        if cem_muslim[r, c] or k == L["cemetery"]:
            return "cemetery"
        if mosque[r, c]:
            return "courtyard"
        if k == L["wood"]:
            return "wood"
        if k in (L["park"], L["garden"]):
            return "park"
        if k == L["courtyard"]:
            return "courtyard"
        return "street"

    def pick(c: int, r: int) -> str:
        mix = MIX[setting(c, r)]["ev" if ever[r, c] else "de"]
        w = np.array([m[1] for m in mix])
        return mix[rng.choice(len(mix), p=w / w.sum())][0]

    def flags_at(c, r):
        return (1 if (cem_muslim[r, c] or lc[r, c] == L["cemetery"]) else 0) | (2 if mosque[r, c] else 0)

    def add(e, n, sp, kind, rad=None, h=None, lean=(0.0, 0.0), cap=True):
        c, r = (int(v) for v in to_px(e, n))
        if not (0 <= c < W and 0 <= r < H):
            return False
        (h0, h1), (r0, r1) = SIZE[sp]
        if rad is None:
            rad = float(rng.uniform(r0, r1))
        if h is None:
            # Height follows the crown: a big crown is an old tree.
            t = (rad - r0) / max(r1 - r0, 1e-3)
            h = float(np.clip(h0 + (h1 - h0) * (0.25 + 0.6 * t) + rng.normal(0, 1.2), h0 * 0.8, h1 * 1.1))
        if cap and not sp.startswith("palm") and sp != "cypress":
            room = float(to_wall[r, c]) + 1.0
            if rad > room:
                ratio = h / rad
                rad = max(1.5, room)
                h = max(SIZE[sp][0][0] * 0.6, min(h, rad * ratio * 1.25))
        shade = int(np.clip(128 + rng.normal(0, 14), 60, 200))
        trees.append((e, n, h, rad, S[sp], kind, flags_at(c, r), shade) + tuple(lean))
        grid.add(e, n, space_r(sp, rad))
        return True

    # 1. Mapped trees (OSM natural=tree), species from their tags or the place.
    n_osm = 0
    for e, n, t in osm["trees"]:
        c, r = (int(v) for v in to_px(e, n))
        if not (0 <= c < W and 0 <= r < H) or wet[r, c] or occ[r, c]:
            continue
        sp = species_from_tags(t) or pick(c, r)
        try:
            h = float((str(t.get("height", "")).split() or ["x"])[0])
        except ValueError:
            h = None
        if not grid.ok(e, n, 1.0, 1.0):
            continue
        add(e, n, sp, 0, h=min(h, 35.0) if h and h > 3 else None)
        n_osm += 1
    print(f"  mapped trees {n_osm}", file=sys.stderr)

    # 2. The Hippodrome's plane rows.
    ha = np.array(project(HIPPODROME[0][1], HIPPODROME[0][0]), float)
    hb = np.array(project(HIPPODROME[1][1], HIPPODROME[1][0]), float)
    hl = float(np.hypot(*(hb - ha)))
    hu = (hb - ha) / hl
    hs = np.array([-hu[1], hu[0]])
    n_hip = 0
    for off in HIPPO_ROWS_M:
        t = 8.0
        while t < hl - 6.0:
            p = ha + hu * (t + rng.uniform(-1.0, 1.0)) + hs * (off + rng.uniform(-0.8, 0.8))
            c, r = (int(v) for v in to_px(*p))
            if not occ[r, c] and grid.ok(float(p[0]), float(p[1]), 3.0, 0.6):
                rad = float(rng.uniform(6.0, 8.5))
                lean = -np.sign(off) * hs * 1.0
                if add(float(p[0]), float(p[1]), "plane", 2, rad=rad, h=float(rng.uniform(17, 23)),
                       lean=(float(lean[0]), float(lean[1])), cap=False):
                    n_hip += 1
            t += HIPPO_STEP_M
    print(f"  Hippodrome planes {n_hip}", file=sys.stderr)

    # 3. Tree rows (OSM natural=tree_row): every ~8 m along the line.
    n_row = 0
    for pts, t in osm["rows"]:
        sp = species_from_tags(t)
        for k in range(len(pts) - 1):
            a0, b0 = pts[k], pts[k + 1]
            seg = float(np.hypot(*(b0 - a0)))
            s = 0.0
            while s < seg:
                p = a0 + (b0 - a0) * (s / max(seg, 1e-6))
                c, r = (int(v) for v in to_px(*p))
                if 0 <= c < W and 0 <= r < H and not bad[r, c] and grid.ok(float(p[0]), float(p[1]), 2.5, 0.7):
                    if add(float(p[0]), float(p[1]), sp or pick(c, r), 2):
                        n_row += 1
                s += 8.0
    print(f"  tree-row trees {n_row}", file=sys.stderr)

    # 4. Great planes in mosque precincts: the open courtyards that are green
    # in summer get one big cinar per ~1500 m^2 of green, the largest first.
    n_cinar = 0
    court = mosque & ~occ & ~road & ~wet
    cs = frac * court
    rr, cc = np.nonzero(cs > 0.35)
    order = np.argsort(-cs[rr, cc] - rng.random(len(rr)) * 0.2)
    for i in order:
        e = E0 + (cc[i] + 0.5) * RES
        n = N1 - (rr[i] + 0.5) * RES
        if to_wall[rr[i], cc[i]] < 4.0:
            continue
        rad = float(np.clip(to_wall[rr[i], cc[i]] + 2.0, 6.0, 12.5))
        if not grid.ok(e, n, rad, 0.75):
            continue
        add(e, n, "plane", 3, rad=rad, h=float(np.clip(rad * 2.3 + rng.normal(0, 1.5), 16, 30)), cap=False)
        n_cinar += 1
    print(f"  courtyard planes {n_cinar}", file=sys.stderr)

    # 5. Canopy from Sentinel-2: dart-throwing, the deepest canopy first.
    ok = canopy
    rr, cc = np.nonzero(ok)
    order = rng.permutation(len(rr))
    rr, cc = rr[order], cc[order]
    jit = rng.uniform(-0.5, 0.5, (len(rr), 2)) * RES
    n_can = 0
    for pass_k, (dlo, dhi) in enumerate(((12.0, 999.0), (6.0, 12.0), (2.0, 6.0), (0.0, 2.0))):
        for i in range(len(rr)):
            d = inside[rr[i], cc[i]]
            if not (dlo <= d < dhi):
                continue
            e = E0 + (cc[i] + 0.5) * RES + jit[i, 0]
            n = N1 - (rr[i] + 0.5) * RES + jit[i, 1]
            sp = pick(cc[i], rr[i])
            (h0, h1), (r0, r1) = SIZE[sp]
            # Deep in the canopy, the species' big crowns; at its edge, small.
            g = float(np.clip(d / 14.0, 0.0, 1.0))
            rad = (r0 + (r1 - r0) * (0.2 + 0.8 * g)) * float(rng.uniform(0.85, 1.12))
            # Crowns interlock in a woodland: centres ~0.75 of the radii apart.
            if not grid.ok(e, n, space_r(sp, rad), 0.75):
                continue
            if add(e, n, sp, 1, rad=rad):
                n_can += 1
        print(f"  canopy pass {pass_k}: {n_can}", file=sys.stderr)

    # 6. Palms on the shore promenades: green land within 60 m of the sea on
    # the Asian shore (Kadikoy, Moda, Uskudar) and at Karakoy/Besiktas.
    n_palm = 0
    shore = (near_water < 60.0) & (near_water > 6.0) & ~bad & np.isin(lc, [L["park"], L["plaza"], L["garden"]])
    rr, cc = np.nonzero(shore)
    order = rng.permutation(len(rr))[: max(1, len(rr) // 260)]
    for i in order:
        e = E0 + (cc[i] + 0.5) * RES
        n = N1 - (rr[i] + 0.5) * RES
        if e < 1500.0 and n < 1500.0:
            continue                                        # the historic peninsula has few
        if not grid.ok(e, n, 3.5, 0.9):
            continue
        sp = "palm_date" if rng.random() < 0.65 else "palm_fan"
        if add(e, n, sp, 4, cap=False):
            n_palm += 1
    print(f"  shore palms {n_palm}", file=sys.stderr)

    arr = np.array(trees, dtype=np.float64)
    nT = len(arr)
    blob = bytearray(struct.pack("<I", nT))
    blob += np.round((arr[:, 0] - E0) / QUANT_M).clip(0, 65535).astype("<u2").tobytes()
    blob += np.round((arr[:, 1] - N0) / QUANT_M).clip(0, 65535).astype("<u2").tobytes()
    blob += np.round(arr[:, 2] / 0.2).clip(1, 255).astype(np.uint8).tobytes()
    blob += np.round(arr[:, 3] / 0.1).clip(1, 255).astype(np.uint8).tobytes()
    for col in (4, 5, 6, 7):
        blob += arr[:, col].astype(np.uint8).tobytes()
    for col in (8, 9):
        blob += np.round(arr[:, col] / 0.1).clip(-127, 127).astype(np.int8).tobytes()
    (out / "trees.dat").write_bytes(bytes(blob))
    kinds = np.bincount(arr[:, 5].astype(int), minlength=5).tolist()
    per_sp = np.bincount(arr[:, 4].astype(int), minlength=len(SPECIES)).tolist()
    meta = {
        "source": ("Contains modified Copernicus Sentinel data 2025 (Sentinel-2 L2A NDVI); "
                   "OpenStreetMap (c) OpenStreetMap contributors, ODbL 1.0"),
        "scenes": {"summer": s_ids, "winter": w_ids},
        "frame": "ENU metres, datum Hagia Sophia 41.0086 N 28.9802 E, no rotation",
        "count": nT, "origin": [E0, N0], "quant_m": QUANT_M,
        "kinds": dict(zip(["mapped", "canopy", "row", "courtyard", "palm"], kinds)),
        "species": SPECIES, "per_species": dict(zip(SPECIES, per_sp)),
    }
    (out / "trees.json").write_text(json.dumps(meta, indent=1))
    print(f"wrote {out}/trees.dat ({len(blob) / 1e6:.1f} MB, {nT} trees: {meta['kinds']}) "
          f"{meta['per_species']}", file=sys.stderr)
    if dbg:
        sm = (np.clip(summer, 0, 1) * 255).astype(np.uint8)
        Image.fromarray(sm).save(dbg / "ndvi_summer.png")
        Image.fromarray((np.clip(winter, 0, 1) * 255).astype(np.uint8)).save(dbg / "ndvi_winter.png")
        vis = np.stack([sm // 2] * 3, axis=-1)
        vis[occ] = (70, 60, 60)
        vis[wet] = (20, 40, 70)
        vis[canopy] = (vis[canopy] * 0.5 + np.array([0, 120, 0])).astype(np.uint8)
        for e, n, h, r, sp, *_ in trees:
            c, rr_ = (int(v) for v in to_px(e, n))
            if 0 <= c < W and 0 <= rr_ < H:
                vis[rr_, c] = (255, 255, 0) if SPECIES[int(sp)] != "cypress" else (255, 0, 255)
        Image.fromarray(vis).save(dbg / "trees.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
