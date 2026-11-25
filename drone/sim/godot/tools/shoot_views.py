#!/usr/bin/env python3
"""Launch the Godot sim on an env, grab fixed camera views to JPEGs, quit.

For checking how a scene LOOKS (and how fast it draws) without a fleet or a
GCS: it opens one vantage camera over IPC, moves it through a list of views
and saves each frame.

    python3 tools/shoot_views.py --out /tmp/shots                 # manhattan_photo
    python3 tools/shoot_views.py --out /tmp/shots --env manhattan --views my.json
    python3 tools/shoot_views.py --out /tmp/shots -- --world-life  # extra sim args

Views file: a JSON list of {"name", "at", "look", "alt", "look_alt"?, "wait"?,
"pre"?}. `at`/`look` are [lat, lon] (Manhattan's projection, see
fetch_manhattan.py) or {"enu": [e, n]} in sim metres. `alt`/`look_alt` are
metres above street level. `pre` is a list of IPC requests sent before the
frame (for example {"op": "fw_set_time", "hour": 21}).

Needs a display: Godot draws nothing headless. The window is the --res size;
the frames are --frame size.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent

# Must match fetch_manhattan.py.
DATUM_LAT = 40.7005
DATUM_LON = -74.0170
GRID_ROTATION_DEG = 29.0


def project(lat: float, lon: float) -> tuple[float, float]:
    m_lat = 111132.0
    m_lon = 111320.0 * math.cos(math.radians(DATUM_LAT))
    x = (lon - DATUM_LON) * m_lon
    y = (lat - DATUM_LAT) * m_lat
    t = math.radians(GRID_ROTATION_DEG)
    return (x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--env", default="manhattan_photo")
    ap.add_argument("--views", default=str(HERE / "views_manhattan.json"))
    ap.add_argument("--only", default="", help="comma list of view names")
    ap.add_argument("--port", type=int, default=9971)
    ap.add_argument("--res", default="1280x720", help="window size")
    ap.add_argument("--frame", default="1280x720", help="grabbed frame size")
    ap.add_argument("--settle", type=float, default=8.0, help="s to wait after the env is up")
    ap.add_argument("--godot", default=os.environ.get("GODOT_BIN", "godot"))
    ap.add_argument("extra", nargs="*", help="extra sim args, after --")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    views = json.loads(Path(a.views).read_text())
    if a.only:
        keep = set(a.only.split(","))
        views = [v for v in views if v["name"] in keep]
    fw, fh = (int(x) for x in a.frame.split("x"))

    log = open(out / "godot.log", "w")
    # Always on top: macOS stops redrawing a window other windows cover, and
    # then every grabbed frame is the same stale one.
    cmd = [a.godot, "--always-on-top", "--position", "40,600", "--path", str(PROJECT),
           "--resolution", a.res, "--",
           "--fleet=", f"--env={a.env}", f"--ipc-port={a.port}", "--seed=0"] + a.extra
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    try:
        sock = None
        for _ in range(300):
            if proc.poll() is not None:
                print("godot exited; see", out / "godot.log", file=sys.stderr)
                return 1
            try:
                sock = socket.create_connection(("127.0.0.1", a.port), timeout=5)
                break
            except OSError:
                time.sleep(1)
        if sock is None:
            print("no IPC", file=sys.stderr)
            return 1
        sock.settimeout(300)
        f = sock.makefile("rwb")

        def rpc(req: dict) -> dict:
            f.write((json.dumps(req) + "\n").encode())
            f.flush()
            return json.loads(f.readline())

        time.sleep(a.settle)
        # The env re-centres its slice; read the shift so lat/lon views land.
        st = rpc({"op": "fw_env_state"})
        off = (st.get("env", st).get("datum_offset_enu") or [0.0, 0.0]) if isinstance(st, dict) else [0, 0]

        def sim(p) -> tuple[float, float]:
            if isinstance(p, dict):
                return tuple(p["enu"])
            e, n = project(*p)
            return (e - off[0], n - off[1])

        allperf = {}
        for v in views:
            for req in v.get("pre", []):
                print(v["name"], "pre", rpc(req))
            pe, pn = sim(v["at"])
            le, ln = sim(v["look"])
            pos = [pe, pn, v["alt"]]
            look = [le, ln, v.get("look_alt", 0.0)]
            # A fresh vantage per view: a moved one's SubViewport kept
            # returning its first frame.
            rpc({"op": "remove_vantage", "name": "shot"})
            time.sleep(0.5)
            rpc({"op": "add_vantage", "name": "shot", "p": pos, "look": look, "w": fw, "h": fh})
            time.sleep(float(v.get("wait", 4.0)))
            allperf[v["name"]] = rpc({"op": "perf"})
            r = rpc({"op": "grab_vantage", "name": "shot"})
            if r.get("jpg"):
                (out / f"{v['name']}.jpg").write_bytes(base64.b64decode(r["jpg"]))
                print("shot", v["name"])
            else:
                print("no frame", v["name"], r)
        # Main-window numbers (its camera does not follow the vantage); compare
        # runs, not views.
        (out / "perf.json").write_text(json.dumps(allperf, indent=1))
        if allperf:
            print("perf", next(iter(allperf.values())))
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
