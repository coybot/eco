# Presidio Drone Platform

Open source autonomous drone intelligence. Natural language → on-device reasoning → flight.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

```
iOS App → API Gateway → Lambda → Claude 3.5 Sonnet → IoT Core → Drone → Motors spin
```

## Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│                           AWS (us-west-2)                                  │
│                                                                            │
│                        ┌───────────────────────────────────────────────┐   │
│                        │              API Gateway                      │   │
│                        │  /drones      /drones/{id}    /command        │   │
│                        │     │              │              │           │   │
│                        └─────┼──────────────┼──────────────┼───────────┘   │
│                              │              │              │               │
│                              ▼              ▼              ▼               │
│  Lambda Authorizer    ┌─────────────────────────────────────────┐          │
│  (validates Google/   │            Lambda Functions             │          │
│   Apple ID tokens)───▶│  register   delete/status   command     │          │
│                       └──────────────────┬──────────────────────┘          │
│                                          │                                 │
│                       ┌──────────────────┼──────────────────┐              │
│                       │                  │                  │              │
│                       ▼                  ▼                  ▼              │
│                 ┌──────────┐      ┌───────────┐      ┌───────────┐         │
│                 │ DynamoDB │      │  Bedrock  │      │ IoT Core  │         │
│                 │ Registry │      │ Claude    │      │  (MQTT)   │         │
│                 │ + Status │      └───────────┘      └─────┬─────┘         │
│                 └──────────┘                               │               │
│                                                            │               │
│  Stack: drone-api                                          │               │
└────────────────────────────────────────────────────────────┼───────────────┘
                                            │
                                            │ MQTT: drone/{id}/command
   ┌─────────────┐                                           ▼
   │   iOS App   │◀───── MQTT (WebSocket) ──▶┌───────────────────────────┐
   │DroneOperator│         via IoT Core      │   Companion Computer      │
   └─────────────┘                           │   (Orin / RPi / other)    │
                              │                           │
                             │   ~/presidio/               │
                             │     ├── daemon.py         │
                             │     ├── drone_sdk.py      │
                             │     └── certs/            │
                              │                           │
                              │         │ USB/Serial      │
                              │         ▼                 │
                              │   ┌─────────────┐         │
                              │   │   Flight    │         │
                              │   │ Controller  │         │
                              │   └─────────────┘         │
                              └───────────────────────────┘
