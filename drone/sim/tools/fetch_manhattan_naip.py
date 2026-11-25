#!/usr/bin/env python3
"""Fetch sharp USGS NAIP ground imagery for the photoreal Manhattan scene.

Source: USGS National Map, NAIP Imagery ImageServer (USDA National
Agriculture Imagery Program, public domain), ~0.3-0.6 m native.

The committed ortho.jpg (fetch_manhattan_ortho.py) is 4.5 m/px over the
island: fine from 2 km, a smear from a drone at 100 m. This bakes, all in the
DATASET frame of buildings.json (rotated 29 deg onto the street grid):

  _hi/photo_<i>_<j>.jpg   1000 m ground tiles, tile (i, j) covering east
                          [i, i+1) km and north [j, j+1) km.
                          "hi" tiles (any of Manhattan's own land: the island,
                          Roosevelt, Randall's/Wards) at 1024 px = 0.98 m/px;
                          "mid" tiles (the other shores within one tile of
                          those) at 512 px = 1.95 m/px.
  _hi/far.jpg             the whole water-mask extent (water.json, ~16 x 32
                          km: New Jersey, Brooklyn, Queens, the Bronx) at
                          8 m/px, so the city no longer sits in an empty sea.
  _hi/tiles.json          the tile list and the far image's bounds.

Every file under _hi/ is gitignored; rerun this on a fresh checkout. The sim
falls back to ortho.jpg when they are absent.

VRAM (the scene must fit a Jetson-class GPU): the sim compresses every tile
to BC1 with mipmaps, 0.67 byte/px, so 86 hi tiles are ~60 MB, the 58 mid
tiles ~10 MB, the far image ~5 MB. 1280 px (0.78 m/px) was tried first:
94 MB for the hi tiles alone put the ground ~70 MB over its budget, for a
difference that only shows below ~60 m (the streets, which matter most up
close, are drawn, not photographed). 0.5 m/px would have been 4x.

HOW EACH TILE IS MADE: the tile is rotated against the imagery, so the
request is the tile's north-up bounding box in Web Mercator (the server's
native projection, so the server does not resample twice), at a little
finer than the output, and every output pixel is inverse-mapped
(dataset metres -> lon/lat -> Mercator pixel) and read bilinearly. Inverse
mapping gets the rotation direction right by construction.

SEAMS: fetch_manhattan_ortho.py found exportImage stitching left brightness
steps on another service (each request picked its own scenes). NAIP's
ImageServer mosaics ByAttribute (a fixed, per-scene order), so a given
ground point comes back with the same value from any request covering it;
neighbouring tiles here join without a visible step. NAIP itself has steps
between its own acquisition strips; those are in the source.

    python3 fetch_manhattan_naip.py           # ~250 requests, ~15 min
    python3 fetch_manhattan_naip.py --only hi
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import io
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

SERVICE = ("https://imagery.nationalmap.gov/arcgis/rest/services/"
           "USGSNAIPImagery/ImageServer/exportImage")
UA = "eco-sim/1.0 (drone simulator; manhattan ground imagery)"
MAX_PX = 4000

DATUM_LAT = 40.7005
DATUM_LON = -74.0170
GRID_ROTATION_DEG = 29.0
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))
R_EARTH = 6378137.0

TILE_M = 1000.0
LEVELS = {"hi": 1024, "mid": 512}
FAR_RES = 8.0
#: Buildings per km2 of a land component above which it is Manhattan's own
#: (the island 820, Roosevelt 110, Randall's 70) rather than a far shore
#: with a few of the borough's buildings on it (Marble Hill: the Bronx, 4).
OWN_DENSITY = 50.0


def to_lonlat(e: np.ndarray, n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = math.radians(GRID_ROTATION_DEG)
    c, s = math.cos(t), math.sin(t)
    x = e * c + n * s
    y = -e * s + n * c
    return DATUM_LON + x / M_PER_DEG_LON, DATUM_LAT + y / M_PER_DEG_LAT


def merc(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.radians(lon) * R_EARTH
    y = np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * R_EARTH
    return x, y


def request(bbox: tuple[float, float, float, float], w: int, h: int, cache: Path) -> np.ndarray:
    key = "_".join(f"{v:.1f}" for v in bbox) + f"_{w}x{h}"
    f = cache / f"{key}.jpg"
    if f.exists():
        return np.asarray(Image.open(f).convert("RGB"))
    qs = urllib.parse.urlencode({
        "bbox": ",".join(f"{v:.3f}" for v in bbox), "bboxSR": 3857, "imageSR": 3857,
        "size": f"{w},{h}", "format": "jpg", "compressionQuality": 92,
        "bandIds": "0,1,2", "interpolation": "RSP_BilinearInterpolation", "f": "image"})
    req = urllib.request.Request(f"{SERVICE}?{qs}", headers={"User-Agent": UA})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                blob = r.read()
            img = Image.open(io.BytesIO(blob)).convert("RGB")
            if img.size != (w, h):
                raise ValueError(f"got {img.size}, asked {w}x{h}")
            f.write_bytes(blob)
            return np.asarray(img)
        except Exception as e:                        # noqa: BLE001
            if attempt == 4:
                raise
            time.sleep(4 * (attempt + 1))
            print(f"  retry {key}: {e}")
    raise RuntimeError("unreachable")


def render(e0: float, n0: float, e1: float, n1: float, W: int, H: int,
           ground_res: float, cache: Path) -> np.ndarray:
    """The dataset rect [e0,e1] x [n0,n1] as a W x H image (row 0 north)."""
    # Pixel centres of the output, in dataset metres.
    ee = e0 + (np.arange(W) + 0.5) * ((e1 - e0) / W)
    nn = n1 - (np.arange(H) + 0.5) * ((n1 - n0) / H)
    EE, NN = np.meshgrid(ee, nn)
    MX, MY = merc(*to_lonlat(EE, NN))
    pad = 8.0 * ground_res / math.cos(math.radians(DATUM_LAT))
    bx0, bx1 = MX.min() - pad, MX.max() + pad
    by0, by1 = MY.min() - pad, MY.max() + pad
    # Mercator metres per pixel for the wanted ground resolution here.
    mres = ground_res / math.cos(math.radians(DATUM_LAT))
    nx = int(math.ceil((bx1 - bx0) / mres))
    ny = int(math.ceil((by1 - by0) / mres))
    # Split into a grid of requests if it exceeds the server's limit.
    gx = int(math.ceil(nx / MAX_PX))
    gy = int(math.ceil(ny / MAX_PX))
    sx, sy = int(math.ceil(nx / gx)), int(math.ceil(ny / gy))
    mos = np.zeros((sy * gy, sx * gx, 3), np.uint8)
    for iy in range(gy):
        for ix in range(gx):
            b = (bx0 + ix * sx * mres, by1 - (iy + 1) * sy * mres,
                 bx0 + (ix + 1) * sx * mres, by1 - iy * sy * mres)
            mos[iy * sy:(iy + 1) * sy, ix * sx:(ix + 1) * sx] = request(b, sx, sy, cache)
    # Mosaic pixel coordinates (centre convention) of every output pixel.
    px = (MX - bx0) / mres - 0.5
    py = (by1 - MY) / mres - 0.5
    out = np.empty((H, W, 3), np.uint8)
    for c in range(3):
        out[..., c] = np.clip(ndimage.map_coordinates(mos[..., c].astype(np.float32), [py, px],
                                                      order=1, mode="nearest"), 0, 255)
    return out


def pick_tiles(assets: Path) -> list[dict]:
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
    own = [k for k in range(1, n + 1) if area[k] > 0 and count[k] / area[k] > OWN_DENSITY]
    mine = np.isin(lab, own)

    def frac(m: np.ndarray, i: int, j: int) -> float:
        x0 = int((i * TILE_M - wb[0]) / res)
        x1 = int(((i + 1) * TILE_M - wb[0]) / res)
        y0 = int((wb[3] - (j + 1) * TILE_M) / res)
        y1 = int((wb[3] - j * TILE_M) / res)
        s = m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
        return float(s.mean()) if s.size else 0.0

    i0, i1 = int(math.floor(wb[0] / TILE_M)), int(math.ceil(wb[2] / TILE_M))
    j0, j1 = int(math.floor(wb[1] / TILE_M)), int(math.ceil(wb[3] / TILE_M))
    hi = {(i, j) for i in range(i0, i1) for j in range(j0, j1) if frac(mine, i, j) > 0.002}
    tiles = [{"i": i, "j": j, "level": "hi", "px": LEVELS["hi"]} for (i, j) in sorted(hi)]
    for i in range(i0, i1):
        for j in range(j0, j1):
            if (i, j) in hi or frac(~water, i, j) <= 0.002:
                continue
            if any((i + di, j + dj) in hi for di in range(-1, 2) for dj in range(-1, 2)):
                tiles.append({"i": i, "j": j, "level": "mid", "px": LEVELS["mid"]})
    return tiles


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent
                                         / "godot" / "assets" / "manhattan"))
    ap.add_argument("--only", default="", help="hi | mid | far")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--list", action="store_true", help="print the tile plan and stop")
    args = ap.parse_args(argv)
    assets = Path(args.out)
    hi_dir = assets / "_hi"
    hi_dir.mkdir(exist_ok=True)
    cache = assets / "_cache" / "naip"
    cache.mkdir(parents=True, exist_ok=True)

    tiles = pick_tiles(assets)
    nh = sum(1 for t in tiles if t["level"] == "hi")
    print(f"{nh} hi tiles, {len(tiles) - nh} mid tiles")
    wb = json.loads((assets / "water.json").read_text())["bounds_enu"]
    far = {"bounds_enu": wb, "px": [int((wb[2] - wb[0]) / FAR_RES), int((wb[3] - wb[1]) / FAR_RES)]}
    (hi_dir / "tiles.json").write_text(json.dumps({
        "source": "USGS NAIP Imagery (public domain)", "tile_m": TILE_M,
        "tiles": tiles, "far": far}, indent=0))
    if args.list:
        return 0

    def job(t: dict) -> str:
        f = hi_dir / f"photo_{t['i']}_{t['j']}.jpg"
        if f.exists():
            return "cached"
        e0, n0 = t["i"] * TILE_M, t["j"] * TILE_M
        res = TILE_M / t["px"]
        img = render(e0, n0, e0 + TILE_M, n0 + TILE_M, t["px"], t["px"], res * 0.66, cache)
        Image.fromarray(img).save(f, quality=90)
        return f.name

    todo = [t for t in tiles if not args.only or t["level"] == args.only]
    if args.only in ("", "far") and not (hi_dir / "far.jpg").exists():
        img = render(wb[0], wb[1], wb[2], wb[3], far["px"][0], far["px"][1], FAR_RES * 0.85, cache)
        Image.fromarray(img).save(hi_dir / "far.jpg", quality=88)
        print("far.jpg", far["px"])
    if args.only == "far":
        return 0
    done = 0
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        for r in ex.map(job, todo):
            done += 1
            print(f"  {done}/{len(todo)} {r}          ", end="\r", flush=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
