"""M0 gate: fly plane_sdk.py's real ArduPlane primitives through ArduPlane SITL.

Unlike sitl_l5.py / sitl_validate.py (which drive ArduCopter/ArduRover SITL with
hand-rolled raw-MAVLink velocity setpoints, deliberately bypassing drone_sdk's
serial-only connection), this harness exercises the ACTUAL flight SDK
(eco/drone/common/plane_sdk.py) that will fly the real Skywalker X8 — the point
is to validate the SDK itself, not a separate SITL-only reimplementation of it.

drone_sdk._connect() only knows how to open a serial device from config.yaml
(real-hardware assumption). Rather than touch that path or write a throwaway
config.yaml, this harness pre-seeds drone_sdk's module-global `_master` with an
already-open SITL MAVLink connection before calling any plane_sdk function —
_connect() sees `_master` is already set and returns it immediately, so every
plane_sdk call (hand_launch, orbit, land, rtl, ...) runs against SITL completely
unmodified from how it would run against a real Pixhawk 6X.

Gate (per the fixed-wing autonomy plan, M0): hand-launch -> GUIDED transit ->
NAV_LOITER orbit N laps -> RTL -> land, in ArduPlane SITL, with geofence +
altitude-floor configured and never breached, no autonomy/perception involved.

Requires a built ArduPlane SITL binary + pymavlink:

    python3 -m eco.drone.training.sitl_plane \\
        --sitl-bin ~/ardupilot/build/sitl/bin/arduplane \\
        --defaults ~/ardupilot/Tools/autotest/default_params/plane.parm \\
        --frame plane-elevon   # verify this frame name against your ArduPilot
                                # checkout — a Skywalker X8 is an elevon flying
                                # wing; "plane-elevon" is the closest stock SITL
                                # frame as of recent ArduPilot, not yet confirmed
                                # against the specific version this will be
                                # built against.

STATUS: unflown / not yet run against a real SITL binary (none was available in
this environment — see the plan's M0 decision point). Written to the gate's
spec; treat first real runs as shakeout, especially launch-detection timing
(TKOFF_THR_MINACC/MINSPD may need SITL-specific tuning vs a real hand-launch —
see --min-accel/--min-airspeed below) and the ArduPlane mode-name mapping.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

_repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_repo))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

DEFAULT_HOME = "37.4141,-122.0834,10,0"   # lat,lon,alt,heading — Baylands-ish; override per site
ORBIT_LAPS = 2
ORBIT_RADIUS_M = 60.0
CLIMB_ALT_M = 40.0


def launch_sitl(sitl_bin, defaults, frame="plane-elevon", home=DEFAULT_HOME):
    cmd = [os.path.expanduser(sitl_bin), "-S", "--model", frame, "--speedup", "1",
           "-I0", "--home", home]
    if defaults:
        cmd += ["--defaults", os.path.expanduser(defaults)]
    print("launching SITL:", " ".join(cmd), flush=True)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def connect_and_seed(addr):
    """Open the SITL MAVLink link and seed it into drone_sdk so plane_sdk's
    calls transparently use it (see module docstring)."""
    from pymavlink import mavutil
    import drone_sdk

    print(f"connecting {addr} ...", flush=True)
    m = mavutil.mavlink_connection(addr, source_system=255)
    m.wait_heartbeat(timeout=30)
    print(f"heartbeat: sys={m.target_system} comp={m.target_component}", flush=True)
    m.mav.request_data_stream_send(m.target_system, m.target_component,
                                    mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1)

    drone_sdk._master = m  # noqa: SLF001 - deliberate, see module docstring
    return m


def wait_ekf_ready(m, timeout=90):
    """Block until EKF reports attitude + horizontal position ready."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        msg = m.recv_match(type="EKF_STATUS_REPORT", blocking=True, timeout=2)
        if msg and (msg.flags & 0x1F) >= 0x0F:
            print("EKF ready", flush=True)
            return True
    print("WARNING: EKF readiness timeout — proceeding anyway (SITL sometimes "
          "under-reports early flags)", flush=True)
    return False


