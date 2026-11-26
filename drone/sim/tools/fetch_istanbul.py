#!/usr/bin/env python3
"""Bake the Istanbul scene's base data: the historic peninsula, Galata and
Beyoglu across the Golden Horn, and the Asian shore at Uskudar and Kadikoy.

Sources (every one open; full credits in ../godot/assets/istanbul/CREDITS.md):
  * OpenStreetMap, from BBBike's weekly Istanbul extract (or the Overpass
    API when the extract cannot be fetched): building footprints and tags,
    coastline, streets, tram and rail lines, ferry routes, piers, land use,
    city walls, minarets. (c) OpenStreetMap contributors, ODbL 1.0.
  * NASA SRTM 1 arc-second elevation (AWS Terrain Tiles "skadi"): the hills.
    Public domain.
  * European Commission JRC, GHSL GHS-BUILT-H R2023A (average building
    height, 3 arc-second): heights where OSM has none. CC BY 4.0.

The frame is local ENU in metres around a datum at Hagia Sophia, with no
rotation: +east, +north. Godot places a point (e, n) at (e, y, -n).

Outputs in drone/sim/godot/assets/istanbul/:

  buildings.dat   footprints, binary little-endian (see write_buildings)
  buildings.json  {"kinds": [...], "names": [...], "count": N, ...}
  terrain.zi16    zlib int16 ground height in decimetres, row 0 = NORTH
  terrain.json    {"bounds_enu", "res_m", "size_px", "min_m", "max_m"}
  water.png       8-bit, 255 = water, row 0 = NORTH
  water.json      {"bounds_enu", "res_m", "size_px"}
  landcover.png   8-bit class per pixel (LANDCOVER below), row 0 = NORTH
  landcover.json
  roads.json      {"nodes", "edges", "names", "rails", "ferries", "piers"}
  features.json   {"walls", "towers", "minarets", "aqueducts", "landmarks"}

    uv run --with numpy --with scipy --with pillow --with rasterio --with osmium \
        python drone/sim/tools/fetch_istanbul.py

Raw downloads are cached in assets/istanbul/_cache (gitignored).
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

DATUM_LAT = 41.0086          # Hagia Sophia
DATUM_LON = 28.9802
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))

#: The detailed scene (buildings, streets, land cover), ENU metres:
#: from outside the Theodosian Walls to Kadikoy, and from the Marmara to
#: Besiktas. 11 x 8 km.
BOUNDS = (-6000.0, -3200.0, 5000.0, 4800.0)
#: Terrain and water reach further, so the far shores have ground under them.
WIDE = (-11000.0, -9000.0, 11000.0, 11000.0)
WATER_RES = 4.0
TERRAIN_RES = 10.0
LANDCOVER_RES = 2.0
#: Footprint vertices are stored as int16 in units of Q metres.
Q = 0.2

UA = "eco-sim/1.0 (drone simulator; istanbul dataset)"
#: The main instance first and most often; the mirrors are tried on every
#: third attempt (some networks cannot reach them, and a connect to an
#: unreachable host would otherwise hang the whole run).
OVERPASS = ("https://overpass-api.de/api/interpreter",
            "https://overpass-api.de/api/interpreter",
            "https://overpass.private.coffee/api/interpreter")

#: Building kinds, the index stored per building.
KINDS = ["residential", "commercial", "mosque", "church", "historic", "industrial",
         "public", "wooden", "tower", "small", "ruin", "bazaar", "palace", "station"]
#: Land cover classes, the value stored per pixel (0 = plain urban ground).
LANDCOVER = ["urban", "park", "wood", "cemetery", "plaza", "parking", "pitch",
             "beach", "courtyard", "railway", "construction", "garden", "playground"]


def project(lon: float, lat: float) -> tuple[float, float]:
    return ((lon - DATUM_LON) * M_PER_DEG_LON, (lat - DATUM_LAT) * M_PER_DEG_LAT)


def unproject(e: float, n: float) -> tuple[float, float]:
    return (DATUM_LON + e / M_PER_DEG_LON, DATUM_LAT + n / M_PER_DEG_LAT)


def geo_bbox(b) -> tuple[float, float, float, float]:
    """south, west, north, east of an ENU box."""
    w, s = unproject(b[0], b[1])
    e, n = unproject(b[2], b[3])
    return (s, w, n, e)


# ----------------------------------------------------------------- download

def overpass(cache: Path, name: str, body: str, bbox) -> dict:
    f = cache / f"osm_{name}.json"
    if f.exists():
        return json.loads(f.read_text())
    s, w, n, e = bbox
    q = f"[out:json][timeout:600][maxsize:1073741824][bbox:{s},{w},{n},{e}];({body});out geom qt;"
    data = urllib.parse.urlencode({"data": q}).encode()
    last = ""
    for attempt in range(14):
        url = OVERPASS[attempt % len(OVERPASS)]
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=900 if "overpass-api.de" in url else 60) as r:
                blob = r.read()
            doc = json.loads(blob)
            if "remark" in doc and "runtime error" in str(doc["remark"]):
                raise RuntimeError(doc["remark"])
            f.write_bytes(blob)
            print(f"  osm {name}: {len(doc.get('elements', []))} elements", file=sys.stderr)
            return doc
        except Exception as ex:                          # noqa: BLE001
            last = str(ex)
            print(f"  overpass {name} attempt {attempt}: {last[:120]}", file=sys.stderr)
            time.sleep(10 * (attempt + 1))
    raise SystemExit(f"Overpass failed for {name}: {last}")


#: The whole city's OSM as one file (BBBike's Istanbul extract, ~48 MB,
#: refreshed weekly). Used instead of the Overpass API when present or
#: downloadable: Overpass answers 502/504 under load for queries this size.
PBF_URL = "https://download.bbbike.org/osm/bbbike/Istanbul/Istanbul.osm.pbf"


def _in(b, lat, lon) -> bool:
    s, w, n, e = b
    return s <= lat <= n and w <= lon <= e


def load_pbf(cache: Path) -> dict[str, dict] | None:
    """Every category fetch_osm asks Overpass for, read from the extract into
    the same shape Overpass's `out geom` gives."""
    f = cache / "Istanbul.osm.pbf"
    if not f.exists():
        try:
            req = urllib.request.Request(PBF_URL, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=900) as r:
                f.write_bytes(r.read())
        except Exception as ex:                          # noqa: BLE001
            print(f"  no extract ({ex}); using Overpass", file=sys.stderr)
            return None
    import osmium
    core, wide = geo_bbox(BOUNDS), geo_bbox(WIDE)
    rel_keys = ("building", "building:part", "leisure", "landuse", "natural", "place", "historic")

    class Rels(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.rels, self.members = [], set()

        def relation(self, r):
            t = dict(r.tags)
            if t.get("type") not in ("multipolygon", "building") or not any(k in t for k in rel_keys):
                return
            ms = [{"type": {"w": "way", "n": "node", "r": "relation"}[m.type], "ref": m.ref, "role": m.role}
                  for m in r.members]
            self.rels.append({"type": "relation", "id": r.id, "tags": t, "members": ms})
            self.members.update(m["ref"] for m in ms if m["type"] == "way")

    rh = Rels()
    rh.apply_file(str(f))

    class Rest(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.nodes, self.ways, self.member_geom = [], [], {}

        def node(self, n):
            if len(n.tags) and n.location.valid() and _in(wide, n.location.lat, n.location.lon):
                self.nodes.append({"type": "node", "id": n.id, "lat": n.location.lat,
                                   "lon": n.location.lon, "tags": dict(n.tags)})

        def way(self, w):
            member = w.id in rh.members
            if not len(w.tags) and not member:
                return
            try:
                geom = [{"lat": nd.location.lat, "lon": nd.location.lon} for nd in w.nodes]
            except osmium.InvalidLocationError:
                return
            if not any(_in(wide, g["lat"], g["lon"]) for g in geom[::max(1, len(geom) // 8)] + geom[-1:]):
                return
            if member:
                self.member_geom[w.id] = geom
            if len(w.tags):
                self.ways.append({"type": "way", "id": w.id, "tags": dict(w.tags),
                                  "nodes": [nd.ref for nd in w.nodes], "geometry": geom})

    h = Rest()
    h.apply_file(str(f), locations=True, idx="flex_mem")
    rels = []
    for r in rh.rels:
        for m in r["members"]:
            if m["type"] == "way" and m["ref"] in h.member_geom:
                m["geometry"] = h.member_geom[m["ref"]]
        if any(m.get("geometry") for m in r["members"]):
            rels.append(r)

    def touches(el, b) -> bool:
        if el["type"] == "node":
            return _in(b, el["lat"], el["lon"])
        gs = el.get("geometry") or [g for m in el.get("members", []) for g in (m.get("geometry") or [])]
        return any(_in(b, g["lat"], g["lon"]) for g in gs)

    def pick(pred, b, kinds=("node", "way", "relation")) -> dict:
        els = [el for el in (h.nodes if "node" in kinds else []) + (h.ways if "way" in kinds else [])
               + (rels if "relation" in kinds else []) if pred(el.get("tags", {})) and touches(el, b)]
        return {"elements": els}

    nat_area = ("wood", "scrub", "grassland", "beach", "sand", "heath")
    out = {
        "buildings0": pick(lambda t: "building" in t, core, ("way", "relation")),
        "buildings1": {"elements": []}, "buildings2": {"elements": []}, "buildings3": {"elements": []},
        "coast": pick(lambda t: t.get("natural") == "coastline", wide, ("way",)),
        "water": pick(lambda t: t.get("natural") == "water" or t.get("waterway") == "riverbank",
                      wide, ("way", "relation")),
        "roads": pick(lambda t: "highway" in t, core, ("way",)),
        "rail": pick(lambda t: t.get("railway") in ("rail", "tram", "light_rail", "subway", "funicular",
                                                    "narrow_gauge") or t.get("route") == "ferry"
                     or t.get("man_made") == "pier", core, ("way",)),
        "areas": pick(lambda t: "leisure" in t or "landuse" in t or t.get("natural") in nat_area
                      or t.get("amenity") in ("parking", "place_of_worship", "school", "university")
                      or (t.get("highway") == "pedestrian" and t.get("area") == "yes")
                      or t.get("place") == "square" or "area:highway" in t, core, ("way", "relation")),
        "features": pick(lambda t: t.get("barrier") == "city_wall" or "historic" in t
                         or t.get("tower:type") == "minaret" or t.get("man_made") in ("tower", "aqueduct")
                         or t.get("bridge") == "aqueduct" or t.get("tourism") == "attraction"
                         or "building:part" in t or t.get("natural") == "tree", core),
    }
    for k, v in out.items():
        print(f"  extract {k}: {len(v['elements'])} elements", file=sys.stderr)
    return out


def fetch_osm(cache: Path) -> dict[str, dict]:
    pbf = load_pbf(cache)
    if pbf is not None:
        return pbf
    core = geo_bbox(BOUNDS)
    wide = geo_bbox(WIDE)
    out = {}
    # Buildings in four tiles so no single Overpass answer is huge.
    e0, n0, e1, n1 = BOUNDS
    em, nm = (e0 + e1) / 2, (n0 + n1) / 2
    tiles = [(e0, n0, em, nm), (em, n0, e1, nm), (e0, nm, em, n1), (em, nm, e1, n1)]
    for i, t in enumerate(tiles):
        out[f"buildings{i}"] = overpass(cache, f"buildings{i}",
                                        'way["building"];relation["building"];', geo_bbox(t))
    out["coast"] = overpass(cache, "coast", 'way["natural"="coastline"];', wide)
    out["water"] = overpass(cache, "water",
                            'way["natural"="water"];relation["natural"="water"];'
                            'way["waterway"="riverbank"];', wide)
    out["roads"] = overpass(cache, "roads", 'way["highway"];', core)
    out["rail"] = overpass(cache, "rail",
                           'way["railway"~"^(rail|tram|light_rail|subway|funicular|narrow_gauge)$"];'
                           'way["route"="ferry"];way["man_made"="pier"];', core)
    out["areas"] = overpass(cache, "areas",
                            'way["leisure"];relation["leisure"];way["landuse"];relation["landuse"];'
                            'way["natural"~"^(wood|scrub|grassland|beach|sand|heath)$"];'
                            'way["amenity"~"^(parking|place_of_worship|school|university)$"];'
                            'way["highway"="pedestrian"]["area"="yes"];way["place"="square"];'
                            'relation["place"="square"];way["area:highway"];', core)
    out["features"] = overpass(cache, "features",
                               'way["barrier"="city_wall"];way["historic"="citywalls"];'
                               'relation["historic"="citywalls"];'
                               'node["tower:type"="minaret"];way["tower:type"="minaret"];'
                               'node["man_made"="tower"];way["man_made"="tower"];'
                               'way["man_made"="aqueduct"];way["bridge"="aqueduct"];'
                               'node["historic"];way["historic"]["historic"!="citywalls"];'
                               'node["tourism"="attraction"];way["tourism"="attraction"];'
                               'way["building:part"];relation["building:part"];'
                               'node["natural"="tree"];', core)
    return out


# ------------------------------------------------------------------ geometry

def ring_area(pts: list[tuple[float, float]]) -> float:
    a = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        a += x0 * y1 - x1 * y0
    return a * 0.5


def way_pts(el: dict) -> list[tuple[float, float]]:
    return [project(p["lon"], p["lat"]) for p in el.get("geometry") or [] if p]


def element_rings(el: dict) -> list[list[tuple[float, float]]]:
    """Outer rings of a closed way or a multipolygon relation (inner rings are
    dropped; courtyards come back as land cover)."""
    if el["type"] == "way":
        pts = way_pts(el)
        if len(pts) >= 4 and pts[0] == pts[-1]:
            return [pts[:-1]]
        return []
    rings = []
    segs = [way_pts(m) for m in el.get("members", [])
            if m.get("type") == "way" and m.get("role") in ("outer", "") and m.get("geometry")]
    # Join open member ways end to end into closed rings.
    segs = [s for s in segs if len(s) >= 2]
    while segs:
        cur = segs.pop(0)
        changed = True
        while cur[0] != cur[-1] and changed:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == cur[-1]:
                    cur += s[1:]
                elif s[-1] == cur[-1]:
                    cur += s[::-1][1:]
                elif s[-1] == cur[0]:
                    cur = s[:-1] + cur
                elif s[0] == cur[0]:
                    cur = s[::-1][:-1] + cur
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if len(cur) >= 4 and cur[0] == cur[-1]:
            rings.append(cur[:-1])
    return rings


def simplify(pts, tol=0.25):
    """Drop near-collinear and duplicate vertices (Douglas-Peucker light)."""
    out = []
    for p in pts:
        if out and abs(p[0] - out[-1][0]) < 0.05 and abs(p[1] - out[-1][1]) < 0.05:
            continue
        out.append(p)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            ab = math.hypot(b[0] - a[0], b[1] - a[1])
            ac = math.hypot(c[0] - a[0], c[1] - a[1])
            if ac < 1e-6:
                continue
            d = abs((c[0] - a[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (c[1] - a[1])) / ac
            if d < tol and ab < ac:
                out.pop(i)
                changed = True
                break
    return out


# ------------------------------------------------------------------- terrain

def load_srtm(cache: Path):
    """Mosaic of the four 1 arc-second tiles: N40-41, E028-029 -> 2 x 2 deg."""
    n = 3601
    mos = np.zeros((2 * (n - 1) + 1, 2 * (n - 1) + 1), np.float32)
    for lat0, row in ((41, 0), (40, n - 1)):
        for lon0, col in ((28, 0), (29, n - 1)):
            raw = np.fromfile(cache / f"N{lat0}E{lon0:03d}.hgt", ">i2").reshape(n, n)
            mos[row:row + n, col:col + n] = raw.astype(np.float32)
    mos[mos < -500] = 0.0
    # Pixel (r, c) is lat 42 - r/3600, lon 28 + c/3600.
    return mos


def sample(grid: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    return ndimage.map_coordinates(grid, [rows, cols], order=1, mode="nearest")


def bake_terrain(cache: Path, water: np.ndarray, wmeta: dict):
    srtm = load_srtm(cache)
    e0, n0, e1, n1 = WIDE
    w = int(round((e1 - e0) / TERRAIN_RES))
    h = int(round((n1 - n0) / TERRAIN_RES))
    es = e0 + (np.arange(w) + 0.5) * TERRAIN_RES
    ns = n1 - (np.arange(h) + 0.5) * TERRAIN_RES
    E, N = np.meshgrid(es, ns)
    lon = DATUM_LON + E / M_PER_DEG_LON
    lat = DATUM_LAT + N / M_PER_DEG_LAT
    hgt = sample(srtm, (42.0 - lat) * 3600.0, (lon - 28.0) * 3600.0)
    # SRTM is a surface model: in a dense city its "ground" is part roof. A
    # grey opening over ~120 m keeps the hills (hundreds of metres across) and
    # takes off the building blobs; a light blur then hides the 30 m posts.
    hgt = ndimage.grey_opening(hgt, size=(13, 13))
    hgt = ndimage.gaussian_filter(hgt, 2.0)
    # Water from the mask: sea level, and the shore steps down to it.
    wm = water_at(water, wmeta, E, N)
    hgt = np.where(wm, 0.0, np.maximum(hgt, 1.2))
    land = ~wm
    dist = ndimage.distance_transform_edt(land) * TERRAIN_RES
    # Within 25 m of the shore, ease down to a 1.2 m quay.
    ease = np.clip(dist / 25.0, 0.0, 1.0)
    hgt = np.where(land, 1.2 + (hgt - 1.2) * ease, hgt)
    meta = {"bounds_enu": list(WIDE), "res_m": TERRAIN_RES, "size_px": [w, h],
            "min_m": float(hgt.min()), "max_m": float(hgt.max()),
            "encoding": "zlib int16 decimetres, row 0 = north edge",
            "source": "NASA SRTM 1 arc-second (public domain), opened 120 m to strip buildings"}
    return hgt.astype(np.float32), meta


# --------------------------------------------------------------------- water

def water_at(water, wmeta, E, N):
    e0, n0, e1, n1 = wmeta["bounds_enu"]
    x = ((E - e0) / wmeta["res_m"]).astype(int).clip(0, water.shape[1] - 1)
    y = ((n1 - N) / wmeta["res_m"]).astype(int).clip(0, water.shape[0] - 1)
    return water[y, x]


def bake_water(osm: dict[str, dict]):
    """Coastline vote fill (see fetch_manhattan_water.py), plus OSM water areas."""
    e0, n0, e1, n1 = WIDE
    w = int(round((e1 - e0) / WATER_RES))
    h = int(round((n1 - n0) / WATER_RES))

    def px(p):
        return ((p[0] - e0) / WATER_RES, (n1 - p[1]) / WATER_RES)

    barrier = Image.new("L", (w, h), 0)
    bd = ImageDraw.Draw(barrier)
    vw = np.zeros((h, w), np.int32)
    vl = np.zeros((h, w), np.int32)
    ways = [way_pts(el) for el in osm["coast"]["elements"] if el["type"] == "way"]
    for way in ways:
        pts = [px(p) for p in way]
        if len(pts) < 2:
            continue
        bd.line(pts, fill=255, width=1)
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            dx, dy = x1 - x0, y1 - y0
            seg = math.hypot(dx, dy)
            if seg < 1e-6:
                continue
            rx, ry = -dy / seg, dx / seg
            for k in range(max(1, int(seg / 2.0))):
                t = (k + 0.5) / max(1, int(seg / 2.0))
                cx, cy = x0 + dx * t, y0 + dy * t
                for sgn, arr in ((1.0, vw), (-1.0, vl)):
                    qx, qy = int(cx + sgn * rx * 3), int(cy + sgn * ry * 3)
                    if 0 <= qx < w and 0 <= qy < h:
                        arr[qy, qx] += 1
    bar = np.asarray(barrier) > 0
    lab, nlab = ndimage.label(~bar)
    sw = np.bincount(lab.ravel(), weights=vw.ravel(), minlength=nlab + 1)
    sl = np.bincount(lab.ravel(), weights=vl.ravel(), minlength=nlab + 1)
    is_w = sw > sl
    is_w[0] = False
    water = is_w[lab]
    nb = ndimage.uniform_filter(water.astype(np.float32), 3)
    water[bar] = nb[bar] > 0.5
    lk = Image.new("L", (w, h), 0)
    ld = ImageDraw.Draw(lk)
    for el in osm["water"]["elements"]:
        for ring in element_rings(el):
            ld.polygon([px(p) for p in ring], fill=255)
    water |= np.asarray(lk) > 0
    meta = {"bounds_enu": list(WIDE), "res_m": WATER_RES, "size_px": [w, h],
            "note": "255 = water, row 0 = north edge",
            "source": "OpenStreetMap coastline and water areas, (c) OpenStreetMap contributors, ODbL 1.0"}
    return water, meta


# ----------------------------------------------------------------- buildings

def load_ghsl(cache: Path):
    import rasterio
    f = next(cache.glob("GHS_BUILT_H_ANBH_*R5_C21.tif"))
    s, w, n, e = geo_bbox(BOUNDS)
    with rasterio.open(f) as src:
        win = rasterio.windows.from_bounds(w - 0.01, s - 0.01, e + 0.01, n + 0.01, src.transform)
        arr = src.read(1, window=win).astype(np.float32)
        tr = src.window_transform(win)
    arr[arr < 0] = 0.0
    return arr, tr


def num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).split(";")[0].replace(",", ".").replace("m", "").strip())
    except ValueError:
        return None


def kind_of(t: dict) -> str:
    b = t.get("building", "yes")
    if b == "mosque" or t.get("religion") == "muslim" and t.get("amenity") == "place_of_worship":
        return "mosque"
    if b in ("church", "cathedral", "chapel", "synagogue") or t.get("amenity") == "place_of_worship":
        return "church"
    if b == "palace" or t.get("historic") in ("palace", "castle"):
        return "palace"
    if b in ("ruins",) or t.get("ruins") == "yes" or t.get("historic") == "ruins":
        return "ruin"
    if t.get("building:material") == "wood" or b == "wooden":
        return "wooden"
    if t.get("amenity") == "marketplace" or t.get("shop") == "mall" and t.get("historic") or \
            "çarşı" in t.get("name", "").lower() or "carsi" in t.get("name", "").lower() or \
            t.get("historic") == "caravanserai" or " han" in t.get("name", "").lower():
        return "bazaar"
    if t.get("historic") or t.get("heritage") or t.get("tourism") == "museum" or b in ("tomb", "mausoleum"):
        return "historic"
    if b in ("tower",) or t.get("man_made") == "tower":
        return "tower"
    if b in ("train_station", "transportation"):
        return "station"
    if b in ("roof", "kiosk", "garage", "garages", "shed", "hut", "carport", "toilets", "service"):
        return "small"
    if b in ("industrial", "warehouse", "factory", "hangar", "manufacture"):
        return "industrial"
    if b in ("school", "university", "hospital", "government", "public", "civic", "college",
             "kindergarten", "fire_station", "police"):
        return "public"
    if b in ("commercial", "retail", "office", "hotel", "supermarket"):
        return "commercial"
    return "residential"


def bake_buildings(osm: dict[str, dict], cache: Path, terrain, tmeta):
    ghsl, tr = load_ghsl(cache)
    inv = ~tr
    seen = set()
    blds = []
    names: list[str] = []
    name_idx: dict[str, int] = {}
    e0, n0, e1, n1 = BOUNDS
    rng = np.random.default_rng(1453)
    for i in range(4):
        for el in osm[f"buildings{i}"]["elements"]:
            key = (el["type"], el["id"])
            if key in seen:
                continue
            seen.add(key)
            t = el.get("tags", {})
            if t.get("building") in ("no", "construction", "proposed", "demolished"):
                continue
            for ring in element_rings(el):
                ring = simplify(ring)
                if len(ring) < 3:
                    continue
                a = ring_area(ring)
                if abs(a) < 6.0:
                    continue
                if a < 0:
                    ring = ring[::-1]      # counter-clockwise (ENU)
                cx = sum(p[0] for p in ring) / len(ring)
                cy = sum(p[1] for p in ring) / len(ring)
                if not (e0 <= cx <= e1 and n0 <= cy <= n1):
                    continue
                kind = kind_of(t)
                area = abs(a)
                lv = num(t.get("building:levels"))
                hh = num(t.get("height"))
                src = "osm_height"
                if hh is None and lv is not None:
                    hh = lv * 3.2 + (1.5 if kind != "small" else 0.3)
                    src = "osm_levels"
                if hh is None:
                    lon, lat = unproject(cx, cy)
                    col, row = inv * (lon, lat)
                    gh = float(ghsl[int(np.clip(row, 0, ghsl.shape[0] - 1)),
                                    int(np.clip(col, 0, ghsl.shape[1] - 1))])
                    # GHSL is a cell AVERAGE over built area; scale by size a
                    # little (a 40 m2 shed is not as tall as the 600 m2 block
                    # beside it) and vary it so streets are not a flat ridge.
                    base = gh if gh > 2.5 else 9.0
                    f = np.clip(0.75 + 0.12 * math.log10(max(area, 10.0) / 40.0), 0.6, 1.25)
                    hh = base * f * float(rng.uniform(0.85, 1.18))
                    src = "ghsl"
                if kind == "small":
                    hh = min(hh, 4.0)
                if kind == "mosque" and src == "ghsl":
                    hh = float(np.clip(math.sqrt(area) * 0.45, 6.0, 22.0))
                    src = "mosque_area"
                hh = float(np.clip(hh, 2.5, 260.0))
                minh = num(t.get("min_height")) or 0.0
                nm = t.get("name:en") or t.get("name") or ""
                ni = -1
                if nm:
                    if nm not in name_idx:
                        name_idx[nm] = len(names)
                        names.append(nm)
                    ni = name_idx[nm]
                flags = (1 if src.startswith("osm") else 0) | (2 if t.get("historic") or t.get("heritage") else 0) \
                    | (4 if t.get("roof:shape") in ("dome", "onion") else 0) \
                    | (8 if t.get("roof:shape") in ("hipped", "gabled", "pyramidal", "half-hipped") else 0) \
                    | (16 if t.get("roof:shape") == "flat" else 0)
                blds.append({"ring": ring, "h": hh, "min": minh, "kind": KINDS.index(kind),
                             "name": ni, "flags": flags, "osm": el["id"], "src": src,
                             "levels": int(lv) if lv else 0,
                             "colour": t.get("building:colour", ""), "roofc": t.get("roof:colour", "")})
    return blds, names


def write_buildings(out: Path, blds, names):
    """buildings.dat, little-endian:
         magic b"IST1", u32 count, u32 total_vertices, f32 Q
         per building (16 bytes): u32 first_vertex, u16 vertex_count,
             u16 height_dm, u16 min_height_dm, u8 kind, u8 flags, u8 levels,
             u8 colour_idx, u16 name_idx (65535 = none)
         vertices: i16 east/Q, i16 north/Q
       Rings are counter-clockwise, outer only, not closed."""
    colours: list[str] = [""]
    cidx = {"": 0}
    nv = sum(len(b["ring"]) for b in blds)
    hdr = bytearray(b"IST1") + struct.pack("<IIf", len(blds), nv, Q)
    rec = bytearray()
    verts = bytearray()
    first = 0
    for b in blds:
        c = (b["colour"] or "").lower()
        if c not in cidx and len(colours) < 255:
            cidx[c] = len(colours)
            colours.append(c)
        rec += struct.pack("<IHHHBBBBH", first, len(b["ring"]), min(65535, int(b["h"] * 10)),
                           max(0, min(65535, int(b["min"] * 10))), b["kind"], b["flags"],
                           max(0, min(255, b["levels"])), cidx.get(c, 0),
                           b["name"] if 0 <= b["name"] < 65535 else 65535)
        for x, y in b["ring"]:
            verts += struct.pack("<hh", int(round(x / Q)), int(round(y / Q)))
        first += len(b["ring"])
    (out / "buildings.dat").write_bytes(bytes(hdr + rec + verts))
    srcs: dict[str, int] = {}
    for b in blds:
        srcs[b["src"]] = srcs.get(b["src"], 0) + 1
    (out / "buildings.json").write_text(json.dumps({
        "count": len(blds), "vertices": nv, "q_m": Q, "kinds": KINDS, "names": names,
        "colours": colours, "height_sources": srcs,
        "flags": {"1": "height from OSM", "2": "historic/heritage", "4": "dome roof tagged",
                  "8": "pitched roof tagged", "16": "flat roof tagged"},
        "source": "OpenStreetMap buildings (ODbL 1.0); heights from OSM height/levels, "
                  "else JRC GHSL GHS-BUILT-H R2023A (CC BY 4.0)"}, ensure_ascii=False))


# ------------------------------------------------------------- land cover

def lc_class(t: dict) -> str | None:
    le, lu, na, am = t.get("leisure"), t.get("landuse"), t.get("natural"), t.get("amenity")
    if lu in ("cemetery",) or am == "grave_yard":
        return "cemetery"
    if le in ("park",) or lu in ("recreation_ground", "village_green", "grass", "meadow"):
        return "park"
    if le in ("garden",) or lu in ("allotments", "orchard", "plant_nursery", "vineyard"):
        return "garden"
    if lu == "forest" or na in ("wood", "scrub", "heath", "grassland"):
        return "wood"
    if le in ("pitch", "stadium", "sports_centre", "track"):
        return "pitch"
    if le == "playground":
        return "playground"
    if na in ("beach", "sand"):
        return "beach"
    if am == "parking":
        return "parking"
    if am in ("place_of_worship", "school", "university"):
        return "courtyard"
    if t.get("highway") == "pedestrian" or t.get("place") == "square" or t.get("area:highway"):
        return "plaza"
    if lu == "railway":
        return "railway"
    if lu in ("construction", "brownfield"):
        return "construction"
    return None


#: Draw order: big and coarse first, so small specific areas paint over them.
LC_ORDER = ["railway", "construction", "wood", "park", "garden", "cemetery", "courtyard",
            "parking", "plaza", "beach", "pitch", "playground"]


def bake_landcover(osm):
    e0, n0, e1, n1 = BOUNDS
    w = int(round((e1 - e0) / LANDCOVER_RES))
    h = int(round((n1 - n0) / LANDCOVER_RES))
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    polys = {c: [] for c in LC_ORDER}
    for el in osm["areas"]["elements"]:
        c = lc_class(el.get("tags", {}))
        if c is None:
            continue
        for ring in element_rings(el):
            polys[c].append([((x - e0) / LANDCOVER_RES, (n1 - y) / LANDCOVER_RES) for x, y in ring])
    for c in LC_ORDER:
        for p in polys[c]:
            if len(p) >= 3:
                d.polygon(p, fill=LANDCOVER.index(c))
    meta = {"bounds_enu": list(BOUNDS), "res_m": LANDCOVER_RES, "size_px": [w, h],
            "classes": LANDCOVER, "note": "row 0 = north edge",
            "counts": {c: len(polys[c]) for c in LC_ORDER},
            "source": "OpenStreetMap land use, leisure and natural areas, ODbL 1.0"}
    return img, meta


# --------------------------------------------------------------------- roads

ROAD_CLASS = {"motorway": "motorway", "trunk": "motorway", "motorway_link": "motorway_link",
              "trunk_link": "motorway_link", "primary": "primary", "primary_link": "primary",
              "secondary": "secondary", "secondary_link": "secondary", "tertiary": "secondary",
              "tertiary_link": "secondary", "residential": "street", "unclassified": "street",
              "living_street": "street", "road": "street", "service": "service",
              "pedestrian": "pedestrian", "footway": "path", "path": "path", "steps": "steps",
              "cycleway": "path", "track": "service", "corridor": None, "construction": None,
              "proposed": None, "platform": None, "bus_stop": None, "elevator": None}
DEFAULT_LANES = {"motorway": 3, "motorway_link": 1, "primary": 2, "secondary": 2, "street": 1,
                 "service": 1, "pedestrian": 0, "path": 0, "steps": 0}
DEFAULT_WIDTH = {"motorway": 22.0, "motorway_link": 7.0, "primary": 14.0, "secondary": 10.0,
                 "street": 6.5, "service": 4.0, "pedestrian": 8.0, "path": 2.5, "steps": 3.0}


def bake_roads(osm):
    nodes: list[list[float]] = []
    nid: dict[int, int] = {}
    names: list[str] = []
    name_idx: dict[str, int] = {}
    edges = []
    for el in osm["roads"]["elements"]:
        if el["type"] != "way":
            continue
        t = el.get("tags", {})
        if t.get("area") == "yes":
            continue
        cls = ROAD_CLASS.get(t.get("highway", ""), "street" if t.get("highway") else None)
        if cls is None:
            continue
        geom = el.get("geometry") or []
        ids = el.get("nodes") or []
        if len(geom) != len(ids) or len(ids) < 2:
            continue
        idx = []
        for nd, g in zip(ids, geom):
            if nd not in nid:
                nid[nd] = len(nodes)
                e, n = project(g["lon"], g["lat"])
                nodes.append([round(e, 1), round(n, 1)])
            idx.append(nid[nd])
        ow = t.get("oneway")
        oneway = 1 if ow in ("yes", "1", "true", "-1") or t.get("junction") == "roundabout" else 0
        if ow == "-1":
            idx = idx[::-1]
        lanes = int(num(t.get("lanes")) or 0) or DEFAULT_LANES[cls]
        width = num(t.get("width")) or DEFAULT_WIDTH[cls] * (1.0 if lanes <= DEFAULT_LANES[cls] or cls in ("path", "steps", "pedestrian")
                                                          else lanes / max(1, DEFAULT_LANES[cls]))
        nm = t.get("name") or ""
        if nm not in name_idx:
            name_idx[nm] = len(names)
            names.append(nm)
        layer = int(num(t.get("layer")) or 0)
        surface = t.get("surface", "")
        flags = ((1 if t.get("bridge") not in (None, "no") else 0)
                 | (2 if t.get("tunnel") not in (None, "no") else 0)
                 | (4 if layer > 0 else 0) | (8 if layer < 0 else 0)
                 | (16 if surface in ("sett", "cobblestone", "unhewn_cobblestone", "paving_stones", "stone") else 0)
                 | (32 if t.get("highway") == "steps" else 0))
        for a, b in zip(idx, idx[1:]):
            if a != b:
                edges.append([a, b, cls, oneway, lanes, round(width, 1), 0, name_idx[nm], flags])
    rails = []
    ferries = []
    piers = []
    for el in osm["rail"]["elements"]:
        if el["type"] != "way":
            continue
        t = el.get("tags", {})
        pts = [[round(e, 1), round(n, 1)] for e, n in way_pts(el)]
        if len(pts) < 2:
            continue
        if t.get("route") == "ferry":
            ferries.append(pts)
        elif t.get("man_made") == "pier":
            piers.append({"pts": pts, "closed": pts[0] == pts[-1], "name": t.get("name", "")})
        else:
            rails.append({"type": t.get("railway"), "pts": pts,
                          "tunnel": t.get("tunnel") not in (None, "no"),
                          "bridge": t.get("bridge") not in (None, "no"),
                          "name": t.get("name", "")})
    return {"frame": "ENU metres, datum Hagia Sophia 41.0086 N 28.9802 E, no rotation",
            "source": "OpenStreetMap highways, railways, ferry routes and piers (ODbL 1.0)",
            "edges_fields": ["a", "b", "class", "oneway", "lanes", "width_m", "unused", "name_idx", "flags"],
            "flags": {"1": "bridge", "2": "tunnel", "4": "layer > 0", "8": "layer < 0",
                      "16": "stone paving", "32": "steps"},
            "nodes": nodes, "edges": edges, "names": names,
            "rails": rails, "ferries": ferries, "piers": piers}


# ------------------------------------------------------------------ features

def bake_features(osm):
    walls, towers, minarets, aqueducts, landmarks, parts, trees = [], [], [], [], [], [], []
    for el in osm["features"]["elements"]:
        t = el.get("tags", {})
        nm = t.get("name:en") or t.get("name") or ""
        if el["type"] == "node":
            p = [round(v, 2) for v in project(el["lon"], el["lat"])]
            if t.get("tower:type") == "minaret":
                minarets.append({"p": p, "h": num(t.get("height"))})
            elif t.get("man_made") == "tower":
                towers.append({"p": p, "h": num(t.get("height")), "type": t.get("tower:type", ""), "name": nm})
            elif t.get("natural") == "tree":
                trees.append(p)
            elif nm and (t.get("historic") or t.get("tourism") == "attraction"):
                landmarks.append({"p": p, "name": nm, "historic": t.get("historic", ""),
                                  "wikidata": t.get("wikidata", "")})
            continue
        rings = element_rings(el)
        line = [[round(e, 2), round(n, 2)] for e, n in way_pts(el)] if el["type"] == "way" else []
        if t.get("barrier") == "city_wall" or t.get("historic") == "citywalls":
            if line:
                walls.append({"pts": line, "h": num(t.get("height")), "name": nm})
            continue
        if t.get("tower:type") == "minaret":
            for r in rings:
                cx = sum(p[0] for p in r) / len(r)
                cy = sum(p[1] for p in r) / len(r)
                rad = math.sqrt(abs(ring_area(r)) / math.pi)
                minarets.append({"p": [round(cx, 2), round(cy, 2)], "h": num(t.get("height")), "r": round(rad, 2)})
            continue
        if t.get("man_made") == "aqueduct" or t.get("bridge") == "aqueduct":
            if line:
                aqueducts.append({"pts": line, "h": num(t.get("height")), "name": nm})
            continue
        if t.get("building:part"):
            for r in rings:
                parts.append({"ring": [[round(e, 2), round(n, 2)] for e, n in r],
                              "part": t.get("building:part"), "h": num(t.get("height")),
                              "min": num(t.get("min_height")), "roof": t.get("roof:shape", ""),
                              "roof_h": num(t.get("roof:height")), "levels": num(t.get("building:levels")),
                              "colour": t.get("building:colour", ""), "name": nm})
            continue
        if t.get("man_made") == "tower":
            for r in rings:
                cx = sum(p[0] for p in r) / len(r)
                cy = sum(p[1] for p in r) / len(r)
                towers.append({"p": [round(cx, 2), round(cy, 2)], "h": num(t.get("height")),
                               "type": t.get("tower:type", ""), "name": nm,
                               "r": round(math.sqrt(abs(ring_area(r)) / math.pi), 2)})
            continue
        if nm and rings and (t.get("historic") or t.get("tourism") == "attraction"):
            r = rings[0]
            landmarks.append({"p": [round(sum(p[0] for p in r) / len(r), 2), round(sum(p[1] for p in r) / len(r), 2)],
                              "name": nm, "historic": t.get("historic", ""), "wikidata": t.get("wikidata", ""),
                              "ring": [[round(e, 2), round(n, 2)] for e, n in r]})
    return {"source": "OpenStreetMap (ODbL 1.0)", "walls": walls, "towers": towers,
            "minarets": minarets, "aqueducts": aqueducts, "landmarks": landmarks,
            "building_parts": parts, "trees": trees}


# ---------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent
                                          / "godot" / "assets" / "istanbul"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    cache = out / "_cache"
    cache.mkdir(parents=True, exist_ok=True)
    osm = fetch_osm(cache)

    water, wmeta = bake_water(osm)
    Image.fromarray((water * 255).astype(np.uint8)).save(out / "water.png", optimize=True)
    (out / "water.json").write_text(json.dumps(wmeta, indent=1))

    terrain, tmeta = bake_terrain(cache, water, wmeta)
    dm = np.round(terrain * 10.0).astype("<i2")
    (out / "terrain.zi16").write_bytes(zlib.compress(dm.tobytes(), 9))
    (out / "terrain.json").write_text(json.dumps(tmeta, indent=1))

    blds, names = bake_buildings(osm, cache, terrain, tmeta)
    write_buildings(out, blds, names)

    lc, lmeta = bake_landcover(osm)
    lc.save(out / "landcover.png", optimize=True)
    (out / "landcover.json").write_text(json.dumps(lmeta, indent=1))

    (out / "roads.json").write_text(json.dumps(bake_roads(osm), separators=(",", ":"), ensure_ascii=False))
    (out / "features.json").write_text(json.dumps(bake_features(osm), separators=(",", ":"), ensure_ascii=False))

    def at(lat, lon):
        e, n = project(lon, lat)
        return bool(water_at(water, wmeta, np.array([e]), np.array([n]))[0])
    checks = {"hagia sophia land": not at(41.0086, 28.9802), "bosphorus water": at(41.030, 29.005),
              "golden horn water": at(41.025, 28.965), "marmara water": at(40.995, 28.955),
              "galata land": not at(41.0256, 28.9741), "uskudar land": not at(41.026, 29.016),
              "kadikoy land": not at(40.990, 29.027), "maidens tower water nearby": at(41.0215, 29.0005)}
    hz = lambda lat, lon: float(terrain[int((WIDE[3] - project(lon, lat)[1]) / TERRAIN_RES),
                                        int((project(lon, lat)[0] - WIDE[0]) / TERRAIN_RES)])
    print(json.dumps({"buildings": len(blds), "terrain_m": [tmeta["min_m"], tmeta["max_m"]],
                      "suleymaniye_ground_m": round(hz(41.0162, 28.9640), 1),
                      "hagia_sophia_ground_m": round(hz(41.0086, 28.9802), 1),
                      "eminonu_ground_m": round(hz(41.0170, 28.9710), 1),
                      "checks": checks}, indent=1))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
