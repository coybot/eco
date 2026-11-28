#!/usr/bin/env python3
"""Bake where Baylands' moving life goes: assets/baylands/life.json.

Read by drone/sim/godot/scripts/baylands_life*.gd (the moving world,
--world-life). Everything is measured or mapped, not invented:

  roads     (life_roads.json, next to life.json) the drivable network from
            OpenStreetMap (features.json), in life_roads.gd's roads.json
            layout, plus each way's posted
            speed (OSM maxspeed, mph -> m/s) and, on bridges, the deck
            height the 2020 lidar measured (class 17, bridge.f32).
  signals   OSM traffic_signals nodes.
  closed    ways OSM closes to motor vehicles.
  walk      the path network (footways, cycleways, the Bay Trail levees),
            each edge tagged park / trail / urban.
  lots      surface car parks (OSM amenity=parking) cut into stalls on the
            lot's own axis (2.7 x 5.5 m stalls, 7.3 m aisles); a stall is
            dropped where the lidar sees a building, a tree or a pole over it.
            Each lot is typed (park, sports, office, home, shop, event).
  picnic    picnic tables / sites / barbecues in the park (OSM points).
  pitches   soccer pitches (Twin Creeks), as oriented rectangles.
  park      Sunnyvale Baylands Park's boundary.
  meadow    open grass spots in the park the lidar sees as bare (dsm - dtm
            < 0.3 m), off the paths, out of the water.
  birds     habitat samples from the lidar water mask (0.5 m, resampled to
            4 m): open pond water, pond edges, slough/channel edges, plus
            mapped fields (pitches, park lawns), marsh and the landfill /
            transfer station.

Run on hoopoe (needs the 0.5 m rasters; ~2 GB RAM):
    uv run --no-project -q --with numpy --with scipy python bake_baylands_life.py \
        --features ../godot/assets/baylands/features.json \
        --rasters ~/bay/data/rasters --out ../godot/assets/baylands/life.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
from pathlib import Path

import numpy as np
from scipy import ndimage

REGION = (-2500.0, -2400.0, 2500.0, 2400.0)
CELL = 0.5
KEEP = (REGION[0] - 100, REGION[1] - 100, REGION[2] + 100, REGION[3] + 100)

# OSM highway -> life_roads class.
ROAD_CLASS = {
    "motorway": "motorway", "trunk": "motorway",
    "motorway_link": "motorway_link", "trunk_link": "motorway_link",
    "primary": "primary", "primary_link": "primary",
    "secondary": "secondary", "secondary_link": "secondary",
    "tertiary": "street", "tertiary_link": "street", "unclassified": "street",
    "residential": "street", "service": "service",
}
# Free-flow speeds (m/s) where OSM has no maxspeed. California defaults:
# freeway 65 mph, expressway 45-50, arterial 40-45, residential 25, service 10.
MPH = 0.44704
DEFAULT_SPEED = {"motorway": 65 * MPH, "motorway_link": 40 * MPH, "primary": 45 * MPH,
                 "secondary": 40 * MPH, "street": 30 * MPH, "service": 10 * MPH}
WALK_HW = {"footway", "cycleway", "path", "track", "pedestrian", "steps", "bridleway"}

STALL_W = 2.7
STALL_D = 5.5
AISLE = 7.3


# ------------------------------------------------------------------ geometry
def poly_area(p):
    s = 0.0
    for i in range(len(p) - 1):
        s += p[i][0] * p[i + 1][1] - p[i + 1][0] * p[i][1]
    return abs(s) * 0.5


def centroid(p):
    n = len(p) - 1 if p[0] == p[-1] else len(p)
    return (sum(q[0] for q in p[:n]) / n, sum(q[1] for q in p[:n]) / n)


def inside(poly, x, y):
    c = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L < 1e-9 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def simplify(pts, tol):
    """Douglas-Peucker."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            d = seg_dist(*pts[i], *pts[a], *pts[b])
            if d > best:
                best, bi = d, i
        if best > tol:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def main_axis(poly):
    """Angle (rad) of the polygon's dominant edge direction, mod 90 deg."""
    hist = {}
    for i in range(len(poly) - 1):
        dx = poly[i + 1][0] - poly[i][0]
        dy = poly[i + 1][1] - poly[i][1]
        L = math.hypot(dx, dy)
        if L < 0.5:
            continue
        a = math.atan2(dy, dx) % (math.pi / 2)
        k = round(a / math.radians(2))
        hist[k] = hist.get(k, 0.0) + L
    if not hist:
        return 0.0
    k = max(hist, key=hist.get)
    return k * math.radians(2)


