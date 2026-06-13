# Eco sim ↔ mobile apps — local Mac end-to-end runbook

Bring up a **simulated drone on this Mac** (Godot engine) that behaves exactly like a real
drone to the cloud, onboard it from the **iOS / Android apps** running in a simulator/emulator,
and drive it through the real AWS pipeline (IoT MQTT commands, DynamoDB registry/status, S3
photos, KVS WebRTC video). No code paths are sim-special: the same `sim_drone_daemon.py` that
runs on the GPU host runs here, only the engine is local Godot instead of Isaac.

For the **Isaac / GPU** path (hoopoe), see [`BRINGUP.md`](BRINGUP.md). This file is the Mac path.

> **Region note:** the `astral` AWS profile defaults to `us-east-1`, but the entire IoT/Lambda/
> DynamoDB stack is in **`us-west-2`**. Every `aws` call here pins `--region us-west-2`.

## Architecture

```
Godot (rendered, TCP IPC :9999) ──engine_client──▶ sim_drone_daemon.py (one per drone)
                                                        │ cert mTLS, AWS IoT :8883
                                                        ▼
                          AWS IoT Core ── DynamoDB (registry/status) ── Lambda + Bedrock
                                  ▲  KVS WebRTC (aiortc) ▲                     │
                                  │                      │ MQTT/WSS (Cognito)  │
                iOS Simulator app ◀──────────────────────┴─────────────────────▶ Android emulator app
```

## Prerequisites (one-time)

- Godot 4 at `/opt/homebrew/bin/godot` (`brew install godot`), `ffmpeg` + `pkg-config` (for PyAV).
- Python venv with daemon + video deps:
  ```bash
  cd drone/sim
  /opt/homebrew/bin/python3.13 -m venv .venv-mac
  .venv-mac/bin/pip install paho-mqtt boto3 requests numpy opencv-python-headless av aiortc websockets
  ```
  (Apple system Python 3.9 has no PyAV/aiortc wheels — use brew python ≥3.12.)
- Xcode (iOS Simulator) and/or Android SDK + an AVD (e.g. `Pixel_10`).
- `AWS_PROFILE=astral` with IoT/DynamoDB/Cognito access.

## 1. Provision a drone cert (same as a real drone)

```bash
AWS_PROFILE=astral bash drone/sim/provision_sim_certs.sh sim-quadcopter-mac01
# → ~/eco-certs-fleet/sim-quadcopter-mac01/{device.pem,private.key,root-ca.pem}
```
Use an id you can register under your own app user. The deterministic `sim-quadcopter-001…010`
slots may already be owned by another dev — pick a fresh id like `sim-quadcopter-mac01`.

## 2. Launch Godot + the drone daemon

```bash
# Explicit-id mode (recommended for app tests): spawns the id into Godot at runtime.
DRONES="sim-quadcopter-mac01:quadcopter" ENV=plaza bash drone/sim/launch_fleet_mac.sh
# Or roster mode (deterministic ids): FLEET=quad:1 ENV=plaza bash drone/sim/launch_fleet_mac.sh
```
- Godot runs **rendered** (a window), NOT `--headless`: macOS headless uses the dummy renderer,
  which leaves per-vehicle camera SubViewports blank (no photos/video).
- Verify Online in the cloud:
  ```bash
  AWS_PROFILE=astral aws dynamodb get-item --region us-west-2 --table-name drone-status-dev \
    --key '{"droneId":{"S":"sim-quadcopter-mac01"}}' \
    --query 'Item.{status:status.S,ttl:ttl.N,variant:variant.S}'
  ```
  `status=online` with `ttl > now` means heartbeats are flowing.
- Stop everything: `bash drone/sim/launch_fleet_mac.sh stop`.

