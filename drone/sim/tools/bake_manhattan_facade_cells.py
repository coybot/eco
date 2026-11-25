#!/usr/bin/env python3
"""Bake the window cells the Manhattan building shader tiles its walls with.

    python3 drone/sim/tools/bake_manhattan_facade_cells.py

Writes drone/sim/godot/assets/facade_cells_nyc/{mask,colour}.png and
cells.json (all small, tracked). Drawn here, not photographed: no licence
restrictions.

Each layer is one bay by one storey of wall, drawn 4x supersampled and filtered
down, so the shader can sample it mipmapped. That is the whole reason for
baking rather than drawing windows with step() in the shader: a sash bar, a
lintel or a fire-escape rail is far under a pixel from 300 m, and a step()
test there flickers on and off as the camera moves (the CC0 photo facades this
replaces had the same problem with their own bright specks, which read as lit
windows in daylight). A mipmapped cell fades to its own average instead.

Channels, per pixel of a layer:
  mask.r  wall: painted the building's wall colour (brick, brownstone, stone)
  mask.g  trim: the building's trim colour (lintels, cornices, mullions,
          spandrels), or a shop's own colour (awnings, sign bands)
  mask.b  glass: lit at night, glossy by day
  mask.a  shade: reveals, joints, slats (a multiplier)
  colour  everything else's own colour: rooms, blinds, sky in the glass, AC
          units, iron fire escapes, roll-down gates (where r and g are 0)

Cell space: x 0..1 across the bay, y 0..1 up the storey (image row 0 is the
top). Window layers keep a wall margin round the edge, because the shader
samples with clamp-to-edge and scales nothing.

The layer ORDER is the shader's table (scripts/manhattan_buildings.gd, L_*):
append new layers at the end.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "godot" / "assets" / "facade_cells_nyc"
PX = 256
SS = 4
N = PX * SS

ROOM = (0.030, 0.034, 0.040)
ROOM_WARM = (0.060, 0.050, 0.045)
BLIND = (0.48, 0.45, 0.39)
BLIND_COOL = (0.56, 0.57, 0.56)
CURTAIN = (0.40, 0.34, 0.28)
SKY_LO, SKY_HI = (0.16, 0.21, 0.27), (0.42, 0.49, 0.57)
AC = (0.74, 0.74, 0.71)
IRON = (0.055, 0.055, 0.060)
ROLLER = (0.52, 0.54, 0.55)
GOODS = [(0.55, 0.35, 0.20), (0.25, 0.40, 0.55), (0.70, 0.62, 0.40), (0.45, 0.22, 0.20), (0.30, 0.45, 0.30),
         (0.80, 0.78, 0.72)]


class Cell:
    def __init__(self) -> None:
        yy, xx = np.mgrid[0:N, 0:N]
        self.x = (xx + 0.5) / N
        self.y = 1.0 - (yy + 0.5) / N
        self.r = np.ones((N, N), np.float32)
        self.g = np.zeros((N, N), np.float32)
        self.b = np.zeros((N, N), np.float32)
        self.a = np.ones((N, N), np.float32)
        self.col = np.full((N, N, 3), 0.5, np.float32)

    def box(self, x0, y0, x1, y1):
        return (self.x >= x0) & (self.x < x1) & (self.y >= y0) & (self.y < y1)

    def arch(self, x0, y0, x1, y1):
        cx, r = (x0 + x1) / 2, (x1 - x0) / 2
        return self.box(x0, y0, x1, y1) | (((self.x - cx) ** 2 + (self.y - y1) ** 2 <= r * r) & (self.y >= y1))

    def wall(self, m, shade=1.0):
        self.r[m], self.g[m], self.b[m] = 1, 0, 0
        self.a[m] *= shade

    def trim(self, m, shade=1.0):
        self.r[m], self.g[m], self.b[m] = 0, 1, 0
        self.a[m] *= shade

    def fixed(self, m, rgb, shade=1.0):
        self.r[m], self.g[m], self.b[m] = 0, 0, 0
        self.col[m] = rgb
        self.a[m] *= shade

    def glass(self, m, rgb=ROOM, gradient=None):
        self.r[m], self.g[m], self.b[m] = 0, 0, 1
        if gradient is None:
            self.col[m] = rgb
        else:
            lo, hi = gradient
            ys = self.y[m]
            t = np.clip((self.y - ys.min()) / max(1e-6, ys.max() - ys.min()), 0, 1)[m][:, None]
            self.col[m] = np.array(lo) * (1 - t) + np.array(hi) * t

    def shade(self, m, k):
        self.a[m] *= k

    def stripes(self, m, period, axis, duty=0.35, k=0.72, phase=0.0):
        v = self.x if axis == "x" else self.y
        self.a[m & ((np.mod(v + phase, period) / period) < duty)] *= k

    def frame(self, opening, t=0.02, kind="trim"):
        k = max(1, int(t * N))
        grown = opening.copy()
        grown[k:, :] |= opening[:-k, :]
        grown[:-k, :] |= opening[k:, :]
        grown[:, k:] |= opening[:, :-k]
        grown[:, :-k] |= opening[:, k:]
        ring = grown & ~opening
        (self.trim if kind == "trim" else self.wall)(ring)
        return ring

    def reveal(self, opening, x0, y1, depth=0.05, k=0.55):
        """The wall's depth shades the head and one jamb of an opening (sun from the upper left)."""
        self.shade(opening & ((self.y > y1 - depth) | (self.x < x0 + depth * 0.8)), k)

    def brick(self, m, course=1 / 14.0, k=0.9):
        """Mortar joints: a faint course line, so brick reads as brick up close and averages away."""
        self.stripes(m & (self.r > 0.5), course, "y", 0.12, k)

    def out(self):
        def down(a):
            img = Image.fromarray(np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8))
            return np.asarray(img.resize((PX, PX), Image.LANCZOS))
        mask = np.stack([down(self.r), down(self.g), down(self.b), down(self.a)], -1)
        colour = np.stack([down(self.col[..., i]) for i in range(3)], -1)
        return mask, colour


