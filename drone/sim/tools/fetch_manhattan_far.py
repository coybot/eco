#!/usr/bin/env python3
"""Bake the far shores round Manhattan: Brooklyn, Queens, the Bronx and the
New Jersey waterfront, as oriented boxes for the photoreal scene's skyline.

Sources:
  * NYC Open Data "Building Footprints" (5zhs-2jue), public domain, the same
    dataset as buildings.json. BBL's first digit is the borough: 2 Bronx,
    3 Brooklyn, 4 Queens. height_roof is US FEET; converted to metres here.
  * Overture Maps buildings in New Jersey (Jersey City, Hoboken, Weehawken,
    West New York, Guttenberg, North Bergen, Edgewater, Fort Lee, Cliffside
    Park), from Overture's public GeoParquet with DuckDB. ODbL 1.0:
    (c) OpenStreetMap contributors, Overture Maps Foundation; the source
    datasets used are listed in far_nj.json. Heights from `height`, else
    `num_floors`, else a default by class.

Outputs, in the DATASET frame of buildings.json (fetch_manhattan.py project()):
  assets/manhattan/far_nyc.json   public domain (NYC Open Data)
  assets/manhattan/far_nj.json    ODbL 1.0 (Overture Maps / OpenStreetMap)
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
Long Island City, Jersey City). Lower ones only within NEAR_M of Manhattan,
where a camera over the island can see them; behind that the ground imagery
carries the low city, and a borough of rowhouses would be
hundreds of thousands of boxes nobody sees past the first row.

Caches every page under assets/manhattan/_cache/far/ and resumes from it.

    uv run --with numpy --with duckdb fetch_manhattan_far.py   # fetch (or reuse cache), bake
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
#: Lower ones are kept within this of the island's buildings: the East and
#: Harlem rivers are 0.2-1 km wide, the Hudson 1.3-1.5 km, so New Jersey's
#: waterfront needs the longer reach to get past its first row.
NEAR_M = {"nyc": 1500.0, "nj": 2600.0}
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


# ------------------------------------------------------------------ NJ (Overture)

#: Overture Maps' buildings theme, public GeoParquet on S3 (no account).
#: Overture publishes monthly; a newer release works the same.
OVERTURE = ("s3://overturemaps-us-west-2/release/2026-11-18.0/"
            "theme=buildings/type=building/*")
#: lon0, lat0, lon1, lat1 round the New Jersey shore inside EXTENT.
NJ_BBOX = (-74.135, 40.655, -73.900, 40.965)

#: The state line down the middle of the Hudson and the Upper Bay, south to
#: north as (lat, lon): New Jersey is west of it. Mid-river is good enough
#: (the river is 1.2 km and more wide); it passes west of Ellis and Liberty
#: islands, which are New York's.
STATE_LINE = [(40.640, -74.082), (40.680, -74.062), (40.690, -74.053), (40.705, -74.046),
              (40.715, -74.028), (40.730, -74.020), (40.755, -74.014), (40.775, -74.004),
              (40.800, -73.986), (40.825, -73.970), (40.852, -73.956), (40.880, -73.939),
              (40.920, -73.923), (40.970, -73.910)]


def in_nj(lon: float, lat: float) -> bool:
    pts = STATE_LINE
    if lat <= pts[0][0]:
        return lon < pts[0][1]
    for (la0, lo0), (la1, lo1) in zip(pts, pts[1:]):
        if lat <= la1:
            return lon < lo0 + (lo1 - lo0) * (lat - la0) / (la1 - la0)
    return lon < pts[-1][1]


def fetch_nj() -> None:
    """Query Overture with DuckDB (pip/uv: duckdb) straight from S3; ~20 s.

    OpenStreetMap through Overpass was the first source tried: the public
    Overpass servers answered 502/504 for most tiles, 23 of 63 tiles took
    over an hour, and OSM has a height or level count for few of Hudson
    County's buildings. Overture carries a height for ~70 % of them (from
    OSM and its other sources) and its GeoParquet is fetched in one query.
    """
    f = CACHE / "nj_overture.json"
    if f.exists():
        return
    import duckdb  # only needed to fetch; the bake reads the cache
    CACHE.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    lo0, la0, lo1, la1 = NJ_BBOX
    rows = con.execute(f"""
        SELECT height, num_floors, class, sources[1].dataset AS src, geometry
        FROM read_parquet('{OVERTURE}', hive_partitioning=1)
        WHERE bbox.xmin > {lo0} AND bbox.xmax < {lo1} AND bbox.ymin > {la0} AND bbox.ymax < {la1}
    """).fetchall()
    out = []
    for h, floors, cls, src, wkb in rows:
        for ring in wkb_outer_rings(bytes(wkb)):
            c = ring[len(ring) // 2]
            if not in_nj(c[0], c[1]):
                continue
            out.append({"r": [[round(x, 7), round(y, 7)] for x, y in ring],
                        "h": h, "f": floors, "c": cls, "s": src})
    f.write_text(json.dumps(out))
    print(f"  overture: {len(rows)} in the box, {len(out)} in New Jersey", file=sys.stderr)


def wkb_outer_rings(b: bytes):
    """Outer rings of a WKB Polygon / MultiPolygon, as [(lon, lat), ...]."""
    import struct

    def poly(off: int):
        e = "<" if b[off] == 1 else ">"
        typ = struct.unpack_from(e + "I", b, off + 1)[0] % 1000
        off += 5
        if typ == 3:
            nr = struct.unpack_from(e + "I", b, off)[0]
            off += 4
            outer = None
            for r in range(nr):
                n = struct.unpack_from(e + "I", b, off)[0]
                off += 4
                pts = struct.unpack_from(e + "%dd" % (2 * n), b, off)
                off += 16 * n
                if r == 0:
                    outer = list(zip(pts[0::2], pts[1::2]))
            return [outer] if outer else [], off
        if typ == 6:
            n = struct.unpack_from(e + "I", b, off)[0]
            off += 4
            rings = []
            for _ in range(n):
                rr, off = poly(off)
                rings += rr
            return rings, off
        return [], off
    return poly(0)[0]


#: Height when Overture has neither a height nor a floor count: what that
#: kind usually is on this shore (Hudson County is two- and three-storey
#: frame houses behind a waterfront of towers, and the towers nearly all
#: carry a height).
DEFAULT_H = {"house": 8.0, "detached": 8.0, "semidetached_house": 8.0, "terrace": 10.0,
             "residential": 10.0, "apartments": 14.0, "commercial": 10.0, "retail": 6.0,
             "industrial": 9.0, "warehouse": 9.0, "office": 15.0, "school": 12.0,
             "church": 14.0, "hospital": 18.0, "hotel": 20.0, "parking": 12.0,
             "train_station": 10.0}
SKIP = {"garage", "garages", "shed", "roof", "carport", "hut", "kiosk", "construction",
        "ruins", "bridge", "container"}


def nj_records():
    f = CACHE / "nj_overture.json"
    if not f.exists():
        return
    for rec in json.loads(f.read_text()):
        cls = rec.get("c") or ""
        if cls in SKIP:
            continue
        h = rec.get("h")
        if h is None and rec.get("f"):
            h = float(rec["f"]) * 3.2 + 1.5
        if h is None:
            h = DEFAULT_H.get(cls, 8.0)
        yield rec["r"], float(h), "nj"


def nj_sources() -> list[str]:
    f = CACHE / "nj_overture.json"
    if not f.exists():
        return []
    return sorted({str(r.get("s")) for r in json.loads(f.read_text())})


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
    for ring, h, src in records:
        if h < MIN_H_M:
            continue
        pts = np.array([project(float(p[0]), float(p[1])) for p in ring])
        c = pts.mean(axis=0)
        if not (e0 <= c[0] <= e1 and n0 <= c[1] <= n1):
            continue
        if h < TALL_M and near(c[0], c[1]) > NEAR_M[src]:
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
            print("fetching NJ buildings (Overture) ...", file=sys.stderr)
            fetch_nj()
    near = shore_distance_fn()
    write("far_nyc.json", bake(nyc_records(), near),
          "NYC Open Data 5zhs-2jue (Building Footprints), boroughs 2-4", "public domain")
    if (CACHE / "nj_overture.json").exists():
        srcs = nj_sources()
        write("far_nj.json", bake(nj_records(), near),
              "Overture Maps Foundation, buildings theme (release in OVERTURE), New Jersey; "
              "datasets: " + ", ".join(srcs),
              "ODbL 1.0: (c) OpenStreetMap contributors, Overture Maps Foundation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