```

## Components

| Component | Description |
|-----------|-------------|
| **iOS App** | SwiftUI app with Google/Apple sign-in, drone management, NL commands, real-time MQTT |
| **AWS Backend** | API Gateway + Lambda Authorizer + DynamoDB + IoT Core + Bedrock Claude |
| **Drone Daemon** | Python service on companion computer, MQTT listener, executes LLM-generated code |

### iOS App Details

- **Auth**: Direct Google/Apple OAuth (no Cognito SDK) - tokens sent as Bearer to API
- **Real-time**: MQTT over WebSocket via AWS IoT Core (using AWS Mobile SDK Gen 1)
- **Credentials**: Cognito Identity Pool provides temporary AWS credentials for MQTT
- **Provisioning**: Guides user through drone WiFi hotspot setup flow
- **Bundle ID**: `us.astral.drone`
- **Known warning**: `UIColor created with component values far outside the expected range` - harmless, from Apple's ASWebAuthenticationSession

## Supported Platforms

| Platform | Status | Install |
|----------|--------|---------|
| iOS App | ✅ Ready | `client/ios/` |
| NVIDIA Orin Nano | ✅ Tested | One-liner (see below) |
| Raspberry Pi | ✅ Ready | One-liner (see below) |
| Other Linux | 📝 Adapt | Modify one-liner |

## Quick Start

### 1. Deploy AWS Stack

```bash
cd aws
sam deploy --region us-west-2 --capabilities CAPABILITY_IAM --resolve-s3 --no-confirm-changeset --stack-name drone-api
```

### 2. Get Stack Outputs

```bash
sam list stack-outputs --stack-name drone-api --region us-west-2
```

You'll need:
- `ApiEndpoint` - For iOS app API calls
- `IoTEndpoint` - For drone MQTT connection

### 3. Set Up iOS App

See [client/ios/README.md](client/ios/README.md) for full instructions:

1. Create Xcode project
2. Add Swift source files
3. Configure `AWSConfig.swift` with your stack outputs
4. Enable Sign in with Apple capability
5. Run on device or simulator

### 4. Install on Drone (One-Liner)

Run this on any new drone (Orin, RPi, or other Linux):

```bash
sudo /bin/bash -c "$(curl -fsSL https://astral-drone-installer.s3.amazonaws.com/install.sh)"
```

This will:
1. Download and install all drone software
2. Install dependencies (Python packages, hostapd)
3. **Automatically provision IoT certificates** (Fleet Provisioning)
4. Register the drone with AWS IoT Core
5. Clear WiFi and start a hotspot for app-based setup

After installation, look for a WiFi network like `Presidio-<model>-XXXX` and use the iOS app to complete setup.

**No manual certificate creation needed!** Fleet Provisioning handles this automatically.

## File Structure

```
eco/
├── aws/                           # AWS SAM deployment
│   ├── template.yaml              # CloudFormation (Cognito, DynamoDB, Lambda, IoT)
│   └── src/
│       ├── handler.py             # Command Lambda (LLM + IoT publish)
│       ├── conversations.py       # Chat handler (generates Goals for autonomous ops)
│       └── drones.py              # Registration/listing Lambdas
│
├── client/
│   └── ios/                       # iOS SwiftUI App
│       ├── README.md              # iOS setup instructions
│       ├── project.yml            # XcodeGen project definition
│       └── DroneOperator/
│           ├── App/               # DroneOperatorApp.swift entry point
│           ├── Config/            # AWSConfig.swift (region, apiEndpoint, googleClientId)
│           ├── Models/            # Drone.swift, User.swift
│           ├── Services/          # AuthService, APIClient, DroneSetupService, Logger
│           └── Views/             # Auth, Drones (list, detail, add, setup wizard)
│
├── drone/
│   ├── common/                    # Platform-independent code
│   │   ├── daemon.py              # MQTT listener, executes LLM code + Goals
│   │   ├── drone_sdk.py           # High-level MAVLink functions
│   │   ├── provisioning.py        # WiFi setup (hotspot + HTTP)
│   │   ├── wifi_manager.py        # Network configuration (hostapd-based hotspot)
│   │   ├── fleet_provisioning.py  # AWS IoT Fleet Provisioning client
│   │   ├── factory_reset.py       # Clear WiFi for re-setup
│   │   ├── motor_test.py          # Standalone motor test
│   │   ├── arm_disarm.py          # Standalone arm/disarm
│   │   ├── config.yaml.example    # Template config
│   │   │
│   │   │   # Autonomous Intelligence
│   │   ├── perception.py          # YOLOv8 object detection + distance estimation
│   │   ├── vlm.py                 # Vision-language model (Qwen3-VL)
│   │   ├── nav2_bridge.py         # Nav2 navigation bridge
│   │   ├── reasoning_loop.py      # Main perceive→reason→act loop
│   │   │
│   │   ├── certs/                 # IoT certificates (auto-generated)
│   │   ├── logs/
│   │   └── camera/                # Abstract camera interfaces
│   │       ├── __init__.py
│   │       └── base.py            # Camera base class, CameraFrame dataclass
│   │
│   ├── models/                    # AI models (downloaded, not in git)
│   │   ├── setup_models.py        # Downloads YOLOv8 + VLM (see models/README.md)
│   │   ├── .gitignore             # Excludes large model files
│   │   ├── yolov8n.onnx           # Object detection (~6MB) - downloaded
│   │   ├── yolov8n.engine         # TensorRT version - generated on Jetson
│   │   ├── phi-3-mini-*.gguf      # Small LLM for Orin Nano (~2.3GB) - downloaded
│   │   ├── llama-3-8b-*.gguf      # Larger LLM for Orin NX (~4.7GB) - downloaded
│   │   └── reasoning_llm.gguf     # Symlink → appropriate model for hardware
│   │
│   ├── ros2_ws/                   # ROS2 workspace for navigation (optional)
│   │   └── src/presidio_drone/
│   │       ├── package.xml
│   │       ├── setup.py
│   │       ├── config/
│   │       │   └── nav2_params.yaml   # Nav2 config for indoor drone
│   │       ├── launch/
│   │       │   └── full_stack.launch.py
│   │       └── presidio_drone/
│   │           ├── camera_node.py     # Camera → ROS2 topics
│   │           └── mavlink_bridge.py  # Nav2 → MAVLink
│   │
│   ├── installer/                 # One-liner installer (hosted on S3)
│   │   └── install.sh             # Downloads everything, runs fleet provisioning
│   │
│   ├── platforms/                 # Platform-specific installers
│   │   ├── orin/
│   │   │   └── install.sh         # Orin install (detects Nano vs NX, downloads models)
│   │   └── rpi/
│   │       └── install.sh
│   │
│   └── camera/
│       ├── oakdlite/              # OAK-D Lite implementation (DepthAI 3.x)
│       │   ├── __init__.py
│       │   ├── camera.py          # OakDLiteCamera class
│       │   └── test_camera.py     # Test script
│       └── intelD435i/            # Intel RealSense D435i
│           └── camera.py          # RealSenseCamera class
│
└── README.md
```

## API Endpoints

All endpoints require Cognito JWT authorization.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/drones` | Register a new drone |
| `GET` | `/drones` | List user's registered drones |
| `DELETE` | `/drones/{droneId}` | Unregister a drone |
| `GET` | `/drones/{droneId}/status` | Get last known status |
| `POST` | `/command` | Send natural language command |

### Example: Register Drone

```bash
curl -X POST $API_URL/drones \
  -H "Authorization: Bearer $ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"droneId": "drone-001", "name": "Backyard Drone"}'
```

### Example: Send Command

```bash
curl -X POST $API_URL/command \
  -H "Authorization: Bearer $ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"drone_id": "drone-001", "command": "test motor 1 briefly"}'
```

## WiFi Provisioning (Hotspot Onboarding)

New drones are set up via a local WiFi hotspot, similar to Amazon Ring devices.

