"""
Full local-stack smoke test: a real mosquitto broker + the real gcs/server.py
(as a subprocess) + a stub OpenAI-compatible model server + a fake MQTT
"drone" client, driven entirely over HTTP/MQTT exactly like a real app and a
real drone would. No AWS, no Bedrock, no network egress.

This is the "no hardware needed" verification for the whole GCS mode change:
if this test passes, register/status/chat all work end-to-end through
aws/src's unmodified production handlers running against local storage and a
local model.

Requires mosquitto installed (e.g. `brew install mosquitto` /
`apt install mosquitto`) and reachable as `mosquitto` on PATH, or set
MOSQUITTO_BIN to its full path.

Run: cd eco/gcs && python3 -m pytest tests/test_local_stack.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

GCS_DIR = Path(__file__).resolve().parent.parent
MOSQUITTO_BIN = os.environ.get("MOSQUITTO_BIN") or shutil.which("mosquitto") or \
    "/opt/homebrew/opt/mosquitto/sbin/mosquitto"

pytestmark = pytest.mark.skipif(
    not (shutil.which("mosquitto") or Path(MOSQUITTO_BIN).exists()),
    reason="mosquitto not installed - see this file's docstring",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"nothing listening on {host}:{port} after {timeout}s")


def _http(method: str, url: str, token: str = None, body: dict = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


# --- stub OpenAI-compatible model server (same pattern as aws/src/test_llm.py) --

class _StubHandler(BaseHTTPRequestHandler):
    next_content = '{"action": "execute", "code": "takeoff(3)\\nland()", "message": "Executing flight command..."}'

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            self._reply(200, {"data": [{"id": "stub-model"}]})
        else:
            self._reply(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # drain, not inspected
        self._reply(200, {"choices": [{"message": {"content": _StubHandler.next_content}}]})

    def _reply(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def stub_llm_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/v1"
    server.shutdown()


# --- real mosquitto broker, test-only anonymous access ------------------------

@pytest.fixture(scope="module")
def mosquitto_broker(tmp_path_factory):
    port = _free_port()
    conf_dir = tmp_path_factory.mktemp("mosquitto")
    conf_path = conf_dir / "mosquitto.conf"
    conf_path.write_text(f"listener {port}\nallow_anonymous true\npersistence false\n")

    proc = subprocess.Popen(
        [MOSQUITTO_BIN, "-c", str(conf_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        _wait_for_port("127.0.0.1", port)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# --- the real GCS server, as a subprocess -------------------------------------

@pytest.fixture
def gcs_instance(tmp_path, mosquitto_broker, stub_llm_server):
    http_port = _free_port()
    data_dir = tmp_path / "gcs-data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"""
data_dir: {data_dir}
http:
  host: 127.0.0.1
  port: {http_port}
mqtt:
  host: 127.0.0.1
  port: {mosquitto_broker}
  username: null
  password: null
llm:
  provider: openai
  base_url: {stub_llm_server}
operators:
  - tester
