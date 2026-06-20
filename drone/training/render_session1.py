"""Render session-1 demo videos: limbo, over_wall, window — policy_v4_dr.onnx.

Three cameras per course:
  - chase   : front-quarter view from beyond goal, looking back (shows obstacle face + drone arc)
  - side    : pure side-on (reveals vertical travel clearly for limbo/over_wall)
  - fpv     : drone onboard

Run on hoopoe:
  cd ~/astral-training
  /home/yusuf/isaac-sim-env/bin/python3 -m eco.drone.training.render_session1 \
      --out /tmp/session1_videos
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

_here = Path(__file__).resolve().parent
_repo = _here.parent.parent.parent
for p in (
    _repo / "eco" / "drone" / "common",
    _repo / "eco" / "drone" / "sim",
    str(_here),
    # hoopoe: sim helpers live here
    Path("/home/yusuf/code/ishmael/eco_sim"),
):
    sys.path.insert(0, str(p))

from reactive_planner import LearnedPlanner          # noqa: E402
from world3d import Box3D, depth_grid, min_dist_to_boxes  # noqa: E402

DT = 0.1
PHYS_DT = 1.0 / 240.0
STEPS_PER_TICK = int(round(DT / PHYS_DT))
MAX_TICKS = 400
FPS = 24
DEPTH_MAX = 10.0
START_Z = 2.0


def _window(cx):
    return [(cx, -4.4, 2.5, 0.5, 3.6, 3.5),
            (cx,  4.4, 2.5, 0.5, 3.6, 3.5),
            (cx,  0.0, 0.5, 0.5, 0.9, 0.7),
            (cx,  0.0, 4.1, 0.5, 0.9, 1.6)]


# Only the three courses this session solved.
COURSES = {
    "limbo": {
        "obstacles": [(5.0, 0.0, 3.25, 0.8, 4.0, 1.75)],
        "goal": (10.0, 0.0, 2.0),
        "maneuver": "DUCK UNDER ceiling",
    },
    "over_wall": {
        "obstacles": [(5.0, 0.0, 1.4, 0.8, 4.0, 1.4)],
        "goal": (10.0, 0.0, 2.0),
        "maneuver": "CLIMB OVER wall",
    },
    "window": {
        "obstacles": _window(6.0),
        "goal": (11.0, 0.0, 2.0),
        "maneuver": "THREAD THROUGH window",
    },
}

# Obstacle colors: ceiling=slate, floor-wall=brick, window=warm grey
OBS_COLORS = {
    "limbo":    (0.45, 0.55, 0.65),
    "over_wall": (0.70, 0.35, 0.25),
    "window":   (0.60, 0.58, 0.55),
}


def body_target(goal_world, pos, yaw):
    dx, dy, dz = goal_world[0] - pos[0], goal_world[1] - pos[1], goal_world[2] - pos[2]
    c, s = math.cos(-yaw), math.sin(-yaw)
    return (c * dx - s * dy, s * dx + c * dy, dz)


def world_vel(bvx, bvy, bvz, yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return (c * bvx - s * bvy, s * bvx + c * bvy, bvz)


def run_course(bridge, encode_mp4, name, course, drone_id, planner, out_dir):
    start = [0.0, 0.0, START_Z]
    goal = tuple(course["goal"])
    obstacles = course["obstacles"]
    boxes = [Box3D(cx, cy, cz, hx, hy, hz) for (cx, cy, cz, hx, hy, hz) in obstacles]

    bridge.set_drone_pose(drone_id, start, yaw_rad=0.0)
    planner.reset()

    yaw = 0.0
    frames_chase, frames_side, frames_fpv = [], [], []
    reached = collided = False
    min_clear = DEPTH_MAX

    for tick in range(MAX_TICKS):
        st = bridge.get_drone_state(drone_id)
        pos = st["position"]
        px, py, pz = float(pos[0]), float(pos[1]), float(pos[2])
        fan = depth_grid(px, py, pz, yaw, boxes)
        min_clear = min(min_clear, min_dist_to_boxes(px, py, pz, boxes))
        tgt = body_target(goal, pos, yaw)

        if min_dist_to_boxes(px, py, pz, boxes) < 0.3:
            collided = True

        plan = planner.step(tgt, depth_fan=fan, altitude_m=pz, dt=DT)

        if collided or plan.reached or plan.rejected:
            bridge.set_drone_velocity(drone_id, [0, 0, 0])
        else:
            wvx, wvy, wvz = world_vel(plan.vx, plan.vy, plan.vz, yaw)
            bridge.set_drone_velocity(drone_id, [wvx, wvy, wvz])
            yaw += plan.yaw_rate * DT
            bridge.set_drone_yaw(drone_id, yaw)

        bridge.step_simulation(n_steps=STEPS_PER_TICK, render=True)

        f_chase = bridge.grab_vantage_frame("chase")
        f_side  = bridge.grab_vantage_frame("side")
        f_fpv   = bridge.grab_frame(drone_id)
        if f_chase is not None: frames_chase.append(f_chase)
        if f_side is not None:  frames_side.append(f_side)
        if f_fpv is not None:   frames_fpv.append(f_fpv)

        if collided or plan.reached or plan.rejected:
            reached = plan.reached
            break

    final = bridge.get_drone_state(drone_id)["position"]
    dist = math.sqrt(sum((goal[i] - final[i])**2 for i in range(3)))
    result = {"reached": reached, "collided": collided,
              "final_dist": round(dist, 2), "min_clearance_m": round(min_clear, 2),
              "ticks": tick + 1}
    print(f"  {name}: {result}", flush=True)

    if frames_chase:
        path = out_dir / f"chase_{name}.mp4"
        path.write_bytes(encode_mp4(frames_chase, fps=FPS))
        print(f"    wrote {path.name} ({len(frames_chase)} frames)", flush=True)
    if frames_side:
        path = out_dir / f"side_{name}.mp4"
        path.write_bytes(encode_mp4(frames_side, fps=FPS))
        print(f"    wrote {path.name} ({len(frames_side)} frames)", flush=True)
    if frames_fpv:
        path = out_dir / f"fpv_{name}.mp4"
        path.write_bytes(encode_mp4(frames_fpv, fps=FPS))
        print(f"    wrote {path.name} ({len(frames_fpv)} frames)", flush=True)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--out", default="/tmp/session1_videos")
    ap.add_argument("--onnx-name", default="policy_v4_dr.onnx")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    import json
    from isaac_vehicle import IsaacVehicleBridge
    from video_record import encode_mp4

    DRONE = "drone"
    bridge = IsaacVehicleBridge(headless=True)
    bridge.setup(environment="none", roster=[{"id": DRONE, "type": "quadcopter"}])
    bridge.ensure_camera(DRONE)

    # Richer lighting: warm sun from above-left + cooler fill dome
    bridge.add_lighting(sun_intensity=3000.0, dome_intensity=900.0)
    # Vivid marker so the drone pops against any background
    bridge.add_marker(DRONE, color=(0.05, 0.90, 0.40), radius=0.30)

    planner = LearnedPlanner(
        models_dir=args.models_dir or str(Path(args.onnx_name).parent if "/" in args.onnx_name else
                                          Path(__file__).parent.parent / "models"),
        onnx_name=args.onnx_name,
        reach_threshold=1.0,
        max_speed=3.0,
        vehicle=0.0,
    )
    print(f"Loaded: {args.onnx_name}", flush=True)

    summary = {}
    for name, course in COURSES.items():
        print(f"\n=== {name.upper()}: {course['maneuver']} ===", flush=True)
        obs_color = OBS_COLORS[name]
        for j, (cx, cy, cz, hx, hy, hz) in enumerate(course["obstacles"]):
            bridge.add_obstacle_box(f"{name}_{j}", [cx, cy, cz], [hx, hy, hz],
                                    color=obs_color)

        gx, gy, gz = course["goal"]
        midx = gx / 2.0

        # Camera 1: front-quarter chase — from beyond goal, angled back, sees obstacle face
        bridge.vantages.pop("chase", None)
        try:
            bridge.add_vantage_camera("chase",
                                      position=[gx + 5.0, -6.0, 4.5],
                                      look_at=[midx, 0.0, 1.8],
                                      resolution=(1920, 1080), hfov_deg=65.0)
        except Exception as e:
            print(f"  chase cam failed: {e}", flush=True)

        # Camera 2: pure side view — perpendicular to flight axis, reveals altitude arc
        bridge.vantages.pop("side", None)
        try:
            bridge.add_vantage_camera("side",
                                      position=[midx, -9.0, 2.5],
                                      look_at=[midx, 0.0, 2.0],
                                      resolution=(1920, 1080), hfov_deg=55.0)
        except Exception as e:
            print(f"  side cam failed: {e}", flush=True)

        summary[name] = run_course(bridge, encode_mp4, name, course, DRONE, planner, out_dir)

        for j in range(len(course["obstacles"])):
            try:
                from omni.isaac.core.utils.prims import delete_prim
                delete_prim(f"/World/obstacle_{name}_{j}")
            except Exception:
                pass

    (out_dir / "results.json").write_text(json.dumps(summary, indent=2))
    print("\n" + json.dumps(summary, indent=2), flush=True)
    bridge.teardown()
    IsaacVehicleBridge.shutdown()
    print(f"\nAll videos → {out_dir}", flush=True)


if __name__ == "__main__":
    main()
