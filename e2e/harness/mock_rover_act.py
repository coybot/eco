"""Fast-tier stand-in for POST /rover/act (the mission brain behind MissionAgent's
CloudBrain — see aws/src/rover.py's act_handler and CloudBrain.swift in the sibling
astral-sdk repo, swift/Sources/PhroverCloud/Cloud). Proves the request/response contract
is reachable and shaped correctly; it does not exercise real vision grounding — that's a
hardware/live-tier concern, not a fast-CI one.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockRoverAct:
    def __init__(self):
        self.received: list[dict] = []
        self._server = HTTPServer(("127.0.0.1", 0), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "MockRoverAct":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path != "/rover/act":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.received.append(body)
                payload = json.dumps({"action": "say", "text": "On it."}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return Handler
