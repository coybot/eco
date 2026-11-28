#!/usr/bin/env python3
"""Check that Baylands' power lines are solid: fly aircraft through a span.

Launches the sim on --env=baylands (needs a display, like shoot_views.py),
reads the conductors over IPC (env_op "wires"), and for a span of the 115 kV
line beside the park:

  1. a fixed wing flown level through the lowest conductor's mid-span must
     strike it (fw_state crashed),
  2. a VTOL flown the same way must strike it too,
  3. a fixed wing flown level 5 m over the top wire must cross untouched.

    python3 tools/check_baylands_wires.py --godot $GODOT_BIN [--port 9975] [-- extra sim args]
Exit 0 when all three behave.
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
# Mid-span of the 115 kV line's span beside the park's west edge (ENU m).
SPAN_MID = (-738.0, -347.0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--godot", required=True)
    ap.add_argument("--port", type=int, default=9975)
    ap.add_argument("--log", default="check_wires_godot.log")
    ap.add_argument("extra", nargs="*")
    a = ap.parse_args(argv)
    log = open(a.log, "w")
    proc = subprocess.Popen([a.godot, "--path", str(PROJECT), "--resolution", "640x360", "--",
                             "--fleet=", "--env=baylands", f"--ipc-port={a.port}", "--seed=0"] + a.extra,
                            stdout=log, stderr=subprocess.STDOUT)
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
        if sock is None:
            return 1
        sock.settimeout(120)
        f = sock.makefile("rwb")

        def rpc(req):
            f.write((json.dumps(req) + "\n").encode())
            f.flush()
            return json.loads(f.readline())

        time.sleep(5)
        e0, n0 = SPAN_MID
        w = rpc({"op": "env_op", "name": "wires", "args": {"rect": [e0 - 200, n0 - 200, e0 + 200, n0 + 200], "step": 2}})
        radius = w["radius"]
        # Conductors whose span crosses the mid-span point's line (nearest polyline point within 30 m).
        best = []
        for c in w["conductors"]:
            pts = c["points"]
            k = min(range(len(pts)), key=lambda i: math.hypot(pts[i][0] - e0, pts[i][1] - n0))
            if math.hypot(pts[k][0] - e0, pts[k][1] - n0) < 30:
                best.append((c, k))
        if not best:
            print("no conductor near", SPAN_MID)
            return 1
        low_c, low_k = min(best, key=lambda ck: ck[0]["points"][ck[1]][2])
        p = low_c["points"][low_k]
        q = low_c["points"][min(low_k + 1, len(low_c["points"]) - 1)]
        tx, ty = q[0] - p[0], q[1] - p[1]
        tl = math.hypot(tx, ty)
        tx, ty = tx / tl, ty / tl
        nx, ny = -ty, tx                              # across the span
        top_z = max(c["points"][k][2] for c, k in best)
        print(f"{len(best)} conductors in the span; lowest at z {p[2]:.2f} m ({low_c['kind']}), top wire z {top_z:.2f} m;"
              f" wire radius {radius} m")
        yaw = math.atan2(ny, nx)
        results = []
        tests = [("fixedwing through the lowest conductor", "fixedwing", p[2], True),
                 ("vtol through the lowest conductor", "vtol", p[2], True),
                 ("fixedwing 5 m over the top wire", "fixedwing", top_z + 5.0, False)]
        for i, (name, frame, z, want_hit) in enumerate(tests):
            did = f"wiretest{i}"
            start = (p[0] - nx * 120, p[1] - ny * 120)
            rpc({"op": "fw_spawn", "id": did, "p": [start[0], start[1], z], "yaw": yaw, "airframe": frame})
            if frame == "vtol":
                rpc({"op": "fw_set_physics", "id": did, "realistic": False})
            rpc({"op": "fw_drive", "id": did, "airspeed": 18.0, "yaw_rate": 0.0, "climb": 0.0})
            crashed = False
            st = {}
            t0 = time.time()
            while time.time() - t0 < 20:
                time.sleep(0.5)
                st = rpc({"op": "fw_state", "id": did})
                if st.get("crashed"):
                    crashed = True
                    break
                pos = st["position"]
                if (pos[0] - p[0]) * nx + (pos[1] - p[1]) * ny > 60:
                    break
            pos = st.get("position", [0, 0, 0])
            along = (pos[0] - p[0]) * nx + (pos[1] - p[1]) * ny
            ok = crashed == want_hit
            results.append(ok)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: z {z:.1f} m -> crashed={crashed} at "
                  f"({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}), {along:+.1f} m past the wire line, "
                  f"airspeed {st.get('airspeed', 0):.1f}")
            rpc({"op": "fw_despawn", "id": did})
        ev = rpc({"op": "fw_events", "since": 0.0})
        for e in ev.get("events", []):
            if e.get("type") == "collision" or e.get("event") == "collision":
                print("   event:", json.dumps(e)[:200])
        print("OVERALL:", "PASS" if all(results) else "FAIL")
        return 0 if all(results) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