Smoke-test the command + perception path without the app (publishes what the cloud Lambda does):
```bash
AWS_PROFILE=astral aws iot-data publish --region us-west-2 \
  --topic "drone/sim-quadcopter-mac01/chat/smoke/command" --cli-binary-format raw-in-base64-out \
  --payload '{"conversation_id":"smoke","action":"execute","code":"capture_photo(\"test\")"}' --qos 1
# daemon log shows: exec on conv smoke → responded (success=True, imgs=1); JPEG lands in S3.
```

## 3. A test app user (for automated onboarding)

The drone must be registered under the **logged-in app user's** Cognito sub. The apps do this
themselves via the onboarding dialog (`registerDrone` → `POST /drones`). For automated tests use
a deterministic user:
```bash
POOL=us-west-2_MkixOuF3S; CLIENT=4j965u17ohomik14cte9ni276h
AWS_PROFILE=astral aws cognito-idp admin-create-user --region us-west-2 --user-pool-id $POOL \
  --username simtest@astral.dev --message-action SUPPRESS \
  --user-attributes Name=email,Value=simtest@astral.dev Name=email_verified,Value=true
AWS_PROFILE=astral aws cognito-idp admin-set-user-password --region us-west-2 --user-pool-id $POOL \
  --username simtest@astral.dev --password 'SimTest!2026' --permanent
```
If re-running the onboarding test, clear the prior registry row first (direct delete avoids the
factory-reset that the `DELETE /drones` API triggers):
```bash
AWS_PROFILE=astral aws dynamodb delete-item --region us-west-2 --table-name drone-registry-dev \
  --key '{"userId":{"S":"<TEST_USER_SUB>"},"droneId":{"S":"sim-quadcopter-mac01"}}'
```

## 4a. iOS — automated onboarding + command (XCUITest)

The app ships e2e accessibility ids; `DroneOperatorUITests/testOnboard_addSimDrone_thenCommand`
signs in, taps **Add Drone → Already configured**, types the id, asserts the card goes **Online**,
opens chat and sends a command. (The UI-test target is created by `add_uitest_target.rb` if your
checkout is missing it.)
```bash
cd client/ios/DroneOperator
export TEST_RUNNER_RUN_HEZARFEN_E2E=1 TEST_RUNNER_E2E_DRONE_ID=sim-quadcopter-mac01
export TEST_RUNNER_E2E_EMAIL=simtest@astral.dev TEST_RUNNER_E2E_PASSWORD='SimTest!2026'
xcodebuild test -project DroneOperator.xcodeproj -scheme DroneOperator \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  -only-testing:DroneOperatorUITests/DroneOperatorUITests/testOnboard_addSimDrone_thenCommand \
  -derivedDataPath build/SimDerived
```
First run downloads the WebRTC + AWS SDK binary frameworks (slow). If you hit
"no XCFramework found", wipe `build/SimDerived/SourcePackages` and rebuild.

## 4b. Android — automated onboarding + command (Compose instrumented test)

`app/src/androidTest/.../OnboardingTest.kt` drives the same flow in the emulator.
```bash
cd client/android
adb wait-for-device
./gradlew :app:connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.RUN_HEZARFEN_E2E=1 \
  -Pandroid.testInstrumentationRunnerArguments.E2E_DRONE_ID=sim-quadcopter-mac01 \
  -Pandroid.testInstrumentationRunnerArguments.E2E_EMAIL=simtest@astral.dev \
  -Pandroid.testInstrumentationRunnerArguments.E2E_PASSWORD=SimTest!2026
```

## Notes / gotchas

- **Heartbeat TTL is 30 s.** After (re)starting a daemon the cloud shows the drone Offline for up
  to ~30 s and rejects commands until a fresh heartbeat lands — wait before sending.
- **Video** uses aiortc + PyAV (no GStreamer on Mac). On-demand only: the daemon starts the KVS
  master when the app opens the viewer (`drone/<id>/video/command` start).
- **One drone = one process / one MQTT connection**, identical to IRL. Scale by launching more
  ids (provision a cert each) — see `BRINGUP.md` for the GPU-host fleet/orchestrator design.
