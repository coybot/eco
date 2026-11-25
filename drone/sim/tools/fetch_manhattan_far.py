#!/usr/bin/env python3
"""Bake the far shores round Manhattan: Brooklyn, Queens, the Bronx and the
New Jersey waterfront, as oriented boxes for the photoreal scene's skyline.

Sources:
  * NYC Open Data "Building Footprints" (5zhs-2jue), public domain, the same
    dataset as buildings.json. BBL's first digit is the borough: 2 Bronx,
    3 Brooklyn, 4 Queens. height_roof is US FEET; converted to metres here.
  * OpenStreetMap buildings in New Jersey (Jersey City, Hoboken, Weehawken,
    West New York, Guttenberg, North Bergen, Edgewater, Fort Lee, Cliffside
    Park), via the Overpass API. (c) OpenStreetMap contributors, ODbL 1.0.
    Heights from `height`, else `building:levels`, else a default by kind.

Outputs, in the DATASET frame of buildings.json (fetch_manhattan.py project()):
  assets/manhattan/far_nyc.json   public domain (NYC Open Data)
  assets/manhattan/far_nj.json    ODbL 1.0 (derived from OpenStreetMap)
Kept as two files so each carries one licence.

  {"fields": ["cx", "cy", "w", "d", "yaw", "h"], "b": [flat numbers ...]}
  cx, cy  box centre, m; w along the box's own x after turning by yaw, d across
  yaw     degrees, 0..90, the box's turn from the dataset frame's east axis
  h       roof height above the ground, m

Oriented boxes, not the axis-aligned ones buildings.json uses: Brooklyn's and
Queens' grids are not Manhattan's 29 deg grid, so an axis-aligned box of a
Brooklyn rowhouse block is up to twice its real area and the borough reads as
a field of cubes. These buildings are visual only (outside the occupancy grid),
so nothing needs them axis-aligned.

Which buildings: every building 20 m and up (the skylines: Downtown Brooklyn,
Long Island City, Jersey City). Lower ones only within NEAR_M of the shore
facing Manhattan, where a camera over the island can see them; behind that
the ground imagery carries the low city, and a borough of rowhouses would be
hundreds of thousands of boxes nobody sees past the first row.

Caches every page under assets/manhattan/_cache/far/ and resumes from it.

    python3 fetch_manhattan_far.py            # fetch (or reuse cache) and bake
    python3 fetch_manhattan_far.py --bake     # bake from the cache only
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "godot" / "assets" / "manhattan"
CACHE = ASSETS / "_cache" / "far"

NYC = "https://data.cityofnewyork.us/resource/5zhs-2jue.json"
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
UA = "eco-sim/1.0 (drone simulator; far shores)"
FT_TO_M = 0.3048
PAGE = 20000

DATUM_LAT = 40.7005
DATUM_LON = -74.0170
GRID_ROTATION_DEG = 29.0
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))

#: The water mask's extent (water.json), dataset frame. Far scenery past it
#: would stand on "open water" (is_water() is true outside the mask).
EXTENT = (-8000.0, -5000.0, 8000.0, 27000.0)

TALL_M = 20.0        # always kept
NEAR_M = 1500.0      # lower ones kept within this of the shore facing the island
MIN_H_M = 4.0        # below this: sheds, garages, kiosks
MIN_SIDE_M = 3.0


def project(lon: float, lat: float) -> tuple[float, float]:
    x = (lon - DATUM_LON) * M_PER_DEG_LON
    y = (lat - DATUM_LAT) * M_PER_DEG_LAT
    t = math.radians(GRID_ROTATION_DEG)
    return (x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t))


def unproject(e: float, n: float) -> tuple[float, float]:
    """Dataset (e, n) -> (lat, lon)."""
    t = math.radians(GRID_ROTATION_DEG)
    x = e * math.cos(t) + n * math.sin(t)
    y = -e * math.sin(t) + n * math.cos(t)
    return (DATUM_LAT + y / M_PER_DEG_LAT, DATUM_LON + x / M_PER_DEG_LON)


def extent_wkt() -> str:
    e0, n0, e1, n1 = EXTENT
    ring = [unproject(e, n) for e, n in [(e0, n0), (e1, n0), (e1, n1), (e0, n1), (e0, n0)]]
    return "MULTIPOLYGON(((" + ",".join(f"{lon:.6f} {lat:.6f}" for lat, lon in ring) + ")))"


def get(url: str, data: bytes | None = None, tries: int = 8) -> bytes:
    wait = 20.0
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=400) as r:
                return r.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as ex:
            # Overpass answers 429 / 504 when busy: back off, do not hammer it.
            print(f"    {ex}; retry in {wait:.0f} s", file=sys.stderr)
            time.sleep(wait)
            wait = min(wait * 1.8, 300.0)
    raise RuntimeError(f"gave up on {url}")


# ------------------------------------------------------------------ NYC

def fetch_nyc() -> None:
    d = CACHE / "nyc"
    d.mkdir(parents=True, exist_ok=True)
    where0 = (f"within_polygon(the_geom, '{extent_wkt()}')"
              " AND (starts_with(base_bbl, '2') OR starts_with(base_bbl, '3')"
              " OR starts_with(base_bbl, '4'))"
              f" AND height_roof > {MIN_H_M / FT_TO_M:.0f}")
    # Keyset paging on :id, not $offset: offsets deep into ~300k rows get
    # slower with every page and Socrata times them out.
    last = ""
    k = 0
    while True:
        f = d / f"page_{k:04d}.json"
        if f.exists():
            page = json.loads(f.read_text())
        else:
            where = where0 + (f" AND :id > '{last}'" if last else "")
            q = urllib.parse.urlencode({
                "$select": ":id,base_bbl,height_roof,ground_elevation,"
                           "simplify_preserve_topology(the_geom, 0.00001) as g",
                "$where": where, "$order": ":id", "$limit": PAGE})
            page = json.loads(get(f"{NYC}?{q}"))
            f.write_text(json.dumps(page))
            print(f"  nyc page {k}: {len(page)}", file=sys.stderr)
        if not page:
            break
        last = page[-1][":id"]
        k += 1
        if len(page) < PAGE:
            break


def nyc_records():
    for f in sorted((CACHE / "nyc").glob("page_*.json")):
        for rec in json.loads(f.read_text()):
            try:
                h = float(rec.get("height_roof") or 0.0) * FT_TO_M
            except ValueError:
                continue
            g = rec.get("g") or {}
            polys = g.get("coordinates") or []
            if g.get("type") == "Polygon":
                polys = [polys]
            for poly in polys:
                if poly:
                    yield poly[0], h, "nyc"


# ------------------------------------------------------------------ NJ (OSM)

#: lat0, lon0, lat1, lon1 of the New Jersey shore inside EXTENT, in tiles
#: small enough that a busy Overpass still answers each one.
NJ_BOX = (40.680, -74.130, 40.960, -73.910)
NJ_TILE = 0.035


def fetch_nj() -> None:
    d = CACHE / "nj"
    d.mkdir(parents=True, exist_ok=True)
    lat0, lon0, lat1, lon1 = NJ_BOX
    tiles = []
    la = lat0
    while la < lat1:
        lo = lon0
        while lo < lon1:
            tiles.append((round(la, 4), round(lo, 4),
                          round(min(la + NJ_TILE, lat1), 4), round(min(lo + NJ_TILE, lon1), 4)))
            lo += NJ_TILE
        la += NJ_TILE
    for i, (s, w, n, e) in enumerate(tiles):
        # Skip tiles wholly outside the extent.
        corners = [project(lo, la) for la in (s, n) for lo in (w, e)]
        if (max(c[0] for c in corners) < EXTENT[0] or min(c[0] for c in corners) > EXTENT[2]
                or max(c[1] for c in corners) < EXTENT[1] or min(c[1] for c in corners) > EXTENT[3]):
            continue
        f = d / f"tile_{s:.4f}_{w:.4f}.json"
        if f.exists():
            continue
        # Inside the state's boundary, so New York's own buildings (already
        # in the city's dataset) and Staten Island never come in twice.
        q = (f'[out:json][timeout:300];area["ISO3166-2"="US-NJ"][admin_level=4]->.nj;'
             f'(way["building"](area.nj)({s},{w},{n},{e});'
             f'relation["building"](area.nj)({s},{w},{n},{e}););out tags geom;')
        body = None
        for url in OVERPASS:
            try:
                body = get(url, urllib.parse.urlencode({"data": q}).encode(), tries=5)
                break
            except RuntimeError:
                continue
        if body is None:
            raise RuntimeError("overpass unavailable")
        f.write_bytes(body)
        n_el = len(json.loads(body).get("elements", []))
        print(f"  nj tile {i + 1}/{len(tiles)} {s},{w}: {n_el}", file=sys.stderr)
        time.sleep(3.0)


def _num(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().lower().replace(",", ".")
    ft = s.endswith("ft") or s.endswith("'")
    s = s.rstrip("ft' m").strip()
    try:
        v = float(s.split(";")[0])
    except ValueError:
        return None
    return v * FT_TO_M if ft else v


#: Height when OSM has neither height nor levels: what that kind usually is
#: on this shore (Hudson County is two- and three-storey frame houses behind
#: a waterfront of towers; the towers nearly all carry levels).
DEFAULT_H = {"house": 8.0, "detached": 8.0, "semidetached_house": 8.0, "terrace": 10.0,
             "residential": 10.0, "apartments": 14.0, "commercial": 10.0, "retail": 6.0,
             "industrial": 9.0, "warehouse": 9.0, "office": 15.0, "school": 12.0,
             "church": 14.0, "hospital": 18.0, "hotel": 20.0, "parking": 12.0,
             "train_station": 10.0, "yes": 8.0}
SKIP = {"garage", "garages", "shed", "roof", "carport", "hut", "kiosk", "construction",
        "ruins", "bridge", "container"}


def osm_height(tags: dict) -> float | None:
    kind = tags.get("building", "yes")
    if kind in SKIP:
        return None
    h = _num(tags.get("height"))
    if h is None:
        lv = _num(tags.get("building:levels"))
        if lv is not None:
            h = lv * 3.2 + 1.5 + (_num(tags.get("roof:levels")) or 0.0) * 2.5
    if h is None:
        h = DEFAULT_H.get(kind, 8.0)
    return h


def nj_records():
    seen = set()
    for f in sorted((CACHE / "nj").glob("tile_*.json")):
        for el in json.loads(f.read_text()).get("elements", []):
            key = (el["type"], el["id"])
            if key in seen:      # a building across a tile edge comes twice
                continue
            seen.add(key)
            h = osm_height(el.get("tags", {}))
            if h is None:
                continue
            if el["type"] == "way":
                ring = [[p["lon"], p["lat"]] for p in el.get("geometry", [])]
                if len(ring) >= 3:
                    yield ring, h, "nj"
            else:
                for m in el.get("members", []):
                    if m.get("role") == "outer" and m.get("geometry"):
                        ring = [[p["lon"], p["lat"]] for p in m["geometry"]]
                        if len(ring) >= 3:
                            yield ring, h, "nj"


# ------------------------------------------------------------------ bake

def hull(pts: np.ndarray) -> np.ndarray:
    """Convex hull (monotone chain), counter-clockwise."""
    p = np.unique(pts, axis=0)
    if len(p) < 3:
        return p
    p = p[np.lexsort((p[:, 1], p[:, 0]))]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for q in p:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    for q in p[::-1]:
        while len(hi) >= 2 and cross(hi[-2], hi[-1], q) <= 0:
            hi.pop()
        hi.append(q)
    return np.array(lo[:-1] + hi[:-1])


def min_rect(pts: np.ndarray):
    """Minimum-area enclosing rectangle: (cx, cy, w, d, yaw_deg in [0, 90))."""
    h = hull(pts)
    if len(h) < 3:
        return None
    edges = np.roll(h, -1, axis=0) - h
    ang = np.mod(np.arctan2(edges[:, 1], edges[:, 0]), math.pi / 2)
    best = None
    for a in np.unique(np.round(ang, 4)):
        c, s = math.cos(a), math.sin(a)
        # Into the box's frame: rotate by -a.
        u = h[:, 0] * c + h[:, 1] * s
        v = -h[:, 0] * s + h[:, 1] * c
        area = (u.max() - u.min()) * (v.max() - v.min())
        if best is None or area < best[0]:
            uc = (u.max() + u.min()) * 0.5
            vc = (v.max() + v.min()) * 0.5
            best = (area, uc * c - vc * s, uc * s + vc * c,
                    u.max() - u.min(), v.max() - v.min(), math.degrees(a))
    return best[1:]


def shore_distance_fn():
    """Distance (m) from a dataset point to Manhattan's nearest building, on a
    coarse grid: "near the shore facing the island" without a coastline."""
    doc = json.loads((ASSETS / "buildings.json").read_text())
    pts = np.array([[b["r"][0] + b["r"][2] * 0.5, b["r"][1] + b["r"][3] * 0.5]
                    for b in doc["buildings"]])
    cell = 100.0
    e0, n0, e1, n1 = EXTENT
    gw, gh = int((e1 - e0) / cell), int((n1 - n0) / cell)
    occ = np.zeros((gh, gw), bool)
    ix = np.clip(((pts[:, 0] - e0) / cell).astype(int), 0, gw - 1)
    iy = np.clip(((pts[:, 1] - n0) / cell).astype(int), 0, gh - 1)
    occ[iy, ix] = True
    # Brute-force distance transform over the occupied cells (a few thousand).
    oy, ox = np.nonzero(occ)
    gy, gx = np.mgrid[0:gh, 0:gw]
    dist = np.full((gh, gw), 1e9)
    for k in range(0, len(ox), 512):
        dx = gx[..., None] - ox[None, None, k:k + 512]
        dy = gy[..., None] - oy[None, None, k:k + 512]
        dist = np.minimum(dist, np.sqrt(dx * dx + dy * dy).min(axis=2) * cell)

    def f(e: float, n: float) -> float:
        x = min(max(int((e - e0) / cell), 0), gw - 1)
        y = min(max(int((n - n0) / cell), 0), gh - 1)
        return float(dist[y, x])
    return f


def bake(records, near) -> list[float]:
    out: list[float] = []
    e0, n0, e1, n1 = EXTENT
    kept = 0
    for ring, h, _src in records:
        if h < MIN_H_M:
            continue
        pts = np.array([project(float(p[0]), float(p[1])) for p in ring])
        c = pts.mean(axis=0)
        if not (e0 <= c[0] <= e1 and n0 <= c[1] <= n1):
            continue
        if h < TALL_M and near(c[0], c[1]) > NEAR_M:
            continue
        r = min_rect(pts)
        if r is None:
            continue
        cx, cy, w, d, yaw = r
        if w < MIN_SIDE_M or d < MIN_SIDE_M:
            continue
        out += [round(cx, 1), round(cy, 1), round(w, 1), round(d, 1), round(yaw, 1), round(h, 1)]
        kept += 1
    return out


def write(name: str, flat: list[float], source: str, licence: str) -> None:
    doc = {"source": source, "licence": licence,
           "frame": "dataset frame of buildings.json (fetch_manhattan.py project())",
           "fields": ["cx", "cy", "w", "d", "yaw", "h"], "count": len(flat) // 6, "b": flat}
    p = ASSETS / name
    p.write_text(json.dumps(doc, separators=(",", ":")))
    hs = flat[5::6]
    print(f"{name}: {len(hs)} buildings, {sum(1 for h in hs if h >= TALL_M)} >= {TALL_M:.0f} m, "
          f"tallest {max(hs):.0f} m, {p.stat().st_size / 1e6:.1f} MB", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bake", action="store_true", help="bake from the cache only")
    ap.add_argument("--skip-nj", action="store_true")
    a = ap.parse_args()
    if not a.bake:
        print("fetching NYC footprints ...", file=sys.stderr)
        fetch_nyc()
        if not a.skip_nj:
            print("fetching NJ buildings (OSM) ...", file=sys.stderr)
            fetch_nj()
    near = shore_distance_fn()
    write("far_nyc.json", bake(nyc_records(), near),
          "NYC Open Data 5zhs-2jue (Building Footprints), boroughs 2-4", "public domain")
    if (CACHE / "nj").exists():
        write("far_nj.json", bake(nj_records(), near),
              "OpenStreetMap buildings, New Jersey, via Overpass",
              "(c) OpenStreetMap contributors, ODbL 1.0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
