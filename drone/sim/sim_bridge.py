#!/usr/bin/env python3
"""Eco sim host: makes a simulated drone behave exactly like an IRL drone.

Commands arrive over AWS IoT **MQTT** (outbound, cert-authenticated — no tunnel),
just like eco/drone/common/daemon.py; live video streams to the app as a KVS
WebRTC master (also cert-authenticated). The vehicle is an Isaac-shipped asset
(Crazyflie quad / Nova Carter rover) driven kinematically by SimWorker.

One process per sim drone (Isaac loads one USD scene per world):

    python3 sim_bridge.py --env office --drone-id sim-quadcopter-abcd \
        --vehicle quadcopter --certs-dir ~/eco-certs \
        --iot-endpoint "$IOT_ENDPOINT" \
        --credentials-endpoint "$CREDENTIALS_ENDPOINT"

Run on hoopoe (Isaac Sim host). No inbound reachability needed: all cloud↔sim
traffic is outbound MQTT + outbound KVS. The optional ``--http-debug`` flag
re-enables a local ``/execute`` server for hand-testing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Make the existing IsaacSimBridge importable.
HARNESS_DIR = Path(
    os.environ.get("ISHMAEL_HARNESS",
                   str(Path.home() / "code/ishmael/swarm_eval/harness"))
)
sys.path.insert(0, str(HARNESS_DIR))
sys.path.insert(0, str(Path(__file__).parent))

from sim_sdk import SimWorker  # noqa: E402

try:  # packaged (eco.drone.sim) in the repo; flat (sim/ on path) when run as a script
    from .endpoints import iot_endpoint, credentials_endpoint
except ImportError:
    from endpoints import iot_endpoint, credentials_endpoint

WORKER: SimWorker | None = None
ENVIRONMENT = "dev"  # deploy env -> channel suffix; matches video.py get_channel_name


class ExecuteHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # health check
        if self.path == "/health":
            ready = WORKER is not None and WORKER.ready.is_set()
            self._send(200 if ready else 503, {"ready": ready})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/execute":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            code = body.get("code", "")
            conversation_id = body.get("conversation_id", "sim")
        except Exception as e:
            self._send(400, {"success": False, "error": f"bad request: {e}",
                             "images": [], "elapsed_s": 0})
            return
        if WORKER is None or not WORKER.ready.is_set():
            self._send(503, {"success": False, "error": "simulator not ready",
                             "images": [], "elapsed_s": 0})
            return
        result = WORKER.submit(code, conversation_id)
        self._send(200, result)

    def log_message(self, fmt, *args):  # quieter logs
        print(f"[http] {fmt % args}", flush=True)


def _start_video(drone_id: str, region: str, cred_conf: dict | None):
    from sim_video_producer import run_video_producer
    WORKER.ready.wait()  # don't stream until Isaac has loaded
    channel_name = f"drone-{drone_id}-{ENVIRONMENT}"
    print(f"[video] starting master on {channel_name} "
          f"(creds: {'IoT cert' if cred_conf else 'boto3 chain'})", flush=True)
    run_video_producer(channel_name, WORKER.frame_bus, region=region,
                       cred_conf=cred_conf)


def _start_mqtt(drone_id: str, iot_endpoint: str, certs_dir: str,
                thing_name: str, vehicle: str):
    """Connect to AWS IoT once Isaac is ready; commands + heartbeat over MQTT."""
    from sim_mqtt import SimMqtt
    WORKER.ready.wait()
    mqtt = SimMqtt(WORKER, drone_id=drone_id, iot_endpoint=iot_endpoint,
                   certs_dir=certs_dir, thing_name=thing_name,
                   vehicle_type=vehicle)
    try:
        mqtt.connect()
    except Exception as e:
        print(f"[mqtt] connect failed: {e}", flush=True)


def main():
    global WORKER
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="office",
                    help="sim scene: office|warehouse|hospital|hangar|outdoor")
    ap.add_argument("--drone-id", required=True)
    ap.add_argument("--vehicle", default="quadcopter",
                    choices=["quadcopter", "rover"])
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--no-mqtt", action="store_true",
                    help="skip the IoT MQTT command/heartbeat channel")
    ap.add_argument("--gui", action="store_true", help="run Isaac with a window")
    # IoT-cert auth (same mechanism as IRL drones) — no static AWS creds needed.
    ap.add_argument("--certs-dir",
                    help="dir with device.pem/private.key/root-ca.pem")
    ap.add_argument("--iot-endpoint",
                    default=iot_endpoint(),
                    help="IoT data-ATS endpoint (MQTT)")
    ap.add_argument("--credentials-endpoint",
                    default=credentials_endpoint(),
                    help="IoT credential provider endpoint")
    ap.add_argument("--video-role-alias", default="drone-video-role-alias-dev")
    ap.add_argument("--s3-role-alias", default="drone-s3-access-role-alias-dev")
    ap.add_argument("--images-bucket",
                    default="drone-images-dev-us-west-2-041686205727")
    ap.add_argument("--iot-thing-name",
                    help="IoT thing name for the cert (defaults to --drone-id)")
    # Local debug-only HTTP /execute (off by default; the real path is MQTT).
    ap.add_argument("--http-debug", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    thing_name = args.iot_thing_name or args.drone_id
    have_certs = bool(args.certs_dir and args.credentials_endpoint)

    # KVS video creds (IoT cert) and S3 photo-upload creds for capture_photo.
    cred_conf = None
    upload_conf = None
    if have_certs:
        cred_conf = {
            "certs_dir": args.certs_dir,
            "credentials_endpoint": args.credentials_endpoint,
            "video_role_alias": args.video_role_alias,
            "iot_thing_name": thing_name,
        }
        upload_conf = {
            "certs_dir": args.certs_dir,
            "credentials_endpoint": args.credentials_endpoint,
            "s3_role_alias": args.s3_role_alias,
            "images_bucket": args.images_bucket,
            "region": args.region,
            "thing_name": thing_name,
            "drone_id": args.drone_id,
        }

    print(f"== eco sim bridge == env={args.env} drone={args.drone_id} "
          f"vehicle={args.vehicle} mqtt={not args.no_mqtt}", flush=True)

    # Isaac's SimulationApp installs a SIGINT handler, which only works on the
    # main thread — so the sim worker MUST run on the main thread. MQTT, video,
    # and the optional debug HTTP server run on background threads.
    WORKER = SimWorker(environment=args.env, vehicle_type=args.vehicle,
                       headless=not args.gui, upload_conf=upload_conf)

    server = None
    if args.http_debug:
        server = ThreadingHTTPServer(("0.0.0.0", args.port), ExecuteHandler)
        threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
        print(f"[main] /execute debug server on :{args.port}", flush=True)

    if not args.no_mqtt:
        if not have_certs:
            print("[main] --no-mqtt not set but no certs given; MQTT disabled",
                  flush=True)
        else:
            threading.Thread(
                target=_start_mqtt,
                args=(args.drone_id, args.iot_endpoint, args.certs_dir,
                      thing_name, args.vehicle),
                name="mqtt", daemon=True).start()

    if not args.no_video:
        threading.Thread(target=_start_video,
                         args=(args.drone_id, args.region, cred_conf),
                         name="video", daemon=True).start()

    print("[main] loading Isaac…", flush=True)
    try:
        WORKER.run()  # blocks on the main thread; owns Isaac Sim
    except KeyboardInterrupt:
        pass
    finally:
        WORKER.stop()
        if server:
            server.shutdown()


if __name__ == "__main__":
    main()
