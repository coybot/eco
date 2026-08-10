#!/usr/bin/env bash
# Manual smoke test for GCS mode - starts mosquitto + the GCS server against
# a local Ollama/vLLM (or any OpenAI-compatible endpoint you already have
# running), then drives a few requests with curl so you can eyeball the
# whole stack coming up without opening the iOS/Android app or writing a
# test. This is the manual counterpart of gcs/tests/test_local_stack.py's
# automated version of the same flow.
#
# Usage:
#   ./scripts/gcs_smoke.sh [--llm-base-url http://localhost:11434/v1]
#
# Requires: mosquitto (e.g. `brew install mosquitto`), python3 with
# eco/gcs/requirements.txt installed, and a reachable OpenAI-compatible
# model server (defaults to Ollama's default port).
set -euo pipefail

LLM_BASE_URL="${LLM_BASE_URL:-http://localhost:11434/v1}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --llm-base-url) LLM_BASE_URL="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GCS_DIR="$REPO_ROOT/gcs"
WORK_DIR="$(mktemp -d)"
trap 'echo "[smoke] cleaning up..."; kill "${GCS_PID:-0}" "${MOSQUITTO_PID:-0}" 2>/dev/null || true; rm -rf "$WORK_DIR"' EXIT

MOSQUITTO_PORT=18830
HTTP_PORT=18880

echo "[smoke] starting mosquitto on :$MOSQUITTO_PORT (anonymous, test-only)..."
cat > "$WORK_DIR/mosquitto.conf" <<EOF
listener $MOSQUITTO_PORT
allow_anonymous true
persistence false
EOF
MOSQUITTO_BIN="$(command -v mosquitto || echo /opt/homebrew/opt/mosquitto/sbin/mosquitto)"
"$MOSQUITTO_BIN" -c "$WORK_DIR/mosquitto.conf" > "$WORK_DIR/mosquitto.log" 2>&1 &
MOSQUITTO_PID=$!

echo "[smoke] starting GCS server on :$HTTP_PORT (LLM: $LLM_BASE_URL)..."
cat > "$WORK_DIR/config.yaml" <<EOF
data_dir: $WORK_DIR/data
http:
  host: 127.0.0.1
  port: $HTTP_PORT
mqtt:
  host: 127.0.0.1
  port: $MOSQUITTO_PORT
llm:
  provider: openai
  base_url: $LLM_BASE_URL
operators:
  - smoketest
EOF
(cd "$GCS_DIR" && python3 server.py --config "$WORK_DIR/config.yaml" > "$WORK_DIR/gcs.log" 2>&1 &)
GCS_PID=$!

echo "[smoke] waiting for /healthz..."
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$HTTP_PORT/healthz" > /dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
curl -sf "http://127.0.0.1:$HTTP_PORT/healthz" || { echo "[smoke] GCS server never became healthy - see $WORK_DIR/gcs.log"; cat "$WORK_DIR/gcs.log"; exit 1; }
echo
echo "[smoke] healthy."

TOKEN="$(python3 -c "import json,glob; d=glob.glob('$WORK_DIR/data/auth.json'); print(json.load(open(d[0]))['operators']['smoketest']) if d else print('')")"
echo "[smoke] operator token: $TOKEN"

echo "[smoke] registering a test drone..."
curl -sf -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"droneId": "smoke-q1", "name": "smoke-q1"}' \
  "http://127.0.0.1:$HTTP_PORT/drones"
echo

echo "[smoke] listing drones..."
curl -sf -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$HTTP_PORT/drones"
echo

echo "[smoke] all requests succeeded. GCS server log: $WORK_DIR/gcs.log (kept until this script exits)"
echo "[smoke] Ctrl+C to stop mosquitto + the GCS server."
wait "$GCS_PID"
