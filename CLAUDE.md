# Ecosystem — Claude working notes

Internal developer notes. **Do not put hostnames, passwords, AWS account IDs, bucket names, or device certificates in this file** — use a private scratchpad for those.

## Test hardware

- Document companion hostname, Tailscale name, and LAN IP in your private notes, not here.
- Prefer SSH keys over password auth for Jetson access.
- Flight controller: document the stable USB path from `/dev/serial/by-id/` on the aircraft, and set that path in `config.yaml` (`serial_port`).

## On-device layout

Install scripts copy `drone/common/*.py` flat into `~/drone-api/` (or your chosen `INSTALL_DIR`). `config.yaml` and `certs/` live next to `daemon.py` and `drone_sdk.py`.

## Cloud vs local control

- **Cloud**: `daemon.py` connects to AWS IoT Core using `certs/` + `iot_endpoint` in `config.yaml`, subscribes to command topics, executes sandboxed Python that calls `drone_sdk`.
- **Local (perception loop)**: `run_prompt.py` runs the Track A perception loop over SSH; it uses the same `config.yaml` defaults for MAVLink when you do not pass `--mav-port` / `--mav-baud`.
- **Local (Ground Control Station)**: `config.yaml`'s `control_plane: gcs` (default `aws`) points `daemon.py` at a GCS server (see `gcs/README.md`) instead of AWS IoT Core — same MQTT topics, no AWS account or internet needed. The GCS runs the same `aws/src` Lambda handlers unmodified against local storage and a local model (Ollama/vLLM) instead of DynamoDB/Bedrock. iOS/Android switch control planes from their Settings screen.

`drone_sdk._connect()` resolves the flight controller MAVLink `target_system` / `target_component` the same way as `run_prompt.py` (prefer ArduPilot heartbeats; avoid bogus `sys=0`).

## Safety defaults

- Confirm disarmed and props-off before motor tests.
- Prefer conservative throttle and duration for ESC checks.
- Always use a stable serial by-id path, not a reorder-sensitive `/dev/ttyACM*`.

## Deployment

Three targets, two deploy steps. Mobile (iOS) has no deploy step unless the app itself changed.

### AWS Lambda (`aws/src/handler.py`, `conversations.py`, etc.)

```bash
cd eco/aws
PATH="/opt/homebrew/bin:$PATH" AWS_PROFILE=presidio sam build && AWS_PROFILE=presidio sam deploy
```

**Do NOT use `sst deploy` for `eco/aws`.** The `sst.config.ts` uses a `create`-only Pulumi command resource — SST only runs SAM on the very first deploy and silently skips it on all subsequent ones (exits 0, deploys nothing). Always run SAM directly.

SAM CLI is at `/opt/homebrew/bin/sam` (installed via Homebrew). If changes aren't being picked up, clear the build cache first: `rm -rf .aws-sam`.

### Drone on-device (`daemon.py`, `drone_sdk.py`, or any `drone/common/*.py`)

Files are installed flat into `~/drone-api/` on the companion computer. Copy changed files and restart the service:

**Legacy credentials:** the `astral` username/password below are what's actually flashed onto
this companion computer today — renaming them here wouldn't rename the account on the device.
They stay `astral` until the device is reflashed or its user/password is changed directly.

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

See the root `CLAUDE.md` for the full SSH options (password-only, `StrictHostKeyChecking`).

### Adding a new SDK function

1. Add it to `drone/common/drone_sdk.py`
2. Update `SYSTEM_PROMPT` in `aws/src/handler.py`
3. Deploy both targets above (AWS + drone)

### Safety gate (`daemon.py` blocked patterns)

`daemon.py` regex-blocks LLM-generated code containing `\bdisarm\s*(` — use `safe_disarm()` for ground disarm or `land()` for in-flight. If you add a new safe wrapper that contains a blocked keyword, verify the regex won't match it (word-boundary `\b` respects `_`).

## Tooling

- Prefer `uv` for Python environments where applicable (see team conventions).

## Papers / perception stack

Design references live under `~/code/ys/a/papers/` on some workstations (not shipped in this repo). Product docs for the VLM + planner flow are under `docs/`.
