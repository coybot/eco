"""Statistical comparison of planners on random 3D obstacle scenarios (no Isaac, CPU/onnx).

Runs rule (reactive_planner), BC, RL, and RL+DR (policy_v4_*) through the SAME random
3D rectangular-prism scenarios in the kinematic world, reporting reach-rate, collision-rate,
and mean time-to-goal. Obstacles are placed at varied altitudes so reaching may require
going over/under/around.

    PYTHONPATH=. python -m drone.training.validate_v3 --models-dir /home/yusuf/models --n 400
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here.parent / "common"))
sys.path.insert(0, str(_here))

from reactive_planner import ReactivePlanner, LearnedPlanner   # noqa: E402
from world3d import Box3D, depth_grid, min_dist_to_boxes       # noqa: E402
from contract import DEPTH_RAYS                                # noqa: E402

DT = 0.1
MAX_TICKS = 700
COLLIDE_R = 0.3
REACH = 1.0


def random_scenario(rng):
    rng_far = rng.uniform(5.0, 15.0)
    bear = rng.uniform(-math.pi, math.pi)
    gx, gy = rng_far * math.cos(bear), rng_far * math.sin(bear)
    z0 = rng.uniform(1.5, 4.0)
    gz = rng.uniform(-2.5, 2.5)
    goal_alt = max(0.6, z0 + gz)
    boxes = []
    if rng.random() > 0.15:
        ux, uy = gx / rng_far, gy / rng_far
        px, py = -uy, ux
        lo, hi = min(z0, goal_alt), max(z0, goal_alt)
        for _ in range(int(rng.integers(1, 5))):
            frac = rng.uniform(0.25, 0.8)
            lat = rng.uniform(-1.5, 1.5)
            cx = ux * rng_far * frac + px * lat
            cy = uy * rng_far * frac + py * lat
            cz = max(0.4, rng.uniform(lo - 1.2, hi + 1.2))
            boxes.append(Box3D(cx, cy, cz, rng.uniform(0.3, 1.0),
                               rng.uniform(0.3, 1.2), rng.uniform(0.3, 1.2)))
    if (min_dist_to_boxes(0, 0, z0, boxes) < 0.9 or
            min_dist_to_boxes(gx, gy, goal_alt, boxes) < 0.9):
        boxes = []
    yaw0 = rng.uniform(-math.pi, math.pi)   # random start heading (tests turn-then-go robustness)
    return gx, gy, goal_alt, z0, yaw0, boxes


def run(planner, gx, gy, goal_alt, z0, yaw0, boxes, is_learned):
    x = y = 0.0; z = z0; yaw = yaw0; vxw = vyw = vzw = 0.0
    if hasattr(planner, "reset"):
        planner.reset()
    for tick in range(MAX_TICKS):
        dx, dy, dz = gx - x, gy - y, goal_alt - z
        c, s = math.cos(-yaw), math.sin(-yaw)
        tf, tl, tu = c * dx - s * dy, s * dx + c * dy, dz
        fan = depth_grid(x, y, z, yaw, boxes)
        if is_learned:
            p = planner.step((tf, tl, tu), depth_fan=fan, altitude_m=z, dt=DT)
        else:
            p = planner.step((tf, tl, tu), float(np.min(fan)), altitude_m=z, dt=DT)
        cc, ss = math.cos(yaw), math.sin(yaw)
        vxw = cc * p.vx - ss * p.vy; vyw = ss * p.vx + cc * p.vy; vzw = p.vz
        x += vxw * DT; y += vyw * DT; z = max(0.2, z + vzw * DT)
        yaw = (yaw + p.yaw_rate * DT + math.pi) % (2 * math.pi) - math.pi
        if min_dist_to_boxes(x, y, z, boxes) < COLLIDE_R:
            return {"reached": False, "collided": True, "ticks": tick}
        if p.reached:
            return {"reached": True, "collided": False, "ticks": tick}
        if p.rejected:
            return {"reached": False, "collided": False, "ticks": tick}
    return {"reached": False, "collided": False, "ticks": MAX_TICKS}


def evaluate(planner, scenarios, is_learned):
    reach = coll = 0
    ticks_reached = []
    for sc in scenarios:
        r = run(planner, *sc, is_learned)
        reach += r["reached"]; coll += r["collided"]
        if r["reached"]:
            ticks_reached.append(r["ticks"])
    n = len(scenarios)
    return {"reach_pct": round(100 * reach / n, 1),
            "collision_pct": round(100 * coll / n, 1),
            "mean_time_s": round(float(np.mean(ticks_reached)) * DT, 2) if ticks_reached else float("nan")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    scenarios = [random_scenario(rng) for _ in range(args.n)]

    rule = ReactivePlanner(reach_threshold=1.0, max_speed=3.0)
    def lp(name):
        return LearnedPlanner(models_dir=args.models_dir, reach_threshold=1.0, max_speed=3.0,
                              vehicle=0.0, onnx_name=name)
    planners = {
        "rule": (rule, False),
        "BC (policy_v4)": (lp("policy_v4.onnx"), True),
        "RL+DR (policy_v4_dr)": (lp("policy_v4_dr.onnx"), True),
    }
    for name, (p, isl) in planners.items():
        if isl:
            print(f"{name}: onnx loaded={p._session is not None}")

    print(f"\n{args.n} random 3D scenarios (DEPTH_RAYS={DEPTH_RAYS}):")
    print(f"{'planner':<24} {'reach%':>7} {'collision%':>11} {'mean_time_s':>12}")
    print("-" * 56)
    for name, (p, isl) in planners.items():
        r = evaluate(p, scenarios, isl)
        print(f"{name:<24} {r['reach_pct']:>7} {r['collision_pct']:>11} {r['mean_time_s']:>12}")


if __name__ == "__main__":
    main()
