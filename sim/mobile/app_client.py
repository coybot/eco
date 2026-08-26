"""Headless stand-in for the iOS DroneOperator app.

Mirrors the app's two channels:
  * **Cloud API** — ``POST {API_BASE}/command`` with ``{drone_id, command}`` and an
    ``Authorization`` bearer token. The cloud (Bedrock) turns the natural-language
    mission into drone code and publishes it; this is exactly what the app does when
    a user types a message.
  * **IoT MQTT** — subscribes (like ``MQTTService.swift``) to
    ``drone/{id}/chat/+/response`` and ``drone/{id}/status`` to learn when drones are
    online and to receive the response payload (``image_urls`` / ``video_urls``).

On Hoopoe the MQTT side authenticates with an IoT device/fleet certificate (the
wildcard ``drone-policy-dev`` allows subscribing to ``drone/+/...``), so no Cognito
is needed for the self-contained path. The API side needs a bearer token
(``ISHMAEL_API_TOKEN``) — a Cognito id/access token for the owning user.

Config (all via env, with sane defaults):
  ISHMAEL_API_BASE       cloud API base URL (e.g. https://xxx.execute-api...prod)
  ISHMAEL_API_TOKEN      bearer token for the API authorizer
  ISHMAEL_IOT_ENDPOINT   AWS IoT ATS data endpoint
  ISHMAEL_MOBILE_CERTS   dir with device.pem/private.key/root-ca.pem (default ~/eco-certs)
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional


class AppClient:
    def __init__(self, drone_ids: list[str], out_dir: str = "~/videos/ishmael",
                 api_base: Optional[str] = None, api_token: Optional[str] = None,
                 iot_endpoint: Optional[str] = None, certs_dir: Optional[str] = None):
        self.drone_ids = list(drone_ids)
        self.out_dir = os.path.expanduser(out_dir)
        self.api_base = (api_base or os.environ.get("ISHMAEL_API_BASE", "")).rstrip("/")
        self.api_token = api_token or os.environ.get("ISHMAEL_API_TOKEN", "")
        # Not part of the drone/ wheel, so it reads the environment directly
        # rather than importing drone.sim.endpoints; sourcing secrets.env sets
        # IOT_ENDPOINT for both. See secrets.env.example.
        self.iot_endpoint = (
            iot_endpoint
            or os.environ.get("ISHMAEL_IOT_ENDPOINT")
            or os.environ.get("IOT_ENDPOINT")
            or "YOUR_IOT_ENDPOINT.iot.us-west-2.amazonaws.com")
        self.certs_dir = os.path.expanduser(
            certs_dir or os.environ.get("ISHMAEL_MOBILE_CERTS", "~/eco-certs"))
        self.conn = None
        self._lock = threading.Lock()
        self._online: set[str] = set()
        self._responses: dict[str, dict] = {}   # droneId -> latest response payload

    # -- MQTT (receive) ---------------------------------------------------------
    def connect(self) -> None:
        from awscrt import mqtt
        from awsiot import mqtt_connection_builder
        client_id = f"ishmael-app-{int(time.time())}"
        self.conn = mqtt_connection_builder.mtls_from_path(
            endpoint=self.iot_endpoint, port=8883,
            cert_filepath=f"{self.certs_dir}/device.pem",
            pri_key_filepath=f"{self.certs_dir}/private.key",
            ca_filepath=f"{self.certs_dir}/root-ca.pem",
            client_id=client_id, clean_session=True, keep_alive_secs=60,
        )
        self.conn.connect().result()
        for did in self.drone_ids:
            self.conn.subscribe(topic=f"drone/{did}/chat/+/response",
                                qos=mqtt.QoS.AT_LEAST_ONCE,
                                callback=self._on_response)[0].result()
            self.conn.subscribe(topic=f"drone/{did}/status",
                                qos=mqtt.QoS.AT_LEAST_ONCE,
                                callback=self._on_status)[0].result()
        print(f"[app] connected ({client_id}); watching {len(self.drone_ids)} drones",
              flush=True)

    def _on_status(self, topic: str, payload: bytes, **kw) -> None:
        try:
            data = json.loads(payload)
        except Exception:
            return
        did = data.get("droneId") or topic.split("/")[1]
        if data.get("status") == "online":
            with self._lock:
                self._online.add(did)

    def _on_response(self, topic: str, payload: bytes, **kw) -> None:
        try:
            data = json.loads(payload)
        except Exception:
            return
        did = data.get("droneId") or topic.split("/")[1]
        result = data.get("result", {}) or {}
        # the iOS app reads image_urls from the top level or inside result
        image_urls = data.get("image_urls") or result.get("image_urls") or []
        video_urls = data.get("video_urls") or result.get("video_urls") or []
        with self._lock:
            self._responses[did] = {
                "droneId": did, "image_urls": image_urls, "video_urls": video_urls,
                "success": result.get("success"), "stdout": result.get("stdout", ""),
                "error": result.get("error"), "raw": data,
            }
        print(f"[app] response from {did}: imgs={len(image_urls)} "
              f"vids={len(video_urls)} ok={result.get('success')}", flush=True)

    def wait_online(self, timeout: float = 240.0) -> list[str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                got = set(self._online)
            if got.issuperset(set(self.drone_ids)):
                break
            time.sleep(2)
        with self._lock:
            return sorted(self._online & set(self.drone_ids))

    # -- API (send) -------------------------------------------------------------
    def _send_command(self, drone_id: str, command: str) -> dict:
        import urllib.request
        if not self.api_base:
            raise RuntimeError("ISHMAEL_API_BASE not set; cannot dispatch missions")
        body = json.dumps({"drone_id": drone_id, "command": command}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = self.api_token
        req = urllib.request.Request(self.api_base + "/command", data=body,
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    def send_mission(self, command: str, timeout: float = 240.0) -> list[dict]:
        """Send the mission to every drone and wait for their responses."""
        with self._lock:
            self._responses.clear()
        for did in self.drone_ids:
            try:
                self._send_command(did, command)
                print(f"[app] dispatched to {did}: {command!r}", flush=True)
            except Exception as e:
                print(f"[app] dispatch to {did} failed: {e}", flush=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if set(self._responses).issuperset(set(self.drone_ids)):
                    break
            time.sleep(2)
        with self._lock:
            return list(self._responses.values())

    # -- media ------------------------------------------------------------------
    def download_media(self, urls: list[str]) -> list[str]:
        import urllib.request
        Path(self.out_dir).mkdir(parents=True, exist_ok=True)
        saved = []
        for i, url in enumerate(urls):
            if not url or not url.startswith("http"):
                continue
            ext = ".mp4" if ".mp4" in url.split("?")[0] else ".jpg"
            dest = os.path.join(self.out_dir, f"media_{int(time.time())}_{i}{ext}")
            try:
                urllib.request.urlretrieve(url, dest)
                saved.append(dest)
                print(f"[app] saved {dest}", flush=True)
            except Exception as e:
                print(f"[app] download failed {url}: {e}", flush=True)
        return saved

    def close(self) -> None:
        try:
            if self.conn:
                self.conn.disconnect().result()
        except Exception:
            pass
