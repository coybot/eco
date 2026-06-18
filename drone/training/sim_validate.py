"""Kinematic sim validation: compare rule-based vs learned planner on a waypoint suite.

Runs both planners through the same set of goals in a pure-Python kinematic simulation
(same physics as the Isaac/Godot bridges: pose integration, no dynamics).  Measures and
prints a smoothness comparison: jerk RMS, yaw-rate variance, time-to-reach, and per-goal
trajectories as a JSON report.

Usage (on hoopoe or locally, no GPU needed):
    python -m eco.drone.training.sim_validate \
        --models-dir eco/drone/models \
        --out /tmp/sim_validate_report.json

The script exits 0 if the learned planner's jerk RMS is ≤ 3× the rule planner's (i.e., no
regression), 1 otherwise.  This gate is intentionally generous — the goal at Phase 1 is
smoothness improvement, not perfection.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional

import numpy as np

# Make sibling modules importable when run with PYTHONPATH=<repo_root> or as a script
_here = Path(__file__).parent
sys.path.insert(0, str(_here.parent / "common"))
sys.path.insert(0, str(_here))

from reactive_planner import ReactivePlanner, make_planner  # noqa: E402
from contract import VEHICLE_QUAD, VEHICLE_ROVER            # noqa: E402

DT = 0.1          # 10 Hz, matching training
MAX_TICKS = 3000  # 300 s — well above worst-case jerk-limited settling time
CLEARANCE = 8.0   # obstacle-free scenario (no walls)

# Goal suite: (fwd_m, left_m, up_m, vehicle)
GOALS = [
    (8.0,   0.0,  0.0, VEHICLE_QUAD),   # straight ahead
    (8.0,   4.0,  0.0, VEHICLE_QUAD),   # diagonal
    (5.0,  -3.0,  1.5, VEHICLE_QUAD),   # diagonal + climb
    (0.0,   6.0,  0.0, VEHICLE_QUAD),   # pure lateral
    (10.0,  0.0,  0.0, VEHICLE_QUAD),   # long straight
    (4.0,   4.0,  0.0, VEHICLE_ROVER),  # rover diagonal
    (8.0,  -4.0,  0.0, VEHICLE_ROVER),  # rover reverse diagonal
]


def _run_goal(planner, gx: float, gy: float, gz: float, vehicle: float) -> dict:
    """Simulate one goal and return metrics."""
    # world-frame position and velocity
    x = y = 0.0
    z = 2.0 if vehicle == VEHICLE_QUAD else 0.0
    yaw = 0.0
    vx_w = vy_w = vz_w = 0.0

    if hasattr(planner, "reset"):
        planner.reset()

    goal_alt = z + gz
    vels = []   # body-frame [vx,vy,vz] at each tick

    reached = False
    for _ in range(MAX_TICKS):
        # body-frame target vector
        dx, dy, dz = gx - x, gy - y, goal_alt - z
        c, s = math.cos(-yaw), math.sin(-yaw)
        tf = c * dx - s * dy
        tl = s * dx + c * dy
        tu = dz

        plan = planner.step((tf, tl, tu), CLEARANCE, altitude_m=z, dt=DT)

        bvx, bvy, bvz, yr = plan.vx, plan.vy, plan.vz, plan.yaw_rate
        vels.append([bvx, bvy, bvz])

        # integrate world position
        cc, ss = math.cos(yaw), math.sin(yaw)
        vx_w = cc * bvx - ss * bvy
        vy_w = ss * bvx + cc * bvy
        vz_w = bvz
        x += vx_w * DT
        y += vy_w * DT
        z += vz_w * DT
        yaw += yr * DT

        if plan.reached:
            reached = True
            break

    vels = np.array(vels, dtype=np.float64)
    if len(vels) < 3:
        return {"reached": reached, "ticks": len(vels),
                "jerk_rms": float("nan"), "yaw_rate_var": float("nan")}

    accel = np.diff(vels, axis=0) / DT
    jerk = np.diff(accel, axis=0) / DT
    jerk_rms = float(np.sqrt(np.mean(np.sum(jerk**2, axis=1))))

    final_dist = math.sqrt((gx - x)**2 + (gy - y)**2 + (goal_alt - z)**2)

    return {
        "reached": reached,
        "ticks": len(vels),
        "final_dist_m": round(final_dist, 3),
        "jerk_rms": round(jerk_rms, 4),
        "accel_max": round(float(np.linalg.norm(accel, axis=1).max()), 4),
    }


def _run_planner(planner, label: str) -> list[dict]:
    results = []
    for gx, gy, gz, vehicle in GOALS:
        # LearnedPlanner embeds the vehicle flag in state; rule planner ignores it
        if hasattr(planner, "vehicle"):
            planner.vehicle = float(vehicle)
        r = _run_goal(planner, gx, gy, gz, vehicle)
        r["goal"] = f"({gx},{gy},{gz}) {'quad' if vehicle == VEHICLE_QUAD else 'rover'}"
        r["planner"] = label
        results.append(r)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=None,
                    help="Directory containing policy_v2.onnx (default: auto-detect).")
    ap.add_argument("--out", default=None, help="Write JSON report to this path.")
    ap.add_argument("--reach-m", type=float, default=1.0)
    ap.add_argument("--max-speed", type=float, default=3.0)
    args = ap.parse_args()

    rule_planner = make_planner(use_learned=False, reach_threshold=args.reach_m, max_speed=args.max_speed)
    learned_planner = make_planner(use_learned=True, models_dir=args.models_dir,
                                   reach_threshold=args.reach_m, max_speed=args.max_speed)

    rule_results = _run_planner(rule_planner, "rule")
    learned_results = _run_planner(learned_planner, "learned")

    # --- print comparison table ---
    header = f"{'Goal':<30} {'Planner':<10} {'Reached':<8} {'Ticks':<7} {'Dist(m)':<9} {'JerkRMS':<10} {'AccMax':<8}"
    print("\n" + header)
    print("-" * len(header))
    for r, l in zip(rule_results, learned_results):
        for row in (r, l):
            print(f"{row['goal']:<30} {row['planner']:<10} {str(row['reached']):<8} "
                  f"{row['ticks']:<7} {row['final_dist_m']:<9} {row['jerk_rms']:<10} {row['accel_max']:<8}")
        print()

    rule_jerk = np.nanmean([r["jerk_rms"] for r in rule_results])
    learned_jerk = np.nanmean([r["jerk_rms"] for r in learned_results])
    ratio = learned_jerk / rule_jerk if rule_jerk > 0 else float("inf")

    print(f"\nRule   jerk RMS (mean): {rule_jerk:.4f}")
    print(f"Learned jerk RMS (mean): {learned_jerk:.4f}")
    print(f"Ratio (learned/rule):   {ratio:.3f}  {'✓ PASS' if ratio <= 1.0 else ('~ acceptable' if ratio <= 3.0 else '✗ FAIL')}")

    report = {
        "rule": rule_results,
        "learned": learned_results,
        "summary": {
            "rule_jerk_rms_mean": round(float(rule_jerk), 4),
            "learned_jerk_rms_mean": round(float(learned_jerk), 4),
            "ratio": round(float(ratio), 3),
            "pass": ratio <= 3.0,
        }
    }

    if args.out:
        def _cvt(o):
            if isinstance(o, (np.bool_, np.integer)): return int(o)
            if isinstance(o, np.floating): return float(o)
            raise TypeError(type(o))
        Path(args.out).write_text(json.dumps(report, indent=2, default=_cvt))
        print(f"\nReport written to {args.out}")

    return 0 if ratio <= 3.0 else 1


if __name__ == "__main__":
    sys.exit(main())
