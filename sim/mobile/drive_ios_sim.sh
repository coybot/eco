#!/usr/bin/env bash
# Drive the real iOS Simulator against sim drones that are already Online in the
# cloud (or, for phrover, against a mock chassis). Mac-only (the iOS Simulator does
# not run on Linux/Hoopoe). See ios_sim_driver.md for the full picture, and
# eco/e2e/README.md for the unified 4-type entry point (eco/e2e/run_phone.sh calls
# this script).
set -euo pipefail

APP="DroneOperator"
VEHICLE_TYPE=""
DRONE_ID=""
MESSAGE="search for a chair and send a picture"
DEVICE="${ISHMAEL_IOS_DEVICE:-iPhone 15}"
SHOT_DIR="${HOME}/videos/ishmael-ios"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)          APP="$2"; shift 2;;
    --vehicle-type) VEHICLE_TYPE="$2"; shift 2;;
    --drone-id)     DRONE_ID="$2"; shift 2;;
    --message)      MESSAGE="$2"; shift 2;;
    --device)       DEVICE="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

case "$APP" in
  DroneOperator|PhroverOperator) ;;
  *) echo "--app must be DroneOperator or PhroverOperator (got: $APP)" >&2; exit 2;;
esac
# PhroverOperator lives in the public coybot-sdk repo (sibling of eco), not eco/client/ios —
# see eco/e2e/README.md. Assumes both repos are checked out side by side.
if [[ "$APP" == "PhroverOperator" ]]; then
  PROJ="$(cd "$SCRIPT_DIR/../../../sdk/examples/PhroverOperator" && pwd)"
else
  PROJ="$(cd "$SCRIPT_DIR/../../client/ios/$APP" && pwd)"
fi
SCHEME="$APP"
UITEST_TARGET="${APP}UITests"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "drive_ios_sim.sh must run on macOS (the iOS Simulator is macOS-only)." >&2
  exit 1
fi

mkdir -p "$SHOT_DIR"

echo "[ios] booting simulator: $DEVICE"
UDID=$(xcrun simctl list devices | grep -m1 "$DEVICE (" | grep -oE '[0-9A-F-]{36}' || true)
if [[ -z "$UDID" ]]; then
  UDID=$(xcrun simctl create "e2e-$DEVICE" "$DEVICE" || true)
fi
xcrun simctl boot "$UDID" 2>/dev/null || true
open -a Simulator || true

echo "[ios] building + installing $APP"
xcodebuild -project "$PROJ/$APP.xcodeproj" -scheme "$SCHEME" \
  -destination "id=$UDID" -derivedDataPath /tmp/e2e-ios-dd build | tail -5

INSTALLED_APP=$(/usr/bin/find /tmp/e2e-ios-dd -name "$APP.app" -type d | head -1)
xcrun simctl install "$UDID" "$INSTALLED_APP"

# Env vars only reach the Simulator's app/test process via the SIMCTL_CHILD_ prefix
# (see `xcrun simctl help launch`) — xcodebuild test uses simctl under the hood.
export SIMCTL_CHILD_RUN_HEZARFEN_E2E=1
[[ -n "$DRONE_ID" ]] && export SIMCTL_CHILD_E2E_DRONE_ID="$DRONE_ID"
[[ -n "$VEHICLE_TYPE" ]] && export SIMCTL_CHILD_E2E_VEHICLE_TYPE="$VEHICLE_TYPE"
[[ -n "${E2E_EMAIL:-}" ]] && export SIMCTL_CHILD_E2E_EMAIL="$E2E_EMAIL"
[[ -n "${E2E_PASSWORD:-}" ]] && export SIMCTL_CHILD_E2E_PASSWORD="$E2E_PASSWORD"
[[ -n "${E2E_ROVER_HOST:-}" ]] && export SIMCTL_CHILD_E2E_ROVER_HOST="$E2E_ROVER_HOST"

if xcodebuild -project "$PROJ/$APP.xcodeproj" -list 2>/dev/null | grep -q "$UITEST_TARGET"; then
  echo "[ios] running $UITEST_TARGET (app=$APP drone=$DRONE_ID type=$VEHICLE_TYPE)"
  xcodebuild test -project "$PROJ/$APP.xcodeproj" -scheme "$SCHEME" \
    -destination "id=$UDID" -only-testing:"$UITEST_TARGET" | tail -20
  xcrun simctl io "$UDID" screenshot "$SHOT_DIR/result_$(date +%s).png" || true
  echo "[ios] screenshot saved under $SHOT_DIR"
else
  BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$INSTALLED_APP/Info.plist")
  xcrun simctl launch "$UDID" "$BUNDLE_ID"
  cat <<EOF
[ios] No $UITEST_TARGET target found — app launched for manual verification.
  1. Sign in as the user that owns drone '$DRONE_ID'.
  2. Open that drone and send: "$MESSAGE"
  3. Confirm the image/video bubble appears.
EOF
fi
