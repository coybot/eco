# Ecosystem — Claude working notes

Internal developer notes. **Do not put hostnames, passwords, AWS account IDs, bucket names,
or device certificates in this file** — use a private scratchpad for those.

---

## What this repo is

Astral drone platform. Three operational modes, one shared codebase:

| Mode | Path | When to use |
|------|------|-------------|
| **Cloud** | iOS → API Gateway → Lambda (Bedrock) → IoT MQTT → `daemon.py` → MAVLink | Production flights |
| **Local SSH** | `run_prompt.py` → VLM reasoning loop → `drone_sdk.py` → MAVLink | Dev/debug without cloud |
| **Offline AP** | iOS → drone hotspot → `local_control_api.py` → `mission_runner.py` | No internet |

---

## Ishmael — Isaac Sim test platform

Ishmael lets you run full sim tests from one English sentence. Lives in
`drone/sim/ishmael/`. Runs on **Hoopoe** (Linux, dual RTX 5090, Isaac Sim 5.1).

**Quick start:**
```bash
cd ~/code/ishmael/eco_sim
python -m ishmael.cli "2 rovers and 1 quadcopter search the office for a chair and send a picture"
```

**What it does end-to-end:**
1. Parses the sentence → `TestSpec` (vehicles, scene, objective, mobile_action)
2. Resolves scene keyword → USD asset path (`scene_resolver.py`)
3. Registers fleet vehicles in DynamoDB
4. Launches Isaac fleet (`launch_fleet.py`): spawns Crazyflie (quad) + Nova Carter (rover) in Isaac
5. Waits for MQTT heartbeats (vehicles connect to AWS IoT Core as real drones do)
6. Dispatches mission via headless mobile client (`app_client.py`)
7. Collects images/videos; returns URLs

**Supported scenes** (pass as `--env` or in the NL sentence):
- `office`, `warehouse`, `hospital` — Isaac built-in indoor scenes (CDN USD)
- wilderness USD paths from `/home/yusuf/wilderness-envs/` — outdoor (forest, etc.)
- Any local `.usd`/`.usda` path

**Vehicles:**
- `quad` / `quadcopter` → Crazyflie cf2x.usd (8× visual scale)
- `rover` → Nova Carter (falls back to Carter v1 if not found)

**Vantage cameras:** auto-placed at scene corners, `d=5m` indoor / `d=20m` outdoor, eye-level.
Grab frames: `engine_client.py EngineClient("/tmp/sim_engine.sock").grab_vantage_jpeg("cam_sw")`

**SDK verbs available in sim** (`sim_sdk.py`):
- `arm()`, `disarm()`, `takeoff(alt)`, `land()`
- `goto(lat, lon, alt)` — converted to sim XY via home-anchor
- `set_velocity(vx, vy, vz)`, `set_yaw(deg)`
- `capture_photo()` — renders Isaac frame, uploads to S3, returns URL
- `look_around()` — sweeps yaw, returns image URLs
- `record_video(seconds)` — encodes mp4, uploads to S3, returns URL
- `get_position()`, `get_altitude()`, `is_armed()`

**NLP parser** (`nlp.py`):
- Primary: local vLLM on Hoopoe (`ISHMAEL_VLLM_URL`, default `http://localhost:8000/v1`)
- Fallback: deterministic regex (works with no LLM)

**Key env vars:**
```
ISHMAEL_VLLM_URL      vLLM endpoint (Qwen served on Hoopoe)
ISHMAEL_API_BASE      Cloud API base URL
ISHMAEL_API_TOKEN     Cognito ID token
ISHMAEL_USER_SUB      Cognito user sub (fleet owner)
ISHMAEL_IOT_ENDPOINT  IoT ATS data endpoint
ISHMAEL_MOBILE_CERTS  device certs dir (default: ~/eco-certs)
ISHMAEL_SIM_PYTHON    Isaac venv python path
```

**Fleet launch directly** (bypass Ishmael NLP):
```bash
cd ~/code/ishmael/eco_sim
python launch_fleet.py \
  --fleet rover:1,quad:2 \
  --certs-base ~/eco-certs-fleet \
  --env office          # or a USD path
```
No `--headless` flag — headless is the default; `--gui` enables the viewport.

**Engine IPC** (grab frames from a running fleet):
```python
from engine_client import EngineClient
c = EngineClient("/tmp/sim_engine.sock")
jpeg = c.grab_vantage_jpeg("cam_sw")   # cam_sw, cam_se, cam_ne, cam_nw
```

**Ishmael branches** (all on `astral-us/eco` remote, stacked on each other):
- `ishmael/01-nlp-orchestrator` — NLP parse + director + headless mobile client
- `ishmael/02-scene-resolver` — scene keyword → USD library
- `ishmael/03-video` — `record_video()` verb + overhead vantage cameras
- `ishmael/04-photoreal` — high-fidelity drone USDs + RTX path-trace toggle
- `ishmael/05-e2e-test` — full pytest suite: office+chair+YOLO+video+mobile check

