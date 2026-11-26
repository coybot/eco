#!/usr/bin/env python3
"""Bake the window cells the Istanbul building shader tiles its walls with.

    uv run --no-project --with numpy --with pillow \\
        python drone/sim/tools/bake_istanbul_facade_cells.py

Writes drone/sim/godot/assets/istanbul/istanbul_facade_cells/{mask,colour}.png
and cells.json (small, tracked). Drawn here, not photographed: no licence
restrictions.

Same scheme as bake_manhattan_facade_cells.py (whose Cell class this reuses):
one layer is one bay by one storey of wall, drawn supersampled and filtered
down, so the shader samples it mipmapped and a window fades to its own
average far off instead of flickering.

Channels: mask.r wall (the building's plaster / stone / clapboard colour),
mask.g trim (frames, surrounds, mouldings: the building's trim colour, or a
shop's own colour), mask.b glass (lit at night), mask.a shade; colour is
everything else's own colour (rooms, net curtains, shutters, AC units, iron).

What Istanbul's walls carry that New York's do not: white net curtains (tul)
in nearly every home window, so glass reads pale by day; PVC roller-shutter
boxes over the windows; balconies with solid plastered parapets, many glazed
in (cam balkon); split-AC condensers hung on the wall; louvred shutters and
moulded surrounds on the 19th-century blocks of Beyoglu and Galata;
clapboard and lattice (kafes) on the Ottoman wooden houses; banded stone and
brick (almasik) on the hans; steel roll-down shutters (kepenk) on the shops;
and exposed party walls of concrete frame and hollow clay brick.

The layer ORDER is the shader's table (scripts/istanbul_buildings.gd, L_*):
append new layers at the end.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bake_manhattan_facade_cells import Cell, PX, ROOM, ROOM_WARM, IRON, GOODS, lintel, sill  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "godot" / "assets" / "istanbul" / "istanbul_facade_cells"

TUL = (0.66, 0.66, 0.63)            # white net curtain seen from outside by day
TUL_DIM = (0.50, 0.50, 0.48)
ROLLER = (0.70, 0.69, 0.64)         # PVC roller shutter slats
KEPENK = (0.50, 0.52, 0.53)         # galvanised shop shutter
AC = (0.82, 0.82, 0.80)
GREEN_SHUTTER = (0.20, 0.33, 0.24)
BROWN_SHUTTER = (0.33, 0.21, 0.13)
WOOD_DARK = (0.30, 0.20, 0.12)
BRICK = (0.58, 0.38, 0.30)
CONCRETE = (0.58, 0.57, 0.54)
MARBLE = (0.86, 0.85, 0.82)
LAUNDRY = [(0.85, 0.85, 0.82), (0.25, 0.35, 0.60), (0.75, 0.25, 0.25), (0.85, 0.75, 0.35), (0.40, 0.55, 0.45)]


def tul(c: Cell, m, x0, x1, y0, y1):
    """Net curtain behind the glass: pale, soft vertical folds, a gap to the room."""
    c.glass(m, TUL)
    c.stripes(m, 0.035, "x", 0.45, 0.88)
    gap = m & (np.abs(c.x - (x0 + x1) / 2) < (x1 - x0) * 0.06)
    c.glass(gap, ROOM_WARM)


def pvc(c: Cell, m, x0, x1, y0, y1, mid=True):
    """A white PVC window: a frame, a mullion, a roller-shutter box over it."""
    c.frame(m, 0.016)
    if mid:
        c.trim(m & (np.abs(c.x - (x0 + x1) / 2) < 0.010))
    box = c.box(x0 - 0.015, y1, x1 + 0.015, y1 + 0.07)
    c.trim(box)
    c.shade(c.box(x0 - 0.015, y1, x1 + 0.015, y1 + 0.012), 0.7)


def marble_sill(c: Cell, x0, x1, y0):
    c.fixed(c.box(x0 - 0.03, y0 - 0.03, x1 + 0.03, y0), MARBLE)
    c.shade(c.box(x0 - 0.03, y0 - 0.07, x1 + 0.03, y0 - 0.03), 0.8)


def plaster(c: Cell, m, k=0.975):
    """Plaster: a faint blotchy unevenness (low frequency, averages away)."""
    rng = np.random.default_rng(3)
    for _ in range(5):
        x, y, r = rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(0.08, 0.2)
        c.shade(m & (((c.x - x) ** 2 + (c.y - y) ** 2) < r * r), min(1.0, k + rng.uniform(0, 0.02)))


# ------------------------------------------------------------------ apartments
def apt(variant: str):
    c = Cell()
    plaster(c, c.box(0, 0, 1, 1))
    x0, x1, y0, y1 = 0.27, 0.73, 0.30, 0.80
    m = c.box(x0, y0, x1, y1)
    if variant == "dark":
        c.glass(m, gradient=(ROOM, ROOM_WARM))
    elif variant == "tul":
        tul(c, m, x0, x1, y0, y1)
    elif variant == "shutter":
        tul(c, m, x0, x1, y0, y1)
        drop = y1 - (y1 - y0) * 0.55
        sh = m & (c.y > drop)
        c.fixed(sh, ROLLER)
        c.stripes(sh, 0.022, "y", 0.3, 0.8)
    elif variant == "ac":
        tul(c, m, x0, x1, y0, y1)
    elif variant == "bars":
        c.glass(m, gradient=(ROOM, ROOM_WARM))
        c.fixed(m & (np.mod(c.x - x0, 0.06) < 0.010), IRON)
        c.fixed(m & (np.mod(c.y - y0, 0.16) < 0.010), IRON)
    pvc(c, m, x0, x1, y0, y1)
    c.reveal(m, x0, y1, 0.035, 0.6)
    marble_sill(c, x0, x1, y0)
    if variant == "ac":
        # The split-AC condenser hung on brackets beside the window.
        u = c.box(0.76, 0.06, 0.98, 0.30)
        c.fixed(u, AC)
        fan = ((c.x - 0.85) ** 2 + (c.y - 0.18) ** 2) < 0.07 ** 2
        c.fixed(fan & u, (0.30, 0.30, 0.31))
        c.shade(c.box(0.76, 0.0, 0.98, 0.06), 0.7)
    # The floor slab shows as a faint line at the floor.
    c.shade(c.box(0, 0.0, 1, 0.02), 0.9)
    return c


def balcony(kind: str):
    """A balcony: the slab at the floor, a parapet, the door and window behind
    in its shade. `solid` plastered parapet, `rail` iron, `laundry` rail with
    washing, `closed` glazed in (cam balkon)."""
    c = Cell()
    back = c.box(0, 0.06, 1, 1)
    c.wall(back, 0.62)                                   # recessed: the wall behind is in shade
    door = c.box(0.12, 0.06, 0.40, 0.84)
    win = c.box(0.50, 0.36, 0.88, 0.84)
    if kind == "closed":
        g = c.box(0.0, 0.40, 1.0, 0.95)
        tul(c, g, 0.0, 1.0, 0.40, 0.95)
        c.trim(g & ((np.mod(c.x, 0.25) < 0.02) | (np.abs(c.y - 0.42) < 0.02) | (np.abs(c.y - 0.93) < 0.02)))
        c.wall(c.box(0, 0.06, 1, 0.40))
        c.shade(c.box(0, 0.06, 1, 0.40), 0.97)
    else:
        c.glass(door, gradient=(ROOM, ROOM_WARM))
        tul(c, win, 0.50, 0.88, 0.36, 0.84)
        c.frame(door, 0.014)
        c.frame(win, 0.014)
        if kind == "solid":
            c.wall(c.box(0, 0.06, 1, 0.38))
            c.trim(c.box(0, 0.36, 1, 0.40))               # the parapet's coping
        else:
            c.fixed(c.box(0, 0.34, 1, 0.37), IRON)
            c.fixed(c.box(0, 0.06, 1, 0.34) & (np.mod(c.x, 0.06) < 0.012), IRON)
            if kind == "laundry":
                c.fixed(c.box(0.05, 0.62, 0.95, 0.63), (0.25, 0.25, 0.25))
                rng = np.random.default_rng(5)
                x = 0.08
                while x < 0.9:
                    w = rng.uniform(0.06, 0.13)
                    h = rng.uniform(0.12, 0.25)
                    c.fixed(c.box(x, 0.62 - h, x + w, 0.62), LAUNDRY[int(rng.integers(len(LAUNDRY)))])
                    x += w + rng.uniform(0.01, 0.05)
            else:
                # Pots on the slab.
                for px in (0.15, 0.62, 0.80):
                    c.fixed(c.box(px, 0.06, px + 0.07, 0.14), (0.55, 0.30, 0.20))
                    c.fixed(c.box(px - 0.02, 0.14, px + 0.09, 0.24), (0.22, 0.32, 0.16))
    # The slab edge at the floor and the shade under the slab above.
    c.trim(c.box(0, 0.0, 1, 0.06))
    c.shade(c.box(0, 0.88, 1, 1.0), 0.6)
    return c


# ------------------------------------------------------------------ 19th-century masonry
def m19(variant: str):
    c = Cell()
    c.stripes(c.box(0, 0, 1, 1), 1 / 7.0, "y", 0.03, 0.93)          # rusticated courses, faint
    x0, x1, y0, y1 = 0.32, 0.68, 0.16, 0.80
    if variant == "arch":
        m = c.arch(x0, y0, x1, y1 - 0.12)
        top = y1 + 0.06
    else:
        m = c.box(x0, y0, x1, y1)
        top = y1
    if variant == "balcony":
        m = c.box(x0, 0.04, x1, y1)
        c.glass(m, gradient=(ROOM, ROOM_WARM))
        c.trim(m & (np.abs(c.x - 0.5) < 0.012))
    else:
        tul(c, m, x0, x1, y0, top)
        c.trim(m & ((np.abs(c.x - 0.5) < 0.010) | (np.abs(c.y - (y0 + top) * 0.5 - 0.1) < 0.008)))
    # Moulded surround and a hood (a small cornice) over it.
    c.frame(m, 0.035)
    c.trim(c.box(x0 - 0.07, top + 0.035, x1 + 0.07, top + 0.10))
    c.shade(c.box(x0 - 0.07, top + 0.035, x1 + 0.07, top + 0.05), 0.65)
    c.reveal(m, x0, top, 0.05, 0.55)
    if variant == "shutter":
        for sx0, sx1 in ((x0 - 0.20, x0 - 0.04), (x1 + 0.04, x1 + 0.20)):
            sh = c.box(sx0, y0, sx1, y1)
            c.fixed(sh, GREEN_SHUTTER)
            c.stripes(sh, 0.025, "y", 0.35, 0.7)
    if variant == "balcony":
        c.fixed(c.box(0.18, 0.02, 0.82, 0.06), MARBLE)                # the stone slab
        c.shade(c.box(0.18, -0.01, 0.82, 0.02), 0.6)
        c.fixed(c.box(0.18, 0.30, 0.82, 0.33), IRON)
        rail = c.box(0.18, 0.06, 0.82, 0.30)
        c.fixed(rail & ((np.mod(c.x, 0.05) < 0.010) | (np.abs(c.y - 0.18) < 0.008)), IRON)
    else:
        sill(c, x0, x1, y0, 0.035, 0.05)
    # The string course at the floor.
    c.trim(c.box(0, 0.0, 1, 0.025))
    return c


# ------------------------------------------------------------------ Ottoman wooden house
def clapboard(c: Cell, m):
    c.stripes(m, 1 / 15.0, "y", 0.16, 0.72)


def wood(variant: str):
    c = Cell()
    clapboard(c, c.box(0, 0, 1, 1))
    c.trim(c.box(0, 0, 0.035, 1))                       # the corner / post board
    c.trim(c.box(0, 0.0, 1, 0.03))                      # floor band
    if variant in ("win", "kafes"):
        # Two tall close-set windows a bay, as the houses have.
        for x0, x1 in ((0.12, 0.44), (0.56, 0.88)):
            y0, y1 = 0.22, 0.84
            m = c.box(x0, y0, x1, y1)
            if variant == "kafes" and x0 < 0.5:
                c.fixed(m, (0.36, 0.25, 0.16))
                c.shade(m & ((np.mod(c.x + c.y, 0.06) < 0.02) | (np.mod(c.x - c.y, 0.06) < 0.02)), 0.45)
            else:
                tul(c, m, x0, x1, y0, y1)
                c.trim(m & ((np.abs(c.x - (x0 + x1) / 2) < 0.012) | (np.abs(c.y - 0.62) < 0.012)))
            c.frame(m, 0.025)
            c.trim(c.box(x0 - 0.04, y1 + 0.025, x1 + 0.04, y1 + 0.06))
            c.trim(c.box(x0 - 0.03, y0 - 0.05, x1 + 0.03, y0 - 0.025))
            c.reveal(m, x0, y1, 0.03, 0.65)
    return c


def wood_door():
    c = Cell()
    clapboard(c, c.box(0, 0, 1, 1))
    c.fixed(c.box(0, 0, 1, 0.28), (0.62, 0.58, 0.52))             # rubble-stone base
    c.stripes(c.box(0, 0, 1, 0.28), 0.07, "y", 0.2, 0.75)
    d = c.box(0.34, 0.0, 0.66, 0.70)
    c.fixed(d, WOOD_DARK)
    c.shade(d & (np.abs(c.x - 0.5) < 0.01), 0.5)
    c.frame(d, 0.03)
    c.glass(c.box(0.36, 0.72, 0.64, 0.80), ROOM_WARM)
    c.trim(c.box(0.30, 0.80, 0.70, 0.84))
    return c


# ------------------------------------------------------------------ stone hans, bazaars
def banded(c: Cell, m):
    """Almasik: a course of cut stone, then three of brick (trim colour)."""
    period = 1 / 4.0
    v = np.mod(c.y, period) / period
    c.trim(m & (v > 0.55))
    c.shade(m & (np.mod(c.y, period / 4.0) < 0.006), 0.8)


def han(variant: str):
    c = Cell()
    banded(c, c.box(0, 0, 1, 1))
    if variant == "arch":
        x0, x1, y0, y1 = 0.36, 0.64, 0.24, 0.62
        m = c.arch(x0, y0, x1, y1)
        c.glass(m, gradient=(ROOM, ROOM_WARM))
        c.fixed(m & ((np.mod(c.x - x0, 0.07) < 0.012) | (np.mod(c.y - y0, 0.1) < 0.012)), IRON)
        ring = c.frame(m, 0.04, "wall")
        c.shade(ring, 1.05)
        c.reveal(m, x0, y1 + 0.14, 0.06, 0.5)
    return c


def han_portal():
    c = Cell()
    banded(c, c.box(0, 0, 1, 1))
    m = c.arch(0.22, 0.0, 0.78, 0.55)
    c.fixed(m, (0.10, 0.09, 0.08))
    c.fixed(c.box(0.25, 0.0, 0.75, 0.52) & (np.abs(c.x - 0.5) > 0.01), (0.28, 0.18, 0.11))
    ring = c.frame(m, 0.05, "wall")
    c.shade(ring, 1.08)
    return c


# ------------------------------------------------------------------ modern, public, industrial
def ribbon(variant: str):
    c = Cell()
    x0, x1, y0, y1 = 0.0, 1.0, 0.34, 0.86
    m = c.box(x0, y0, x1, y1)
    if variant == "dark":
        c.glass(m, gradient=((0.10, 0.12, 0.14), (0.30, 0.36, 0.42)))
    else:
        c.glass(m, gradient=((0.25, 0.30, 0.36), (0.50, 0.56, 0.62)))
    c.trim(m & ((np.mod(c.x, 0.5) < 0.02) | (c.y < y0 + 0.015) | (c.y > y1 - 0.015)))
    c.shade(c.box(0, 0.30, 1, 0.34), 0.75)
    return c


def curtain():
    c = Cell()
    c.trim(c.box(0, 0, 1, 1))
    v = c.box(0.03, 0.18, 0.97, 0.98)
    c.glass(v, gradient=((0.22, 0.28, 0.34), (0.48, 0.55, 0.62)))
    c.shade(c.box(0.03, 0.0, 0.97, 0.18), 0.75)
    return c


def pub_tall():
    c = Cell()
    plaster(c, c.box(0, 0, 1, 1))
    x0, x1, y0, y1 = 0.30, 0.70, 0.14, 0.86
    m = c.box(x0, y0, x1, y1)
    c.glass(m, gradient=(ROOM, (0.20, 0.24, 0.28)))
    c.trim(m & ((np.abs(c.x - 0.5) < 0.012) | (np.abs(c.y - 0.62) < 0.012)))
    c.frame(m, 0.03)
    c.reveal(m, x0, y1, 0.04)
    sill(c, x0, x1, y0)
    return c


def industrial():
    c = Cell()
    c.stripes(c.box(0, 0, 1, 1), 0.08, "x", 0.25, 0.8)                # profiled sheet
    c.shade(c.box(0, 0.0, 1, 0.04), 0.7)
    return c


# ------------------------------------------------------------------ ground floor
def shop(kind: str):
    c = Cell()
    m = c.box(0.04, 0.02, 0.96, 0.68)
    c.glass(m, ROOM_WARM)
    rng = np.random.default_rng(7 if kind != "awning" else 11)
    # Shelves of goods behind the glass: small, many, muted.
    for sy in (0.10, 0.30, 0.50):
        c.glass(c.box(0.06, sy - 0.012, 0.94, sy) & m, (0.18, 0.16, 0.14))
        x = 0.07
        while x < 0.92:
            wd = rng.uniform(0.025, 0.06)
            ht = rng.uniform(0.05, 0.15)
            g = GOODS[int(rng.integers(len(GOODS)))]
            c.glass(c.box(x, sy, x + wd, sy + ht) & m, tuple(0.65 * v for v in g))
            x += wd + rng.uniform(0.005, 0.03)
    c.fixed(m & ((np.abs(c.x - 0.38) < 0.008) | (c.y > 0.665)), (0.55, 0.56, 0.57))   # aluminium
    c.trim(c.box(0.0, 0.70, 1.0, 0.92))                           # sign band
    c.shade(c.box(0.0, 0.68, 1.0, 0.70), 0.5)
    c.fixed(c.box(0.0, 0.92, 1.0, 0.96), (0.42, 0.42, 0.42))      # the shutter box
    if kind == "shutter":
        g = c.box(0.04, 0.02, 0.96, 0.68)
        c.fixed(g, KEPENK)
        c.stripes(g, 0.022, "y", 0.35, 0.75)
        c.fixed(c.box(0.04, 0.02, 0.96, 0.04), IRON)
    elif kind == "awning":
        aw = c.box(0.02, 0.50, 0.98, 0.74)
        c.trim(aw)
        c.stripes(aw, 0.12, "x", 0.5, 0.75)
        c.trim(c.box(0.02, 0.46, 0.98, 0.50), 0.85)
        c.shade(c.box(0.04, 0.24, 0.96, 0.46), 0.55)
    return c


def entrance():
    """An apartment block's entrance: an aluminium-and-glass door in a marble surround, a canopy."""
    c = Cell()
    plaster(c, c.box(0, 0, 1, 1))
    c.fixed(c.box(0.22, 0.0, 0.78, 0.74), MARBLE)
    d = c.box(0.30, 0.0, 0.70, 0.66)
    c.glass(d, ROOM_WARM)
    c.fixed(d & ((np.abs(c.x - 0.5) < 0.012) | (np.abs(c.y - 0.33) < 0.01) | (c.x < 0.315) | (c.x > 0.685)), (0.45, 0.45, 0.46))
    c.fixed(c.box(0.16, 0.76, 0.84, 0.82), (0.82, 0.82, 0.80))
    c.shade(c.box(0.16, 0.70, 0.84, 0.76), 0.55)
    return c