""")

    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, "server.py", "--config", str(config_path)],
        cwd=str(GCS_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    base_url = f"http://127.0.0.1:{http_port}"
    try:
        _wait_for_port("127.0.0.1", http_port, timeout=15)
        deadline = time.time() + 10
        last_err = None
        while time.time() < deadline:
            try:
                status, _ = _http("GET", f"{base_url}/healthz")
                if status == 200:
                    break
            except Exception as e:
                last_err = e
            time.sleep(0.2)
        else:
            raise TimeoutError(f"GCS server never became healthy: {last_err}")

        auth_data = json.loads((data_dir / "auth.json").read_text())
        token = auth_data["operators"]["tester"]

        yield {"base_url": base_url, "token": token, "mqtt_port": mosquitto_broker, "proc": proc}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_healthz(gcs_instance):
    status, body = _http("GET", f"{gcs_instance['base_url']}/healthz")
    assert status == 200
    assert body["status"] == "ok"


def test_unauthenticated_request_rejected(gcs_instance):
    status, _ = _http("GET", f"{gcs_instance['base_url']}/drones")
    assert status == 401


def test_full_flow_register_status_message_response(gcs_instance):
    """The end-to-end path this whole change exists for: an app registers a
    drone, the drone heartbeats over MQTT, the app sends a chat message that
    reaches a local model (the stub) and gets published back out over MQTT as
    a drone command, and a simulated drone response round-trips into the
    conversation history - all through aws/src's unmodified handlers."""
    import paho.mqtt.client as mqtt

    base_url = gcs_instance["base_url"]
    token = gcs_instance["token"]
    mqtt_port = gcs_instance["mqtt_port"]
    drone_id = "sim-q1"

    # 1. Register the drone (pure DynamoDB-shim path, no MQTT/LLM involved).
    status, body = _http("POST", f"{base_url}/drones", token=token, body={"droneId": drone_id})
    assert status == 201, body

    # 2. A fake drone heartbeats over real MQTT -> rules.py -> store_status_handler.
    heartbeat_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    heartbeat_client.connect("127.0.0.1", mqtt_port)
    heartbeat_client.loop_start()
    heartbeat_client.publish(f"drone/{drone_id}/status", json.dumps({"battery": 95}), qos=1)

    deadline = time.time() + 5
    online = False
    while time.time() < deadline:
        status, body = _http("GET", f"{base_url}/drones/{drone_id}/status", token=token)
        if status == 200 and body.get("status", {}).get("droneId") == drone_id:
            online = True
            break
        time.sleep(0.2)
    assert online, "status heartbeat never landed via MQTT -> rules.py -> DynamoDB shim"

    # 3. Subscribe as if we were the drone, BEFORE sending the chat message,
    # so we can observe the command the GCS publishes.
    received = []
    command_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

    def on_message(client, userdata, message):
        received.append((message.topic, json.loads(message.payload)))

    command_client.on_message = on_message
    command_client.connect("127.0.0.1", mqtt_port)
    command_client.subscribe(f"drone/{drone_id}/chat/+/command", qos=1)
    command_client.loop_start()

    # 4. Create a conversation and send a message - this is the real
    # conversations.create_handler / message_handler, calling the real
    # llm.invoke() against the stub server configured above.
    status, body = _http("POST", f"{base_url}/drones/{drone_id}/conversations", token=token, body={})
    assert status in (200, 201), body
    conversation_id = body["conversation_id"]

    status, body = _http(
        "POST", f"{base_url}/drones/{drone_id}/conversations/{conversation_id}/messages",
        token=token, body={"message": "take off to 3 meters and land"},
    )
    assert status == 200, body
    assert body["status"] == "sent", body

    deadline = time.time() + 5
    while time.time() < deadline and not received:
        time.sleep(0.2)
    assert received, "no command was published to MQTT for the app's chat message"
    topic, payload = received[0]
    assert topic == f"drone/{drone_id}/chat/{conversation_id}/command"
    assert payload["action"] == "execute"
    assert "takeoff" in payload["code"]

    # 5. Simulate the drone's response landing back over MQTT -> rules.py ->
    # conversations.response_handler -> saved into conversation history.
    response_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    response_client.connect("127.0.0.1", mqtt_port)
    response_client.loop_start()
    response_client.publish(
        f"drone/{drone_id}/chat/{conversation_id}/response",
        json.dumps({
            "droneId": drone_id,
            "conversation_id": conversation_id,
            "result": {"success": True, "stdout": "landed safely"},
            "image_urls": [],
            "original_message": "take off to 3 meters and land",
        }),
        qos=1,
    )

    deadline = time.time() + 5
    found = False
    while time.time() < deadline:
        status, body = _http(
            "GET", f"{base_url}/drones/{drone_id}/conversations/{conversation_id}", token=token,
        )
        if status == 200:
            texts = [m.get("content", {}).get("text", "") for m in body.get("messages", [])]
            if any("landed safely" in t for t in texts):
                found = True
                break
        time.sleep(0.2)
    assert found, "drone's MQTT response never showed up in conversation history"

    for c in (heartbeat_client, command_client, response_client):
        c.loop_stop()
        c.disconnect()