# ------------------------------------------------------------------ what fills a window
def pane(c: Cell, m, variant: str, x0, x1, y0, y1, sash=True):
    if variant == "dark":
        c.glass(m, gradient=(ROOM, ROOM_WARM))
    elif variant == "blind":
        c.glass(m, gradient=(ROOM, ROOM_WARM))
        drop = y1 - (y1 - y0) * 0.45
        c.glass(m & (c.y > drop), BLIND)
        c.shade(m & (c.y > drop) & (c.y < drop + 0.02), 0.6)
    elif variant == "curtain":
        c.glass(m, ROOM)
        mid, gap = (x0 + x1) / 2, (x1 - x0) * 0.14
        cur = m & ((c.x < mid - gap) | (c.x > mid + gap))
        c.glass(cur, CURTAIN)
        c.stripes(cur, 0.03, "x", 0.3, 0.85)
    elif variant == "sky":
        c.glass(m, gradient=(SKY_LO, SKY_HI))
    elif variant == "ac":
        c.glass(m, gradient=(ROOM, ROOM_WARM))
    if sash:
        # Double-hung: the meeting rail across the middle and a thin frame.
        mid = (y0 + y1) / 2
        c.trim(m & (np.abs(c.y - mid) < 0.012))
    if variant == "ac":
        # A window AC unit in the lower sash: Manhattan's most common rooftop-to-
        # street detail. Sits proud, so it shades the sill below.
        ax0, ax1 = x0 + 0.02, x1 - 0.02
        u = c.box(ax0, y0, ax1, y0 + (y1 - y0) * 0.30)
        c.fixed(u, AC)
        c.stripes(u, 0.018, "y", 0.4, 0.8)
        c.shade(c.box(ax0, y0 - 0.06, ax1, y0), 0.6)


def lintel(c: Cell, x0, x1, y1, h=0.07, over=0.04):
    c.trim(c.box(x0 - over, y1, x1 + over, y1 + h))
    c.shade(c.box(x0 - over, y1 + h - 0.012, x1 + over, y1 + h), 1.15)


