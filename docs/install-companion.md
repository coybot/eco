# Installing on a companion computer

Two supported platforms: NVIDIA Orin (Nano/NX/AGX) and Raspberry Pi. Both
install scripts are self-contained — they copy from your local checkout, no
S3 bucket or Coybot-hosted artifact needed.

## NVIDIA Orin

```bash
cd drone/platforms/orin
./install.sh --start                              # local install
./install.sh --remote user@host --start           # remote via SSH (key auth)
./install.sh --remote user@host --password pass --start   # remote, password auth
```

Detects the Orin variant (Nano/NX/AGX 32GB/AGX 64GB) from `/proc/device-tree/model`
or RAM size, and picks the model set accordingly (YOLOv8 + a Qwen3-VL variant
sized to fit). Installs `llama-cpp-python` with CUDA support (uses a
pre-built wheel from `drone/wheels/` if present — saves ~45 minutes). AGX
variants additionally get ROS 2 Nav2 (requires ROS 2 Humble already
installed).

## Raspberry Pi

```bash
cd drone/platforms/rpi
./install.sh
```

Lighter install — copies `daemon.py`, `drone_sdk.py`, `motor_test.py`,
`arm_disarm.py`, and `certs/` flat; no VLM/model download stack.

## Where things land

Both install to `~/drone-api/` (override with `INSTALL_DIR`):

```
~/drone-api/
  daemon.py, drone_sdk.py, ...       # drone/common/*.py, flat
  config.yaml                        # from config.yaml.example if not present
  certs/                             # claim + device certs (AWS mode) — see certificates.md
  logs/
  models/                            # Orin only
  venv/
```

## systemd services

| Service | Platform | Purpose |
|---|---|---|
| `drone-api` | both | Runs `daemon.py` — the always-on command loop |
| `coybot-network-manager` | Orin | WiFi state machine (infra WiFi ↔ AP-mode fallback) — see [`provisioning.md`](provisioning.md) |
| `coybot-local-control` | Orin | Local HTTPS API for the phone-hosted offline path — see [`offline-first.md`](offline-first.md) |

```bash
sudo systemctl status drone-api
journalctl -u drone-api -f              # live logs
sudo systemctl restart drone-api
```

## Updating an installed drone

Copy changed files and restart:

```bash
DRONE=user@drone-host
scp drone/common/daemon.py drone/common/drone_sdk.py "$DRONE":~/drone-api/
ssh "$DRONE" 'sudo systemctl restart drone-api'
```

## Uninstall

```bash
cd drone/platforms/orin  # or rpi
./uninstall.sh
```
