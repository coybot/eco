#!/usr/bin/env python3
"""Bake Manhattan's street network for the photoreal scene's scenery scripts
(traffic, street lamps, lane markings, ferries).

Source: NYC Open Data, "Centerline" (CSCL, dataset inkn-q76z), public domain.
One record per street segment between intersections, with its roadway type,
traffic direction, street width (FEET in the source, converted to metres
here) and lane counts.

Output: assets/manhattan/roads.json, in the DATASET frame of buildings.json
(metres east/north of the Battery Park datum, turned 29 deg onto the street
grid; see fetch_manhattan.py):

  {"nodes": [[e, n], ...],
   "edges": [[a, b, class, oneway, lanes, width_m, park_lanes, name_idx, flags], ...],
   "names": [...], "ferries": [[[e, n], ...], ...]}

  class     motorway | motorway_link | primary | secondary | street | service | path
            (primary: >= 4 travel lanes, the avenues; secondary: 3 lanes or
            a >= 50 ft roadway, the wide crosstown streets)
  oneway    1: a -> b only (CSCL "FT"/"TF" are both written a -> b)
  lanes     travel lanes (CSCL number_travel_lanes; a guess by class where blank)
  width_m   kerb to kerb (CSCL streetwidth, ft -> m); 0 where blank
  flags     bit 0 bridge, bit 1 tunnel, bit 2 elevated, bit 3 below grade
            (CSCL from/to level codes: 13 is grade)
  ferries   CSCL's ferry routes (rw_type 14) as polylines, for ship traffic

Vertices are welded within WELD_M, so segments that meet at an intersection
share a node. Tunnel segments are kept but flagged: a traffic script should
not put cars on them.

    python3 fetch_manhattan_roads.py
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

DATASET = "https://data.cityofnewyork.us/resource/inkn-q76z.json"
UA = "eco-sim/1.0 (drone simulator; manhattan roads)"
PAGE = 20000
FT_TO_M = 0.3048
WELD_M = 0.6

DATUM_LAT = 40.7005
DATUM_LON = -74.0170
GRID_ROTATION_DEG = 29.0
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))

#: CSCL rw_type -> class. Streets (1) and bridges (3) are classed by width
#: below; 12 (non-physical) and 13 (U-turn) are dropped; 14 is a ferry route.
RW_CLASS = {2: "motorway", 9: "motorway_link", 4: "street", 5: "path", 6: "path",
            7: "path", 8: "service", 10: "service", 11: "street"}
DEFAULT_LANES = {"motorway": 3, "motorway_link": 1, "primary": 3, "secondary": 2,
                 "street": 1, "service": 1, "path": 0}


def project(lon: float, lat: float) -> tuple[float, float]:
    x = (lon - DATUM_LON) * M_PER_DEG_LON
    y = (lat - DATUM_LAT) * M_PER_DEG_LAT
    t = math.radians(GRID_ROTATION_DEG)
    return (round(x * math.cos(t) - y * math.sin(t), 2),
            round(x * math.sin(t) + y * math.cos(t), 2))


def fetch(cache: Path) -> list[dict]:
    f = cache / "cscl_manhattan.json"
    if f.exists():
        return json.loads(f.read_text())
    cols = ("the_geom,rw_type,trafdir,streetwidth,number_travel_lanes,number_park_lanes,"
            "full_street_name,from_level_code,to_level_code,status")
    rows: list[dict] = []
    off = 0
    while True:
        qs = urllib.parse.urlencode({"$select": cols, "$where": "boroughcode='1'",
                                     "$limit": PAGE, "$offset": off, "$order": "physicalid"})
        req = urllib.request.Request(DATASET + "?" + qs, headers={"User-Agent": UA})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    page = json.loads(r.read())
                break
            except Exception:                            # noqa: BLE001
                if attempt == 3:
                    raise
                time.sleep(5 * 2 ** attempt)
        rows += page
        print(f"  {len(rows)} segments", file=sys.stderr)
        if len(page) < PAGE:
            break
        off += PAGE
    f.write_text(json.dumps(rows))
    return rows


def classify(row: dict) -> str | None:
    rw = int(row.get("rw_type") or 0)
    if rw in (12, 13) or rw == 0:
        return None
    if rw == 14:
        return "ferry"
    if str(row.get("trafdir", "")).strip() == "NV" and rw not in (2, 9):
        return "path"
    if rw in (1, 3):
        # By travel lanes first: CSCL's streetwidth is the ROADWAY, and the
        # avenues' roadways are only ~60 ft, the same as a wide crosstown street.
        lanes = int(float(row.get("number_travel_lanes") or 0))
        w = float(row.get("streetwidth") or 0.0)
        if lanes >= 4 or w >= 75.0:
            return "primary"
        if lanes >= 3 or w >= 50.0:
            return "secondary"
        return "street"
    return RW_CLASS.get(rw, "street")


def lines(geom: dict) -> list[list[tuple[float, float]]]:
    if not geom:
        return []
    if geom["type"] == "LineString":
        return [[project(*p[:2]) for p in geom["coordinates"]]]
    if geom["type"] == "MultiLineString":
        return [[project(*p[:2]) for p in ln] for ln in geom["coordinates"]]
    return []


def bake(rows: list[dict]) -> dict:
    nodes: list[list[float]] = []
    grid: dict[tuple[int, int], list[int]] = {}

    def node(p: tuple[float, float]) -> int:
        kx, ky = int(math.floor(p[0] / WELD_M)), int(math.floor(p[1] / WELD_M))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in grid.get((kx + dx, ky + dy), []):
                    q = nodes[i]
                    if (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 <= WELD_M ** 2:
                        return i
        nodes.append([p[0], p[1]])
        grid.setdefault((kx, ky), []).append(len(nodes) - 1)
        return len(nodes) - 1

    names: list[str] = []
    name_idx: dict[str, int] = {}
    edges = []
    ferries = []
    seen = set()
    for row in rows:
        cls = classify(row)
        if cls is None:
            continue
        pls = lines(row.get("the_geom"))
        if cls == "ferry":
            ferries += [[list(p) for p in pl] for pl in pls if len(pl) >= 2]
            continue
        tr = str(row.get("trafdir", "")).strip()
        oneway = 1 if tr in ("FT", "TF") else 0
        lanes = int(float(row.get("number_travel_lanes") or 0)) or DEFAULT_LANES[cls]
        park = int(float(row.get("number_park_lanes") or 0))
        width = round(float(row.get("streetwidth") or 0.0) * FT_TO_M, 1)
        nm = str(row.get("full_street_name") or "")
        if nm not in name_idx:
            name_idx[nm] = len(names)
            names.append(nm)
        rw = int(row.get("rw_type") or 0)
        # CSCL level codes: 13 is grade; above it elevated, below it under.
        lv = [int(row.get(k) or 13) for k in ("from_level_code", "to_level_code")]
        flags = ((1 if rw == 3 else 0) | (2 if rw == 4 else 0)
                 | (4 if max(lv) > 13 else 0) | (8 if min(lv) < 13 else 0))
        for pl in pls:
            if tr == "TF":
                pl = pl[::-1]
            ids = [node(p) for p in pl]
            for a, b in zip(ids, ids[1:]):
                if a == b:
                    continue
                key = (a, b, cls) if oneway else (min(a, b), max(a, b), cls)
                if key in seen:
                    continue
                seen.add(key)
                edges.append([a, b, cls, oneway, lanes, width, park, name_idx[nm], flags])
    return {"source": "NYC Open Data Centerline (CSCL, inkn-q76z), public domain; "
                      "streetwidth ft -> m",
            "frame": "dataset frame of buildings.json (Battery Park datum, 29 deg grid)",
            "nodes": nodes, "edges": edges, "names": names, "ferries": ferries}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent
                                          / "godot" / "assets" / "manhattan"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    cache = out / "_cache"
    cache.mkdir(parents=True, exist_ok=True)
    doc = bake(fetch(cache))
    (out / "roads.json").write_text(json.dumps(doc, separators=(",", ":")))
    by = {}
    for e in doc["edges"]:
        by[e[2]] = by.get(e[2], 0) + 1
    print(f"{len(doc['nodes'])} nodes, {len(doc['edges'])} edges {by}, "
          f"{len(doc['ferries'])} ferry lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
