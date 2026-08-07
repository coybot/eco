"""Local HTTP bridge that serves the REAL /rover/act handler with a REAL model.

Same server shape as mock_rover_act.py, but instead of a canned reply it invokes the
literal production Lambda handler (aws/src/rover.py act_handler) — same system prompt,
same decide tool schema, same Bedrock model — with the one Lambda-ism faked locally: the
API Gateway authorizer context (userId), which in production is set by the Cognito
authorizer. Auth is faked HERE ONLY, on a 127.0.0.1-bound server; the deployed endpoint
is untouched.

This is what lets the Swift sim (CloudBrainLiveMissionTests in the sibling presidio-sdk
repo) drive the real MissionAgent + real CloudBrain wire path against a real model:

    Swift MissionAgent -> CloudBrain -> http://127.0.0.1:<port>/rover/act
        -> rover.act_handler -> bedrock.invoke_model -> real Claude

Makes real, billed Bedrock calls — NEVER wire this into the fast e2e gate. Needs AWS
credentials (AWS_PROFILE=astral).

Run standalone:
    cd <repo-root>/eco
    AWS_PROFILE=astral python3 -m e2e.harness.live_rover_act_bridge
prints the URL it is serving on, then serves until killed.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aws" / "src"))
import rover  # noqa: E402  (the production module — prompt, tool schema, bedrock client)


class LiveRoverActBridge:
    def __init__(self, port: int = 0):
        self.requests_served = 0
        self._server = HTTPServer(("127.0.0.1", port), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "LiveRoverActBridge":
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
                body = self.rfile.read(length).decode("utf-8")

                # The production handler, with the API Gateway authorizer faked locally.
                event = {
                    "requestContext": {"authorizer": {"userId": "sim-live"}},
                    "body": body,
                }
                result = rover.act_handler(event, None)
                outer.requests_served += 1

                payload = result["body"].encode()
                self.send_response(result["statusCode"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

                # One-line trace per decision so a human tailing the bridge can follow
                # the mission in real time.
                try:
                    decision = json.loads(result["body"])
                    print(f"[bridge] #{outer.requests_served} -> {decision.get('action')}"
                          f" {json.dumps({k: v for k, v in decision.items() if v is not None and k != 'action'})}",
                          flush=True)
                except (json.JSONDecodeError, KeyError):
                    print(f"[bridge] #{outer.requests_served} -> HTTP {result['statusCode']}", flush=True)

        return Handler


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=0, help="port to bind (default: ephemeral)")
    args = ap.parse_args()

    with LiveRoverActBridge(port=args.port) as bridge:
        print(f"BRIDGE_URL={bridge.base_url}", flush=True)
        print(f"model: {rover.BEDROCK_MODEL_ID} (region {rover.AWS_REGION}) — Ctrl-C to stop", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
