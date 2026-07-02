"""Stage 5 gate: fly the L5 controller through ArduPilot SITL (the real autopilot).

Unlike sitl_validate.py (single-drone LearnedPlanner on toy courses), this drives the
*L5 reactive_goto_controller* — the one validated to L5 / 99.5%-distributional in the
kinematic sim — through ArduPilot's own GUIDED controllers, so it exercises the real
actuator dynamics, latency, and control loops instead of our kinematic integrator.

One vehicle per run (SITL is single-vehicle): pick an agent from a scenario, feed it
that scenario's obstacles. Sensing is reused verbatim from the sim (TeamWorld.observe)
so the controller sees exactly the observation shape it expects; only the *dynamics*
come from ArduPilot. Scores reach / collision / time against the scenario geometry.

Requires a built SITL binary (ArduCopter for quads, ArduRover for rovers) + pymavlink.

    ~/isaac-sim-env/bin/python3 -m eco.drone.training.sitl_l5 \
        --scenario eco/drone/sim/scenarios/dense_urban.yaml --agent quad_0 \
        --sitl-bin ~/ardupilot/build/sitl/bin/arducopter \
        --defaults ~/ardupilot/Tools/autotest/default_params/copter.parm
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

_repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_repo))

from eco.drone.sim.team_world import TeamWorld, KinematicWorld, Box  # noqa: E402
from eco.drone.sim.team_world import reactive_goto_controller           # noqa: E402
from eco.drone.sim.vehicle_class import Kinematics                      # noqa: E402
from eco.drone.sim.scenario import Scenario                             # noqa: E402

DT = 0.1
REACH = 1.5            # m (SITL position tracking is looser than kinematic)
TAKEOFF_ALT = 5.0      # quads cruise at z=5 in the scenarios
MAX_SECONDS = 90.0


# ---------------------------------------------------------------- SITL / MAVLink plumbing
def launch_sitl(sitl_bin, defaults, model="+", home="37.0,-122.0,0,0"):
    cmd = [os.path.expanduser(sitl_bin), "-S", "--model", model, "--speedup", "1",
           "-I0", "--home", home]
    if defaults:
        cmd += ["--defaults", os.path.expanduser(defaults)]
    print("launching SITL:", " ".join(cmd), flush=True)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def connect(addr):
    from pymavlink import mavutil
    print(f"connecting {addr} ...", flush=True)
    m = mavutil.mavlink_connection(addr)
    m.wait_heartbeat()
    print(f"heartbeat sys {m.target_system} comp {m.target_component}", flush=True)
    m.mav.request_data_stream_send(m.target_system, m.target_component,
                                   mavutil.mavlink.MAV_DATA_STREAM_ALL, 25, 1)
    return m


_STATE = {"x": None, "y": None, "alt": None, "yaw": None}


def pump_state(m):
    """world x=fwd(north), y=left(-east), alt=up(-z), yaw (CCW+)."""
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
    t0 = time.time()
    while time.time() - t0 < timeout:
        msg = m.recv_match(type="EKF_STATUS_REPORT", blocking=True, timeout=2)
        if msg and (msg.flags & 0x1F) >= 0x0F:
            return True
    return True


def _set_mode(m, mode):
    m.set_mode(m.mode_mapping()[mode])


def arm_and_takeoff(m, alt, is_rover):
    from pymavlink import mavutil
    _set_mode(m, "GUIDED")
    time.sleep(1)
    m.arducopter_arm(); m.motors_armed_wait()
    print("armed", flush=True)
    if is_rover:
        return   # rovers don't take off
    m.mav.command_long_send(m.target_system, m.target_component,
                            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, 0, 0, 0, 0, alt)
    t0 = time.time()
    while time.time() - t0 < 25:
        msg = m.recv_match(type="LOCAL_POSITION_NED", blocking=True, timeout=1)
        if msg and -msg.z >= alt * 0.95:
            break
    print(f"takeoff ~{alt}m", flush=True)


def send_cmd(m, action, is_rover, yaw):
    """L5 action → MAVLink velocity setpoint.

    Quad (holonomic): body-frame velocity [vx,vy,vz,yaw_rate]; body (fwd,left,up) →
    MAVLink body-NED (fwd,right,down).
    Rover (nonholonomic): ArduRover GUIDED steers to follow a *ground-velocity vector*,
    not body-forward+yaw_rate (which just drove it straight into a wall). Convert the
    [speed, yaw_rate] action to a world-heading velocity vector — heading ≈ yaw +
    yaw_rate/2 (yaw_cmd = clip(desired*2)) — and send it in LOCAL_NED."""
    from pymavlink import mavutil
    if is_rover:
        speed, yaw_rate = float(action[0]), float(action[1])
        hd = yaw + 0.5 * yaw_rate            # desired world heading
        vx_w, vy_w = speed * math.cos(hd), speed * math.sin(hd)   # world fwd(N), left
        # world (fwd=N, left) → NED (north, east=-left)
        m.mav.set_position_target_local_ned_send(
            0, m.target_system, m.target_component,
            mavutil.mavlink.MAV_FRAME_LOCAL_NED, 0b0000111111000111,
            0, 0, 0, vx_w, -vy_w, 0, 0, 0, 0, 0, 0)
        return
    vx, vy, vz, yaw_rate = (float(action[0]), float(action[1]),
                            float(action[2]), float(action[3]))
    type_mask = 0b0000011111000111  # velocities + yaw_rate
    m.mav.set_position_target_local_ned_send(
        0, m.target_system, m.target_component,
        mavutil.mavlink.MAV_FRAME_BODY_NED, type_mask,
        0, 0, 0, vx, -vy, -vz, 0, 0, 0, 0, -yaw_rate)


# ---------------------------------------------------------------- run one scenario agent
def run(m, world, agent, goal, is_rover):
    ctl = reactive_goto_controller()
    boxes = world.backend.obstacles()
    t0 = time.time()
    min_clear = 1e9
    last_dbg = 0.0
    gx, gy, gz = goal
    while time.time() - t0 < MAX_SECONDS:
        st = pump_state(m)
        if st is None:
            time.sleep(0.02); continue
        x, y, alt, yaw = st
        agent.pos = np.array([x, y, 0.0 if is_rover else alt], dtype=np.float32)
        agent.yaw = float(yaw)
        clr = world._min_surface_dist(agent, boxes)
        min_clear = min(min_clear, clr)
        if clr < agent.vclass.radius_m:
            return {"reached": False, "collided": True,
                    "t": round(time.time() - t0, 1), "min_clear": round(min_clear, 2)}
        d = math.sqrt((gx - x) ** 2 + (gy - y) ** 2 + (gz - agent.pos[2]) ** 2)
        if d < REACH:
            return {"reached": True, "collided": False,
                    "t": round(time.time() - t0, 1), "min_clear": round(min_clear, 2)}
        obs = world.observe(agent)
        action = ctl(agent, obs)
        send_cmd(m, action, is_rover, float(yaw))
        if time.time() - last_dbg > 2.0:
            last_dbg = time.time()
            print(f"    t={time.time()-t0:4.1f} pos=({x:5.1f},{y:5.1f},{agent.pos[2]:4.1f}) "
                  f"d={d:4.1f} clr={clr:4.2f} act={np.round(action,2)}", flush=True)
        time.sleep(DT)
    return {"reached": False, "collided": False,
            "t": round(MAX_SECONDS, 1), "min_clear": round(min_clear, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--agent", required=True, help="agent id from the scenario roster")
    ap.add_argument("--sitl-bin", default=None)
    ap.add_argument("--defaults", default=None)
    ap.add_argument("--connect", default="tcp:127.0.0.1:5760")
    args = ap.parse_args()

    scn = Scenario.from_yaml(args.scenario)
    spec = next(a for a in scn.team if a["id"] == args.agent)
    # SITL's local origin (0,0) is the takeoff point, so shift the whole scenario frame
    # so this agent's start maps to (0,0). Obstacles/goal become start-relative; the
    # SITL LOCAL_POSITION_NED pose then lines up directly with the shifted world.
    sx, sy = float(spec["pos"][0]), float(spec["pos"][1])
    goal = [float(spec["goal"][0]) - sx, float(spec["goal"][1]) - sy, float(spec["goal"][2])]
    boxes = [Box(np.array([o[0] - sx, o[1] - sy, o[2]], np.float32),
                 np.asarray(o[3:], np.float32)) for o in scn.obstacles]
    world = TeamWorld(backend=KinematicWorld(boxes))
    agent = world.add_agent(spec["id"], spec["type"], [0.0, 0.0, float(spec["pos"][2])],
                            yaw=spec.get("yaw", 0.0), goal=goal)
    is_rover = agent.vclass.kinematics is Kinematics.UNICYCLE_2D
    print(f"scenario={scn.name} agent={args.agent} class={agent.vclass.name} "
          f"goal={goal} obstacles={len(scn.obstacles)} rover={is_rover}", flush=True)

    model = "rover" if is_rover else "+"
    proc = launch_sitl(args.sitl_bin, args.defaults, model=model) if args.sitl_bin else None
    if proc:
        time.sleep(8)
    try:
        m = connect(args.connect)
        wait_ready(m)
        arm_and_takeoff(m, TAKEOFF_ALT, is_rover)
        res = run(m, world, agent, goal, is_rover)
        print(f"\nSITL-L5 {scn.name}/{args.agent}: {res}", flush=True)
        ok = res["reached"] and not res["collided"]
        print(f"{'PASS' if ok else 'FAIL'}", flush=True)
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
