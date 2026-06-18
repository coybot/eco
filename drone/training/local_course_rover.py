"""Headless kinematic CPU validation for the rover learned policy.

Runs the LearnedPlanner through 2D obstacle courses without Isaac Sim.
Courses are defined as XY box layouts; depth sensing uses the same DEPTH_RAYS ray-AABB
math as RoverEnv (floor-to-ceiling prisms, camera at z=CAMERA_Z).

    python -m eco.drone.training.local_course_rover --course slalom
    python -m eco.drone.training.local_course_rover --all --trials 10 --depth-noise 0.07
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here.parent / "common"))
sys.path.insert(0, str(_here))

from reactive_planner import LearnedPlanner           # noqa: E402
from contract import (                                # noqa: E402
    DEPTH_RAYS, RAY_DIRS, DEPTH_MAX, VEHICLE_ROVER, wrap_pi
)

DT = 0.1
MAX_TICKS = 400
HALT_R = 0.35          # collision radius (m)
REACH_R = 1.0          # goal-reached radius (m)
CAMERA_Z = 0.5         # rover camera height (matches RoverEnv)


# --------------------------------------------------------------------------- courses
@dataclass
class Box2D:
    cx: float; cy: float; hx: float; hy: float

    def dist_2d(self, x: float, y: float) -> float:
        ddx = max(abs(x - self.cx) - self.hx, 0.0)
        ddy = max(abs(y - self.cy) - self.hy, 0.0)
        return math.hypot(ddx, ddy)


COURSES = {
    "straight": {
        "goal": [10.0, 0.0],
        "obstacles": [],
    },
    "slalom": {
        # Three offset columns to weave through
        "goal": [12.0, 0.0],
        "obstacles": [
            [3.0,  1.2, 0.4, 0.4],
            [6.0, -1.2, 0.4, 0.4],
            [9.0,  1.2, 0.4, 0.4],
        ],
    },
    "tight_gap": {
        # Wall with a 1.5 m gap centred on the path
        "goal": [10.0, 0.0],
        "obstacles": [
            [5.0, -4.0, 0.4, 3.25],   # left wall
            [5.0,  4.0, 0.4, 3.25],   # right wall
        ],
    },
    "maze_4": {
        # Four-room maze: two corridors, alternating left/right gaps
        "goal": [14.0, 0.0],
        "obstacles": [
            [4.0,  -3.5, 0.4, 1.5],   # wall 1 left segment
            [4.0,   4.5, 0.4, 0.5],   # wall 1 right segment (gap at y=-1..+1)
            [8.0,  -4.5, 0.4, 0.5],   # wall 2 left segment (gap at y=-1..+1)
            [8.0,   3.5, 0.4, 1.5],   # wall 2 right segment
            [12.0, -3.5, 0.4, 1.5],   # wall 3 left segment
            [12.0,  4.5, 0.4, 0.5],   # wall 3 right segment (gap at y=-1..+1)
        ],
    },
    "corridor": {
        # Narrow corridor with offset bumps
        "goal": [12.0, 0.0],
        "obstacles": [
            [2.5,  1.6, 0.4, 0.4],
            [5.0, -1.6, 0.4, 0.4],
            [7.5,  1.6, 0.4, 0.4],
            [10.0, -1.6, 0.4, 0.4],
        ],
    },
}


# --------------------------------------------------------------------------- sensing
def _ray_box_2d(ox: float, oy: float, dx: float, dy: float, box: Box2D) -> float:
    """Distance to AABB along 2D ray (ox,oy)+(dx,dy), or inf."""
    tmin, tmax = -math.inf, math.inf
    for o, d, c, h in ((ox, dx, box.cx, box.hx), (oy, dy, box.cy, box.hy)):
        lo, hi = c - h, c + h
        if abs(d) < 1e-9:
            if o < lo or o > hi:
                return math.inf
        else:
            t1, t2 = (lo - o) / d, (hi - o) / d
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1); tmax = min(tmax, t2)
            if tmin > tmax:
                return math.inf
    return math.inf if tmax < 0 else (tmin if tmin >= 0 else 0.0)


def depth_grid_2d(
    x: float, y: float, yaw: float,
    boxes: list[Box2D],
    noise_std: float = 0.0,
    rng: Optional[np.random.Generator] = None,
) -> np.ndarray:
    """Cast all DEPTH_RAYS against floor-to-ceiling boxes and return a DEPTH_RAYS-length array.

    For floor-to-ceiling prisms the XY-plane distance is independent of pitch, so we project
    each 3D ray direction onto the XY plane to get the horizontal bearing, then do 2D ray-box.
    """
    out = np.full(DEPTH_RAYS, DEPTH_MAX, dtype=np.float32)
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    for i, (rf, rl, _) in enumerate(RAY_DIRS):
        # World-frame horizontal direction of this ray
        dx = cos_y * rf - sin_y * rl
        dy = sin_y * rf + cos_y * rl
        norm = math.hypot(dx, dy)
        if norm < 1e-6:
            out[i] = DEPTH_MAX
            continue
        dx /= norm; dy /= norm
        best = DEPTH_MAX
        for b in boxes:
            t = _ray_box_2d(x, y, dx, dy, b)
            if t < best:
                best = t
        if noise_std > 0 and rng is not None:
            best = float(np.clip(best + rng.normal(0.0, noise_std), 0.0, DEPTH_MAX))
        out[i] = min(max(best, 0.0), DEPTH_MAX)
    return out


def min_dist_to_boxes(x: float, y: float, boxes: list[Box2D]) -> float:
    if not boxes:
        return math.inf
    return min(b.dist_2d(x, y) for b in boxes)


# --------------------------------------------------------------------------- runner
def run_course(planner, course_def, halt_r=HALT_R, capture=True,
               depth_noise=0.0, target_noise=0.0, rng=None):
    """Drive the planner through one 2D course. Returns result dict + trajectory list."""
    gx, gy = course_def["goal"]
    boxes = [Box2D(*o) for o in course_def["obstacles"]]
    if rng is None:
        rng = np.random.default_rng(0)
    planner.reset()

    x, y, yaw = 0.0, 0.0, 0.0
    reached = collided = False
    min_clear = DEPTH_MAX
    traj = []

    for tick in range(MAX_TICKS):
        clr = min_dist_to_boxes(x, y, boxes)
        min_clear = min(min_clear, clr)
        if capture:
            traj.append((x, y, yaw, clr))
        if clr < halt_r:
            collided = True
            break

        fan = depth_grid_2d(x, y, yaw, boxes,
                            noise_std=depth_noise,
                            rng=(rng if depth_noise > 0 else None))

        # Body-frame target (2D — target_up = 0)
        dx, dy = gx - x, gy - y
        cos_y, sin_y = math.cos(-yaw), math.sin(-yaw)
        tf = cos_y * dx - sin_y * dy
        tl = sin_y * dx + cos_y * dy
        tgt = (tf, tl, 0.0)
        if target_noise > 0:
            tgt = (tgt[0] + rng.normal(0, target_noise),
                   tgt[1] + rng.normal(0, target_noise),
                   0.0)

        plan = planner.step(tgt, depth_fan=fan, altitude_m=0.0, dt=DT)

        if plan.reached:
            reached = True
            break
        if plan.rejected:
            break

        # Integrate 2D kinematics
        cos_y2, sin_y2 = math.cos(yaw), math.sin(yaw)
        x += (cos_y2 * plan.vx - sin_y2 * plan.vy) * DT
        y += (sin_y2 * plan.vx + cos_y2 * plan.vy) * DT
        yaw = wrap_pi(yaw + plan.yaw_rate * DT)

    dist = math.hypot(gx - x, gy - y)
    return {
        "reached": reached, "collided": collided,
        "final_pos": (round(x, 2), round(y, 2)),
        "final_dist": round(dist, 2),
        "min_clearance": round(min_clear, 2),
        "ticks": tick + 1,
    }, traj


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=str(_here.parent / "models"))
    ap.add_argument("--onnx-name", default="policy_rover_v1.onnx")
    ap.add_argument("--course", default=None, help="single course name; omit for all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--trace", action="store_true",
                    help="print waypoint trail every 10 ticks")
    ap.add_argument("--depth-noise", type=float, default=0.0)
    ap.add_argument("--target-noise", type=float, default=0.0)
    ap.add_argument("--trials", type=int, default=1)
    args = ap.parse_args()

    planner = LearnedPlanner(
        models_dir=args.models_dir,
        onnx_name=args.onnx_name,
        vehicle=VEHICLE_ROVER,
        reach_threshold=REACH_R,
        max_speed=2.0,
    )
    loaded = planner._session is not None
    print(f"onnx loaded={loaded}  model={args.onnx_name}  vehicle=ROVER\n")

    noisy = args.depth_noise > 0 or args.target_noise > 0
    if noisy:
        print(f"perturbations: depth_noise={args.depth_noise}m  "
              f"target_noise={args.target_noise}m  trials={args.trials}\n")

    names = [args.course] if args.course else list(COURSES.keys())
    for name in names:
        course = COURSES[name]
        if args.trials > 1 or noisy:
            nreach = ncoll = 0
            clears = []
            for k in range(args.trials):
                res, _ = run_course(planner, course,
                                    depth_noise=args.depth_noise,
                                    target_noise=args.target_noise,
                                    rng=np.random.default_rng(1000 + k))
                nreach += res["reached"]; ncoll += res["collided"]
                clears.append(res["min_clearance"])
            print(f"{name:<12} reach={nreach}/{args.trials}  "
                  f"collided={ncoll}/{args.trials}  "
                  f"min_clear[min/mean]={min(clears):.2f}/{sum(clears)/len(clears):.2f}")
        else:
            res, traj = run_course(planner, course)
            print(f"{name:<12} {res}")
            if args.trace:
                for i in range(0, len(traj), 10):
                    x, y, yaw, clr = traj[i]
                    print(f"    t={i:3d}  x={x:5.2f} y={y:5.2f} "
                          f"yaw={math.degrees(yaw):6.1f}  clr={clr:.2f}")


if __name__ == "__main__":
    main()
