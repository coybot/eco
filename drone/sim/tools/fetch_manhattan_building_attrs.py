#!/usr/bin/env python3
"""Bake what each Manhattan building IS, for the photoreal scene's facades.

    python3 drone/sim/tools/fetch_manhattan_building_attrs.py

Writes drone/sim/godot/assets/manhattan/building_attrs.json, a side file to
buildings.json (which this never touches: its rects and heights are what the
sim flies against). The building shader and rooftop kit
(scripts/manhattan_buildings.gd) read it to pick a facade style: a brownstone,
a walk-up with fire escapes, pre-war masonry, a glass office tower.

Sources, both NYC Open Data (public domain):
  * Building Footprints (5zhs-2jue): construction_year and base_bbl, per
    footprint. The same records fetch_manhattan.py boxes, so the same query.
  * PLUTO (64uk-42ks), per tax lot (BBL): bldgclass (A/B one- and two-family,
    C walk-up, D elevator apartments, O office, K retail, S mixed ...),
    numfloors and retailarea (a shop on the ground floor).

MATCHING. buildings.json keeps no BIN, so each footprint is boxed again
exactly as fetch_manhattan.to_buildings does and matched on the box. The key is
the box centre on a 0.05 m grid, computed in integers from the 0.1 m-rounded
corner and size (2*x0 + w, in units of 0.05 m), because the centre of a
0.1 m-rounded box is always a multiple of 0.05 m and so never sits on a
rounding boundary, in Python or in Godot's float32. Footprints are re-fetched
live, so a few edited since buildings.json was baked will not match; those
buildings fall back to a style from their height and footprint alone.

Output: {"fields": [...], "rows": [kx, ky, year, floors, cls, retail, ...]}
flat, one row of len(fields) ints per box. party: bits of the faces
against a neighbour (party_faces). cls is the PLUTO class letter as
0..25 (A = 0), -1 unknown. year/floors 0 = unknown.

The raw responses are cached in assets/manhattan/_cache/ (gitignored); pass
--refresh to fetch again.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fetch_manhattan as fm  # noqa: E402  (same projection, regions and boxing)

ASSETS = HERE.parent / "godot" / "assets" / "manhattan"
CACHE = ASSETS / "_cache"
PLUTO = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
FIELDS = ["kx", "ky", "year", "floors", "cls", "retail", "office", "res", "party"]


def _get(url: str, params: dict) -> list:
    q = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{q}", timeout=300) as r:
        return json.load(r)


def fetch_footprints(refresh: bool) -> list[dict]:
    path = CACHE / "footprints_attrs.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    lat_n, lon_w, lat_s, lon_e = fm.REGIONS["manhattan"]
    where = (f"within_box(the_geom, {lat_n}, {lon_w}, {lat_s}, {lon_e})"
             f" AND starts_with(base_bbl, '1') AND height_roof > 0.0")
    out: list[dict] = []
    while True:
        page = _get(fm.DATASET, {"$select": "the_geom,height_roof,construction_year,base_bbl,bin",
                                 "$where": where, "$limit": fm.PAGE, "$offset": len(out), "$order": "bin"})
        out.extend(page)
        print(f"  footprints {len(out)}", file=sys.stderr)
        if len(page) < fm.PAGE:
            break
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def fetch_pluto(refresh: bool) -> dict[str, dict]:
    path = CACHE / "pluto_mn.json"
    if path.exists() and not refresh:
        rows = json.loads(path.read_text())
    else:
        rows = []
        while True:
            page = _get(PLUTO, {"$select": "bbl,bldgclass,numfloors,yearbuilt,retailarea,officearea,resarea",
                                "$where": "borough='MN'", "$limit": 20000, "$offset": len(rows), "$order": "bbl"})
            rows.extend(page)
            print(f"  pluto {len(rows)}", file=sys.stderr)
            if len(page) < 20000:
                break
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
    # PLUTO's bbl is "1004780007.00000000"; the footprints' base_bbl "1004780007".
    return {str(r["bbl"]).split(".")[0]: r for r in rows}


def party_faces(boxes: list[list]) -> dict[tuple[int, int], int]:
    """Which faces of each box are against a neighbour (a party wall).

    Bits: 1 east (x0 + w), 2 west (x0), 4 north (y0 + h), 8 south (y0).
    A face counts when another box's opposite edge lies within 1.5 m of it and
    the two overlap along it by over half the face (or 8 m). Manhattan's lot-
    line walls carry no windows, so these are drawn as blank brick: the bare
    side wall a walk-up shows above its shorter neighbour is half of what makes
    a block read as New York rather than a grid of identical boxes.
    """
    cell = 50.0
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (x0, y0, w, h) in enumerate(boxes):
        for gx in range(int(x0 // cell), int((x0 + w) // cell) + 1):
            for gy in range(int(y0 // cell), int((y0 + h) // cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    out: dict[tuple[int, int], int] = {}
    tol = 1.5
    for i, (x0, y0, w, h) in enumerate(boxes):
        bits = 0
        seen = set()
        for gx in range(int((x0 - tol) // cell), int((x0 + w + tol) // cell) + 1):
            for gy in range(int((y0 - tol) // cell), int((y0 + h + tol) // cell) + 1):
                for j in grid.get((gx, gy), ()):
                    if j == i or j in seen:
                        continue
                    seen.add(j)
                    a0, b0, aw, bh = boxes[j]
                    ov_y = min(y0 + h, b0 + bh) - max(y0, b0)
                    ov_x = min(x0 + w, a0 + aw) - max(x0, a0)
                    if ov_y > min(0.5 * h, 8.0):
                        if abs(a0 - (x0 + w)) < tol or (a0 < x0 + w - tol < a0 + aw and a0 > x0 + w * 0.5):
                            bits |= 1
                        if abs(a0 + aw - x0) < tol or (a0 < x0 + tol < a0 + aw and a0 + aw < x0 + w * 0.5):
                            bits |= 2
                    if ov_x > min(0.5 * w, 8.0):
                        if abs(b0 - (y0 + h)) < tol or (b0 < y0 + h - tol < b0 + bh and b0 > y0 + h * 0.5):
                            bits |= 4
                        if abs(b0 + bh - y0) < tol or (b0 < y0 + tol < b0 + bh and b0 + bh < y0 + h * 0.5):
                            bits |= 8
        out[key(boxes[i])] = bits
    return out


def key(r: list) -> tuple[int, int]:
    x0, y0, w, h = (int(round(v * 10.0)) for v in r)
    return (2 * x0 + w, 2 * y0 + h)


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    bdoc = json.loads((ASSETS / "buildings.json").read_text())
    wanted = {key(b["r"]) for b in bdoc["buildings"]}
    pluto = fetch_pluto(a.refresh)
    feet = fetch_footprints(a.refresh)
    party = party_faces([b["r"] for b in bdoc["buildings"]])
    # Every box gets a row (party walls need no lot data); unmatched ones keep
    # year/floors 0 and class -1.
    rows: dict[tuple[int, int], list[int]] = {k: [k[0], k[1], 0, 0, -1, 0, 0, 0, party.get(k, 0)] for k in wanted}
    for rec in feet:
        lot = pluto.get(str(rec.get("base_bbl") or ""), {})
        year = int(_num(rec.get("construction_year")))
        if year < 1700 or year > 2030:
            year = int(_num(lot.get("yearbuilt")))
            if year < 1700 or year > 2030:
                year = 0
        cls_s = str(lot.get("bldgclass") or "")
        cls = ord(cls_s[0]) - 65 if cls_s and "A" <= cls_s[0] <= "Z" else -1
        # numfloors is per LOT: right for the one building most lots hold, too
        # many storeys for a garage behind a tower. The shader caps the storey
        # count by the building's own height, so an over-count only means
        # shorter storeys on a few back buildings.
        floors = int(round(_num(lot.get("numfloors"))))
        retail = 1 if _num(lot.get("retailarea")) > 0 else 0
        office = 1 if _num(lot.get("officearea")) > 0 else 0
        res = 1 if _num(lot.get("resarea")) > 0 else 0
        for b in fm.to_buildings([{"the_geom": rec.get("the_geom"), "height_roof": rec.get("height_roof")}]):
            k = key(b["r"])
            if k in wanted:
                rows[k] = [k[0], k[1], year, floors, cls, retail, office, res, party.get(k, 0)]
    flat = [v for k in sorted(rows) for v in rows[k]]
    doc = {
        "source": "NYC Open Data: Building Footprints (5zhs-2jue) construction_year; "
                  "PLUTO (64uk-42ks) bldgclass, numfloors, retail/office/residential area. Public domain.",
        "made_by": "drone/sim/tools/fetch_manhattan_building_attrs.py",
        "key": "box centre in the dataset frame of buildings.json, in 0.05 m units: (2*x0 + w, 2*y0 + h) "
               "with x0, y0, w, h the record's r[] in 0.1 m",
        "fields": FIELDS,
        "count": len(rows),
        "rows": flat,
    }
    out = ASSETS / "building_attrs.json"
    out.write_text(json.dumps(doc, separators=(",", ":")))
    n = len(bdoc["buildings"])
    yrs = sum(1 for r in rows.values() if r[2] > 0)
    pw = sum(1 for r in rows.values() if r[8])
    cl = sum(1 for r in rows.values() if r[4] >= 0)
    print(f"{len(rows)} distinct boxes ({n} records); year {yrs}, class {cl}, party {pw} -> {out}"
          f" ({out.stat().st_size / 1e6:.1f} MB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