# ------------------------------------------------------------------ rasters
class Rasters:
    def __init__(self, d: Path):
        g = json.loads((d / "grid.json").read_text())
        self.rows, self.cols = g["rows"], g["cols"]

        def mm(name, dt):
            return np.memmap(d / name, dtype=dt, mode="r", shape=(self.rows, self.cols))
        self.dtm = mm("dtm.f32", np.float32)
        self.dsm = mm("dsm.f32", np.float32)
        self.bld = mm("bld.f32", np.float32)
        self.veg = mm("veg.f32", np.float32)
        self.other = mm("other.f32", np.float32)
        self.bridge = mm("bridge.f32", np.float32)
        self.water = mm("water_full.u8", np.uint8)

    def rc(self, e, n):
        c = int((e - REGION[0]) / CELL)
        r = int((REGION[3] - n) / CELL)
        return r, c

    def ok(self, r, c):
        return 0 <= r < self.rows and 0 <= c < self.cols

    def window(self, arr, e, n, rad):
        r, c = self.rc(e, n)
        k = max(1, int(rad / CELL))
        r0, r1 = max(0, r - k), min(self.rows, r + k + 1)
        c0, c1 = max(0, c - k), min(self.cols, c + k + 1)
        if r0 >= r1 or c0 >= c1:
            return None
        return np.asarray(arr[r0:r1, c0:c1])

    def ground(self, e, n):
        r, c = self.rc(e, n)
        return float(self.dtm[r, c]) if self.ok(r, c) else 0.0

    def deck(self, e, n):
        w = self.window(self.bridge, e, n, 1.5)
        if w is None or not np.isfinite(w).any():
            return None
        return float(np.nanmax(w))

    def clutter(self, e, n, rad):
        """Highest thing over the ground near (e, n): building, tree, pole (m)."""
        g = self.ground(e, n)
        top = 0.0
        for arr in (self.bld, self.veg, self.other):
            w = self.window(arr, e, n, rad)
            if w is not None and np.isfinite(w).any():
                top = max(top, float(np.nanmax(w)) - g)
        return top

    def is_water(self, e, n):
        r, c = self.rc(e, n)
        return self.ok(r, c) and self.water[r, c] > 0


def in_keep(p):
    return KEEP[0] <= p[0] <= KEEP[2] and KEEP[1] <= p[1] <= KEEP[3]


def parse_speed(v):
    if not v:
        return None
    m = re.match(r"\s*([\d.]+)\s*(mph)?", v)
    if not m:
        return None
    x = float(m.group(1))
    return x * MPH if m.group(2) else x / 3.6


