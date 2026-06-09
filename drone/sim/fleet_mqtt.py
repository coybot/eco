"""Fleet MQTT via paho-mqtt: one client per vehicle (like IRL drones).

Each sim drone gets its own paho-mqtt client (its own cert + loop thread),
subscribes to its own command topics, and publishes its own heartbeat/responses
— identical to a real drone. paho's per-client loop thread handles many
simultaneous connections reliably (awscrt went deaf past a few connections in
one process). Command execution funnels through one shared FleetWorker (one
Isaac world) via an executor pool.
"""

from __future__ import annotations

import json
import queue
import ssl
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from fleet_worker import run_command


def _make_client(client_id):
    """paho v1/v2 compatible client (v1 callback signatures)."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id,
                           clean_session=True)
    except (AttributeError, TypeError):
        return mqtt.Client(client_id=client_id, clean_session=True)


class FleetMqtt:
    HEARTBEAT = 5  # s

    def __init__(self, worker, ids, iot_endpoint, certs_base, region,
                 credentials_endpoint, video_role_alias, s3_role_alias, images_bucket):
        self.worker = worker
        self.ids = list(ids)
        self.vtype = {d: worker.bridge.vehicles[d].vtype for d in ids}
        self.iot_endpoint = iot_endpoint
        self.certs_base = certs_base.rstrip("/")
        self.region = region
        self.credentials_endpoint = credentials_endpoint
        self.video_role_alias = video_role_alias
        self.s3_role_alias = s3_role_alias
        self.images_bucket = images_bucket
        self.conns: dict[str, object] = {}
        self._video_threads: dict[str, threading.Thread] = {}
        self._running = True
        self._cmd_q: "queue.Queue[tuple]" = queue.Queue()
        self._pool_size = 12

    # -- connections ------------------------------------------------------------
    def connect(self):
        for did in self.ids:
            cd = f"{self.certs_base}/{did}"
            c = _make_client(did)
            c.tls_set(ca_certs=f"{cd}/root-ca.pem", certfile=f"{cd}/device.pem",
                      keyfile=f"{cd}/private.key", tls_version=ssl.PROTOCOL_TLSv1_2)
            c.on_connect = self._make_on_connect(did)
            c.on_message = self._on_message
            c.reconnect_delay_set(min_delay=1, max_delay=30)
            c.connect(self.iot_endpoint, 8883, keepalive=60)
            c.loop_start()  # background thread per client
            self.conns[did] = c
        print(f"[fleet-mqtt] started {len(self.conns)} paho clients", flush=True)
        threading.Thread(target=self._heartbeat_loop, name="hb", daemon=True).start()
        for i in range(self._pool_size):
            threading.Thread(target=self._executor_loop, name=f"exec-{i}",
                             daemon=True).start()

    def _make_on_connect(self, did):
        def _on_connect(client, userdata, flags, rc, *a):
            if rc != 0:
                print(f"[fleet-mqtt] {did} connect rc={rc}", flush=True)
                return
            client.subscribe([(f"drone/{did}/chat/+/command", 1),
                              (f"drone/{did}/video/command", 1)])
        return _on_connect

    def _executor_loop(self):
        while self._running:
            try:
                did, conv, code, original = self._cmd_q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._run_and_respond(did, conv, code, original)
            except Exception as e:
                print(f"[fleet-mqtt] exec error {did}: {e}", flush=True)

    # -- command intake ---------------------------------------------------------
    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) < 5 or parts[2] != "chat":
            if len(parts) >= 3 and parts[2] == "video":
                self._on_video(parts[1], msg.payload)
            return
        did, conv = parts[1], parts[3]
        try:
            data = json.loads(msg.payload)
        except Exception:
            return
        self._cmd_q.put((did, conv, data.get("code"), data.get("original_message", "")))

    def _run_and_respond(self, did, conv, code, original):
        if not code:
            self._respond(did, conv, original, {"success": True, "stdout": "ack (sim).",
                                                "error": None, "image_urls": []}, [])
            return
        print(f"[fleet-mqtt] {did} exec on conv {conv}", flush=True)
        upload_conf = {
            "certs_dir": f"{self.certs_base}/{did}",
            "credentials_endpoint": self.credentials_endpoint,
            "s3_role_alias": self.s3_role_alias, "images_bucket": self.images_bucket,
            "region": self.region, "thing_name": did}
        res = run_command(self.worker, did, conv, code, upload_conf)
        self._respond(did, conv, original, {
            "success": res.get("success", False), "stdout": res.get("stdout", ""),
            "error": res.get("error"), "image_urls": res.get("image_urls", [])},
            res.get("image_urls", []))

    def _respond(self, did, conv, original, result, image_urls):
        try:
            self.conns[did].publish(
                f"drone/{did}/chat/{conv}/response",
                json.dumps({"droneId": did, "conversation_id": conv,
                            "original_message": original, "result": result,
                            "image_urls": image_urls,
                            "timestamp": datetime.now(timezone.utc).isoformat()}), qos=1)
        except Exception as e:
            print(f"[fleet-mqtt] respond {did} failed: {e}", flush=True)
        print(f"[fleet-mqtt] {did} responded conv {conv} "
              f"(success={result.get('success')}, imgs={len(image_urls)})", flush=True)

    # -- on-demand video --------------------------------------------------------
    def _on_video(self, did, payload):
        try:
            action = json.loads(payload).get("action")
        except Exception:
            action = None
        if action == "start":
            self._start_video(did)
        elif action == "stop":
            self.worker.unwatch(did)

    def _start_video(self, did):
        bus = self.worker.watch(did)
        if did in self._video_threads:
            return
        from sim_video_producer import run_video_producer
        ch = f"drone-{did}-dev"
        video_conf = {"certs_dir": f"{self.certs_base}/{did}",
                      "credentials_endpoint": self.credentials_endpoint,
                      "video_role_alias": self.video_role_alias, "iot_thing_name": did}

        def _run():
            print(f"[fleet-mqtt] video master {ch}", flush=True)
            run_video_producer(ch, bus, region=self.region, cred_conf=video_conf)
        t = threading.Thread(target=_run, name=f"vid-{did}", daemon=True)
        self._video_threads[did] = t
        t.start()

    # -- heartbeats -------------------------------------------------------------
    def _heartbeat_loop(self):
        while self._running:
            now_ms = int(time.time() * 1000)
            for did in self.ids:
                with self.worker.bridge.vehicles[did].lock:
                    armed = self.worker.bridge.vehicles[did].armed
                try:
                    self.conns[did].publish(
                        f"drone/{did}/status",
                        json.dumps({"droneId": did, "status": "online",
                                    "lastUpdate": now_ms, "ttl": int(time.time()) + 30,
                                    "armed": armed, "battery": 100, "variant": "sim",
                                    "capabilities": {"variant": "sim",
                                                     "vlm_available": False,
                                                     "nav2_available": False,
                                                     "vehicle_type": self.vtype[did]}}),
                        qos=0)
                except Exception as e:
                    print(f"[fleet-mqtt] hb {did} failed: {e}", flush=True)
            time.sleep(self.HEARTBEAT)

    def stop(self):
        self._running = False
        for c in self.conns.values():
            try:
                c.loop_stop(); c.disconnect()
            except Exception:
                pass
