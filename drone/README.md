# Coybot Drone - Autonomous On-Device Intelligence

On-device AI for autonomous drone operation. Natural language goals → local reasoning → flight.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](../LICENSE)

## Overview

This is the drone-side software for the Coybot platform. It runs on NVIDIA Jetson Orin and enables:

- **Mission autonomy**: VLM (Qwen3-VL) sees the environment and decides actions; Nav2 executes navigation
- **Real-time perception**: YOLOv8 object detection with distance estimation
- **Autonomous navigation**: SLAM + obstacle avoidance via Nav2
- **Cloud support**: Receives missions from the cloud; can ask for help when stuck

> **Looking for the L5 fleet controller?** The zero-intervention multi-agent navigation
> result (quad + rover, 16-scenario benchmark, validated through ArduPilot SITL) is a
> separate, **classical** stack — no AI, no models, nothing to download. See
> [`common/L5.md`](common/L5.md) to reproduce it and run it on your own vehicle.

## Cloud vs local SSH control

Two supported paths share the same install tree (`~/drone-api/` on the Jetson by default):

| Mode | Entry point | When to use |
|------|-------------|-------------|
| **Cloud** | `daemon.py` (systemd service) | Production: AWS IoT MQTT, iOS app, LLM-generated code via `drone_sdk` |
| **Local / bench** | `run_prompt.py` | Development and flight tests without the cloud: hardened Track A pipeline over SSH |

Copy `drone/common/config.yaml.example` to `config.yaml` and set `serial_port` (prefer a stable `/dev/serial/by-id/...` path), `iot_endpoint`, and certs for cloud mode. For SSH-only runs, `run_prompt.py` only needs MAVLink + camera + model weights; it reads `serial_port` / `baud_rate` from `config.yaml` when present so it stays aligned with `drone_sdk.py`.

## Hardware Requirements

| Component | Supported Options |
|-----------|-------------------|
| Companion Computer | NVIDIA Jetson Orin Nano, Orin NX, AGX Orin (32GB / 64GB) |
| Flight Controller | ArduPilot-compatible (Pixhawk, Cube, etc.) |
| Depth Camera | Intel RealSense D435i, OAK-D Lite |
| Connectivity | WiFi (for provisioning), LTE/5G (for cloud) |

## Quick Install (Jetson Orin)

### Remote Install (from your laptop to the Orin)

```bash
cd drone/platforms/orin

# With password authentication
./install.sh --remote orin-admin@quadcopter --password "<ssh-password>" --start

# With SSH key authentication
./install.sh --remote jetson@192.168.1.50 --start
```

### Local Install (on the Orin itself)

```bash
# Clone the repo
git clone https://github.com/coybot/eco.git
cd eco/drone/platforms/orin

# Install and start immediately
./install.sh --start
```

This will:
1. Install system dependencies (ROS2 Humble, Nav2 for AGX)
2. Set up Python environment
3. Install `llama-cpp-python` with CUDA GPU support (uses pre-built wheel if available)
4. Run fleet provisioning (get device certificates)
5. Download AI models (YOLOv8 + VLM per variant; see [models/README.md](models/README.md))
6. Configure and start systemd services

### Pre-built Wheel (fast installs)

Building `llama-cpp-python` with CUDA from source takes **~45 minutes** on the Orin Nano. To skip this on repeat installs, a pre-built wheel is included in `drone/wheels/`:

```
drone/wheels/llama_cpp_python-0.3.16-cp310-cp310-linux_aarch64.whl  (260MB, CUDA, sm_50-sm_89)
```

The install script automatically detects and uses this wheel. When using `--remote`, the wheel is copied to the drone along with the rest of the source files.

If no wheel is found, the script builds from source and saves the resulting wheel to `drone/wheels/` for next time.

**To rebuild the wheel** (e.g. after upgrading llama-cpp-python):