# ------------------------------------------------------------------ roads
def bake_roads(feat, R: Rasters):
    nodes, index = [], {}

    def node(p):
        k = (round(p[0] * 100), round(p[1] * 100))
        if k not in index:
            index[k] = len(nodes)
            nodes.append([round(p[0], 2), round(p[1], 2)])
        return index[k]

    names, name_ix = [], {}
    edges, closed = [], []
    for w in feat["roads"]:
        t = w["tags"]
        cls = ROAD_CLASS.get(t.get("highway", ""))
        if cls is None or t.get("area") == "yes":
            continue
        if t.get("service") in ("driveway", "drive-through", "emergency_access"):
            continue
        pts = [p for p in w["pts"]]
        if not any(in_keep(p) for p in pts):
            continue
        ow = t.get("oneway", "")
        oneway = ow in ("yes", "1", "true") or (cls.startswith("motorway") and ow != "no") \
            or t.get("junction") == "roundabout"
        if ow == "-1":
            pts = pts[::-1]
            oneway = True
        try:
            lanes = int(str(t.get("lanes", "0")).split(";")[0])
        except ValueError:
            lanes = 0
        if lanes <= 0:
            lanes = {"motorway": 3 if oneway else 6, "motorway_link": 1, "primary": 4,
                     "secondary": 4, "street": 2, "service": 2}[cls]
            if oneway and cls in ("primary", "secondary"):
                lanes = 2
        try:
            width = float(str(t.get("width", "0")).split()[0])
        except ValueError:
            width = 0.0
        flags = 0
        if t.get("bridge") not in (None, "no"):
            flags |= 1
        if t.get("tunnel") not in (None, "no") or t.get("covered") == "yes":
            flags |= 2
        try:
            layer = int(t.get("layer", "0"))
        except ValueError:
            layer = 0
        flags |= 4 if layer > 0 else 0
        flags |= 8 if layer < 0 else 0
        nm = t.get("name") or t.get("ref") or ""
        if nm not in name_ix:
            name_ix[nm] = len(names)
            names.append(nm)
        sp = parse_speed(t.get("maxspeed")) or DEFAULT_SPEED[cls]
        if t.get("service") == "parking_aisle":
            sp = 3.0
        # Private campus roads carry their own staff's cars; only roads closed
        # to everyone's motor vehicles are closed.
        noveh = t.get("access") == "no" and t.get("motor_vehicle") not in ("yes", "designated", "private") \
            or t.get("motor_vehicle") == "no" or t.get("vehicle") == "no"
        ids = [node(p) for p in pts]
        for a, b in zip(ids, ids[1:]):
            if a == b:
                continue
            edges.append([a, b, cls, 1 if oneway else 0, lanes, width, 0, name_ix[nm], flags,
                          round(sp, 2)])
        if noveh:
            flat = []
            for p in pts:
                flat += [round(p[0], 2), round(p[1], 2)]
            closed.append([flat])
    # Lidar deck heights on bridge nodes.
    deck = {}
    on_bridge = set()
    for e in edges:
        if e[8] & 1:
            on_bridge.update((e[0], e[1]))
    for i in on_bridge:
        z = R.deck(*nodes[i])
        g = R.ground(*nodes[i])
        if z is not None and z > g + 0.8:
            deck[str(i)] = round(z, 2)
    signals = [[round(p["p"][0], 1), round(p["p"][1], 1)] for p in feat["points"]
               if p["tags"].get("highway") == "traffic_signals" and in_keep(p["p"])]
    print(f"roads: {len(nodes)} nodes, {len(edges)} edges, {len(deck)} lidar deck nodes, "
          f"{len(signals)} signals, {len(closed)} closed ways")
    return {"nodes": nodes, "edges": edges, "names": names}, deck, signals, closed


# ------------------------------------------------------------------ paths
def bake_walk(feat, park_polys, R: Rasters):
    nodes, index = [], {}

    def node(p):
        k = (round(p[0] * 10), round(p[1] * 10))
        if k not in index:
            index[k] = len(nodes)
            nodes.append([round(p[0], 1), round(p[1], 1)])
        return index[k]

    edges = []
    for w in feat["paths"]:
        t = w["tags"]
        if t.get("highway") not in WALK_HW:
            continue
        if t.get("access") in ("private", "no") and t.get("foot") not in ("yes", "designated") \
                and t.get("bicycle") not in ("yes", "designated"):
            continue
        if t.get("foot") == "no":
            continue
        pts = [p for p in w["pts"] if in_keep(p)]
        if len(pts) < 2:
            continue
        pts = simplify(pts, 0.4)
        c = centroid(pts + [pts[0]]) if len(pts) > 2 else ((pts[0][0] + pts[-1][0]) / 2,
                                                           (pts[0][1] + pts[-1][1]) / 2)
        in_park = any(inside(pp, c[0], c[1]) for pp in park_polys)
        nm = t.get("name", "")
        surf = t.get("surface", "")
        if in_park:
            cat = 0                                  # park path
        elif "Bay Trail" in nm or "Trail" in nm or surf in ("gravel", "compacted", "fine_gravel",
                                                            "dirt", "ground", "unpaved") \
                or t.get("highway") in ("track", "path"):
            cat = 1                                  # levee / creek trail
        else:
            cat = 2                                  # urban sidewalk / footway
        bike = 1 if (t.get("highway") == "cycleway" or t.get("bicycle") in ("yes", "designated")) else 0
        ids = [node(p) for p in pts]
        for a, b in zip(ids, ids[1:]):
            if a != b:
                edges.append([a, b, cat, bike])
    print(f"walk: {len(nodes)} nodes, {len(edges)} edges "
          f"(park {sum(1 for e in edges if e[2] == 0)}, trail {sum(1 for e in edges if e[2] == 1)})")
    return {"nodes": nodes, "edges": edges}


