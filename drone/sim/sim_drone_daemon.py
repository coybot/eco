#!/usr/bin/env python3
"""One simulated drone = one process with ONE MQTT connection (exactly like IRL).

Receives commands on AWS IoT, runs them against the shared Isaac world via the
engine IPC socket, publishes responses/heartbeat, and serves on-demand KVS video.
Because each drone is its own process, there is no GIL contention between many
MQTT connections — the thing that broke the single-process fleet.

    python3 sim_drone_daemon.py --drone-id sim-quadcopter-001 --vehicle quadcopter \
        --sock /tmp/sim_engine.sock --certs-dir ~/eco-certs-fleet/sim-quadcopter-001
"""

from __future__ import annotations

import argparse
import json
import queue
import ssl
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from engine_client import EngineClient
from sim_control import run_command

try:  # packaged (drone.sim) in the repo; flat (sim/ on path) when run as a script
    from .endpoints import iot_endpoint, credentials_endpoint
except ImportError:
    from endpoints import iot_endpoint, credentials_endpoint


def _make_client(cid):
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=cid, clean_session=True)
    except (AttributeError, TypeError):
        return mqtt.Client(client_id=cid, clean_session=True)


class DroneDaemon:
    def __init__(self, args):
        self.id = args.drone_id
        self.vtype = args.vehicle
        self.region = args.region
        self.control_plane = args.control_plane
        self.certs_dir = args.certs_dir.rstrip("/") if args.certs_dir else None
        self.iot_endpoint = args.iot_endpoint
        self.mqtt_host = args.mqtt_host
        self.mqtt_port = args.mqtt_port
        self.mqtt_username = args.mqtt_username or self.id
        self.mqtt_password = args.mqtt_password
        self.mqtt_tls = args.mqtt_tls
        self.engine = EngineClient(args.sock)
        self.armed = {"v": False}
        self.upload_conf = {
            "certs_dir": self.certs_dir, "credentials_endpoint": args.credentials_endpoint,
            "s3_role_alias": args.s3_role_alias, "images_bucket": args.images_bucket,
            "region": args.region, "thing_name": self.id,
            "control_plane": self.control_plane, "images_base_url": args.images_base_url,
            # this drone's GCS pairing token (== its MQTT password), reused as
            # the Bearer token for photo-upload HTTP auth - see gcs/http_api.py
            "auth_token": self.mqtt_password}
        self.video_conf = {
            "certs_dir": self.certs_dir, "credentials_endpoint": args.credentials_endpoint,
            "video_role_alias": args.video_role_alias, "iot_thing_name": self.id}
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._video = None
        self._running = True

    def start(self):
        c = _make_client(self.id)
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.reconnect_delay_set(1, 30)

        if self.control_plane == "gcs":
            # Local GCS mosquitto broker: username/password, plain TCP by
            # default, or TLS (--mqtt-tls) with a self-signed CA if the
            # broker's mosquitto.conf enables that listener.
            if self.mqtt_username:
                c.username_pw_set(self.mqtt_username, self.mqtt_password)
            if self.mqtt_tls:
                c.tls_set(tls_version=ssl.PROTOCOL_TLSv1_2)
            c.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
        else:
            c.tls_set(ca_certs=f"{self.certs_dir}/root-ca.pem",
                      certfile=f"{self.certs_dir}/device.pem",
                      keyfile=f"{self.certs_dir}/private.key",
                      tls_version=ssl.PROTOCOL_TLSv1_2)
            c.connect(self.iot_endpoint, 8883, keepalive=60)

        self.client = c
        c.loop_start()
        threading.Thread(target=self._exec_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        print(f"[{self.id}] daemon up ({self.vtype}, control_plane={self.control_plane})", flush=True)

    def _on_connect(self, client, userdata, flags, rc, *a):
        if rc == 0:
            client.subscribe([(f"drone/{self.id}/chat/+/command", 1),
                              (f"drone/{self.id}/command", 1),
                              (f"drone/{self.id}/video/command", 1)])

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        try:
            data = json.loads(msg.payload)
        except Exception:
            return
        if "video" in parts:
            act = data.get("action")
            if act == "start":
                self._start_video()
            return
        conv = parts[3] if len(parts) >= 5 and parts[2] == "chat" else data.get("conversation_id", "c")
        # Carry follow_up so the cloud's image-analysis loop (response_handler) fires after a
        # `look`/capture. The IRL daemon echoes this; without it, sim `look` returns a raw image
        # with no Claude analysis.
        self._q.put((conv, data.get("code"), data.get("original_message", ""),
                     bool(data.get("follow_up", False))))

    def _exec_loop(self):
        while self._running:
            try:
                conv, code, original, follow_up = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            if not code:
                self._respond(conv, original, {"success": True, "stdout": "ack (sim).",
                                               "error": None, "image_urls": []}, follow_up)
                continue
            print(f"[{self.id}] exec on conv {conv}", flush=True)
            res = run_command(self.engine, self.id, self.vtype, conv, code,
                              self.upload_conf, self.armed)
            self._respond(conv, original, {
                "success": res.get("success", False), "stdout": res.get("stdout", ""),
                "error": res.get("error"), "image_urls": res.get("image_urls", []),
                "video_urls": res.get("video_urls", [])}, follow_up)

    def _respond(self, conv, original, result, follow_up=False):
        self.client.publish(f"drone/{self.id}/chat/{conv}/response", json.dumps({
            "droneId": self.id, "conversation_id": conv, "original_message": original,
            "result": result, "image_urls": result.get("image_urls", []),
            "video_urls": result.get("video_urls", []),
            "follow_up": bool(follow_up),
            "timestamp": datetime.now(timezone.utc).isoformat()}), qos=1)
        print(f"[{self.id}] responded conv {conv} (success={result.get('success')}, "
              f"imgs={len(result.get('image_urls', []))})", flush=True)

    def _heartbeat_loop(self):
        while self._running:
            self.client.publish(f"drone/{self.id}/status", json.dumps({
                "droneId": self.id, "status": "online",
                "lastUpdate": int(time.time() * 1000), "ttl": int(time.time()) + 30,
                "armed": self.armed["v"], "battery": 100, "variant": "sim",
                "capabilities": {"variant": "sim", "vlm_available": False,
                                 "nav2_available": False, "vehicle_type": self.vtype}}),
                qos=0)
            time.sleep(5)

    def _start_video(self):
        if self._video:
            return
        from sim_sdk import FrameBus
        from sim_video_producer import run_video_producer
        import cv2
        import numpy as np
        bus = FrameBus()
        ch = f"drone-{self.id}-dev"

        def _poll():  # feed frames from the engine into the bus
            while self._running:
                jpg = self.engine.grab_jpeg(self.id)
                if jpg:
                    arr = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                    if arr is not None:
                        bus.set(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))
                time.sleep(1 / 15.0)

        threading.Thread(target=_poll, daemon=True).start()
        threading.Thread(
            target=lambda: run_video_producer(ch, bus, region=self.region,
                                              cred_conf=self.video_conf),
            daemon=True).start()
        self._video = ch
        print(f"[{self.id}] video master {ch}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drone-id", required=True)
    ap.add_argument("--vehicle", default="quadcopter", choices=["quadcopter", "rover"])
    ap.add_argument("--sock", default="tcp://127.0.0.1:9999",
                    help="Engine IPC: Unix socket path or tcp://host:port (Godot default)")
    ap.add_argument("--control-plane", default="aws", choices=["aws", "gcs"],
                    help="aws (default, AWS IoT Core mTLS) or gcs (a local Ground Control "
                         "Station's mosquitto broker - see eco/gcs/README.md)")
    ap.add_argument("--certs-dir", default=None,
                    help="required for --control-plane aws; unused for gcs")
    ap.add_argument("--iot-endpoint",
                    default=iot_endpoint())
    ap.add_argument("--credentials-endpoint",
                    default=credentials_endpoint())
    ap.add_argument("--video-role-alias", default="drone-video-role-alias-dev")
    ap.add_argument("--s3-role-alias", default="drone-s3-access-role-alias-dev")
    ap.add_argument("--images-bucket", default="drone-images-dev-us-west-2-041686205727")
    ap.add_argument("--region", default="us-west-2")
    # --control-plane gcs options
    ap.add_argument("--mqtt-host", default="127.0.0.1", help="GCS mosquitto broker host")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--mqtt-username", default=None, help="defaults to --drone-id")
    ap.add_argument("--mqtt-password", default=None, help="this drone's GCS pairing token")
    ap.add_argument("--mqtt-tls", action="store_true",
                    help="use TLS to the GCS broker (default: plain TCP, matching gcs/config.yaml.example's mqtt.tls: false)")
    ap.add_argument("--images-base-url", default=None, help="the GCS's own HTTP API base URL, for photo uploads")
    args = ap.parse_args()

    if args.control_plane == "aws" and not args.certs_dir:
        ap.error("--certs-dir is required for --control-plane aws")

    DroneDaemon(args).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
