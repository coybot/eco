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
- **Local**: `run_prompt.py` runs the Track A perception loop over SSH; it uses the same `config.yaml` defaults for MAVLink when you do not pass `--mav-port` / `--mav-baud`.

`drone_sdk._connect()` resolves the flight controller MAVLink `target_system` / `target_component` the same way as `run_prompt.py` (prefer ArduPilot heartbeats; avoid bogus `sys=0`).

## Safety defaults

- Confirm disarmed and props-off before motor tests.
- Prefer conservative throttle and duration for ESC checks.
- Always use a stable serial by-id path, not a reorder-sensitive `/dev/ttyACM*`.

## Tooling

- Prefer `uv` for Python environments where applicable (see team conventions).

## Papers / perception stack

Design references live under `~/code/ys/a/papers/` on some workstations (not shipped in this repo). Product docs for the VLM + planner flow are under `docs/`.
