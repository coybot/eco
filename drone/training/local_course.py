"""Headless kinematic re-run of the Isaac obstacle courses (no Isaac needed).

`run_pass` in record_comparison.py integrates the planner's velocity setpoint kinematically and
halts on contact — Isaac only adds visuals. This module reproduces that exact loop on CPU so the
gauntlet2 stall can be reproduced and the fix iterated without the training box. Trajectories are
also returned so we can plot/inspect where the drone goes.

    ~/.presidio-venv/bin/python -m drone.training.local_course --course gauntlet2
    ~/.presidio-venv/bin/python -m drone.training.local_course --all
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

from reactive_planner import LearnedPlanner, HierarchicalPlanner  # noqa: E402
from world3d import Box3D, depth_grid, min_dist_to_boxes  # noqa: E402
from record_comparison import COURSES, body_target, world_vel, START_Z  # noqa: E402

DT = 0.1
MAX_TICKS = 400
HALT_R = 0.3
DEPTH_MAX = 10.0


def run_course(planner, course, halt_r=HALT_R, capture=True,
               depth_noise=0.0, target_noise=0.0, rng=None, alt_cap=None):
    """Fly the learned planner through one course; return result dict + trajectory.

    `depth_noise`: std (m) of Gaussian noise added to the depth grid (deploy: RealSense noise).
    `target_noise`: std (m) of jitter on the body-frame target (deploy: vision backproject error).
    """
    goal = tuple(course["goal"])
    boxes = [Box3D(*o) for o in course["obstacles"]]
    hierarchical = isinstance(planner, HierarchicalPlanner)
    if rng is None:
        rng = np.random.default_rng(0)
    planner.reset()

    x, y, z = 0.0, 0.0, START_Z
    yaw = 0.0
    reached = collided = rejected = False
    min_clear = DEPTH_MAX
    reject_reason = ""
    traj = []

    for tick in range(MAX_TICKS):
        clr = min_dist_to_boxes(x, y, z, boxes)
        min_clear = min(min_clear, clr)
        if capture:
            traj.append((x, y, z, yaw, clr))
        if clr < halt_r:
            collided = True
            break

        fan = depth_grid(x, y, z, yaw, boxes,
                         noise_std=depth_noise, rng=(rng if depth_noise > 0 else None))
        if hierarchical:
            plan = planner.step((x, y, z), yaw, goal, depth_fan=fan, dt=DT, boxes=boxes)
        else:
            tgt = body_target(goal, (x, y, z), yaw)
            if target_noise > 0:
                tgt = tuple(c + rng.normal(0.0, target_noise) for c in tgt)
            plan = planner.step(tgt, depth_fan=fan, altitude_m=z, dt=DT)

        if plan.reached:
            reached = True
            break
        if plan.rejected:
            rejected = True
            reject_reason = plan.reason
            break

        wvx, wvy, wvz = world_vel(plan.vx, plan.vy, plan.vz, yaw)
        # deploy altitude cap: no climbing above alt_cap (mirrors run_prompt.py max_alt)
        if alt_cap is not None and z >= alt_cap and wvz > 0:
            wvz = 0.0
        x += wvx * DT
        y += wvy * DT
        z = max(0.2, z + wvz * DT)
        yaw += plan.yaw_rate * DT

    dist = math.sqrt((goal[0] - x) ** 2 + (goal[1] - y) ** 2 + (goal[2] - z) ** 2)
    return {
        "reached": reached, "collided": collided, "rejected": rejected,
        "reject_reason": reject_reason,
        "final_pos": (round(x, 2), round(y, 2), round(z, 2)),
        "final_dist": round(dist, 2), "min_clearance": round(min_clear, 2),
        "ticks": tick + 1,
    }, traj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=str(_here.parent / "models"))
    ap.add_argument("--onnx-name", default="policy_v4_dr.onnx")
    ap.add_argument("--course", default=None, help="single course name; default = all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--trace", action="store_true", help="print waypoint trail every 10 ticks")
    ap.add_argument("--hierarchical", action="store_true",
                    help="run the A* + policy HierarchicalPlanner instead of policy-alone")
    ap.add_argument("--privileged", action="store_true",
                    help="hierarchical with A* over KNOWN boxes (sim proof) instead of online occupancy")
    ap.add_argument("--depth-noise", type=float, default=0.0, help="std (m) of depth-grid noise")
    ap.add_argument("--target-noise", type=float, default=0.0, help="std (m) of body-target jitter")
    ap.add_argument("--trials", type=int, default=1, help="repeat each course N times (noise seeds)")
    ap.add_argument("--alt-cap", type=float, default=None,
                    help="deploy altitude cap (m): no climbing above this (mirrors run_prompt max_alt)")
    args = ap.parse_args()

    if args.hierarchical:
        planner = HierarchicalPlanner(models_dir=args.models_dir, onnx_name=args.onnx_name,
                                      vehicle=0.0, max_speed=3.0, reach_threshold=1.0,
                                      online_occupancy=not args.privileged)
        loaded = planner.using_policy
    else:
        planner = LearnedPlanner(models_dir=args.models_dir, reach_threshold=1.0,
                                 max_speed=3.0, vehicle=0.0, onnx_name=args.onnx_name)
        loaded = planner._session is not None
    mode = "hierarchical (A*+policy)" if args.hierarchical else "policy-alone"
    print(f"onnx loaded={loaded}  model={args.onnx_name}  mode={mode}\n")

    noisy = args.depth_noise > 0 or args.target_noise > 0
    if noisy:
        print(f"perturbations: depth_noise={args.depth_noise}m target_noise={args.target_noise}m "
              f"trials={args.trials}\n")
    names = [args.course] if args.course else list(COURSES.keys())
    for name in names:
        if args.trials > 1 or noisy:
            nreach = ncoll = 0
            clears = []
            for k in range(args.trials):
                res, _ = run_course(planner, COURSES[name], depth_noise=args.depth_noise,
                                    target_noise=args.target_noise, alt_cap=args.alt_cap,
                                    rng=np.random.default_rng(1000 + k))
                nreach += res["reached"]; ncoll += res["collided"]; clears.append(res["min_clearance"])
            print(f"{name:<12} reach={nreach}/{args.trials} collided={ncoll}/{args.trials} "
                  f"min_clear[min/mean]={min(clears):.2f}/{sum(clears)/len(clears):.2f}")
        else:
            res, traj = run_course(planner, COURSES[name], alt_cap=args.alt_cap)
            print(f"{name:<12} {res}")
            if args.trace:
                for i in range(0, len(traj), 10):
                    x, y, z, yaw, clr = traj[i]
                    print(f"    t={i:3d}  x={x:5.2f} y={y:5.2f} z={z:5.2f} "
                          f"yaw={math.degrees(yaw):6.1f} clr={clr:4.2f}")


if __name__ == "__main__":
    main()
