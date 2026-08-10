"""
Tests for sim_drone_daemon.py / sim_control.py's control_plane: gcs wiring -
the sim's counterpart of the real daemon's config.yaml `control_plane: gcs`
switch (see drone/common/daemon.py, drone/common/gcs_mqtt.py).

Doesn't require a running Godot engine: EngineClient connects eagerly in its
constructor, so these tests give it a minimal fake TCP listener just to let
DroneDaemon.__init__ succeed - _exec_loop/_heartbeat_loop (which actually talk
to the engine) are never started.

Run: eco/drone/sim/.venv-mac/bin/python -m pytest eco/drone/sim/tests/test_gcs_control_plane.py -v
"""
from __future__ import annotations

import argparse
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sim_control import _upload_jpeg_to_gcs
from sim_drone_daemon import DroneDaemon


@pytest.fixture
def fake_engine_addr():
    """A TCP listener that accepts and holds connections open, just so
    EngineClient's eager connect() in DroneDaemon.__init__ succeeds."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()
    stop = threading.Event()

    def accept_loop():
        while not stop.is_set():
            server.settimeout(0.2)
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            # Hold the connection open; DroneDaemon never sends anything in
            # these tests since _exec_loop is never started.

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    try:
        yield f"tcp://{host}:{port}"
    finally:
        stop.set()
        server.close()


def _base_args(**overrides):
    defaults = dict(
        drone_id="sim-q1", vehicle="quadcopter", sock=None,
        control_plane="gcs", certs_dir=None,
        iot_endpoint="unused", credentials_endpoint="unused",
        video_role_alias="unused", s3_role_alias="unused",
        images_bucket="unused", region="us-west-2",
        mqtt_host="127.0.0.1", mqtt_port=1883, mqtt_username=None,
        mqtt_password="drone-token-abc", mqtt_tls=False,
        images_base_url="http://127.0.0.1:8080",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_gcs_mode_wires_upload_conf_correctly(fake_engine_addr):
    args = _base_args(sock=fake_engine_addr)
    daemon = DroneDaemon(args)

    assert daemon.control_plane == "gcs"
    assert daemon.mqtt_username == "sim-q1"  # defaults to drone_id when not given
    assert daemon.upload_conf["control_plane"] == "gcs"
    assert daemon.upload_conf["images_base_url"] == "http://127.0.0.1:8080"
    assert daemon.upload_conf["auth_token"] == "drone-token-abc"


def test_aws_mode_still_defaults_correctly(fake_engine_addr):
    args = _base_args(sock=fake_engine_addr, control_plane="aws", certs_dir="/tmp/certs")
    daemon = DroneDaemon(args)

    assert daemon.control_plane == "aws"
    assert daemon.upload_conf["control_plane"] == "aws"
    assert daemon.certs_dir == "/tmp/certs"


def test_explicit_mqtt_username_overrides_drone_id(fake_engine_addr):
    args = _base_args(sock=fake_engine_addr, mqtt_username="custom-user")
    daemon = DroneDaemon(args)
    assert daemon.mqtt_username == "custom-user"


# --- _upload_jpeg_to_gcs (capture_photo's GCS-mode upload path) ---------------

class _CapturingImagePutHandler(BaseHTTPRequestHandler):
    last_request = None

    def log_message(self, *args):
        pass

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _CapturingImagePutHandler.last_request = {
            "path": self.path, "body": body,
            "authorization": self.headers.get("Authorization"),
        }
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def image_put_server():
    server = HTTPServer(("127.0.0.1", 0), _CapturingImagePutHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_upload_jpeg_to_gcs_puts_bytes_with_bearer_auth(image_put_server):
    upload_conf = {"images_base_url": image_put_server, "auth_token": "tok-123"}
    url = _upload_jpeg_to_gcs(upload_conf, "sim-q1", "conv-1", b"\xff\xd8fakejpeg")

    assert url.startswith(f"{image_put_server}/images/drones/sim-q1/conversations/conv-1/")
    req = _CapturingImagePutHandler.last_request
    assert req["authorization"] == "Bearer tok-123"
    assert req["body"] == b"\xff\xd8fakejpeg"


def test_upload_jpeg_to_gcs_requires_base_url_and_token():
    with pytest.raises(RuntimeError):
        _upload_jpeg_to_gcs({}, "sim-q1", "conv-1", b"data")
