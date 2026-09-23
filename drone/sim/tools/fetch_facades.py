#!/usr/bin/env python3
"""Fetch CC0 photographic facade textures for the photoreal city envs.

Source: ambientCG (https://ambientcg.com), all assets CC0 — public domain, no
attribution required and safe to commit, unlike the Fab assets whose licence
forbids redistribution. Baked to files so the sim needs no network at run time.

WHY THIS AND NOT PROCEDURAL WINDOWS: a shader can draw a convincing window
GRID, but it cannot invent the things that make a building read as real — the
grime, the reflections, the unevenness between panes, the fact that no two
floors are quite the same. Those are photographs of actual buildings, tiled.
This is what city renderers have always done; photogrammetry is only needed
when you want THESE buildings specifically rather than buildings in general.

WHY NOT ONE TEXTURE: a city where every tower wears the same facade reads as a
repeated asset immediately. A handful of distinct materials, chosen per
building from a hash of its position, is enough to break that up.

Output: assets/facades/facade_NN.jpg (albedo) plus facades.json recording
which ambientCG asset each came from and how many metres one tile spans, which
is what lets the shader keep a storey the same height on every building.

Usage:
    python3 fetch_facades.py --out ../godot/assets/facades
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

API = "https://ambientcg.com/get?file={}_1K-JPG.zip"
UA = "eco-sim/1.0 (drone simulator; facade texture builder)"

# Chosen to span what Manhattan actually looks like from the air: glass curtain
# wall, pre-war masonry, white modernist, and a lit tower for dusk. `span_m` is
# how tall one tile of that photograph is in the real world — it sets storey
# height, so getting it roughly right is what stops a 300 m tower from looking
# like a 6-storey block scaled up.
FACADES = [
    ("Facade001", 18.0),   # dark glass curtain wall
    ("Facade006", 20.0),   # pale modernist, strong floor bands
    ("Facade018A", 16.0),  # brick with punched windows
    ("Facade002", 18.0),
    ("Facade005", 17.0),
    ("Facade013", 19.0),
    ("Facade017", 18.0),
]
# Roofs matter more here than facades do: a drone spends most of its time
# looking down at them, and the existing env draws them flat grey.
ROOF = ("Asphalt015", 12.0)


def grab(asset_id: str) -> bytes | None:
    """Download one asset and return its Color map bytes."""
    req = urllib.request.Request(API.format(asset_id), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            blob = r.read()
    except Exception as exc:                          # noqa: BLE001
        print(f"  {asset_id}: FAILED ({exc})")
        return None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            if "Color" in name and name.lower().endswith((".jpg", ".jpeg")):
                return z.read(name)
    print(f"  {asset_id}: no Color map in archive")
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    entries = []
    for i, (asset_id, span) in enumerate(FACADES):
        data = grab(asset_id)
        if data is None:
            continue
        name = f"facade_{len(entries):02d}.jpg"
        (out / name).write_bytes(data)
        entries.append({"file": name, "source": asset_id, "span_m": span})
        print(f"  {asset_id}: {len(data)/1e6:.1f} MB -> {name}")

    roof_entry = None
    data = grab(ROOF[0])
    if data is not None:
        (out / "roof.jpg").write_bytes(data)
        roof_entry = {"file": "roof.jpg", "source": ROOF[0], "span_m": ROOF[1]}
        print(f"  {ROOF[0]}: roof")

    if not entries:
        print("no facades fetched", file=sys.stderr)
        return 1

    (out / "facades.json").write_text(json.dumps({
        "licence": "CC0 1.0 (ambientCG) — public domain, safe to redistribute",
        "facades": entries,
        "roof": roof_entry,
    }, indent=1))
    print(f"wrote {out}/facades.json ({len(entries)} facades)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
