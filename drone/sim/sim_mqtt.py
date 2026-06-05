"""AWS IoT MQTT client for the eco sim host — makes a sim drone behave like IRL.

Instead of receiving commands over inbound HTTP (which needs a public tunnel),
the sim bridge connects *outbound* to AWS IoT Core with its device cert, exactly
like eco/drone/common/daemon.py. It subscribes to the same command topics,
executes generated code through the Isaac SimWorker, and publishes responses and
heartbeats back over MQTT. No tunnel anywhere.

Mirrors daemon.py: mtls_from_path connection, drone/{id}/chat/+/command +
command + video/command subscriptions, drone/{id}/chat/{cid}/response replies,
drone/{id}/status heartbeats.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from awscrt import mqtt
from awsiot import mqtt_connection_builder


class SimMqtt:
    HEARTBEAT_INTERVAL = 5  # seconds

    def __init__(self, worker, drone_id: str, iot_endpoint: str, certs_dir: str,
                 thing_name: str, vehicle_type: str = "quadcopter"):
        self.worker = worker
        self.drone_id = drone_id
        self.iot_endpoint = iot_endpoint
        self.certs_dir = certs_dir.rstrip("/")
        self.thing_name = thing_name
        self.vehicle_type = vehicle_type
        self.conn = None
        self._running = True

    # -- connection -------------------------------------------------------------
    def connect(self) -> None:
        client_id = f"{self.drone_id}-{int(time.time())}"
        self.conn = mqtt_connection_builder.mtls_from_path(
            endpoint=self.iot_endpoint,
            port=8883,
            cert_filepath=f"{self.certs_dir}/device.pem",
            pri_key_filepath=f"{self.certs_dir}/private.key",
            ca_filepath=f"{self.certs_dir}/root-ca.pem",
            client_id=client_id,
            clean_session=False,
            keep_alive_secs=120,
        )
        self.conn.connect().result()
        print(f"[mqtt] connected as {client_id}", flush=True)

        for topic, cb in [
            (f"drone/{self.drone_id}/chat/+/command", self._on_chat),
            (f"drone/{self.drone_id}/command", self._on_chat),
            (f"drone/{self.drone_id}/video/command", self._on_video),
        ]:
            self.conn.subscribe(topic=topic, qos=mqtt.QoS.AT_LEAST_ONCE,
                                callback=cb)[0].result()
            print(f"[mqtt] subscribed {topic}", flush=True)

        threading.Thread(target=self._heartbeat_loop, name="heartbeat",
                         daemon=True).start()

    def _publish(self, topic: str, payload: dict) -> None:
        self.conn.publish(topic=topic, payload=json.dumps(payload),
                          qos=mqtt.QoS.AT_LEAST_ONCE)

    # -- handlers ---------------------------------------------------------------
    def _on_chat(self, topic: str, payload: bytes, **kwargs) -> None:
        try:
            data = json.loads(payload)
        except Exception as e:
            print(f"[mqtt] bad command payload: {e}", flush=True)
            return
        # conversation_id from the topic (drone/{id}/chat/{cid}/command) or body
        parts = topic.split("/")
        conversation_id = (parts[3] if len(parts) >= 5 and parts[2] == "chat"
                           else data.get("conversation_id", "sim"))
        action = data.get("action", "execute")
        original = data.get("original_message", "")
        print(f"[mqtt] {action} on conv {conversation_id}", flush=True)

        code = data.get("code")
        if action == "set_goal" and not code:
            # Best-effort: legacy goal path. Without code we can't navigate
            # precisely; acknowledge so the app isn't left hanging.
            self._respond(conversation_id, original,
                          {"success": True, "stdout": "", "error": None,
                           "image_urls": []}, [])
            return
        if action == "mission" and not code:
            self._respond(conversation_id, original,
                          {"success": True, "stdout": "Mission acknowledged (sim).",
                           "error": None, "image_urls": []}, [])
            return
        if not code:
            self._respond(conversation_id, original,
                          {"success": False, "stdout": "", "error": "no code",
                           "image_urls": []}, [])
            return

        result = self.worker.submit(code, conversation_id)
        res = {
            "success": result.get("success", False),
            "stdout": result.get("stdout", ""),
            "error": result.get("error"),
            "image_urls": result.get("image_urls", []),
            "video_urls": result.get("video_urls", []),
        }
        self._respond(conversation_id, original, res, res["image_urls"],
                      video_urls=res["video_urls"],
                      follow_up=data.get("follow_up", False))

    def _respond(self, conversation_id: str, original: str, result: dict,
                 image_urls: list, video_urls: list = None,
                 follow_up: bool = False) -> None:
        topic = f"drone/{self.drone_id}/chat/{conversation_id}/response"
        self._publish(topic, {
            "droneId": self.drone_id,
            "conversation_id": conversation_id,
            "original_message": original,
            "result": result,
            "image_urls": image_urls,
            "video_urls": video_urls or [],
            "follow_up": follow_up,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        print(f"[mqtt] responded conv {conversation_id} "
              f"(success={result.get('success')}, imgs={len(image_urls)})", flush=True)

    def _on_video(self, topic: str, payload: bytes, **kwargs) -> None:
        # The KVS master is always-on, so just acknowledge start/stop/heartbeat.
        try:
            action = json.loads(payload).get("action")
        except Exception:
            action = None
        if action in ("start", "stop"):
            self._publish(f"drone/{self.drone_id}/video/status",
                          {"droneId": self.drone_id, "streaming": True,
                           "timestamp": datetime.now(timezone.utc).isoformat()})

    # -- heartbeat --------------------------------------------------------------
    def _heartbeat_loop(self) -> None:
        topic = f"drone/{self.drone_id}/status"
        while self._running:
            now_ms = int(time.time() * 1000)
            try:
                self._publish(topic, {
                    "droneId": self.drone_id,
                    "status": "online",
                    "lastUpdate": now_ms,
                    "ttl": int(time.time()) + 30,
                    "armed": bool(getattr(self.worker, "_armed", False)),
                    "battery": 100,
                    "variant": "sim",
                    "capabilities": {"variant": "sim", "vlm_available": False,
                                     "nav2_available": False,
                                     "vehicle_type": self.vehicle_type},
                })
            except Exception as e:
                print(f"[mqtt] heartbeat failed: {e}", flush=True)
            time.sleep(self.HEARTBEAT_INTERVAL)

    def stop(self) -> None:
        self._running = False
        try:
            if self.conn:
                self.conn.disconnect().result()
        except Exception:
            pass
