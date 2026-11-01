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
| `cam-split` | Orin, with a RealSense D435i | Copies the D435i colour stream onto three v4l2loopback nodes — see [Camera split](#camera-split-realsense-d435i) |
| `cam-split-watchdog.timer` | Orin, with a RealSense D435i | Every 30 s, restarts `cam-split` if its feed has frozen |

```bash
sudo systemctl status drone-api
journalctl -u drone-api -f              # live logs
sudo systemctl restart drone-api
```

## Camera split (RealSense D435i)

`install.sh` sets this up on its own when it finds a D435i (`CAMERA_SPLIT=yes`
or `no` overrides that). A RealSense colour stream can be opened by one
process, and the aircraft wants it in three places, so `cam-split` (ffmpeg)
owns the camera and writes three v4l2loopback copies, which udev names:

| Name | Read by |
|---|---|
| `/dev/eco-stream` | the RTSP video stream |
| `/dev/eco-color` | `drone-api` (`CAMERA_DEVICE`): photos and perception |
| `/dev/eco-watch` | `cam-split-watchdog`, alone on its node |

Everything refers to these names, never to a `/dev/videoN`. The numbers move
when USB cameras enumerate: with the loopbacks at `video10,11` a RealSense took
those numbers one boot and every photo failed with `could not open V4L2 device`.
The loopbacks are pinned to `video20-22`, well clear of any camera, and the
watchdog has its own node because sharing one with `drone-api` made the drone's
OpenCV reader lose frames whenever the two overlapped.

The files are in `drone/platforms/orin/camera-split/`, and
`drone/common/tests/test_camera_split_config.py` fails if they stop agreeing
with each other. An aircraft installed before this existed needs a reboot after
`install.sh` (or `sudo modprobe -r v4l2loopback` with `drone-api` and
`cam-split` stopped) so the module loads with the new node numbers.

If photos fail, check in this order:

```bash
ls -l /dev/eco-* /dev/d435i-color          # the names exist
systemctl is-active cam-split cam-split-watchdog.timer
journalctl -u cam-split -n 30 --no-pager   # ffmpeg's own errors
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
