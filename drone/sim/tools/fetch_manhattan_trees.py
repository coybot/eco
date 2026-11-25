#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["numpy", "pillow", "scipy"]
# ///
"""Bake Manhattan's trees and park furniture for the photoreal scene
(scripts/nature.gd, a child of env_manhattan_photo).

Sources (every one public domain or open-licensed; see CREDITS.md):
  * NYC Open Data, 2015 Street Tree Census (uvpi-gqnh), public domain:
    every street tree in Manhattan with status Alive (~62k), at its surveyed
    spot, with species and trunk diameter (tree_dbh, INCHES at 4.5 ft).
  * NYC Open Data, Parks Properties (enfh-gkve), public domain: the city's
    park polygons.
  * OpenStreetMap (c) OpenStreetMap contributors, ODbL 1.0, via Overpass:
    leisure=park/garden/nature_reserve, natural=wood/scrub, landuse=grass/
    forest (the parks NYC Parks does not run: Hudson River Park, Battery Park
    City, the Trust and Conservancy grounds, private gardens), pitches and
    playgrounds (kept clear of trees), and hand-mapped natural=tree nodes.
  * USGS National Map imagery (USGSImageryOnly tile service, z16, ~1.8 m/px),
    public domain: which parts of the parks are canopy and which are lawn.
  * roads.json (NYC Centerline, public domain), already baked: park paths and
    drives, for lamp posts and benches and to keep trunks off the paths.

Output, in the DATASET frame of buildings.json (metres east/north of the
Battery Park datum, turned 29 deg onto the street grid; fetch_manhattan.py):

  trees.dat    (not .bin: the repo ignores *.bin for model weights)
               columns, little-endian, quantised (15 bytes a tree):
                 u32 count N, then u16 e[N], u16 n[N] (QUANT_M steps from
                 the raster origin [E0, N0] in trees.json), u8 height[N]
                 (0.2 m), u8 crown_radius[N] (0.1 m), u8 family[N],
                 u8 palette[N], u8 kind[N], u8 flags[N], u8 tint_r[N],
                 u8 tint_g[N], u8 tint_b[N], i8 lean_e[N], i8 lean_n[N] (0.1 m)
               family   crown shape: 0 round, 1 vase/spreading, 2 columnar/oval,
                        3 conical (nature.gd shapes one crown mesh per family)
               palette  leaf colour (PALETTE below; nature.gd has the colours)
               kind     0 street tree (census), 1 park canopy (photo),
                        2 placed: mapped (OSM) or laid out (the Mall's elms)
               flags    bit 0 airy crown (honeylocust, locusts: fine leaves you
                        see the sky through)
               lean     how far the crown's centre leans off the trunk (m),
                        away from the nearest facade: street trees whose crown
                        would not fit the sidewalk grow out over the street
               tint     the crown's colour, sRGB: the mean of the photo's
                        vegetation pixels within ~5 m of the trunk, or the
                        species colour (PALETTE_SRGB) where the photo shows
                        none (a young tree, a crown in a building's shadow)
  trees.json   {"count", "origin": [E0, N0], "quant_m", "kinds", "palettes",
                "families", "lamps": [[e, n, hdg]],
                "benches": [[e, n, hdg]], "source"}

Why canopy from the photo rather than a scatter over the park polygons:
Manhattan's parks are woodland in some places and lawn in others, and the
open places are the famous ones (the Great Lawn, Sheep Meadow, the ballfields
of Riverside and Randall's Island). A uniform scatter fills them. The z16
photo separates them cleanly: canopy is darker and textured, lawn bright and
smooth, so trees go where the photo shows crowns and the lawns stay open by
construction. Crowns are sized by how far each spot is inside the canopy
patch, so woodland interiors get big interlocking crowns and the patch edges
smaller ones, which is how a real woodland edge looks from above.

Nothing is placed on water (water.png), inside a building footprint
(buildings.json), on a pitch or playground, or (park trees) on a path or a
drive; street trees, which stand on the sidewalk, only lose the few the
census puts inside a footprint box.

    python3 fetch_manhattan_trees.py            # fetches once into _cache/
    python3 fetch_manhattan_trees.py --debug /tmp/trees   # + mask PNGs
"""
from __future__ import annotations

import argparse
import io
import json
import math
import struct
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "godot" / "assets" / "manhattan"
UA = "eco-sim/1.0 (drone simulator; manhattan trees)"

TREE_CENSUS = "https://data.cityofnewyork.us/resource/uvpi-gqnh.json"
PARKS = "https://data.cityofnewyork.us/resource/enfh-gkve.json"
OVERPASS = ("https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter")
TILES = ("https://basemap.nationalmap.gov/arcgis/rest/services/"
         "USGSImageryOnly/MapServer/tile/{z}/{y}/{x}")
#: z16 is the service's deepest level here (z17 returns 404).
ZOOM = 16
OSM_BBOX = (40.68, -74.05, 40.885, -73.90)    # s, w, n, e

# Must match fetch_manhattan.py exactly.
DATUM_LAT = 40.7005
DATUM_LON = -74.0170
GRID_ROTATION_DEG = 29.0
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))

#: Working raster (dataset frame). 2 m: a crown is 3-9 m across.
RES = 2.0
E0, N0, E1, N1 = -3200.0, -200.0, 2900.0, 22100.0
W = int((E1 - E0) / RES)
H = int((N1 - N0) / RES)

IN_TO_CM = 2.54
#: Position step in trees.dat: u16 over the 22.3 km of the island.
QUANT_M = 0.4

# ------------------------------------------------------------------ species
#: Leaf colours, by name; nature.gd holds the RGB (index order matters).
PALETTE = ["plane", "locust", "pear", "ginkgo", "oak", "linden", "conifer",
           "pagoda", "purple", "elm", "maple"]