```bash
# On the Orin:
source ~/drone-api/venv/bin/activate
export PATH=/usr/local/cuda/bin:$PATH CUDACXX=/usr/local/cuda/bin/nvcc CMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc
CMAKE_ARGS="-DGGML_CUDA=on" pip wheel llama-cpp-python==0.3.16 --wheel-dir ~/wheels --no-deps

# Copy back to your laptop:
scp orin-admin@quadcopter:~/wheels/*.whl drone/wheels/
```

> **Note:** The `.whl` file is gitignored (too large). Keep it in your local clone or host it on S3.

## Manual Installation

### 1. System Dependencies (JetPack 5.x+)

```bash
# ROS2 Humble
sudo apt install ros-humble-desktop ros-humble-nav2-bringup

# Isaac ROS (for SLAM)
# Follow: https://nvidia-isaac-ros.github.io/getting_started/index.html

# Python build tools
sudo apt install python3-pip python3-venv
```

### 2. Python Environment

```bash
cd /home/$USER/coybot
python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt

# llama-cpp-python with CUDA (use pre-built wheel if available, otherwise ~45 min build)
export PATH=/usr/local/cuda/bin:$PATH
export CUDACXX=/usr/local/cuda/bin/nvcc
export CMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc

# Option A: Pre-built wheel (seconds)
pip install drone/wheels/llama_cpp_python-*-linux_aarch64.whl

# Option B: Build from source (~45 min on Orin Nano)
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --force-reinstall --no-cache-dir
```

> **Important:** On Jetson, `/usr/local/cuda/bin` is not in PATH by default. You must export `CUDACXX` and `CMAKE_CUDA_COMPILER` or the CUDA build will fail with `CMAKE_CUDA_COMPILER-NOTFOUND`.

### 3. AI Models

```bash
cd models
python setup_models.py --yolo-only
python setup_models.py --qwen3-vl-2b   # Nano (small VLM)
# or --qwen3-vl-8b (NX/AGX 32GB), --qwen3-vl-32b (AGX 64GB)
python setup_models.py --setup-device --variant nano  # or nx, agx32, agx64
```

See [models/README.md](models/README.md) for which models each variant uses. Same filenames (`vlm.gguf`, `vlm_mmproj.gguf`, `yolov8n.onnx`) on all devices; the VLM file content is variant-specific.

### 4. Configuration

```bash
cp config.yaml.example config.yaml
# Edit config.yaml with your settings
```

Key settings:
```yaml
drone_id: "your-drone-id"
camera_type: "oakd"  # or "realsense"
flight_controller:
  connection: "/dev/ttyTHS1"
  baud: 921600
```

### 5. Fleet Provisioning (automatic)

Fleet provisioning runs automatically during install to get device certificates.
If it fails, you can retry manually:

```bash
python3 fleet_provisioning.py
```

Or connect via iOS app for WiFi + provisioning:

```bash
# Start provisioning mode (creates WiFi hotspot)
sudo python3 provisioning.py

# Use iOS app to complete provisioning
```

## Running the Drone

### If you used `--start` flag

Services are already running! View logs:

```bash
journalctl -u drone-api -f
```

### Manual start

```bash
# Via systemd (recommended)
sudo systemctl start drone-api
sudo systemctl status drone-api

# Or directly (for debugging)
./venv/bin/python daemon.py
```

### Start ROS2 Navigation Stack

For autonomous navigation with SLAM:

