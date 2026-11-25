#!/usr/bin/env python3
"""Fetch NYC's planimetric street surfaces and waterfront structures for the
photoreal Manhattan scene (env_manhattan_photo), and bake what the ground and
the piers draw.

Source: NYC Open Data, NYC Planimetric Database (DoITT), public domain:
  Roadbed                 i36f-5ih7   kerb-to-kerb carriageway polygons
  Sidewalk                52n9-sdep   sidewalk polygons
  Median                  ees7-4ufv   raised medians and traffic islands
  Hydrography Structures  6hbv-tek4   piers (feat_code 2800), docks, platforms

WHY THE STREETS ARE DRAWN, NOT PHOTOGRAPHED

The aerial photo is a central projection taken from ~3 km, so every tall
building leans away from the photo's centre by height x (distance off nadir)
/ altitude: a 200 m tower 1 km off nadir lies 65 m across the street next to
it. In Midtown the photo's streets are mostly facades. The planimetric
roadbed and sidewalk polygons are surveyed on the ground plane and do not
lean, so the ground shader paints asphalt and concrete wherever they say,
over the photo, and keeps the photo only for what is between the streets
(lots, plazas, parks, roofs the city has no box for).

OUTPUTS (dataset frame of buildings.json: metres east/north of the Battery
Park datum, turned 29 deg onto the street grid; see fetch_manhattan.py)

  _cache/planimetric/<layer>.json        the raw rows (gitignored)
  _hi/streets_<i>_<j>.png                per 1000 m ground tile (the tiling of
                                         fetch_manhattan_naip.py): signed-
                                         distance fields at 2 m/px, R = roadbed,
                                         G = any paved public way (roadbed +
                                         sidewalk + median), B = median. 128 is
                                         the edge, +1 per 0.125 m inside.
  piers.json                             pier/platform polygons (committed)

A signed-distance field rather than a mask because the edges are straight
lines and an SDF reconstructs a straight edge exactly under bilinear
filtering at any resolution, so a 2 m/px texture gives a kerb that is sharp
at 1 cm/px on screen. Only corners round, at ~2 m radius, which is about the
radius real kerbs have.

    python3 fetch_manhattan_planimetric.py            # fetch + bake
    python3 fetch_manhattan_planimetric.py --no-fetch # re-bake from the cache
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

UA = "eco-sim/1.0 (drone simulator; manhattan planimetric)"
PAGE = 20000

DATUM_LAT = 40.7005
DATUM_LON = -74.0170
GRID_ROTATION_DEG = 29.0
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))

#: Layer -> (dataset id, the box it is fetched over). Streets only over
#: Manhattan (the far shores keep their photo: they are seen from >300 m and
#: their low buildings barely lean); piers over the whole water mask.
MANHATTAN_BOX = (40.689, 40.885, -74.048, -73.864)     # lat0, lat1, lon0, lon1
MASK_BOX = (40.626, 40.948, -74.129, -73.778)
LAYERS = {
    "roadbed": ("i36f-5ih7", MANHATTAN_BOX),
    "sidewalk": ("52n9-sdep", MANHATTAN_BOX),
    "median": ("ees7-4ufv", MANHATTAN_BOX),
    "hydro_structures": ("6hbv-tek4", MASK_BOX),
}

#: Ground tiling, shared with fetch_manhattan_naip.py and manhattan_ground.gd.
TILE_M = 1000.0
SDF_RES = 2.0              # m per texel of the street field
SDF_SCALE = 8.0            # texel value per metre (0.125 m per step)
RASTER_RES = 0.5           # m per pixel the polygons are rasterised at


def project(lon: float, lat: float) -> tuple[float, float]:
    x = (lon - DATUM_LON) * M_PER_DEG_LON
    y = (lat - DATUM_LAT) * M_PER_DEG_LAT
    t = math.radians(GRID_ROTATION_DEG)
    return (x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t))


def fetch(layer: str, cache: Path) -> list[dict]:
    f = cache / f"{layer}.json"
    if f.exists():
        return json.loads(f.read_text())
    ds, (la0, la1, lo0, lo1) = LAYERS[layer]
    wkt = (f"POLYGON(({lo0} {la0}, {lo1} {la0}, {lo1} {la1}, {lo0} {la1}, {lo0} {la0}))")
    rows: list[dict] = []
    off = 0
    while True:
        qs = urllib.parse.urlencode({"$where": f"intersects(the_geom, '{wkt}')",
                                     "$limit": PAGE, "$offset": off, "$order": ":id"})
        url = f"https://data.cityofnewyork.us/resource/{ds}.json?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    page = json.loads(r.read())
                break
            except Exception as e:                    # noqa: BLE001
                if attempt == 3:
                    raise
                print(f"  {layer}: retry after {e}")
                time.sleep(5 * (attempt + 1))
        rows += page
        print(f"  {layer}: {len(rows)} rows", end="\r", flush=True)
        if len(page) < PAGE:
            break
        off += PAGE
    print(f"  {layer}: {len(rows)} rows")
    f.write_text(json.dumps(rows))
    return rows


def rings(geom: dict) -> list[list[list[tuple[float, float]]]]:
    """Polygons as [outer, hole, ...] rings, projected to the dataset frame."""
    if not geom:
        return []
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    out = []
    for poly in polys:
        out.append([[project(float(p[0]), float(p[1])) for p in ring] for ring in poly])
    return out


# ------------------------------------------------------------------ streets
def rasterise(polys: list, x0: float, y1: float, w: int, h: int) -> np.ndarray:
    """Fill polygons (holes cut) into a w x h bool image at RASTER_RES, row 0 at y1."""
    im = Image.new("L", (w, h), 0)
    dr = ImageDraw.Draw(im)
    for poly in polys:
        outer = [((e - x0) / RASTER_RES, (y1 - n) / RASTER_RES) for e, n in poly[0]]
        if len(outer) >= 3:
            dr.polygon(outer, fill=255)
        for hole in poly[1:]:
            hp = [((e - x0) / RASTER_RES, (y1 - n) / RASTER_RES) for e, n in hole]
            if len(hp) >= 3:
                dr.polygon(hp, fill=0)
    return np.asarray(im) > 127


def sdf(mask: np.ndarray) -> np.ndarray:
    """Signed distance in metres, + inside."""
    inside = ndimage.distance_transform_edt(mask)
    outside = ndimage.distance_transform_edt(~mask)
    return (inside - outside) * RASTER_RES


def bbox(poly) -> tuple[float, float, float, float]:
    es = [p[0] for p in poly[0]]
    ns = [p[1] for p in poly[0]]
    return min(es), min(ns), max(es), max(ns)


def own_land(assets: Path) -> tuple[np.ndarray, list]:
    """Manhattan's own land (the island, Roosevelt, Randall's), grown 40 m,
    at the water mask's 4 m, and the mask's bounds. Streets are drawn only
    there: the data box also takes in Queens, Brooklyn and the Bronx, but
    their facing shores are mostly in the coarser "mid" tiles, which have no
    street field, so drawing them in the few hi tiles that reach across the
    river put a hard edge in the far shore where the tiles change."""
    meta = json.loads((assets / "water.json").read_text())
    wb = meta["bounds_enu"]
    res = float(meta["res_m"])
    water = np.asarray(Image.open(assets / "water.png").convert("L")) > 127
    lab, n = ndimage.label(~water)
    count = np.zeros(n + 1)
    for b in json.loads((assets / "buildings.json").read_text())["buildings"]:
        r = b["r"]
        x = int((r[0] + r[2] / 2 - wb[0]) / res)
        y = int((wb[3] - (r[1] + r[3] / 2)) / res)
        if 0 <= x < water.shape[1] and 0 <= y < water.shape[0]:
            count[lab[y, x]] += 1
    area = ndimage.sum(np.ones_like(lab), lab, index=np.arange(n + 1)) * res * res / 1e6
    own = [k for k in range(1, n + 1) if area[k] > 0 and count[k] / area[k] > 50.0]
    mine = ndimage.binary_dilation(np.isin(lab, own), iterations=10)
    return mine, wb


def bake_streets(road: list, paved: list, median: list, tiles: list[tuple[int, int]], out: Path,
                 own: tuple[np.ndarray, list]) -> int:
    pad = 24.0                      # m of context so the field is right at tile edges
    side = TILE_M + 2 * pad
    wpx = int(round(side / RASTER_RES))
    k = int(round(SDF_RES / RASTER_RES))
    rb = [(bbox(p), p) for p in road]
    pb = [(bbox(p), p) for p in paved]
    mb = [(bbox(p), p) for p in median]
    n = 0
    for (i, j) in tiles:
        x0 = i * TILE_M - pad
        y0 = j * TILE_M - pad
        x1, y1 = x0 + side, y0 + side

        def pick(lst):
            return [p for (b, p) in lst if b[2] >= x0 and b[0] <= x1 and b[3] >= y0 and b[1] <= y1]
        r_sel, p_sel, m_sel = pick(rb), pick(pb), pick(mb)
        if not r_sel and not p_sel:
            continue
        chans = []
        for sel in (r_sel, p_sel, m_sel):
            m = rasterise(sel, x0, y1, wpx, wpx)
            d = sdf(m)
            # Box-average down to SDF_RES: the mean of a linear field is the
            # field at the block centre, so straight edges stay exact.
            cut = int(round(pad / RASTER_RES))
            d = d[cut:cut + int(TILE_M / RASTER_RES), cut:cut + int(TILE_M / RASTER_RES)]
            d = d.reshape(d.shape[0] // k, k, d.shape[1] // k, k).mean(axis=(1, 3))
            chans.append(np.clip(np.round(128.0 + d * SDF_SCALE), 0, 255).astype(np.uint8))
        # Off Manhattan's own land: "outside" everywhere (the photo shows).
        mine, wb = own
        ee = i * TILE_M + (np.arange(chans[0].shape[1]) + 0.5) * SDF_RES
        nn = (j + 1) * TILE_M - (np.arange(chans[0].shape[0]) + 0.5) * SDF_RES
        xs = np.clip(((ee - wb[0]) / 4.0).astype(int), 0, mine.shape[1] - 1)
        ys = np.clip(((wb[3] - nn) / 4.0).astype(int), 0, mine.shape[0] - 1)
        keep = mine[np.ix_(ys, xs)]
        for ch in chans:
            ch[~keep] = 0
        rgb = np.stack(chans, axis=-1)
        Image.fromarray(rgb, "RGB").save(out / f"streets_{i}_{j}.png", optimize=True)
        n += 1
        print(f"  streets tile {n}/{len(tiles)}", end="\r", flush=True)
    print()
    return n


# ------------------------------------------------------------------ piers
#: Hydrography Structures feat_code 2800 sub_codes kept as decks. Docks and
#: platforms (280000..280020 in the 2022 release) all stand on piles at
#: about street level, so they are drawn alike; anything else in the layer
#: (breakwaters, jetties, dolphins) is dropped. Areas under 30 m2 are mooring
#: dolphins and fender clusters, not decks.
MIN_PIER_M2 = 30.0


def bake_piers(rows: list[dict], out: Path) -> int:
    piers = []
    codes: dict = {}
    for r in rows:
        fc = str(r.get("feat_code", ""))
        codes[(fc, str(r.get("sub_code", "")))] = codes.get((fc, str(r.get("sub_code", ""))), 0) + 1
        if fc != "2800":
            continue
        for poly in rings(r.get("the_geom")):
            outer = poly[0]
            if len(outer) < 4:
                continue
            a = 0.0
            for q in range(len(outer) - 1):
                a += outer[q][0] * outer[q + 1][1] - outer[q + 1][0] * outer[q][1]
            if abs(a) * 0.5 < MIN_PIER_M2:
                continue
            piers.append({
                "sub": str(r.get("sub_code", "")),
                "elev_ft": round(float(r.get("elevation") or 0.0), 2),
                "rings": [[[round(e, 2), round(n, 2)] for e, n in ring] for ring in poly],
            })
    print("hydro structure codes:", dict(sorted(codes.items())))
    (out / "piers.json").write_text(json.dumps({
        "source": "NYC Open Data, NYC Planimetric Database: Hydrography Structures (6hbv-tek4), public domain",
        "note": "feat_code 2800 (piers, docks, platforms); dataset frame of buildings.json; rings[0] outer",
        "piers": piers}, separators=(",", ":")))
    return len(piers)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent
                                         / "godot" / "assets" / "manhattan"))
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out)
    cache = out / "_cache" / "planimetric"
    cache.mkdir(parents=True, exist_ok=True)
    hi = out / "_hi"
    hi.mkdir(exist_ok=True)

    data = {}
    for layer in LAYERS:
        if args.no_fetch and not (cache / f"{layer}.json").exists():
            print(f"missing cache for {layer}")
            return 1
        data[layer] = fetch(layer, cache)

    print(f"piers: {bake_piers(data['hydro_structures'], out)}")

    road, paved, median = [], [], []
    for r in data["roadbed"]:
        road += rings(r.get("the_geom"))
    for r in data["median"]:
        median += rings(r.get("the_geom"))
    paved += road + median
    for r in data["sidewalk"]:
        paved += rings(r.get("the_geom"))
    tj = hi / "tiles.json"
    if not tj.exists():
        print("no _hi/tiles.json: run fetch_manhattan_naip.py first (it picks the tiles)")
        return 1
    tiles = [(int(t["i"]), int(t["j"])) for t in json.loads(tj.read_text())["tiles"]
             if t["level"] == "hi"]
    n = bake_streets(road, paved, median, tiles, hi, own_land(out))
    print(f"streets: {n} tiles in {hi}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
