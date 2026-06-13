#!/usr/bin/env python3
"""Launch a Godot-backed sim fleet: godot_engine.py + one daemon per drone.

Drop-in replacement for launch_fleet.py — identical CLI, identical behaviour
above the seam. Swap --python to point at a regular Python env (no Isaac).

    python3 godot_launch_fleet.py --fleet quad:3,rover:2 --env office \
        --certs-base ~/eco-certs-fleet

Optional flags (same as launch_fleet.py where applicable):
  --sock        Unix socket path (default /tmp/sim_engine.sock)
  --godot       Path to godot4 binary (default: GODOT_BIN env or PATH)
  --tcp-port    TCP port Godot IPC listens on (default 9999)
  --gui         Show Godot window (don't run headless)
  --xvfb        Launch Xvfb :99 before Godot (needed on headless GPU servers)
  --display     X display to use when --xvfb is set (default :99)
  --env         Scene environment: office | city | outdoor (default office)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
SIM_DIR = HERE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", required=True)
    ap.add_argument("--env", default="office")
    ap.add_argument("--sock", default="/tmp/sim_engine.sock")
    ap.add_argument("--certs-base", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--godot", default=None)
    ap.add_argument("--tcp-port", type=int, default=9999)
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--xvfb", action="store_true",
                    help="Start Xvfb before Godot (required on headless GPU servers)")
    ap.add_argument("--display", default=":99")
    args = ap.parse_args()

    certs_base = os.path.expanduser(args.certs_base)
    py = args.python
    env = dict(os.environ)

    if os.path.exists(args.sock):
        os.remove(args.sock)

    procs = []

    # 1. Optionally start Xvfb.
    if args.xvfb:
        xvfb = subprocess.Popen(
            ["Xvfb", args.display, "-screen", "0", "1280x720x24", "-ac"],
            stdout=open("/tmp/xvfb.log", "w"), stderr=subprocess.STDOUT)
        procs.append(xvfb)
        time.sleep(2)
        print(f"[launch] Xvfb started on {args.display}", flush=True)
        env["DISPLAY"] = args.display

    # 2. Start godot_engine.py (manages Godot subprocess + Unix socket proxy).
    engine_cmd = [
        py, str(SIM_DIR / "godot_engine.py"),
        "--fleet", args.fleet,
        "--env", args.env,
        "--sock", args.sock,
        "--tcp-port", str(args.tcp_port),
    ]
    if args.godot:
        engine_cmd += ["--godot", args.godot]
    if args.gui:
        engine_cmd.append("--gui")

    print(f"[launch] starting Godot engine ({args.fleet})…", flush=True)
    eng = subprocess.Popen(
        engine_cmd,
        stdout=open("/tmp/sim_engine.log", "w"),
        stderr=subprocess.STDOUT,
        env=env)
    procs.append(eng)

    # Wait for socket (godot_engine.py creates it after Godot prints "IPC ready").
    for _ in range(120):
        if os.path.exists(args.sock):
            break
        if eng.poll() is not None:
            print("[launch] engine exited early; see /tmp/sim_engine.log", flush=True)
            return
        time.sleep(1)
    else:
        print("[launch] engine socket never appeared", flush=True)
        return
    print("[launch] engine ready; starting daemons…", flush=True)

    # 3. Start one sim_drone_daemon per vehicle.
    # Import fleet parser from the sim dir.
    sys.path.insert(0, str(SIM_DIR))
    from fleet import parse_roster  # noqa: E402
    roster = parse_roster(args.fleet)

    for spec in roster:
        did = spec["id"]
        p = subprocess.Popen(
            [py, str(SIM_DIR / "sim_drone_daemon.py"),
             "--drone-id", did,
             "--vehicle", spec["type"],
             "--sock", args.sock,
             "--certs-dir", f"{certs_base}/{did}"],
            stdout=open(f"/tmp/daemon-{did}.log", "w"),
            stderr=subprocess.STDOUT,
            env=env)
        procs.append(p)
        time.sleep(0.2)

    print(f"[launch] fleet up: engine + {len(roster)} daemons", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("[launch] shutting down…", flush=True)
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