def sill(c: Cell, x0, x1, y0, h=0.035, over=0.03):
    c.trim(c.box(x0 - over, y0 - h, x1 + over, y0))
    c.shade(c.box(x0 - over, y0 - h - 0.05, x1 + over, y0 - h), 0.78)


# ------------------------------------------------------------------ upper floors
def sash(variant):
    """Brownstone / walk-up punched window: stone lintel and sill, 1-over-1 sash."""
    c = Cell()
    c.brick(c.box(0, 0, 1, 1))
    x0, x1, y0, y1 = 0.27, 0.73, 0.20, 0.80
    m = c.box(x0, y0, x1, y1)
    c.frame(m, 0.018)
    pane(c, m, variant, x0, x1, y0, y1)
    c.reveal(m, x0, y1, 0.05)
    lintel(c, x0, x1, y1)
    sill(c, x0, x1, y0)
    return c


def pair(variant):
    """Pre-war: two windows a bay, a shared stone sill course."""
    c = Cell()
    c.brick(c.box(0, 0, 1, 1))
    for x0, x1 in ((0.13, 0.44), (0.56, 0.87)):
        y0, y1 = 0.22, 0.80
        m = c.box(x0, y0, x1, y1)
        c.frame(m, 0.014)
        pane(c, m, "ac" if variant == "ac" and x0 > 0.5 else ("dark" if variant == "ac" else variant), x0, x1, y0, y1)
        c.reveal(m, x0, y1, 0.04)
    c.trim(c.box(0.0, 0.17, 1.0, 0.205))                  # the sill course runs right across
    c.shade(c.box(0.0, 0.13, 1.0, 0.17), 0.8)
    return c


def fire_escape(mirror: bool, variant="dark"):
    """A walk-up's iron fire escape in front of a window: the slatted platform,
    its railing and the drop ladder to the floor below."""
    c = sash(variant)
    sx = (lambda x: 1.0 - x) if mirror else (lambda x: x)
    # Platform at the floor line, slats.
    plat = c.box(0.0, 0.0, 1.0, 0.055)
    c.fixed(plat, IRON)
    c.stripes(plat, 0.03, "x", 0.4, 0.6)
    c.shade(c.box(0.0, 0.055, 1.0, 0.10), 0.7)           # its shadow on the wall
    # Railing: top rail and posts, balusters.
    c.fixed(c.box(0.0, 0.33, 1.0, 0.35), IRON)
    c.fixed(c.box(0.0, 0.055, 1.0, 0.33) & (np.mod(c.x, 0.08) < 0.010), IRON)
    c.fixed(c.box(0.0, 0.17, 1.0, 0.18), IRON)
    # Ladder: a diagonal pair of stringers rising across the bay to the next platform.
    xx = sx(c.x)
    for off in (0.0, 0.07):
        line = np.abs((c.y - 0.055) - (xx - 0.18 - off) * 1.25) < 0.016
        c.fixed(line & (c.y > 0.055) & (xx > 0.18) & (xx < 0.98), IRON)
    rungs = (np.abs((c.y - 0.055) - (xx - 0.215) * 1.25) < 0.06) & (np.mod(c.y, 0.06) < 0.012)
    c.fixed(rungs & (xx > 0.2) & (xx < 0.96) & (c.y > 0.06), IRON)
    return c


def ribbon(variant):
    """Post-war white-brick apartment: a wide window, aluminium frames."""
    c = Cell()
    c.brick(c.box(0, 0, 1, 1), 1 / 13.0, 0.93)
    x0, x1, y0, y1 = 0.10, 0.90, 0.26, 0.78
    m = c.box(x0, y0, x1, y1)
    pane(c, m, variant, x0, x1, y0, y1, sash=False)
    c.trim(m & ((np.abs(c.x - 0.5) < 0.012) | (np.abs(c.x - 0.3) < 0.008) | (np.abs(c.x - 0.7) < 0.008)
                | (np.abs(c.y - 0.42) < 0.008)))
    c.frame(m, 0.012)
    c.reveal(m, x0, y1, 0.035, 0.6)
    c.shade(c.box(x0, y0 - 0.03, x1, y0), 0.85)
    return c


