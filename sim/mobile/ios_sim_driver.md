# Driving the real iOS Simulator (Mac side)

This is the **true UI verification** path. Isaac + the director + `app_client.py`
run on **Hoopoe**; the iOS Simulator runs **here on the Mac**. Both reach the same
AWS dev stack — there is no direct Mac↔Hoopoe link, so timing is coordinated through
the cloud (the drones are Online in the registry; the app sees them like any user).

The headless `app_client.py` is enough for CI. Use this only when you want to confirm
the *actual app UI* renders the received picture/video.

## Prerequisites
- Xcode + an iOS Simulator runtime installed.
- The DroneOperator project at
  `eco/client/ios/DroneOperator/DroneOperator.xcodeproj` (signing team set once).
- The sim drones already launched on Hoopoe and **Online** for the signed-in user
  (the director handles spawn + registry; sign into the app as that same user).

## One-shot run
```bash
eco/sim/mobile/drive_ios_sim.sh \
  --drone-id sim-quadcopter-001 \
  --message "search for a chair and send a picture"
```

What the script does:
1. `xcrun simctl boot` a known iPhone simulator (creates one if needed).
2. `xcodebuild` build + install DroneOperator into the booted simulator.
3. Launch the app and run a small XCUITest (`IshmaelUITests`) that:
   - opens the drone whose id matches `--drone-id`,
   - types `--message` into the chat composer and sends,
   - waits for an image (or video) bubble to appear,
   - asserts the bubble exists and screenshots it to `~/videos/ishmael-ios/`.

## Notes
- The XCUITest target `IshmaelUITests` is a thin addition to the existing app target;
  it drives the existing `DroneChatView` / `VideoStreamView`. See the test stub the
  script references; if absent, the script falls back to launching the app and
  printing manual steps.
- Auth: sign in once interactively (Sign in with Apple / Google) so the Cognito
  session is cached in the simulator; subsequent runs reuse it.
- This path verifies rendering only — the contract itself is already asserted by
  `app_client.py` on Hoopoe.