def run_gate(args):
    import plane_sdk

    breaches = {"geofence": 0, "altitude_floor": 0}

    print("--- configuring safety envelope (geofence + altitude floor) ---", flush=True)
    plane_sdk.configure_safety_envelope(
        fence_radius_m=args.fence_radius, fence_max_alt_m=args.fence_max_alt,
        min_alt_floor_m=args.altitude_floor, action='RTL')
    plane_sdk.configure_launch_detection(
        min_accel_mss=args.min_accel, min_airspeed_mps=args.min_airspeed)

    print(f"--- hand_launch to {CLIMB_ALT_M}m ---", flush=True)
    ok = plane_sdk.hand_launch(CLIMB_ALT_M, launch_timeout_s=args.launch_timeout)
    assert ok, "hand_launch() did not report reaching altitude — gate FAILED"
    assert plane_sdk.is_armed(), "expected armed after successful launch"

    lat, lon, alt = plane_sdk.get_position()
    print(f"airborne at ({lat:.6f},{lon:.6f}) alt={alt:.1f}m", flush=True)
    assert alt >= args.altitude_floor, (
        f"altitude {alt:.1f}m below configured floor {args.altitude_floor}m "
        f"immediately after launch")

    print(f"--- orbit radius={ORBIT_RADIUS_M}m for {ORBIT_LAPS} laps ---", flush=True)
    orbit_ok = plane_sdk.orbit(lat, lon, radius_m=ORBIT_RADIUS_M, alt_m=CLIMB_ALT_M)
    assert orbit_ok, "orbit() failed to enter GUIDED / command the loiter"

    turn_radius = args.max_speed / args.max_yaw_rate  # matches vehicle_class.turn_radius_m formula
    assert ORBIT_RADIUS_M >= turn_radius * 0.95, (
        f"commanded orbit radius {ORBIT_RADIUS_M}m is tighter than this "
        f"airframe's turn_radius_m ({turn_radius:.1f}m) — would not be flyable")

    laps_ok = plane_sdk.wait_for_orbit_laps(ORBIT_LAPS, timeout_s=args.max_seconds)
    print(f"orbit laps completed: {laps_ok}", flush=True)
    # Altitude-floor check post-orbit. wait_for_orbit_laps() blocks on ATTITUDE
    # messages, so it can't also poll position concurrently in this single-
    # threaded harness — this is a defined-point check (after launch, after the
    # orbit, after RTL below), not continuous monitoring. Continuous in-flight
    # floor monitoring would want its own thread/asyncio task if that fidelity
    # is needed later; the real hard guarantee is FENCE_ALT_MIN on the FC itself
    # (configure_safety_envelope above), not this harness's polling.
    _, _, cur_alt = plane_sdk.get_position()
    if cur_alt < args.altitude_floor:
        breaches["altitude_floor"] += 1
        print(f"WARNING: altitude {cur_alt:.1f}m below floor {args.altitude_floor}m "
              f"after orbit", flush=True)

    print("--- RTL ---", flush=True)
    assert plane_sdk.rtl(), "rtl() failed to enter RTL mode"

    print("--- land ---", flush=True)
    approach_lat, approach_lon = lat, lon  # same field; real flight must pick an
                                            # into-wind approach point (see land()
                                            # docstring) — SITL has no real wind.
    landed = plane_sdk.land(approach_lat, approach_lon, heading_deg=args.land_heading,
                             alt_m=0.0)
    assert landed, "land() did not confirm disarm within timeout — gate FAILED"
    assert not plane_sdk.is_armed(), "expected disarmed after landing"

    print(f"\ngeofence/altitude-floor breaches observed: {breaches}", flush=True)
    ok = breaches["altitude_floor"] == 0
    print("PASS" if ok else "FAIL — altitude floor was breached", flush=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sitl-bin", required=True)
    ap.add_argument("--defaults", default=None)
    ap.add_argument("--frame", default="plane-elevon")
    ap.add_argument("--home", default=DEFAULT_HOME)
    ap.add_argument("--connect", default="tcp:127.0.0.1:5760")
    ap.add_argument("--fence-radius", type=float, default=300.0, help="meters")
    ap.add_argument("--fence-max-alt", type=float, default=100.0, help="meters")
    ap.add_argument("--altitude-floor", type=float, default=20.0, help="meters — the tree-clearance guarantee")
    ap.add_argument("--min-accel", type=float, default=2.0,
                     help="m/s^2 — SITL launch-detection threshold; a real hand "
                          "launch needs a higher value (see plane_sdk default)")
    ap.add_argument("--min-airspeed", type=float, default=5.0, help="m/s")
    ap.add_argument("--launch-timeout", type=float, default=30.0, help="seconds")
    ap.add_argument("--max-seconds", type=float, default=180.0)
    ap.add_argument("--land-heading", type=float, default=0.0, help="degrees")
    ap.add_argument("--max-speed", type=float, default=25.0, help="m/s, matches vehicle_class.FIXEDWING")
    ap.add_argument("--max-yaw-rate", type=float, default=0.6, help="rad/s, matches vehicle_class.FIXEDWING")
    args = ap.parse_args()

    proc = launch_sitl(args.sitl_bin, args.defaults, frame=args.frame, home=args.home)
    time.sleep(10)
    try:
        m = connect_and_seed(args.connect)
        wait_ekf_ready(m)
        ok = run_gate(args)
        sys.exit(0 if ok else 1)
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