**Known headless rendering constraints:**
- `UsdPreviewSurface` — renders correctly in all headless modes ✅
- `MDL / OmniPBR / SimPBR` — renders black in headless (CDN shader compile fails) ❌
- `UsdGeom` primitives (Sphere, Cylinder, Cone) — work fine, low physics overhead ✅
- High-poly Mesh trees via Reference — causes CUDA OOM in physics plugin ❌
- Wilderness scene CDN vegetation — black in headless; use procedural geometry instead

---

## On-device layout

Install scripts copy `drone/common/*.py` flat into `~/drone-api/` (or your chosen
`INSTALL_DIR`). `config.yaml` and `certs/` live next to `daemon.py` and `drone_sdk.py`.

## Test hardware

- Document companion hostname, Tailscale name, and LAN IP in your private notes, not here.
- Prefer SSH keys over password auth for Jetson access.
- Flight controller: document the stable USB path from `/dev/serial/by-id/` on the aircraft,
  and set that path in `config.yaml` (`serial_port`).

## Cloud vs local control

- **Cloud**: `daemon.py` connects to AWS IoT Core using `certs/` + `iot_endpoint` in
  `config.yaml`, subscribes to command topics, executes sandboxed Python that calls `drone_sdk`.
- **Local**: `run_prompt.py` runs the Track A perception loop over SSH; it uses the same
  `config.yaml` defaults for MAVLink when you do not pass `--mav-port` / `--mav-baud`.

`drone_sdk._connect()` resolves the flight controller MAVLink `target_system` /
`target_component` the same way as `run_prompt.py` (prefer ArduPilot heartbeats; avoid
bogus `sys=0`).

## Safety defaults

- Confirm disarmed and props-off before motor tests.
- Prefer conservative throttle and duration for ESC checks.
- Always use a stable serial by-id path, not a reorder-sensitive `/dev/ttyACM*`.

## Deployment

Three targets, two deploy steps. Mobile (iOS) has no deploy step unless the app itself changed.

### AWS Lambda (`aws/src/handler.py`, `conversations.py`, etc.)

```bash
cd eco/aws
PATH="/opt/homebrew/bin:$PATH" AWS_PROFILE=astral sam build && AWS_PROFILE=astral sam deploy
```

**Do NOT use `sst deploy` for `eco/aws`.** The `sst.config.ts` uses a `create`-only Pulumi
command resource — SST only runs SAM on the very first deploy and silently skips it on all
subsequent ones (exits 0, deploys nothing). Always run SAM directly.

SAM CLI is at `/opt/homebrew/bin/sam` (installed via Homebrew). If changes aren't being
picked up, clear the build cache first: `rm -rf .aws-sam`.

### Drone on-device (`daemon.py`, `drone_sdk.py`, or any `drone/common/*.py`)

Files are installed flat into `~/drone-api/` on the companion computer. Copy changed files
and restart the service:

```bash
DRONE=astral@quadcopter   # or your host/IP

sshpass -p "$DRONE_SSH_PASSWORD" scp \
  -o PreferredAuthentications=password \
  -o PubkeyAuthentication=no \
  -o StrictHostKeyChecking=accept-new \
  eco/drone/common/daemon.py \
  eco/drone/common/drone_sdk.py \
  "$DRONE":~/drone-api/

sshpass -p "$DRONE_SSH_PASSWORD" ssh \
  -o PreferredAuthentications=password \
  -o PubkeyAuthentication=no \
  -o StrictHostKeyChecking=accept-new \
  "$DRONE" 'sudo systemctl restart drone-api'
```

### Deploy Ishmael to Hoopoe

```bash
sshpass -p "$HOOPOE_SSH_PASSWORD" scp -o PreferredAuthentications=password -o PubkeyAuthentication=no \
  eco/drone/sim/isaac_vehicle.py \
  eco/drone/sim/fleet_worker.py \
  yusuf@hoopoe:~/code/ishmael/eco_sim/
```

### Adding a new SDK function

1. Add it to `drone/common/drone_sdk.py`
2. Mirror it in `drone/sim/sim_sdk.py` (sim version)
3. Update `SYSTEM_PROMPT` in `aws/src/handler.py`
4. Deploy both targets above (AWS + drone)

### Safety gate (`daemon.py` blocked patterns)

`daemon.py` regex-blocks LLM-generated code containing `\bdisarm\s*(` — use `safe_disarm()`
for ground disarm or `land()` for in-flight. If you add a new safe wrapper that contains a
blocked keyword, verify the regex won't match it (word-boundary `\b` respects `_`).

## Tooling

- Prefer `uv` for Python environments where applicable (see team conventions).
- `just` for task running on Hoopoe.

## Papers / perception stack

Design references live under `~/code/ys/a/papers/` on some workstations (not shipped in
this repo). Product docs for the VLM + planner flow are under `docs/`.