def plinth():
    c = Cell()
    c.fixed(c.box(0, 0, 1, 1), (0.60, 0.58, 0.54))
    c.stripes(c.box(0, 0, 1, 1), 0.12, "y", 0.06, 0.8)
    m = c.box(0.34, 0.55, 0.66, 0.80)
    c.glass(m, ROOM)
    c.fixed(m & (np.mod(c.x - 0.34, 0.06) < 0.012), IRON)
    c.shade(c.box(0, 0.94, 1, 1), 0.8)
    return c


def blank():
    c = Cell()
    plaster(c, c.box(0, 0, 1, 1))
    return c


def cornice():
    c = Cell()
    c.trim(c.box(0, 0, 1, 1))
    c.shade(c.box(0, 0.0, 1, 0.35), 0.85)
    c.shade(c.box(0, 0.35, 1, 0.45), 0.6)
    br = c.box(0.40, 0.45, 0.60, 0.80)
    c.shade(c.box(0, 0.45, 1, 0.80) & ~br, 0.62)
    c.shade(c.box(0, 0.80, 1, 0.86), 0.7)
    c.shade(c.box(0, 0.86, 1, 1.0), 1.1)
    return c


def party(kind: str):
    c = Cell()
    if kind == "brick":
        c.fixed(c.box(0, 0, 1, 1), BRICK)
        c.stripes(c.box(0, 0, 1, 1), 1 / 10.0, "y", 0.12, 0.8)
        c.stripes(c.box(0, 0, 1, 1), 0.25, "x", 0.04, 0.85)
        c.fixed(c.box(0, 0.0, 1, 0.10), CONCRETE)                # the slab edge
        c.fixed(c.box(0, 0.0, 0.07, 1), CONCRETE)                # a column
        c.shade(c.box(0.3, 0.4, 0.7, 0.6), 0.93)
    else:
        plaster(c, c.box(0, 0, 1, 1), 0.93)
        c.shade(c.box(0, 0.0, 1, 0.08), 0.92)
        c.shade(c.box(0.55, 0.1, 0.62, 1.0), 0.9)                 # a rain streak
    return c