```bash
source /opt/ros/humble/setup.bash
source ros2_ws/install/setup.bash
ros2 launch coybot_drone full_stack.launch.py
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLOUD (AWS)                             │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                     │
│  │ iOS App │───▶│ Lambda  │───▶│ Claude  │                     │
│  └─────────┘    └─────────┘    └─────────┘                     │
│                       │              │                          │
│                       │         Goal + Advice                   │
│                       ▼              ▼                          │
│                 ┌──────────────────────┐                        │
│                 │    AWS IoT Core      │                        │
│                 │       (MQTT)         │                        │
│                 └──────────────────────┘                        │
└─────────────────────────│───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DRONE (Jetson Orin)                          │
│                                                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │   daemon.py │───▶│  Reasoning  │───▶│  Primitives │         │
│  │  (MQTT rx)  │    │    Loop     │    │  (actions)  │         │
│  └─────────────┘    └─────────────┘    └─────────────┘         │
│                            │                  │                 │
│                            ▼                  ▼                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │  Local LLM  │    │ Perception  │    │   Nav2 +    │         │
│  │ (Phi/Llama) │    │  (YOLOv8)   │    │    SLAM     │         │
│  └─────────────┘    └─────────────┘    └─────────────┘         │
│                            │                  │                 │
│                            ▼                  ▼                 │
│                     ┌─────────────┐    ┌─────────────┐         │
│                     │   Camera    │    │   Flight    │         │
│                     │ (OAK/D435i) │    │ Controller  │         │
│                     └─────────────┘    └─────────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

## Key Components

| File | Purpose |
|------|---------|
| `daemon.py` | Main entry point, MQTT communication |
| `reasoning_loop.py` | Mission execution (VLM + Nav2) |
| `vlm.py` | Vision-language model (Qwen3-VL) |
| `nav2_bridge.py` | Nav2 navigation bridge |
| `perception.py` | YOLOv8 detection + distance estimation |

## Testing

```bash
# Test perception
./venv/bin/python -c "
from perception import PerceptionService
p = PerceptionService()
print(p.get_scene_description())
"

# Test mission loop (requires VLM models)
./venv/bin/python reasoning_loop.py
```

## Logs

```bash
# View daemon logs
tail -f /home/$USER/coybot/logs/drone.log

# View systemd logs
journalctl -u coybot-drone -f
```

## Troubleshooting

### Camera not detected

```bash
# Check USB devices
lsusb

# For OAK-D
python -c "import depthai; print(depthai.Device.getAllAvailableDevices())"

# For RealSense
rs-enumerate-devices
```

### VLM not loading

```bash
# Check available memory
free -h

# Ensure VLM models exist
ls -la models/vlm.gguf models/vlm_mmproj.gguf
```

### Flight controller not responding

```bash
# Check serial connection
ls /dev/ttyTHS*
sudo chmod 666 /dev/ttyTHS1

# Test MAVLink
python -c "from pymavlink import mavutil; m = mavutil.mavlink_connection('/dev/ttyTHS1', baud=921600); print(m.recv_match(blocking=True))"
```

### MQTT connection issues

```bash
# Check certificates
ls -la certs/

