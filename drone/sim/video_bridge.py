"""Local MJPEG video bridge for the fixed-wing sim (Part 2 — demo tooling).

The production app streams a drone's camera over Kinesis Video Streams WebRTC
(client/ios/.../VideoStreamView.swift). That path is cloud-only and unavailable
in the no-cloud GCS demo, so this bridge gives the app a live per-drone feed the
same way the rest of the demo stands in for AWS: it reads each aircraft's
forward-camera frames straight out of Godot (the ``fw_grab_frame`` IPC op the
on-device VLM already uses) and re-serves them as plain HTTP the iPad can pull:

    GET /healthz            -> "ok"
    GET /drones             -> {"drones": ["alpha", "bravo"]}
    GET /snapshot/{id}.jpg  -> latest single JPEG for that drone
    GET /video/{id}         -> multipart/x-mixed-replace MJPEG stream

A single DepotClient polls Godot round-robin at ``--fps`` and keeps the newest
JPEG per drone in memory; HTTP handlers just serve whatever is latest, so any
number of viewers share one readback stream and a slow/absent viewer never
stalls the sim. Requires the Godot sim running with ``--gui true`` (headless
Godot's dummy renderer leaves the camera SubViewport blank — same caveat as
DepotClient.fw_grab_frame).

No auth: this is a LAN sim feed carrying nothing sensitive. Bind to a trusted
network only.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

# depot_client lives in the rover sim tree; the fixed-wing daemon imports it the
# same way (see fw_gcs_daemon.py:start).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "rover" / "sim"))
from depot_client import DepotClient  # noqa: E402

_BOUNDARY = "presidioframe"


class FrameHub:
    """Holds the latest JPEG per drone and drives the Godot grab loop."""

    def __init__(self, host: str, port: int, fps: float,
                 drones: list[str] | None, client=None):
        # `client` is injectable for tests; production builds one that opens the
        # Godot IPC socket.
        self._client = client if client is not None else DepotClient(host=host,
                                                                     port=port)
        self._interval = 1.0 / max(fps, 0.5)
        self._explicit = drones
        self._lock = threading.Lock()
        self._frames: dict[str, bytes] = {}
        self._seq: dict[str, int] = {}
        self._stop = threading.Event()

    # -- discovery ---------------------------------------------------------
    def _discover(self) -> list[str]:
        if self._explicit:
            return self._explicit
        try:
            r = self._client._call({"op": "fw_all_states"})
            # fw_all_states returns a list of per-drone state dicts, each with
            # an "id" (see fixedwing_manager.all_states / ipc_server.gd).
            states = r.get("states", []) if r.get("ok") else []
            ids = [s.get("id") for s in states if isinstance(s, dict) and s.get("id")]
            return sorted(ids)
        except Exception:
            return sorted(self._frames.keys())

    # -- grab loop ---------------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            drones = self._discover()
            if not drones:
                time.sleep(0.5)
                continue
            for did in drones:
                if self._stop.is_set():
                    break
                try:
                    jpg = self._client.fw_grab_frame(did)
                except Exception:
                    jpg = None
                if jpg:
                    with self._lock:
                        self._frames[did] = jpg
                        self._seq[did] = self._seq.get(did, 0) + 1
                time.sleep(self._interval)

    def stop(self) -> None:
        self._stop.set()

    # -- readers -----------------------------------------------------------
    def drones(self) -> list[str]:
        with self._lock:
            return sorted(self._frames.keys())

    def latest(self, did: str) -> tuple[bytes | None, int]:
        with self._lock:
            return self._frames.get(did), self._seq.get(did, 0)


def _make_handler(hub: FrameHub):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):  # quiet
            pass

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")

        def do_GET(self):  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/healthz":
                return self._text(200, "ok")
            if path == "/drones":
                import json
                return self._text(200, json.dumps({"drones": hub.drones()}),
                                  ctype="application/json")
            if path.startswith("/snapshot/"):
                did = path[len("/snapshot/"):]
                if did.endswith(".jpg"):
                    did = did[:-4]
                return self._snapshot(did)
            if path.startswith("/video/"):
                return self._video(path[len("/video/"):])
            self._text(404, "not found")

        def _text(self, code: int, body: str, ctype: str = "text/plain"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def _snapshot(self, did: str):
            jpg, _ = hub.latest(did)
            if not jpg:
                return self._text(503, "no frame yet")
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(jpg)

        def _video(self, did: str):
            self.send_response(200)
            self.send_header(
                "Content-Type",
                f"multipart/x-mixed-replace; boundary={_BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self._cors()
            self.end_headers()
            last_seq = -1
            try:
                while True:
                    jpg, seq = hub.latest(did)
                    if jpg and seq != last_seq:
                        last_seq = seq
                        head = (f"--{_BOUNDARY}\r\n"
                                f"Content-Type: image/jpeg\r\n"
                                f"Content-Length: {len(jpg)}\r\n\r\n").encode()
                        self.wfile.write(head)
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return  # viewer went away

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--godot-host", default="127.0.0.1")
    ap.add_argument("--godot-port", type=int, default=9978)
    ap.add_argument("--http-host", default="0.0.0.0")
    ap.add_argument("--http-port", type=int, default=8091)
    ap.add_argument("--fps", type=float, default=6.0,
                    help="per-drone grab rate (round-robined over the fleet)")
    ap.add_argument("--drones", default=None,
                    help="comma list to override auto-discovery, e.g. alpha,bravo")
    args = ap.parse_args()

    drones = [d.strip() for d in args.drones.split(",")] if args.drones else None
    hub = FrameHub(args.godot_host, args.godot_port, args.fps, drones)
    grab = threading.Thread(target=hub.run, daemon=True)
    grab.start()

    server = ThreadingHTTPServer((args.http_host, args.http_port),
                                 _make_handler(hub))
    print(f"[video_bridge] serving MJPEG on http://{args.http_host}:"
          f"{args.http_port} (godot {args.godot_host}:{args.godot_port}, "
          f"{args.fps} fps)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop()
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
