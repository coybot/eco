"""Rover gauntlet demo — runs a reactive VFH planner through a tough 2D course.

No torch/ONNX needed. Uses a Vector Field Histogram (VFH) reactive planner:
  - 360° lidar scan (72 rays) → polar histogram of obstacle density
  - Goal direction provides attraction; free valleys provide steering candidates
  - Pick the valley closest to goal direction, set (v, yaw_rate) accordingly

This is the analytic baseline. The trained GRU policy will handle the same
course with memory (doesn't get stuck in local minima, knows which way it came).

    ~/.presidio-venv/bin/python -m drone.training.demo_rover_gauntlet
    ~/.presidio-venv/bin/python -m drone.training.demo_rover_gauntlet --course maze
    ~/.presidio-venv/bin/python -m drone.training.demo_rover_gauntlet --all --svg out.svg
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

# ---- make importable as both module and script ----
_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))
from rover_contract import LIDAR_RAYS, LIDAR_MAX, LIDAR_ANGLES   # noqa: E402

DT = 0.05          # 20 Hz sim (finer than training for smoother ASCII trace)
MAX_TICKS = 3000
HALT_R = 0.30
REACH_R = 1.0
MAX_V = 1.5        # m/s
MAX_W = 2.0        # rad/s


# --------------------------------------------------------------------------- geometry
@dataclass
class Box2D:
    cx: float; cy: float; hx: float; hy: float

    def dist_2d(self, x: float, y: float) -> float:
        return math.hypot(max(abs(x - self.cx) - self.hx, 0.0),
                          max(abs(y - self.cy) - self.hy, 0.0))

    def contains(self, x: float, y: float) -> bool:
        return abs(x - self.cx) <= self.hx and abs(y - self.cy) <= self.hy


def _ray_box_2d(ox, oy, dx, dy, b: Box2D) -> float:
    tmin, tmax = -math.inf, math.inf
    for o, d, c, h in ((ox, dx, b.cx, b.hx), (oy, dy, b.cy, b.hy)):
        lo, hi = c - h, c + h
        if abs(d) < 1e-9:
            if o < lo or o > hi:
                return math.inf
        else:
            t1, t2 = (lo - o) / d, (hi - o) / d
            if t1 > t2: t1, t2 = t2, t1
            tmin = max(tmin, t1); tmax = min(tmax, t2)
            if tmin > tmax:
                return math.inf
    return math.inf if tmax < 0 else (tmin if tmin >= 0 else 0.0)


def lidar_scan(x, y, yaw, boxes):
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    out = np.full(LIDAR_RAYS, LIDAR_MAX, dtype=np.float32)
    for i, a in enumerate(LIDAR_ANGLES):
        bfwd, bleft = math.cos(a), math.sin(a)
        dx = cos_y * bfwd - sin_y * bleft
        dy = sin_y * bfwd + cos_y * bleft
        best = LIDAR_MAX
        for b in boxes:
            t = _ray_box_2d(x, y, dx, dy, b)
            if t < best:
                best = t
        out[i] = best
    return out


def min_dist(x, y, boxes):
    if not boxes:
        return math.inf
    return min(b.dist_2d(x, y) for b in boxes)


# --------------------------------------------------------------------------- courses
COURSES = {
    "gauntlet": {
        # 5-obstacle gauntlet: wall → cluster → offset wall → tight squeeze → wall
        # Rover must: thread gap, weave columns, commit to off-centre gap, squeeze through
        "start": (0.0, 0.0),
        "goal":  (22.0, 0.0),
        "obstacles": [
            # Wall 1 at x=4 — centred gap 2.4 m wide
            Box2D(4.0, -3.8, 0.4, 2.6),   # left
            Box2D(4.0,  3.8, 0.4, 2.6),   # right
            # Column cluster x=7–10
            Box2D(7.5,  1.4, 0.45, 0.45),
            Box2D(8.5, -1.4, 0.45, 0.45),
            Box2D(9.5,  0.6, 0.45, 0.45),
            Box2D(10.5,-1.0, 0.45, 0.45),
            # Wall 2 at x=14 — gap is offset RIGHT (y=+1.5..+4.5)
            Box2D(14.0, -3.5, 0.4, 3.5),  # left (full, no gap on left)
            Box2D(14.0,  5.5, 0.4, 1.5),  # right stub → gap at y=[+3.0,+4.0]
            # Tight squeeze x=17–18 (corridor 1.6 m wide)
            Box2D(17.5, -3.5, 0.5, 2.7),  # left wall
            Box2D(17.5,  3.5, 0.5, 2.7),  # right wall
            # Final column before goal
            Box2D(20.0,  1.5, 0.5, 0.5),
        ],
    },
    "slalom": {
        "start": (0.0, 0.0),
        "goal":  (15.0, 0.0),
        "obstacles": [
            Box2D(2.5,  1.8, 0.5, 0.5),
            Box2D(5.0, -1.8, 0.5, 0.5),
            Box2D(7.5,  1.8, 0.5, 0.5),
            Box2D(10.0,-1.8, 0.5, 0.5),
            Box2D(12.5, 1.8, 0.5, 0.5),
        ],
    },
    "maze": {
        # Rover starts in bottom-left open area, goal is top-right.
        # A T-wall blocks the direct path — must go around the vertical bar.
        "start": (1.5, 1.5),
        "goal":  (11.0, 9.0),
        "obstacles": [
            # Vertical spine wall blocking direct path
            Box2D(6.0,  6.0, 0.4, 5.0),   # tall vertical bar (gap below y=1)
            # Horizontal crossbar — creates L-shape
            Box2D(9.0,  6.0, 3.0, 0.4),   # horizontal: blocks going right at y=6
            # Scattered columns to make the detour non-trivial
            Box2D(3.5,  5.0, 0.45, 0.45),
            Box2D(9.0,  3.5, 0.45, 0.45),
            Box2D(4.5,  8.5, 0.45, 0.45),
            # Narrow corridor wall forcing a specific entry angle
            Box2D(8.0,  8.5, 0.4, 1.5),   # partial right wall near goal
        ],
    },
    "cluttered": {
        "start": (0.0, 0.0),
        "goal":  (14.0, 2.0),
        "obstacles": [
            Box2D(2.0,  0.5, 0.4, 0.4), Box2D(2.0, -2.0, 0.4, 0.4),
            Box2D(4.0,  2.0, 0.5, 0.5), Box2D(4.5, -0.5, 0.3, 0.3),
            Box2D(6.5,  1.0, 0.5, 0.5), Box2D(6.0, -2.0, 0.4, 0.4),
            Box2D(8.5,  3.0, 0.4, 0.4), Box2D(8.5, -1.0, 0.5, 0.5),
            Box2D(10.5, 0.5, 0.4, 0.4), Box2D(10.0,-2.5, 0.4, 0.4),
            Box2D(12.0, 2.5, 0.6, 0.6),
        ],
    },
}


# --------------------------------------------------------------------------- VFH planner
class VFHPlanner:
    """Vector Field Histogram reactive planner.

    Builds a polar obstacle density histogram from the lidar scan, identifies
    'free valleys' (consecutive sectors below a density threshold), picks the
    valley closest to the goal direction, and steers toward it.

    Handles local minima with a random-walk escape after N consecutive stall ticks.
    """

    N_SECTORS = 36       # 10° sectors over 360°
    RAYS_PER = LIDAR_RAYS // N_SECTORS
    DENSITY_THRESH = 0.6 # fraction of a sector that must be blocked to call it occupied
    CLEAR_DIST = 1.2     # m: rays closer than this increase density
    SAFETY_R = 0.55      # m: hard-stop if any ray < this
    V_NOM = 1.2          # nominal forward speed
    KW = 3.5             # yaw-rate gain (rad/s per rad of heading error)

    def __init__(self, seed=42):
        self._rng = np.random.default_rng(seed)
        self._stall = 0
        self._escape_ticks = 0
        self._escape_w = 0.0

    def reset(self):
        self._stall = 0
        self._escape_ticks = 0

    def step(self, x, y, yaw, gx, gy, scan):
        # Goal bearing in body frame
        dx, dy = gx - x, gy - y
        dist_goal = math.hypot(dx, dy)
        goal_world = math.atan2(dy, dx)
        goal_body = (goal_world - yaw + math.pi) % (2 * math.pi) - math.pi
        goal_sector = int((goal_body % (2 * math.pi)) / (2 * math.pi) * self.N_SECTORS)

        # Build density histogram
        hist = np.zeros(self.N_SECTORS)
        for s in range(self.N_SECTORS):
            rays = scan[s * self.RAYS_PER:(s + 1) * self.RAYS_PER]
            blocked = np.sum(rays < self.CLEAR_DIST) / self.RAYS_PER
            hist[s] = blocked

        # Hard-stop zone: nearest ray
        min_ray = float(np.min(scan))

        # Find free valleys: sectors where hist < DENSITY_THRESH
        free = hist < self.DENSITY_THRESH
        if not free.any():
            # fully surrounded → spin to find opening
            self._stall += 1
            return 0.0, MAX_W * 0.8, False

        # Find the free sector closest (angular distance) to goal sector
        best_sec = None; best_angdist = math.inf
        for s in range(self.N_SECTORS):
            if not free[s]:
                continue
            ad = abs((s - goal_sector + self.N_SECTORS // 2) % self.N_SECTORS
                     - self.N_SECTORS // 2)
            if ad < best_angdist:
                best_angdist = ad
                best_sec = s

        # Target heading: centre of chosen sector
        target_body = (best_sec + 0.5) / self.N_SECTORS * 2 * math.pi
        if target_body > math.pi:
            target_body -= 2 * math.pi
        yaw_err = target_body

        # Check stall (heading toward goal but not moving forward because of dense field)
        if abs(yaw_err) < 0.3 and min_ray < self.CLEAR_DIST * 0.7:
            self._stall += 1
        else:
            self._stall = 0

        # Escape: if stalled 60+ ticks, pick a random free sector and commit for 40 ticks
        if self._escape_ticks > 0:
            self._escape_ticks -= 1
            return self.V_NOM * 0.5, self._escape_w
        if self._stall > 60:
            self._stall = 0
            self._escape_ticks = 40
            free_secs = np.where(free)[0]
            esc_sec = self._rng.choice(free_secs)
            esc_body = (esc_sec + 0.5) / self.N_SECTORS * 2 * math.pi
            if esc_body > math.pi: esc_body -= 2 * math.pi
            self._escape_w = float(np.clip(self.KW * esc_body, -MAX_W, MAX_W))
            return self.V_NOM * 0.4, self._escape_w

        # Normal: proportional yaw control toward target valley
        w = float(np.clip(self.KW * yaw_err, -MAX_W, MAX_W))
        # Slow down if turning hard or obstacle is very close
        turn_factor = max(0.2, 1.0 - abs(yaw_err) / math.pi)
        prox_factor = min(1.0, max(0.1, (min_ray - self.SAFETY_R) / 0.8))
        v = self.V_NOM * turn_factor * prox_factor

        reached = dist_goal < REACH_R
        return float(v), w, reached


# --------------------------------------------------------------------------- runner
def run_course(planner, course_def, seed=0):
    sx, sy = course_def["start"]
    gx, gy = course_def["goal"]
    boxes = course_def["obstacles"]

    planner.reset()
    x, y, yaw = sx, sy, 0.0
    v, w = 0.0, 0.0
    reached = collided = False
    traj = [(x, y)]
    min_clr = LIDAR_MAX

    for tick in range(MAX_TICKS):
        clr = min_dist(x, y, boxes)
        min_clr = min(min_clr, clr)
        if clr < HALT_R:
            collided = True
            break
        scan = lidar_scan(x, y, yaw, boxes)
        result = planner.step(x, y, yaw, gx, gy, scan)
        if len(result) == 3:
            v, w, done = result
        else:
            v, w = result; done = False
        if done:
            reached = True
            traj.append((x, y))
            break
        # Unicycle
        x += math.cos(yaw) * v * DT
        y += math.sin(yaw) * v * DT
        yaw = (yaw + w * DT + math.pi) % (2 * math.pi) - math.pi
        if tick % 4 == 0:
            traj.append((x, y))

    dist_final = math.hypot(gx - x, gy - y)
    return {
        "reached": reached, "collided": collided,
        "final_pos": (round(x, 2), round(y, 2)),
        "final_dist": round(dist_final, 2),
        "min_clearance": round(min_clr, 2),
        "ticks": tick + 1,
    }, traj


# --------------------------------------------------------------------------- ASCII renderer
def render_ascii(course_def, traj, width=90, height=30):
    sx, sy = course_def["start"]
    gx, gy = course_def["goal"]
    boxes = course_def["obstacles"]
    all_x = [sx, gx] + [b.cx for b in boxes]
    all_y = [sy, gy] + [b.cy for b in boxes]
    pad = 1.5
    xlo = min(all_x) - pad; xhi = max(all_x) + pad + 2
    ylo = min(all_y) - pad; yhi = max(all_y) + pad

    def wx(x): return int((x - xlo) / (xhi - xlo) * (width - 1))
    def wy(y): return int((1 - (y - ylo) / (yhi - ylo)) * (height - 1))

    grid = [['·'] * width for _ in range(height)]

    # Draw obstacles
    for b in boxes:
        for px in range(width):
            for py in range(height):
                wx_val = xlo + px / (width - 1) * (xhi - xlo)
                wy_val = ylo + (1 - py / (height - 1)) * (yhi - ylo)
                if abs(wx_val - b.cx) <= b.hx and abs(wy_val - b.cy) <= b.hy:
                    grid[py][px] = '█'

    # Draw trajectory
    for i, (tx, ty) in enumerate(traj):
        px, py = wx(tx), wy(ty)
        if 0 <= px < width and 0 <= py < height:
            if grid[py][px] == '·':
                grid[py][px] = '∘' if i < len(traj) - 1 else '◉'

    # Start / goal
    spx, spy = wx(sx), wy(sy)
    gpx, gpy = wx(gx), wy(gy)
    if 0 <= spx < width and 0 <= spy < height:
        grid[spy][spx] = 'S'
    if 0 <= gpx < width and 0 <= gpy < height:
        grid[gpy][gpx] = 'G'

    # Scale bar
    lines = [''.join(row) for row in grid]
    scale_m = (xhi - xlo) / 4
    lines.append(f"  x: {xlo:.1f}m {'─' * (width // 4)}► {xhi:.1f}m    "
                 f"(scale ≈{scale_m:.1f}m per quarter-width)")
    return '\n'.join(lines)


# --------------------------------------------------------------------------- SVG renderer
def render_svg(course_def, traj, W=900, H=400) -> str:
    sx, sy = course_def["start"]
    gx, gy = course_def["goal"]
    boxes = course_def["obstacles"]
    all_x = [sx, gx] + [b.cx - b.hx for b in boxes] + [b.cx + b.hx for b in boxes]
    all_y = [sy, gy] + [b.cy - b.hy for b in boxes] + [b.cy + b.hy for b in boxes]
    pad = 1.0
    xlo = min(all_x) - pad; xhi = max(all_x) + pad
    ylo = min(all_y) - pad; yhi = max(all_y) + pad
    mx, my = 30, 20

    def px(x): return mx + (x - xlo) / (xhi - xlo) * (W - 2 * mx)
    def py(y): return H - my - (y - ylo) / (yhi - ylo) * (H - 2 * my)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'style="background:#111;font-family:monospace">',
    ]
    # Obstacles
    for b in boxes:
        x0, y0 = px(b.cx - b.hx), py(b.cy + b.hy)
        bw = (b.hx * 2) / (xhi - xlo) * (W - 2 * mx)
        bh = (b.hy * 2) / (yhi - ylo) * (H - 2 * my)
        lines.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
                     f'fill="#4a5568" stroke="#718096" stroke-width="1"/>')
    # Trajectory
    if traj:
        pts = ' '.join(f"{px(tx):.1f},{py(ty):.1f}" for tx, ty in traj)
        lines.append(f'<polyline points="{pts}" fill="none" stroke="#48bb78" '
                     f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')
    # Start / goal
    lines.append(f'<circle cx="{px(sx):.1f}" cy="{py(sy):.1f}" r="6" fill="#63b3ed"/>')
    lines.append(f'<text x="{px(sx)+8:.1f}" y="{py(sy)+4:.1f}" fill="#63b3ed" '
                 f'font-size="12">START</text>')
    lines.append(f'<circle cx="{px(gx):.1f}" cy="{py(gy):.1f}" r="6" fill="#f6ad55"/>')
    lines.append(f'<text x="{px(gx)+8:.1f}" y="{py(gy)+4:.1f}" fill="#f6ad55" '
                 f'font-size="12">GOAL</text>')
    lines.append('</svg>')
    return '\n'.join(lines)


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", default="gauntlet",
                    choices=list(COURSES.keys()), help="which course to run")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--svg", default=None, help="write SVG to this path")
    args = ap.parse_args()

    planner = VFHPlanner()
    names = list(COURSES.keys()) if args.all else [args.course]

    for name in names:
        course = COURSES[name]
        print(f"\n{'═'*60}")
        print(f"  Course: {name.upper()}")
        print(f"  Start: {course['start']}  Goal: {course['goal']}")
        print(f"  Obstacles: {len(course['obstacles'])}")
        print(f"{'═'*60}")

        result, traj = run_course(planner, course)
        status = "✓ REACHED" if result["reached"] else ("✗ COLLISION" if result["collided"] else "✗ TIMEOUT")
        print(f"  {status}   ticks={result['ticks']}   "
              f"final_dist={result['final_dist']:.1f}m   "
              f"min_clearance={result['min_clearance']:.2f}m")
        print()
        print(render_ascii(course, traj))

        if args.svg and not args.all:
            svg = render_svg(course, traj)
            Path(args.svg).write_text(svg)
            print(f"\nSVG written to {args.svg}")

    if args.svg and args.all:
        # Render last course to SVG
        svg = render_svg(COURSES[names[-1]], traj)
        Path(args.svg).write_text(svg)
        print(f"\nSVG written to {args.svg}")


if __name__ == "__main__":
    main()
