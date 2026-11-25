#!/usr/bin/env python3
"""Fetch and bake Central Park for the photoreal Manhattan scene
(scripts/central_park.gd).

Sources (all open):
  * USGS 3DEP bare-earth DEM, 3DEPElevation ImageServer (1 m lidar DEM over
    New York City). Public domain. Bare earth matters: a first-return surface
    would put the park's canopy and the buildings along its edge into the
    terrain.
  * USGS NAIP imagery, USGSNAIPImagery ImageServer (0.3 m native over NYC).
    Public domain.
  * OpenStreetMap via Overpass: the park outline, its paths and drives, the
    lakes, ballfields, bare rock and named features. (c) OpenStreetMap
    contributors, ODbL 1.0.

Everything is baked in the DATASET frame of buildings.json (metres east/north
of the Battery Park datum, turned 29 deg onto the street grid; see
fetch_manhattan.py). The park's walls are almost exactly axis-aligned in that
frame, so every raster here is a plain rectangle, row 0 north.

Outputs in assets/manhattan/:
  central_park.json        committed. Rect, park outline, the lakes (outline +
                           surface level), landmarks and drives, levels.
  central_park_height.zi16  committed. Terrain RELATIVE TO STREET LEVEL at
                           HEIGHT_RES, int16 centimetres, zlib (header in
                           the json). 0 at the walls.
  _park/photo.jpg          gitignored. NAIP at PHOTO_RES over the rect.
  _park/cover.png          gitignored. RGBA cover fields at COVER_RES:
                           R paved-way signed distance, G water signed
                           distance (both 128 + 8 * metres, + inside),
                           B bare rock 0..255, A lawn kind (255 mown lawn,
                           140 meadow / rough, 0 woodland floor).
  _park/cache/             raw downloads.

    python3 fetch_central_park.py      # ~30 requests, ~2 min; re-runs bake
                                       # from _park/cache/ in ~10 s
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "godot" / "assets" / "manhattan"

UA = "eco-sim/1.0 (drone simulator; central park terrain)"
DEM_SERVICE = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
               "3DEPElevation/ImageServer/exportImage")
NAIP_SERVICE = ("https://imagery.nationalmap.gov/arcgis/rest/services/"
                "USGSNAIPImagery/ImageServer/exportImage")
OVERPASS = ("https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter")
MAX_PX = 4000

# Must match fetch_manhattan.py exactly.
DATUM_LAT = 40.7005
DATUM_LON = -74.0170
GRID_ROTATION_DEG = 29.0
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))
R_EARTH = 6378137.0

#: Geographic box that holds the park and its four bounding streets.
GEO_BOX = (40.7610, -73.9850, 40.8030, -73.9465)   # S, W, N, E
#: Margin of the baked rasters past the park outline, metres. Covers the
#: perimeter avenues, whose DEM is the street level the park is measured from.
MARGIN = 40.0
HEIGHT_RES = 2.0
PHOTO_RES = 0.5
COVER_RES = 1.0
#: The terrain is ramped to exactly street level over this many metres inside
#: the wall, so it meets the scene's flat streets (y = 0) with no step.
WALL_RAMP = 14.0
#: Street level is sampled on the avenue this far outside the outline: past
#: the sidewalk, on the roadbed.
STREET_OFFSET = 14.0


# ----------------------------------------------------------------- frames
def to_dataset(lat, lon):
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)
    x = (lon - DATUM_LON) * M_PER_DEG_LON
    y = (lat - DATUM_LAT) * M_PER_DEG_LAT
    t = math.radians(GRID_ROTATION_DEG)
    return x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t)


def to_lonlat(e, n):
    t = math.radians(GRID_ROTATION_DEG)
    c, s = math.cos(t), math.sin(t)
    x = e * c + n * s
    y = -e * s + n * c
    return DATUM_LON + x / M_PER_DEG_LON, DATUM_LAT + y / M_PER_DEG_LAT


def merc(lon, lat):
    x = np.radians(lon) * R_EARTH
    y = np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * R_EARTH
    return x, y


# ----------------------------------------------------------------- fetch
def _get(url: str, data: bytes | None = None, timeout: int = 240) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                        # noqa: BLE001
            if attempt == 4:
                raise
            print(f"  retry ({e})")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def fetch_osm(cache: Path) -> dict:
    f = cache / "osm.json"
    if f.exists():
        return json.loads(f.read_text())
    s, w, n, e = GEO_BOX
    bb = f"({s},{w},{n},{e})"
    q = f"""[out:json][timeout:180];
