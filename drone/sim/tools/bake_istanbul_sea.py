#!/usr/bin/env python3
"""Bake the Istanbul sea's two fields from the scene's water mask.

Input:  drone/sim/godot/assets/istanbul/water.png + water.json
        (fetch_istanbul.py: OpenStreetMap coastline, 4 m, row 0 = north).

Outputs in drone/sim/godot/assets/istanbul/:

  sea_shore.png  8-bit signed distance to the shoreline at the mask's 4 m,
                 row 0 = north. Metres d (+ over water) are stored as
                 128 + 12 * sign(d) * sqrt(|d|), clamped: fine near the shore,
                 where the wash and the shallows need it, out to ~110 m
                 (and only ~6 m in over the land).
  sea_flow.png   RGBA8 at 32 m, row 0 = north: R = the surface current's
                 strength (0..1; 1 = the Bosphorus' main stream, ~1.5 m/s),
                 G, B = its direction (east, north) * 0.5 + 0.5, A = the
                 distance to the nearest shore / 1500 m (the broad shallows).
  sea.json       {"shore": {...}, "flow": {...}} bounds, resolution, encoding.

The current: the Bosphorus' surface water runs from the Black Sea to the
Marmara (south-south-west past the scene) and swings west along the
peninsula's Marmara shore; the Golden Horn is all but still. The stream's
direction is the channel's own axis: the principal direction of a smoothed
structure tensor of the distance field's gradient (across the channel),
turned 90 degrees and signed down-stream. Strength is a hand-drawn region
map: the Bosphorus north of the Sarayburnu-Haydarpasa line, its plume
fading west and south into the Marmara, the Golden Horn near zero.

Derived from OpenStreetMap (ODbL 1.0); see assets/istanbul/CREDITS.md.

    uv run --no-project --with numpy --with scipy --with pillow \
        python drone/sim/tools/bake_istanbul_sea.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "godot" / "assets" / "istanbul"

FLOW_RES = 32.0
# The Bosphorus' mouth: Sarayburnu (Seraglio Point) to the Haydarpasa
# breakwater, ENU metres.
MOUTH_A = np.array([400.0, 880.0])
MOUTH_B = np.array([3100.0, -900.0])
# The Golden Horn's mouth: Sarayburnu to Tophane.
HORN_A = np.array([400.0, 880.0])
HORN_B = np.array([-60.0, 1650.0])


def side(p_e, p_n, a, b):
    """> 0 left of a->b."""
    return (b[0] - a[0]) * (p_n - a[1]) - (b[1] - a[1]) * (p_e - a[0])


def main() -> None:
    meta = json.loads((OUT / "water.json").read_text())
    e0, n0, e1, n1 = meta["bounds_enu"]
    res = float(meta["res_m"])
    water = np.asarray(Image.open(OUT / "water.png").convert("L")) > 127
    h, w = water.shape

    # ---------------------------------------------------------- shore SDF
    # The mask's edge is open water (env.is_water says so past it): pad with
    # water so the border never reads as a shore.
    pad = 64
    wp = np.pad(water, pad, constant_values=True)
    d_in = ndimage.distance_transform_edt(wp) * res          # water -> nearest land
    d_out = ndimage.distance_transform_edt(~wp) * res        # land -> nearest water
    sd = np.where(wp, d_in - 0.5 * res, -(d_out - 0.5 * res))[pad:-pad, pad:-pad]
    # Past ~110 m out and ~6 m in the field only saturates: smaller to store.
    sd = np.clip(sd, -6.0, 200.0)
    enc = 128.0 + 12.0 * np.sign(sd) * np.sqrt(np.abs(sd))
    Image.fromarray(np.clip(np.round(enc), 0, 255).astype(np.uint8), "L").save(
        OUT / "sea_shore.png", optimize=True)

    # ---------------------------------------------------------- current
    k = int(FLOW_RES / res)
    fh, fw = h // k, w // k
    frac = water[: fh * k, : fw * k].reshape(fh, k, fw, k).mean(axis=(1, 3))
    fwater = frac > 0.5
    dist = ndimage.distance_transform_edt(np.pad(fwater, 8, constant_values=True))[8:-8, 8:-8] * FLOW_RES
    # Pixel centres in ENU (row 0 = north).
    ee = e0 + (np.arange(fw) + 0.5) * FLOW_RES
    nn = n1 - (np.arange(fh) + 0.5) * FLOW_RES
    E, N = np.meshgrid(ee, nn)
    # Structure tensor of the distance field: its principal direction is
    # across the channel.
    sm = ndimage.gaussian_filter(dist, 2.0)
    gy, gx = np.gradient(sm)           # gy: rows (south+), gx: east
    gn = -gy
    sig = 6.0
    jxx = ndimage.gaussian_filter(gx * gx, sig)
    jxy = ndimage.gaussian_filter(gx * gn, sig)
    jyy = ndimage.gaussian_filter(gn * gn, sig)
    ang = 0.5 * np.arctan2(2 * jxy, jxx - jyy)            # across-channel axis
    lam_d = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2)
    coh = np.clip(lam_d / (jxx + jyy + 1e-9), 0.0, 1.0)
    ax_e = -np.sin(ang)                                    # along-channel axis
    ax_n = np.cos(ang)

    # Left of Sarayburnu->Tophane (west/north), and its creeks running north.
    horn = ((side(E, N, HORN_A, HORN_B) > 0) & (N > 800.0)) | ((E < 900.0) & (N > 1650.0))
    bos = (side(E, N, MOUTH_A, MOUTH_B) > 0) & ~horn                # left of the mouth line: north-east
    # Down-stream hints.
    hint_e = np.full(E.shape, -0.95)
    hint_n = np.full(E.shape, -0.30)                       # the Marmara: west along the peninsula
    hint_e[bos], hint_n[bos] = -0.37, -0.93                # the Bosphorus: SSW
    hint_e[horn], hint_n[horn] = 0.55, -0.83               # the Horn: out to its mouth
    sgn = np.sign(ax_e * hint_e + ax_n * hint_n)
    sgn[sgn == 0] = 1.0
    ax_e *= sgn
    ax_n *= sgn
    c = np.clip((coh - 0.2) / 0.5, 0.0, 1.0)
    # Out in the open the channel axis means nothing: the hint.
    c *= np.clip(1.0 - (dist - 900.0) / 900.0, 0.0, 1.0)
    de = c * ax_e + (1 - c) * hint_e
    dn = c * ax_n + (1 - c) * hint_n
    ln = np.hypot(de, dn) + 1e-9
    de /= ln
    dn /= ln

    mouth = 0.5 * (MOUTH_A + MOUTH_B)
    from_mouth = np.hypot(E - mouth[0], N - mouth[1])
    strength = np.where(bos, 1.0, 0.08 + 0.55 * np.exp(-from_mouth / 2600.0))
    strength = np.where(horn, 0.06, strength)
    strength = ndimage.gaussian_filter(strength, 3.0)
    # Slack along the shores, fastest mid-stream.
    strength *= np.clip(dist / 220.0, 0.25, 1.0)
    strength = np.where(fwater, strength, 0.0)
    # Spread the water values a few pixels onto the land so the GPU's
    # bilinear filter does not darken the shoreline's current.
    for _ in range(3):
        grown = ndimage.grey_dilation(strength, size=3)
        strength = np.where(fwater, strength, grown)

    far = np.clip(dist / 1500.0, 0.0, 1.0)
    rgb = np.stack([strength, de * 0.5 + 0.5, dn * 0.5 + 0.5, far], axis=-1)
    Image.fromarray(np.clip(np.round(rgb * 255), 0, 255).astype(np.uint8), "RGBA").save(
        OUT / "sea_flow.png", optimize=True)

    doc = {
        "source": "Derived from water.png (OpenStreetMap coastline, ODbL 1.0) by tools/bake_istanbul_sea.py",
        "shore": {"bounds_enu": [e0, n0, e1, n1], "res_m": res, "size_px": [w, h],
                  "encoding": "d_m = sign(s) * s^2, s = (v - 128) / 12; + over water"},
        "flow": {"bounds_enu": [e0, n1 - fh * FLOW_RES, e0 + fw * FLOW_RES, n1], "res_m": FLOW_RES,
                 "size_px": [fw, fh], "encoding": "R strength 0..1, G/B dir (east, north) * 0.5 + 0.5, A shore distance / 1500 m"},
    }
    (OUT / "sea.json").write_text(json.dumps(doc, indent=1))
    for f in ("sea_shore.png", "sea_flow.png"):
        print(f, (OUT / f).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
