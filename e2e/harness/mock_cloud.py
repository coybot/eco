"""Fast-tier stand-in for the cloud `/command` contract (no AWS, no MQTT).

The real flow is: phone -> POST {API_BASE}/command -> Bedrock generates drone code ->
published over IoT MQTT -> drone executes -> response comes back on
`drone/{id}/chat/+/response` (see eco/sim/mobile/app_client.py). That full round trip is
the live-tier's job (against the real dev stack). The fast tier only needs to prove the
harness sends the right mission and correctly interprets the response shape, so this
mock collapses the async MQTT leg into a synchronous HTTP response.

Not a substitute for testing the real API — see eco/test_sim_e2e.sh / the live tier for
that.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockCloud:
    """Minimal local HTTP server implementing just POST /command.

    Returns a canned success response: {"success": True, "image_urls": [...]} when the
    mission mentions capturing something visual, else {"success": True, "image_urls": []}.
    A mission mentioning video/recording also gets "video_urls", mirroring the
    real daemon, which publishes both fields side by side.
    """

    _PHOTO_WORDS = re.compile(r"\b(photos?|pictures?|photographs?|see|look)\b", re.IGNORECASE)
    # "record"/"recording" as well as "video": the phase pair behind this is
    # start_recording/stop_recording, and an operator says either.
    _VIDEO_WORDS = re.compile(r"\b(video|record|recording|film|footage)\b", re.IGNORECASE)

    def __init__(self):
        self.received: list[dict] = []
        self._server = HTTPServer(("127.0.0.1", 0), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "MockCloud":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D401 — silence default access logging
                pass

            def do_POST(self):
                if self.path != "/command":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.received.append(body)
                command = body.get("command", "")
                has_image = bool(outer._PHOTO_WORDS.search(command))
                has_video = bool(outer._VIDEO_WORDS.search(command))
                resp = {
                    "success": True,
                    "image_urls": ["https://mock-cloud.invalid/photo.jpg"] if has_image else [],
                    "video_urls": ["https://mock-cloud.invalid/clip.mp4"] if has_video else [],
                    "stdout": f"mock executed: {command}",
                }
                payload = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return Handler