(
  way["leisure"="park"]["name"="Central Park"]{bb};
  way["highway"]{bb};
  way["natural"]{bb}; relation["natural"]{bb};
  way["water"]{bb}; relation["water"]{bb};
  way["leisure"]{bb}; relation["leisure"]{bb};
  way["landuse"]{bb};
  way["building"]{bb};
  way["man_made"]{bb};
  way["barrier"]{bb};
  way["historic"]{bb}; way["tourism"]{bb}; way["amenity"]{bb};
  node["amenity"]{bb}; node["historic"]{bb}; node["tourism"]{bb};
  node["highway"="street_lamp"]{bb}; node["natural"]{bb};
);
out geom;"""
    body = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for url in OVERPASS:
        try:
            doc = json.loads(_get(url, body, 300))
            f.write_text(json.dumps(doc))
            print(f"  osm: {len(doc['elements'])} elements")
            return doc
        except Exception as ex:                       # noqa: BLE001
            last = ex
    raise RuntimeError(f"overpass failed: {last}")


def _export(service: str, bbox, w: int, h: int, cache: Path, kind: str) -> np.ndarray:
    key = "_".join(f"{v:.1f}" for v in bbox) + f"_{w}x{h}"
    if kind == "dem":
        f = cache / f"dem_{key}.tif"
        params = {"format": "tiff", "pixelType": "F32", "noData": -9999,
                  "interpolation": "RSP_BilinearInterpolation"}
    else:
        f = cache / f"naip_{key}.jpg"
        params = {"format": "jpg", "compressionQuality": 92, "bandIds": "0,1,2",
                  "interpolation": "RSP_BilinearInterpolation"}
    if not f.exists():
        qs = urllib.parse.urlencode({"bbox": ",".join(f"{v:.3f}" for v in bbox),
                                     "bboxSR": 3857, "imageSR": 3857,
                                     "size": f"{w},{h}", "f": "image", **params})
        blob = _get(f"{service}?{qs}")
        img = Image.open(io.BytesIO(blob))
        if img.size != (w, h):
            raise ValueError(f"{kind}: got {img.size}, asked {w}x{h}")
        f.write_bytes(blob)
    img = Image.open(f)
    if kind == "dem":
        return np.asarray(img, dtype=np.float32)
    return np.asarray(img.convert("RGB"))


def render(service: str, kind: str, e0: float, n0: float, e1: float, n1: float,
           res: float, cache: Path) -> np.ndarray:
    """The dataset rect [e0,e1] x [n0,n1] at `res` m/px, row 0 north. The
    request is the rect's north-up Mercator box (the services' native
    projection, so they resample once) and every output pixel is inverse-
    mapped into it, which gets the 29 deg turn right by construction."""
    W = int(round((e1 - e0) / res))
    H = int(round((n1 - n0) / res))
    ee = e0 + (np.arange(W) + 0.5) * res
    nn = n1 - (np.arange(H) + 0.5) * res
    EE, NN = np.meshgrid(ee, nn)
    MX, MY = merc(*to_lonlat(EE, NN))
    mres = res / math.cos(math.radians(40.78)) * (0.8 if kind == "naip" else 1.0)
    pad = 6 * mres
    bx0, bx1 = MX.min() - pad, MX.max() + pad
    by0, by1 = MY.min() - pad, MY.max() + pad
    nx = int(math.ceil((bx1 - bx0) / mres))
    ny = int(math.ceil((by1 - by0) / mres))
    gx = int(math.ceil(nx / MAX_PX))
    gy = int(math.ceil(ny / MAX_PX))
    sx, sy = int(math.ceil(nx / gx)), int(math.ceil(ny / gy))
    ch = 1 if kind == "dem" else 3
    mos = np.zeros((sy * gy, sx * gx, ch), np.float32)
    for iy in range(gy):
        for ix in range(gx):
            b = (bx0 + ix * sx * mres, by1 - (iy + 1) * sy * mres,
                 bx0 + (ix + 1) * sx * mres, by1 - iy * sy * mres)
            a = _export(service, b, sx, sy, cache, kind)
            mos[iy * sy:(iy + 1) * sy, ix * sx:(ix + 1) * sx] = a.reshape(sy, sx, ch)
    px = (MX - bx0) / mres - 0.5
    py = (by1 - MY) / mres - 0.5
    out = np.empty((H, W, ch), np.float32)
    for c in range(ch):
        out[..., c] = ndimage.map_coordinates(mos[..., c], [py, px], order=1, mode="nearest")
    return out[..., 0] if ch == 1 else out


# ----------------------------------------------------------------- osm
def _ring(way) -> np.ndarray:
    g = way.get("geometry") or []
    lat = [p["lat"] for p in g]
    lon = [p["lon"] for p in g]
    e, n = to_dataset(lat, lon)
    return np.stack([e, n], 1)


def _relation_rings(rel) -> list[tuple[str, np.ndarray]]:
    """Outer/inner rings of a multipolygon, joining member ways end to end."""
    out = []
    for role in ("outer", "inner"):
        segs = [m for m in rel.get("members", []) if m.get("role") == role and m.get("geometry")]
        pts = [[(p["lat"], p["lon"]) for p in m["geometry"]] for m in segs]
        while pts:
            cur = pts.pop(0)
            grown = True
            while grown and cur[0] != cur[-1]:
                grown = False
                for i, p in enumerate(pts):
                    if p[0] == cur[-1]:
                        cur += p[1:]
                    elif p[-1] == cur[-1]:
                        cur += p[::-1][1:]
                    elif p[-1] == cur[0]:
                        cur = p[:-1] + cur
                    elif p[0] == cur[0]:
                        cur = p[::-1][:-1] + cur
                    else:
                        continue
                    pts.pop(i)
                    grown = True
                    break
            e, n = to_dataset([c[0] for c in cur], [c[1] for c in cur])
            out.append((role, np.stack([e, n], 1)))
    return out


def _centroid(el) -> tuple[float, float] | None:
    if el["type"] == "node":
        e, n = to_dataset(el["lat"], el["lon"])
        return float(e), float(n)
    if el.get("geometry"):
        r = _ring(el)
        return float(r[:, 0].mean()), float(r[:, 1].mean())
    if "bounds" in el:
        b = el["bounds"]
        e, n = to_dataset((b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2)
        return float(e), float(n)
    return None


class Raster:
    """Draw dataset-frame polygons/lines into a rect at `res` m/px, row 0 north."""

    def __init__(self, e0, n0, e1, n1, res):
        self.e0, self.n0, self.e1, self.n1, self.res = e0, n0, e1, n1, res
        self.W = int(round((e1 - e0) / res))
        self.H = int(round((n1 - n0) / res))

    def px(self, pts: np.ndarray) -> list[tuple[float, float]]:
        x = (pts[:, 0] - self.e0) / self.res
        y = (self.n1 - pts[:, 1]) / self.res
        return list(zip(x.tolist(), y.tolist()))

    def new(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        im = Image.new("L", (self.W, self.H), 0)
        return im, ImageDraw.Draw(im)


def _sdf(mask: np.ndarray, res: float) -> np.ndarray:
    """Signed distance in metres, + inside."""
    inside = ndimage.distance_transform_edt(mask) * res
    outside = ndimage.distance_transform_edt(~mask) * res
    return np.where(mask, inside - 0.5 * res, -(outside - 0.5 * res))


#: Paved-way widths, metres. The drives are 2-3 lanes of asphalt; the park's
#: footpaths 3-5 m (the Mall's 10 m is its own polygon in OSM).
WAY_W = {"service": 8.0, "unclassified": 9.0, "tertiary": 9.0, "secondary": 10.0,
         "residential": 8.0, "cycleway": 4.0, "footway": 3.6, "path": 2.8,
         "pedestrian": 7.0, "bridleway": 3.2, "track": 3.0, "steps": 3.0,
         "living_street": 6.0}
DRIVE_NAMES = ("East Drive", "West Drive", "Center Drive", "Terrace Drive")

#: Named lakes and their landmark points, from OSM names. Aliases cover the
#: way OSM spells them.
LAKES = {
    "reservoir": ("Jacqueline Kennedy Onassis Reservoir", "Reservoir"),
    "lake": ("The Lake",),
    "meer": ("Harlem Meer",),
    "turtle_pond": ("Turtle Pond",),
    "pond": ("The Pond",),
    "conservatory_water": ("Conservatory Water",),
    "pool": ("The Pool",),
}


def bake(cache: Path, out: Path) -> None:
    osm = fetch_osm(cache)
    els = osm["elements"]
    park = next(el for el in els if el["type"] == "way"
                and el.get("tags", {}).get("leisure") == "park"
                and el.get("tags", {}).get("name") == "Central Park")
    outline = _ring(park)
    pe0, pn0 = outline.min(0)
    pe1, pn1 = outline.max(0)
    print(f"  park outline: e {pe0:.0f}..{pe1:.0f}, n {pn0:.0f}..{pn1:.0f} "
          f"({pe1 - pe0:.0f} x {pn1 - pn0:.0f} m), {len(outline)} pts")
    # Raster rect: outline bbox + margin, snapped to HEIGHT_RES*4.
    snap = HEIGHT_RES * 4
    e0 = math.floor((pe0 - MARGIN) / snap) * snap
    n0 = math.floor((pn0 - MARGIN) / snap) * snap
    e1 = math.ceil((pe1 + MARGIN) / snap) * snap
    n1 = math.ceil((pn1 + MARGIN) / snap) * snap

    # --- DEM, at 1 m then pooled to HEIGHT_RES.
    dem = render(DEM_SERVICE, "dem", e0, n0, e1, n1, 1.0, cache)
    bad = dem < -100
    if bad.any():
        print(f"  dem: {bad.sum()} nodata px filled")
        idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
        dem = dem[tuple(idx)]
    print(f"  dem: {dem.shape}, {dem.min():.1f}..{dem.max():.1f} m NAVD88")
    np.save(cache / "dem1m.npy", dem)

    r1 = Raster(e0, n0, e1, n1, 1.0)
    im, dr = r1.new()
    dr.polygon(r1.px(outline), fill=255)
    inside1 = np.asarray(im) > 0

    # --- street level around the park: the DEM on the perimeter avenues,
    # then carried inward as a membrane (Laplace with the perimeter fixed).
    dist_out = ndimage.distance_transform_edt(~inside1)
    ring = (dist_out > STREET_OFFSET - 3) & (dist_out < STREET_OFFSET + 3)
    # Robust to a car, a kerb or a planted median: the median of a window.
    street = ndimage.median_filter(dem, size=9)
    rel = _membrane(street, ring, inside1, 1.0)
    h = dem - rel
    dist_in = ndimage.distance_transform_edt(inside1)
    # The ramp widens where the park sits far below (or above) the avenue:
    # 14 m where it is nearly level, up to 45 m where it is 8-10 m down
    # (north of 100th St on 5th Ave), so the drop reads as a bank, not a
    # cliff at the wall.
    width = np.clip(WALL_RAMP + 3.0 * ndimage.gaussian_filter(np.abs(np.where(inside1, h, 0.0)), 10), WALL_RAMP, 45.0)
    ramp = np.clip(dist_in / width, 0, 1)
    ramp = ramp * ramp * (3 - 2 * ramp)
    h = np.where(inside1, h * ramp, 0.0)

    # --- the four transverse roads. They really run below grade (the park's
    # bridges cross them), but the scene's traffic drives every street at
    # y = 0, so a sunken roadbed would leave the cars floating over a trench.
    # They are flattened to street level with a shoulder that blends back
    # into the park's own relief; the drives, car-free since 2018, keep theirs.
    roads = json.loads((ASSETS / "roads.json").read_text())
    im, dr = r1.new()
    rn = roads["nodes"]
    names = roads.get("names", [])
    for ed in roads["edges"]:
        nm = names[ed[7]] if 0 <= ed[7] < len(names) else ""
        if "TRANSVERSE" not in nm:
            continue
        a, b = rn[ed[0]], rn[ed[1]]
        dr.line(r1.px(np.array([a, b], float)), fill=255,
                width=int(round(float(ed[5]) + 6)), joint="curve")
    trans = np.asarray(im) > 0
    gates = _gates(roads, outline)
    print(f"  wall: {len(gates)} gates where a way crosses the outline")
    dt = ndimage.distance_transform_edt(~trans)
    flat = np.clip(1 - dt / 12.0, 0, 1)
    flat = flat * flat * (3 - 2 * flat)
    h = h * (1 - flat)

    # --- the lakes: outline, a flat surface at the DEM's own water level.
    # 3DEP is hydro-flattened, so the DEM over open water IS the surface.
    lakes = []
    water_mask = np.zeros_like(inside1)
    seen = set()
    for el in els:
        t = el.get("tags", {})
        nm = t.get("name", "")
        is_water = t.get("natural") == "water" or "water" in t
        if not is_water:
            continue
        rings = []
        if el["type"] == "way" and el.get("geometry"):
            rings = [("outer", _ring(el))]
        elif el["type"] == "relation":
            rings = _relation_rings(el)
        outers = [r for role, r in rings if role == "outer" and len(r) > 3]
        if not outers:
            continue
        c = np.concatenate(outers).mean(0)
        if not (pe0 + 5 < c[0] < pe1 - 5 and pn0 + 5 < c[1] < pn1 - 5):
            continue
        key = next((k for k, names in LAKES.items() if nm in names), None)
        im, dr = r1.new()
        for role, r in rings:
            if len(r) > 2:
                dr.polygon(r1.px(r), fill=255 if role == "outer" else 0)
        m = np.asarray(im) > 0
        area = float(m.sum())
        if area < 150:
            continue
        sig = (round(float(c[0])), round(float(c[1])))
        if sig in seen:
            continue
        seen.add(sig)
        core = ndimage.binary_erosion(m, iterations=4)
        if core.sum() < 20:
            core = m
        level = float(np.median(h[core]))
        water_mask |= m
        lakes.append({"name": key or nm or "water", "label": nm,
                      "level": round(level, 2), "area_m2": round(area),
                      "rings": [{"role": role, "pts": np.round(r, 1).tolist()}
                                for role, r in rings if len(r) > 2]})
    lakes.sort(key=lambda l: -l["area_m2"])
    for l in lakes:
        print(f"  water {l['name']!r:28} {l['area_m2']:8.0f} m2  level {l['level']:+.2f} m")
    # Under each lake the ground is the lakebed: a couple of metres below
    # the surface, so the shoreline reads as a shelving bank, not a cliff.
    sdw = _sdf(water_mask, 1.0)
    lvl = np.zeros_like(h)
    for l in lakes:
        im, dr = r1.new()
        for rr in l["rings"]:
            dr.polygon(r1.px(np.array(rr["pts"])), fill=255 if rr["role"] == "outer" else 0)
        m = ndimage.binary_dilation(np.asarray(im) > 0, iterations=3)
        lvl = np.where(m, l["level"], lvl)
    bed = lvl - np.clip(sdw, 0, 6) * 0.35 - 0.25
    h = np.where(water_mask, np.minimum(h, bed), h)

    # --- pool to HEIGHT_RES and write.
    k = int(HEIGHT_RES)
    Hh, Wh = h.shape[0] // k, h.shape[1] // k
    hp = h[:Hh * k, :Wh * k].reshape(Hh, k, Wh, k).mean((1, 3))
    cm = np.clip(np.round(hp * 100), -32000, 32000).astype("<i2")
    blob = zlib.compress(cm.tobytes(), 9)
    (out / "central_park_height.zi16").write_bytes(blob)
    print(f"  height: {Wh} x {Hh} @ {HEIGHT_RES} m, {hp.min():+.1f}..{hp.max():+.1f} m, "
          f"{len(blob) / 1024:.0f} KB")

    # --- cover fields at COVER_RES (same rect).
    cov = _cover(els, outline, water_mask, h, dem, r1, cache)
    Image.fromarray(cov).save(out / "_park" / "cover.png", optimize=True)

    # --- the committed fields at HEIGHT_RES: R inside the park (antialiased,
    # the line the city ground is cut at and the terrain begins), G water
    # signed distance (128 + 8 * m), B bare rock, A lawn kind. Pooled from
    # the 1 m rasters, so a fresh checkout has the shore and the cut without
    # running this tool.
    def pool(a):
        return a[:Hh * k, :Wh * k].reshape(Hh, k, Wh, k).mean((1, 3))
    sdw2 = pool(_sdf(water_mask, 1.0))
    fields = np.stack([np.clip(pool(inside1.astype(np.float32)) * 255, 0, 255),
                       np.clip(np.round(128 + 8 * sdw2), 0, 255),
                       pool(cov[..., 2].astype(np.float32)),
                       pool(cov[..., 3].astype(np.float32))], -1).astype(np.uint8)
    Image.fromarray(fields).save(out / "central_park_fields.png", optimize=True)
    print(f"  fields: {(out / 'central_park_fields.png').stat().st_size / 1024:.0f} KB")

    # --- photo.
    photo = render(NAIP_SERVICE, "naip", e0, n0, e1, n1, PHOTO_RES, cache)
    Image.fromarray(np.clip(photo, 0, 255).astype(np.uint8)).save(
        out / "_park" / "photo.jpg", quality=90)
    print(f"  photo: {photo.shape[1]} x {photo.shape[0]} @ {PHOTO_RES} m")

    features = _features(els, outline, h, r1)
    doc = {
        "source": "USGS 3DEP 1 m bare-earth DEM (public domain); USGS NAIP (public domain); "
                  "OpenStreetMap contributors (ODbL 1.0)",
        "rect": [e0, n0, e1, n1],
        "outline": np.round(outline, 1).tolist(),
        "fields": {"file": "central_park_fields.png", "res_m": HEIGHT_RES,
                   "channels": "R inside park, G water sdf (128 + 8*m, + water), B rock, A lawn kind"},
        "height": {"file": "central_park_height.zi16", "res_m": HEIGHT_RES,
                   "size_px": [Wh, Hh], "dtype": "int16_cm_zlib", "row0": "north"},
        "photo": {"file": "_park/photo.jpg", "res_m": PHOTO_RES},
        "cover": {"file": "_park/cover.png", "res_m": COVER_RES,
                  "channels": "R way sdf, G water sdf (128 + 8*m, + inside), B rock, A lawn kind"},
        "lakes": lakes,
        "gates": gates,
        **features,
    }
    (out / "central_park.json").write_text(json.dumps(doc, separators=(",", ":")))
    print(f"  central_park.json {len(json.dumps(doc)) / 1024:.0f} KB")


def _gates(roads: dict, outline: np.ndarray) -> list:
    """Where any street or path crosses the park outline: the openings in
    the perimeter wall. [e, n, width_m]."""
    nodes = np.asarray(roads["nodes"], float)
    a = outline[:-1]
    b = outline[1:]
    lo = outline.min(0) - 30
    hi = outline.max(0) + 30
    out = []
    for ed in roads["edges"]:
        p, q = nodes[ed[0]], nodes[ed[1]]
        if not ((lo <= p).all() and (p <= hi).all() or (lo <= q).all() and (q <= hi).all()):
            continue
        r = q - p
        sv = b - a
        den = r[0] * sv[:, 1] - r[1] * sv[:, 0]
        ok = np.abs(den) > 1e-9
        ap = a - p
        t = np.where(ok, (ap[:, 0] * sv[:, 1] - ap[:, 1] * sv[:, 0]) / np.where(ok, den, 1), -1)
        u = np.where(ok, (ap[:, 0] * r[1] - ap[:, 1] * r[0]) / np.where(ok, den, 1), -1)
        hit = ok & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)
        for i in np.nonzero(hit)[0]:
            x = p + t[i] * r
            out.append([round(float(x[0]), 1), round(float(x[1]), 1), round(float(ed[5]) + 1.5, 1)])
    return out


def _membrane(street: np.ndarray, ring: np.ndarray, inside: np.ndarray, res: float) -> np.ndarray:
    """Street level everywhere: fixed on the perimeter ring, harmonic between.
    Solved on an 8 m grid (Jacobi, plenty for a smooth membrane), then
    upsampled; outside the ring it is just the street itself."""
    k = 8
    H, W = street.shape
    Hs, Ws = H // k, W // k
    s = street[:Hs * k, :Ws * k].reshape(Hs, k, Ws, k).mean((1, 3))
    fixed = ring[:Hs * k, :Ws * k].reshape(Hs, k, Ws, k).any((1, 3))
    ins = inside[:Hs * k, :Ws * k].reshape(Hs, k, Ws, k).any((1, 3)) & ~fixed
    u = np.where(fixed, s, float(np.median(s[fixed])))
    for _ in range(6000):
        avg = 0.25 * (np.roll(u, 1, 0) + np.roll(u, -1, 0) + np.roll(u, 1, 1) + np.roll(u, -1, 1))
        u = np.where(ins, avg, u)
    up = ndimage.zoom(u, (H / Hs, W / Ws), order=1)[:H, :W]
    if up.shape != street.shape:
        up = np.pad(up, ((0, H - up.shape[0]), (0, W - up.shape[1])), mode="edge")
    return np.where(inside, up, street)


def _inner_rect(inside: np.ndarray, r: Raster) -> list[float]:
    """The largest axis-aligned rect inside the outline (the hole the city
    ground leaves for the park), found by shrinking the bbox until every
    edge row/column is fully inside."""
    ys, xs = np.nonzero(inside)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    for _ in range(200):
        moved = False
        if inside[y0, x0:x1 + 1].mean() < 0.999:
            y0 += 1; moved = True
        if inside[y1, x0:x1 + 1].mean() < 0.999:
            y1 -= 1; moved = True
        if inside[y0:y1 + 1, x0].mean() < 0.999:
            x0 += 1; moved = True
        if inside[y0:y1 + 1, x1].mean() < 0.999:
            x1 -= 1; moved = True
        if not moved:
            break
    return [r.e0 + x0 * r.res, r.n1 - (y1 + 1) * r.res, r.e0 + (x1 + 1) * r.res, r.n1 - y0 * r.res]


def _cover(els, outline, water_mask, h, dem, r1: Raster, cache: Path) -> np.ndarray:
    # Paved ways: drawn as lines of their width.
    im, dr = r1.new()
    drives = []
    for el in els:
        t = el.get("tags", {})
        hw = t.get("highway")
        if el["type"] != "way" or not hw or not el.get("geometry"):
            continue
        if t.get("area") == "yes" or hw == "pedestrian" and el["geometry"][0] == el["geometry"][-1]:
            r = _ring(el)
            dr.polygon(r1.px(r), fill=255)
            continue
        w = float(t.get("width", "0").split()[0]) if t.get("width", "").replace(".", "").isdigit() else WAY_W.get(hw, 0.0)
        if t.get("name") in DRIVE_NAMES:
            w = max(w, 9.0)
        if w <= 0:
            continue
        if t.get("tunnel") == "yes":
            continue
        r = _ring(el)
        dr.line(r1.px(r), fill=255, width=max(1, int(round(w / r1.res))), joint="curve")
    paved = np.asarray(im) > 0
    # Plazas, terraces (Bethesda) and the Mall: area polygons.
    for el in els:
        t = el.get("tags", {})
        if el["type"] == "way" and el.get("geometry") and (
                t.get("area:highway") or t.get("place") == "square"
                or (t.get("highway") == "pedestrian" and t.get("area") == "yes")):
            im2, dr2 = r1.new()
            dr2.polygon(r1.px(_ring(el)), fill=255)
            paved |= np.asarray(im2) > 0
    sd_way = _sdf(paved, r1.res)

    # Bare rock: OSM's natural=bare_rock where mapped, plus where the DEM is
    # steep and rough at the scale of an outcrop and the photo is not tree
    # canopy (the canopy test is done in the shader with the photo itself).
    im, dr = r1.new()
    for el in els:
        t = el.get("tags", {})
        if t.get("natural") in ("bare_rock", "rock", "stone") and el["type"] == "way" and el.get("geometry"):
            dr.polygon(r1.px(_ring(el)), fill=255)
    rock_osm = np.asarray(im).astype(np.float32) / 255.0
    gy, gx = np.gradient(dem)
    slope = np.hypot(gx, gy)
    smooth = ndimage.gaussian_filter(dem, 6)
    rough = ndimage.gaussian_filter(np.abs(dem - smooth), 2)
    rock_dem = np.clip((slope - 0.45) / 0.5, 0, 1) * np.clip((rough - 0.35) / 0.6, 0, 1)
    rock = np.maximum(rock_osm, ndimage.gaussian_filter(rock_dem, 1.0))
    rock *= ~paved & ~water_mask

    # Lawn kind: mown lawns (Sheep Meadow, the Great Lawn, ballfields), rough
    # meadow (North Meadow edges, the hills) and woodland floor (the Ramble,
    # the North Woods, Hallett).
    lawn = np.full(paved.shape, 140, np.uint8)
    for kind, val in (("wood", 0), ("lawn", 255)):
        im, dr = r1.new()
        for el in els:
            t = el.get("tags", {})
            g = el.get("geometry")
            if el["type"] != "way" or not g or len(g) < 4:
                continue
            if kind == "wood" and t.get("natural") in ("wood", "scrub") or t.get("landuse") == "forest":
                dr.polygon(r1.px(_ring(el)), fill=255)
            if kind == "lawn" and (t.get("leisure") in ("pitch", "garden") or t.get("landuse") == "grass"
                                   or (t.get("leisure") == "park" and t.get("name") in
                                       ("Sheep Meadow", "Great Lawn", "North Meadow", "East Meadow"))):
                dr.polygon(r1.px(_ring(el)), fill=255)
        m = np.asarray(im) > 0
        lawn[m] = val
    lawn = ndimage.gaussian_filter(lawn.astype(np.float32), 2.0)

    enc = lambda d: np.clip(np.round(128 + 8 * d), 0, 255).astype(np.uint8)
    cov = np.stack([enc(sd_way), enc(_sdf(water_mask, r1.res)),
                    np.clip(rock * 255, 0, 255).astype(np.uint8),
                    np.clip(lawn, 0, 255).astype(np.uint8)], -1)
    print(f"  cover: {cov.shape[1]} x {cov.shape[0]} @ {r1.res} m; paved {paved.mean() * 100:.1f}%, "
          f"rock {(rock > 0.5).mean() * 100:.2f}%")
    return cov


#: Landmarks by OSM name -> our key. Positions come from OSM; anything not
#: found falls back to the coordinates here (lat, lon, checked against the
#: NAIP photo).
LANDMARKS = {
    "bethesda_fountain": (("Bethesda Fountain", "Angel of the Waters"), (40.77405, -73.97089)),
    "bethesda_terrace": (("Bethesda Terrace",), (40.77385, -73.97115)),
    "bow_bridge": (("Bow Bridge",), (40.77575, -73.97180)),
    "belvedere_castle": (("Belvedere Castle",), (40.77937, -73.96903)),
    "wollman_rink": (("Wollman Rink", "Trump Rink", "Victorian Gardens"), (40.76967, -73.97456)),
    "bandshell": (("Naumburg Bandshell",), (40.77205, -73.97140)),
    "carousel": (("Central Park Carousel", "Friedsam Memorial Carousel", "Michael Friedsam Memorial Carousel"),
                 (40.77013, -73.97540)),
    "gapstow_bridge": (("Gapstow Bridge",), (40.76637, -73.97372)),
    "dairy": (("The Dairy", "Dairy"), (40.76848, -73.97523)),
    "cleopatras_needle": (("Cleopatra's Needle", "Obelisk"), (40.77965, -73.96526)),
    "conservatory_garden": (("Conservatory Garden",), (40.79390, -73.95225)),
    "great_lawn": (("Great Lawn",), (40.78136, -73.96655)),
    "sheep_meadow": (("Sheep Meadow",), (40.77176, -73.97497)),
    "delacorte": (("Delacorte Theater",), (40.78040, -73.96875)),
    "boathouse": (("Loeb Boathouse",), (40.77525, -73.96895)),
    "the_mall": (("The Mall", "Literary Walk"), (40.77150, -73.97275)),
}


def _features(els, outline, h, r1: Raster) -> dict:
    lm = {}
    for key, (names, fallback) in LANDMARKS.items():
        best = None
        for el in els:
            nm = el.get("tags", {}).get("name", "")
            if nm in names:
                c = _centroid(el)
                if c is not None:
                    poly = _ring(el).round(1).tolist() if el.get("geometry") else None
                    best = {"en": [round(c[0], 1), round(c[1], 1)], "src": "osm", "osm": el["type"],
                            "poly": poly}
                    if el["type"] == "way" and el.get("geometry"):
                        break
        if best is None:
            e, n = to_dataset(*fallback)
            best = {"en": [round(float(e), 1), round(float(n), 1)], "src": "fallback"}
        lm[key] = best
    # Ballfields (baseball diamonds) on the Great Lawn and North Meadow:
    # OSM maps them as pitches.
    pitches = []
    for el in els:
        t = el.get("tags", {})
        if t.get("leisure") == "pitch" and el["type"] == "way" and el.get("geometry"):
            c = _centroid(el)
            if c and _inside(outline, c):
                pitches.append({"sport": t.get("sport", ""), "pts": _ring(el).round(1).tolist()})
    # Drives as polylines (for lamp posts).
    drives = []
    for el in els:
        t = el.get("tags", {})
        if el["type"] == "way" and el.get("geometry") and t.get("name") in DRIVE_NAMES:
            drives.append(_ring(el).round(1).tolist())
    # Bridges inside the park (OSM bridge=yes ways) for small arches.
    bridges = []
    for el in els:
        t = el.get("tags", {})
        if el["type"] == "way" and el.get("geometry") and t.get("bridge") in ("yes", "arch") \
                and t.get("highway"):
            r = _ring(el)
            c = r.mean(0)
            if _inside(outline, c):
                bridges.append({"name": t.get("bridge:name", t.get("name", "")),
                                "hw": t["highway"], "pts": r.round(1).tolist()})
    # Buildings inside the park (the castle, the Dairy, the boathouse ...).
    blds = []
    for el in els:
        t = el.get("tags", {})
        if el["type"] == "way" and el.get("geometry") and "building" in t:
            r = _ring(el)
            if _inside(outline, r.mean(0)):
                blds.append({"name": t.get("name", ""), "levels": t.get("building:levels", ""),
                             "height": t.get("height", ""), "pts": r.round(1).tolist()})
    return {"landmarks": lm, "pitches": pitches, "drives": drives, "bridges": bridges,
            "buildings": blds}


def _inside(poly: np.ndarray, p) -> bool:
    x, y = float(p[0]), float(p[1])
    n = len(poly)
    c = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            c = not c
        j = i
    return c


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ASSETS))
    a = ap.parse_args(argv)
    out = Path(a.out)
    cache = out / "_park" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    bake(cache, out)
    print(f"done in {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
