#!/usr/bin/env python3
"""Sim engine: ONE Isaac world serving many drone daemons over a local socket.

This process owns Isaac (the GPU) and NOTHING else — no MQTT. Each drone runs as
its own daemon process (sim_drone_daemon.py) with its own MQTT connection and
talks to this engine via IPC to move its vehicle and grab camera frames. That
keeps MQTT off the GIL-contended Isaac process (many MQTT connections in one
process starved inbound delivery), while still sharing a single Isaac world.

    python3 sim_engine.py --fleet quad:10,rover:10 --env office \
        --sock /tmp/sim_engine.sock
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socketserver
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fleet import parse_roster        # noqa: E402
from fleet_worker import FleetWorker   # noqa: E402

WORKER: FleetWorker | None = None


def _dispatch(req: dict) -> dict:
    op = req.get("op")
    did = req.get("id")
    b = WORKER.bridge
    try:
        if op == "get_state":
            st = b.get_drone_state(did)
            return {"ok": True, "position": st["position"].tolist(), "yaw": st["yaw"]}
        if op == "set_goal":
            b.set_drone_goal(did, req["p"]); return {"ok": True}
        if op == "set_velocity":
            b.set_drone_velocity(did, req["v"]); return {"ok": True}
        if op == "clear_velocity":
            b.clear_velocity(did); return {"ok": True}
        if op == "set_yaw":
            b.set_drone_yaw(did, req["yaw"]); return {"ok": True}
        if op == "grab_frame":
            import cv2
            rgb = WORKER.request_frame(did)
            if rgb is None:
                return {"ok": True, "jpg": None}
            ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                                   [cv2.IMWRITE_JPEG_QUALITY, 85])
            return {"ok": True, "jpg": base64.b64encode(jpg.tobytes()).decode() if ok else None}
        if op == "add_vantage":
            WORKER.add_vantage(req["name"], req["p"], req["look"])
            return {"ok": True}
        if op == "auto_overhead":
            name = WORKER.auto_overhead_vantage(req.get("name", "overhead"))
            return {"ok": True, "name": name}
        if op == "grab_vantage":
            import cv2
            rgb = WORKER.request_vantage_frame(req["name"])
            if rgb is None:
                return {"ok": True, "jpg": None}
            ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                                   [cv2.IMWRITE_JPEG_QUALITY, 85])
            return {"ok": True, "jpg": base64.b64encode(jpg.tobytes()).decode() if ok else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": f"unknown op {op}"}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            if not line.strip():
                continue
            try:
                req = json.loads(line)
            except Exception:
                continue
            resp = _dispatch(req)
            self.wfile.write((json.dumps(resp) + "\n").encode())
            self.wfile.flush()


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    global WORKER
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", required=True)
    ap.add_argument("--env", default="office")
    ap.add_argument("--sock", default="/tmp/sim_engine.sock")
    ap.add_argument("--gui", action="store_true")
    args = ap.parse_args()

    roster = parse_roster(args.fleet)
    print(f"== sim engine == {len(roster)} vehicles env={args.env} sock={args.sock}",
          flush=True)
    WORKER = FleetWorker(environment=args.env, roster=roster, headless=not args.gui)

    def serve():
        WORKER.ready.wait()
        if os.path.exists(args.sock):
            os.remove(args.sock)
        srv = _Server(args.sock, _Handler)
        os.chmod(args.sock, 0o770)
        print(f"[engine] IPC serving on {args.sock}", flush=True)
        srv.serve_forever()

    threading.Thread(target=serve, name="ipc", daemon=True).start()
    print("[engine] loading Isaac…", flush=True)
    WORKER.run()  # main thread owns Isaac


if __name__ == "__main__":
    main()
