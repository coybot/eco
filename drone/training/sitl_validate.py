"""Stage 3 gate: fly the learned policy through ArduPilot SITL (the real autopilot).

This is the pre-flight validation. The policy emits body-frame velocity setpoints; ArduPilot's
own controllers track them in GUIDED mode — so this exercises the *actual* autopilot dynamics
(not our DR approximation). We feed the policy an analytic depth fan from a known obstacle
course (same representation it trained on) and score reach / collision / time-to-goal.

Requires a built ArduCopter SITL binary (Tools build: build/sitl/bin/arducopter) and pymavlink.

    PYTHONPATH=<repo> /opt/ml/isaac-sim-env/bin/python -m drone.training.sitl_validate \
        --models-dir /home/yusuf/models --onnx-name policy_v3_dr.onnx \
        --sitl-bin ~/ardupilot/build/sitl/bin/arducopter \
        --defaults ~/ardupilot/Tools/autotest/default_params/copter.parm

If a SITL is already running, pass --connect tcp:127.0.0.1:5760 and omit --sitl-bin.
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here.parent / "common"))
sys.path.insert(0, str(_here))

from reactive_planner import LearnedPlanner              # noqa: E402
from world3d import Box3D, depth_grid, min_dist_to_boxes  # noqa: E402

DT = 0.1
REACH = 1.5            # m (SITL position tracking is looser than kinematic)
COLLIDE_R = 0.4
TAKEOFF_ALT = 2.0
MAX_SECONDS = 45.0

# 3D courses (goal includes target altitude); prisms at varied heights force over/under/around
COURSES = {
    "go_over":   ([Box3D(5, 0, 1.0, 1.0, 2.5, 1.0)], (10.0, 0.0, 2.0)),
    "go_under":  ([Box3D(5, 0, 3.2, 1.0, 2.5, 0.8)], (10.0, 0.0, 2.0)),
    "slalom_3d": ([Box3D(3.5, 1.2, 2.0, 0.6, 0.9, 1.2),
                   Box3D(6.0, -1.2, 2.8, 0.6, 0.9, 1.2),
                   Box3D(8.5, 1.0, 1.4, 0.6, 0.9, 1.2)], (12.0, 0.0, 2.0)),
}


def launch_sitl(sitl_bin, defaults):
    """Start the ArduCopter SITL binary; returns the Popen. Listens on tcp:5760."""
    cmd = [os.path.expanduser(sitl_bin), "-S", "--model", "+",
           "--speedup", "1", "-I0", "--home", "37.0,-122.0,0,0"]
    if defaults:
        cmd += ["--defaults", os.path.expanduser(defaults)]
    print("launching SITL:", " ".join(cmd), flush=True)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def connect(addr):
    from pymavlink import mavutil
    print(f"connecting {addr} ...", flush=True)
    m = mavutil.mavlink_connection(addr)
    m.wait_heartbeat()
    print(f"heartbeat from sys {m.target_system} comp {m.target_component}", flush=True)
    # stream position + attitude (and everything) at 25 Hz so get_state always has fresh data
    m.mav.request_data_stream_send(m.target_system, m.target_component,
                                   mavutil.mavlink.MAV_DATA_STREAM_ALL, 25, 1)
    return m


# cached vehicle state, refreshed by draining the MAVLink buffer each tick
_STATE = {"x": None, "y": None, "alt": None, "yaw": None}


def pump_state(m):
    """Drain pending messages, updating the cached pose. Returns (x_fwd, y_left, alt, yaw) or None.

    NED: x north, y east, z down -> our world x fwd(=north), y left(=-east), up alt(=-z).
    """
    while True:
        msg = m.recv_match(type=["LOCAL_POSITION_NED", "ATTITUDE"], blocking=False)
        if msg is None:
            break
        if msg.get_type() == "LOCAL_POSITION_NED":
            _STATE["x"], _STATE["y"], _STATE["alt"] = msg.x, -msg.y, -msg.z
        else:
            _STATE["yaw"] = -msg.yaw
    if _STATE["x"] is None or _STATE["yaw"] is None:
        return None
    return (_STATE["x"], _STATE["y"], _STATE["alt"], _STATE["yaw"])


def wait_ready(m, timeout=60):
    """Wait for EKF/GPS so GUIDED + arm will be accepted."""
    from pymavlink import mavutil
    t0 = time.time()
    while time.time() - t0 < timeout:
        msg = m.recv_match(type="EKF_STATUS_REPORT", blocking=True, timeout=2)
        if msg and (msg.flags & 0x1F) >= 0x0F:  # attitude+velocity+pos horiz/vert
            return True
    return True  # proceed anyway; arming check will gate


def _set_mode(m, mode):
    m.set_mode(m.mode_mapping()[mode])


def arm_and_takeoff(m, alt):
    from pymavlink import mavutil
    _set_mode(m, "GUIDED")
    time.sleep(1)
    m.arducopter_arm()
    m.motors_armed_wait()
    print("armed; taking off", flush=True)
    m.mav.command_long_send(m.target_system, m.target_component,
                            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, 0, 0, 0, 0, alt)
    t0 = time.time()
    while time.time() - t0 < 20:
        msg = m.recv_match(type="LOCAL_POSITION_NED", blocking=True, timeout=1)
        if msg and -msg.z >= alt * 0.95:
            break
    print(f"reached takeoff alt ~{alt} m", flush=True)


def send_body_velocity(m, vx_fwd, vy_left, vz_up, yaw_rate):
    """SET_POSITION_TARGET in body-NED. Policy is fwd/left/up; MAVLink body is fwd/right/down."""
    from pymavlink import mavutil
    type_mask = 0b0000011111000111  # use vx,vy,vz + yaw_rate only
    m.mav.set_position_target_local_ned_send(
        0, m.target_system, m.target_component,
        mavutil.mavlink.MAV_FRAME_BODY_NED, type_mask,
        0, 0, 0,
        vx_fwd, -vy_left, -vz_up,         # left->right, up->down
        0, 0, 0, 0, -yaw_rate)            # yaw_rate sign: CCW(+) policy -> NED yaw is CW(+)


def run_course(m, planner, boxes, goal):
    planner.reset()
    gx, gy, gz = goal
    t0 = time.time()
    minclear = 1e9
    last_dbg = 0.0
    while time.time() - t0 < MAX_SECONDS:
        st = pump_state(m)
        if st is None:
            time.sleep(0.02)
            continue
        x, y, alt, yaw = st
        minclear = min(minclear, min_dist_to_boxes(x, y, alt, boxes))
        if min_dist_to_boxes(x, y, alt, boxes) < COLLIDE_R:
            return {"reached": False, "collided": True, "t": round(time.time() - t0, 1),
                    "min_clear": round(minclear, 2)}
        dx, dy, dz = gx - x, gy - y, gz - alt
        if math.sqrt(dx * dx + dy * dy + dz * dz) < REACH:
            return {"reached": True, "collided": False, "t": round(time.time() - t0, 1),
                    "min_clear": round(minclear, 2)}
        c, s = math.cos(-yaw), math.sin(-yaw)
        tf, tl = c * dx - s * dy, s * dx + c * dy
        fan = depth_grid(x, y, alt, yaw, boxes)
        plan = planner.step((tf, tl, dz), depth_fan=fan, altitude_m=alt, dt=DT)
        send_body_velocity(m, plan.vx, plan.vy, plan.vz, plan.yaw_rate)
        if time.time() - last_dbg > 1.0:
            last_dbg = time.time()
            print(f"    t={time.time()-t0:4.1f} pos=({x:5.1f},{y:5.1f},{alt:4.1f}) yaw={yaw:+.2f} "
                  f"d={math.sqrt(dx*dx+dy*dy+dz*dz):4.1f} fanmin={float(min(fan)):4.1f} "
                  f"cmd=({plan.vx:+.1f},{plan.vy:+.1f},{plan.vz:+.1f},yr={plan.yaw_rate:+.2f}) "
                  f"{plan.reason[:24]}", flush=True)
        time.sleep(DT)
    return {"reached": False, "collided": False, "t": round(MAX_SECONDS, 1),
            "min_clear": round(minclear, 2)}


def goto_origin(m, timeout=15.0):
    """Return to takeoff XY/alt between courses; STREAM the position target and wait for arrival
    (a single setpoint + blind sleep often left the drone short, so the next course started from
    a bad pose and never converged)."""
    from pymavlink import mavutil
    t0 = time.time()
    while time.time() - t0 < timeout:
        # position (x,y,z) + yaw=0 so each course starts facing +x (toward the goal), matching
        # the training distribution; restoring position alone left the drone facing backward.
        m.mav.set_position_target_local_ned_send(
            0, m.target_system, m.target_component,
            mavutil.mavlink.MAV_FRAME_LOCAL_NED, 0b0000101111111000,
            0, 0, -TAKEOFF_ALT, 0, 0, 0, 0, 0, 0, 0.0, 0)
        st = pump_state(m)
        if st is not None:
            x, y, alt, yaw = st
            if math.hypot(x, y) < 1.0 and abs(alt - TAKEOFF_ALT) < 1.0 and abs(yaw) < 0.2:
                break
        time.sleep(0.1)
    print(f"    back at origin: {pump_state(m)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--onnx-name", default="policy_v4_dr.onnx")
    ap.add_argument("--sitl-bin", default=None)
    ap.add_argument("--defaults", default=None)
    ap.add_argument("--connect", default="tcp:127.0.0.1:5760")
    ap.add_argument("--only", default=None, help="run only this course (debug)")
    args = ap.parse_args()
    courses = {args.only: COURSES[args.only]} if args.only else COURSES

    proc = None
    if args.sitl_bin:
        proc = launch_sitl(args.sitl_bin, args.defaults)
        time.sleep(8)
    try:
        m = connect(args.connect)
        wait_ready(m)
        arm_and_takeoff(m, TAKEOFF_ALT)
        planner = LearnedPlanner(models_dir=args.models_dir, reach_threshold=1.0,
                                 max_speed=3.0, vehicle=0.0, onnx_name=args.onnx_name)
        print("policy loaded:", planner._session is not None, flush=True)
        results = {}
        for name, (boxes, goal) in courses.items():
            goto_origin(m)
            results[name] = run_course(m, planner, boxes, goal)
            print(f"{name}: {results[name]}", flush=True)
        print("\nSITL gate results:")
        for k, v in results.items():
            print(f"  {k:14s} {v}")
        ok = sum(v["reached"] and not v["collided"] for v in results.values())
        print(f"\nPASSED {ok}/{len(results)} courses in ArduPilot SITL")
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
