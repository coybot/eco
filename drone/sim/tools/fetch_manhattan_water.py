#!/usr/bin/env python3
"""Bake Manhattan's land/water mask for the photoreal scene (env_manhattan_photo).

Sources:
  * OpenStreetMap coastline (natural=coastline), via the Overpass API.
    (c) OpenStreetMap contributors, ODbL 1.0. The mask is a produced work;
    attribution is in ../godot/assets/manhattan/CREDITS.md.
  * USGS National Hydrography Dataset, Waterbody (Large Scale), for the
    lakes and ponds (Central Park's Reservoir, the Lake, Harlem Meer). Public
    domain.

Output, in the DATASET frame of buildings.json (metres east/north of the
Battery Park datum, turned 29 deg onto the street grid; see
fetch_manhattan.py):

  water.png   8-bit, 255 = water, 0 = land; row 0 is the NORTH edge.
  water.json  {"bounds_enu": [e0, n0, e1, n1], "res_m": ..., "size_px": [w, h]}

The extent reaches well past the island on every side (the far shores of New
Jersey, Brooklyn, Queens and the Bronx), so the scene knows where the far
ground is land too.

How the mask is filled: the coastline is drawn as a one-pixel barrier, and
OSM's coastline convention (land on the LEFT of the way's direction) gives a
water vote just right of every segment and a land vote just left. Each
connected region between barriers takes the majority of its votes. That needs
no hand-placed seed points and cannot flood a borough through a gap, because a
region with no votes at all (a sliver) keeps its neighbours' answer by being
left as land.

    python3 fetch_manhattan_water.py --out ../godot/assets/manhattan
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

# Must match fetch_manhattan.py exactly.
DATUM_LAT = 40.7005
DATUM_LON = -74.0170
GRID_ROTATION_DEG = 29.0
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))

#: Dataset-frame extent of the mask: the island (e -2.8..2.5 km, n 0.1..21.7 km)
#: plus ~5 km all round.
BOUNDS = (-8000.0, -5000.0, 8000.0, 27000.0)
RES_M = 4.0

OVERPASS = ("https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter")
NHD = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/{layer}/query"
#: NHD layers and the feature types kept from each. Waterbody (12): lakes and
#: ponds (390), reservoirs (436), estuaries (493: the Hudson). Area (9): rivers
#: (460) and sea (445). The
#: Area layer matters because OSM's coastline crosses the Hudson and the
#: Harlem River part way up, and above that line they are river polygons,
#: not coastline: without it the upper Hudson came out as land.
NHD_LAYERS = {12: (390, 436, 493), 9: (460, 445)}
UA = "eco-sim/1.0 (drone simulator; manhattan water mask)"


def project(lon: float, lat: float) -> tuple[float, float]:
    x = (lon - DATUM_LON) * M_PER_DEG_LON
    y = (lat - DATUM_LAT) * M_PER_DEG_LAT
    t = math.radians(GRID_ROTATION_DEG)
    return (x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t))


def unproject(e: float, n: float) -> tuple[float, float]:
    t = math.radians(GRID_ROTATION_DEG)
    x = e * math.cos(t) + n * math.sin(t)
    y = -e * math.sin(t) + n * math.cos(t)
    return (DATUM_LON + x / M_PER_DEG_LON, DATUM_LAT + y / M_PER_DEG_LAT)


def geo_bbox(margin_m: float = 1500.0) -> tuple[float, float, float, float]:
    """south, west, north, east of the rotated extent, with a margin."""
    e0, n0, e1, n1 = BOUNDS
    pts = [unproject(e, n) for e in (e0 - margin_m, e1 + margin_m)
           for n in (n0 - margin_m, n1 + margin_m)]
    return (min(p[1] for p in pts), min(p[0] for p in pts),
            max(p[1] for p in pts), max(p[0] for p in pts))


def fetch_coastline(cache: Path) -> list[list[tuple[float, float]]]:
    f = cache / "coastline.json"
    if not f.exists():
        s, w, n, e = geo_bbox()
        q = f'[out:json][timeout:120];way["natural"="coastline"]({s},{w},{n},{e});out geom;'
        body = urllib.parse.urlencode({"data": q}).encode()
        last = ""
        for attempt in range(6):
            url = OVERPASS[attempt % len(OVERPASS)]
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=180) as r:
                    blob = r.read()
                json.loads(blob)
                f.write_bytes(blob)
                break
            except Exception as ex:                      # noqa: BLE001
                last = str(ex)
                time.sleep(5 * 2 ** attempt)
        else:
            raise SystemExit(f"Overpass failed: {last}")
    doc = json.loads(f.read_text())
    return [[(p["lon"], p["lat"]) for p in w["geometry"]] for w in doc["elements"]
            if w.get("type") == "way" and w.get("geometry")]


def fetch_lakes(cache: Path) -> list[list[list[tuple[float, float]]]]:
    """NHD water polygons kept by NHD_LAYERS, each [outer ring, hole, ...].
    The holes matter: the estuary's are Roosevelt, Randalls and Wards
    Islands."""
    rings = []
    for layer, ftypes in NHD_LAYERS.items():
        f = cache / f"nhd_{layer}.json"
        if not f.exists():
            s, w, n, e = geo_bbox(0.0)
            qs = urllib.parse.urlencode({
                "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
                "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                "outFields": "ftype,gnis_name,areasqkm", "returnGeometry": "true",
                "outSR": 4326, "f": "geojson"})
            req = urllib.request.Request(NHD.format(layer=layer) + "?" + qs,
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                f.write_bytes(r.read())
        doc = json.loads(f.read_text())
        if doc.get("exceededTransferLimit"):
            print(f"warning: NHD layer {layer} hit its record limit", file=sys.stderr)
        for feat in doc.get("features", []):
            props = {k.lower(): v for k, v in (feat.get("properties") or {}).items()}
            if int(props.get("ftype") or 0) not in ftypes:
                continue
            # Tiny ponds are noise at 4 m and mostly fountains or pools.
            if layer == 12 and float(props.get("areasqkm") or 0.0) < 0.002:
                continue
            g = feat["geometry"]
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            for poly in polys:
                rings.append([[tuple(p[:2]) for p in ring] for ring in poly])
    return rings


def to_px(e: float, n: float, w: int, h: int) -> tuple[float, float]:
    e0, n0, e1, n1 = BOUNDS
    return ((e - e0) / RES_M, (n1 - n) / RES_M)


def bake(cache: Path) -> tuple[np.ndarray, dict]:
    e0, n0, e1, n1 = BOUNDS
    w = int(round((e1 - e0) / RES_M))
    h = int(round((n1 - n0) / RES_M))
    ways = fetch_coastline(cache)

    barrier = Image.new("L", (w, h), 0)
    bd = ImageDraw.Draw(barrier)
    votes_w = np.zeros((h, w), np.int32)
    votes_l = np.zeros((h, w), np.int32)
    side = 3.0   # px either side of the line the votes are cast
    for way in ways:
        pts = [to_px(*project(lon, lat), w, h) for lon, lat in way]
        bd.line(pts, fill=255, width=1)
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            dx, dy = x1 - x0, y1 - y0
            seg = math.hypot(dx, dy)
            if seg < 1e-6:
                continue
            # Pixel rows grow SOUTH, so the image is mirrored against ENU:
            # "right of travel" in ENU is (dy, -dx) in ENU terms, which in
            # pixel terms (y flipped) is (-dy_px, dx_px).
            rx, ry = -dy / seg, dx / seg
            steps = max(1, int(seg / 2.0))
            for k in range(steps):
                t = (k + 0.5) / steps
                cx, cy = x0 + dx * t, y0 + dy * t
                for sgn, arr in ((1.0, votes_w), (-1.0, votes_l)):
                    px = int(cx + sgn * rx * side)
                    py = int(cy + sgn * ry * side)
                    if 0 <= px < w and 0 <= py < h:
                        arr[py, px] += 1
    bar = np.asarray(barrier) > 0
    lab, nlab = ndimage.label(~bar)
    vw = np.bincount(lab.ravel(), weights=votes_w.ravel(), minlength=nlab + 1)
    vl = np.bincount(lab.ravel(), weights=votes_l.ravel(), minlength=nlab + 1)
    is_w = vw > vl
    is_w[0] = False
    water = is_w[lab]
    # Barrier pixels take the majority of their 3x3 neighbourhood.
    nb = ndimage.uniform_filter(water.astype(np.float32), 3)
    water[bar] = nb[bar] > 0.5

    lakes = fetch_lakes(cache)
    lk = Image.new("L", (w, h), 0)
    holes = Image.new("L", (w, h), 0)
    ld = ImageDraw.Draw(lk)
    hd = ImageDraw.Draw(holes)
    for poly in lakes:
        ld.polygon([to_px(*project(lon, lat), w, h) for lon, lat in poly[0]], fill=255)
        for hole in poly[1:]:
            hd.polygon([to_px(*project(lon, lat), w, h) for lon, lat in hole], fill=255)
    water |= (np.asarray(lk) > 0) & ~(np.asarray(holes) > 0)

    meta = {"source": "OpenStreetMap coastline (ODbL, (c) OpenStreetMap contributors); "
                      "USGS NHD waterbodies (public domain)",
            "note": "255 = water. Row 0 is the north edge. Dataset frame of buildings.json.",
            "bounds_enu": list(BOUNDS), "res_m": RES_M, "size_px": [w, h],
            "coast_ways": len(ways), "nhd_polygons": len(lakes)}
    return water, meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent
                                          / "godot" / "assets" / "manhattan"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    cache = out / "_cache"      # gitignored (assets/*/_*)
    cache.mkdir(parents=True, exist_ok=True)
    water, meta = bake(cache)
    Image.fromarray((water * 255).astype(np.uint8)).save(out / "water.png", optimize=True)
    (out / "water.json").write_text(json.dumps(meta, indent=1))
    # Sanity: the island's own middle is land, the Hudson west of it water.
    def at(lat, lon):
        e, n = project(lon, lat)
        x, y = to_px(e, n, *meta["size_px"])
        return bool(water[int(y), int(x)])
    checks = {"midtown land": not at(40.7549, -73.9840), "hudson water": at(40.7550, -74.0150),
              "east river water": at(40.7450, -73.9650), "central park reservoir water": at(40.7851, -73.9625),
              "brooklyn land": not at(40.6900, -73.9700), "hoboken land": not at(40.7440, -74.0300),
              "upper hudson water": at(40.8900, -73.9200), "harlem river water": at(40.8600, -73.9180),
              "roosevelt island land": not at(40.7620, -73.9490), "randalls island land": not at(40.7930, -73.9220)}
    print(json.dumps(meta), json.dumps(checks))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