P = {k: i for i, k in enumerate(PALETTE)}
#: Each palette's typical summer crown in the z16 photo, sRGB.
PALETTE_SRGB = [(68, 92, 48), (98, 116, 50), (50, 74, 36), (88, 116, 50), (46, 70, 36),
                (60, 86, 42), (40, 58, 40), (68, 96, 46), (78, 48, 46), (60, 84, 40),
                (56, 84, 38)]
FAMILIES = ["round", "vase", "columnar", "conical"]
ROUND, VASE, COLUMNAR, CONICAL = range(4)
AIRY = 1

#: spc_common -> (family, palette, Hmax m, k cm, crown-width factor, flags).
#: Height H = 1.8 + Hmax * (1 - exp(-dbh_cm / k)); crown diameter
#: CD = cw * (1.2 + 0.19 * dbh_cm), capped at 22 m. Fitted by eye to the
#: urban allometry in McPherson et al., "Urban Tree Database" (USDA FS, 2016)
#: for the species NYC plants most; good to a metre or two, which from a drone
#: at 30 m and up is all that shows.
SPECIES = {
    "London planetree": (ROUND, P["plane"], 26, 45, 1.0, 0),
    "honeylocust": (VASE, P["locust"], 18, 30, 1.05, AIRY),
    "Callery pear": (COLUMNAR, P["pear"], 12, 22, 0.8, 0),
    "ginkgo": (COLUMNAR, P["ginkgo"], 20, 40, 0.55, 0),
    "pin oak": (CONICAL, P["oak"], 22, 35, 0.8, 0),
    "Sophora": (ROUND, P["pagoda"], 18, 30, 1.0, 0),
    "Japanese zelkova": (VASE, P["pagoda"], 18, 30, 1.05, 0),
    "littleleaf linden": (CONICAL, P["linden"], 16, 30, 0.85, 0),
    "American elm": (VASE, P["elm"], 26, 45, 1.15, 0),
    "American linden": (ROUND, P["linden"], 20, 35, 0.95, 0),
    "silver linden": (ROUND, P["linden"], 18, 35, 0.95, 0),
    "northern red oak": (ROUND, P["oak"], 22, 38, 1.0, 0),
    "willow oak": (ROUND, P["ginkgo"], 20, 35, 0.95, 0),
    "swamp white oak": (ROUND, P["oak"], 20, 38, 1.0, 0),
    "white oak": (ROUND, P["oak"], 20, 38, 1.0, 0),
    "English oak": (COLUMNAR, P["oak"], 18, 35, 0.75, 0),
    "shingle oak": (ROUND, P["oak"], 18, 35, 1.0, 0),
    "black oak": (ROUND, P["oak"], 20, 38, 1.0, 0),
    "sawtooth oak": (ROUND, P["oak"], 16, 30, 1.0, 0),
    "Schumard's oak": (ROUND, P["oak"], 20, 38, 1.0, 0),
    "scarlet oak": (ROUND, P["oak"], 20, 38, 1.0, 0),
    "cherry": (ROUND, P["plane"], 8, 18, 1.0, 0),
    "crab apple": (ROUND, P["pear"], 6, 15, 1.0, 0),
    "hawthorn": (ROUND, P["pear"], 6, 15, 1.0, 0),
    "'Schubert' chokecherry": (ROUND, P["purple"], 8, 18, 0.8, 0),
    "purple-leaf plum": (ROUND, P["purple"], 7, 16, 1.0, 0),
    "Japanese tree lilac": (ROUND, P["linden"], 7, 16, 0.9, 0),
    "magnolia": (ROUND, P["pear"], 8, 18, 1.0, 0),
    "eastern redbud": (ROUND, P["plane"], 7, 15, 1.1, 0),
    "serviceberry": (COLUMNAR, P["plane"], 7, 15, 0.8, 0),
    "Chinese elm": (VASE, P["elm"], 16, 30, 1.05, 0),
    "Siberian elm": (VASE, P["elm"], 18, 32, 1.0, 0),
    "green ash": (ROUND, P["linden"], 18, 32, 0.95, 0),
    "white ash": (ROUND, P["linden"], 20, 35, 0.95, 0),
    "red maple": (ROUND, P["maple"], 16, 30, 0.9, 0),
    "Norway maple": (ROUND, P["maple"], 15, 30, 1.05, 0),
    "sugar maple": (ROUND, P["maple"], 18, 35, 0.95, 0),
    "silver maple": (VASE, P["maple"], 20, 35, 1.1, 0),
    "Amur maple": (ROUND, P["maple"], 6, 15, 1.0, 0),
    "golden raintree": (ROUND, P["locust"], 10, 22, 1.0, 0),
    "Kentucky coffeetree": (VASE, P["oak"], 18, 35, 0.9, AIRY),
    "black locust": (COLUMNAR, P["locust"], 18, 30, 0.8, AIRY),
    "sweetgum": (CONICAL, P["plane"], 20, 35, 0.75, 0),
    "dawn redwood": (CONICAL, P["ginkgo"], 22, 35, 0.55, 0),
    "bald cypress": (CONICAL, P["ginkgo"], 20, 35, 0.55, 0),
    "European hornbeam": (COLUMNAR, P["linden"], 14, 28, 0.7, 0),
    "American hornbeam": (ROUND, P["linden"], 9, 20, 0.95, 0),
    "common hackberry": (VASE, P["linden"], 18, 32, 1.0, 0),
    "tree of heaven": (VASE, P["locust"], 16, 28, 1.0, 0),
    "Turkish hazelnut": (CONICAL, P["linden"], 14, 28, 0.75, 0),
    "tulip-poplar": (COLUMNAR, P["ginkgo"], 26, 45, 0.75, 0),
    "katsura tree": (ROUND, P["plane"], 14, 28, 1.0, 0),
    "black cherry": (ROUND, P["oak"], 18, 32, 0.95, 0),
    "mulberry": (ROUND, P["plane"], 10, 22, 1.1, 0),
    "Amur cork tree": (VASE, P["plane"], 12, 25, 1.1, 0),
    "Ohio buckeye": (ROUND, P["plane"], 12, 25, 1.0, 0),
    "horse chestnut": (ROUND, P["oak"], 16, 30, 1.0, 0),
    "eastern white pine": (CONICAL, P["conifer"], 20, 35, 0.6, 0),
    "Austrian pine": (CONICAL, P["conifer"], 14, 30, 0.65, 0),
    "Scots pine": (CONICAL, P["conifer"], 14, 30, 0.6, 0),
    "Norway spruce": (CONICAL, P["conifer"], 18, 35, 0.5, 0),
    "Colorado spruce": (CONICAL, P["conifer"], 14, 30, 0.5, 0),
    "Atlas cedar": (CONICAL, P["conifer"], 16, 30, 0.6, 0),
    "eastern redcedar": (CONICAL, P["conifer"], 10, 22, 0.45, 0),
}
DEFAULT_SPECIES = (ROUND, P["plane"], 15, 30, 1.0, 0)

