#!/usr/bin/env python3
"""Launch a process-separated sim fleet: ONE Isaac engine + ONE daemon per drone.

    python3 launch_fleet.py --fleet quad:10,rover:10 --env office \
        --certs-base ~/eco-certs-fleet --python /opt/ml/isaac-sim-env/bin/python3

Engine owns Isaac; each daemon is its own process with its own MQTT connection
(no cross-connection GIL contention). Logs: /tmp/sim_engine.log, /tmp/daemon-*.log.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from fleet import parse_roster  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", required=True)
    ap.add_argument("--env", default="office")
    ap.add_argument("--sock", default="/tmp/sim_engine.sock")
    ap.add_argument("--certs-base", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--photoreal", action="store_true",
                    help="Pass --photoreal to the engine (high-fidelity assets + RTX PT)")
    args = ap.parse_args()

    roster = parse_roster(args.fleet)
    certs_base = os.path.expanduser(args.certs_base)
    py = args.python
    env = dict(os.environ)

    if os.path.exists(args.sock):
        os.remove(args.sock)

    print(f"[launch] starting engine ({len(roster)} vehicles)…", flush=True)
    engine_cmd = [py, str(HERE / "sim_engine.py"), "--fleet", args.fleet,
                  "--env", args.env, "--sock", args.sock]
    if args.photoreal:
        engine_cmd.append("--photoreal")
    eng = subprocess.Popen(engine_cmd,
        stdout=open("/tmp/sim_engine.log", "w"), stderr=subprocess.STDOUT, env=env)

    # wait for the engine to create the IPC socket (Isaac finished loading)
    for _ in range(180):
        if os.path.exists(args.sock):
            break
        if eng.poll() is not None:
            print("[launch] engine exited early; see /tmp/sim_engine.log", flush=True)
            return
        time.sleep(2)
    else:
        print("[launch] engine never created socket", flush=True)
        return
    print("[launch] engine ready; starting daemons…", flush=True)

    procs = [eng]
    for spec in roster:
        did = spec["id"]
        p = subprocess.Popen(
            [py, str(HERE / "sim_drone_daemon.py"), "--drone-id", did,
             "--vehicle", spec["type"], "--sock", args.sock,
             "--certs-dir", f"{certs_base}/{did}"],
            stdout=open(f"/tmp/daemon-{did}.log", "w"), stderr=subprocess.STDOUT, env=env)
        procs.append(p)
        time.sleep(0.2)
    print(f"[launch] fleet up: engine + {len(roster)} daemons", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