def curtain(variant, spandrel=0.20):
    """A curtain-wall module: vision glass, an opaque spandrel at the slab, mullions."""
    c = Cell()
    c.trim(c.box(0, 0, 1, 1))                            # mullion/spandrel colour everywhere first
    vis = c.box(0.035, spandrel, 0.965, 0.985)
    if variant == "blind":
        c.glass(vis, gradient=(SKY_LO, SKY_HI))
        c.glass(vis & (c.y > 0.985 - (0.985 - spandrel) * 0.35), BLIND_COOL)
    elif variant == "dark":
        c.glass(vis, gradient=(ROOM, (0.09, 0.11, 0.13)))
    else:
        c.glass(vis, gradient=(SKY_LO, SKY_HI))
    # The spandrel is back-painted glass: same plane, a shade darker, faint joint.
    span = c.box(0.035, 0.0, 0.965, spandrel)
    c.shade(span, 0.75)
    c.shade(c.box(0.035, spandrel - 0.012, 0.965, spandrel), 0.55)
    c.shade(c.box(0.0, 0.0, 0.035, 1.0), 1.12)           # the mullion cap catches light
    return c


def loft(variant):
    """Cast-iron / loft: tall windows between columns."""
    c = Cell()
    x0, x1, y0, y1 = 0.16, 0.84, 0.12, 0.88
    m = c.box(x0, y0, x1, y1)
    c.trim(c.box(0, 0, 1, 1) & ~m)                       # painted iron front
    c.shade(c.box(0, 0, 0.06, 1) | c.box(0.94, 0, 1, 1), 1.12)   # column faces
    c.shade(c.box(0.06, 0, 0.10, 1), 0.7)
    c.shade(c.box(0, 0.90, 1, 0.94), 0.75)
    pane(c, m, variant, x0, x1, y0, y1)
    c.trim(m & (np.abs(c.x - 0.5) < 0.012))
    c.reveal(m, x0, y1, 0.04)
    return c


def precast(variant):
    """Modern residential: a big square window in a precast or glass-banded wall."""
    c = Cell()
    x0, x1, y0, y1 = 0.12, 0.88, 0.16, 0.90
    m = c.box(x0, y0, x1, y1)
    pane(c, m, variant, x0, x1, y0, y1, sash=False)
    c.trim(m & ((np.abs(c.x - 0.62) < 0.01) | (c.y < y0 + 0.012)))
    c.frame(m, 0.012)
    c.shade(c.box(0, 0.0, 1, 0.025), 0.8)                # the slab joint
    return c


def civic(variant):
    """Limestone civic / church: a tall round-headed window."""
    c = Cell()
    c.stripes(c.box(0, 0, 1, 1), 1 / 6.0, "y", 0.03, 0.9)   # ashlar courses
    x0, x1, y0, y1 = 0.32, 0.68, 0.10, 0.70
    m = c.arch(x0, y0, x1, y1)
    c.frame(m, 0.03)
    pane(c, m, variant, x0, x1, y0, y1 + 0.18, sash=False)
    c.trim(m & ((np.abs(c.x - 0.5) < 0.01) | (np.mod(c.y, 0.12) < 0.01)))
    c.reveal(m, x0, y1 + 0.18, 0.05)
    sill(c, x0, x1, y0)
    return c