def eave():
    c = Cell()
    plaster(c, c.box(0, 0, 1, 1))
    c.trim(c.box(0, 0.55, 1, 0.75))
    c.shade(c.box(0, 0.75, 1, 1.0), 0.7)
    return c


LAYERS = [
    *[(f"apt_{v}", lambda v=v: apt(v)) for v in ("dark", "tul", "shutter", "ac")],         # 0-3
    *[(f"bal_{v}", lambda v=v: balcony(v)) for v in ("solid", "rail", "laundry", "closed")],  # 4-7
    *[(f"m19_{v}", lambda v=v: m19(v)) for v in ("tall", "shutter", "balcony", "arch")],    # 8-11
    *[(f"wood_{v}", lambda v=v: wood(v)) for v in ("win", "kafes", "blank")],               # 12-14
    *[(f"han_{v}", lambda v=v: han(v)) for v in ("arch", "blank")],                         # 15-16
    *[(f"ribbon_{v}", lambda v=v: ribbon(v)) for v in ("sky",)],                            # 17
    ("curtain", curtain), ("pub_tall", pub_tall),                                           # 18-19
    *[(f"shop_{v}", lambda v=v: shop(v)) for v in ("open", "shutter", "awning")],           # 20-22
    ("entrance", entrance), ("plinth", plinth), ("wood_door", wood_door),                   # 23-25
    ("han_portal", han_portal), ("blank", blank), ("cornice", cornice),                     # 26-28
    ("party_brick", lambda: party("brick")), ("party_plaster", lambda: party("plaster")),   # 29-30
    ("eave", eave), ("industrial", industrial), ("apt_bars", lambda: apt("bars")),          # 31-33
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    masks, cols, names = [], [], []
    for name, fn in LAYERS:
        m, col = fn().out()
        masks.append(m)
        cols.append(col)
        names.append(name)
        print(f"  {len(names) - 1:2d} {name}")
    Image.fromarray(np.concatenate(masks, 0), "RGBA").save(OUT / "mask.png", optimize=True)
    Image.fromarray(np.concatenate(cols, 0), "RGB").save(OUT / "colour.png", optimize=True)
    (OUT / "cells.json").write_text(json.dumps({
        "px": PX, "layers": names,
        "made_by": "drone/sim/tools/bake_istanbul_facade_cells.py (drawn, not photographed; no licence restrictions)",
    }, indent=1))
    print(f"wrote {len(names)} layers to {OUT}")


if __name__ == "__main__":
    main()