#: Park trees have no species; the mix is what Manhattan's park woodlands
#: hold (oak, elm, plane, cherry, maple, tupelo, a few pines):
#: (weight, family, palette, height / crown-radius, flags).
PARK_MIX = [
    (0.20, ROUND, P["oak"], 2.6, 0),
    (0.14, ROUND, P["plane"], 2.5, 0),
    (0.12, ROUND, P["maple"], 2.4, 0),
    (0.10, ROUND, P["linden"], 2.5, 0),
    (0.12, VASE, P["elm"], 2.3, 0),
    (0.06, VASE, P["locust"], 2.3, AIRY),
    (0.10, CONICAL, P["oak"], 3.0, 0),
    (0.05, CONICAL, P["conifer"], 3.2, 0),
    (0.07, COLUMNAR, P["ginkgo"], 3.4, 0),
    (0.04, ROUND, P["purple"], 2.2, 0),
]


#: Central Park's Mall and Literary Walk: the straight promenade from the
#: Shakespeare statue (~66th St) to the Naumburg Bandshell, under four rows of
#: American elms whose crowns meet over the walk. Centreline extended along
#: OSM's "Literary Walk" area (way 1315432496, (c) OpenStreetMap contributors,
#: ODbL); the walk is ~10.5 m (35 ft) wide.
MALL = ((40.76940, -73.97272), (40.77325, -73.97136))
MALL_ROWS_M = (-15.5, -7.0, 7.0, 15.5)      # elm rows across the walk
MALL_STEP_M = 9.0
MALL_CLEAR_M = 22.0                         # nothing else planted this close to the walk

# ------------------------------------------------------------------ frame
def project(lon, lat):
    """lon/lat -> dataset frame (e, n). Works on scalars and numpy arrays."""
    x = (np.asarray(lon) - DATUM_LON) * M_PER_DEG_LON
    y = (np.asarray(lat) - DATUM_LAT) * M_PER_DEG_LAT
    t = math.radians(GRID_ROTATION_DEG)
    return x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t)


def unproject(e, n):
    t = math.radians(GRID_ROTATION_DEG)
    x = e * math.cos(t) + n * math.sin(t)
    y = -e * math.sin(t) + n * math.cos(t)
    return DATUM_LON + x / M_PER_DEG_LON, DATUM_LAT + y / M_PER_DEG_LAT


def to_px(e, n):
    """Dataset (e, n) -> working raster (col, row); row 0 is the SOUTH edge."""
    return (np.asarray(e) - E0) / RES, (np.asarray(n) - N0) / RES


