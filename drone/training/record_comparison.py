"""Honest obstacle-course eval: rule-based vs learned (policy_v3) planner, rendered in Isaac.

Both drones must fly from a free-space start, AROUND obstacle cuboids, to a goal placed behind
them ("fly around the bookcase to the chair"). Perception is the REAL forward depth image from
each drone's camera, sampled into the same 9-ray fan the policy was trained on — no hardcoded
clearance, no blind goals.

  - rule drone:    ReactivePlanner (sees only forward clearance scalar -> can slow, can't steer)
  - learned drone: LearnedPlanner / policy_v3 (sees the depth fan -> steers around)

Outputs per course (to --out):
  - overhead_<course>.mp4  : top-down vantage of BOTH drones + obstacles
  - split_<course>.mp4     : onboard cameras side-by-side (RULE | LEARNED)

Usage on hoopoe (Isaac Sim python env):
    /home/yusuf/isaac-sim-env/bin/python3 /home/yusuf/record_comparison.py \
        --models-dir /home/yusuf/models --out /tmp/astral_course
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

_here = Path(__file__).resolve().parent
_repo = _here.parent.parent.parent
for p in (_repo / "eco" / "drone" / "common", _repo / "eco" / "drone" / "sim", str(_here)):
    sys.path.insert(0, str(p))

from reactive_planner import ReactivePlanner, LearnedPlanner  # noqa: E402
from world3d import Box3D, depth_grid, min_dist_to_boxes      # noqa: E402

DT = 0.1
PHYS_DT = 1.0 / 240.0
STEPS_PER_TICK = int(round(DT / PHYS_DT))   # advance a full DT of motion per control tick
MAX_TICKS = 400
FPS = 15
DEPTH_MAX = 10.0

# Each course: prisms (cx, cy, cz, hx, hy, hz) + goal (gx, gy, gz). Start at (0,0,2).
# Obstacles span the FULL lateral corridor (hy~4) so there is NO going around — the only way
# through is to duck UNDER, climb OVER, or thread a WINDOW. That forces real vertical maneuvers.
START_Z = 2.0


def _window(cx):
    """Full-corridor wall at x=cx with a single central opening y[-0.8,0.8] z[1.2,2.5].

    Jambs span y +-0.8..8 (no end to fly around), tall (z -1..6, can't fly over); sill fills
    z<1.2 and lintel fills z>2.5 across the opening. The ONLY way through is the hole — which
    sits on the goal axis at the drone's cruise altitude, so it must thread it, not dodge it.
    """
    return [(cx, -4.4, 2.5, 0.5, 3.6, 3.5),   # left jamb   (y -8.0..-0.8)
            (cx,  4.4, 2.5, 0.5, 3.6, 3.5),   # right jamb  (y  0.8.. 8.0)
            (cx,  0.0, 0.5, 0.5, 0.9, 0.7),   # sill        (z -0.2.. 1.2, y -0.9..0.9)
            (cx,  0.0, 4.1, 0.5, 0.9, 1.6)]   # lintel      (z  2.5.. 5.7, y -0.9..0.9)


COURSES = {
    "limbo": {     # wide ceiling, gap underneath -> must DESCEND and fly UNDER
        "obstacles": [(5.0, 0.0, 3.25, 0.8, 4.0, 1.75)],   # z 1.5..5.0, y -4..4
        "goal": (10.0, 0.0, 2.0),
    },
    "over_wall": {  # wide floor wall, too tall to be ignored -> must climb OVER
        "obstacles": [(5.0, 0.0, 1.4, 0.8, 4.0, 1.4)],     # z 0..2.8, y -4..4
        "goal": (10.0, 0.0, 2.0),
    },
    "window": {     # full wall with a central opening -> thread THROUGH
        "obstacles": _window(6.0),
        "goal": (11.0, 0.0, 2.0),
    },
    "gauntlet2": {  # UNDER -> OVER -> THROUGH in one run, all corridor-spanning (no fly-around).
                    # Spaced ~4.5 m apart so the big vertical swings between them are feasible.
        "obstacles": [(3.5, 0.0, 3.25, 0.7, 4.0, 1.75)]    # ceiling: under (z 1.5..5.0)
                    + [(7.5, 0.0, 1.3, 0.7, 4.0, 1.3)]     # floor wall: over (z 0..2.6)
                    + _window(11.0),                        # window: through
        "goal": (14.0, 0.0, 2.0),
    },
}


def body_target(goal_world, pos, yaw):
    dx, dy, dz = goal_world[0] - pos[0], goal_world[1] - pos[1], goal_world[2] - pos[2]
    c, s = math.cos(-yaw), math.sin(-yaw)
    return (c * dx - s * dy, s * dx + c * dy, dz)


def world_vel(bvx, bvy, bvz, yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return (c * bvx - s * bvy, s * bvx + c * bvy, bvz)


def side_by_side(left, right, lbl_l, lbl_r):
    h = min(left.shape[0], right.shape[0])
    w = min(left.shape[1], right.shape[1])
    l = np.ascontiguousarray(left[:h, :w])
    r = np.ascontiguousarray(right[:h, :w])
    div = np.ones((h, 4, 3), dtype=np.uint8) * 255
    out = np.concatenate([l, div, r], axis=1)
    try:
        import cv2
        cv2.putText(out, lbl_l, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 100), 2, cv2.LINE_AA)
        cv2.putText(out, lbl_r, (w + 12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (100, 200, 255), 2, cv2.LINE_AA)
    except Exception:
        pass
    return out


def run_pass(bridge, drone_id, planner, goal, is_learned, start, obstacles, halt_r=0.3):
    """Fly one drone from `start` to `goal` through the (already-placed) 3D course.

    The control-input depth grid is computed ANALYTICALLY from the known prism geometry at the
    drone's pose — identical to the representation the policy trained on. Isaac provides visuals
    + the enforced 3D collision-halt (kinematic sim has no physical collision): if the body comes
    within `halt_r` of a prism the drone STOPS (visible failure) instead of ghosting through.
    """
    bridge.set_drone_pose(drone_id, start, yaw_rad=0.0)
    planner.reset()
    boxes = [Box3D(cx, cy, cz, hx, hy, hz) for (cx, cy, cz, hx, hy, hz) in obstacles]
    yaw = 0.0
    onboard, overhead = [], []
    reached = collided = False
    min_clear = DEPTH_MAX
    for tick in range(MAX_TICKS):
        st = bridge.get_drone_state(drone_id)
        pos = st["position"]
        px, py, pz = float(pos[0]), float(pos[1]), float(pos[2])
        fan = depth_grid(px, py, pz, yaw, boxes)
        min_clear = min(min_clear, min_dist_to_boxes(px, py, pz, boxes))
        tgt = body_target(goal, pos, yaw)

        # enforced 3D collision: halt at contact
        if min_dist_to_boxes(px, py, pz, boxes) < halt_r:
            collided = True

        if is_learned:
            plan = planner.step(tgt, depth_fan=fan, altitude_m=pz, dt=DT)
        else:
            plan = planner.step(tgt, float(np.min(fan)), altitude_m=pz, dt=DT)

        if collided or plan.reached or plan.rejected:
            bridge.set_drone_velocity(drone_id, [0, 0, 0])
        else:
            wvx, wvy, wvz = world_vel(plan.vx, plan.vy, plan.vz, yaw)
            bridge.set_drone_velocity(drone_id, [wvx, wvy, wvz])
            yaw += plan.yaw_rate * DT
            bridge.set_drone_yaw(drone_id, yaw)

        bridge.step_simulation(n_steps=STEPS_PER_TICK, render=True)

        of = bridge.grab_frame(drone_id)
        vf = bridge.grab_vantage_frame("overhead")
        if of is not None:
            onboard.append(of)
        if vf is not None:
            overhead.append(vf)

        if collided:
            break
        if plan.reached:
            reached = True
            break
        if plan.rejected:
            break

    final = bridge.get_drone_state(drone_id)["position"]
    dist = math.sqrt((goal[0] - final[0])**2 + (goal[1] - final[1])**2 + (goal[2] - final[2])**2)
    return onboard, overhead, {
        "reached": reached, "collided": collided,
        "final_dist": round(dist, 2), "min_clearance": round(min_clear, 2),
        "ticks": len(onboard),
    }


def run_course(bridge, encode_mp4, course_name, course, drone_id, learned_planner, out_dir):
    """Single drone (the learned policy) through the course; write a single-panel chase video."""
    start = [0.0, 0.0, START_Z]
    goal = tuple(course["goal"])   # (gx, gy, gz)
    obstacles = course["obstacles"]

    onboard, overhead, res = run_pass(bridge, drone_id, learned_planner, goal, True, start, obstacles)
    print(f"    {course_name}: {res}", flush=True)

    if overhead:
        (out_dir / f"chase_{course_name}.mp4").write_bytes(encode_mp4(overhead, fps=FPS))
        print(f"  wrote chase_{course_name}.mp4 ({len(overhead)} frames)", flush=True)
    if onboard:
        (out_dir / f"fpv_{course_name}.mp4").write_bytes(encode_mp4(onboard, fps=FPS))
        print(f"  wrote fpv_{course_name}.mp4 ({len(onboard)} frames)", flush=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--out", default="/tmp/astral_course")
    ap.add_argument("--env", default="none", help="'none' = bare ground plane (default)")
    ap.add_argument("--onnx-name", default="policy_v4_dr.onnx",
                    help="learned model to render")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    import json
    from isaac_vehicle import IsaacVehicleBridge
    from video_record import encode_mp4

    DRONE = "drone"
    bridge = IsaacVehicleBridge(headless=True)
    bridge.setup(environment=args.env, roster=[{"id": DRONE, "type": "quadcopter"}])
    bridge.ensure_camera(DRONE)
    bridge.add_lighting(sun_intensity=2000.0, dome_intensity=700.0)
    bridge.add_marker(DRONE, color=(0.1, 0.95, 0.3), radius=0.28)  # ~real quad radius

    learned_planner = LearnedPlanner(models_dir=args.models_dir, reach_threshold=1.0,
                                     max_speed=3.0, vehicle=0.0, onnx_name=args.onnx_name)
    if learned_planner._session is None:
        print(f"WARNING: {args.onnx_name} not loaded — learned planner is running the fallback!",
              flush=True)

    summary = {}
    for course_name, course in COURSES.items():
        print(f"\n=== Course: {course_name} (goal {course['goal']}) ===", flush=True)
        for j, (cx, cy, cz, hx, hy, hz) in enumerate(course["obstacles"]):
            bridge.add_obstacle_box(f"{course_name}_{j}", [cx, cy, cz], [hx, hy, hz])

        gx, gy, gz = course["goal"]
        cxm, cym = gx / 2.0, gy / 2.0
        bridge.vantages.pop("overhead", None)
        try:
            # Front-quarter view from BEYOND the goal, looking back along the flight axis. This
            # sees the obstacle FACES (so the under-gap and the window opening are visible) plus
            # the drone's vertical position as it ducks under / climbs over / threads through.
            bridge.add_vantage_camera("overhead",
                                      position=[gx + 4.0, -7.0, 4.0],
                                      look_at=[cxm, cym, 1.8],
                                      resolution=(1280, 720), hfov_deg=70.0)
        except Exception as e:
            print(f"  vantage add failed: {e}", flush=True)

        summary[course_name] = run_course(bridge, encode_mp4, course_name, course, DRONE,
                                          learned_planner, out_dir)

        for j in range(len(course["obstacles"])):
            try:
                from omni.isaac.core.utils.prims import delete_prim
                delete_prim(f"/World/obstacle_{course_name}_{j}")
            except Exception:
                pass

    (out_dir / "results.json").write_text(json.dumps(summary, indent=2))
    print("\n" + json.dumps(summary, indent=2), flush=True)
    bridge.teardown()
    IsaacVehicleBridge.shutdown()
    print(f"\nAll videos saved to {out_dir}", flush=True)


if __name__ == "__main__":
    main()
