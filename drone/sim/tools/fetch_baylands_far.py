#!/usr/bin/env python3
"""The land and bay past Baylands Park's detailed 5 x 4.8 km, out to ~60 km:
the horizon a drone sees (scripts/baylands_far.gd draws it).

Run on a machine with network and memory (not a laptop):

    uv run --no-project --with rasterio --with pyproj --with numpy --with pillow \\
        --with requests --with scipy python drone/sim/tools/fetch_baylands_far.py \\
        --work ~/bay/bl-sky/work --out drone/sim/godot/assets/baylands [--naip ~/bay/out/ortho.jpg]

FRAME. Both grids are in the TRUE local tangent plane of the scene's datum
(azimuthal equidistant, 37.4125 N 121.9970 W, WGS84): every point sits at its
real geodesic distance and bearing from the park, so a ridge's bearing and
elevation angle are right. Inside the detailed region this agrees with the
scene's own flat-earth frame (env_baylands.geo_to_sim) to ~3 m. Heights are
metres above NAVD88, like the lidar. Earth curvature is NOT baked in: the
shader drops each vertex by d^2 (1 - k) / 2R from the camera (k = 0.13,
standard refraction), which is exact for any camera position.

Grids (row 0 = north, posts at cell centres; int16 decimetres, zlib):
  near  +-16 km at 32 m (1000 x 1000)   far_near.zi16
  wide  +-64 km at 128 m (1000 x 1000)  far_wide.zi16
Imagery, the same boxes, 2048 px: far_<near|wide>_<dry|green>.jpg
Water mask (Sentinel-2 scene classification, majority over the dry scenes):
  far_<near|wide>_water.png (L8, 255 = water: the bay, sloughs, salt ponds)

Sources (all redistributable; rows in assets/baylands/CREDITS.md):
  * USGS 3DEP 1 arc-second DEM (public domain), tiles n38w122 n38w123
    n37w122 n37w123, from the USGS staged-products bucket.
  * Copernicus Sentinel-2 L2A (Collection 1) from Earth Search (AWS open
    data): the TCI true-colour product and the SCL classification. Median of
    the clearest scenes, per pixel, clouds and shadows masked by SCL.
    dry = July-September (gold hills), green = February-March, 2023-2025.
    "Contains modified Copernicus Sentinel data 2023-2025".
  * OpenStreetMap buildings (ODbL) past the region: every one to +-30 km,
    the tall ones (>= 30 m) and Lick Observatory's to +-62 km, as oriented
    boxes: far_city.zbin (>= 14 m or >= 2500 m2, committed) and
    _sky/far_city_all.zbin (all, gitignored; --all-out).
  * Optional --naip: the scene's own NAIP ortho; the Sentinel colours are
    fitted (per channel, linear) to it over the detailed region so the two
    meet without a colour step at the region's edge.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import zlib
from pathlib import Path

import numpy as np
import requests

DATUM = (37.4125, -121.9970)
AEQD = f"+proj=aeqd +lat_0={DATUM[0]} +lon_0={DATUM[1]} +datum=WGS84 +units=m +no_defs"
REGION = (-2500.0, -2400.0, 2500.0, 2400.0)       # env_baylands.BOUNDS (e0, n0, e1, n1)
GRIDS = {"near": (16000.0, 1000), "wide": (64000.0, 1000)}   # half-size m, posts
IMG_PX = 2048
DEM_URL = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/TIFF/current/{t}/USGS_1_{t}.tif"
DEM_TILES = ["n38w122", "n38w123", "n37w122", "n37w123"]
STAC = "https://earth-search.aws.element84.com/v1/search"
SEASONS = {
    "dry": [f"{y}-07-01T00:00:00Z/{y}-09-30T23:59:59Z" for y in (2023, 2024, 2025)],
    "green": [f"{y}-02-01T00:00:00Z/{y}-03-31T23:59:59Z" for y in (2023, 2024, 2025)],
}
PER_TILE = {"dry": 7, "green": 8}
GOOD_SCL = (4, 5, 6, 7, 11)       # vegetation, bare, water, unclassified, snow


def transform_for(half: float, n: int):
    from rasterio.transform import from_origin
    res = 2 * half / n
    return from_origin(-half, half, res, res)


def wgs_bbox(half: float) -> list[float]:
    from pyproj import Transformer
    t = Transformer.from_crs(AEQD, "EPSG:4326", always_xy=True)
    xs, ys = [], []
    for e in np.linspace(-half, half, 9):
        for n in (-half, half):
            for a, b in ((e, n), (n, e)):
                lon, lat = t.transform(a, b)
                xs.append(lon)
                ys.append(lat)
    return [min(xs), min(ys), max(xs), max(ys)]


def step_dem(work: Path, out: Path) -> dict:
    import rasterio
    from rasterio.warp import reproject, Resampling
    meta = {}
    srcs = []
    for t in DEM_TILES:
        p = work / f"USGS_1_{t}.tif"
        if not p.exists():
            print("download", t, flush=True)
            r = requests.get(DEM_URL.format(t=t), timeout=600)
            r.raise_for_status()
            p.write_bytes(r.content)
        srcs.append(p)
    for name, (half, n) in GRIDS.items():
        dst = np.full((n, n), np.nan, np.float32)
        for p in srcs:
            with rasterio.open(p) as s:
                tmp = np.full((n, n), np.nan, np.float32)
                reproject(rasterio.band(s, 1), tmp, dst_transform=transform_for(half, n), dst_crs=AEQD,
                          src_nodata=s.nodata, dst_nodata=np.nan,
                          resampling=Resampling.bilinear if name == "near" else Resampling.average)
                ok = np.isfinite(tmp)
                dst[ok] = tmp[ok]
        miss = ~np.isfinite(dst)
        if miss.any():
            print(name, "dem gaps", int(miss.sum()), "-> 0", flush=True)
            dst[miss] = 0.0
        np.save(work / f"dem_{name}.npy", dst)
        meta[name] = {"min": float(dst.min()), "max": float(dst.max())}
        print(name, "dem", meta[name], flush=True)
    return meta


def stac_items(bbox, season: str) -> list[dict]:
    items = []
    for dtr in SEASONS[season]:
        body = {"collections": ["sentinel-2-c1-l2a"], "bbox": bbox, "datetime": dtr, "limit": 200,
                "query": {"eo:cloud_cover": {"lt": 20 if season == "green" else 5}}}
        r = requests.post(STAC, json=body, timeout=120)
        r.raise_for_status()
        items += r.json()["features"]
    by_tile: dict[str, list] = {}
    for it in items:
        tile = it["properties"].get("grid:code") or it["id"].split("_")[1]
        by_tile.setdefault(tile, []).append(it)
    keep = []
    for tile, its in sorted(by_tile.items()):
        # A tile on the edge of an orbit's swath is mostly empty and so
        # "cloud-free": rank by cloud AND how much of the tile has data.
        its.sort(key=lambda f: f["properties"]["eo:cloud_cover"]
                 + 0.3 * float(f["properties"].get("s2:nodata_pixel_percentage", 0.0)))
        keep += its[:PER_TILE[season]]
        print(season, tile, len(its), "scenes; using", [f["id"][4:24] for f in its[:PER_TILE[season]]], flush=True)
    return keep


def composite(items: list[dict], half: float, n_px: int, overview: int | None, dem: np.ndarray | None = None):
    """Per-pixel median of the masked TCI over items -> (rgb uint8, water frac)."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    tr = transform_for(half, n_px)
    stack = []
    water = np.zeros((n_px, n_px), np.float32)
    valid_n = np.zeros((n_px, n_px), np.float32)
    opts = {"OVERVIEW_LEVEL": overview} if overview is not None else {}
    for it in items:
        try:
            rgb = np.zeros((3, n_px, n_px), np.uint8)
            with rasterio.open(it["assets"]["visual"]["href"], **opts) as s:
                for b in range(3):
                    reproject(rasterio.band(s, b + 1), rgb[b], dst_transform=tr, dst_crs=AEQD,
                              src_nodata=0, dst_nodata=0, resampling=Resampling.average)
            scl = np.zeros((n_px, n_px), np.uint8)
            with rasterio.open(it["assets"]["scl"]["href"]) as s:
                reproject(rasterio.band(s, 1), scl, dst_transform=tr, dst_crs=AEQD,
                          src_nodata=0, dst_nodata=0, resampling=Resampling.nearest)
        except Exception as e:      # a flaky read: skip the scene
            print("skip", it["id"], e, flush=True)
            continue
        good = np.isin(scl, GOOD_SCL) & (rgb.sum(0) > 0)
        rgbf = rgb.astype(np.float32)
        rgbf[:, ~good] = np.nan
        stack.append(rgbf)
        water += (scl == 6) & good
        valid_n += good
        print("  +", it["id"][4:24], f"{good.mean():.2f} valid", flush=True)
    arr = np.stack(stack)                      # k, 3, h, w
    med = np.nanmedian(arr, axis=0)
    # Holes no scene saw clearly: the plain mean of everything, then the neighbours.
    hole = ~np.isfinite(med[0])
    if hole.any() and dem is not None:
        # Open sea no clear scene covered (the Pacific under its summer fog):
        # the median colour of the water that was seen.
        from PIL import Image
        d = np.asarray(Image.fromarray(dem).resize((n_px, n_px), Image.BILINEAR))
        wat_seen = (water / np.maximum(valid_n, 1.0) > 0.5) & np.isfinite(med[0])
        sea = hole & (d <= 0.5)
        if wat_seen.any():
            for b in range(3):
                med[b][sea] = np.median(med[b][wat_seen])
    hole = ~np.isfinite(med[0])
    if hole.any():
        from scipy import ndimage
        for b in range(3):
            ch = med[b]
            idx = ndimage.distance_transform_edt(~np.isfinite(ch), return_distances=False, return_indices=True)
            med[b] = ch[tuple(idx)]
    return med, water / np.maximum(valid_n, 1.0)


