#!/usr/bin/env python3
"""Bake the shoreline the photoreal Manhattan scene draws and answers with.

Input: assets/manhattan/water.png (fetch_manhattan_water.py: OSM coastline,
ODbL, plus USGS NHD) and piers.json (fetch_manhattan_planimetric.py: NYC
Planimetric piers, public domain).

Outputs (dataset frame of buildings.json, metres east/north of the Battery
Park datum turned 29 deg onto the street grid):

  shore.png / shore.json   signed distance to the shore, + over water, at
                           4 m/px over Manhattan and its rivers (FINE box).
  shore_far.png            the same at 8 m/px over the whole mask extent.
                           Both 8-bit: 128 is the shoreline, 4 steps per
                           metre, so +-32 m; farther saturates.
  coast.json               the shoreline (the 0 isoline of that field) as
                           polylines with the water on their RIGHT, for the
                           bulkheads (manhattan_coast.gd); each tagged with
                           whether it is Manhattan's own shore.

ONE FIELD FOR EVERYTHING. The ground shader cuts the land where the field
crosses 0, the water shader puts its shallows and its wash along the walls
from it, the bulkheads stand on its 0 isoline and env.is_water() samples it
bilinearly the same way. Drawing and answering from one field is what keeps
them agreeing; the first cut sampled water.png by nearest pixel and the
walls by marching squares on it, and they disagreed by up to 4 m along
every diagonal shore.

WHY A DISTANCE FIELD, NOT THE MASK: water.png is binary at 4 m, so its
bilinear 0.5 isoline is a staircase on every shore not parallel to the grid
(most of the Hudson's piers are, the East River's shore is not). The signed
distance, blurred by ~1 px, has a smooth 0 isoline and also gives the water
shader its distance to the wall for free.

INLAND WATER is land here: only water joined to the rivers and harbour (or
the mask's edge) is kept. Lakes stand above street level and belong to the
park's own terrain.

PIERS are carved OUT of the land: the planimetric piers are decks on piles
over the water and are drawn as such (manhattan_piers.gd), but the OSM
coastline wraps about a quarter of the big ones (it maps the pier as land).
Where it does, the bulkhead would run round the pier's edge and the deck
would sit on solid ground. Carving them makes the shore run along the
bulkhead line behind them, as it really does.

    python3 bake_manhattan_shore.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

STEPS_PER_M = 4.0
#: The fine field's box: the island's buildings (-2834..2538, 140..21708)
#: plus 1.2 km, which reaches both banks of the East River and most of the
#: Hudson.
FINE_BOX = (-4100.0, -1100.0, 3800.0, 23000.0)
SIMPLIFY_M = 0.4


def carve_piers(water: np.ndarray, wb, res: float, piers: list) -> int:
    im = Image.fromarray(water.astype(np.uint8) * 255)
    dr = ImageDraw.Draw(im)
    n = 0
    for p in piers:
        ring = [((e - wb[0]) / res - 0.5, (wb[3] - nn) / res - 0.5) for e, nn in p["rings"][0]]
        if len(ring) >= 3:
            dr.polygon(ring, fill=255)
            n += 1
    water[:] = np.asarray(im) > 127
    return n


def signed_distance(water: np.ndarray, res: float) -> np.ndarray:
    # Pixel-centre distances to the nearest pixel of the other class, less
    # half a pixel so the 0 crossing lies on the pixel edge between them.
    d_in = ndimage.distance_transform_edt(water) - 0.5
    d_out = ndimage.distance_transform_edt(~water) - 0.5
    sd = np.where(water, d_in, -d_out) * res
    return ndimage.gaussian_filter(sd, 1.0)


def encode(sd: np.ndarray) -> np.ndarray:
    return np.clip(np.round(128.0 + sd * STEPS_PER_M), 0, 255).astype(np.uint8)


# ------------------------------------------------------------ marching squares
def contours(v: np.ndarray) -> list[np.ndarray]:
    """0 isolines of v (rows, cols) as arrays of (row, col) points."""
    a = v[:-1, :-1] > 0       # TL
    b = v[:-1, 1:] > 0        # TR
    c = v[1:, 1:] > 0         # BR
    d = v[1:, :-1] > 0        # BL
    case = a * 8 + b * 4 + c * 2 + d * 1
    rows, cols = np.nonzero((case != 0) & (case != 15))
    H, W = v.shape
    # Edge ids: horizontal edge h(r, c) joins (r,c)-(r,c+1); vertical v(r, c)
    # joins (r,c)-(r+1,c). Encoded as one int.
    def hid(r, c):
        return (r * W + c) * 2

    def vid(r, c):
        return (r * W + c) * 2 + 1
    # Per case, the pairs of cell edges the line joins (T, R, B, L).
    table = {1: [("L", "B")], 2: [("B", "R")], 3: [("L", "R")], 4: [("T", "R")],
             6: [("T", "B")], 7: [("L", "T")], 8: [("L", "T")], 9: [("T", "B")],
             11: [("T", "R")], 12: [("L", "R")], 13: [("B", "R")], 14: [("L", "B")]}
    segs = []
    for r, cc in zip(rows.tolist(), cols.tolist()):
        k = int(case[r, cc])
        e = {"T": hid(r, cc), "B": hid(r + 1, cc), "L": vid(r, cc), "R": vid(r, cc + 1)}
        if k in (5, 10):
            centre = v[r, cc] + v[r, cc + 1] + v[r + 1, cc] + v[r + 1, cc + 1] > 0
            if (k == 5) == centre:
                pairs = [("L", "T"), ("B", "R")]
            else:
                pairs = [("L", "B"), ("T", "R")]
        else:
            pairs = table[k]
        for p, q in pairs:
            segs.append((e[p], e[q]))
    # Link segments sharing an edge into chains.
    adj: dict[int, list[int]] = {}
    for s, (p, q) in enumerate(segs):
        adj.setdefault(p, []).append(s)
        adj.setdefault(q, []).append(s)
    used = np.zeros(len(segs), bool)

    def point(eid: int) -> tuple[float, float]:
        cell, kind = divmod(eid, 2)
        r, c = divmod(cell, W)
        if kind == 0:   # horizontal: (r,c)-(r,c+1)
            v0, v1 = v[r, c], v[r, c + 1]
            t = v0 / (v0 - v1)
            return (r, c + t)
        v0, v1 = v[r, c], v[r + 1, c]
        t = v0 / (v0 - v1)
        return (r + t, c)

    out = []
    for s0 in range(len(segs)):
        if used[s0]:
            continue
        used[s0] = True
        chain = [segs[s0][0], segs[s0][1]]
        # Grow forward from the tail, then backward from the head.
        for grow_tail in (True, False):
            while True:
                end = chain[-1] if grow_tail else chain[0]
                nxt = [s for s in adj[end] if not used[s]]
                if not nxt:
                    break
                s = nxt[0]
                used[s] = True
                p, q = segs[s]
                o = q if p == end else p
                if grow_tail:
                    chain.append(o)
                else:
                    chain.insert(0, o)
        out.append(np.array([point(e) for e in chain]))
    return out


def simplify(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker."""
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        ab = b - a
        L = np.hypot(*ab)
        seg = pts[i + 1:j]
        if L < 1e-9:
            dist = np.hypot(*(seg - a).T)
        else:
            dist = np.abs(ab[0] * (seg[:, 1] - a[1]) - ab[1] * (seg[:, 0] - a[0])) / L
        k = int(np.argmax(dist))
        if dist[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return pts[keep]


def bilinear(sd: np.ndarray, x: float, y: float) -> float:
    return float(ndimage.map_coordinates(sd, [[y], [x]], order=1, mode="nearest")[0])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent
                                         / "godot" / "assets" / "manhattan"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    meta = json.loads((out / "water.json").read_text())
    wb = [float(v) for v in meta["bounds_enu"]]
    res = float(meta["res_m"])
    water = np.asarray(Image.open(out / "water.png").convert("L")) > 127
    land0 = ~water.copy()
    piers = json.loads((out / "piers.json").read_text())["piers"] if (out / "piers.json").exists() else []
    # Only the tidal water: the rivers, the harbour and anything joined to
    # them. Lakes and ponds (Central Park's Reservoir, the Lake, Harlem Meer;
    # ponds on the far shores) stand above street level and are someone
    # else's to draw (the park's terrain); here they are land, flat ground
    # under the park, so no river surface at SEA_Y shows through them.
    lab, n = ndimage.label(water)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(n + 1)) * res * res / 1e6
    keep = [k for k in range(1, n + 1) if k in border or sizes[k] > 3.0]
    inland = int(water.sum() - np.isin(lab, keep).sum())
    water = np.isin(lab, keep)
    print(f"tidal water: {len(keep)} bodies; {inland * res * res / 1e4:.0f} ha of inland water made land")
    # Piers after that, so a pier the coastline mapped as land (a pocket of
    # carved water inside it) is not taken for a pond.
    print(f"carved {carve_piers(water, wb, res, piers)} piers")
    sd = signed_distance(water, res)

    # Fine field: a crop of the 4 m field.
    x0 = int(round((FINE_BOX[0] - wb[0]) / res))
    x1 = int(round((FINE_BOX[2] - wb[0]) / res))
    y0 = int(round((wb[3] - FINE_BOX[3]) / res))
    y1 = int(round((wb[3] - FINE_BOX[1]) / res))
    fine = sd[y0:y1, x0:x1]
    Image.fromarray(encode(fine)).save(out / "shore.png", optimize=True)
    # Coarse field: 2 x 2 means (a linear field's mean is its value at the
    # block's centre, so the coarse isoline lies on the fine one).
    h2, w2 = sd.shape[0] // 2, sd.shape[1] // 2
    coarse = sd[:h2 * 2, :w2 * 2].reshape(h2, 2, w2, 2).mean(axis=(1, 3))
    Image.fromarray(encode(coarse)).save(out / "shore_far.png", optimize=True)
    fine_bounds = [wb[0] + x0 * res, wb[3] - y1 * res, wb[0] + x1 * res, wb[3] - y0 * res]
    (out / "shore.json").write_text(json.dumps({
        "source": "derived from water.png (OSM coastline, ODbL; USGS NHD) and piers.json (NYC Planimetric, public domain)",
        "note": "signed distance to the shore, + over water; value = 128 + metres * steps_per_m; row 0 north",
        "steps_per_m": STEPS_PER_M,
        "fine": {"bounds_enu": fine_bounds, "res_m": res, "size_px": [fine.shape[1], fine.shape[0]]},
        "far": {"bounds_enu": wb, "res_m": res * 2, "size_px": [w2, h2]},
    }, indent=1))

    # Shore lines, from the same (full 4 m) field.
    lab, _ = ndimage.label(~water)
    own = set()
    b = json.loads((out / "buildings.json").read_text())["buildings"]
    count: dict[int, int] = {}
    for bb in b:
        r = bb["r"]
        x = int((r[0] + r[2] / 2 - wb[0]) / res)
        y = int((wb[3] - (r[1] + r[3] / 2)) / res)
        if 0 <= x < lab.shape[1] and 0 <= y < lab.shape[0] and lab[y, x] > 0:
            count[int(lab[y, x])] = count.get(int(lab[y, x]), 0) + 1
    sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(lab.max() + 1)) * res * res / 1e6
    for k, c in count.items():
        if c / max(sizes[k], 1e-6) > 50.0:
            own.add(k)
    lines = []
    total = 0.0
    for pts in contours(sd):
        if len(pts) < 3:
            continue
        # (row, col) pixel centres -> dataset metres.
        en = np.stack([wb[0] + (pts[:, 1] + 0.5) * res, wb[3] - (pts[:, 0] + 0.5) * res], axis=1)
        L = float(np.sum(np.hypot(*np.diff(en, axis=0).T)))
        if L < 12.0:
            continue
        en = simplify(en, SIMPLIFY_M)
        # Water on the right: probe 3 m right of the middle segment.
        m = len(en) // 2
        a, bq = en[max(0, m - 1)], en[min(len(en) - 1, m)]
        t = bq - a
        t = t / max(np.hypot(*t), 1e-9)
        q = (a + bq) / 2 + np.array([t[1], -t[0]]) * 3.0
        if bilinear(sd, (q[0] - wb[0]) / res - 0.5, (wb[3] - q[1]) / res - 0.5) < 0:
            en = en[::-1]
            q = (a + bq) / 2 - np.array([t[1], -t[0]]) * 3.0
        # Whose shore: the land component 6 m to the left.
        a, bq = en[max(0, len(en) // 2 - 1)], en[min(len(en) - 1, len(en) // 2)]
        t = (bq - a) / max(np.hypot(*(bq - a)), 1e-9)
        lp = (a + bq) / 2 - np.array([t[1], -t[0]]) * 6.0
        lx, ly = int((lp[0] - wb[0]) / res), int((wb[3] - lp[1]) / res)
        mine = 0 <= lx < lab.shape[1] and 0 <= ly < lab.shape[0] and int(lab[ly, lx]) in own
        lines.append({"own": bool(mine), "pts": [[round(float(e), 2), round(float(n), 2)] for e, n in en]})
        total += L
    (out / "coast.json").write_text(json.dumps({
        "source": "0 isoline of shore.png's field (water.png: OSM coastline ODbL, USGS NHD; NYC Planimetric piers)",
        "note": "dataset frame; water on the right of each line; own = Manhattan's own land",
        "lines": lines}, separators=(",", ":")))
    print(f"coast: {len(lines)} lines, {total / 1000:.0f} km; own {sum(1 for l in lines if l['own'])}")
    for f in ("shore.png", "shore_far.png", "coast.json"):
        print(f, (out / f).stat().st_size // 1024, "KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
