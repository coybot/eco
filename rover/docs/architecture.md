# Rover architecture

## Hardware grounding: WAVE ROVER

- **ESP32 driver board = "lower computer."** 4WD differential drive, onboard 9-axis IMU,
  WiFi + Bluetooth, UART/USB @115200, ESP-NOW.
- Accepts **JSON commands**. Motion: `{"T":1,"L":<left>,"R":<right>}` — left/right wheel
  target velocities (m/s, negative = reverse, 0 = stop). Feedback flow streams chassis + IMU.
  HTTP transport: `GET /js?json=<url-encoded JSON>`.
- In Waveshare's reference designs a Raspberry Pi / Jetson is the "upper computer"
  (`ugv_rpi` / `ugv_jetson` Flask apps). **We replace it with an iPhone/iPad.** Same
  base-agnostic JSON protocol across all Waveshare UGVs → the base can later be swapped for
  a larger one (UGV Beast / custom) with no software rewrite.
- ⚠️ **No wheel encoders on the base WAVE ROVER.** Odometry comes from **ARKit
  visual-inertial tracking**, not the wheels — a stronger indoor source anyway.

## Decisions

- **Device:** iPhone Pro *and* iPad Pro (both LiDAR + ARKit + Neural Engine). Universal app.
- **AI split:** **All-Apple on-device is primary; Claude cloud is an escalation path**,
  not the default:
  - **STT:** `SFSpeechRecognizer`, on-device mode (`requiresOnDeviceRecognition`).
  - **TTS:** `AVSpeechSynthesizer`, on-device voices — the rover must be able to speak
    even in a WiFi dead spot mid-hallway; cloud TTS would add latency and a failure mode
    at exactly the wrong moment. Tradeoff: more robotic-sounding than a cloud premium voice.
  - **Reasoning:** Apple's on-device **Foundation Models framework** (`FoundationModels`,
    the actual "Apple Intelligence" surface available to third-party developers — Apple
    does **not** expose its bigger server-side/Private Cloud Compute model to apps)
    parses ground-crew speech into a structured `RoverIntent` (navigate/stop/greet +
    destination) via guided generation, entirely offline. **Claude (cloud, reusing eco's
    AWS API) is only called when the on-device model flags `needsEscalation`** —
    open-ended small talk or general-knowledge questions it isn't built for — or when
    parsing fails.
  - **Vision:** on-device CoreML (custom-trained detector; see "Custom models" below).
- **First milestone:** autonomous point-to-point navigation (control loop is its prerequisite).

### Custom models

Navigation itself is model-free (ARKit pose + LiDAR mesh → costmap → A* → pursuit — pure
geometry). The one custom/fine-tuned model is a **vision detector** (`Detector.swift`):
reuse the drone platform's YOLO lineage (`eco/drone/models/`, YOLOv8n), converted to
CoreML, ideally fine-tuned on ramp classes (people, baggage carts/tugs, jet bridges,
aircraft, ground service equipment) instead of generic COCO classes. No ramp dataset
exists yet — see `eco/rover/models/README.md` "Synthetic data" for how to bootstrap one.

## Software layers

| Layer | Where | Component |
|---|---|---|
| Odometry / mapping | on-device | `ARSessionManager` (ARKit VIO pose + LiDAR mesh) |
| Perception | on-device | `Detector` (Vision + CoreML YOLO); LiDAR depth via `ARSessionManager.forwardClearance` |
| Costmap | on-device | `CostmapBuilder` (mesh → `RoverNav.Costmap`) |
| Global plan | on-device | `RoverNav.AStarPlanner` |
| Local control | on-device | `RoverNav.PursuitController` → `RoverNav.DifferentialDrive` |
| Safety | on-device | `ObstacleGuard` (LiDAR depth + comms watchdog + tip detection) |
| Actuation | on-device → base | `RoverControl` (JSON over WiFi HTTP) |
| Orchestration | on-device | `NavigationController` (closed loop on ARKit pose) |
| Persistence | on-device | `WorldMapStore` (`ARWorldMap` + named places) |
| Voice in/out | on-device | `SpeechIn` (SFSpeechRecognizer), `SpeechOut` (AVSpeechSynthesizer) |
| Reasoning (primary) | on-device | `DialogAgent` + `RoverIntent` (Apple FoundationModels, guided generation) |
| Reasoning (escalation) | cloud | `ClaudeDialogClient` → POST `/rover/converse` → `aws/src/rover.py` (open-ended chat only; deployed and live) |
| Cloud / fleet | on-device → cloud | `MQTTService` + `RoverTelemetryPublisher` → `drone/{id}/status` (reuses the existing `DroneStatusRule`/`DroneStatusTable`, no new IoT policy) |

