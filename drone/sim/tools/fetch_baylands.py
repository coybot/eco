#!/usr/bin/env python3
"""Fetch Baylands Park, Sunnyvale and emit the sim's photoreal baylands dataset.

Three public-domain sources, all baked to files at fetch time so the sim needs
no network at run time. Baking rather than streaming is deliberate: a demo
should not depend on a remote service answering while someone is watching, and
a committed asset is deterministic and has no per-run latency.

  * TERRAIN  — USGS 3DEP elevation (1 m LIDAR-derived), exported as a float32
    raster and written here as a 16-bit PNG heightmap plus the metre range
    needed to decode it. This is the part manhattan does not have: that env
    assumes flat ground, which is fine for an island of boxes and wrong here,
    where the levees and the rising ground toward the business park are the
    only relief a low-flying aircraft can hit.
  * IMAGERY  — USDA NAIP orthophotography (~0.6 m/px native), public domain.
    From a drone at altitude looking down at a marsh, a real orthophoto on real
    terrain *is* photorealism; there are almost no facades here to get wrong.
  * VECTORS  — OpenStreetMap (ODbL) for water bodies, levee trails and the few
    buildings. Buildings become axis-aligned rects exactly like manhattan's, so
    they drop straight into the existing occupancy grid and slab-test occlusion.

UNITS: metres throughout, as everywhere else in the sim. 3DEP is requested in
metres; nothing downstream re-converts.

Output is a local ENU frame (x=east, y=north) with its origin at the region's
south-west corner, so every coordinate is positive — same convention as
fetch_manhattan.py, whose datum choice puts Manhattan in positive north.

Usage:
    python3 fetch_baylands.py --out ../godot/assets/baylands
    python3 fetch_baylands.py --out /tmp/baylands --terrain-size 512
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

# Region: Baylands Park and the salt-pond levees north of it, which is where
# people actually fly. West edge takes in the park proper and its car park,
# east edge the water-treatment ponds, north the open bay margin, south the
# Caribbean Dr business park where the ground starts to rise.
LAT_S, LAT_N = 37.4100, 37.4400
LON_W, LON_E = -122.0300, -121.9950

# Datum at the south-west corner so the whole region is positive east/north.
DATUM_LAT, DATUM_LON = LAT_S, LON_W

ELEVATION = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
             "3DEPElevation/ImageServer/exportImage")
NAIP = ("https://imagery.nationalmap.gov/arcgis/rest/services/"
        "USGSNAIPPlus/ImageServer/exportImage")
OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_MIRROR = "https://overpass.kumi.systems/api/interpreter"
UA = "eco-sim/1.0 (drone simulator; baylands dataset builder)"


def enu(lat: float, lon: float) -> tuple[float, float]:
    """Local tangent plane, metres east/north of the datum.

    Same flat-earth approximation as fetch_manhattan.py. Over a 3 km region the
    error against a proper projection is centimetres — far below the terrain
    raster's own 3 m post spacing.
    """
    m_per_deg_lat = 111132.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(DATUM_LAT))
    return ((lon - DATUM_LON) * m_per_deg_lon, (lat - DATUM_LAT) * m_per_deg_lat)


def _get(url: str, params: dict, timeout: int = 120, tries: int = 4) -> bytes:
    """GET with retries, and treat an ArcGIS JSON error body as a failure.

    Both USGS services answer a rejected request with HTTP 200 and a JSON
    error payload, so a naive fetch happily writes 156 bytes of JSON into a
    .jpg and everything downstream fails somewhere far less obvious.
    """
    full = url + "?" + urllib.parse.urlencode(params)
    last = ""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            if body[:1] == b"{":
                raise RuntimeError(json.loads(body).get("error", {}).get(
                    "details", ["service returned an error"])[0])
            return body
        except Exception as exc:                      # noqa: BLE001 - retry anything
            last = str(exc)
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"{url}: {last}")


def fetch_terrain(out_dir: Path, size: int) -> dict:
    """3DEP float32 raster -> 16-bit PNG heightmap + the range to decode it.

    A 16-bit PNG holds 65536 levels; across this region's ~35 m of relief that
    is sub-millimetre, so quantisation is irrelevant and the file is a tenth
    the size of the float raster.
    """
    from PIL import Image
    import numpy as np

    raw = _get(ELEVATION, {
        "bbox": f"{LON_W},{LAT_S},{LON_E},{LAT_N}", "bboxSR": 4326,
        "size": f"{size},{size}", "format": "tiff", "pixelType": "F32", "f": "image",
    })
    tif = out_dir / "_terrain_f32.tif"
    tif.write_bytes(raw)
    arr = np.array(Image.open(tif)).astype("float32")

    # 3DEP returns very negative sentinels for no-data over open water. Clamp to
    # sea level rather than letting them blow out the range the PNG encodes.
    arr[~np.isfinite(arr)] = 0.0
    arr[arr < -100.0] = 0.0

    lo, hi = float(arr.min()), float(arr.max())
    span = max(hi - lo, 1e-6)
    # PNG rows run north->south; the sim's north is +y, so flip once here and
    # the env can index the image directly without thinking about it again.
    norm = np.flipud((arr - lo) / span)
    Image.fromarray((norm * 65535.0).astype("uint16"), mode="I;16").save(out_dir / "terrain.png")
    tif.unlink()

    print(f"  terrain: {size}x{size}  {lo:.2f}..{hi:.2f} m")
    return {"file": "terrain.png", "size": size, "min_m": round(lo, 3), "max_m": round(hi, 3)}


def fetch_ortho(out_dir: Path, size: int, tile_px: int = 3600) -> dict:
    """NAIP orthophoto for the same bbox — the ground albedo.

    Fetched in ONE request wherever it fits. The ImageServer rejects exports
    at 4096 px but serves 3600, which over this 3.1 km region is ~0.86 m/px —
    near NAIP's ~0.6 m native and plenty for ground texture.

    Tiling is kept for regions too big for one export, but avoid it when you
    can: the server picks source scenes per request extent, so adjacent tiles
    come back with different radiometry and the join is a visible brightness
    step. A 2x2 stitch here put a seam at x=2047 eight times larger than any
    natural gradient in the scene.
    """
    from PIL import Image
    import io

    n = max(1, math.ceil(size / tile_px))
    step = size // n
    canvas = Image.new("RGB", (step * n, step * n))
    for iy in range(n):
        for ix in range(n):
            # Tile bbox in lon/lat. Image row 0 is the NORTH edge, so y runs
            # down from LAT_N while the canvas pastes top-down to match.
            lon0 = LON_W + (LON_E - LON_W) * ix / n
            lon1 = LON_W + (LON_E - LON_W) * (ix + 1) / n
            lat1 = LAT_N - (LAT_N - LAT_S) * iy / n
            lat0 = LAT_N - (LAT_N - LAT_S) * (iy + 1) / n
            raw = _get(NAIP, {
                "bbox": f"{lon0},{lat0},{lon1},{lat1}", "bboxSR": 4326,
                "size": f"{step},{step}", "format": "jpg", "f": "image",
            })
            canvas.paste(Image.open(io.BytesIO(raw)), (ix * step, iy * step))
    canvas.save(out_dir / "ortho.jpg", quality=88)
    got = (out_dir / "ortho.jpg").stat().st_size
    print(f"  ortho:   {canvas.width}x{canvas.height} ({n}x{n} tiles)  {got/1e6:.1f} MB")
    return {"file": "ortho.jpg", "size": canvas.width}


def fetch_features(e_max: float, n_max: float, cache: Path | None = None) -> dict:
    """Water, trails and buildings from OSM, as ENU geometry.

    Buildings are emitted as axis-aligned rects (the polygon's bounding box),
    matching fetch_manhattan.py: the rect never under-approximates the
    footprint, so routing stays conservative.
    """
    query = f"""
    [out:json][timeout:60];
    (
      way["natural"="water"]({LAT_S},{LON_W},{LAT_N},{LON_E});
      way["landuse"="basin"]({LAT_S},{LON_W},{LAT_N},{LON_E});
      way["highway"~"path|footway|cycleway|track"]({LAT_S},{LON_W},{LAT_N},{LON_E});
      way["building"]({LAT_S},{LON_W},{LAT_N},{LON_E});
    );
    out geom;
    """
    # Overpass 504s under load often enough that a single attempt is not
    # reliable; kumi is a mirror of the same data.
    # Overpass is the flakiest of the three sources and the only one whose
    # failure would otherwise throw away a completed terrain+ortho fetch, so
    # its raw response is cached next to the output. Delete the cache file to
    # force a refresh.
    data = None
    if cache and cache.exists():
        data = json.loads(cache.read_text())
        print("  vectors: using cached overpass response")
    last = ""
    for endpoint in ((OVERPASS, OVERPASS_MIRROR) if data is None else ()):
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    endpoint, data=urllib.parse.urlencode({"data": query}).encode(),
                    headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.load(r)
                if cache:
                    cache.write_text(json.dumps(data))
                break
            except Exception as exc:                  # noqa: BLE001 - retry anything
                last = str(exc)
                time.sleep(2 ** attempt)
        if data is not None:
            break
    if data is None:
        raise RuntimeError(f"overpass unavailable: {last}")

    water: list = []
    trails: list = []
    buildings: list = []
    for el in data.get("elements", []):
        # Overpass `out geom` emits this as "geometry", not "geom" — reading the
        # wrong key silently yields zero features rather than an error.
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        pts = [enu(p["lat"], p["lon"]) for p in geom]
        tags = el.get("tags", {})
        if tags.get("building"):
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            # Overpass returns any way that *intersects* the bbox, with its full
            # geometry, so campus buildings on the edge otherwise land partly
            # outside the region and break the all-positive ENU invariant.
            x0, y0 = max(min(xs), 0.0), max(min(ys), 0.0)
            x1, y1 = min(max(xs), e_max), min(max(ys), n_max)
            if x1 - x0 < 1.0 or y1 - y0 < 1.0:
                continue

            # Height, best source first. Only 21 of 182 buildings here carry any
            # height data at all, so the fallback decides almost every box — and
            # the error directions are not symmetric. A box that is too SHORT is
            # unsafe: routing and collision both let an aircraft through a
            # building that is really there. Too tall only costs some airspace.
            # So the fallback leans tall, split by footprint: the park's
            # restrooms and pump sheds are single-storey, while everything of
            # office size on Caribbean Dr is 3-4 storeys.
            if tags.get("height"):
                h, src = float(tags["height"]), "osm_height"
            elif tags.get("building:levels"):
                h, src = float(tags["building:levels"]) * 3.5, "osm_levels"
            else:
                h, src = (4.0, "default_small") if (x1 - x0) * (y1 - y0) < 200.0 \
                    else (12.0, "default_office")
            buildings.append({
                "r": [round(x0, 1), round(y0, 1), round(x1 - x0, 1), round(y1 - y0, 1)],
                "h": round(max(h, 3.0), 1),
                "hs": src,
                "n": tags.get("name", ""),
            })
        else:
            ring = [[round(min(max(x, 0.0), e_max), 1),
                     round(min(max(y, 0.0), n_max), 1)] for x, y in pts]
            if tags.get("natural") == "water" or tags.get("landuse") == "basin":
                water.append(ring)
            else:
                trails.append(ring)

    print(f"  vectors: {len(water)} water, {len(trails)} trails, {len(buildings)} buildings")
    return {"water": water, "trails": trails, "buildings": buildings}



def fetch_trees(e_max: float, n_max: float, px: int = 1600) -> list:
    """Find tree canopy from the imagery itself and emit discrete trees.

    OSM is not usable for this: it has 32 individual trees mapped across the
    whole region, against the hundreds actually there. The imagery does have
    them, and NAIP Plus is FOUR band — the near-infrared makes vegetation
    separable from everything else by NDVI.

    NDVI alone is not enough here, because marsh and mown grass score as high
    as tree crowns. What separates them is TEXTURE: a canopy is individual
    crowns and the shadows between them, so its local standard deviation is
    high, while grass and open water are smooth at this resolution. NDVI picks
    vegetation, local std picks the woody part of it.

    HEIGHTS ARE INFERRED, not measured. 3DEP is a bare-earth DEM, so there is
    no canopy height model to difference against it; height is estimated from
    crown radius and recorded as such. Treat a tree's height as approximate
    when deciding whether something cleared one.
    """
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    raw = _get(NAIP, {
        "bbox": f"{LON_W},{LAT_S},{LON_E},{LAT_N}", "bboxSR": 4326,
        "size": f"{px},{px}", "format": "tiff", "f": "image",
    })
    tmp = Path("/tmp/_naip4.tif")
    tmp.write_bytes(raw)
    a = np.array(Image.open(tmp))
    tmp.unlink()
    if a.ndim != 3 or a.shape[2] < 4:
        print("  trees:   NAIP returned no NIR band — skipped")
        return []

    red = a[:, :, 0].astype("f4")
    nir = a[:, :, 3].astype("f4")
    ndvi = (nir - red) / np.maximum(nir + red, 1)
    vis = a[:, :, :3].astype("f4").mean(axis=2)
    mean = ndimage.uniform_filter(vis, 7)
    sq = ndimage.uniform_filter(vis * vis, 7)
    std = np.sqrt(np.maximum(sq - mean * mean, 0))

    canopy = (ndvi > 0.20) & (std > 12.0)
    canopy = ndimage.binary_opening(canopy, np.ones((3, 3)))

    m_per_px_e = e_max / px
    m_per_px_n = n_max / px
    # One tree every ~7 m of canopy: closer than that and a row of street
    # trees becomes a solid green wall of overlapping spheres.
    step = max(2, int(round(7.0 / max(m_per_px_e, 0.1))))

    trees = []
    lbl, n_lbl = ndimage.label(canopy)
    for sl in ndimage.find_objects(lbl):
        if sl is None:
            continue
        sub = canopy[sl]
        if sub.sum() < 6:
            continue
        ys, xs = np.nonzero(sub)
        y0, x0 = sl[0].start, sl[1].start
        taken = set()
        for yy, xx in zip(ys, xs):
            key = ((yy + y0) // step, (xx + x0) // step)
            if key in taken:
                continue
            taken.add(key)
            gx, gy = xx + x0, yy + y0
            # Image row 0 is NORTH; ENU north grows the other way.
            e = (gx + 0.5) * m_per_px_e
            n = n_max - (gy + 0.5) * m_per_px_n
            # Crown radius from how much canopy is around this point.
            r = float(np.clip(2.0 + 0.6 * math.sqrt(sub.sum()), 2.0, 7.0))
            h = float(np.clip(r * 2.4, 4.0, 18.0))
            trees.append({"p": [round(e, 1), round(n, 1)],
                          "r": round(r, 1), "h": round(h, 1)})
    print(f"  trees:   {len(trees)} from NDVI+texture (heights inferred)")
    return trees


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--terrain-size", type=int, default=1024)
    ap.add_argument("--ortho-size", type=int, default=3600)
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    e1, n1 = enu(LAT_N, LON_E)
    print(f"Baylands Park, Sunnyvale — {e1:.0f} x {n1:.0f} m")

    terrain = fetch_terrain(out_dir, args.terrain_size)
    ortho = fetch_ortho(out_dir, args.ortho_size)
    features = fetch_features(e1, n1, out_dir / "_overpass_cache.json")
    trees = fetch_trees(e1, n1)

    manifest = {
        "source": "USGS 3DEP (public domain), USDA NAIP (public domain), OpenStreetMap (ODbL)",
        "units": "metres; ENU x=east y=north, origin at region SW corner",
        "datum": {"lat": DATUM_LAT, "lon": DATUM_LON},
        "region": "baylands_sunnyvale",
        "bounds_enu": [0.0, 0.0, round(e1, 1), round(n1, 1)],
        "terrain": terrain,
        "ortho": ortho,
        "trees": trees,
        "trees_note": "positions from NAIP NDVI+texture; heights INFERRED from crown radius, not measured",
        **features,
    }
    (out_dir / "baylands.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {out_dir}/baylands.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
