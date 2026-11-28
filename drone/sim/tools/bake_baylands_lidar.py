#!/usr/bin/env python3
"""Rasterise the 2020 Santa Clara County lidar into Baylands Park grids.

Step 2 of the Baylands pipeline (step 1, fetch_baylands.py, downloads the
tiles). Reads every COPC/LAZ tile in --tiles, keeps the points inside the
scene region, and writes 0.5 m grids in the scene's own frame to --out
(a gitignored work folder; nothing here is committed):

    dtm.f32       bare ground, metres above NAVD88 (class 2), gaps filled
    dsm.f32       highest non-noise return
    bld.f32       highest building return (class 6), NaN where none
    veg.f32       highest vegetation return (classes 3-5), NaN where none
    wire.f32      highest wire return (class 14), NaN where none
    tower.f32     highest transmission-tower return (class 15), NaN where none
    bridge.f32    highest bridge-deck return (class 17), NaN where none
    other.f32     highest unclassified return (class 1) more than 1 m above
                  the ground (poles, light standards, fences), NaN where none
    water.u8      1 where water returns (class 9) fell
    inten.u8      first-return intensity of ground-level points, 0-255
    count.u16     points per cell (all kept classes)
    grid.json     size, cell, bounds and the frame

SOURCE: USGS 3DEP "CA_SantaClaraCounty_2020" (Sanborn, April 2020), public
domain, from NOAA Digital Coast's mirror (dataset 9329), which serves it as
COPC in NAD83(2011) / UTM 10N with NAVD88 (GEOID18) heights, ALL IN METRES.
The USGS rockyweb copy of the same survey is in US survey feet; this tool
assumes metres and checks the tile CRS says so (it stops otherwise).

FRAME: the scene's ENU, metres east/north of env_baylands.gd's GEO_DATUM, by
the same flat-earth formula the env uses (geo_to_sim). UTM -> ENU is an affine
fit per tile (the worst residual is printed; about a centimetre).

Run on a machine with RAM to spare (it holds ~20 float32 grids of 96 M cells):
    uv run --no-project --with "laspy[lazrs]" --with numpy --with scipy \
        --with pyproj python bake_baylands_lidar.py --tiles DIR --out DIR
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

# Must match env_baylands.gd (GEO_DATUM, REGION) and fetch_baylands.py.
DATUM_LAT, DATUM_LON = 37.4125, -121.9970
REGION = (-2500.0, -2400.0, 2500.0, 2400.0)       # e0, n0, e1, n1 metres
CELL = 0.5
M_PER_DEG_LAT = 111132.0

NOISE = {7, 18}
GROUND, WATER, BUILDING, WIRE, TOWER, BRIDGE, UNCLASS = 2, 9, 6, 14, 15, 17, 1
VEG = {3, 4, 5}


def grid_shape() -> tuple[int, int]:
    e0, n0, e1, n1 = REGION
    return int(round((n1 - n0) / CELL)), int(round((e1 - e0) / CELL))   # rows (north->south), cols


def utm_to_enu_affine(x0: float, y0: float, x1: float, y1: float) -> tuple[np.ndarray, float]:
    """Least-squares affine UTM10N -> scene ENU over one tile's extent.

    The scene's frame is the flat-earth formula (fixed metres per degree), which
    is NOT affine to UTM over the whole 5 km region (~0.8 m at the corners), but
    over one ~650 m tile an affine fit is good to about a centimetre."""
    from pyproj import Transformer
    to_ll = Transformer.from_crs(26910, 4326, always_xy=True)
    m_lon = 111320.0 * math.cos(math.radians(DATUM_LAT))
    ux, uy = np.meshgrid(np.linspace(x0, x1, 6), np.linspace(y0, y1, 6))
    lon, lat = to_ll.transform(ux.ravel(), uy.ravel())
    e = (np.asarray(lon) - DATUM_LON) * m_lon
    n = (np.asarray(lat) - DATUM_LAT) * M_PER_DEG_LAT
    A = np.column_stack([ux.ravel(), uy.ravel(), np.ones(ux.size)])
    coef, *_ = np.linalg.lstsq(A, np.column_stack([e, n]), rcond=None)
    return coef, float(np.abs(A @ coef - np.column_stack([e, n])).max())


def _tile(path) -> dict | None:
    import laspy
    with laspy.open(path) as r:
        hx0, hy0 = r.header.mins[:2]
        hx1, hy1 = r.header.maxs[:2]
        crs = r.header.parse_crs()
        name = crs.name if crs is not None else ""
        if "UTM zone 10N" not in name or "ft" in name.lower():
            raise RuntimeError(f"{path}: unexpected CRS {name!r} (this tool expects UTM 10N metres)")
        p = r.read()
    coef, resid = utm_to_enu_affine(hx0, hy0, hx1, hy1)
    cls = np.asarray(p.classification).astype(np.int16)
    keep = ~np.isin(cls, list(NOISE))
    try:
        keep &= ~np.asarray(p.withheld, dtype=bool)
    except Exception:                                   # noqa: BLE001 - older formats
        pass
    x = np.asarray(p.x)[keep]; y = np.asarray(p.y)[keep]; z = np.asarray(p.z)[keep].astype(np.float32)
    cls = cls[keep]
    rn = np.asarray(p.return_number)[keep]
    it = np.asarray(p.intensity)[keep].astype(np.float32)
    e = coef[0, 0] * x + coef[1, 0] * y + coef[2, 0]
    n = coef[0, 1] * x + coef[1, 1] * y + coef[2, 1]
    e0, n0, e1, n1 = REGION
    rows, cols = grid_shape()
    c = np.floor((e - e0) / CELL).astype(np.int64)
    rr = np.floor((n1 - n) / CELL).astype(np.int64)
    inside = (c >= 0) & (c < cols) & (rr >= 0) & (rr < rows)
    if not inside.any():
        return None
    c, rr, z, cls, rn, it = c[inside], rr[inside], z[inside], cls[inside], rn[inside], it[inside]
    r0, r1, c0, c1 = int(rr.min()), int(rr.max()) + 1, int(c.min()), int(c.max()) + 1
    h, w = r1 - r0, c1 - c0
    flat = (rr - r0) * w + (c - c0)

    def mx(mask):
        a = np.full(h * w, -np.inf, np.float32)
        if mask.any():
            np.maximum.at(a, flat[mask], z[mask])
        return a.reshape(h, w)

    def sm(mask, v):
        s = np.bincount(flat[mask], weights=v[mask], minlength=h * w).astype(np.float64)
        k = np.bincount(flat[mask], minlength=h * w)
        return s.reshape(h, w), k.reshape(h, w)

    out = {"r0": r0, "c0": c0, "h": h, "w": w, "classes": np.bincount(cls, minlength=32), "resid": resid}
    g = cls == GROUND
    out["gsum"], out["gcnt"] = sm(g, z)
    a = np.full(h * w, np.inf, np.float32)
    if g.any():
        np.minimum.at(a, flat[g], z[g])
    out["gmin"] = a.reshape(h, w)
    wmask = cls == WATER
    out["wsum"], out["wcnt"] = sm(wmask, z)
    out["dsm"] = mx(np.ones_like(g))
    out["bld"] = mx(cls == BUILDING)
    out["veg"] = mx(np.isin(cls, list(VEG)))
    out["wire"] = mx(cls == WIRE)
    out["tower"] = mx(cls == TOWER)
    out["bridge"] = mx(cls == BRIDGE)
    out["other"] = mx(cls == UNCLASS)
    first_low = (rn == 1) & (g | wmask)
    out["isum"], out["icnt"] = sm(first_low, it)
    out["count"] = np.bincount(flat, minlength=h * w).reshape(h, w)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tiles", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=min(16, os.cpu_count() or 4))
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    tiles = sorted(str(p) for p in Path(a.tiles).glob("*.laz"))
    print(f"{len(tiles)} tiles")
    worst = 0.0
    rows, cols = grid_shape()
    F = lambda v: np.full((rows, cols), v, np.float32)          # noqa: E731
    gsum = np.zeros((rows, cols)); gcnt = np.zeros((rows, cols), np.int32)
    wsum = np.zeros((rows, cols)); wcnt = np.zeros((rows, cols), np.int32)
    isum = np.zeros((rows, cols)); icnt = np.zeros((rows, cols), np.int32)
    gmin = F(np.inf)
    maxes = {k: F(-np.inf) for k in ("dsm", "bld", "veg", "wire", "tower", "bridge", "other")}
    count = np.zeros((rows, cols), np.int32)
    classes = np.zeros(32, np.int64)
    done = 0
    with ProcessPoolExecutor(a.jobs) as ex:
        for res in ex.map(_tile, tiles):
            done += 1
            if res is None:
                continue
            s = (slice(res["r0"], res["r0"] + res["h"]), slice(res["c0"], res["c0"] + res["w"]))
            gsum[s] += res["gsum"]; gcnt[s] += res["gcnt"]
            wsum[s] += res["wsum"]; wcnt[s] += res["wcnt"]
            isum[s] += res["isum"]; icnt[s] += res["icnt"]
            gmin[s] = np.minimum(gmin[s], res["gmin"])
            for k in maxes:
                maxes[k][s] = np.maximum(maxes[k][s], res[k])
            count[s] += res["count"]
            classes += res["classes"]
            worst = max(worst, res["resid"])
            if done % 10 == 0:
                print(f"  {done}/{len(tiles)}", flush=True)
    print(f"UTM->ENU per-tile affine: worst residual {worst * 1000:.1f} mm")
    print("class counts:", {i: int(v) for i, v in enumerate(classes) if v})

    from scipy import ndimage
    # Ground: mean of ground returns; cells without one (under buildings,
    # water, dense canopy) take the nearest ground cell, then a light blur
    # over just those filled cells so roofs do not leave terraces.
    have = gcnt > 0
    dtm = np.where(have, gsum / np.maximum(gcnt, 1), np.nan).astype(np.float32)
    water = wcnt > 0
    wlev = np.where(water, wsum / np.maximum(wcnt, 1), np.nan)
    # Water with no ground under it: its own surface is the ground.
    dtm = np.where(~have & water, wlev, dtm).astype(np.float32)
    known = np.isfinite(dtm)
    print(f"ground coverage {known.mean() * 100:.1f}%")
    idx = ndimage.distance_transform_edt(~known, return_distances=False, return_indices=True)
    filled = dtm[tuple(idx)]
    blur = ndimage.uniform_filter(filled, 9)
    dtm = np.where(known, dtm, blur).astype(np.float32)

    dtm.tofile(out / "dtm.f32")
    for k, v in maxes.items():
        v = v.copy(); v[~np.isfinite(v)] = np.nan
        if k == "other":
            v[~(v > dtm + 1.0)] = np.nan
        if k == "dsm":
            v = np.where(np.isfinite(v), v, dtm)
        v.astype(np.float32).tofile(out / f"{k}.f32")
    water.astype(np.uint8).tofile(out / "water.u8")
    inten = np.where(icnt > 0, isum / np.maximum(icnt, 1), 0)
    lo, hi = np.percentile(inten[icnt > 0], [1, 99]) if (icnt > 0).any() else (0, 1)
    np.clip((inten - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8).tofile(out / "inten.u8")
    np.clip(count, 0, 65535).astype(np.uint16).tofile(out / "count.u16")
    e0, n0, e1, n1 = REGION
    (out / "grid.json").write_text(json.dumps({
        "rows": rows, "cols": cols, "cell_m": CELL, "region_enu": REGION,
        "row0": "north edge (n1); rows run north -> south, cols west -> east",
        "datum": [DATUM_LAT, DATUM_LON], "heights": "metres above NAVD88 (GEOID18)",
        "classes": {str(i): int(v) for i, v in enumerate(classes) if v},
        "intensity_stretch": [float(lo), float(hi)],
        "source": "USGS 3DEP CA_SantaClaraCounty_2020 via NOAA Digital Coast 9329 (public domain)",
    }, indent=1))
    print(f"dtm {np.nanmin(dtm):.2f}..{np.nanmax(dtm):.2f} m; wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