# ------------------------------------------------------------------ fetch
def _get(url: str, data: bytes | None = None, timeout: int = 240) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as ex:                          # noqa: BLE001
            if attempt == 5:
                raise
            wait = 10 * 2 ** attempt
            print(f"  retry in {wait}s ({ex})", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_census(cache: Path) -> list[dict]:
    f = cache / "street_trees_2015_manhattan.json"
    if f.exists():
        return json.loads(f.read_text())
    rows: list[dict] = []
    off = 0
    while True:
        qs = urllib.parse.urlencode({
            "$select": "tree_id,tree_dbh,spc_common,latitude,longitude,curb_loc,health",
            "$where": "borocode='1' AND status='Alive'",
            "$limit": 50000, "$offset": off, "$order": "tree_id"})
        page = json.loads(_get(TREE_CENSUS + "?" + qs))
        rows += page
        print(f"  {len(rows)} street trees", file=sys.stderr)
        if len(page) < 50000:
            break
        off += 50000
    f.write_text(json.dumps(rows))
    return rows


def fetch_parks(cache: Path) -> list[dict]:
    f = cache / "parks_properties_manhattan.json"
    if f.exists():
        return json.loads(f.read_text())
    qs = urllib.parse.urlencode({"$where": "borough='M'", "$limit": 5000,
                                 "$select": "signname,typecategory,acres,multipolygon"})
    rows = json.loads(_get(PARKS + "?" + qs))
    f.write_text(json.dumps(rows))
    return rows


def _overpass(q: str) -> dict:
    last = None
    for url in OVERPASS:
        try:
            return json.loads(_get(url, urllib.parse.urlencode({"data": q}).encode(), 300))
        except Exception as ex:                          # noqa: BLE001
            last = ex
    raise RuntimeError(f"overpass failed: {last}")


def fetch_osm(cache: Path) -> tuple[dict, dict]:
    s, w, n, e = OSM_BBOX
    bb = f"({s},{w},{n},{e})"
    fa = cache / "osm_green_areas.json"
    if not fa.exists():
        q = (f"[out:json][timeout:240];("
             f'way["leisure"~"^(park|garden|pitch|playground|nature_reserve|recreation_ground)$"]{bb};'
             f'relation["leisure"~"^(park|garden|nature_reserve)$"]{bb};'
             f'way["natural"~"^(wood|scrub|grassland)$"]{bb};relation["natural"~"^(wood|scrub)$"]{bb};'
             f'way["landuse"~"^(forest|grass|meadow)$"]{bb};relation["landuse"~"^(forest|grass)$"]{bb};'
             f");out geom;")
        fa.write_text(json.dumps(_overpass(q)))
    ft = cache / "osm_trees.json"
    if not ft.exists():
        q = f'[out:json][timeout:240];(node["natural"="tree"]{bb};);out body;'
        ft.write_text(json.dumps(_overpass(q)))
    return json.loads(fa.read_text()), json.loads(ft.read_text())


def _tile(x: int, y: int, cache: Path):
    f = cache / f"{ZOOM}_{y}_{x}.jpg"
    if not f.exists():
        try:
            f.write_bytes(_get(TILES.format(z=ZOOM, y=y, x=x), timeout=60))
        except Exception:                                # noqa: BLE001
            return None
    try:
        return np.asarray(Image.open(f).convert("RGB"))
    except Exception:                                    # noqa: BLE001
        f.unlink(missing_ok=True)
        return None


def photo(cache: Path, want: np.ndarray) -> np.ndarray:
    """USGS z16 imagery resampled onto the working raster (H x W x 3, row 0
    SOUTH), fetched only for tiles that touch `want` (bool H x W)."""
    tcache = cache / f"usgs_z{ZOOM}"
    tcache.mkdir(parents=True, exist_ok=True)
    nt = 2.0 ** ZOOM
    # Every wanted pixel's tile, on a coarse 16 px lattice (a tile is ~140 px).
    rr, cc = np.nonzero(want[::16, ::16])
    e = E0 + (cc * 16 + 8) * RES
    nn = N0 + (rr * 16 + 8) * RES
    lon, lat = unproject(e, nn)
    tx = ((lon + 180.0) / 360.0 * nt).astype(int)
    ty = ((1.0 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2.0 * nt).astype(int)
    keys = sorted(set(zip(tx.tolist(), ty.tolist())))
    grown = set()
    for x, y in keys:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                grown.add((x + dx, y + dy))
    keys = sorted(grown)
    print(f"  imagery: {len(keys)} z{ZOOM} tiles", file=sys.stderr)
    with ThreadPoolExecutor(4) as ex:
        tiles = dict(zip(keys, ex.map(lambda k: _tile(k[0], k[1], tcache), keys)))
    out = np.zeros((H, W, 3), np.uint8)
    cols = np.arange(W)
    for r0 in range(0, H, 512):
        rows = np.arange(r0, min(H, r0 + 512))
        CC, RR = np.meshgrid(cols, rows)
        lon, lat = unproject(E0 + (CC + 0.5) * RES, N0 + (RR + 0.5) * RES)
        px = (lon + 180.0) / 360.0 * nt
        py = (1.0 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2.0 * nt
        tx, ty = px.astype(int), py.astype(int)
        ix = ((px - tx) * 256).astype(int).clip(0, 255)
        iy = ((py - ty) * 256).astype(int).clip(0, 255)
        blk = np.zeros(CC.shape + (3,), np.uint8)
        m = want[rows]
        for (x, y), t in tiles.items():
            if t is None:
                continue
            sel = m & (tx == x) & (ty == y)
            if sel.any():
                blk[sel] = t[iy[sel], ix[sel]]
        out[rows] = blk
    return out


# ------------------------------------------------------------------ rasters
def blank(v=0) -> Image.Image:
    return Image.new("L", (W, H), v)


def draw_ring(dr: ImageDraw.ImageDraw, lonlat, fill: int) -> None:
    if len(lonlat) < 3:
        return
    a = np.asarray(lonlat, float)
    e, n = project(a[:, 0], a[:, 1])
    c, r = to_px(e, n)
    dr.polygon(list(zip(c.tolist(), r.tolist())), fill=fill)


def as_array(img: Image.Image) -> np.ndarray:
    return np.asarray(img) > 0


def footprints() -> np.ndarray:
    doc = json.loads((ASSETS / "buildings.json").read_text())
    occ = np.zeros((H, W), bool)
    for b in doc["buildings"]:
        x, y, w, h = b["r"]
        c0, r0 = to_px(x, y)
        c1, r1 = to_px(x + w, y + h)
        c0, c1 = int(max(0, math.floor(c0))), int(min(W, math.ceil(c1)))
        r0, r1 = int(max(0, math.floor(r0))), int(min(H, math.ceil(r1)))
        if c1 > c0 and r1 > r0:
            occ[r0:r1, c0:c1] = True
    return occ


def water() -> np.ndarray:
    meta = json.loads((ASSETS / "water.json").read_text())
    b = meta["bounds_enu"]
    res = float(meta["res_m"])
    img = np.asarray(Image.open(ASSETS / "water.png").convert("L"))
    cc, rr = np.meshgrid(np.arange(W), np.arange(H))
    e = E0 + (cc + 0.5) * RES
    n = N0 + (rr + 0.5) * RES
    x = ((e - b[0]) / res).astype(int)
    y = ((b[3] - n) / res).astype(int)
    ok = (x >= 0) & (y >= 0) & (x < img.shape[1]) & (y < img.shape[0])
    w = np.ones((H, W), bool)
    w[ok] = img[y[ok], x[ok]] > 127
    return w


def roads_mask() -> tuple[np.ndarray, dict]:
    """Every road and path as its carriageway width (park trees keep their
    trunks off them), plus the roads doc for furniture."""
    doc = json.loads((ASSETS / "roads.json").read_text())
    img = blank()
    dr = ImageDraw.Draw(img)
    nodes = doc["nodes"]
    for ed in doc["edges"]:
        a, b, cls = nodes[ed[0]], nodes[ed[1]], ed[2]
        if ed[8] & 2:                                    # tunnel
            continue
        wm = float(ed[5]) or {"path": 3.0, "street": 9.0, "service": 6.0}.get(cls, 12.0)
        if cls == "path":
            wm = max(3.0, min(wm, 6.0))
        ca, ra = to_px(*a)
        cb, rb = to_px(*b)
        dr.line([(float(ca), float(ra)), (float(cb), float(rb))], fill=255,
                width=max(1, int(round((wm + 1.0) / RES))))
    return as_array(img), doc


# ------------------------------------------------------------------ canopy
def classify(rgb: np.ndarray, zone: np.ndarray) -> np.ndarray:
    """Canopy (True) vs lawn, paving, water and shadow from the photo.

    Canopy is green, darker than lawn, and rough: a crown is lit on one side
    and shaded on the other, so it has strong 2-4 px brightness variance that
    mown grass does not. Lawn is green and smooth; turf pitches are bright
    and smooth; paths and plazas are grey. A pixel is canopy when it is green
    enough AND (dark-ish OR rough), then the mask is closed and opened by a
    crown's width so single dark pixels and gaps between crowns don't count.
    """
    f = rgb.astype(np.float32) / 255.0
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    exg = 2.0 * g - r - b
    mean = ndimage.uniform_filter(lum, 5)
    var = ndimage.uniform_filter(lum * lum, 5) - mean * mean
    rough = np.sqrt(np.maximum(var, 0.0))
    green = exg > 0.045
    # Shaded canopy goes nearly black-green; keep it when its neighbourhood is green.
    greenish = ndimage.uniform_filter(exg, 7) > 0.05
    dark = lum < 0.30
    canopy = zone & ((green & (dark | (rough > 0.055))) | (greenish & (lum < 0.16)))
    canopy &= ~((lum > 0.42) & (rough < 0.05))          # bright smooth: lawn, turf
    canopy = ndimage.binary_closing(canopy, iterations=2)
    canopy = ndimage.binary_opening(canopy, iterations=1)
    lab, n = ndimage.label(canopy)
    if n:
        sizes = ndimage.sum(canopy, lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes >= 6                           # >= ~24 m^2, one small crown
        canopy = keep[lab]
    return canopy


def photo_tint(rgb: np.ndarray, occ: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean colour of the vegetation pixels in a 5 x 5 (10 m) window, and the
    fraction of the window that is vegetation."""
    f = rgb.astype(np.float32)
    lum = (0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]) / 255.0
    veg = ((2.0 * f[..., 1] - f[..., 0] - f[..., 2]) / 255.0 > 0.03) & (lum < 0.55) & (lum > 0.06) & ~occ
    vf = veg.astype(np.float32)
    m = ndimage.uniform_filter(vf, 5)
    out = np.zeros(rgb.shape, np.uint8)
    for k in range(3):
        s = ndimage.uniform_filter(f[..., k] * vf, 5)
        out[..., k] = np.clip(s / np.maximum(m, 1e-3), 0, 255).astype(np.uint8)
    return out, m


def tint_at(tint, c: int, r: int, pal: int) -> tuple[int, int, int]:
    col, frac = tint
    if 0 <= c < W and 0 <= r < H and frac[r, c] > 0.25:
        return tuple(int(v) for v in col[r, c])
    return PALETTE_SRGB[pal]


class Grid:
    """Spatial hash of placed crowns for spacing tests."""

    def __init__(self, cell: float = 12.0):
        self.cell = cell
        self.g: dict[tuple[int, int], list[tuple[float, float, float]]] = {}

    def ok(self, e: float, n: float, r: float, k: float) -> bool:
        cx, cy = int(e // self.cell), int(n // self.cell)
        reach = int(math.ceil((r + 10.0) / self.cell))
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                for (qe, qn, qr) in self.g.get((cx + dx, cy + dy), ()):
                    if (qe - e) ** 2 + (qn - n) ** 2 < (k * (qr + r)) ** 2:
                        return False
        return True

    def add(self, e: float, n: float, r: float) -> None:
        self.g.setdefault((int(e // self.cell), int(n // self.cell)), []).append((e, n, r))


# ------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ASSETS))
    ap.add_argument("--debug", default="", help="write mask PNGs here")
    ap.add_argument("--seed", type=int, default=2015)
    a = ap.parse_args(argv)
    out = Path(a.out)
    cache = ASSETS / "_cache"
    cache.mkdir(parents=True, exist_ok=True)
    dbg = Path(a.debug) if a.debug else None
    if dbg:
        dbg.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(a.seed)

    print("fetching", file=sys.stderr)
    census = fetch_census(cache)
    parks = fetch_parks(cache)
    osm_areas, osm_trees = fetch_osm(cache)

    print("rasters", file=sys.stderr)
    occ = footprints()
    wet = water()
    road, roads = roads_mask()
    # Manhattan's own land: the land masses that hold Manhattan footprints
    # (the island, Roosevelt, Randall's and Governors, and the mainland at
    # Marble Hill), inside the orthophoto. The OSM fetch reaches New Jersey,
    # Brooklyn and Queens, whose parks drew trees floating over the harbour
    # where no ground is drawn.
    lab, _ = ndimage.label(~wet)
    keep = np.unique(lab[occ & ~wet])
    keep = keep[keep > 0]
    land = np.isin(lab, keep)
    del lab
    ob = json.loads((ASSETS / "ortho.json").read_text())["bounds_enu"]
    c0, r0 = (int(v) for v in to_px(ob[0], ob[1]))
    c1, r1 = (int(v) for v in to_px(ob[2], ob[3]))
    in_ortho = np.zeros((H, W), bool)
    in_ortho[max(r0, 0):min(r1, H), max(c0, 0):min(c1, W)] = True
    land &= in_ortho
    wet |= ~land

    # Green zone: NYC Parks properties plus OSM's parks, gardens and woods.
    # Pitches and playgrounds are cut out after.
    zone_img = blank()
    dz = ImageDraw.Draw(zone_img)
    for p in parks:
        mp = p.get("multipolygon") or {}
        for poly in mp.get("coordinates", []):
            for k, ring in enumerate(poly):
                draw_ring(dz, ring, 0 if k else 255)
    clear_img = blank()
    dcl = ImageDraw.Draw(clear_img)
    for el in osm_areas.get("elements", []):
        t = el.get("tags", {})
        clear = t.get("leisure") in ("pitch", "playground")
        rings = []
        if el["type"] == "way" and "geometry" in el:
            rings = [(el["geometry"], "outer")]
        elif el["type"] == "relation":
            rings = [(m["geometry"], m.get("role", "outer")) for m in el.get("members", [])
                     if m.get("type") == "way" and "geometry" in m]
        for geom, role in rings:
            ll = [(q["lon"], q["lat"]) for q in geom]
            if clear:
                draw_ring(dcl, ll, 255)
            else:
                draw_ring(dz, ll, 0 if role == "inner" else 255)
    clear = as_array(clear_img)
    zone = as_array(zone_img) & land & ~occ & ~clear
    zone = ndimage.binary_erosion(zone, iterations=1) | (zone & ~ndimage.binary_erosion(land, iterations=2))
    print(f"  green zone {zone.sum() * RES * RES / 1e6:.2f} km^2", file=sys.stderr)

    print("imagery", file=sys.stderr)
    # The whole island's land, not just the parks: street trees take their
    # colour from the photo too.
    rgb = photo(cache, land | ndimage.binary_dilation(zone, iterations=4))
    canopy = classify(rgb, zone)
    tint = photo_tint(rgb, occ)
    print(f"  canopy {canopy.sum() * RES * RES / 1e6:.2f} km^2", file=sys.stderr)
    if dbg:
        Image.fromarray(rgb[::-1]).save(dbg / "photo.jpg", quality=85)
        vis = rgb.copy()
        vis[canopy] = (vis[canopy] * 0.4 + np.array([255, 0, 255]) * 0.6).astype(np.uint8)
        Image.fromarray(vis[::-1]).save(dbg / "canopy.jpg", quality=85)

    trees = []   # (e, n, h, r, fam, pal, kind, flags, tint r, g, b, lean e, n)
    grid = Grid()

    # 1. Street trees, from the census, as surveyed.
    #
    # Footprints are boxes in the street-grid frame, so off the grid (the
    # Village, downtown) a box overhangs its sidewalk and ~10 % of census
    # points land inside one. Those are moved to the nearest open spot within
    # ~6 m, and dropped only when there is none. Crowns are capped by the
    # distance to the nearest facade (+1 m), the way the city prunes them: a
    # sphere through a box wall is the one thing that reads as fake.
    to_wall = ndimage.distance_transform_edt(~occ).astype(np.float32) * RES
    nudge = [(dx * RES, dy * RES) for dx in range(-3, 4) for dy in range(-3, 4)]
    nudge.sort(key=lambda d: d[0] ** 2 + d[1] ** 2)
    nb = 0
    moved = 0
    for row in census:
        try:
            lat, lon = float(row["latitude"]), float(row["longitude"])
            dbh = float(row.get("tree_dbh") or 0)
        except (KeyError, ValueError):
            continue
        e, n = (float(v) for v in project(lon, lat))
        c, r = (int(v) for v in to_px(e, n))
        if not (0 <= c < W and 0 <= r < H) or wet[r, c]:
            continue
        if occ[r, c]:
            for dx, dy in nudge:
                c2, r2 = (int(v) for v in to_px(e + dx, n + dy))
                if 0 <= c2 < W and 0 <= r2 < H and not occ[r2, c2] and not wet[r2, c2]:
                    e, n, c, r = e + dx, n + dy, c2, r2
                    moved += 1
                    break
            else:
                nb += 1
                continue
        fam, pal, hmax, k, cw, fl = SPECIES.get(str(row.get("spc_common") or ""), DEFAULT_SPECIES)
        dcm = max(3.0, min(dbh, 60.0)) * IN_TO_CM
        h = 1.8 + hmax * (1.0 - math.exp(-dcm / k))
        rad = 0.5 * min(22.0, cw * (1.2 + 0.19 * dcm))
        if str(row.get("health")) == "Poor":
            rad *= 0.8
        # Room on the sidewalk side; past it the crown leans out over the
        # street, as Manhattan's street trees do (they grow toward the light).
        room = float(to_wall[r, c]) + 1.0
        lean = (0.0, 0.0)
        if rad > room:
            gx = float(to_wall[r, min(c + 1, W - 1)] - to_wall[r, max(c - 1, 0)])
            gy = float(to_wall[min(r + 1, H - 1), c] - to_wall[max(r - 1, 0), c])
            gl = math.hypot(gx, gy)
            if gl > 1e-3:
                sh = min(rad - room, 2.5, 0.25 * h)
                lean = (gx / gl * sh, gy / gl * sh)
                room += sh
        rad = max(1.6, min(rad, room))
        trees.append((e, n, h, rad, fam, pal, 0, fl) + tint_at(tint, c, r, pal) + lean)
        grid.add(e, n, rad)
    print(f"  street trees {len(trees)} ({moved} moved out of footprint boxes, {nb} dropped)",
          file=sys.stderr)

    # Canopy interior distance (m): big crowns deep in a woodland, small ones at its edge.
    inside = ndimage.distance_transform_edt(canopy) * RES
    weights = np.array([m[0] for m in PARK_MIX])
    weights /= weights.sum()

    def park_tree(e, n, rad, kind, fam_pal=None):
        i = rng.choice(len(PARK_MIX), p=weights)
        _, fam, pal, ratio, fl = PARK_MIX[i]
        if fam_pal is not None:
            fam, pal = fam_pal
        c, r = (int(v) for v in to_px(e, n))
        rad = max(1.8, min(rad, float(to_wall[r, c]) + 1.5))
        h = float(np.clip(rad * ratio * rng.uniform(0.85, 1.15), 5.0, 30.0))
        trees.append((e, n, h, rad, fam, pal, kind, fl) + tint_at(tint, c, r, pal) + (0.0, 0.0))
        grid.add(e, n, rad)

    # 1b. The Mall's elms: a double row each side, ~9 m apart, tall vase
    # crowns leaning in over the walk so they meet in the middle.
    ma = np.array(project(MALL[0][1], MALL[0][0]), float)
    mb = np.array(project(MALL[1][1], MALL[1][0]), float)
    mlen = float(np.hypot(*(mb - ma)))
    mu = (mb - ma) / mlen
    mside = np.array([-mu[1], mu[0]])

    def in_mall(e, n):
        q = np.array([e, n]) - ma
        along = float(q @ mu)
        return -10.0 < along < mlen + 10.0 and abs(float(q @ mside)) < MALL_CLEAR_M

    n_mall = 0
    for off in MALL_ROWS_M:
        t = 4.0 + (MALL_STEP_M * 0.5 if abs(off) > 10 else 0.0)
        while t < mlen - 3.0:
            p = ma + mu * (t + rng.uniform(-0.8, 0.8)) + mside * (off + rng.uniform(-0.5, 0.5))
            inner = abs(off) < 10
            rad = float(rng.uniform(8.0, 9.5) if inner else rng.uniform(7.0, 8.5))
            h = float(rng.uniform(23.0, 27.0))
            lean = -np.sign(off) * mside * (2.2 if inner else 1.2)
            c, r = (int(v) for v in to_px(*p))
            trees.append((float(p[0]), float(p[1]), h, rad, VASE, P["elm"], 2, 0)
                         + tint_at(tint, c, r, P["elm"]) + (float(lean[0]), float(lean[1])))
            grid.add(float(p[0]), float(p[1]), rad)
            n_mall += 1
            t += MALL_STEP_M
    print(f"  the Mall: {n_mall} elms", file=sys.stderr)

    # 2. Mapped trees (OSM natural=tree) where the census has none: plazas,
    # housing campuses, courtyards, and the parks' own mapped trees.
    n_osm = 0
    for el in osm_trees.get("elements", []):
        e, n = (float(v) for v in project(el["lon"], el["lat"]))
        c, r = (int(v) for v in to_px(e, n))
        if not (0 <= c < W and 0 <= r < H) or wet[r, c] or occ[r, c] or road[r, c] or clear[r, c]:
            continue
        if in_mall(e, n):
            continue
        t = el.get("tags", {})
        if not grid.ok(e, n, 1.0, 1.0):
            continue                                     # a census tree is already here
        try:
            h = float(str(t.get("height", "0")).split()[0])
        except ValueError:
            h = 0.0
        rad = float(np.clip(inside[r, c] * 0.8 + 3.0, 3.0, 8.0)) if canopy[r, c] else 3.5
        if h > 3.0:
            rad = float(np.clip(h * 0.33, 2.0, 9.0))
        fp = (CONICAL, P["conifer"]) if t.get("leaf_type") == "needleleaved" else None
        park_tree(e, n, rad, 2, fp)
        if h > 3.0:
            lst = list(trees[-1])
            lst[2] = min(h, 35.0)
            trees[-1] = tuple(lst)
        n_osm += 1
    print(f"  mapped trees {n_osm}", file=sys.stderr)

    # 3. Park canopy from the photo: dart-throwing, large crowns first.
    ok_trunk = canopy & ~road & ~occ & ~wet
    rr, cc = np.nonzero(ok_trunk)
    order = rng.permutation(len(rr))
    rr, cc = rr[order], cc[order]
    jit = rng.uniform(-0.5, 0.5, (len(rr), 2)) * RES
    n_park = 0
    for pass_k, (lo, hi) in enumerate(((10.0, 99.0), (5.0, 10.0), (0.0, 5.0))):
        for i in range(len(rr)):
            d = inside[rr[i], cc[i]]
            if not (lo <= d < hi):
                continue
            e = E0 + (cc[i] + 0.5) * RES + jit[i, 0]
            n = N0 + (rr[i] + 0.5) * RES + jit[i, 1]
            if in_mall(e, n):
                continue
            # Woodland crowns in Manhattan's parks are 8-20 m across; the
            # first bake's 5-17 m read as an orchard of shrubs from the air.
            rad = float(np.clip(4.0 + d * 0.5, 4.0, 11.0)) * float(rng.uniform(0.8, 1.15))
            # Crowns interlock in a woodland: centres ~0.7 of the radii apart,
            # so from above the canopy closes as it does in the Ramble.
            if not grid.ok(e, n, rad, 0.7):
                continue
            park_tree(e, n, rad, 1)
            n_park += 1
        print(f"  park canopy pass {pass_k}: {n_park}", file=sys.stderr)

    # 4. Park furniture along paths and drives inside the green zone.
    lamps, benches = furniture(roads, zone | canopy, occ | wet, rng, in_mall)
    # The Mall itself: lamps down both edges of the walk, and the benches
    # that line it end to end (CSCL's path there is a centreline, which put
    # the lamps in the middle of a 10 m promenade).
    t = 6.0
    while t < mlen - 4.0:
        for sgn in (-1.0, 1.0):
            p = ma + mu * t + mside * sgn * 5.6
            lamps.append((float(p[0]), float(p[1]), 0.0))
        t += 24.0
    hdg_in = [math.degrees(math.atan2(-sg * mside[0], -sg * mside[1])) for sg in (-1.0, 1.0)]
    t = 3.0
    while t < mlen - 3.0:
        for k, sgn in enumerate((-1.0, 1.0)):
            if (t % 24.0) > 2.5:              # not at a lamp
                p = ma + mu * t + mside * sgn * 5.0
                benches.append((float(p[0]), float(p[1]), hdg_in[k]))
        t += 2.2
    print(f"  lamps {len(lamps)}, benches {len(benches)}", file=sys.stderr)

    arr = np.array(trees, dtype=np.float64)
    nT = len(arr)
    blob = bytearray(struct.pack("<I", nT))
    blob += np.round((arr[:, 0] - E0) / QUANT_M).clip(0, 65535).astype("<u2").tobytes()
    blob += np.round((arr[:, 1] - N0) / QUANT_M).clip(0, 65535).astype("<u2").tobytes()
    blob += np.round(arr[:, 2] / 0.2).clip(1, 255).astype(np.uint8).tobytes()
    blob += np.round(arr[:, 3] / 0.1).clip(1, 255).astype(np.uint8).tobytes()
    for col in range(4, 11):
        blob += arr[:, col].astype(np.uint8).tobytes()
    for col in (11, 12):
        blob += np.round(arr[:, col] / 0.1).clip(-127, 127).astype(np.int8).tobytes()
    (out / "trees.dat").write_bytes(bytes(blob))
    kinds = np.bincount(arr[:, 6].astype(int), minlength=3).tolist()
    meta = {
        "source": ("NYC Open Data 2015 Street Tree Census (uvpi-gqnh) and Parks Properties "
                   "(enfh-gkve), public domain; OpenStreetMap (c) OpenStreetMap contributors, "
                   "ODbL 1.0; USGS National Map imagery, public domain"),
        "frame": "dataset frame of buildings.json (Battery Park datum, 29 deg grid)",
        "count": nT, "origin": [E0, N0], "quant_m": QUANT_M, "kinds": {"street": kinds[0], "park": kinds[1], "mapped": kinds[2]},
        "families": FAMILIES, "palettes": PALETTE,
        "lamps": [[round(x, 1), round(y, 1), round(h, 1)] for x, y, h in lamps],
        "benches": [[round(x, 1), round(y, 1), round(h, 1)] for x, y, h in benches],
    }
    (out / "trees.json").write_text(json.dumps(meta, separators=(",", ":")))
    print(f"wrote {out}/trees.dat ({len(blob) / 1e6:.1f} MB, {nT} trees: {meta['kinds']}) "
          f"and trees.json", file=sys.stderr)
    if dbg:
        vis = (rgb * 0.5).astype(np.uint8)
        for e, n, h, r, *_ in trees:
            c, rr_ = (int(v) for v in to_px(e, n))
            if 0 <= c < W and 0 <= rr_ < H:
                vis[rr_, c] = (255, 255, 0)
        Image.fromarray(vis[::-1]).save(dbg / "trees.png")
    return 0


def furniture(roads: dict, park: np.ndarray, bad: np.ndarray, rng, skip) -> tuple[list, list]:
    """NYC Parks lamp posts (~one every 30 m along a path, on one side) and
    benches (in twos and threes along the paths, facing them).

    From roads.json's path class (park paths, CSCL rw_type 5-7) and the park
    drives (street class inside a park, not below grade: the transverses are
    sunken and lit from their walls)."""
    nodes = roads["nodes"]
    lamps, benches = [], []
    taken: dict[tuple[int, int], int] = {}

    def free(e, n, cell=6.0):
        k = (int(e // cell), int(n // cell))
        if k in taken:
            return False
        taken[k] = 1
        return True

    def at(e, n):
        c, r = (int(v) for v in to_px(e, n))
        if not (0 <= c < W and 0 <= r < H):
            return False
        return bool(park[r, c]) and not bool(bad[r, c])

    for ed in roads["edges"]:
        cls, flags = ed[2], int(ed[8])
        if cls not in ("path", "street") or flags & (1 | 2 | 8):
            continue
        a = np.array(nodes[ed[0]], float)
        b = np.array(nodes[ed[1]], float)
        mid = (a + b) * 0.5
        if not at(*mid) or skip(*mid):
            continue
        d = b - a
        L = float(np.hypot(*d))
        if L < 4.0:
            continue
        u = d / L
        side = np.array([-u[1], u[0]])
        half = (float(ed[5]) or (3.5 if cls == "path" else 9.0)) * 0.5
        if cls == "path":
            half = min(max(half, 1.5), 3.0)
        hdg = math.degrees(math.atan2(u[0], u[1]))
        # Lamps: one side, every ~30 m; phase by edge so the spacing carries on.
        t = float(rng.uniform(0, 30.0))
        sgn = 1.0 if rng.random() < 0.5 else -1.0
        while t < L:
            p = a + u * t + side * sgn * (half + 0.6)
            if at(*p) and free(*p):
                lamps.append((float(p[0]), float(p[1]), hdg))
            t += float(rng.uniform(26.0, 34.0))
        if cls != "path":
            continue
        # Benches: groups along the path, facing it.
        t = float(rng.uniform(5.0, 40.0))
        while t < L - 2.0:
            s2 = 1.0 if rng.random() < 0.5 else -1.0
            for j in range(int(rng.integers(1, 4))):
                p = a + u * (t + j * 2.2) + side * s2 * (half + 0.9)
                if at(*p) and free(*p, 2.0):
                    # Facing the path: bench +Z faces toward the path centre.
                    face = math.degrees(math.atan2(-s2 * side[0], -s2 * side[1]))
                    benches.append((float(p[0]), float(p[1]), face))
            t += float(rng.uniform(25.0, 60.0))
    return lamps, benches


if __name__ == "__main__":
    sys.exit(main())
