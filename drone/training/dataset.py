"""Generate behavioral-cloning data with a PRIVILEGED A* path planner as the teacher.

The teacher knows the obstacle geometry, plans a genuinely collision-free 3D path (over / under
/ around / through a hole), and follows it smoothly. Each tick records (depth-grid state ->
smooth velocity action). The policy only ever sees the depth grid, so it learns to reproduce
planner-quality behavior from local perception.

Goals lie along +x with lateral/vertical offset, so axis-aligned wall/window obstacles sit
perpendicular to the path; initial heading is randomized (turn-then-go robustness). Obstacle
modes: scattered prisms, wide over/under barriers, and walls with a window opening.

Run:  python -m eco.drone.training.dataset --episodes 6000 --out ~/drone-data/bc_v4
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .contract import (
    STATE_DIM, ACTION_DIM, STATE_FIELDS, ACTION_FIELDS,
    VEHICLE_QUAD, VEHICLE_ROVER, build_state, wrap_pi, DEPTH_MAX,
)
from .expert import make_expert
from .world3d import Box3D, depth_grid, min_dist_to_boxes, window_prisms
from .planner3d import astar_path, PathFollower


# Deploy altitude cap (run_prompt.py --max-alt). The teacher and policy must operate within it:
# 'over' walls are climbable below it, 'under' ceilings exceed it (forcing a duck), goals sit
# under it. Training above the cap taught v6 to "fly over everything" — impossible on the vehicle.
ALT_CAP = 4.0


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _place_obstacles(gx, z0, goal_alt, vehicle, rng):
    """Obstacles along the +x path. Quad: scattered / wide barrier (over|under) / window wall.
    Rover: ground prisms only (planar -> around)."""
    if rng.random() < 0.12:
        return []
    band_lo, band_hi = min(z0, goal_alt), max(z0, goal_alt)
    mode = rng.random()

    # HY=9: corridor walls wider than the depth FOV can see around at the approach range, so the
    # A* oracle routes OVER/UNDER/THROUGH (not around) and the cloned policy learns to commit to
    # the vertical maneuver. With HY=4 the 8m walls were skirtable, so the policy learned to skirt
    # — a dead-end in a gauntlet where the next wall is full-corridor.
    HY = 9.0

    def _wall(cx, kind):
        """One corridor-spanning obstacle at x=cx: 'over' floor wall, 'under' ceiling, or 'window'.

        Geometry respects ALT_CAP (deploy max_alt): 'over' walls top out BELOW the cap so they can be
        climbed within it; 'under' ceilings extend ABOVE the cap so flying over isn't an option and
        the drone must duck under (the deploy-faithful constraint v6 violated by flying high)."""
        if kind == "window":
            cyo = rng.uniform(-1.0, 1.0)
            czo = float(np.clip(rng.uniform(band_lo - 0.4, band_hi + 0.4), 1.4, ALT_CAP - 0.7))
            return window_prisms(cx, cyo, czo, ohy=rng.uniform(0.95, 1.3),
                                 ohz=rng.uniform(0.85, 1.05), span=2 * HY)
        if kind == "over":
            top = min(band_hi + rng.uniform(0.4, 0.8), ALT_CAP - 0.3)   # climbable under the cap
            return [Box3D(cx, 0.0, top / 2, 0.6, HY, top / 2)]
        bottom = rng.uniform(1.1, 1.6)
        top = ALT_CAP + 1.5                                             # above cap -> must duck under
        return [Box3D(cx, 0.0, (bottom + top) / 2, 0.6, HY, (top - bottom) / 2)]

    if vehicle == VEHICLE_QUAD and mode < 0.38:               # SEQUENCE: chained corridor walls
        # Heavy direct exposure to the gauntlet maneuver: 65% are the exact under->over->through
        # chain (the oracle demonstrates the correct ALTERNATING altitude — duck low, climb high,
        # thread — which a constant altitude bias can't teach). Tight, evenly-spaced like gauntlet2.
        if rng.random() < 0.65:
            seq = ["under", "over", "window"]                 # the hard gauntlet pattern
            fracs = np.array([0.25, 0.52, 0.78]) + rng.uniform(-0.04, 0.04, 3)
        else:
            kinds = ["over", "under", "window"]
            seq = [kinds[int(rng.integers(0, 3))] for _ in range(int(rng.integers(2, 4)))]
            fracs = np.sort(rng.uniform(0.22, 0.82, len(seq)))
        boxes = []
        for frac, kind in zip(fracs, seq):
            boxes += _wall(frac * gx, kind)
        return boxes

    if vehicle == VEHICLE_QUAD and mode < 0.54:               # WINDOW wall (thread through)
        return _wall(rng.uniform(0.4, 0.65) * gx, "window")

    if vehicle == VEHICLE_QUAD and mode < 0.72:               # wide BARRIER (over or under)
        return _wall(rng.uniform(0.4, 0.65) * gx, "over" if rng.random() < 0.5 else "under")

    boxes = []                                                # scattered prisms (bob & weave)
    for _ in range(int(rng.integers(1, 5))):
        cx = rng.uniform(0.25, 0.85) * gx
        cy = rng.uniform(-2.0, 2.0)
        if vehicle == VEHICLE_QUAD:
            cz = max(0.4, rng.uniform(band_lo - 1.0, band_hi + 1.0))
            hz = rng.uniform(0.3, 1.2)
        else:
            cz, hz = 0.0, 1.5
        boxes.append(Box3D(cx, cy, cz, rng.uniform(0.3, 1.0), rng.uniform(0.3, 1.2), hz))
    return boxes


def rollout_episode(vehicle, rng, dt=0.1, max_ticks=700, limits=None):
    expert = make_expert(vehicle, limits)
    lim = expert.lim
    planar = (vehicle == VEHICLE_ROVER)

    gx = rng.uniform(6.0, 15.0)                               # goal along +x
    gy = rng.uniform(-3.0, 3.0)
    # start/goal altitudes kept under ALT_CAP so over-walls + goals are reachable within the cap
    z0 = rng.uniform(1.0, 2.2) if vehicle == VEHICLE_QUAD else 0.5
    gz = rng.uniform(-1.0, 0.9) if vehicle == VEHICLE_QUAD else 0.0
    goal_alt = float(np.clip(z0 + gz, 0.6, ALT_CAP - 0.4)) if vehicle == VEHICLE_QUAD else 0.5

    # DECORRELATE altitude from the over/under choice (matches the RL env): with some probability,
    # force a single floor-wall approached from a LOW start (must climb OVER from low) or a ceiling
    # from a HIGH start (must duck UNDER from high). The oracle demonstrates the correct maneuver, so
    # the cloned policy stops using current altitude as a shortcut for over-vs-under (the gauntlet
    # failure: forced low by a ceiling, it then wouldn't climb the next floor-wall).
    decorr = rng.random() if vehicle == VEHICLE_QUAD else 1.0
    if decorr < 0.16:                                         # over-from-LOW
        z0 = rng.uniform(0.7, 1.1)
        goal_alt = float(np.clip(rng.uniform(1.6, 2.6), 0.6, ALT_CAP - 0.4))
        cx = rng.uniform(0.4, 0.65) * gx
        top = min(max(z0, goal_alt) + rng.uniform(0.4, 0.9), ALT_CAP - 0.3)
        boxes = [Box3D(cx, 0.0, top / 2, 0.6, 9.0, top / 2)]
    elif decorr < 0.30:                                       # under-from-HIGH
        z0 = rng.uniform(2.6, ALT_CAP - 0.2)
        goal_alt = float(np.clip(rng.uniform(0.8, 1.8), 0.6, ALT_CAP - 0.4))
        cx = rng.uniform(0.4, 0.65) * gx
        bottom = rng.uniform(1.1, 1.6)
        boxes = [Box3D(cx, 0.0, (bottom + ALT_CAP + 1.5) / 2, 0.6, 9.0, (ALT_CAP + 1.5 - bottom) / 2)]
    else:
        boxes = _place_obstacles(gx, z0, goal_alt, vehicle, rng)
    start = (0.0, 0.0, z0)
    goal = (gx, gy, goal_alt)
    if (min_dist_to_boxes(*start, boxes) < 0.8 or min_dist_to_boxes(*goal, boxes) < 0.8):
        boxes = []

    # privileged A* path; if blocked, skip this episode (caller yields nothing).
    # zmax=ALT_CAP for the quad so the teacher DUCKS UNDER tall ceilings instead of routing over
    # them (matches the deploy altitude cap; otherwise BC would teach the fly-high cheat).
    path = astar_path(start, goal, boxes, inflate=(0.55 if vehicle == VEHICLE_QUAD else 0.5),
                      zmin=(0.4 if vehicle == VEHICLE_QUAD else 0.45),
                      zmax=(ALT_CAP if vehicle == VEHICLE_QUAD else 6.0))
    if path is None:
        return
    follower = PathFollower(path, lookahead=0.9)

    # heading mix: 60% roughly facing the goal (+x), 40% any direction (turn-then-go)
    yaw = rng.uniform(-0.6, 0.6) if rng.random() < 0.6 else rng.uniform(-math.pi, math.pi)
    x = y = 0.0
    z = z0
    vx_w = vy_w = vz_w = 0.0
    collide_r = 0.18 if vehicle == VEHICLE_QUAD else 0.35   # truncate only on real penetration
    explore_sigma = float(rng.choice([0.0, 0.3, 0.6]))

    for _ in range(max_ticks):
        dx, dy, dz = gx - x, gy - y, goal_alt - z
        c, s = math.cos(-yaw), math.sin(-yaw)
        tf, tl, tu = c * dx - s * dy, s * dx + c * dy, dz
        dist = math.sqrt(tf * tf + tl * tl + tu * tu)
        bvf, bvl, bvu = c * vx_w - s * vy_w, s * vx_w + c * vy_w, vz_w

        depth = depth_grid(x, y, z, yaw, boxes, max_range=DEPTH_MAX, noise_std=0.05, rng=rng)
        state = build_state((tf, tl, tu), (bvf, bvl, bvu), expert._prev_yaw_rate,
                            depth_fan=depth, altitude_m=z, vehicle=vehicle)
        expert._prev_vel = np.array([bvf, bvl, bvu], dtype=np.float64)   # DAgger relabel
        action = expert.step_follow((x, y, z), yaw, goal, follower, dt=dt, planar=planar)

        yield state.to_vec(), action, {
            "target_xyz": [tf, tl, tu], "min_clearance_m": float(np.min(depth)),
            "altitude_m": z, "vehicle": vehicle,
        }

        avx, avy, avz, ayr = (float(a) for a in action)
        ex_vx = avx + float(rng.normal(0.0, explore_sigma))
        ex_vy = avy + (float(rng.normal(0.0, explore_sigma)) if not planar else 0.0)
        ex_vz = avz + (float(rng.normal(0.0, explore_sigma)) if vehicle == VEHICLE_QUAD else 0.0)
        cc, ss = math.cos(yaw), math.sin(yaw)
        vx_w = cc * ex_vx - ss * ex_vy
        vy_w = ss * ex_vx + cc * ex_vy
        vz_w = ex_vz
        x += vx_w * dt
        y += vy_w * dt
        z = min(ALT_CAP, max(0.2, z + vz_w * dt)) if vehicle == VEHICLE_QUAD else max(0.2, z + vz_w * dt)
        yaw = wrap_pi(yaw + ayr * dt)

        if min_dist_to_boxes(x, y, z, boxes) < collide_r:
            break
        if dist <= lim.reach_threshold and (avx * avx + avy * avy + avz * avz) < 1e-3:
            break


def generate(out_dir, episodes=6000, rover_fraction=0.4, dt=0.1, seed=0, write_jsonl=True):
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    rng = _rng(seed)

    X_list, Y_list, EP_list = [], [], []
    jf = open(out / "plan.jsonl", "w", buffering=1) if write_jsonl else None
    seq = 0
    try:
        for ep in range(episodes):
            vehicle = VEHICLE_ROVER if rng.random() < rover_fraction else VEHICLE_QUAD
            ep_id = f"bc{ep:06d}"
            for svec, action, raw in rollout_episode(vehicle, rng, dt=dt):
                X_list.append(svec)
                Y_list.append(action)
                EP_list.append(ep)
                if jf is not None:
                    seq += 1
                    jf.write(json.dumps({
                        "kind": "plan", "seq": seq, "episode": ep_id,
                        "vehicle_type": "rover" if vehicle == VEHICLE_ROVER else "quad",
                        "source": "sim", "target_xyz": raw["target_xyz"],
                        "min_clearance_m": raw["min_clearance_m"], "altitude_m": raw["altitude_m"],
                        "step": dict(zip(ACTION_FIELDS, [float(a) for a in action])),
                        "outcome": None,
                    }) + "\n")
    finally:
        if jf is not None:
            jf.close()

    X = np.asarray(X_list, dtype=np.float32)
    Y = np.asarray(Y_list, dtype=np.float32)
    EP = np.asarray(EP_list, dtype=np.int32)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < 1e-6] = 1.0
    np.savez(out / "dataset.npz", X=X, Y=Y, ep=EP,
             state_mean=mean.astype(np.float32), state_std=std.astype(np.float32))
    summary = {"episodes": episodes, "samples": int(X.shape[0]),
               "state_dim": STATE_DIM, "action_dim": ACTION_DIM,
               "rover_fraction": rover_fraction, "out": str(out)}
    (out / "dataset_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate A*-oracle BC dataset.")
    ap.add_argument("--out", required=True)
    ap.add_argument("--episodes", type=int, default=6000)
    ap.add_argument("--rover-fraction", type=float, default=0.4)
    ap.add_argument("--dt", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-jsonl", action="store_true")
    args = ap.parse_args()
    print(json.dumps(generate(args.out, episodes=args.episodes, rover_fraction=args.rover_fraction,
                              dt=args.dt, seed=args.seed, write_jsonl=not args.no_jsonl), indent=2))


if __name__ == "__main__":
    main()