# ------------------------------------------------------------------ ground floor
def shop(kind: str):
    """A storefront: plate glass, a sign band, and either an awning, a gate or neither."""
    c = Cell()
    m = c.box(0.04, 0.02, 0.96, 0.70)
    c.glass(m, ROOM_WARM)
    rng = np.random.default_rng(7 if kind != "awning" else 11)
    for i in range(6):
        gx, gy = rng.uniform(0.06, 0.8), rng.uniform(0.03, 0.35)
        blk = c.box(gx, gy, gx + rng.uniform(0.08, 0.16), gy + rng.uniform(0.08, 0.2)) & m
        c.glass(blk, GOODS[i % len(GOODS)])
    door = c.box(0.42, 0.02, 0.58, 0.62)
    c.glass(door, ROOM)
    c.fixed(c.box(0.04, 0.0, 0.96, 0.03), (0.35, 0.34, 0.33))     # the bulkhead kerb
    c.fixed(m & ((np.abs(c.x - 0.42) < 0.008) | (np.abs(c.x - 0.58) < 0.008) | (c.y > 0.685)), IRON)
    c.trim(c.box(0.0, 0.73, 1.0, 0.95))                           # sign band
    c.shade(c.box(0.0, 0.70, 1.0, 0.73), 0.5)
    c.shade(c.box(0.0, 0.93, 1.0, 0.95), 0.75)
    if kind == "gate":
        g = c.box(0.04, 0.02, 0.96, 0.70)
        c.fixed(g, ROLLER)
        c.stripes(g, 0.02, "y", 0.35, 0.78)
        c.fixed(c.box(0.04, 0.02, 0.96, 0.045), IRON)
        # Tags on the gate: a few low-frequency colour blocks.
        for i in range(3):
            gx, gy = rng.uniform(0.1, 0.7), rng.uniform(0.1, 0.45)
            c.fixed(c.box(gx, gy, gx + rng.uniform(0.1, 0.2), gy + rng.uniform(0.05, 0.12)),
                    GOODS[(i + 2) % len(GOODS)], 0.85)
    elif kind == "awning":
        # A canvas awning from the sign band, seen square on: its face in the
        # shop's colour, stripes, the valance, and a deep shade under it.
        aw = c.box(0.02, 0.56, 0.98, 0.80)
        c.trim(aw)
        c.stripes(aw, 0.12, "x", 0.5, 0.72)
        c.trim(c.box(0.02, 0.52, 0.98, 0.56), 0.85)               # valance
        c.shade(c.box(0.04, 0.30, 0.96, 0.52), 0.55)
    return c


def stoop_door():
    """A brownstone's entrance: a double door with a transom in a stone surround,
    raised up a stoop (the steps shade the lower wall)."""
    c = Cell()
    c.brick(c.box(0, 0, 1, 1))
    d = c.box(0.33, 0.28, 0.67, 0.86)
    c.trim(c.box(0.27, 0.22, 0.73, 0.94))
    c.fixed(d, (0.20, 0.12, 0.07))
    c.shade(d & (np.abs(c.x - 0.5) < 0.008), 0.5)
    c.glass(c.box(0.35, 0.78, 0.65, 0.85), ROOM_WARM)
    for py in (0.33, 0.55):
        c.shade(c.box(0.36, py, 0.47, py + 0.17) | c.box(0.53, py, 0.64, py + 0.17), 0.75)
    st = c.box(0.22, 0.0, 0.78, 0.26)
    c.trim(st, 0.9)
    c.stripes(st, 0.065, "y", 0.25, 0.7)                          # the treads
    c.fixed(c.box(0.20, 0.0, 0.23, 0.36) | c.box(0.77, 0.0, 0.80, 0.36), IRON)   # railings
    return c


def garden():
    """The low garden-level window under a stoop, behind an iron grille."""
    c = Cell()
    c.brick(c.box(0, 0, 1, 1))
    x0, x1, y0, y1 = 0.28, 0.72, 0.38, 0.80
    m = c.box(x0, y0, x1, y1)
    c.glass(m, ROOM)
    c.fixed(m & (np.mod(c.x - x0, 0.055) < 0.012), IRON)
    c.fixed(m & (np.mod(c.y - y0, 0.14) < 0.012), IRON)
    lintel(c, x0, x1, y1, 0.05)
    c.fixed(c.box(0.0, 0.0, 1.0, 0.30) & (np.mod(c.x, 0.05) < 0.01), IRON)   # the areaway fence
    c.fixed(c.box(0.0, 0.28, 1.0, 0.30), IRON)
    return c


def lobby():
    """A tower's lobby: full-height glass, a canopy, a revolving door."""
    c = Cell()
    c.trim(c.box(0, 0, 1, 1))
    m = c.box(0.03, 0.0, 0.97, 0.84)
    c.glass(m, gradient=((0.10, 0.09, 0.08), (0.20, 0.20, 0.20)))
    c.trim(m & ((np.mod(c.x, 0.235) < 0.012) | (np.abs(c.y - 0.62) < 0.01)))
    c.shade(c.box(0.0, 0.84, 1.0, 0.88), 0.5)
    c.trim(c.box(0.0, 0.88, 1.0, 1.0), 1.1)
    return c


