#!/usr/bin/env bash
# Run the live mission-cognition tests: the real Swift MissionAgent + CloudBrain driving
# the sim world, with every decision made by the REAL model behind the production
# /rover/act handler (via harness/live_rover_act_bridge.py).
#
#   AWS_PROFILE=coybot bash eco/e2e/run_live_mission.sh
#
# Makes real, billed Bedrock calls (roughly 15-35 per run) — this is deliberately NOT part
# of the fast e2e gate. Output (including the full per-tick transcripts) is teed to a log
# file whose path is printed at the end.
set -euo pipefail

ECO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="$(cd "$ECO_DIR/../sdk" && pwd)"
LOG="$(mktemp /tmp/live_mission_XXXX.log)"
export AWS_PROFILE="${AWS_PROFILE:-coybot}"

echo "eco:   $ECO_DIR"
echo "sdk:   $SDK_DIR"
echo "log:   $LOG"
echo "model: $(cd "$ECO_DIR" && python3 -c 'import sys; sys.path.insert(0, "aws/src"); import rover; print(rover.BEDROCK_MODEL_ID)')"

# Start the bridge on an ephemeral port; capture its URL from the first stdout line.
BRIDGE_OUT="$(mktemp /tmp/live_bridge_XXXX.log)"
(cd "$ECO_DIR" && exec python3 -m e2e.harness.live_rover_act_bridge) >"$BRIDGE_OUT" 2>&1 &
BRIDGE_PID=$!
trap 'kill "$BRIDGE_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
    URL="$(grep -m1 '^BRIDGE_URL=' "$BRIDGE_OUT" | cut -d= -f2 || true)"
    [ -n "${URL:-}" ] && break
    sleep 0.2
done
if [ -z "${URL:-}" ]; then
    echo "bridge failed to start:" >&2
    cat "$BRIDGE_OUT" >&2
    exit 1
fi
echo "bridge: $URL (pid $BRIDGE_PID)"

# Find a simulator the same way harness/phrover.py does.
UDID="$(xcrun simctl list devices available -j | python3 -c '
import json, sys
devices = json.load(sys.stdin)["devices"]
for runtime, entries in devices.items():
    if "iOS" not in runtime:
        continue
    for e in entries:
        if e.get("isAvailable") and "iPhone" in e.get("name", ""):
            print(e["udid"]); raise SystemExit
')"
echo "simulator: $UDID"

set +e
(cd "$SDK_DIR" && TEST_RUNNER_LIVE_ROVER_ACT_URL="$URL" xcodebuild test \
    -scheme coybot-sdk-Package \
    -destination "id=$UDID" \
    -only-testing:PhroverKitLiveProbes/CloudBrainLiveMissionTests) 2>&1 | tee "$LOG"
STATUS=${PIPESTATUS[0]}
set -e

echo ""
echo "bridge served $(grep -c '^\[bridge\]' "$BRIDGE_OUT" || true) real model decisions:"
grep '^\[bridge\]' "$BRIDGE_OUT" || true
echo ""
echo "full log: $LOG"
echo "bridge log: $BRIDGE_OUT"
exit "$STATUS"
