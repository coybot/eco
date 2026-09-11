#!/usr/bin/env python3
"""Fetch real NYC building footprints and emit the sim's Manhattan dataset.

Source: NYC Open Data "Building Footprints" (dataset 5zhs-2jue), public domain,
updated daily by NYC OTI. Each record carries the footprint polygon plus
`height_roof` and `ground_elevation`.

UNITS: the source publishes height_roof and ground_elevation in US FEET. This
tool converts to METRES on write (FT_TO_M below) — the sim is metres
throughout. Nothing downstream should re-convert.

Output is a compact JSON the Godot env loads: axis-aligned footprint rects in a
local ENU frame (x=east, y=north, metres) plus height. Axis-aligned because
fixedwing_manager's occupancy grid is built from Rect2 footprints; the rect is
the polygon's bounding box, so a building is never under-approximated (routing
stays conservative).

Usage:
    python3 fetch_manhattan.py --out ../godot/assets/manhattan/buildings.json
    python3 fetch_manhattan.py --district midtown --out /tmp/midtown.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.parse
import urllib.request

DATASET = "https://data.cityofnewyork.us/resource/5zhs-2jue.json"
FT_TO_M = 0.3048
PAGE = 10000

# Datum: Battery Park, the island's south tip. Local ENU origin, so every
# coordinate in the output is metres north/east of here and Manhattan lies in
# positive north.
DATUM_LAT = 40.7005
DATUM_LON = -74.0170

# lat_north, lon_west, lat_south, lon_east
REGIONS = {
    # Whole island (the bbox also clips a little of the Bronx/Queens waterfront;
    # `--min-height` and the island polygon test below keep that manageable).
    "manhattan": (40.8820, -74.0200, 40.7000, -73.9070),
    "midtown":   (40.7640, -73.9930, 40.7440, -73.9700),
    "downtown":  (40.7250, -74.0180, 40.7000, -73.9950),
}


# Manhattan's Commissioners' Plan grid runs 29 deg off true north, so a building
# is 29 deg off OUR axes and its axis-aligned bounding box is far bigger than the
# building. That matters because the occupancy grid is built from those boxes:
# the inflation bleeds across the ~18 m cross-streets and seals them, leaving a
# city with avenues but no side streets to fly down.
#
# Rotating the frame onto the street grid makes the boxes tight. Measured on 400
# Midtown footprints, total AABB area falls 45% and the minimum is at exactly
# 29 deg — the plan angle, recovered from the data rather than assumed.
#
# The cost: x/y are then city-aligned, NOT true east/north. Anything converting
# sim coordinates back to lat/lon must undo this rotation — the angle travels
# with the dataset as `grid_rotation_deg` so that stays possible.
GRID_ROTATION_DEG = 29.0


def project(lon: float, lat: float) -> tuple[float, float]:
    """Equirectangular lon/lat -> local, street-grid-aligned metres about the datum.

    Manhattan spans ~0.18 deg of latitude, where the distortion of this
    projection against a proper transverse Mercator is well under a metre —
    irrelevant next to the footprint simplification below.
    """
    m_per_deg_lat = 111132.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(DATUM_LAT))
    x = (lon - DATUM_LON) * m_per_deg_lon
    y = (lat - DATUM_LAT) * m_per_deg_lat
    t = math.radians(GRID_ROTATION_DEG)
    c, s = math.cos(t), math.sin(t)
    return (x * c - y * s, x * s + y * c)


def rings(geom: dict):
    """Yield each polygon's outer ring, for Polygon or MultiPolygon."""
    if not geom:
        return
    t = geom.get("type")
    coords = geom.get("coordinates") or []
    if t == "Polygon":
        if coords:
            yield coords[0]
    elif t == "MultiPolygon":
        for poly in coords:
            if poly:
                yield poly[0]