def entrance():
    """A pre-war apartment entrance: a door in a stone surround under a canopy."""
    c = Cell()
    d = c.box(0.30, 0.0, 0.70, 0.66)
    c.trim(c.box(0.24, 0.0, 0.76, 0.74))
    c.glass(d, ROOM_WARM)
    c.fixed(d & ((np.abs(c.x - 0.5) < 0.012) | (np.mod(c.y, 0.22) < 0.01)), IRON)
    c.fixed(c.box(0.18, 0.78, 0.82, 0.86), (0.12, 0.20, 0.14))    # the canopy edge, green
    c.shade(c.box(0.18, 0.74, 0.82, 0.78), 0.5)
    return c


def blank():
    c = Cell()
    c.brick(c.box(0, 0, 1, 1))
    return c


def cornice():
    """The cornice band at a walk-up's or pre-war block's roof line: a crown
    moulding over a row of brackets over a frieze. The cell is one bracket
    spacing wide and the band's height tall."""
    c = Cell()
    c.trim(c.box(0, 0, 1, 1))
    c.shade(c.box(0, 0.0, 1, 0.30), 0.88)                         # frieze
    c.shade(c.box(0, 0.30, 1, 0.36), 0.62)                        # shadow under the brackets
    br = c.box(0.36, 0.36, 0.64, 0.78)
    c.shade(c.box(0, 0.36, 1, 0.78) & ~br, 0.55)                  # deep between them
    c.shade(br & (c.x > 0.56), 0.8)
    c.shade(c.box(0, 0.78, 1, 0.84), 0.7)
    c.shade(c.box(0, 0.84, 1, 1.0), 1.12)                         # the lit crown
    c.stripes(c.box(0, 0.10, 1, 0.20), 0.08, "x", 0.45, 0.8)      # dentils
    return c


def party():
    """A lot-line wall: bare common brick, a few patched courses."""
    c = Cell()
    c.brick(c.box(0, 0, 1, 1), 1 / 14.0, 0.88)
    c.shade(c.box(0.1, 0.4, 0.6, 0.55), 0.94)
    return c


LAYERS = [
    *[(f"sash_{v}", lambda v=v: sash(v)) for v in ("dark", "blind", "curtain", "ac", "sky")],   # 0-4
    *[(f"pair_{v}", lambda v=v: pair(v)) for v in ("dark", "blind", "ac", "sky")],              # 5-8
    ("fire_a", lambda: fire_escape(False)), ("fire_b", lambda: fire_escape(True, "blind")),     # 9-10
    *[(f"ribbon_{v}", lambda v=v: ribbon(v)) for v in ("dark", "blind", "curtain")],            # 11-13
    *[(f"curtain_{v}", lambda v=v: curtain(v)) for v in ("sky", "dark", "blind")],              # 14-16
    ("curtain_deep", lambda: curtain("sky", 0.38)),                                             # 17
    *[(f"loft_{v}", lambda v=v: loft(v)) for v in ("dark", "sky")],                             # 18-19
    *[(f"precast_{v}", lambda v=v: precast(v)) for v in ("dark", "blind", "sky")],              # 20-22
    *[(f"civic_{v}", lambda v=v: civic(v)) for v in ("dark", "sky")],                           # 23-24
    ("shop_open", lambda: shop("open")), ("shop_gate", lambda: shop("gate")),                   # 25-26
    ("shop_awning", lambda: shop("awning")),                                                    # 27
    ("stoop_door", stoop_door), ("garden", garden), ("lobby", lobby), ("entrance", entrance),   # 28-31
    ("blank", blank), ("cornice", cornice), ("party", party),                                   # 32-34
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
        "made_by": "drone/sim/tools/bake_manhattan_facade_cells.py (drawn, not photographed; no licence restrictions)",
    }, indent=1))
    print(f"wrote {len(names)} layers to {OUT}")


if __name__ == "__main__":
    main()
