"""Fake WAVE ROVER ESP32 base for hardware-free phrover testing.

Speaks just enough of the Waveshare JSON command protocol that PhroverKit's
`RoverSDK/RoverControl.swift` (in the sibling coybot-sdk repo) gets a real 2xx round trip:

    GET /js?json={"T":1,"L":<m/s>,"R":<m/s>}   -- speed control
    GET /js?json={"T":0}                        -- emergency stop
    GET /js?json={"T":131,"cmd":1}              -- feedback flow on (accepted, not streamed;
                                                    nothing in the app parses feedback yet —
                                                    see RoverFeedback.parse call sites)

`RoverControl.sendJSON` only checks the status code, so a 200 with an empty body is a
faithful stand-in. Every command is recorded and exposed at GET /__received for the test
harness / CI to assert against.

Usable as a library (``MockESP32`` context manager, for the Python-side test) or as a
standalone process (``python3 mock_esp32.py --port 8080``) for a human or CI driving the
real PhroverOperator app/UI test against a laptop instead of a physical chassis.
"""
from __future__ import annotations

import argparse
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockESP32:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.received: list[dict] = []
        self._lock = threading.Lock()
        self._server = HTTPServer((host, port), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def host_port(self) -> str:
        host, port = self._server.server_address
        return f"{host}:{port}"

    def __enter__(self) -> "MockESP32":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    def drive_commands(self) -> list[dict]:
        """Commands with T==1 (speed control) — what a "did it drive?" assertion checks."""
        with self._lock:
            return [c for c in self.received if c.get("T") == 1]

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/__received":
                    self._json(200, outer.received)
                    return
                if parsed.path != "/js":
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                raw = (qs.get("json") or ["{}"])[0]
                try:
                    cmd = json.loads(raw)
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                with outer._lock:
                    outer.received.append(cmd)
                self._json(200, {})

            def _json(self, status: int, body) -> None:
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    mock = MockESP32(host=args.host, port=args.port)
    mock.__enter__()
    print(f"[mock_esp32] listening on http://{mock.host_port} "
          f"(point RoverConfig at this host to test without a chassis)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        mock.__exit__()


if __name__ == "__main__":
    main()
