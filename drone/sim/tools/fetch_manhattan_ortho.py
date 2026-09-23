#!/usr/bin/env python3
"""Bake a USGS orthophoto ground for the Manhattan envs.

Source: USGS National Map imagery (public domain, no API key, no usage
agreement). Baked to a file rather than streamed: a demo should not depend on
a government service answering while someone is watching, and the boxes get a
deterministic asset with no per-run latency.

TWO THINGS MAKE THIS MORE THAN A bbox FETCH:

1. The sim's Manhattan ENU frame is ROTATED 29 degrees (fetch_manhattan.py's
   GRID_ROTATION_DEG) so the avenue grid runs along the axes. Imagery arrives
   north-up, so it has to be resampled into the rotated frame or the ground
   slides sideways against the buildings as you travel uptown. This resamples
   by INVERSE mapping — for each output pixel, work out its ENU metres, then
   its lat/lon, then read the mosaic — which gets the rotation direction right
   by construction instead of by guessing a sign.

2. It mosaics from the XYZ TILE service, not exportImage. exportImage picks
   source scenes per request extent, so two adjacent requests come back with
   different radiometry and the join is a visible brightness step (a 2x2
   stitch for the baylands ortho put a seam eight times larger than any
   natural gradient in the scene). The tile service is one pre-built global
   mosaic, so tiles are radiometrically consistent and can be stitched freely.

Usage:
    python3 fetch_manhattan_ortho.py --out ../godot/assets/manhattan
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import urllib.request
from pathlib import Path

TILES = ("https://basemap.nationalmap.gov/arcgis/rest/services/"
         "USGSImageryOnly/MapServer/tile/{z}/{y}/{x}")
UA = "eco-sim/1.0 (drone simulator; manhattan ortho builder)"

# Must match fetch_manhattan.py exactly.
DATUM_LAT = 40.7005
DATUM_LON = -74.0170
GRID_ROTATION_DEG = 29.0
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))


def enu_to_lonlat(e: float, n: float) -> tuple[float, float]:
    """Inverse of fetch_manhattan.py's enu(): rotated metres -> lon/lat."""
    t = math.radians(GRID_ROTATION_DEG)
    c, s = math.cos(t), math.sin(t)
    x = e * c + n * s        # undo the +t rotation
    y = -e * s + n * c
    return (DATUM_LON + x / M_PER_DEG_LON, DATUM_LAT + y / M_PER_DEG_LAT)


def lonlat_to_tilexy(lon: float, lat: float, z: int) -> tuple[float, float]:
    """Web Mercator tile coordinates (fractional)."""
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return (x, y)


def fetch_tile(z: int, x: int, y: int, cache: Path):
    from PIL import Image
    f = cache / f"{z}_{y}_{x}.jpg"
    if f.exists():
        return Image.open(f).convert("RGB")
    url = TILES.format(z=z, y=y, x=x)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                blob = r.read()
            img = Image.open(io.BytesIO(blob)).convert("RGB")
            f.write_bytes(blob)
            return img
        except Exception:                             # noqa: BLE001
            if attempt == 2:
                return None
    return None


def main(argv: list[str] | None = None) -> int:
    from PIL import Image
    import numpy as np

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--zoom", type=int, default=15, help="tile zoom (15 ~= 3.6 m/px)")
    ap.add_argument("--width", type=int, default=1600, help="output px across ENU east")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((out / "buildings.json").read_text())
    b = manifest["bounds_enu"]          # [e0, n0, e1, n1] in rotated metres
    e0, n0, e1, n1 = (float(v) for v in b)
    span_e, span_n = e1 - e0, n1 - n0
    W = args.width
    H = max(1, int(round(W * span_n / span_e)))
    print(f"ENU rect {span_e:.0f} x {span_n:.0f} m -> {W} x {H} px "
          f"({span_e / W:.1f} m/px)")

    # Which tiles cover the rotated rect? Take its four corners.
    corners = [enu_to_lonlat(e, n) for e, n in
               ((e0, n0), (e1, n0), (e0, n1), (e1, n1))]
    txs, tys = zip(*[lonlat_to_tilexy(lo, la, args.zoom) for lo, la in corners])
    tx0, tx1 = int(math.floor(min(txs))), int(math.ceil(max(txs)))
    ty0, ty1 = int(math.floor(min(tys))), int(math.ceil(max(tys)))
    nx, ny = tx1 - tx0, ty1 - ty0
    print(f"mosaic: {nx} x {ny} = {nx * ny} tiles at z{args.zoom}")

    cache = out / "_tiles"
    cache.mkdir(exist_ok=True)
    mosaic = Image.new("RGB", (nx * 256, ny * 256))
    got = 0
    for iy in range(ny):
        for ix in range(nx):
            t = fetch_tile(args.zoom, tx0 + ix, ty0 + iy, cache)
            if t is not None:
                mosaic.paste(t, (ix * 256, iy * 256))
                got += 1
        print(f"  row {iy + 1}/{ny} ({got} tiles)", end="\r", flush=True)
    print(f"\nfetched {got}/{nx * ny} tiles")

    # Inverse-map every output pixel into the mosaic.
    mos = np.asarray(mosaic)
    ee = e0 + (np.arange(W) + 0.5) * (span_e / W)
    nn = n1 - (np.arange(H) + 0.5) * (span_n / H)      # row 0 = NORTH edge
    EE, NN = np.meshgrid(ee, nn)

    t = math.radians(GRID_ROTATION_DEG)
    c, s = math.cos(t), math.sin(t)
    X = EE * c + NN * s
    Y = -EE * s + NN * c
    lon = DATUM_LON + X / M_PER_DEG_LON
    lat = DATUM_LAT + Y / M_PER_DEG_LAT

    n_t = 2.0 ** args.zoom
    px = (lon + 180.0) / 360.0 * n_t
    lat_r = np.radians(lat)
    py = (1.0 - np.arcsinh(np.tan(lat_r)) / np.pi) / 2.0 * n_t
    cx = np.clip(((px - tx0) * 256).astype(np.int32), 0, nx * 256 - 1)
    cy = np.clip(((py - ty0) * 256).astype(np.int32), 0, ny * 256 - 1)

    Image.fromarray(mos[cy, cx]).save(out / "ortho.jpg", quality=87)
    size_mb = (out / "ortho.jpg").stat().st_size / 1e6
    print(f"wrote {out}/ortho.jpg  {W}x{H}  {size_mb:.1f} MB")

    (out / "ortho.json").write_text(json.dumps({
        "source": "USGS National Map imagery (public domain)",
        "note": "resampled into the sim's 29-degree-rotated Manhattan ENU frame",
        "bounds_enu": [e0, n0, e1, n1],
        "size_px": [W, H],
        "zoom": args.zoom,
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
