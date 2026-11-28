#!/usr/bin/env python3
"""Fly straight lines through a scene and report what each aircraft hit.

For checking that what is drawn is solid: each case spawns an aircraft at
`from` (ENU metres; z = metres in the env's height frame, NAVD88 for
baylands), points it at `to`, flies level at `airspeed` and reports whether
and where it struck (fw_state / fw_events), against an expectation.

    python3 tools/strike_test.py --env baylands --cases cases.json [--godot BIN]

cases.json: [{"name", "from": [e, n, z], "to": [e, n], "airspeed": 15,
"airframe": "fixedwing"|"vtol", "expect": "hit"|"clear", "seconds": 30}]
Needs a display (Godot draws nothing headless) only because the sim's
cameras run; no frames are saved.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env", default="baylands")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--port", type=int, default=9961)
    ap.add_argument("--godot", default=os.environ.get("GODOT_BIN", "godot"))
    ap.add_argument("--log", default="strike_godot.log")
    a = ap.parse_args(argv)
    cases = json.loads(Path(a.cases).read_text())
    log = open(a.log, "w")
    proc = subprocess.Popen([a.godot, "--path", str(PROJECT), "--resolution", "640x360", "--",
                             "--fleet=", f"--env={a.env}", f"--ipc-port={a.port}", "--seed=0"],
                            stdout=log, stderr=subprocess.STDOUT)
    fails = 0
    try:
        sock = None
        for _ in range(300):
            if proc.poll() is not None:
                print("godot exited; see", a.log)
                return 1
            try:
                sock = socket.create_connection(("127.0.0.1", a.port), timeout=5)
                break
            except OSError:
                time.sleep(1)
        f = sock.makefile("rwb")

        def rpc(req):
            f.write((json.dumps(req) + "\n").encode())
            f.flush()
            return json.loads(f.readline())

        time.sleep(6)
        for i, c in enumerate(cases):
            did = f"strike{i}"
            (e0, n0, z), (e1, n1) = c["from"], c["to"][:2]
            yaw = math.atan2(n1 - n0, e1 - e0)            # rad CCW from +x (east)
            rpc({"op": "fw_spawn", "id": did, "p": [e0, n0, z], "yaw": yaw, "ceiling": 400,
                 "airframe": c.get("airframe", "fixedwing")})
            v = float(c.get("airspeed", 15.0))
            rpc({"op": "fw_drive", "id": did, "airspeed": v, "yaw_rate": 0.0, "climb": 0.0})
            dist = math.hypot(e1 - e0, n1 - n0)
            t_end = time.time() + float(c.get("seconds", dist / v + 6.0))
            st = {}
            while time.time() < t_end:
                st = rpc({"op": "fw_state", "id": did})
                s = st.get("state", st)
                if s.get("crashed"):
                    break
                p = s.get("position") or s.get("p") or [0, 0, 0]
                if math.hypot(p[0] - e0, p[1] - n0) > dist + 5:
                    break
                time.sleep(0.1)
            s = st.get("state", st)
            ev = rpc({"op": "fw_events", "id": did}).get("events", [])
            crash = [x for x in ev if "crash" in json.dumps(x).lower() or "strike" in json.dumps(x).lower()]
            got = "hit" if s.get("crashed") else "clear"
            ok = got == c.get("expect", got)
            fails += 0 if ok else 1
            what = crash[-1] if crash else {}
            print(f"{'PASS' if ok else 'FAIL'} {c['name']}: {got} (expected {c.get('expect')}) "
                  f"pos={[round(x, 1) for x in (s.get('position') or s.get('p') or [])]} {json.dumps(what)[:200]}",
                  flush=True)
            rpc({"op": "fw_despawn", "id": did})
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(f"{len(cases) - fails}/{len(cases)} as expected")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
