#!/usr/bin/env python3
"""
Local Control API (offline-first).

Endpoints:
  POST /mission  -> start mission (structured JSON only)
  GET /status    -> mission status
  POST /abort    -> abort mission

Authentication:
  - Bearer token from /var/lib/presidio/pairing_token.json
  - No cloud dependency
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import ssl
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, Tuple

from mission_runner import MissionRunner

logger = logging.getLogger("presidio.local_api")

STATE_DIR = Path("/var/lib/presidio")
PAIRING_TOKEN_PATH = STATE_DIR / "pairing_token.json"

DEFAULT_CERT = "/etc/presidio/certs/local_control.crt"
DEFAULT_KEY = "/etc/presidio/certs/local_control.key"


class LocalControlConfig:
    host = "0.0.0.0"
    port = 8443
    cert_path = DEFAULT_CERT
    key_path = DEFAULT_KEY
    allow_http = False


def _ensure_pairing_token() -> str:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if PAIRING_TOKEN_PATH.exists():
        try:
            data = json.loads(PAIRING_TOKEN_PATH.read_text() or "{}")
            token = data.get("token")
            if token:
                return token
        except Exception:
            pass
    token = secrets.token_urlsafe(32)
    PAIRING_TOKEN_PATH.write_text(json.dumps({"token": token}))
    # Set secure permissions (owner read/write only)
    os.chmod(PAIRING_TOKEN_PATH, 0o600)
    logger.info("Generated new pairing token at %s", PAIRING_TOKEN_PATH)
    return token


def _ensure_cert(cert_path: str, key_path: str) -> None:
    cert_file = Path(cert_path)
    key_file = Path(key_path)
    if cert_file.exists() and key_file.exists():
        return
    cert_file.parent.mkdir(parents=True, exist_ok=True)
    logger.warning("Generating self-signed TLS cert for local API")
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", key_path,
        "-out", cert_path,
        "-days", "3650",
        "-nodes",
        "-subj", "/CN=presidio-drone"
    ], check=True)


def _is_json_dict(payload: Dict) -> bool:
    return isinstance(payload, dict)


def validate_mission(payload: Dict) -> Tuple[bool, str]:
    if not _is_json_dict(payload):
        return False, "payload must be a JSON object"
    required = {"goal", "target", "constraints", "failsafes"}
    if set(payload.keys()) != required:
        return False, f"mission must contain only {sorted(required)}"
    if not isinstance(payload.get("goal"), str) or not payload.get("goal"):
        return False, "goal must be a non-empty string"
    for key in ("target", "constraints", "failsafes"):
        if not isinstance(payload.get(key), dict):
            return False, f"{key} must be an object"
    return True, "ok"


class LocalControlHandler(BaseHTTPRequestHandler):
    runner = MissionRunner()
    token = _ensure_pairing_token()

    def log_message(self, fmt, *args):
        logger.info("HTTP %s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, data: Dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _authorize(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return auth.split(" ", 1)[1] == self.token

    def do_GET(self):
        if self.path != "/status":
            self._send(404, {"error": "not_found"})
            return
        if not self._authorize():
            self._send(401, {"error": "unauthorized"})
            return
        status = self.runner.get_status()
        self._send(200, {
            "mission_id": status.mission_id,
            "active": status.active,
            "started_at": status.started_at,
            "last_error": status.last_error
        })

    def do_POST(self):
        if not self._authorize():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            payload = self._read_body()
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid_json"})
            return

        if self.path == "/mission":
            ok, reason = validate_mission(payload)
            if not ok:
                logger.warning("Mission rejected: %s", reason)
                self._send(400, {"error": "invalid_mission", "reason": reason})
                return
            status = self.runner.start_mission(payload)
            self._send(200, {
                "mission_id": status.mission_id,
                "active": status.active,
                "started_at": status.started_at
            })
            return

        if self.path == "/abort":
            status = self.runner.abort_mission(reason="local_abort")
            self._send(200, {
                "mission_id": status.mission_id,
                "active": status.active,
                "last_error": status.last_error
            })
            return

        self._send(404, {"error": "not_found"})


def run_server():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _ensure_pairing_token()
    server = HTTPServer((LocalControlConfig.host, LocalControlConfig.port), LocalControlHandler)

    if LocalControlConfig.allow_http:
        logger.warning("Starting local API in HTTP mode (not recommended)")
    else:
        _ensure_cert(LocalControlConfig.cert_path, LocalControlConfig.key_path)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(LocalControlConfig.cert_path, LocalControlConfig.key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)

    logger.info("Local control API listening on %s:%s", LocalControlConfig.host, LocalControlConfig.port)
    server.serve_forever()


if __name__ == "__main__":
    run_server()