### How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                     DRONE SETUP FLOW                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Power on drone (no WiFi configured)                        │
│     └── daemon.py checks: is WiFi configured?                  │
│     └── If NO → start provisioning mode for 5 minutes          │
│     └── Creates open hotspot: "DroneSetup-XXXX" (last 4 of MAC)│
│     └── Starts HTTP server on 192.168.4.1:80                   │
│                                                                 │
│  2. In iOS app, tap "Add Drone" → "Set up new drone"           │
│     └── App shows instructions to join drone's hotspot         │
│     └── User goes to iPhone Settings → WiFi                    │
│                                                                 │
│  3. User joins "DroneSetup-XXXX" (open network, no password)   │
│     └── iPhone gets IP 10.0.0.x via DHCP from drone            │
│                                                                 │
│  4. iOS app detects connection (polls http://192.168.4.1/info) │
│     └── Shows drone ID from response                           │
│     └── User enters home WiFi SSID + password                  │
│     └── App POSTs to http://192.168.4.1/configure              │
│                                                                 │
│  5. Drone receives credentials                                 │
│     └── Stops hotspot                                          │
│     └── Saves WiFi to NetworkManager/wpa_supplicant            │
│     └── Connects to home WiFi                                  │
│     └── Starts normal daemon operation (MQTT to IoT Core)      │
│                                                                 │
│  6. iOS app registers drone with cloud                         │
│     └── User reconnects to home WiFi                           │
│     └── App calls POST /drones with droneId                    │
│     └── Drone appears in user's list                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Provisioning Components

| File | Purpose |
|------|---------|
| `drone/common/daemon.py` | On boot, calls `run_provisioning_if_needed()` |
| `drone/common/provisioning.py` | HTTP server at 192.168.4.1:80, handles `/info` and `/configure` |
| `drone/common/wifi_manager.py` | Platform-agnostic WiFi config (NetworkManager or wpa_supplicant) |
| `DroneSetupService.swift` | iOS client that talks to drone's HTTP server |
| `SetupDroneView.swift` | iOS UI wizard for the setup flow |

### Drone HTTP Endpoints (during provisioning)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` or `/status` | Returns `{"status": "provisioning", "drone_id": "..."}` |
| `GET` | `/info` | Returns `{"drone_id": "...", "mac_address": "...", "hotspot_name": "..."}` |
| `POST` | `/configure` | Body: `{"ssid": "...", "password": "...", "user_id": "..."}` |

### Drone ID Generation

- Generated on first boot: `drone-{uuid[:12]}` (e.g., `drone-a1b2c3d4e5f6`)
- Saved to `/etc/drone-id` (or `~/.drone-id` if not root)
- Hotspot name derived from MAC: `DroneSetup-{mac[-4:]}` (e.g., `DroneSetup-F1A2`)

### Testing Provisioning

```bash
# On drone: Force provisioning mode
ssh $DRONE_HOST "sudo rm /etc/drone-id && sudo python3 ~/drone/common/factory_reset.py && sudo reboot"

# On drone: Test provisioning server manually
ssh $DRONE_HOST "cd ~/drone/common && sudo python3 provisioning.py"

# From laptop on same network as hotspot:
curl http://192.168.4.1/info
curl -X POST http://192.168.4.1/configure -H "Content-Type: application/json" \
  -d '{"ssid": "MyWiFi", "password": "secret123", "user_id": "test"}'
```

### Factory Reset

To move a drone to a new WiFi network or transfer ownership:

```bash
# Interactive (asks for confirmation)
ssh $DRONE_HOST "cd ~/presidio && sudo python3 factory_reset.py"

# Non-interactive (for scripts)
ssh $DRONE_HOST "cd ~/presidio && sudo python3 factory_reset.py --force && sudo reboot"
```

After reboot, the drone will enter provisioning mode (hotspot) for 5 minutes.

## Fleet Provisioning

Drones automatically get their own unique IoT certificates via AWS IoT Fleet Provisioning.

### How It Works

1. **Installer runs** on a new drone
2. **Claim certificate** (shared) is used to connect to IoT Core
3. Drone requests a **unique certificate** via Fleet Provisioning template
4. IoT Core creates a new Thing (`drone-{serial}`) and returns unique cert/key
5. Drone saves certs and registers with the cloud
6. All future connections use the unique certificate

### Benefits

- **No manual cert creation** - Drones self-provision
- **Secure** - Claim cert can only provision, not send commands
- **Scalable** - Add unlimited drones with same installer

### AWS Resources for Fleet Provisioning

| Resource | Purpose |
|----------|---------|
| `DroneProvisioningTemplate` | Defines how new Things are created |
| `DroneFleetProvisioningRole` | IAM role for provisioning operations |
| `DroneClaimPolicy` | Limited policy for claim certificate |
| Claim certificate | Shared cert for initial provisioning only |

### Claim Certificate Location

The claim certificate is stored in S3 (private) and downloaded by the installer:
- `s3://astral-drone-installer/certs/claim-cert.pem`
- `s3://astral-drone-installer/certs/claim-private.key`

### Helper Scripts on Drone

```bash
~/presidio/reset-and-reboot.sh   # Factory reset + immediate reboot
~/presidio/show-logs.sh          # Show recent daemon logs
~/presidio/test-hotspot.sh       # Manually test hotspot creation
```

## Authentication

The iOS app uses direct social sign-in (no Cognito SDK):

- **Sign in with Apple** - Native `ASAuthorizationController`
- **Sign in with Google** - OAuth 2.0 PKCE via `ASWebAuthenticationSession`

ID tokens are sent as `Bearer` tokens to API Gateway, validated by a Lambda Authorizer.

### Setting Up Social Sign-In

#### Apple
1. Enable "Sign in with Apple" capability in Xcode
2. Configure App ID in Apple Developer Portal

#### Google
1. Create OAuth 2.0 credentials in Google Cloud Console (iOS app type)
2. Download the `.plist` file, extract `CLIENT_ID`
3. Add to `AWSConfig.swift`: `static let googleClientId = "..."`
4. Add URL scheme in Info.plist: `com.googleusercontent.apps.{CLIENT_ID}`
5. Update OAuth consent screen in Google Cloud Console (app name appears during sign-in)

## Drone SDK Functions

The LLM generates code using these functions (defined in `drone/common/drone_sdk.py`):

| Function | Description |
|----------|-------------|
| `motor_test(motor_num, throttle_pct=15, duration_sec=2)` | Test single motor (1-4) |
| `arm()` | Arm the drone |
| `disarm()` | Disarm the drone |
| `takeoff(altitude_m)` | Take off to altitude |
| `land()` | Land the drone |
| `goto(lat, lon, alt)` | Fly to GPS coordinates |
| `set_velocity(vx, vy, vz)` | Set velocity (m/s, body frame) |
| `set_yaw(angle_deg, relative=False)` | Set heading |
| `wait(seconds)` | Pause execution |
| `get_position()` | Returns (lat, lon, alt_m) |
| `get_attitude()` | Returns (roll, pitch, yaw) degrees |

## AWS Resources Created

| Resource | Name | Purpose |
|----------|------|---------|
| API Gateway | `drone-api-dev` | REST endpoints with Lambda authorizer |
| Lambda Authorizer | `drone-authorizer-dev` | Validates Google/Apple ID tokens |
| Lambda (3) | `drone-*-dev` | drones.py, handler.py |
| DynamoDB | `drone-registry-dev` | Drone ownership (userId → droneId) |
| DynamoDB | `drone-status-dev` | Cached telemetry (from IoT Rule) |
| IoT Thing | `drone-001-dev` | Device identity |
| IoT Policy | `drone-policy-dev` | MQTT permissions |
| IoT Rule | Writes drone status to DynamoDB |

## Common Tasks

### Deploy AWS Changes
```bash
cd aws && sam deploy --region us-west-2 --capabilities CAPABILITY_IAM --resolve-s3 --no-confirm-changeset --stack-name drone-api
```

### View Drone Logs
```bash
ssh $DRONE_HOST "tail -f ~/presidio/logs/drone.log"
```

### Restart Drone Daemon
```bash
ssh $DRONE_HOST "sudo systemctl restart astral"
```

### Test Flight Controller Directly
```bash
ssh $DRONE_HOST "cd ~/presidio && ./venv/bin/python -c 'from drone_sdk import *; motor_test(1)'"
```

### Delete Everything
```bash
sam delete --stack-name drone-api --region us-west-2
```

## Hardware Tested

| Component | Model |
|-----------|-------|
| Flight Controller | CubeOrange (Hex/ProfiCNC) |
| Firmware | ArduPilot (ArduCopter) |
| Companion Computer | NVIDIA Orin Nano, Raspberry Pi |
| Connection | USB `/dev/ttyACM0` at 115200 baud |
| Depth Camera | OAK-D Lite (Luxonis) |

## Camera (OAK-D Lite)

The OAK-D Lite provides RGB + stereo depth for obstacle avoidance and perception.

### Setup on Orin

```bash
# Install depthai
pip3 install depthai --user

# Install udev rules (for non-root access)
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"' | sudo tee /etc/udev/rules.d/80-movidius.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### Usage

```python
from drone.camera.oakdlite import OakDLiteCamera

with OakDLiteCamera(enable_depth=True) as cam:
    frame = cam.get_frame()
    print(f"RGB: {frame.rgb.shape}")      # (1080, 1920, 3)
    print(f"Depth: {frame.depth.shape}")  # (400, 640) in mm
```

### Test Script

```bash
cd drone/camera/oakdlite
python3 test_camera.py --frames 50        # Capture 50 frames
python3 test_camera.py --save --frames 10 # Save frames to disk
```

### Specs

| Feature | Value |
|---------|-------|
| RGB Resolution | 1920×1080 (configurable up to 4K) |
| Depth Resolution | 640×400 (configurable) |
| Frame Rate | ~11 FPS (RGB + depth) |
| Depth Range | ~200mm - 10m |
| Interface | USB 3.0 (vendor ID `03e7`) |

## Autonomous Intelligence (On-Device Reasoning)

The drone has on-device AI that can reason about goals and decide what actions to take, rather than just following specific commands.

### Architecture

```
User (iOS) ───▶ Claude (AWS) ───▶ Goal ───▶ MQTT ───▶ Drone
                                                        │
                                          ┌─────────────┴─────────────┐
                                          │   On-Device Intelligence  │
                                          │                           │
                                          │  ┌─────────────────────┐  │
                                          │  │  VLM (Qwen3-VL)    │  │
                                          │  └──────────┬──────────┘  │
                                          │             │             │
                                          │  Perceive → Decide → Act  │
                                          │             │             │
                                          │  ┌──────────▼──────────┐  │
                                          │  │  YOLOv8 + Nav2      │  │
                                          │  └─────────────────────┘  │
                                          │             │             │
                                          │  (If stuck & online)      │
                                          │      Ask cloud ───────────┼──▶ Cloud
                                          │  (Mission phases)        │
                                          └───────────────────────────┘
```

### How It Works

1. **User says:** "Go inside and check if anyone needs help"
2. **Cloud generates a Mission** (phases with objectives).
3. **On-device VLM + Nav2:**
   - VLM sees the camera image and mission phase
   - Outputs actions (navigate to point, navigate to object, capture photo, report)
   - Nav2 executes navigation; perception (YOLOv8) provides object locations
4. **If stuck, can ask cloud for advice**

### AI Models

| Model | Purpose | Size | Hardware |
|-------|---------|------|----------|
| **YOLOv8n** | Object detection (person, door, car, etc.) | ~6 MB | All devices |
| **Qwen3-VL 2B** | VLM (mission autonomy) | ~1.5 GB | Orin Nano |
| **Qwen3-VL 8B** | VLM (mission autonomy) | ~5.8 GB | Orin NX, AGX 32GB |
| **Qwen3-VL 30B** | VLM (mission autonomy) | ~19 GB | Orin AGX 64GB |

The install script automatically selects the right VLM size based on detected hardware (see `drone/models/README.md`).

### Key Files

| File | Purpose |
|------|---------|
| `drone/common/perception.py` | YOLOv8 detection + distance estimation |
| `drone/common/vlm.py` | VLM (Qwen3-VL via llama-cpp-python) |
| `drone/common/nav2_bridge.py` | Nav2 navigation bridge |
| `drone/common/reasoning_loop.py` | Main perceive→reason→act loop |
| `drone/models/setup_models.py` | Downloads and configures AI models |
| `aws/src/conversations.py` | Claude prompt to generate Goals |

### Action Primitives

The on-device LLM composes these simple actions to achieve complex goals:

| Action | Description |
|--------|-------------|
| `move_forward(distance_m)` | Move forward (max 5m) |
| `turn_left(degrees)` / `turn_right(degrees)` | Rotate |
| `ascend(distance_m)` / `descend(distance_m)` | Vertical movement |
| `navigate_toward(target)` | Navigate to detected object |
| `look_around()` | 360° scan, describe surroundings |
| `take_photo()` | Capture and upload photo |
| `report(message)` | Send message to user |
| `ask_for_help(question)` | Ask Claude (or local LLM) for advice |
| `goal_complete(summary)` | Signal goal achieved |
| `goal_failed(reason)` | Signal goal cannot be completed |

### Deploying to Drone (Full Stack with AI)

**Option 1: Remote install from laptop (recommended for fleets)**
```bash
cd drone/platforms/orin
./install.sh --remote orin-admin@quadcopter --password "<ssh-password>" --start
```

This copies files (including the pre-built `llama-cpp-python` CUDA wheel from `drone/wheels/`), installs everything, and starts the service. With the wheel, install takes ~5 minutes instead of ~50.

**Option 2: One-liner installer (new drones, downloads from S3)**
```bash
sudo /bin/bash -c "$(curl -fsSL https://astral-drone-installer.s3.amazonaws.com/install.sh)"
```

**Option 3: Local install (on the Orin itself)**
```bash
cd drone/platforms/orin
./install.sh --start
```

This will:
1. Copy all drone code including autonomous intelligence modules
2. Detect Orin variant (Nano vs NX vs AGX)
3. Install `llama-cpp-python` with CUDA GPU support (pre-built wheel or ~45 min build)
4. Download appropriate AI models (~2-5 GB depending on variant)
5. Install Python dependencies (onnxruntime, ultralytics, huggingface_hub)
6. Run fleet provisioning (get device certificates)
7. Set up and start systemd service

**Option 3: Manual model setup**
```bash
# On the drone:
cd ~/drone-api/models

# Download all models
python setup_models.py

# Or download specific models
python setup_models.py --yolo-only      # Just YOLOv8 (6MB)
python setup_models.py --qwen3-vl-2b    # Small VLM for Nano
python setup_models.py --qwen3-vl-8b    # 7B VLM for NX/AGX32
python setup_models.py --qwen3-vl-32b   # 32B VLM for AGX64

# Configure for specific hardware
python setup_models.py --setup-device --variant nano  # or nx, agx32, agx64

# List installed models
python setup_models.py --list
```

### Testing Autonomous Behavior

```bash
# On drone: Test perception
cd ~/drone-api
./venv/bin/python -c "
from perception import PerceptionService
p = PerceptionService()
print(p.get_scene_description())
p.release()
"

# On drone: Test mission loop (requires VLM models)
cd ~/drone-api
./venv/bin/python reasoning_loop.py
```

### ROS2 Navigation (Optional)

For advanced obstacle avoidance and SLAM, the drone can use Isaac ROS:

```bash
# Build ROS2 workspace
cd drone/ros2_ws
colcon build

# Launch full stack (camera + SLAM + Nav2)
ros2 launch presidio_drone full_stack.launch.py
```

Files:
- `drone/ros2_ws/src/presidio_drone/` - ROS2 package
- `drone/ros2_ws/src/presidio_drone/config/nav2_params.yaml` - Navigation config
- `drone/ros2_ws/src/presidio_drone/launch/full_stack.launch.py` - Launch file

## Safety Notes

⚠️ **Before any motor/flight tests:**
1. Remove propellers or secure the drone
2. Ensure safety switch is disengaged (press and hold until LED changes)
3. Have battery disconnect ready
4. Keep clear of motors

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Unauthorized" in app | Check `apiEndpoint` and `googleClientId` in `AWSConfig.swift` |
| "Drone already registered" | Drone registered to another account |
| No status updates | Check drone is online, IoT Rule writing to DynamoDB |
| "Motor Emergency Stopped" | Press safety switch for 3-5 seconds |
| Daemon not receiving commands | Check `systemctl status astral`, verify IoT certs |
| LLM generates bad code | Improve prompt in `handler.py` SYSTEM_PROMPT |
| Provisioning timeout | Drone hotspot lasts 5 minutes; run `reset-and-reboot.sh` to retry |
| Can't find drone hotspot | Check WiFi interface exists, run `nmcli radio wifi on` |
| "Local network prohibited" (iOS) | Add `NSLocalNetworkUsageDescription` to Info.plist |
| Hotspot requires password | Bug in wifi_manager.py; ensure open AP mode is used |
| WiFi interface wrong | Orin Nano uses `wlP1p1s0`, RPi uses `wlan0` - check wifi_manager.py |

## Extending

### Add New Drone

Just run the one-liner on any Linux device:

```bash
sudo /bin/bash -c "$(curl -fsSL https://astral-drone-installer.s3.amazonaws.com/install.sh)"
```

The drone will:
1. Auto-provision its IoT certificate
2. Start a WiFi hotspot (`DroneSetup-XXXX`)
3. Wait for the iOS app to configure WiFi

### Add new SDK function
1. Add function to `drone/common/drone_sdk.py`
2. Update SYSTEM_PROMPT in `aws/src/handler.py`
3. Deploy: `sam deploy` + scp to drone

### Add new platform
1. Copy `drone/platforms/rpi/` to `drone/platforms/newplatform/`
2. Modify `install.sh` for platform-specific dependencies
3. Test and submit PR

---

## Notes for AI Assistants

Context for future sessions working on this codebase:

### iOS App Architecture (Updated Jan 2026)

- **No Cognito SDK** - Direct Google/Apple OAuth, tokens sent as Bearer to API
- **MQTT via AWS Mobile SDK (Gen 1)** - Uses `AWSIoTDataManager` for real-time updates
- **Cognito Identity Pool** - Provides AWS credentials for MQTT (WebSocket + SigV4)
- **AWSConfig.swift** needs: `region`, `apiEndpoint`, `googleClientId`, `identityPoolId`, `iotEndpoint`, `userPoolId`
- Polling is only a fallback if MQTT doesn't deliver within 60 seconds

### Key Files

| File | Purpose |
|------|---------|
| `aws/template.yaml` | SAM template - API Gateway, Lambda, DynamoDB, IoT, Fleet Provisioning |
| `aws/src/authorizer.py` | Lambda that validates Google/Apple ID tokens |
| `aws/src/handler.py` | Command handler - calls Bedrock Claude, publishes to IoT |
| `aws/src/drones.py` | CRUD for drone registry and status (includes factory reset via IoT) |
| `client/ios/.../AuthService.swift` | Google/Apple OAuth flows, stores token in Keychain |
| `client/ios/.../APIClient.swift` | REST client, includes status polling |
| `client/ios/.../DroneSetupService.swift` | Talks to drone during WiFi provisioning |
| `drone/common/daemon.py` | Main loop - provisioning check, MQTT listener, heartbeats |
| `drone/common/provisioning.py` | HTTP server for hotspot-based setup |
| `drone/common/fleet_provisioning.py` | AWS IoT Fleet Provisioning client |
| `drone/common/wifi_manager.py` | WiFi config + hostapd-based hotspot |
| `drone/installer/install.sh` | One-liner installer (hosted on S3) |
| `drone/common/camera/base.py` | Abstract Camera class, CameraFrame dataclass |
| `drone/camera/oakdlite/camera.py` | OAK-D Lite implementation (DepthAI 3.x) |

### Key Behaviors

1. **Heartbeats** - Drones send status every 5 seconds; shown as offline after 10 seconds
2. **Delete = Factory Reset** - Deleting a drone in the app sends a factory reset command via IoT, clearing WiFi and restarting hotspot
3. **Fleet Provisioning** - Drones auto-provision unique certs on first install

### Common Issues Encountered

1. **UIColor warning** - Harmless, from Apple's ASWebAuthenticationSession
2. **401 from API** - Check Lambda authorizer logs in CloudWatch
3. **Drone not receiving commands** - Check IoT certificates and policy
4. **Provisioning not starting** - `is_wifi_configured()` must exclude hotspot connections
5. **Hotspot not appearing** - Ensure hostapd is installed (`apt install hostapd`)
6. **Wrong WiFi interface** - Orin Nano uses `wlP1p1s0`, not `wlan0`

### MQTT & Real-Time Communication (Jan 2026)

**iOS MQTT - Use AWS Mobile SDK (Gen 1), NOT the newer Swift SDK:**
- The `AWS IoT Device SDK for Swift` (developer preview) crashes during SigV4 signing
- Use `AWSIoTDataManager` from the legacy iOS SDK - it's stable and works
- Dependencies in `project.yml`: `AWSCore`, `AWSIoT` (NOT `AWSIoTDeviceSDK`)
- Reduce AWS SDK log noise: `AWSDDLog.sharedInstance.logLevel = .warning`

**Cognito Identity Pool + IoT Policy:**
- IAM permissions on the Cognito authenticated role are NOT enough
- You must also **attach an IoT Policy to the Cognito Identity ID**:
  ```bash
aws iot attach-policy --policy-name drone-cognito-policy-dev \
  --target "us-west-2:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  ```
- Without this, WebSocket connects but MQTT CONNECT is rejected (server closes connection)

**Drone MQTT - Use AWS IoT Device SDK v2 for Python:**
- Package: `awsiotsdk` (NOT `AWSIoTPythonSDK` which is v1)
- Install: `pip install awsiotsdk`
- The v2 SDK has different API - uses callbacks, not blocking calls

**Backend Ack/Error Pattern (avoid unnecessary polling):**
- When backend receives a chat message, it should:
  1. Check if drone is online → send `"error"` via MQTT if offline
  2. Send `"ack"` via MQTT when forwarding to drone
  3. iOS waits for MQTT response (up to 60s), only polls if no MQTT at all
- Message types: `ack`, `error`, `text`, `image`, `image_choice`

### Vision / Image Analysis

**LLM cannot "see" URLs - you must fetch and encode the image:**
```python
# WRONG - LLM hallucinates (says "park with benches" for any image)
user_content[0]['text'] += f"\n[Image: {img_url}]"

# CORRECT - Actually send the image to Claude 3.5 Sonnet vision
img_data = urllib.request.urlopen(presigned_url).read()
img_base64 = base64.b64encode(img_data).decode('utf-8')
user_content.append({
    'type': 'image',
    'source': {
        'type': 'base64',
        'media_type': 'image/jpeg',
        'data': img_base64
    }
})
```

**Model options for vision:**
| Model | Vision | Notes |
|-------|--------|-------|
| `anthropic.claude-3-5-sonnet-*` | ✅ | Current default, best vision quality |
| `us.amazon.nova-pro-v1:0` | ✅ | Decent quality, lower cost |
| `anthropic.claude-3-haiku-*` | ✅ | Fast, cheap, good for simple tasks |

### Camera Auto-Detection

The drone supports multiple cameras - detection is automatic:
- **OAK-D Lite** (Luxonis) - Uses DepthAI, vendor ID `03e7`
- **Intel RealSense D435i** - Uses `pyrealsense2`

Camera code structure:
```
drone/camera/
├── __init__.py              # get_camera(), list_cameras()
├── common/
│   ├── base.py              # Abstract Camera class
│   └── auto.py              # Auto-detection logic
├── oakdlite/camera.py       # OakDLiteCamera
└── intelD435i/camera.py     # IntelD435iCamera
```

**RealSense gotchas:**
- May fail at default resolution - code tries multiple: 1280x720 → 640x480
- "Device busy" error means another process (e.g. `video_producer.py`) has the camera

### Debugging Tips

```bash
# Check if drone is online
aws iot-data get-thing-shadow --thing-name drone-XXXXX --region us-west-2

# Check IoT policy attached to Cognito identity  
aws iot list-attached-policies --target "us-west-2:identity-id" --region us-west-2

# View Lambda logs for chat handler
aws logs tail /aws/lambda/drone-api-ChatResponseFunction --follow --region us-west-2

# Test MQTT from drone manually
python3 -c "from awsiotsdk import mqtt5; print('SDK loaded')"
```

### Live Video Streaming (Jan 2026)

**Architecture:**
```
iOS App ──WebRTC──▶ AWS KVS Signaling ◀──WebRTC── Drone (video_producer.py)
         └─────────── MQTT ────────────────────▶ (start/stop/heartbeat)
```

**How it works:**
1. iOS app sends MQTT command `{"action": "start"}` to `drone/{id}/video/command`
2. Drone daemon starts `video_producer.py` as subprocess
3. `video_producer.py` connects to KVS signaling as MASTER
4. iOS app connects as VIEWER, exchanges SDP offer/answer
5. WebRTC peer connection established, video flows
6. iOS sends heartbeats every 5s; drone auto-stops after 10s without heartbeat

**Key files:**
| File | Purpose |
|------|---------|
| `drone/common/video_producer.py` | WebRTC producer - camera → KVS signaling |
| `drone/common/daemon.py` | MQTT handler for video start/stop/heartbeat |
| `VideoStreamView.swift` | iOS WebRTC viewer + MQTT control |
| `aws/template.yaml` | IoT policies for video topics |

**Dependencies for video streaming:**
```bash
pip install aiortc websockets av pyrealsense2
```

**MQTT topics:**
- `drone/{id}/video/command` - iOS publishes `{action: "start"|"stop"|"heartbeat"}`
- `drone/{id}/video/status` - Drone publishes `{streaming: true|false}`

**IoT Policy requirements (in template.yaml):**
- iOS (Cognito) needs: `iot:Publish` on `drone/*/video/command`
- Drone needs: `iot:Subscribe` + `iot:Receive` on `drone/*/video/command`

**Common video issues:**
| Issue | Cause | Fix |
|-------|-------|-----|
| No SDP answer | Drone not receiving start command | Check IoT policy, redeploy AWS template |
| "Frame XYZ" test pattern | Camera not detected | Check camera module, pyrealsense2 installed |
| Video stops after ~10s | Heartbeats not reaching drone | Check MQTT connection stability |
| ModuleNotFoundError | Missing dependencies | `pip install aiortc websockets av` |

### Development & Debugging (SSH to Drone)

**Quick SSH access** (replace host and install paths with yours; prefer SSH keys):

```bash
ssh orin-admin@<tailscale-or-lan-host>

# Check daemon status
ssh orin-admin@<host> "ps aux | grep daemon.py"

# View live logs (default install layout is often ~/drone-api/logs/)
ssh orin-admin@<host> "tail -f ~/drone-api/logs/drone.log"
```

**Updating drone code (without full reinstall):**

```bash
INSTALL=orin-admin@<host>:~/drone-api/

scp drone/common/daemon.py "$INSTALL"

scp drone/common/daemon.py "$INSTALL" && \
ssh orin-admin@<host> "sudo systemctl restart drone-api || (sudo pkill -f daemon.py; sleep 2; cd ~/drone-api && nohup venv/bin/python daemon.py &)"
```

**Installing new Python dependencies:**

```bash
ssh orin-admin@<host> "~/drone-api/venv/bin/pip install websockets aiortc"
```

**Testing telemetry directly:**
```bash
ssh orin-admin@100.69.83.8 "cd /home/orin-admin/presidio && venv/bin/python -c '
from drone_sdk import get_battery, get_telemetry
print(\"Battery:\", get_battery())
print(\"Telemetry:\", get_telemetry())
'"
```

**Checking flight controller connection:**
```bash
ssh orin-admin@100.69.83.8 "ls -la /dev/serial/by-id/"
# Should show something like: usb-Hex_ProfiCNC_CubeOrange_...
```

**Battery shows -1%:**
- This is normal if no battery is connected (FC powered via USB)
- When battery is connected, percentage requires current sensor OR voltage-based estimation
- Configure with: `configure_battery_monitoring(n_cells=4, capacity_mah=5000)`

### Autonomous Intelligence (Jan 2026)

**Goal-based architecture:**
- Claude no longer generates Python code for complex tasks
- Instead, Claude generates a **Goal** (objective, success_criteria, constraints, priority)
- The drone's on-device VLM (Qwen3-VL) sees the scene and decides actions; Nav2 executes
- This allows the drone to handle novel situations we didn't anticipate

**Key concepts:**
- **Goal**: What to accomplish (generated by Claude)
- **Primitives**: Low-level actions the LLM can invoke (move, turn, look, report)
- **Reasoning Loop**: perceive → reason (LLM) → act → repeat
- **Cloud Fallback**: If stuck, ask Claude for advice (if online), else local LLM tries harder

**Model selection by hardware:**
| Orin Variant | RAM | Model | Reason |
|--------------|-----|-------|--------|
| Nano | 8GB | Qwen3-VL 2B (~1.5GB) | Mission autonomy |
| NX / AGX 32GB | 16–32GB | Qwen3-VL 8B (~5.8GB) | Mission autonomy |
| AGX 64GB | 64GB | Qwen3-VL 30B (~19GB) | Mission autonomy |

**Install script auto-detection:**
```bash
# In drone/platforms/orin/install.sh:
detect_orin_variant() {
    # Checks /proc/device-tree/model or memory size
    # Returns "nano" or "nx"
}
```

**Model symlink:**
- `reasoning_llm.gguf` → `phi-3-mini-*.gguf` (on Nano)
- `reasoning_llm.gguf` → `llama-3-8b-*.gguf` (on NX)
- `vlm.py` loads `vlm.gguf` and `vlm_mmproj.gguf` (variant-specific content from setup_models)

**When to use Goal vs Execute:**
| User Request | Action | Reason |
|--------------|--------|--------|
| "Take off to 3 meters" | `execute` | Simple, specific command |
| "Land" | `execute` | Simple command |
| "Find the nearest person" | `mission` | VLM + Nav2 |
| "Explore this building" | `mission` | Multi-phase mission |
| "Search for anyone needing help" | `mission` | VLM + perception |

**Testing on drone:**
```bash
# Test perception
./venv/bin/python -c "from perception import PerceptionService; p = PerceptionService(); print(p.get_scene_description())"

# Test mission loop (requires VLM)
./venv/bin/python reasoning_loop.py
```

### Commands

```bash
# Regenerate Xcode project
cd client/ios/DroneOperator && xcodegen generate

# Deploy AWS (REQUIRED after template.yaml changes)
cd aws && sam build && sam deploy

# Deploy single file to drone
scp drone/common/daemon.py orin-admin@100.69.83.8:/home/orin-admin/presidio/

# Deploy autonomous intelligence modules
scp drone/common/{perception,vlm,nav2_bridge,reasoning_loop}.py orin-admin@100.69.83.8:/home/orin-admin/drone-api/

# Download AI models on drone
ssh orin-admin@100.69.83.8 "cd ~/presidio/models && python setup_models.py"

# Test provisioning locally
cd drone/common && sudo python3 provisioning.py

# Check drone logs
ssh orin-admin@100.69.83.8 "tail -50 /home/orin-admin/presidio/logs/drone.log"
```

---

## License

Copyright 2026 Presidio Autonomy, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