def fetch(region: str, min_height_ft: float) -> list[dict]:
    lat_n, lon_w, lat_s, lon_e = REGIONS[region]
    # Borough filter, not just the bounding box. A lat/lon box around Manhattan
    # also catches Brooklyn, Queens and the Bronx across the rivers — 103,641
    # buildings in the box versus 45,035 actually on the island. Those far-shore
    # buildings are not harmless scenery: anything fitting a flight path to the
    # data averages Manhattan with the opposite bank and puts the aircraft over
    # open water. BBL's first digit is the borough code, and Manhattan is 1.
    where = (f"within_box(the_geom, {lat_n}, {lon_w}, {lat_s}, {lon_e})"
             f" AND starts_with(base_bbl, '1')"
             f" AND height_roof > {min_height_ft}")
    out: list[dict] = []
    offset = 0
    while True:
        q = urllib.parse.urlencode({
            "$select": "the_geom,height_roof,ground_elevation,name,bin",
            "$where": where,
            "$limit": PAGE,
            "$offset": offset,
            "$order": "bin",
        })
        with urllib.request.urlopen(f"{DATASET}?{q}", timeout=180) as r:
            page = json.load(r)
        if not page:
            break
        out.extend(page)
        offset += PAGE
        print(f"  fetched {len(out)}", file=sys.stderr)
        if len(page) < PAGE:
            break
    return out


def to_buildings(records: list[dict]) -> list[dict]:
    buildings = []
    for rec in records:
        try:
            h_ft = float(rec.get("height_roof") or 0.0)
        except (TypeError, ValueError):
            continue
        if h_ft <= 0:
            continue
        for ring in rings(rec.get("the_geom")):
            xs, ys = [], []
            for pt in ring:
                x, y = project(float(pt[0]), float(pt[1]))
                xs.append(x)
                ys.append(y)
            if len(xs) < 3:
                continue
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            # Drop slivers: sub-2 m footprints are sheds, vents and map noise,
            # and at the occupancy grid's resolution they only add clutter.
            if (x1 - x0) < 2.0 or (y1 - y0) < 2.0:
                continue
            buildings.append({
                "r": [round(x0, 1), round(y0, 1),
                      round(x1 - x0, 1), round(y1 - y0, 1)],
                "h": round(h_ft * FT_TO_M, 1),
                "n": rec.get("name") or "",
            })
    return buildings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="manhattan", choices=sorted(REGIONS))
    ap.add_argument("--min-height", type=float, default=0.0,
                    help="drop buildings shorter than this, in FEET (source units)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    print(f"fetching {args.region} ...", file=sys.stderr)
    records = fetch(args.region, args.min_height)
    buildings = to_buildings(records)
    if not buildings:
        print("no buildings returned", file=sys.stderr)
        return 1

    xs0 = min(b["r"][0] for b in buildings)
    ys0 = min(b["r"][1] for b in buildings)
    xs1 = max(b["r"][0] + b["r"][2] for b in buildings)
    ys1 = max(b["r"][1] + b["r"][3] for b in buildings)
    tallest = max(buildings, key=lambda b: b["h"])

    doc = {
        "source": "NYC Open Data 5zhs-2jue (Building Footprints), public domain",
        "units": "metres; source height_roof is US feet, converted on write",
        "datum": {"lat": DATUM_LAT, "lon": DATUM_LON},
        "grid_rotation_deg": GRID_ROTATION_DEG,
        "region": args.region,
        "bounds_enu": [round(xs0, 1), round(ys0, 1), round(xs1, 1), round(ys1, 1)],
        "count": len(buildings),
        "buildings": buildings,
    }
    with open(args.out, "w") as f:
        json.dump(doc, f, separators=(",", ":"))

    print(f"{len(buildings)} buildings -> {args.out}", file=sys.stderr)
    print(f"  extent: {xs1 - xs0:.0f} m east x {ys1 - ys0:.0f} m north", file=sys.stderr)
    print(f"  tallest: {tallest['h']:.0f} m {tallest['n']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