# ------------------------------------------------------------------ lots
def lot_kind(lot, areas, park_polys, sports_polys):
    t = lot["tags"]
    c = centroid(lot["pts"])
    nm = t.get("name", "")
    if re.search(r"(Red|Blue|Green|Yellow|Gold|Orange) Lot", nm) or "Great America" in nm:
        return "event"
    if any(inside(p, *c) for p in sports_polys):
        return "sports"
    if any(inside(p, *c) for p in park_polys):
        return "park"
    best = None
    for a in areas:
        lu = a["tags"].get("landuse")
        if lu in ("residential", "commercial", "industrial", "retail") and inside(a["pts"], *c):
            ar = poly_area(a["pts"])
            if best is None or ar < best[0]:
                best = (ar, lu)
    if best is None:
        return "office"
    return {"residential": "home", "commercial": "office", "industrial": "office", "retail": "shop"}[best[1]]


def stalls_in(poly, R: Rasters, street_side: bool):
    ang = main_axis(poly)
    ca, sa = math.cos(ang), math.sin(ang)
    loc = [((x * ca + y * sa), (-x * sa + y * ca)) for x, y in poly]
    us = [p[0] for p in loc]
    vs = [p[1] for p in loc]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    if (u1 - u0) < (v1 - v0):
        # Rows along the long side.
        ang += math.pi / 2
        ca, sa = math.cos(ang), math.sin(ang)
        loc = [((x * ca + y * sa), (-x * sa + y * ca)) for x, y in poly]
        us = [p[0] for p in loc]
        vs = [p[1] for p in loc]
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    module = STALL_D * 2 + AISLE
    rows = [STALL_D * 0.5] if street_side else [STALL_D * 0.5, STALL_D * 1.5 + AISLE]
    best = []
    for phase_v in np.linspace(0.0, module if not street_side else 1.0, 6, endpoint=False):
        for phase_u in (0.0, STALL_W * 0.5):
            out = []
            v = v0 + phase_v - module
            while v < v1:
                for k, rv in enumerate(rows):
                    cv = v + rv
                    u = u0 + phase_u + STALL_W * 0.5
                    while u < u1:
                        ok = True
                        for du, dv in ((-1.2, -2.6), (1.2, -2.6), (1.2, 2.6), (-1.2, 2.6)):
                            if not inside(loc, u + du, cv + dv):
                                ok = False
                                break
                        if ok:
                            # Nose toward the aisle side (+v for row 0, -v for row 1).
                            out.append((u, cv, 1.0 if k == 0 else -1.0))
                        u += STALL_W
                v += module
            if len(out) > len(best):
                best = out
    res = []
    for u, v, s in best:
        x = u * ca - v * sa
        y = u * sa + v * ca
        if R.clutter(x, y, 1.0) > 1.4 or R.is_water(x, y):
            continue
        # Heading of the car's nose, ENU radians CCW from east.
        nose = ang + math.pi / 2 * s
        res.append([round(x, 1), round(y, 1), round(nose, 2)])
    return res


def bake_lots(feat, park_polys, sports_polys, R: Rasters):
    lots = []
    total = 0
    for a in feat["areas"]:
        t = a["tags"]
        if t.get("amenity") != "parking":
            continue
        if t.get("parking") in ("multi-storey", "underground", "garage", "rooftop") or t.get("building"):
            continue
        p = a["pts"]
        c = centroid(p)
        if not (REGION[0] < c[0] < REGION[2] and REGION[1] < c[1] < REGION[3]):
            continue
        if poly_area(p) < 60:
            continue
        st = stalls_in(p, R, t.get("parking") == "street_side")
        if not st:
            continue
        kind = lot_kind(a, feat["areas"], park_polys, sports_polys)
        flat = []
        for s in st:
            flat += s
        lots.append({"kind": kind, "c": [round(c[0], 1), round(c[1], 1)], "s": flat})
        total += len(st)
    by = {}
    for l in lots:
        by[l["kind"]] = by.get(l["kind"], 0) + len(l["s"]) // 3
    print(f"lots: {len(lots)} lots, {total} stalls {by}")
    return lots


# ------------------------------------------------------------------ park
def oriented_rect(poly):
    ang = main_axis(poly)
    best = None
    for a in (ang, ang + math.pi / 2):
        ca, sa = math.cos(a), math.sin(a)
        us = [x * ca + y * sa for x, y in poly]
        vs = [-x * sa + y * ca for x, y in poly]
        L, W = max(us) - min(us), max(vs) - min(vs)
        cu, cv = (max(us) + min(us)) / 2, (max(vs) + min(vs)) / 2
        if best is None or L > best[3]:
            best = (cu * ca - cv * sa, cu * sa + cv * ca, a, L, W)
    return [round(best[0], 1), round(best[1], 1), round(best[2], 3), round(best[3], 1), round(best[4], 1)]


