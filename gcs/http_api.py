"""
HTTP server for the GCS: serves the same REST routes the iOS/Android apps call
in cloud mode, by adapting each request into the Lambda `event` dict aws/src's
handler functions already expect and calling them directly - same pattern as
e2e/harness/live_rover_act_bridge.py, generalized to the full route table in
aws/template.yaml.

Routes NOT served here (cloud-only, no local equivalent): POST /auth/iot-policy
(Cognito<->IoT policy), /sim/* (EC2-hosted Godot sim sessions), and
/drones/{droneId}/video/* (Kinesis Video Streams WebRTC).
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

from auth import AuthStore, user_id_from_authorization_header


class Route:
    def __init__(self, method: str, pattern: str, handler: Callable):
        self.method = method
        self.handler = handler
        param_names = re.findall(r"\{(\w+)\}", pattern)
        regex = re.sub(r"\{(\w+)\}", lambda m: f"(?P<{m.group(1)}>[^/]+)", pattern)
        self.regex = re.compile(f"^{regex}$")
        self.param_names = param_names

    def match(self, method: str, path: str) -> Optional[dict]:
        if method != self.method:
            return None
        m = self.regex.match(path)
        return m.groupdict() if m else None


def build_routes(handler_mod, rover_mod, drones_mod, conversations_mod, groups_mod) -> list:
    """Mirrors aws/template.yaml's Events blocks exactly (method, path ->
    handler function) for every route that has a local equivalent."""
    return [
        Route("POST", "/command", handler_mod.lambda_handler),
        Route("POST", "/rover/converse", rover_mod.converse_handler),
        Route("POST", "/rover/act", rover_mod.act_handler),
        Route("POST", "/drones", drones_mod.register_handler),
        Route("GET", "/drones", drones_mod.list_handler),
        Route("DELETE", "/drones/{droneId}", drones_mod.delete_handler),
        Route("PATCH", "/drones/{droneId}", drones_mod.update_handler),
        Route("GET", "/drones/{droneId}/wifi", drones_mod.wifi_get_handler),
        Route("PUT", "/drones/{droneId}/wifi", drones_mod.wifi_put_handler),
        Route("GET", "/drones/{droneId}/battery-config", drones_mod.battery_get_handler),
        Route("PUT", "/drones/{droneId}/battery-config", drones_mod.battery_put_handler),
        Route("GET", "/drones/{droneId}/status", drones_mod.status_handler),
        Route("GET", "/drones/{droneId}/logs", drones_mod.get_logs_handler),
        Route("POST", "/drones/{droneId}/conversations", conversations_mod.create_handler),
        Route("GET", "/drones/{droneId}/conversations/{conversationId}", conversations_mod.get_handler),
        Route("POST", "/drones/{droneId}/conversations/{conversationId}/messages",
              conversations_mod.message_handler),
        Route("POST", "/drones/{droneId}/conversations/{conversationId}/select",
              conversations_mod.select_handler),
        Route("POST", "/drones/{droneId}/conversations/{conversationId}/upload-url",
              conversations_mod.upload_url_handler),
        Route("POST", "/groups", groups_mod.create_handler),
        Route("GET", "/groups", groups_mod.list_handler),
        Route("DELETE", "/groups/{groupId}", groups_mod.delete_handler),
        Route("POST", "/groups/{groupId}/conversations/{conversationId}/messages", groups_mod.message_handler),
        Route("GET", "/groups/{groupId}/conversations/{conversationId}", groups_mod.history_handler),
    ]


def _json_error(status: int, message: str) -> dict:
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": message})}


def make_handler_class(routes: list, auth_store: AuthStore, images_dir: Path,
                        sign_verify_fn: Callable[[str, str], bool]):
    """Builds the BaseHTTPRequestHandler subclass. Routes + auth + the images
    dir are closed over rather than passed via class attributes so tests can
    construct multiple independent servers (e.g. one per test) without global
    state leaking between them."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "gcs-http/1"

        def log_message(self, fmt, *args):
            print(f"[gcs-http] {self.address_string()} - {fmt % args}")

        # --- plumbing --------------------------------------------------------

        def _send_lambda_result(self, result: dict):
            body = (result.get("body") or "").encode("utf-8")
            self.send_response(result.get("statusCode", 200))
            for k, v in (result.get("headers") or {"Content-Type": "application/json"}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> str:
            length = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(length).decode("utf-8") if length else ""

        def _build_event(self, path: str, params: dict) -> dict:
            from urllib.parse import urlsplit, parse_qsl
            split = urlsplit(self.path)
            query = dict(parse_qsl(split.query))
            user_id = user_id_from_authorization_header(auth_store, self.headers.get("Authorization"))
            return {
                "httpMethod": self.command,
                "path": path,
                "pathParameters": params or {},
                "queryStringParameters": query or None,
                "body": self._read_body() if self.command in ("POST", "PUT", "PATCH") else None,
                "requestContext": {"authorizer": {"userId": user_id} if user_id else {}},
            }

        def _dispatch(self):
            path = self.path.split("?", 1)[0]

            if path == "/healthz":
                self._send_lambda_result({"statusCode": 200,
                                           "body": json.dumps({"status": "ok"})})
                return

            if path.startswith("/images/"):
                self._handle_image(path[len("/images/"):])
                return

            for route in routes:
                params = route.match(self.command, path)
                if params is not None:
                    event = self._build_event(path, params)
                    try:
                        result = route.handler(event, None)
                    except Exception as e:  # handlers already catch their own errors;
                        # this is a last-resort guard so one bad request can't crash the server.
                        result = _json_error(500, f"internal error: {e}")
                    self._send_lambda_result(result)
                    return

            self._send_lambda_result(_json_error(404, "not found"))

        # --- image PUT/GET (LocalS3's presigned-URL target) -------------------

        def _handle_image(self, key: str):
            path_only = key.split("?", 1)[0]
            target = (images_dir / path_only).resolve()
            if images_dir.resolve() not in target.parents and target != images_dir.resolve():
                self._send_lambda_result(_json_error(400, "invalid key"))
                return

            if self.command == "GET":
                if not target.exists():
                    self._send_lambda_result(_json_error(404, "not found"))
                    return
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            if self.command == "PUT":
                # Two valid ways in: a short-lived HMAC token (LocalS3's
                # presigned-URL equivalent, for the app's own upload flow via
                # POST .../upload-url), or a drone's own long-lived bearer
                # token (drone_sdk.py's GCS branch of upload_photo() - mirrors
                # how, in cloud mode, the drone uploads directly with its own
                # IoT-role credentials rather than an app-issued presigned URL).
                from urllib.parse import urlsplit, parse_qsl
                query = dict(parse_qsl(urlsplit(self.path).query))
                signed_ok = sign_verify_fn(path_only, query.get("token", ""))
                auth = self.headers.get("Authorization", "")
                drone_token = auth.split(" ", 1)[1].strip() if auth.startswith("Bearer ") else ""
                drone_ok = bool(auth_store.drone_id_for_token(drone_token))
                if not (signed_ok or drone_ok):
                    self._send_lambda_result(_json_error(403, "invalid or expired token"))
                    return
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self._read_body_bytes())
                self._send_lambda_result({"statusCode": 200, "body": json.dumps({"key": path_only})})
                return

            self._send_lambda_result(_json_error(405, "method not allowed"))

        def _read_body_bytes(self) -> bytes:
            length = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(length) if length else b""

        def do_GET(self):
            self._dispatch()

        def do_POST(self):
            self._dispatch()

        def do_PUT(self):
            self._dispatch()

        def do_PATCH(self):
            self._dispatch()

        def do_DELETE(self):
            self._dispatch()

    return Handler


class GCSHttpServer:
    def __init__(self, host: str, port: int, routes: list, auth_store: AuthStore,
                 images_dir: Path, sign_verify_fn: Callable[[str, str], bool]):
        handler_cls = make_handler_class(routes, auth_store, images_dir, sign_verify_fn)
        self._server = ThreadingHTTPServer((host, port), handler_cls)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        host = "127.0.0.1" if host == "0.0.0.0" else host
        return f"http://{host}:{port}"

    def start(self):
        self._thread.start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
