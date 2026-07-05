#!/usr/bin/env bash
# One-command phone/simulator e2e for any of the 4 drone types — the "anyone can test
# from a phone" entry point. Wraps the existing per-platform runners; see README.md for
# the fully-manual (real-device) path and for what each type needs brought up first.
#
# Usage:
#   eco/e2e/run_phone.sh --type quadcopter --platform ios
#   eco/e2e/run_phone.sh --type rover      --platform android
#   eco/e2e/run_phone.sh --type fixedwing  --platform ios
#   eco/e2e/run_phone.sh --type phrover    --platform ios     # starts mock_esp32.py itself
set -euo pipefail

TYPE=""
PLATFORM=""
DEVICE=""
E2E_DRONE_ID="${E2E_DRONE_ID:-}"
E2E_EMAIL="${E2E_EMAIL:-}"
E2E_PASSWORD="${E2E_PASSWORD:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ECO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --type) TYPE="$2"; shift 2;;
    --platform) PLATFORM="$2"; shift 2;;
    --device) DEVICE="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

case "$TYPE" in
  quadcopter|rover|fixedwing|phrover) ;;
  *) echo "usage: $0 --type quadcopter|rover|fixedwing|phrover --platform ios|android" >&2; exit 2;;
esac
case "$PLATFORM" in
  ios|android) ;;
  *) echo "usage: $0 --type <type> --platform ios|android" >&2; exit 2;;
esac
if [[ "$TYPE" == "phrover" && "$PLATFORM" == "android" ]]; then
  echo "phrover is PhroverOperator, an iOS-only app — use --platform ios" >&2
  exit 2
fi

if [[ -z "$E2E_DRONE_ID" && "$TYPE" != "phrover" ]]; then
  E2E_DRONE_ID="sim-${TYPE}-e2e"
fi

MOCK_PID=""
E2E_ROVER_HOST=""
if [[ "$TYPE" == "phrover" ]]; then
  echo "[run_phone] starting mock_esp32.py (no chassis needed for phrover)"
  python3 "$SCRIPT_DIR/harness/mock_esp32.py" --port 8080 &
  MOCK_PID=$!
  trap '[[ -n "$MOCK_PID" ]] && kill "$MOCK_PID" 2>/dev/null || true' EXIT
  E2E_ROVER_HOST="127.0.0.1:8080"
  sleep 1  # let the mock's listener come up before the app tries to reach it
fi

if [[ "$PLATFORM" == "ios" ]]; then
  APP="DroneOperator"
  [[ "$TYPE" == "phrover" ]] && APP="PhroverOperator"
  ARGS=(--vehicle-type "$TYPE" --app "$APP")
  [[ -n "$E2E_DRONE_ID" ]] && ARGS+=(--drone-id "$E2E_DRONE_ID")
  [[ -n "$DEVICE" ]] && ARGS+=(--device "$DEVICE")
  [[ -n "$E2E_ROVER_HOST" ]] && export E2E_ROVER_HOST
  exec "$ECO_DIR/sim/mobile/drive_ios_sim.sh" "${ARGS[@]}"
else
  cd "$ECO_DIR/client/android"
  PROPS=(-Pandroid.testInstrumentationRunnerArguments.RUN_HEZARFEN_E2E=1
        -Pandroid.testInstrumentationRunnerArguments.E2E_VEHICLE_TYPE="$TYPE")
  [[ -n "$E2E_DRONE_ID" ]] && PROPS+=(-Pandroid.testInstrumentationRunnerArguments.E2E_DRONE_ID="$E2E_DRONE_ID")
  [[ -n "$E2E_EMAIL" ]] && PROPS+=(-Pandroid.testInstrumentationRunnerArguments.E2E_EMAIL="$E2E_EMAIL")
  [[ -n "$E2E_PASSWORD" ]] && PROPS+=(-Pandroid.testInstrumentationRunnerArguments.E2E_PASSWORD="$E2E_PASSWORD")
  exec ./gradlew connectedAndroidTest "${PROPS[@]}"
fi