def near_path(x, y, walk, rad):
    for a, b, *_ in walk["edges"]:
        pa, pb = walk["nodes"][a], walk["nodes"][b]
        if min(pa[0], pb[0]) - rad > x or max(pa[0], pb[0]) + rad < x:
            continue
        if min(pa[1], pb[1]) - rad > y or max(pa[1], pb[1]) + rad < y:
            continue
        if seg_dist(x, y, pa[0], pa[1], pb[0], pb[1]) < rad:
            return True
    return False


def bake_meadow(park_polys, walk, lots, R: Rasters, rng):
    spots = []
    lot_pts = []
    for l in lots:
        s = l["s"]
        lot_pts += [(s[i], s[i + 1]) for i in range(0, len(s), 3)]
    for poly in park_polys:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        x = min(xs)
        while x < max(xs):
            y = min(ys)
            while y < max(ys):
                px, py = x + rng.uniform(-4, 4), y + rng.uniform(-4, 4)
                if inside(poly, px, py) and not R.is_water(px, py):
                    r, c = R.rc(px, py)
                    w = R.window(R.dsm, px, py, 3.0)
                    g = R.ground(px, py)
                    if w is not None and float(np.nanmax(w)) - g < 0.35 \
                            and R.clutter(px, py, 4.0) < 0.5 and not near_path(px, py, walk, 5.0) \
                            and not any(abs(px - q[0]) < 8 and abs(py - q[1]) < 8 for q in lot_pts):
                        spots.append([round(px, 1), round(py, 1)])
                y += 14.0
            x += 14.0
    print(f"meadow: {len(spots)} open grass spots")
    return spots


