"""Headless kinematic CPU validation for the rover learned policy.

Runs the policy through named 2D obstacle courses using 360° lidar sensing (no Isaac needed).
Courses are defined as XY box layouts; the rover integrates unicycle kinematics with DT=0.1s.

The policy ONNX is consumed via a thin RoverPlanner wrapper that mirrors how LearnedPlanner
works for the drone — carry-state GRU, step-mode inference.

    # Validate a trained rover policy across all courses:
    python -m drone.training.local_course_rover \\
        --onnx ~/drone-data/rover/models/policy_rover_v1.onnx --all

    # Noisy stress test (simulates real RPLidar + odometry noise):
    python -m drone.training.local_course_rover \\
        --onnx ~/drone-data/rover/models/policy_rover_v1.onnx \\
        --all --trials 20 --lidar-noise 0.05 --target-noise 0.15

    # Single course with waypoint trace:
    python -m drone.training.local_course_rover \\
        --onnx policy_rover_v1.onnx --course slalom --trace
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
sys.path.insert(0, str(_here))

from rover_contract import (                        # noqa: E402
    R_STATE_DIM, R_ACTION_DIM, LIDAR_RAYS, LIDAR_MAX, LIDAR_ANGLES,
    R_STATE_MEAN, R_STATE_STD, wrap_pi, build_obs,
)

DT = 0.1
MAX_TICKS = 400
HALT_R = 0.30      # collision halt radius (m) — rover body half-width
REACH_R = 1.0      # goal-reached radius (m)
MAX_V = 2.0
MAX_W = 2.0


# --------------------------------------------------------------------------- geometry
@dataclass
class Box2D:
    cx: float; cy: float; hx: float; hy: float

    def dist_2d(self, x: float, y: float) -> float:
        return math.hypot(max(abs(x - self.cx) - self.hx, 0.0),
                          max(abs(y - self.cy) - self.hy, 0.0))


def _ray_box_2d(ox: float, oy: float, dx: float, dy: float, b: Box2D) -> float:
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


def lidar_scan_2d(
    x: float, y: float, yaw: float,
    boxes: list[Box2D],
    noise_std: float = 0.0,
    rng: Optional[np.random.Generator] = None,
) -> np.ndarray:
    """Full 360° lidar scan (LIDAR_RAYS values) via 2D ray-box intersection."""
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    out = np.full(LIDAR_RAYS, LIDAR_MAX, dtype=np.float32)
    for i, a in enumerate(LIDAR_ANGLES):
        # Rotate body-frame ray direction into world frame
        bfwd, bleft = math.cos(a), math.sin(a)
        dx = cos_y * bfwd - sin_y * bleft
        dy = sin_y * bfwd + cos_y * bleft
        best = LIDAR_MAX
        for b in boxes:
            t = _ray_box_2d(x, y, dx, dy, b)
            if t < best:
                best = t
        if noise_std > 0 and rng is not None:
            best = float(np.clip(best + rng.normal(0.0, noise_std), 0.0, LIDAR_MAX))
        out[i] = min(max(best, 0.0), LIDAR_MAX)
    return out


def min_dist(x: float, y: float, boxes: list[Box2D]) -> float:
    if not boxes:
        return math.inf
    return min(b.dist_2d(x, y) for b in boxes)


# --------------------------------------------------------------------------- courses
#  Each course: "goal": [x, y], "obstacles": [[cx, cy, hx, hy], ...]
COURSES = {
    "straight": {
        "goal": [10.0, 0.0],
        "obstacles": [],
    },
    "slalom": {
        # Three staggered columns — weave left-right-left
        "goal": [12.0, 0.0],
        "obstacles": [
            [3.0,  1.3, 0.4, 0.4],
            [6.0, -1.3, 0.4, 0.4],
            [9.0,  1.3, 0.4, 0.4],
        ],
    },
    "tight_gap": {
        # Single wall with a 1.6 m navigable gap (rover width ~0.6 m → tight)
        "goal": [10.0, 0.0],
        "obstacles": [
            [5.0, -3.9, 0.4, 3.1],    # left wall
            [5.0,  3.9, 0.4, 3.1],    # right wall
            # gap centred at y=0, width 1.6 m
        ],
    },
    "double_gap": {
        # Two walls with offset gaps — tests planner's sequential commitment
        "goal": [14.0, 0.0],
        "obstacles": [
            [4.0, -4.5, 0.4, 3.7],    # wall-1 left
            [4.0,  4.5, 0.4, 1.7],    # wall-1 right  → gap at y=[-2.8, -0.8]
            [9.0, -4.5, 0.4, 1.7],    # wall-2 left   → gap at y=[0.8, 2.8]
            [9.0,  4.5, 0.4, 3.7],    # wall-2 right
        ],
    },
    "cluttered": {
        # Dense random-ish field of columns
        "goal": [14.0, 0.0],
        "obstacles": [
            [2.5,  1.5, 0.4, 0.4], [2.5, -2.0, 0.4, 0.4],
            [5.0,  0.5, 0.5, 0.5], [5.0, -1.0, 0.3, 0.3],
            [7.5,  2.0, 0.4, 0.4], [7.5, -0.5, 0.5, 0.5],
            [10.0,  1.0, 0.4, 0.4], [10.0, -2.0, 0.3, 0.3],
            [12.0,  0.0, 0.6, 0.6],
        ],
    },
    "maze_4room": {
        # Four-room maze: three corridor walls with alternating left/right gaps
        "goal": [16.0, 0.0],
        "obstacles": [
            [4.0,  -4.0, 0.4, 1.5],   # wall-1 left  → gap at y=[-2.5, +2.5]
            [4.0,   4.0, 0.4, 1.5],   # wall-1 right
            [8.5,  -4.5, 0.4, 0.5],   # wall-2 left  → gap at y=[-4.0, +4.0] on left
            [8.5,   3.0, 0.4, 2.0],   # wall-2 right
            [13.0, -3.0, 0.4, 2.0],   # wall-3 left
            [13.0,  4.5, 0.4, 0.5],   # wall-3 right → gap on right side
        ],
    },
}


# --------------------------------------------------------------------------- planner
class RoverPlanner:
    """Thin ONNX inference wrapper — mirrors LearnedPlanner interface for the rover.

    Carries GRU hidden state across calls (call reset() at episode start).
    Inference: (state[1,1,83], h_in[1,1,hidden]) → (action[1,2], h_out[1,1,hidden])
    """

    def __init__(self, onnx_path: str, reach_threshold: float = REACH_R):
        self.reach_threshold = reach_threshold
        self._session = None
        self._h = None
        self._hidden = None
        try:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            self._session = ort.InferenceSession(str(onnx_path), opts)
            # infer hidden size from the h_in input shape
            self._hidden = self._session.get_inputs()[1].shape[2]
            print(f"[RoverPlanner] loaded {onnx_path} hidden={self._hidden}")
        except Exception as e:
            print(f"[RoverPlanner] ONNX not available ({e}); will return dummy actions")
        self.reset()

    def reset(self):
        if self._hidden:
            self._h = np.zeros((1, 1, self._hidden), dtype=np.float32)

    def step(
        self,
        target_fwd: float,
        target_left: float,
        vel_fwd: float,
        yaw_rate: float,
        lidar: np.ndarray,
        dist: float,
    ):
        """Return (v_linear, yaw_rate, reached)."""
        reached = dist < self.reach_threshold
        if reached or self._session is None:
            return 0.0, 0.0, reached

        obs = build_obs(target_fwd, target_left, vel_fwd, yaw_rate, lidar)
        x = obs.reshape(1, 1, R_STATE_DIM).astype(np.float32)  # ONNX normalizes internally
        action, h_out = self._session.run(None, {"state": x, "h_in": self._h})
        self._h = h_out
        v_lin = float(np.clip(action[0, 0], -MAX_V, MAX_V))
        w = float(np.clip(action[0, 1], -MAX_W, MAX_W))
        return v_lin, w, False


# --------------------------------------------------------------------------- runner
def run_course(
    planner: RoverPlanner,
    course_def: dict,
    halt_r: float = HALT_R,
    capture: bool = True,
    lidar_noise: float = 0.0,
    target_noise: float = 0.0,
    rng: Optional[np.random.Generator] = None,
) -> tuple[dict, list]:
    gx, gy = course_def["goal"]
    boxes = [Box2D(*o) for o in course_def["obstacles"]]
    if rng is None:
        rng = np.random.default_rng(0)
    planner.reset()

    x, y, yaw = 0.0, 0.0, 0.0
    v, w = 0.0, 0.0
    reached = collided = False
    min_clear = LIDAR_MAX
    traj = []
    tick = 0

    for tick in range(MAX_TICKS):
        clr = min_dist(x, y, boxes)
        min_clear = min(min_clear, clr)
        if capture:
            traj.append((x, y, yaw, clr, v))
        if clr < halt_r:
            collided = True
            break

        scan = lidar_scan_2d(x, y, yaw, boxes,
                             noise_std=lidar_noise,
                             rng=rng if lidar_noise > 0 else None)

        # Goal in body frame
        dx, dy = gx - x, gy - y
        tf = math.cos(-yaw) * dx - math.sin(-yaw) * dy
        tl = math.sin(-yaw) * dx + math.cos(-yaw) * dy
        dist_to_goal = math.hypot(dx, dy)
        if target_noise > 0:
            tf += rng.normal(0, target_noise)
            tl += rng.normal(0, target_noise)

        v_cmd, w_cmd, done = planner.step(tf, tl, v, w, scan, dist_to_goal)
        if done:
            reached = True
            break

        # Unicycle integration
        x += math.cos(yaw) * v_cmd * DT
        y += math.sin(yaw) * v_cmd * DT
        yaw = wrap_pi(yaw + w_cmd * DT)
        v, w = v_cmd, w_cmd

    dist_final = math.hypot(gx - x, gy - y)
    return {
        "reached": reached, "collided": collided,
        "final_pos": (round(x, 2), round(y, 2)),
        "final_dist": round(dist_final, 2),
        "min_clearance": round(min_clear, 2),
        "ticks": tick + 1,
    }, traj


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True, help="path to policy_rover_vN.onnx")
    ap.add_argument("--course", default=None, help="one course name; default = all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--trace", action="store_true",
                    help="print waypoint trail every 10 ticks")
    ap.add_argument("--lidar-noise", type=float, default=0.0,
                    help="std (m) of Gaussian lidar range noise (RPLidar: ~0.03-0.05 m)")
    ap.add_argument("--target-noise", type=float, default=0.0,
                    help="std (m) of body-frame goal jitter (odometry error)")
    ap.add_argument("--trials", type=int, default=1,
                    help="repeat each course N times with different noise seeds")
    args = ap.parse_args()

    planner = RoverPlanner(args.onnx)
    noisy = args.lidar_noise > 0 or args.target_noise > 0
    if noisy:
        print(f"perturbations: lidar_noise={args.lidar_noise}m  "
              f"target_noise={args.target_noise}m  trials={args.trials}\n")

    names = [args.course] if args.course else list(COURSES.keys())
    for name in names:
        course = COURSES[name]
        if args.trials > 1 or noisy:
            nreach = ncoll = 0; clears = []
            for k in range(args.trials):
                res, _ = run_course(planner, course,
                                    lidar_noise=args.lidar_noise,
                                    target_noise=args.target_noise,
                                    rng=np.random.default_rng(2000 + k))
                nreach += res["reached"]; ncoll += res["collided"]
                clears.append(res["min_clearance"])
            print(f"{name:<14} reach={nreach}/{args.trials}  "
                  f"collided={ncoll}/{args.trials}  "
                  f"clear[min/mean]={min(clears):.2f}/{sum(clears)/len(clears):.2f}")
        else:
            res, traj = run_course(planner, course)
            print(f"{name:<14} {res}")
            if args.trace:
                for i in range(0, len(traj), 10):
                    x, y, yaw, clr, v = traj[i]
                    print(f"    t={i:3d}  x={x:5.2f} y={y:5.2f} "
                          f"yaw={math.degrees(yaw):6.1f}°  clr={clr:.2f}m  v={v:.2f}m/s")


if __name__ == "__main__":
    main()
