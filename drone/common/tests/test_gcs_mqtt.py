"""
Unit/integration tests for drone/common/gcs_mqtt.py's PahoMqttAdapter - the
control_plane: gcs counterpart of daemon.py's AWS IoT mqtt_connection, used
when a drone points at a local Ground Control Station instead of the cloud
(see eco/gcs/README.md).

Exercises the adapter against a REAL mosquitto broker (subprocess), the same
way daemon.py actually calls it: connect().result(), subscribe(...).result(),
fire-and-forget publish(). If this passes, daemon.py's on_command/
on_chat_command/publish_heartbeat/etc. work unmodified against this adapter.

Requires mosquitto installed (e.g. `brew install mosquitto`) and reachable as
`mosquitto` on PATH, or set MOSQUITTO_BIN.

Run: cd eco/drone/common && python3 -m pytest tests/test_gcs_mqtt.py -v
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gcs_mqtt import PahoMqttAdapter  # noqa: E402

MOSQUITTO_BIN = shutil.which("mosquitto") or "/opt/homebrew/opt/mosquitto/sbin/mosquitto"

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
    raise TimeoutError(f"nothing listening on {host}:{port}")


@pytest.fixture
def broker(tmp_path):
    port = _free_port()
    conf = tmp_path / "mosquitto.conf"
    conf.write_text(f"listener {port}\nallow_anonymous true\npersistence false\n")
    proc = subprocess.Popen([MOSQUITTO_BIN, "-c", str(conf)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _wait_for_port("127.0.0.1", port)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_connect_returns_a_resolvable_future(broker):
    conn = PahoMqttAdapter(host="127.0.0.1", port=broker, client_id="test-drone")
    connect_future = conn.connect()
    connect_future.result(timeout=5)  # must not raise/hang - exactly what daemon.py's main() does
    conn.disconnect().result(timeout=5)


def test_subscribe_future_resolves_and_callback_fires(broker):
    conn = PahoMqttAdapter(host="127.0.0.1", port=broker, client_id="test-drone")
    conn.connect().result(timeout=5)

    received = []
    sub_future, _mid = conn.subscribe(
        topic="drone/d1/chat/+/command", qos=1,
        callback=lambda topic, payload: received.append((topic, json.loads(payload))),
    )
    sub_future.result(timeout=5)  # daemon.py's resubscribe_topics() blocks on exactly this

    publisher = PahoMqttAdapter(host="127.0.0.1", port=broker, client_id="test-publisher")
    publisher.connect().result(timeout=5)
    publisher.publish(topic="drone/d1/chat/c1/command", payload=json.dumps({"action": "execute"}), qos=1)

    deadline = time.time() + 5
    while time.time() < deadline and not received:
        time.sleep(0.1)
    assert received == [("drone/d1/chat/c1/command", {"action": "execute"})]

    conn.disconnect().result(timeout=5)
    publisher.disconnect().result(timeout=5)


def test_callback_signature_matches_awsiot_sdk(broker):
    """daemon.py's on_command/on_chat_command/etc. are defined as
    def handler(topic, payload, **kwargs) to match the AWS IoT SDK's callback
    shape - the adapter must call back with exactly that shape (positional
    topic, positional payload, no required kwargs) for those handlers to work
    unmodified in GCS mode."""
    conn = PahoMqttAdapter(host="127.0.0.1", port=broker, client_id="test-drone")
    conn.connect().result(timeout=5)

    calls = []

    def handler(topic, payload, **kwargs):
        calls.append((topic, payload))

    conn.subscribe(topic="drone/d1/status", qos=1, callback=handler)[0].result(timeout=5)
    conn.publish(topic="drone/d1/status", payload=b'{"battery": 90}', qos=1)

    deadline = time.time() + 5
    while time.time() < deadline and not calls:
        time.sleep(0.1)
    assert calls == [("drone/d1/status", b'{"battery": 90}')]
    conn.disconnect().result(timeout=5)


def test_publish_accepts_qos_enum_like_object(broker):
    """daemon.py always passes qos=mqtt.QoS.AT_LEAST_ONCE (an IntEnum from
    awscrt.mqtt) - the adapter must accept anything int()-able, not just a
    plain int, since callers aren't changed when swapping control planes."""

    class FakeAwsCrtQoS(int):
        pass

    conn = PahoMqttAdapter(host="127.0.0.1", port=broker, client_id="test-drone")
    conn.connect().result(timeout=5)
    future, _mid = conn.publish(topic="drone/d1/status", payload=b"{}", qos=FakeAwsCrtQoS(1))
    future.result(timeout=5)  # must not raise
    conn.disconnect().result(timeout=5)
