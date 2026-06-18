#!/usr/bin/env python3
"""Local CLI: submit a 'go to X and come back' prompt over SSH.

Implements the hardened Track A pipeline locally, no cloud dependency:

  prompt -> target noun (target_selector)
         -> GroundingDINO bbox on RGB frame
         -> D435i stereo depth at bbox center (no Depth Anything V2 needed)
         -> EMA spatial memory
         -> Classical reactive planner
         -> velocity command (printed in --dry-run, else sent via MAVLink)

Usage:
  ./venv/bin/python run_prompt.py --dry-run "go to the nearest person and come back"

Options:
  --dry-run        Print velocities/mode-changes, do not send them to the FC.
  --max-iters N    Maximum perception-plan ticks per phase (default 200).
  --tick-hz HZ     Loop frequency (default 5 Hz).
  --reach-m M      Consider target reached when within M meters (default 1.0).
  --no-camera      Skip camera (pure parse + model cold start smoke test).
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

# Make sibling modules importable whether this lives in ~/drone-api or drone/common
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from target_selector import parse as parse_prompt  # noqa: E402
from grounding import GroundingDINO, GDetection  # noqa: E402
from spatial_memory import SpatialMemory  # noqa: E402
from reactive_planner import ReactivePlanner, PlanStep, make_planner  # noqa: E402


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def open_realsense():
    """Open a RealSense D435i pipeline with aligned color+depth. Returns
    (pipeline, align, depth_scale, intrinsics). depth_scale is meters/unit."""
    import pyrealsense2 as rs
    pipe = rs.pipeline()
    cfg = rs.config()
    cfg.enable_stream(rs.stream.color, 640, 480, rs.format.rgb8, 30)
    cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    profile = pipe.start(cfg)
    align = rs.align(rs.stream.color)
    depth_sensor = profile.get_device().first_depth_sensor()
    depth_scale = float(depth_sensor.get_depth_scale())  # usually 0.001
    color_stream = profile.get_stream(rs.stream.color).as_video_stream_profile()
    intr = color_stream.get_intrinsics()
    # Let auto-exposure settle
    for _ in range(15):
        pipe.wait_for_frames(5000)
    return pipe, align, depth_scale, intr


def depth_at(depth_img: np.ndarray, scale: float, x: int, y: int, win: int = 5) -> Optional[float]:
    """Median depth in a small window around (x, y), in meters. None if no valid samples."""
    h, w = depth_img.shape
    x0, x1 = max(0, x - win), min(w, x + win + 1)
    y0, y1 = max(0, y - win), min(h, y + win + 1)
    patch = depth_img[y0:y1, x0:x1]
    vals = patch[patch > 0]  # 0 = no measurement
    if vals.size == 0:
        return None
    return float(np.median(vals)) * scale


def depth_grid_5x9(depth_img: np.ndarray, scale: float, intr, depth_max: float = 10.0) -> np.ndarray:
    """Sample the 5×9 forward depth grid that matches the training state contract.

    Layout: row-major top→bottom, left→right (matches contract.py RAY_DIRS).
    HFOV = 90° (±45°, 9 cols), VFOV = 70° (±35°, 5 rows).
    Body frame: fwd=+Z_cam, left=−X_cam, up=−Y_cam (matches backproject convention).
    Rays outside the D435i FOV return depth_max (no obstacle assumed).
    Returns float32 array of shape (45,) in metres.
    """
    import math as _math
    h_img, w_img = depth_img.shape
    cx, cy, fx, fy = intr.ppx, intr.ppy, intr.fx, intr.fy

    # Training ray angles (top→down, left→right)
    h_angles = [_math.pi / 4 - c * _math.pi / 4 / 4 for c in range(9)]   # +45°..−45°
    v_angles = [_math.pi * 35 / 180 - r * _math.pi * 35 / 180 / 2 for r in range(5)]  # +35°..−35°

    rays = np.empty(45, dtype=np.float32)
    idx = 0
    for va in v_angles:
        for ha in h_angles:
            cos_va = _math.cos(va)
            fwd = cos_va * _math.cos(ha)
            left = cos_va * _math.sin(ha)
            up = _math.sin(va)
            u = int(round(cx + fx * (-left / fwd)))
            v = int(round(cy + fy * (-up / fwd)))
            if 0 <= u < w_img and 0 <= v < h_img:
                u0, u1 = max(0, u - 1), min(w_img, u + 2)
                v0, v1 = max(0, v - 1), min(h_img, v + 2)
                patch = depth_img[v0:v1, u0:u1].ravel()
                valid = patch[patch > 0]
                raw = int(np.median(valid)) if valid.size > 0 else 0
                d_m = float(raw) * scale if raw > 0 else depth_max
            else:
                d_m = depth_max
            rays[idx] = min(d_m, depth_max)
            idx += 1
    return rays


def backproject(x: int, y: int, z_m: float, intr) -> Tuple[float, float, float]:
    """Pinhole backprojection (x,y pixel + z meters -> body-frame X,Y,Z meters).

    Body convention: forward=Z_camera, right=+X_camera, down=+Y_camera (standard OpenCV).
    We return (forward, left, up) for the planner:
      forward = Z
      left    = -X
      up      = -Y
    """
    X = (x - intr.ppx) / intr.fx * z_m
    Y = (y - intr.ppy) / intr.fy * z_m
    Z = z_m
    return (Z, -X, -Y)


def _mavlink_defaults_from_config() -> Tuple[str, int]:
    """Use serial_port / baud_rate from config.yaml next to this file (matches drone_sdk / daemon)."""
    cfg_path = Path(__file__).resolve().parent / "config.yaml"
    port, baud = "/dev/ttyACM0", 115200
    if cfg_path.exists():
        try:
            import yaml
            with open(cfg_path, encoding="utf-8") as f:
                c = yaml.safe_load(f) or {}
            port = str(c.get("serial_port", port))
            baud = int(c.get("baud_rate", baud))
        except Exception:
            pass
    return port, baud


def print_action(args, label: str, payload: str):
    prefix = "[DRY-RUN]" if args.dry_run else "[SEND   ]"
    print(f"{_ts()} {prefix} {label}: {payload}")


def send_velocity(mav, vx: float, vy: float, vz: float, yaw_rate: float, dt: float):
    """Send SET_POSITION_TARGET_LOCAL_NED with velocity only, body frame."""
    from pymavlink import mavutil
    # BODY_OFFSET_NED frame (8); type_mask: ignore position+accel+yaw (use yaw_rate)
    type_mask = (
        0b0000_0111_1100_0111  # ignore pos (x,y,z) + accel (ax,ay,az) + yaw
    )
    mav.mav.set_position_target_local_ned_send(
        0,  # time_boot_ms
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_FRAME_BODY_OFFSET_NED,
        type_mask,
        0, 0, 0,                   # pos (ignored)
        vx, vy, vz,                # vel
        0, 0, 0,                   # accel (ignored)
        0.0, yaw_rate,             # yaw, yaw_rate
    )


def capture_home(mav) -> Optional[dict]:
    """Snapshot GPS + local pose so we can return."""
    msg = mav.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=3)
    if not msg:
        return None
    return {
        "lat": msg.lat / 1e7,
        "lon": msg.lon / 1e7,
        "alt_m": msg.alt / 1000.0,
        "rel_alt_m": msg.relative_alt / 1000.0,
    }


def request_data_streams(mav, rate_hz: int = 4) -> None:
    """Force the FC to stream position/attitude at rate_hz. ArduPilot will sometimes
    stop streaming these on new USB connections until asked."""
    from pymavlink import mavutil
    # REQUEST_DATA_STREAM is deprecated but universally understood by ArduPilot.
    streams = [
        mavutil.mavlink.MAV_DATA_STREAM_POSITION,    # GLOBAL_POSITION_INT, LOCAL_POSITION_NED
        mavutil.mavlink.MAV_DATA_STREAM_EXTRA1,      # ATTITUDE
        mavutil.mavlink.MAV_DATA_STREAM_EXTRA2,      # VFR_HUD (has altitude)
        mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS,  # SYS_STATUS, GPS_RAW_INT
    ]
    for s in streams:
        mav.mav.request_data_stream_send(
            mav.target_system, mav.target_component, s, rate_hz, 1,
        )


def current_rel_alt(mav, timeout: float = 1.0) -> Optional[float]:
    """Altitude above home (meters) from GLOBAL_POSITION_INT.relative_alt.
    Returns None if no fresh message arrives within `timeout`.

    Do NOT use VFR_HUD.alt — on ArduPilot it's MSL absolute, not relative.
    """
    msg = mav.recv_match(type="GLOBAL_POSITION_INT",
                         blocking=True, timeout=timeout)
    if not msg:
        return None
    return msg.relative_alt / 1000.0


def safe_disarm(mav) -> None:
    """Best-effort disarm. Used on abort paths. Does not wait for confirmation."""
    from pymavlink import mavutil
    try:
        mav.mav.command_long_send(
            mav.target_system, mav.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
            0, 0, 0, 0, 0, 0, 0,
        )
        print(f"{_ts()} safe_disarm: DISARM sent.")
    except Exception as e:
        print(f"{_ts()} safe_disarm: failed: {e}")


def emergency_land(mav) -> None:
    """Best-effort LAND. Called on any exit path (signal, exception, normal) while
    the drone may still be armed. LAND = powered controlled descent, not freefall.
    """
    if mav is None:
        return
    try:
        print(f"{_ts()} emergency_land: setting mode LAND")
        mav.set_mode_apm("LAND")
    except Exception as e:
        print(f"{_ts()} emergency_land: set_mode LAND failed: {e}; trying disarm")
        safe_disarm(mav)


def arm_and_takeoff(mav, target_alt_m: float, max_wait_s: float = 20.0) -> bool:
    """Set GUIDED, arm, take off to target_alt_m. Blocks until ~95% of target alt.

    Returns True on success. Assumes ArduCopter. Pre-arm must already pass.
    """
    from pymavlink import mavutil
    print(f"{_ts()} setting mode GUIDED")
    mav.set_mode_apm("GUIDED")
    # Wait for mode change to be acknowledged
    t0 = time.time()
    while time.time() - t0 < 5:
        m = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if m and mavutil.mode_string_v10(m) == "GUIDED":
            break
    print(f"{_ts()} arming...")
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
        1, 0, 0, 0, 0, 0, 0,
    )
    # Wait for armed
    t0 = time.time()
    while time.time() - t0 < 5:
        m = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if m and (m.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
            print(f"{_ts()} armed.")
            break
    else:
        print(f"{_ts()} ARM FAILED (pre-arm check?)")
        return False

    print(f"{_ts()} takeoff to {target_alt_m}m...")
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0,
        0, 0, 0, 0, 0, 0, float(target_alt_m),
    )
    # Wait until we're within 95% of target alt
    t0 = time.time()
    while time.time() - t0 < max_wait_s:
        alt = current_rel_alt(mav, timeout=0.5)
        if alt is None:
            continue
        print(f"{_ts()} rel_alt={alt:.2f}m (target {target_alt_m}m)")
        if alt >= target_alt_m * 0.95:
            print(f"{_ts()} takeoff reached.")
            return True
    print(f"{_ts()} takeoff TIMEOUT at {current_rel_alt(mav) or 'unknown'}m")
    # Drone may be airborne with telemetry not reaching us. LAND for controlled
    # descent at current position (not a freefall — powered let-down).
    print(f"{_ts()} takeoff timed out; setting mode LAND for controlled descent.")
    mav.set_mode_apm("LAND")
    return False


def land(mav, max_wait_s: float = 30.0) -> bool:
    """Set LAND mode and wait for disarm."""
    from pymavlink import mavutil
    print(f"{_ts()} setting mode LAND")
    mav.set_mode_apm("LAND")
    t0 = time.time()
    while time.time() - t0 < max_wait_s:
        m = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if m and not (m.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
            print(f"{_ts()} landed + disarmed.")
            return True
    print(f"{_ts()} land TIMEOUT (still armed)")
    return False


def main():
    default_mav_port, default_mav_baud = _mavlink_defaults_from_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", type=str,
                    help="e.g. 'go to the nearest person and come back'")
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="Print commands instead of sending to FC; no arming, no flight.")
    ap.add_argument("--max-iters", type=int, default=200)
    ap.add_argument("--tick-hz", type=float, default=5.0)
    ap.add_argument("--reach-m", type=float, default=1.0)
    ap.add_argument("--box-threshold", type=float, default=0.30)
    ap.add_argument("--text-threshold", type=float, default=0.25)
    ap.add_argument("--mav-port", type=str, default=default_mav_port,
                    help="MAVLink serial (default: config.yaml serial_port, else /dev/ttyACM0).")
    ap.add_argument("--mav-baud", type=int, default=default_mav_baud,
                    help="MAVLink baud (default: config.yaml baud_rate, else 115200).")
    ap.add_argument("--no-camera", action="store_true",
                    help="Smoke test: parse prompt + cold-start GDINO only, skip camera loop.")
    ap.add_argument("--takeoff-alt", type=float, default=0.0,
                    help="If >0, arm and take off to this altitude (m) before perception.")
    ap.add_argument("--land-at-end", action="store_true",
                    help="LAND + disarm at the end (regardless of come-back in prompt).")
    ap.add_argument("--max-alt", type=float, default=3.0,
                    help="Safety altitude cap (m). vz is zeroed above this.")
    ap.add_argument("--max-speed", type=float, default=1.0,
                    help="Planner max speed (m/s). Default 1.0 for indoor/tight spaces.")
    ap.add_argument("--use-learned-planner", action="store_true",
                    help="Use policy_v2.onnx GRU planner instead of the rule-based planner. "
                         "Falls back silently to rules if onnxruntime or model is unavailable.")
    ap.add_argument("--models-dir", type=str, default=None,
                    help="Path to directory containing policy_v2.onnx (default: auto-detect).")
    args = ap.parse_args()

    # 1. Parse prompt
    parsed = parse_prompt(args.prompt)
    print(f"{_ts()} prompt parsed: target={parsed.target!r} return_home={parsed.return_home}")

    # 2. Load GDINO (cold start)
    print(f"{_ts()} loading Grounding DINO (cold start)...")
    t0 = time.time()
    gd = GroundingDINO()
    gd._lazy_load()  # force load now so we see the time
    print(f"{_ts()} Grounding DINO ready ({time.time() - t0:.1f}s)")

    if args.no_camera:
        print(f"{_ts()} --no-camera: smoke test done.")
        return 0

    # 3. MAVLink (only for home pose / velocity send)
    mav = None
    home = None
    # Installed below once mav is up; signal handlers should LAND and bail.
    import signal
    def _sig_handler(signum, frame):
        print(f"\n{_ts()} signal {signum} received — emergency LAND.")
        emergency_land(mav)
        # Re-raise as KeyboardInterrupt so the surrounding try/finally runs cleanup.
        raise KeyboardInterrupt(f"signal {signum}")
    if not args.dry_run:
        from pymavlink import mavutil
        print(f"{_ts()} connecting MAVLink {args.mav_port}@{args.mav_baud}...")
        mav = mavutil.mavlink_connection(args.mav_port, baud=args.mav_baud, source_system=255)
        mav.wait_heartbeat(timeout=10)
        print(f"{_ts()} MAVLink up. sys={mav.target_system} comp={mav.target_component}")
        # wait_heartbeat sometimes picks up a sys=0 ghost heartbeat first. Loop
        # until we see a real ArduPilot heartbeat (autopilot=3) and adopt its ids.
        t0 = time.time()
        while (mav.target_system == 0 or mav.target_component == 0) and time.time() - t0 < 5:
            m = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
            if m and m.autopilot == mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA:
                mav.target_system = m.get_srcSystem()
                mav.target_component = m.get_srcComponent()
                print(f"{_ts()} found ArduPilot FC: sys={mav.target_system} comp={mav.target_component}")
                break
        if mav.target_system == 0:
            # Fallback: we know from prior sessions the FC is sys=1 comp=1.
            mav.target_system = 1
            mav.target_component = 1
            print(f"{_ts()} forcing sys=1 comp=1 (fallback).")
        # ArduPilot on USB doesn't always stream position by default. Ask for it.
        request_data_streams(mav, rate_hz=4)
        time.sleep(0.5)  # give the FC a moment to start streaming
        home = capture_home(mav)
        print(f"{_ts()} home pose: {home}")
        # Now that mav is connected, install emergency-LAND signal handlers so
        # that Ctrl-C or SIGTERM (Claude Code interrupt, etc) doesn't leave the
        # drone hovering in GUIDED indefinitely.
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
        if args.takeoff_alt > 0:
            ok = arm_and_takeoff(mav, args.takeoff_alt)
            if not ok:
                print(f"{_ts()} takeoff failed; aborting.")
                return 2
    else:
        print(f"{_ts()} dry-run: skipping MAVLink connect / home-pose capture.")

    # 4. Camera
    print(f"{_ts()} opening RealSense D435i...")
    pipe, align, depth_scale, intr = open_realsense()
    print(f"{_ts()} camera up. depth_scale={depth_scale} m/unit "
          f"fx={intr.fx:.1f} fy={intr.fy:.1f} ppx={intr.ppx:.1f} ppy={intr.ppy:.1f}")

    memory = SpatialMemory(merge_radius=1.5, alpha=0.4)
    planner = make_planner(
        use_learned=args.use_learned_planner,
        models_dir=args.models_dir,
        reach_threshold=args.reach_m,
        max_speed=args.max_speed,
    )
    print(f"{_ts()} planner: {'LearnedPlanner (policy_v2)' if args.use_learned_planner else 'ReactivePlanner (rule-based)'}")

    # ============ PHASE 1: go to nearest <target> ============
    print(f"\n{_ts()} === PHASE 1: find and reach nearest {parsed.target!r} ===")
    import pyrealsense2 as rs
    dt = 1.0 / max(1e-3, args.tick_hz)
    reached = False
    rejected = False
    last_det: Optional[GDetection] = None

    # Wrap all airborne work in try/finally so ANY exit path (normal return,
    # exception, SIGINT/SIGTERM via the handler raising KeyboardInterrupt) runs
    # the emergency-LAND safety net if the drone is still armed.
    try:
        for it in range(args.max_iters):
            frames = pipe.wait_for_frames(5000)
            frames = align.process(frames)
            color = frames.get_color_frame()
            depth = frames.get_depth_frame()
            if not color or not depth:
                print(f"{_ts()} [{it:03d}] no frame")
                time.sleep(dt)
                continue

            rgb = np.asanyarray(color.get_data())       # HxWx3 uint8 RGB
            d = np.asanyarray(depth.get_data())         # HxW uint16

            t1 = time.time()
            dets = gd.detect(rgb, [parsed.target],
                             box_threshold=args.box_threshold,
                             text_threshold=args.text_threshold)
            det_ms = (time.time() - t1) * 1000.0

            # filter to detections whose label contains/matches our target
            tgt = parsed.target.lower()
            dets = [x for x in dets if tgt in x.label.lower() or x.label.lower() in tgt] or dets

            chosen = dets[0] if dets else None
            target_xyz = None
            clearance = None
            if chosen is not None:
                cx, cy = chosen.center
                z_m = depth_at(d, depth_scale, cx, cy)
                clearance = depth_at(d, depth_scale, rgb.shape[1] // 2, rgb.shape[0] // 2)
                if z_m is not None:
                    target_xyz = backproject(cx, cy, z_m, intr)
                    lm = memory.update(parsed.target, *target_xyz, score=chosen.score)
                    last_det = chosen

            cur_alt = current_rel_alt(mav, timeout=0.05) if mav is not None else None

            # Sample the full 5×9 depth grid for LearnedPlanner (ReactivePlanner ignores it
            # and uses the scalar clearance_m fallback path instead).
            dfan = depth_grid_5x9(d, depth_scale, intr)

            # Horizontal-only approach: ground-level targets (chair, person, etc.)
            # sit BELOW the drone; if we navigate toward their 3D center the drone
            # descends into them. Pass a flattened target (up=0) so the planner
            # keeps altitude and stops when horizontally close.
            flat_target = ((target_xyz[0], target_xyz[1], 0.0)
                           if target_xyz is not None else None)
            plan = planner.step(flat_target, clearance, altitude_m=cur_alt, dt=dt,
                                depth_fan=dfan)

            # Altitude cap: no climbing above max_alt.
            if cur_alt is not None and cur_alt > args.max_alt and plan.vz > 0:
                plan = PlanStep(vx=plan.vx, vy=plan.vy, vz=0.0, yaw_rate=plan.yaw_rate,
                                reason=plan.reason + f" [ALT CAP {args.max_alt}m]",
                                reached=plan.reached, rejected=plan.rejected)

            if chosen is None:
                print(f"{_ts()} [{it:03d}] det={det_ms:.0f}ms no match; plan: {plan.reason}")
            else:
                x1, y1, x2, y2 = chosen.bbox
                fx, fy, fz = target_xyz if target_xyz else (float('nan'),) * 3
                print(f"{_ts()} [{it:03d}] det={det_ms:.0f}ms "
                      f"'{chosen.label}' score={chosen.score:.2f} "
                      f"bbox=({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f}) "
                      f"body=({fx:.2f},{fy:.2f},{fz:.2f})m "
                      f"| plan: vx={plan.vx:.2f} vy={plan.vy:.2f} vz={plan.vz:.2f} "
                      f"yaw_rate={plan.yaw_rate:.2f} :: {plan.reason}")

            # Planner: (forward, left, up). MAV body-NED: (forward, right, down).
            mav_vx, mav_vy, mav_vz = plan.vx, -plan.vy, -plan.vz
            if args.dry_run:
                print_action(args, "set_velocity (mav body-NED)",
                             f"vx={mav_vx:.2f} vy={mav_vy:.2f} vz={mav_vz:.2f} "
                             f"yaw_rate={plan.yaw_rate:.2f}")
            else:
                send_velocity(mav, mav_vx, mav_vy, mav_vz, plan.yaw_rate, dt)

            if plan.reached:
                reached = True
                break
            if plan.rejected:
                rejected = True
                break
            time.sleep(dt)

        if reached:
            print(f"\n{_ts()} PHASE 1 SUCCESS: within {args.reach_m}m of {parsed.target!r}")
        elif rejected:
            print(f"\n{_ts()} PHASE 1 ABORTED (planner veto)")
        else:
            print(f"\n{_ts()} PHASE 1 TIMEOUT after {args.max_iters} ticks")

        # ============ PHASE 2: come back (RTL) ============
        if parsed.return_home:
            print(f"\n{_ts()} === PHASE 2: return to launch ===")
            if args.dry_run:
                print_action(args, "SET_MODE", "RTL")
                print_action(args, "disarm", "(after RTL complete)")
            else:
                from pymavlink import mavutil
                # Pin RTL_ALT to current alt (capped at max_alt) so RTL doesn't
                # climb to the default 15m before returning.
                cur_alt_m = current_rel_alt(mav, timeout=2.0)
                rtl_alt_m = min(cur_alt_m if cur_alt_m is not None else args.max_alt,
                                args.max_alt)
                rtl_alt_cm = float(int(max(1.0, rtl_alt_m) * 100))
                print(f"{_ts()} setting RTL_ALT={rtl_alt_cm/100:.1f}m (was default 15m)")
                mav.mav.param_set_send(
                    mav.target_system, mav.target_component,
                    b"RTL_ALT", rtl_alt_cm,
                    mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
                )
                time.sleep(0.5)
                mav.set_mode_apm("RTL")
                print(f"{_ts()} RTL commanded. Waiting for disarm (touchdown)...")
                # Only accept heartbeats from the FC (other components send
                # armed=False and would falsely signal touchdown). Require 3
                # consecutive disarmed heartbeats to absorb transient flags.
                fc_sys, fc_comp = mav.target_system, mav.target_component
                disarmed_streak = 0
                t0 = time.time()
                touched_down = False
                while time.time() - t0 < 180:
                    m = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
                    if not m:
                        continue
                    if m.get_srcSystem() != fc_sys or m.get_srcComponent() != fc_comp:
                        continue
                    armed = bool(m.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                    if armed:
                        disarmed_streak = 0
                    else:
                        disarmed_streak += 1
                        if disarmed_streak >= 3:
                            print(f"{_ts()} disarmed (landed).")
                            touched_down = True
                            break
                if not touched_down:
                    print(f"{_ts()} RTL did not complete within 180s.")
        else:
            print(f"\n{_ts()} (prompt did not request return-to-home; skipping phase 2)")

        if args.land_at_end and not args.dry_run and not parsed.return_home:
            print(f"\n{_ts()} === land ===")
            land(mav)

    except BaseException as e:
        # BaseException catches KeyboardInterrupt (from our SIGINT/SIGTERM
        # handler) too. Log it and let `finally` handle the actual LAND.
        print(f"\n{_ts()} exiting via exception: {type(e).__name__}: {e}")
        raise
    finally:
        # Safety net: if we're exiting with the drone still armed, LAND.
        if mav is not None and not args.dry_run:
            try:
                from pymavlink import mavutil as _mu
                hb = mav.messages.get("HEARTBEAT")
                still_armed = bool(hb and (hb.base_mode & _mu.mavlink.MAV_MODE_FLAG_SAFETY_ARMED))
            except Exception:
                still_armed = True  # assume worst case
            if still_armed:
                print(f"\n{_ts()} finally: drone STILL ARMED on exit — emergency LAND")
                emergency_land(mav)
        try:
            pipe.stop()
        except Exception:
            pass

    print(f"\n{_ts()} done.")
    return 0 if reached else 1


if __name__ == "__main__":
    sys.exit(main())
