#!/usr/bin/env bash
# Drive the real iOS Simulator against sim drones that are already Online in the
# cloud. Mac-only (the iOS Simulator does not run on Linux/Hoopoe). See
# ios_sim_driver.md for the full picture.
set -euo pipefail

DRONE_ID=""
MESSAGE="search for a chair and send a picture"
DEVICE="${ISHMAEL_IOS_DEVICE:-iPhone 15}"
SHOT_DIR="${HOME}/videos/ishmael-ios"
PROJ="$(cd "$(dirname "$0")/../../client/ios/DroneOperator" && pwd)"
SCHEME="DroneOperator"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --drone-id) DRONE_ID="$2"; shift 2;;
    --message)  MESSAGE="$2"; shift 2;;
    --device)   DEVICE="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

if [[ "$(uname)" != "Darwin" ]]; then
  echo "drive_ios_sim.sh must run on macOS (the iOS Simulator is macOS-only)." >&2
  exit 1
fi

mkdir -p "$SHOT_DIR"

echo "[ios] booting simulator: $DEVICE"
UDID=$(xcrun simctl list devices | grep -m1 "$DEVICE (" | grep -oE '[0-9A-F-]{36}' || true)
if [[ -z "$UDID" ]]; then
  UDID=$(xcrun simctl create "Ishmael-$DEVICE" "$DEVICE" || true)
fi
xcrun simctl boot "$UDID" 2>/dev/null || true
open -a Simulator || true

echo "[ios] building + installing DroneOperator"
xcodebuild -project "$PROJ/DroneOperator.xcodeproj" -scheme "$SCHEME" \
  -destination "id=$UDID" -derivedDataPath /tmp/ishmael-dd build | tail -5

APP=$(/usr/bin/find /tmp/ishmael-dd -name "DroneOperator.app" -type d | head -1)
xcrun simctl install "$UDID" "$APP"

# Prefer an XCUITest target if present; else launch the app and print manual steps.
if xcodebuild -project "$PROJ/DroneOperator.xcodeproj" -list 2>/dev/null \
     | grep -q "IshmaelUITests"; then
  echo "[ios] running IshmaelUITests (drone=$DRONE_ID msg=$MESSAGE)"
  ISHMAEL_DRONE_ID="$DRONE_ID" ISHMAEL_MESSAGE="$MESSAGE" \
  xcodebuild test -project "$PROJ/DroneOperator.xcodeproj" -scheme "$SCHEME" \
    -destination "id=$UDID" -only-testing:IshmaelUITests | tail -20
  xcrun simctl io "$UDID" screenshot "$SHOT_DIR/result_$(date +%s).png" || true
  echo "[ios] screenshot saved under $SHOT_DIR"
else
  BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$APP/Info.plist")
  xcrun simctl launch "$UDID" "$BUNDLE_ID"
  cat <<EOF
[ios] No IshmaelUITests target found — app launched for manual verification.
  1. Sign in as the user that owns drone '$DRONE_ID'.
  2. Open that drone and send: "$MESSAGE"
  3. Confirm the image/video bubble appears.
  (Add an IshmaelUITests target to automate this — see ios_sim_driver.md.)
EOF
fi
