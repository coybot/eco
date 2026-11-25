#!/usr/bin/env python3
"""Bake the bridges round Manhattan for the photoreal scene (scripts/bridges.gd).

Where each bridge runs comes from OpenStreetMap (its roadway or track ways,
via the Overpass API; (c) OpenStreetMap contributors, ODbL 1.0). What each
bridge IS — its spans, tower heights, deck height, colour — is in SPEC below,
from the bridges' published dimensions (heights above mean high water, metres;
the sources publish feet and the numbers here are already converted).

Placement: a bridge's axis is the principal axis of its OSM ways, s = 0 at
the first point. Where the spans are known but OSM does not say where the
towers stand, the main span is centred on the water the axis crosses
(assets/manhattan/water.png), which puts the towers where they really are,
at or just off the two shores; for the Brooklyn and Queensboro bridges, whose
OSM ways run anchorage to anchorage, the spans are laid from the way's end.

Output: assets/manhattan/bridges.json, DATASET frame of buildings.json (see
fetch_manhattan.py project()). Heights are metres above STREET level (the
scene's y = 0); the water is SEA_Y = -1.6 m below it.

  {"bridges": [{"id", "name", "kind", "a": [e, n], "u": [ue, un],
                "width", "deck_y", "camber", "colour", ... per kind}]}

  Everything along a bridge is an `s` in metres from `a` along `u`; across
  it, + is to the left of u.

    python3 fetch_manhattan_bridges.py          # fetch (or reuse the cache) and bake
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "godot" / "assets" / "manhattan"
CACHE = ASSETS / "_cache" / "bridges"
OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "eco-sim/1.0 (drone simulator; bridges)"
BBOX = "40.69,-74.06,40.89,-73.90"

DATUM_LAT = 40.7005
DATUM_LON = -74.0170
GRID_ROTATION_DEG = 29.0
SEA_Y = -1.6
FT = 0.3048

QUERIES = {
    "osm_bridge_ways.json":
        f'[out:json][timeout:180];(way["bridge"]["highway"~"motorway|trunk|primary|secondary|'
        f'tertiary|cycleway|footway|path"]({BBOX});way["bridge"]["railway"]({BBOX});'
        f'way["aerialway"](40.74,-73.97,40.77,-73.94););out tags geom;',
    "osm_bridge_outlines.json":
        f'[out:json][timeout:120];(way["man_made"="bridge"]({BBOX}););out tags geom;',
}


def project(lon: float, lat: float) -> tuple[float, float]:
    m_lon = 111320.0 * math.cos(math.radians(DATUM_LAT))
    x = (lon - DATUM_LON) * m_lon
    y = (lat - DATUM_LAT) * 111132.0
    t = math.radians(GRID_ROTATION_DEG)
    return (x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t))


def fetch() -> dict[int, list]:
    CACHE.mkdir(parents=True, exist_ok=True)
    ways: dict[int, list] = {}
    for name, q in QUERIES.items():
        f = CACHE / name
        if not f.exists():
            wait = 20.0
            for _ in range(8):
                try:
                    req = urllib.request.Request(
                        OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
                        headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=300) as r:
                        f.write_bytes(r.read())
                    break
                except (urllib.error.URLError, TimeoutError) as ex:
                    print(f"  overpass: {ex}; retry in {wait:.0f} s", file=sys.stderr)
                    time.sleep(wait)
                    wait *= 1.8
        for el in json.loads(f.read_text())["elements"]:
            if el.get("geometry"):
                ways[el["id"]] = [project(p["lon"], p["lat"]) for p in el["geometry"]]
    return ways


# Colours: the paint each bridge actually wears (sRGB, 0..1).
GREY = [0.56, 0.58, 0.59]
STONE = [0.64, 0.59, 0.52]

#: kind: suspension | cantilever | arch_through | arch_under | swing | lift | tram
#: ways: OSM way ids along the structure (their principal axis is the bridge's)
#: ends: what happens past each end of `ways` (s < 0 and s > L):
#:   ["ramp", grade] an approach viaduct down to the street at that grade
#:   ["abut", length] high ground: the deck runs on to an abutment block
#: Heights are above mean high water as published; the bake turns them into
#: the scene's street-level frame.
SPEC = [
    {"id": "gwb", "name": "George Washington Bridge", "kind": "suspension",
     "ways": [49036344, 9702602], "centre": "water", "spans": [186.0, 1067.0, 186.0],
     # 604 ft towers; 212 ft clearance under the lower deck at mid-span, the
     # upper roadway ~10 m above that.
     "tower_h": 184.0, "deck_h": 70.0, "camber": 5.0, "width": 36.0, "lower_deck": 9.0,
     "tower": "lattice", "cables": 2, "colour": GREY,
     "ends": [["abut", 120.0], ["abut", 140.0]]},
    {"id": "brooklyn", "name": "Brooklyn Bridge", "kind": "suspension",
     "ways": [432550255, 46177152], "centre": "start", "spans": [283.5, 486.3, 283.5],
     # 276.5 ft granite towers with two pointed arches; 135 ft clearance.
     "tower_h": 84.3, "deck_h": 41.0, "camber": 5.0, "width": 26.0,
     "tower": "gothic", "cables": 2, "stays": True, "colour": [0.70, 0.63, 0.52],
     "stone": STONE, "ends": [["ramp", 0.08], ["ramp", 0.08]]},
    {"id": "manhattan", "name": "Manhattan Bridge", "kind": "suspension",
     "ways": [46179217], "centre": "water", "spans": [221.0, 448.0, 221.0],
     "tower_h": 102.0, "deck_h": 41.0, "camber": 3.0, "width": 37.0, "lower_deck": 7.0,
     "tower": "portal", "cables": 2, "colour": [0.45, 0.60, 0.74],
     "ends": [["ramp", 0.07], ["ramp", 0.07]]},
    {"id": "williamsburg", "name": "Williamsburg Bridge", "kind": "suspension",
     "ways": [40337615], "centre": "water", "spans": [182.0, 488.0, 182.0],
     "tower_h": 102.0, "deck_h": 41.0, "camber": 3.0, "width": 36.0, "lower_deck": 8.0,
     "tower": "lattice", "cables": 2, "colour": [0.66, 0.24, 0.21],
     "ends": [["ramp", 0.07], ["ramp", 0.07]]},
    {"id": "queensboro", "name": "Ed Koch Queensboro Bridge", "kind": "cantilever",
     "ways": [198924626],
     # Anchor arm 469 ft, channel 1182 ft, over Roosevelt Island 630 ft,
     # channel 984 ft, anchor arm 459 ft: 3724 ft, the OSM way's own length.
     "piers": [0.0, 143.0, 503.0, 695.0, 995.0, 1135.0], "pier_top_h": 100.0,
     "deck_h": 41.0, "camber": 1.5, "width": 27.0, "truss": 16.0,
     "colour": [0.66, 0.64, 0.58], "stone": STONE,
     "ends": [["ramp", 0.07], ["ramp", 0.07]]},
    {"id": "rfk", "name": "Robert F. Kennedy Bridge (Hell Gate span)", "kind": "suspension",
     "ways": [808614661], "centre": "water", "spans": [215.0, 421.0, 215.0],
     "tower_h": 96.0, "deck_h": 44.0, "camber": 3.0, "width": 30.0,
     "tower": "portal", "cables": 1, "colour": [0.50, 0.57, 0.54],
     "ends": [["ramp", 0.045], ["ramp", 0.045]]},
    {"id": "rfk_lift", "name": "Robert F. Kennedy Bridge (Harlem River lift)", "kind": "lift",
     "ways": [576003316], "span": 94.0, "tower_h": 64.0, "deck_h": 17.0, "width": 30.0,
     "colour": [0.50, 0.57, 0.54], "ends": [["ramp", 0.05], ["ramp", 0.05]]},
    {"id": "hellgate", "name": "Hell Gate Bridge", "kind": "arch_through",
     "ways": [5700380], "span": 298.0, "crown_h": 93.0, "tower_h": 76.0, "deck_h": 41.0,
     "width": 28.0, "colour": [0.55, 0.17, 0.16], "stone": STONE,
     "ends": [["ramp", 0.03], ["ramp", 0.03]]},
    {"id": "henryhudson", "name": "Henry Hudson Bridge", "kind": "arch_under",
     "ways": [9764584], "arches": [256.0], "deck_h": 43.0, "width": 25.0,
     "colour": [0.46, 0.51, 0.56], "ends": [["abut", 80.0], ["abut", 80.0]]},
    {"id": "washington", "name": "Washington Bridge", "kind": "arch_under",
     "ways": [33117565], "arches": [155.0, 155.0], "arch_offset": -78.0, "deck_h": 41.0,
     "width": 27.0, "colour": [0.42, 0.44, 0.44], "stone": STONE,
     "ends": [["abut", 60.0], ["abut", 60.0]]},
    {"id": "hamilton", "name": "Alexander Hamilton Bridge", "kind": "arch_under",
     "ways": [46593907], "arches": [169.0], "deck_h": 31.0, "width": 34.0,
     "colour": [0.52, 0.54, 0.54], "ends": [["abut", 40.0], ["abut", 40.0]]},
    {"id": "highbridge", "name": "High Bridge", "kind": "arch_under",
     "ways": [60251530], "arches": [137.0], "deck_h": 42.0, "width": 8.0,
     "colour": [0.33, 0.38, 0.34], "stone": STONE, "arcade": 25.0,
     "ends": [["abut", 30.0], ["abut", 30.0]]},
    # The Harlem River's low crossings: swing spans (a through truss on a
    # centre pivot pier) and vertical lifts (a span between two pairs of towers).
    {"id": "macombs", "name": "Macombs Dam Bridge", "kind": "swing", "ways": [420895471, 584971387],
     "span": 126.0, "truss_h": 18.0, "deck_h": 9.0, "width": 25.0, "colour": [0.50, 0.56, 0.62],
     "ends": [["ramp", 0.04], ["ramp", 0.04]]},
    {"id": "145st", "name": "145th Street Bridge", "kind": "swing", "ways": [799505593, 409222602],
     "span": 91.0, "truss_h": 11.0, "deck_h": 8.0, "width": 20.0, "colour": [0.38, 0.47, 0.42],
     "ends": [["ramp", 0.04], ["ramp", 0.04]]},
    {"id": "madison", "name": "Madison Avenue Bridge", "kind": "swing", "ways": [33117595, 799505599],
     "span": 91.0, "truss_h": 12.0, "deck_h": 8.0, "width": 22.0, "colour": [0.55, 0.57, 0.57],
     "ends": [["ramp", 0.04], ["ramp", 0.04]]},
    {"id": "thirdave", "name": "Third Avenue Bridge", "kind": "swing", "ways": [46626602],
     "span": 105.0, "truss_h": 13.0, "deck_h": 8.0, "width": 24.0, "colour": [0.52, 0.55, 0.57],
     "ends": [["ramp", 0.04], ["ramp", 0.04]]},
    {"id": "willis", "name": "Willis Avenue Bridge", "kind": "swing", "ways": [949990384],
     "span": 107.0, "truss_h": 14.0, "deck_h": 8.0, "width": 24.0, "colour": [0.58, 0.64, 0.64],
     "ends": [["ramp", 0.04], ["ramp", 0.04]]},
    {"id": "univheights", "name": "University Heights Bridge", "kind": "swing", "ways": [19174360],
     "span": 81.0, "truss_h": 11.0, "deck_h": 8.0, "width": 18.0, "colour": [0.50, 0.53, 0.55],
     "ends": [["ramp", 0.04], ["ramp", 0.04]]},
    {"id": "spuyten", "name": "Spuyten Duyvil Bridge", "kind": "swing", "ways": [799252567],
     "span": 88.0, "truss_h": 10.0, "deck_h": 5.0, "width": 9.0, "colour": [0.33, 0.35, 0.36],
     "ends": [["ramp", 0.02], ["ramp", 0.02]]},
    {"id": "broadway", "name": "Broadway Bridge", "kind": "lift", "ways": [799505579],
     "span": 92.0, "tower_h": 46.0, "deck_h": 9.0, "width": 24.0, "colour": [0.42, 0.52, 0.64],
     "ends": [["ramp", 0.04], ["ramp", 0.04]]},
    {"id": "parkave", "name": "Harlem River Lift Bridge", "kind": "lift", "ways": [180743247],
     "span": 104.0, "tower_h": 50.0, "deck_h": 9.0, "width": 26.0, "colour": [0.48, 0.50, 0.50],
     "ends": [["ramp", 0.02], ["ramp", 0.02]]},
    {"id": "roosevelt", "name": "Roosevelt Island Bridge", "kind": "lift", "ways": [351201921, 351201922],
     "span": 127.0, "tower_h": 36.0, "deck_h": 12.0, "width": 16.0, "colour": [0.52, 0.55, 0.56],
     "ends": [["ramp", 0.05], ["ramp", 0.05]]},
    {"id": "wards", "name": "Wards Island Bridge", "kind": "lift", "ways": [1016498838],
     "span": 95.0, "tower_h": 42.0, "deck_h": 17.0, "width": 6.0, "colour": [0.55, 0.62, 0.70],
     "ends": [["ramp", 0.07], ["ramp", 0.07]]},
    # The aerial tramway beside the Queensboro: stations at each end, three
    # towers, the tallest 250 ft by the river.
    {"id": "tram", "name": "Roosevelt Island Tramway", "kind": "tram", "ways": [22886820],
     "towers": [[230.0, 45.0], [520.0, 76.0], [880.0, 55.0]], "station_h": 18.0,
     "colour": [0.60, 0.62, 0.63], "cabin": [0.80, 0.10, 0.10]},
]


def water_mask():
    meta = json.loads((ASSETS / "water.json").read_text())
    img = np.array(Image.open(ASSETS / "water.png").convert("L"))
    e0, n0, e1, n1 = meta["bounds_enu"]
    res = float(meta["res_m"])

    def is_water(e: float, n: float) -> bool:
        x, y = int((e - e0) / res), int((n1 - n) / res)
        if x < 0 or y < 0 or x >= img.shape[1] or y >= img.shape[0]:
            return True
        return img[y, x] > 127
    return is_water


def water_runs(a, u, s0, s1, is_water) -> list[tuple[float, float]]:
    runs, cur = [], None
    for s in np.arange(s0, s1, 2.0):
        w = is_water(a[0] + u[0] * s, a[1] + u[1] * s)
        if w and cur is None:
            cur = s
        elif not w and cur is not None:
            runs.append((cur, s))
            cur = None
    if cur is not None:
        runs.append((cur, s1))
    return [r for r in runs if r[1] - r[0] > 16.0]


def bake_one(sp: dict, ways: dict, is_water) -> dict:
    pts = np.array([p for w in sp["ways"] for p in ways[w]])
    c = pts.mean(axis=0)
    u = np.linalg.svd(pts - c)[2][0]
    s = (pts - c) @ u
    a = c + u * s.min()
    length = float(s.max() - s.min())
    runs = water_runs(a, u, -500.0, length + 500.0, is_water)
    # The run the structure crosses: the longest one overlapping it.
    over = [r for r in runs if r[1] > 0 and r[0] < length] or runs
    wr = max(over, key=lambda r: r[1] - r[0]) if over else (0.0, length)
    mid = float(0.5 * (wr[0] + wr[1]))
    out = {k: v for k, v in sp.items() if k not in ("ways", "centre", "tower_h", "deck_h",
                                                     "pier_top_h", "crown_h")}
    out.update({"a": [round(float(a[0]), 2), round(float(a[1]), 2)],
                "u": [round(float(u[0]), 5), round(float(u[1]), 5)],
                "length": round(length, 1),
                "water": [round(float(wr[0]), 1), round(float(wr[1]), 1)]})
    if "deck_h" in sp:
        out["deck_y"] = round(sp["deck_h"] + SEA_Y, 2)
    if "tower_h" in sp:
        out["tower_y"] = round(sp["tower_h"] + SEA_Y, 2)
    k = sp["kind"]
    if k == "suspension":
        side0, main, side1 = sp["spans"]
        t0 = side0 if sp["centre"] == "start" else mid - main * 0.5
        out["towers"] = [round(t0, 1), round(t0 + main, 1)]
        out["anchors"] = [round(t0 - side0, 1), round(t0 + main + side1, 1)]
    elif k == "cantilever":
        out["piers"] = sp["piers"]
        out["anchors"] = [sp["piers"][0], sp["piers"][-1]]
        out["pier_top_y"] = round(sp["pier_top_h"] + SEA_Y, 2)
    elif k == "arch_through":
        out["towers"] = [round(mid - sp["span"] * 0.5, 1), round(mid + sp["span"] * 0.5, 1)]
        out["anchors"] = [round(mid - sp["span"] * 0.5 - 12, 1), round(mid + sp["span"] * 0.5 + 12, 1)]
        out["crown_y"] = round(sp["crown_h"] + SEA_Y, 2)
    elif k == "arch_under":
        total = sum(sp["arches"])
        x = mid - total * 0.5 + sp.get("arch_offset", 0.0)
        arches = []
        for w in sp["arches"]:
            arches.append([round(x, 1), round(x + w, 1)])
            x += w
        out["arches"] = arches
        out["anchors"] = [round(min(0.0, arches[0][0] - 10), 1),
                          round(max(length, arches[-1][1] + 10), 1)]
    elif k in ("swing", "lift"):
        out["span"] = [round(mid - sp["span"] * 0.5, 1), round(mid + sp["span"] * 0.5, 1)]
        # The OSM way is often the movable span alone; the deck runs on, on
        # fixed spans, over the rest of the river and onto each bank.
        out["anchors"] = [round(min(0.0, wr[0] - 15.0), 1), round(max(length, wr[1] + 15.0), 1)]
    elif k == "tram":
        out["towers"] = [[t[0], round(t[1] + SEA_Y, 1)] for t in sp["towers"]]
        out["anchors"] = [0.0, round(length, 1)]
    return out


def main() -> int:
    ways = fetch()
    is_water = water_mask()
    out = []
    for sp in SPEC:
        missing = [w for w in sp["ways"] if w not in ways]
        if missing:
            print(f"  {sp['id']}: OSM ways {missing} missing, skipped", file=sys.stderr)
            continue
        b = bake_one(sp, ways, is_water)
        out.append(b)
        print(f"  {b['id']:12s} L={b['length']:6.0f} water={b['water']} "
              f"towers={b.get('towers', b.get('piers', b.get('arches', b.get('span'))))}",
              file=sys.stderr)
    doc = {"source": "bridge alignments: OpenStreetMap via Overpass, (c) OpenStreetMap "
                     "contributors, ODbL 1.0; dimensions: published bridge data",
           "frame": "dataset frame of buildings.json; y metres above street level",
           "sea_y": SEA_Y, "bridges": out}
    p = ASSETS / "bridges.json"
    p.write_text(json.dumps(doc, indent=1))
    print(f"{len(out)} bridges -> {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