def naip_fit(med_near: np.ndarray, naip: Path) -> list[tuple[float, float]]:
    """Per-channel gain/offset taking Sentinel-2 TCI to the NAIP ortho, fitted
    over the detailed region's land."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    im = np.asarray(Image.open(naip).convert("RGB"), np.float32)     # REGION, row 0 north
    half, _ = GRIDS["near"]
    res = 2 * half / IMG_PX
    e0, n0, e1, n1 = REGION
    c0, c1 = int((e0 + half) / res) + 1, int((e1 + half) / res) - 1
    r0, r1 = int((half - n1) / res) + 1, int((half - n0) / res) - 1
    h, w = im.shape[:2]
    out = []
    s2 = med_near[:, r0:r1, c0:c1]
    # NAIP box-averaged onto the same cells.
    ys = ((half - (np.arange(r0, r1) + 0.5) * res) - n0) / (n1 - n0)
    xs = (((np.arange(c0, c1) + 0.5) * res - half) - e0) / (e1 - e0)
    k = max(1, int(res / ((e1 - e0) / w)))
    from scipy import ndimage
    fits = []
    for b in range(3):
        sm = ndimage.uniform_filter(im[:, :, b], k)
        yy = np.clip(((1 - ys) * h).astype(int), 0, h - 1)
        xx = np.clip((xs * w).astype(int), 0, w - 1)
        nb = sm[yy][:, xx]
        a = s2[b].ravel()
        t = nb.ravel()
        ok = np.isfinite(a) & (t > 8) & (a > 8)
        g, o = np.polyfit(a[ok], t[ok], 1)
        fits.append((float(g), float(o)))
    print("naip fit", fits, flush=True)
    return fits


def step_imagery(work: Path, out: Path, naip: Path | None) -> dict:
    from PIL import Image
    meta = {}
    fits = None
    for season in ("dry", "green"):
        for name in ("near", "wide"):
            half, _ = GRIDS[name]
            cache = work / f"s2_{name}_{season}.npz"
            if cache.exists():
                z = np.load(cache)
                med, wat = z["med"], z["wat"]
            else:
                items = stac_items(wgs_bbox(half), season)
                dem = np.load(work / f"dem_{name}.npy") if (work / f"dem_{name}.npy").exists() else None
                med, wat = composite(items, half, IMG_PX, None if name == "near" else 1, dem)
                np.savez(cache, med=med, wat=wat)
            if naip is not None and fits is None and name == "near" and season == "dry":
                fits = naip_fit(med, naip)
            rgb = med.copy()
            if fits:
                for b in range(3):
                    rgb[b] = rgb[b] * fits[b][0] + fits[b][1]
            img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8).transpose(1, 2, 0))
            q = 88 if name == "near" else 86
            if season == "green":
                img = img.resize((IMG_PX // 2, IMG_PX // 2), Image.LANCZOS)
            img.save(out / f"far_{name}_{season}.jpg", quality=q, optimize=True)
            if season == "dry":
                m = (wat > 0.5).astype(np.uint8) * 255
                Image.fromarray(m).resize((IMG_PX // 2, IMG_PX // 2), Image.BILINEAR).save(
                    out / f"far_{name}_water.png", optimize=True)
                np.save(work / f"water_{name}.npy", wat)
            print("wrote", name, season, flush=True)
    meta["naip_fit"] = fits
    return meta


def _zi16(path: Path, arr: np.ndarray) -> None:
    q = np.clip(np.round(arr * 10.0), -32768, 32767).astype("<i2")
    path.write_bytes(zlib.compress(q.tobytes(), 9))


def step_grids(work: Path, out: Path) -> None:
    """Heights out: water cells held at or below 1 m (the shader raises them to
    the tide); the rest as measured."""
    for name, (half, n) in GRIDS.items():
        dem = np.load(work / f"dem_{name}.npy")
        wp = work / f"water_{name}.npy"
        if wp.exists():
            from PIL import Image
            w = np.asarray(Image.fromarray((np.load(wp) * 255).astype(np.uint8)).resize((n, n), Image.BILINEAR)) > 160
            dem = np.where(w & (dem < 2.0), np.minimum(dem, 0.0), dem)
        _zi16(out / f"far_{name}.zi16", dem)


# --------------------------------------------------------------------- city
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
CITY_ALL_HALF = 30000.0      # every building out to +-30 km (the low skyline)
CITY_TALL_HALF = 62000.0     # tall ones (>= 30 m or >= 8 levels) and named sites to +-62 km
KINDS = {"house": 0, "detached": 0, "residential": 0, "semidetached_house": 0, "terrace": 0, "garage": 6,
         "garages": 6, "carport": 6, "shed": 6, "roof": 6, "apartments": 1, "dormitory": 1, "hotel": 1,
         "commercial": 2, "office": 2, "retail": 2, "supermarket": 2, "school": 2, "university": 2,
         "college": 2, "hospital": 2, "civic": 2, "public": 2, "church": 2,
         "industrial": 3, "warehouse": 3, "manufacture": 3, "hangar": 3, "data_center": 3}
DEFAULT_H = {0: 6.0, 1: 12.0, 2: 9.0, 3: 9.0, 4: 7.0, 5: 12.0, 6: 3.0}


def overpass(q: str, tries: int = 6) -> dict:
    import time
    for t in range(tries):
        url = OVERPASS[t % len(OVERPASS)]
        try:
            r = requests.post(url, data={"data": q}, timeout=600, headers={"User-Agent": "eco-sim-fetch/1.0"})
            if r.status_code == 200:
                return r.json()
            print("overpass", r.status_code, "retry", flush=True)
        except Exception as e:
            print("overpass", e, "retry", flush=True)
        time.sleep(20 * (t + 1))
    raise RuntimeError("overpass failed")


def _num(v):
    try:
        return float(str(v).split(";")[0].replace("m", "").replace("'", "").strip())
    except ValueError:
        return None


def _pbf_buildings(pbf: Path) -> list[dict]:
    """Building ways from a Geofabrik extract (download.geofabrik.de, ODbL;
    norcal-latest.osm.pbf), in Overpass's `out tags geom` shape. Used when
    present: the public Overpass servers time out on 600k buildings."""
    import osmium
    lon0, lat0, lon1, lat1 = wgs_bbox(CITY_TALL_HALF)
    a0, b0, a1, b1 = wgs_bbox(CITY_ALL_HALF)
    out = []
    fp = osmium.FileProcessor(str(pbf)).with_locations().with_filter(osmium.filter.KeyFilter("building"))
    for o in fp:
        if not o.is_way():
            continue
        try:
            g = [{"lat": n.location.lat, "lon": n.location.lon} for n in o.nodes]
        except osmium.InvalidLocationError:
            continue
        if not g:
            continue
        la, lo = g[0]["lat"], g[0]["lon"]
        if not (lat0 <= la <= lat1 and lon0 <= lo <= lon1):
            continue
        t = dict(o.tags)
        inner = a0 <= lo <= a1 and b0 <= la <= b1
        tall = "height" in t or "building:levels" in t
        if not inner and not tall:
            continue
        out.append({"id": o.id, "tags": t, "geometry": g})
    print("pbf buildings", len(out), flush=True)
    return out


def step_bridges(work: Path, out: Path) -> None:
    """The bay's two long bridges in view (Dumbarton, San Mateo-Hayward) as
    deck polylines: far_bridges.json [{name, pts: [[e, n], ...], tags}]."""
    import osmium
    from pyproj import Transformer
    to_xy = Transformer.from_crs("EPSG:4326", AEQD, always_xy=True)
    pbf = work / "norcal-latest.osm.pbf"
    res = []
    fp = osmium.FileProcessor(str(pbf)).with_locations().with_filter(osmium.filter.KeyFilter("bridge"))
    for o in fp:
        if not o.is_way():
            continue
        t = dict(o.tags)
        nm = t.get("name", "") + " " + t.get("bridge:name", "")
        if not any(k in nm for k in ("Dumbarton", "San Mateo")):
            continue
        if t.get("highway") not in ("motorway", "trunk", "primary") and t.get("railway") is None:
            continue
        try:
            ll = [(n.location.lon, n.location.lat) for n in o.nodes]
        except osmium.InvalidLocationError:
            continue
        xs, ys = to_xy.transform([a for a, b in ll], [b for a, b in ll])
        res.append({"name": nm.strip(), "id": o.id, "pts": [[round(x, 1), round(y, 1)] for x, y in zip(xs, ys)],
                    "tags": {k: v for k, v in t.items() if k in ("highway", "railway", "layer", "bridge",
                             "bridge:structure", "maxheight", "lanes", "oneway", "seamark:type")}})
    (out / "far_bridges.json").write_text(json.dumps(res))
    print("bridges", [(r["name"], len(r["pts"]), r["tags"]) for r in res], flush=True)


def step_city(work: Path, out: Path, all_out: Path | None) -> None:
    """OSM buildings past the detailed region as oriented boxes (minimum-area
    rectangle + height: OSM height, else levels x 3.2 m + 1, else a default by
    kind), standing on the DEM. Lean set committed; every building gitignored."""
    import shapely
    from pyproj import Transformer
    from scipy import ndimage
    to_xy = Transformer.from_crs("EPSG:4326", AEQD, always_xy=True)
    cache = work / "osm_city.json"
    pbf = work / "norcal-latest.osm.pbf"
    if cache.exists():
        ways = json.loads(cache.read_text())
    elif pbf.exists():
        ways = _pbf_buildings(pbf)
        cache.write_text(json.dumps(ways))
    else:
        ways = []
        lon0, lat0, lon1, lat1 = wgs_bbox(CITY_ALL_HALF)
        steps = 6
        for i in range(steps):
            for j in range(steps):
                a = lat0 + (lat1 - lat0) * i / steps
                b = lon0 + (lon1 - lon0) * j / steps
                c = lat0 + (lat1 - lat0) * (i + 1) / steps
                d = lon0 + (lon1 - lon0) * (j + 1) / steps
                q = f"[out:json][timeout:500];way[building]({a},{b},{c},{d});out tags geom qt;"
                els = overpass(q)["elements"]
                ways += els
                print("city tile", i, j, len(els), flush=True)
        lon0, lat0, lon1, lat1 = wgs_bbox(CITY_TALL_HALF)
        q = (f"[out:json][timeout:500];(way[building][height]({lat0},{lon0},{lat1},{lon1});"
             f"way[building][\"building:levels\"]({lat0},{lon0},{lat1},{lon1});"
             f"way[building](37.336,-121.650,37.348,-121.630););out tags geom qt;")
        els = overpass(q)["elements"]
        print("tall", len(els), flush=True)
        ways += els
        cache.write_text(json.dumps(ways))
    dem_w = np.load(work / "dem_wide.npy")
    half_w = GRIDS["wide"][0]
    n_w = dem_w.shape[0]
    seen = set()
    rows = []
    e0, n0, e1, n1 = REGION
    for wy in ways:
        if wy["id"] in seen or "geometry" not in wy:
            continue
        seen.add(wy["id"])
        t = wy.get("tags", {})
        g = wy["geometry"]
        if len(g) < 4:
            continue
        xs, ys = to_xy.transform([p["lon"] for p in g], [p["lat"] for p in g])
        cx, cy = float(np.mean(xs)), float(np.mean(ys))
        if e0 <= cx <= e1 and n0 <= cy <= n1:
            continue                    # the region draws its own
        kind = KINDS.get(t.get("building", ""), 4)
        if t.get("man_made") == "observatory" or t.get("roof:shape") == "dome" or "dome" in t.get("name", "").lower():
            kind = 5
        h = _num(t.get("height"))
        if h is None:
            lv = _num(t.get("building:levels"))
            h = lv * 3.2 + 1.0 if lv is not None else DEFAULT_H[kind]
        far = max(abs(cx), abs(cy)) > CITY_ALL_HALF
        if far and h < 30.0 and kind != 5:
            continue
        poly = shapely.Polygon(list(zip(xs, ys)))
        if not poly.is_valid or poly.area < 6.0:
            continue
        r = shapely.minimum_rotated_rectangle(poly)
        c = np.array(r.exterior.coords)[:4]
        ea = c[1] - c[0]
        eb = c[2] - c[1]
        w, d = float(np.hypot(*ea)), float(np.hypot(*eb))
        yaw = math.degrees(math.atan2(ea[1], ea[0])) % 180.0
        # Base: the DEM's lowest corner, so a building on a slope is not floating.
        col = np.clip(((c[:, 0] + half_w) / (2 * half_w) * n_w).astype(int), 0, n_w - 1)
        row = np.clip(((half_w - c[:, 1]) / (2 * half_w) * n_w).astype(int), 0, n_w - 1)
        base = float(dem_w[row, col].min())
        rows.append((cx, cy, w, d, yaw, min(h, 300.0), base, kind))
    arr = np.array(rows, np.float64)
    print("city buildings", len(arr), "tall>=30", int((arr[:, 5] >= 30).sum()), flush=True)

    def write(path: Path, a: np.ndarray) -> None:
        rec = np.zeros(len(a), dtype=[("e", "<i4"), ("n", "<i4"), ("w", "<u2"), ("d", "<u2"), ("yaw", "<u2"),
                                      ("h", "<u2"), ("base", "<i2"), ("kind", "u1")])
        rec["e"] = np.round(a[:, 0] * 10)
        rec["n"] = np.round(a[:, 1] * 10)
        rec["w"] = np.clip(np.round(a[:, 2] * 10), 0, 65535)
        rec["d"] = np.clip(np.round(a[:, 3] * 10), 0, 65535)
        rec["yaw"] = np.round(a[:, 4] * 100)
        rec["h"] = np.round(a[:, 5] * 10)
        rec["base"] = np.round(a[:, 6] * 10)
        rec["kind"] = a[:, 7]
        raw = b"BFC1" + len(a).to_bytes(4, "little") + rec.tobytes()
        path.write_bytes(zlib.compress(raw, 9))
        print("wrote", path, path.stat().st_size, flush=True)

    lean = arr[(arr[:, 5] >= 14.0) | (arr[:, 2] * arr[:, 3] >= 2500.0) | (arr[:, 7] == 5)]
    write(out / "far_city.zbin", lean)
    if all_out is not None:
        all_out.mkdir(parents=True, exist_ok=True)
        write_chunks(all_out / "far_city_all.zbin", arr)


## Tiers as baylands_far.gd's CITY_TIERS: (min height, chunk m).
CITY_TIERS = [(0.0, 2000.0), (14.0, 8000.0), (35.0, 16000.0)]


def write_chunks(path: Path, a: np.ndarray) -> None:
    """Every building as ready MultiMesh buffers, chunked by tier: 'BFC2',
    u32 chunks, then per chunk u8 tier, i32 cx, i32 cy, u32 n and n x 16
    float32 (Godot's TRANSFORM_3D rows + INSTANCE_CUSTOM). Loading 800k
    instances this way takes no per-instance GDScript."""
    import struct
    e, n, w, d, yaw, h, base, kind = (a[:, i] for i in range(8))
    tier = np.where((h >= 35.0) | (kind == 5), 2, np.where(h >= 14.0, 1, 0))
    # Sheds, garages and carports (< 40 m2 or kind 6) are under a pixel past
    # the region: left out.
    keep = (w >= 1.0) & (d >= 1.0) & (h >= 1.5) & (w * d >= 40.0) & (kind != 6)
    th = np.radians(yaw)
    c, s_ = np.cos(th), np.sin(th)
    hs = (np.abs((e * 3.7 + n * 1.3).astype(np.int64)) % 97) / 97.0
    buf = np.stack([w * c, np.zeros_like(w), d * s_, e,
                    np.zeros_like(w), h + 1.0, np.zeros_like(w), base - 1.0,
                    -w * s_, np.zeros_like(w), d * c, -n,
                    kind / 8.0, hs, h / 300.0, np.ones_like(w)], axis=1).astype("<f4")
    parts = []
    nchunks = 0
    for t, (_, ch) in enumerate(CITY_TIERS):
        sel = keep & (tier == t)
        cx = np.floor(e / ch).astype(int)
        cy = np.floor(n / ch).astype(int)
        keys = sorted(set(zip(cx[sel].tolist(), cy[sel].tolist())))
        for kx, ky in keys:
            m = sel & (cx == kx) & (cy == ky)
            parts.append(struct.pack("<Biii", t, kx, ky, int(m.sum())) + buf[m].tobytes())
            nchunks += 1
    raw = b"BFC2" + struct.pack("<I", nchunks) + b"".join(parts)
    path.write_bytes(zlib.compress(raw, 6))
    print("wrote", path, path.stat().st_size, "chunks", nchunks, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--naip", default="")
    ap.add_argument("--steps", default="dem,imagery,grids,city,bridges")
    ap.add_argument("--all-out", default="", help="gitignored folder for every building (assets/baylands/_sky)")
    a = ap.parse_args(argv)
    work = Path(a.work).expanduser()
    out = Path(a.out).expanduser()
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    steps = a.steps.split(",")
    meta_p = work / "far_meta.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    if "dem" in steps:
        meta["dem"] = step_dem(work, out)
    if "imagery" in steps:
        meta["imagery"] = step_imagery(work, out, Path(a.naip).expanduser() if a.naip else None)
    if "grids" in steps:
        step_grids(work, out)
    if "city" in steps:
        step_city(work, out, Path(a.all_out).expanduser() if a.all_out else None)
    if "bridges" in steps:
        step_bridges(work, out)
    meta_p.write_text(json.dumps(meta))
    doc = {"frame": AEQD, "datum": list(DATUM), "region_enu": list(REGION),
           "grids": {k: {"half_m": h, "posts": n, "res_m": 2 * h / n, "file": f"far_{k}.zi16",
                         "encoding": "zlib int16 decimetres above NAVD88, row 0 north, posts at cell centres",
                         "dry": f"far_{k}_dry.jpg", "green": f"far_{k}_green.jpg", "water": f"far_{k}_water.png"}
                     for k, (h, n) in GRIDS.items()},
           "heights_m": meta.get("dem"), "naip_fit": (meta.get("imagery") or {}).get("naip_fit"),
           "sources": ["USGS 3DEP 1 arc-second DEM (public domain)",
                       "Contains modified Copernicus Sentinel data 2023-2025 (Sentinel-2 L2A via Earth Search)",
                       "OpenStreetMap buildings (ODbL)"],
           "city": {"lean": "far_city.zbin", "all": "_sky/far_city_all.zbin",
                    "encoding": "zlib; 'BFC1', u32 count, then records <i4 e dm, <i4 n dm, <u2 w dm, <u2 d dm, <u2 yaw 0.01 deg (of w side from east, CCW), <u2 h dm, <i2 base dm NAVD88, u1 kind"}}
    (out / "far.json").write_text(json.dumps(doc, indent=1))
    print("done", out / "far.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
