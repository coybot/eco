# Rover — iOS-brained WAVE ROVER

> **The Swift code (RoverNav / PhroverKit / PhroverCloud / PhroverOperator) has moved to
> the public [`coybot-sdk`](https://github.com/coybot/coybot-sdk) repo** — a sibling of
> `eco`, checked out at `../sdk` in this workspace — as part of open-sourcing Phrover's
> on-device brain. Only the cloud-side pieces (`eco/aws/src/rover.py`, `template.yaml`)
> stay here. The "Layout"/"Building the app" sections below describe the new locations;
> the "Status" section is left as the historical dev log for the work that produced them.

An indoor autonomous ground robot where the **brain is an iPhone Pro or iPad Pro**, not a
Jetson Orin. The phone's camera, LiDAR, IMU, mic, speaker, and Neural Engine do the
perception, navigation, and voice; a **Waveshare WAVE ROVER** 4WD chassis does the driving.

First use case: an autonomous ramp-agent robot at **Hawthorne Airport** — talk to ground
crew and autonomously move baggage to/from aircraft on the tarmac.

> Sibling platform to `eco/drone/` (the Orin-based drone). Reuses the eco AWS/IoT/Cognito
> cloud and the `DroneOperator` iOS app scaffolding.

## How it splits

```
iPhone/iPad Pro (brain)                          WAVE ROVER ESP32 (base)
─────────────────────────                        ───────────────────────
ARKit VIO pose  (odometry — no wheel encoders)
ARKit LiDAR mesh ─► CostmapBuilder
CoreML YOLO      ─► person/obstacle detection
   │
RoverNav: AStarPlanner ─► PursuitController ──►  {"T":1,"L":<l>,"R":<r>}  ──► 4WD motors
   │                                              (WiFi HTTP  GET /js?json=…)
ObstacleGuard (LiDAR depth + comms watchdog)
Voice (on-device STT/TTS) + Claude dialog (cloud, hybrid)
Cloud link: Cognito auth · AWS IoT MQTT telemetry
```

Control transport is **WiFi HTTP** (iOS can't do USB serial cleanly). Run the ESP32 in
**STA mode** on an airport ops WiFi network so the phone keeps internet for the hybrid
cloud AI — there's no ambient "building WiFi" on a ramp, so this needs real network
coverage arranged for the ramp area; fall back to ESP32 **AP mode** + phone cellular for
infra-free demos/testing.

## Layout

```
eco/rover/
  README.md
  docs/architecture.md          full design, phases, risks
  models/                       CoreML/MLX model conversion scripts (YOLO etc.)

sdk/                             ← public coybot-sdk repo, sibling of eco
  Package.swift                 three library products from one root manifest:
    RoverNav                    platform-independent nav core (Sources/RoverNav, TESTED)
                                 Geometry · Costmap · AStarPlanner · PursuitController · DifferentialDrive
    PhroverKit                  the brain: Config/RoverConfig, RoverSDK (RoverControl ·
                                 RoverTelemetry), Perception (ARSessionManager · Detector),
                                 Nav (CostmapBuilder · ObstacleGuard · NavigationController ·
                                 WorldMapStore), Voice (SpeechIn · SpeechOut · RoverIntent ·
                                 DialogAgent · DialogEscalating)
    PhroverCloud                mobile cloud client: AuthService, MQTTService, APIClient,
                                 ClaudeDialogClient (conforms to DialogEscalating),
                                 RoverTelemetryPublisher — depends on aws-sdk-ios-spm
  examples/PhroverOperator/     the iOS app — thin SwiftUI wrapper over the three products
    PhroverOperator/App, Views, Config/PhroverCloud.example.plist
    PhroverOperatorUITests/
```

## Verifying the nav core (works today, no hardware)

```bash
cd sdk
swift build --target RoverNav   # pure Foundation, builds/tests on plain macOS
```

Running the full `RoverNavTests` suite (12 tests, incl. the collision-free
corner-navigation sim) needs an iOS destination, since RoverNav now shares a package with
ARKit-dependent PhroverKit/PhroverCloud, which don't build on plain macOS:

```bash
xcodebuild test -scheme coybot-sdk-Package -destination 'platform=iOS Simulator,name=iPhone 16' \
  -only-testing:RoverNavTests
```

## Building the app

```bash
cd sdk/examples/PhroverOperator
xcodebuild -project PhroverOperator.xcodeproj -scheme PhroverOperator -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' build
```

To run on real hardware (required for ARKit/LiDAR/FoundationModels — the Simulator can
compile against these frameworks but not exercise them): open the project in Xcode,
select a development team in Signing & Capabilities, and deploy to an iPhone/iPad **Pro**.

## Status

- ✅ `RoverNav` navigation core + tests (planner, pursuit, differential drive, costmap).
- ✅ iOS Phase-0 (teleop, control loop, AR pose, telemetry) and Phase-1 (autonomous
  point-to-point nav) sources.
- ✅ iOS Phase-2 voice sources: on-device STT/TTS + on-device Foundation Model as the
  primary reasoning/intent parser, with Claude cloud reserved for open-ended escalation
  (see [architecture.md](docs/architecture.md) "AI split").
- ✅ `POST /rover/converse` Claude-escalation route (`eco/aws/src/rover.py` +
  `RoverConverseFunction` in `template.yaml`) — **deployed and verified live** on the
  `drone-api` stack (`us-west-2`), returns 401 without a valid Cognito token, same as
  the existing `/command` route, using the same Lambda authorizer.
- ✅ `DroneOperator` auth fork: Cognito email/password (`Auth/AuthService.swift`),
  wired to `ClaudeDialogClient`'s token provider and gating `RootView`. Google Hosted-UI
  sign-in was **not** ported — it needs a new Google iOS OAuth client + Cognito callback
  URL registration, which is new shared cloud config outside this fork's scope. MQTT/IoT
  telemetry (`MQTTService`, needs the AWS-SDK-iOS SPM package) was also deferred — nothing
  in Phase 0/1/2 needs it yet; see Phase 3.
- ✅ **`RoverOperator.xcodeproj` exists and builds.** Generated via `xcodegen` from
  `project.yml` (deployment target iOS 26.0 — the floor `FoundationModels` requires),
  wired to `RoverNav` as a local Swift package plus ARKit/CoreML/Vision/Speech/
  AVFoundation/AuthenticationServices/FoundationModels. Verified with a real
  `xcodebuild -sdk iphonesimulator build` — **BUILD SUCCEEDED**, Swift 6 strict
  concurrency clean. Regenerate after adding/removing files: `cd eco/client/ios/RoverOperator && xcodegen generate`.
- ✅ `RoverYOLO` CoreML detector (`eco/rover/models/convert_yolo_coreml.py`) — a
  COCO-pretrained YOLOv8n exported to CoreML, **verified with real inference** (correctly
  detected 4 people + 1 bus on a test image), wired into the Xcode target, and confirmed
  compiled into the built `.app` bundle as `RoverYOLO.mlmodelc`. Generic COCO classes only
  (not fine-tuned on the airport ramp — no such dataset exists yet; see `models/README.md`).
- ✅ **IoT telemetry (Phase 3)**: `MQTTService` + minimal `APIClient` forked from
  DroneOperator, `aws-sdk-ios-spm` (AWSCore + AWSIoT, same version DroneOperator pins)
  added as an SPM dependency, `RoverTelemetryPublisher` streams pose/nav-state/tracking
  every 2s. **Reuses the existing `drone/{id}/status` topic and `DroneStatusRule`
  verbatim — zero AWS infra changes.** Confirmed by reading the live IoT policy
  (`drone-policy-dev` only grants `drone/*` topics) and the deployed topic rule
  (`SELECT * FROM 'drone/+/status'`, generic pass-through into `DroneStatusTable`) before
  writing any code, rather than assuming a new `rover/*` namespace was needed. Verified:
  `AWSCore.framework`/`AWSIoT.framework` are physically embedded in the built `.app`.
  **Not yet exercised end-to-end** (would need a real signed-in user driving the app on
  hardware) — the connect/publish code path is the same one already working in the live
  DroneOperator app, just re-pointed at a rover-generated device id.
- ✅ **Synthetic ramp data pipeline** (`eco/rover/models/synthetic/`): real cutouts
  (tug, baggage carts, suitcases) composited onto a real HHR ground-level photo found via
  Wikimedia Commons; fine-tuned YOLOv8n (15 epochs, val mAP50 ≈ 0.95), verified with real
  inference on a held-out image, exported to CoreML. **Proof-of-concept, not
  production** — one real HHR background, non-HHR-specific equipment photos, and the mAP
  number reflects the synthetic distribution rather than real-world generalization; see
  `models/synthetic/README.md` for the honest limitations and next steps (real HHR photos
  are the highest-leverage fix). Not wired into the app.
- ⏭️ Running on a real LiDAR device (simulator can compile but not exercise
  ARKit/FoundationModels at runtime — that's the one thing left that can only be verified
  on hardware).

See [docs/architecture.md](docs/architecture.md) for the full plan, phases, and risks.