`RoverNav` is deliberately free of ARKit/UIKit so it is unit-testable off-device and
embeddable in the app as a local Swift package.

## Phases

- **Phase 0 — control loop (prereq):** WiFi connect, `RoverControl` JSON drive, ARKit pose,
  IMU telemetry, teleop UI + e-stop + comms-loss watchdog. → `DriveView`.
- **Phase 1 — autonomous point-to-point (first milestone):** costmap → A* → pursuit closed on
  ARKit pose → reactive obstacle stop; persist/relocalize `ARWorldMap`. → `NavigateView`.
- **Phase 2 — voice & conversation:** push-to-talk `SpeechIn` (on-device STT) →
  `DialogAgent` (on-device Foundation Model parses `RoverIntent`, dispatches to
  `NavigationController`/stop, escalates to `ClaudeDialogClient` only for open-ended
  chat) → `SpeechOut` (on-device TTS). Natural-language destinations resolve against
  `WorldMapStore` saved places. Person avoidance / follow-me is a later addition here.
- **Phase 3 — cloud/fleet:** ✅ pose/nav-state telemetry now publishes to AWS IoT (see
  Software layers table). Still open: remote monitoring UI, teleop override (subscribe
  side — `MQTTService.subscribe` exists but nothing calls it yet), and device
  provisioning/registration via the existing eco `/drones` flow.
- **Phase 4 — ramp end-to-end demo** (+ optional off-device sim harness extension).

## Risks / constraints

- **Chassis size/payload:** WAVE ROVER is a small dev chassis — real checked baggage
  (routinely 20–25 kg per bag) is well beyond what it can tow. Treat it strictly as a
  proof-of-concept for the software stack; the base-agnostic JSON protocol means
  upgrading to a real ramp-rated tug/cart later needs no software rewrite.
- **LiDAR in direct sunlight:** the iPhone/iPad LiDAR scanner is a known-weaker performer
  in bright outdoor sunlight (IR interference from the sun reduces range/quality) —
  this was validated primarily for indoor use. An open, sun-exposed tarmac is close to
  the worst case for it. Needs on-ramp testing; fallback is leaning more on the
  monocular-vision costmap path and closer obstacle margins if LiDAR degrades.
- **No wheel encoders** → rely on ARKit VIO; a large, flat, visually-repetitive apron is
  a feature-poor environment for VIO much like a long corridor is — validate tracking
  robustness there (mitigate with `ARWorldMap` relocalization + anchors / painted-line
  markers already on the ramp).
- **No "building WiFi" on a ramp:** the STA-mode assumption (ESP32 joins existing WiFi so
  the phone keeps internet for cloud AI) needs an actual airport ops WiFi network or a
  dedicated one — don't assume ambient coverage like an indoor venue would have.
- **Airport movement-area rules:** a ramp is a far higher-stakes regulatory environment
  than a lobby — secured movement areas, aircraft/jet-blast proximity rules, and ramp
  safety protocols apply. `DialogAgent`'s system prompt already defers any safety-sounding
  request to a human; this needs real operational sign-off before live ramp testing, not
  just a software safeguard.
- **Phone mounting & power:** mount forward-facing; power from the rover's onboard UPS/USB;
  needs weatherproofing for outdoor use (not addressed yet).
- **ARKit needs a real device** — not the Simulator (see verification).
- **iPad on the small chassis** is heavier / higher CoG.

## Verification

- **Nav core (off-device, today):** `cd eco/rover/nav/RoverNav && swift test` — 12 tests,
  including a kinematic sim that plans around a corner and drives to the goal collision-free
  with < 0.3 m final error.
- **Phase 0 (device):** connect over WiFi; tap-drive forward/turn/stop; confirm motion +
  IMU/chassis telemetry; trigger e-stop and comms-loss watchdog (kill AP → rover stops).
- **Phase 1 (device):** map a section of ramp/apron; goal ~10 m away around a parked cart or
  equipment with a person walking through; reach it collision-free; log ARKit pose vs. goal.
  Repeat after relocalizing a saved `ARWorldMap`, and repeat again in direct sunlight to
  check the LiDAR-degradation risk above.
- **Phase 2:** spoken destination → correct anchor → navigates there; measure STT accuracy +
  end-to-end latency.