# Test connectivity
ping mqtt.iot.us-west-2.amazonaws.com
```

## Development

### Adding New Object Classes

YOLOv8n detects 80 COCO classes. For custom objects:
1. Train custom YOLOv8 model
2. Export to ONNX: `yolo export model=custom.pt format=onnx`
3. Place in `models/` directory
4. Update `perception.py` to load custom model

## License

Copyright 2026 Coybot AI, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines.

---

## Hardened Track A bring-up (Orin Nano + CubeOrange + D435i)

**Date: 2026-04-21**

Built and flew the "prompt → GDINO → depth → classical planner → flight" pipeline from the three papers end-to-end on the test drone. Pipeline matches `Closing_the_Metric_Gap` Track A v5 hardened config, minus monocular depth (replaced with D435i stereo) and minus local VLM for v1 (regex target parser).

### What shipped

New modules in `drone/common/` (mirrored to `~/drone-api/` on the drone):

| File | Role |
|---|---|
| `grounding.py` | HF `IDEA-Research/grounding-dino-tiny` wrapper. fp16 autocast, 300×400 processor size for Orin Nano. |
| `spatial_memory.py` | Per-label EMA store, 1.5 m merge radius. |
| `reactive_planner.py` | Depth-conditioned speed ramp (1–3 m/s), altitude floor 0.8 m, 3 m/step clamp, stall detector, confidence gate. |
| `target_selector.py` | Regex parser: `"go to the nearest X and come back"` → `X`. |
| `where.py` | One-shot diagnostic: grab RGB+depth, run GDINO, print body-frame positions. Used to verify axis signs. |
| `run_prompt.py` | CLI entry point. Takeoff → Phase 1 (perception loop) → Phase 2 (RTL) → land. Includes SIGINT/SIGTERM emergency-LAND safety net. |

### GDINO latency on Orin Nano

Cold CPU (torch 2.10 +cpu wheel, accidental install): **~15000 ms/frame**.
After jetson-ai-lab CUDA wheels (torch 2.9.1 + torchvision 0.24.1 + libcudss preload) and fp16 autocast + 300×400 processor size: **~290 ms/frame standalone, ~450 ms in-pipeline** (~34× speedup). torch.compile + triton was tried and abandoned — autotune OOMs on Orin Nano 8 GB.

### Bugs found and fixed during flight testing (props on)

1. **Altitude floor inverted** (`reactive_planner.py`): `vz > 0` zeroed climb when near ground; should zero descent. Fixed to `vz < 0`.
2. **Axis sign error**: planner emits `(forward, left, up)`; `MAV_FRAME_BODY_OFFSET_NED` expects `(forward, right, down)`. Without flip, drone flew right when target was left. Fixed with `mav_vy = -plan.vy`, `mav_vz = -plan.vz`.
3. **MAVLink `target_system=0`**: `wait_heartbeat()` sometimes picked up a sys=0 ghost heartbeat. Arm command broadcast to sys 0, silently ignored → `ARM FAILED`. Fix: scan heartbeats for `autopilot == MAV_AUTOPILOT_ARDUPILOTMEGA` and adopt its src sys/comp, fallback to sys=1 comp=1.
4. **Pre-arm compass check failed**: `Arm: Check mag field (xy diff:117>100)`. Resolved by power-cycling the FC outdoors — not a code issue.
5. **`current_rel_alt` fell back to `VFR_HUD.alt`** (MSL absolute, ~22 m), triggering false "takeoff reached" instantly. Fixed to use `GLOBAL_POSITION_INT.relative_alt` only.
6. **RTL climbed to default `RTL_ALT=15 m`** → "shot up 10 m then landed manually". Fix: `PARAM_SET RTL_ALT` pinned to current altitude (capped at `--max-alt`) before issuing RTL.
7. **Disarm detection false-positive**: any component heartbeat with `armed=0` (gimbal, companion) tripped "landed" immediately. Fix: filter `recv_match` by FC sys/comp, require 3 consecutive disarmed heartbeats.
8. **Planner aimed at 3D chair center** → drone descended into seat (crash). Fix: flatten `target_xyz[2]=0` before passing to planner so approach is horizontal-only at takeoff altitude.
9. **Script death left drone hovering in GUIDED**: if the Python process was killed mid-mission (Claude Code interrupt, Ctrl-C), no new velocity commands → ArduCopter holds position until battery dies. Fix: SIGINT/SIGTERM handler + try/finally around all airborne phases → emergency LAND if still armed on any exit.

### Final successful flight

Command:
```bash
python run_prompt.py --takeoff-alt 2.0 --reach-m 2.0 --max-speed 1.0 --max-alt 3.0 \
  --max-iters 120 --tick-hz 2 'go to the nearest chair and come back'
```

Timeline:
```
18:11:59  armed
18:12:05  takeoff reached 2.0m (real climb 0 → 1.95m over 5s)
18:12:12  reached chair at dist=2.00m (horizontal, no descent)
18:12:12  RTL commanded with RTL_ALT=2.0m
18:12:32  disarmed (landed)        ← real 20s flight + touchdown
          total mission: 33s
```

Three detections during approach, scores 0.43–0.65, GDINO ~490–610 ms/frame. Drone stayed at 2 m throughout, approached horizontally, stopped 2 m away, returned at same altitude, landed at home.

### Known deviations from paper

- No local VLM — regex target parser instead of Qwen2.5-VL or SmolVLM. Swap planned for v2.
- No Depth Anything V2 — D435i stereo depth is strictly better for this hardware.
- No ResNet-18 failure detector.
- No 4-heading active yaw scan or frontier exploration.
- "Come back" implemented via ArduPilot RTL (not in paper — paper only covers go-to).