# ------------------------------------------------------------------ birds
def bake_birds(feat, R: Rasters, rng):
    # Water at 4 m: share of 0.5 m water cells.
    k = 8
    w = np.asarray(R.water[: R.rows // k * k, : R.cols // k * k], dtype=np.float32)
    w = w.reshape(R.rows // k, k, R.cols // k, k).mean(axis=(1, 3)) > 0.5
    res = CELL * k
    lab, nlab = ndimage.label(w)
    sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, nlab + 1)) * res * res
    edt_w = ndimage.distance_transform_edt(w) * res           # water: metres to land
    maxd = ndimage.maximum(edt_w, lab, index=np.arange(1, nlab + 1))
    pond_ids = {i + 1 for i in range(nlab) if sizes[i] > 30000 and maxd[i] > 30}
    slough_ids = {i + 1 for i in range(nlab) if sizes[i] > 2000 and (i + 1) not in pond_ids}
    rows, cols = w.shape

    def enu(r, c):
        return (REGION[0] + (c + 0.5) * res, REGION[3] - (r + 0.5) * res)

    def sample(mask, spacing, cap):
        rr, cc = np.nonzero(mask)
        order = list(range(len(rr)))
        rng.shuffle(order)
        taken = []
        grid = {}
        for i in order:
            e, n = enu(rr[i], cc[i])
            key = (int(e // spacing), int(n // spacing))
            bad = False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for q in grid.get((key[0] + dx, key[1] + dy), []):
                        if (q[0] - e) ** 2 + (q[1] - n) ** 2 < spacing * spacing:
                            bad = True
                            break
            if bad:
                continue
            grid.setdefault(key, []).append((e, n))
            taken.append([round(e, 1), round(n, 1), round(float(edt_w[rr[i], cc[i]]), 1),
                          int(lab[rr[i], cc[i]])])
            if len(taken) >= cap:
                break
        return taken

    pond = np.isin(lab, list(pond_ids))
    slough = np.isin(lab, list(slough_ids))
    out = {
        "open_water": sample(pond & (edt_w > 40), 180.0, 220),
        "pond_water": sample(pond & (edt_w > 8) & (edt_w <= 60), 90.0, 300),
        "pond_edge": sample(pond & (edt_w <= 6), 60.0, 400),
        "slough_edge": sample(slough & (edt_w <= 4), 45.0, 500),
        "slough_water": sample(slough & (edt_w > 3), 70.0, 250),
    }
    # Mapped areas: fields (pitches, park lawns, grass), marsh, the landfill
    # and the transfer station (gulls).
    def area_samples(pred, spacing, cap, open_only=True):
        pts = []
        for a in feat["areas"]:
            if not pred(a["tags"]):
                continue
            p = a["pts"]
            xs = [q[0] for q in p]
            ys = [q[1] for q in p]
            if max(xs) < REGION[0] or min(xs) > REGION[2] or max(ys) < REGION[1] or min(ys) > REGION[3]:
                continue
            x = min(xs) + rng.uniform(0, spacing)
            while x < max(xs):
                y = min(ys) + rng.uniform(0, spacing)
                while y < max(ys):
                    if REGION[0] < x < REGION[2] and REGION[1] < y < REGION[3] and inside(p, x, y) \
                            and not R.is_water(x, y) and (not open_only or R.clutter(x, y, 6.0) < 0.6):
                        pts.append([round(x, 1), round(y, 1), round(poly_area(p))])
                    y += spacing
                x += spacing
        rng.shuffle(pts)
        return pts[:cap]

    out["field"] = area_samples(lambda t: t.get("leisure") == "pitch" and t.get("sport") in
                                ("soccer", "baseball", "softball", "american_football", "multi")
                                or t.get("landuse") in ("grass", "meadow", "recreation_ground")
                                or t.get("leisure") == "park", 35.0, 400)
    out["marsh"] = area_samples(lambda t: t.get("natural") == "wetland" or t.get("wetland"), 120.0, 300,
                                open_only=False)
    out["landfill"] = area_samples(lambda t: t.get("landuse") == "landfill"
                                   or t.get("amenity") in ("recycling", "waste_transfer_station")
                                   or t.get("man_made") == "wastewater_plant", 90.0, 120, open_only=False)
    print("birds:", {k: len(v) for k, v in out.items()}, f"ponds {len(pond_ids)}, sloughs {len(slough_ids)}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True)
    ap.add_argument("--rasters", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    feat = json.loads(Path(a.features).read_text())
    R = Rasters(Path(a.rasters).expanduser())
    rng = random.Random(20240520)
    park_polys = [x["pts"] for x in feat["areas"] if x["tags"].get("name") == "Sunnyvale Baylands Park"]
    sports_polys = [x["pts"] for x in feat["areas"] if x["tags"].get("name") == "Twin Creeks Sports Complex"]
    roads, deck, signals, closed = bake_roads(feat, R)
    walk = bake_walk(feat, park_polys + sports_polys, R)
    lots = bake_lots(feat, park_polys, sports_polys, R)
    picnic = []
    for p in feat["points"]:
        t = p["tags"]
        if t.get("leisure") == "picnic_table" or t.get("tourism") == "picnic_site" or t.get("amenity") == "bbq":
            if any(inside(pp, *p["p"]) for pp in park_polys + sports_polys):
                picnic.append([round(p["p"][0], 1), round(p["p"][1], 1),
                               {"picnic_table": 0, "picnic_site": 1}.get(t.get("leisure") or t.get("tourism"), 2)])
    for x in feat["areas"]:
        if x["tags"].get("tourism") == "picnic_site":
            c = centroid(x["pts"])
            if any(inside(pp, *c) for pp in park_polys):
                picnic.append([round(c[0], 1), round(c[1], 1), 1])
    pitches = [oriented_rect(x["pts"]) for x in feat["areas"]
               if x["tags"].get("leisure") == "pitch" and x["tags"].get("sport") == "soccer"
               and REGION[0] < centroid(x["pts"])[0] < REGION[2] and REGION[1] < centroid(x["pts"])[1] < REGION[3]]
    meadow = bake_meadow(park_polys, walk, lots, R, rng)
    birds = bake_birds(feat, R, rng)
    doc = {
        "source": "OpenStreetMap (ODbL) via features.json; USGS 3DEP 2020 lidar (public domain): "
                  "bridge decks, stall clutter, open grass, water mask. bake_baylands_life.py",
        "walk": walk, "lots": lots, "picnic": picnic, "pitches": pitches,
        "park": [[[round(x, 1), round(y, 1)] for x, y in pp] for pp in park_polys],
        "meadow": meadow, "birds": birds,
    }
    Path(a.out).write_text(json.dumps(doc, separators=(",", ":")))
    # The drivable network on its own, in life_roads.gd's roads.json layout
    # (its loader reads a file), with the deck heights, signals and closures.
    roads.update({"deck_z": deck, "signals": signals, "closed": closed,
                  "source": "OpenStreetMap (ODbL); bridge decks from the USGS 3DEP 2020 lidar (public domain)"})
    rp = Path(a.out).with_name("life_roads.json")
    rp.write_text(json.dumps(roads, separators=(",", ":")))
    print(f"wrote {rp} ({rp.stat().st_size / 1e6:.2f} MB)")
    print(f"picnic {len(picnic)}, pitches {len(pitches)}; wrote {a.out} "
          f"({Path(a.out).stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
