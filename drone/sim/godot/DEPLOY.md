# Godot Sim Fleet — Deployment Guide

## Prerequisites

- Ubuntu 22.04+ (hoopoe or any Linux box)
- Godot 4.4 binary at `~/godot4` (or set `$GODOT_BIN`)
- Xvfb running on `:99` (`sudo Xvfb :99 -screen 0 1280x720x24 &`)
- Python 3.10+ with `paho-mqtt`, `boto3`, `opencv-python` installed
- AWS IoT certs at `~/drone-api/certs/` (`root-ca.pem`, `device.pem`, `private.key`)
- Godot project synced to `~/eco-sim/godot/` (or set `$GODOT_PROJECT`)

## Quick Start

```bash
# 1. Sync the Godot project to hoopoe
scp -r eco/drone/sim/godot/ hoopoe:~/eco-sim/godot/

# 2. Launch Godot + all 5 daemons
ssh hoopoe 'bash ~/eco-sim/godot/launch_fleet.sh'

# 3. Check daemon logs
ssh hoopoe 'tail -f /tmp/sim_daemon_sim-quadcopter-01.log'
```

## Architecture

```
Godot 4 (headless, DISPLAY=:99)
    ipc_server.gd  TCP:9999  (newline-delimited JSON)
         |
         |  EngineClient (engine_client.py)
         |
    sim_drone_daemon.py  x5  (one process per vehicle)
         |
         |  paho-mqtt  TLS:8883
         v
    AWS IoT Core  (drone/{id}/command topics)
         |
    handler.py Lambda
         |
    Claude Bedrock
         |
    user (iOS / web)
```

## Vehicle IDs

| ID                  | Type        | Label   |
|---------------------|-------------|---------|
| sim-quadcopter-01   | quadcopter  | Quad 1  |
| sim-quadcopter-02   | quadcopter  | Quad 2  |
| sim-quadcopter-03   | quadcopter  | Quad 3  |
| sim-rover-04        | rover       | Rover 1 |
| sim-rover-05        | rover       | Rover 2 |

Register all five in DynamoDB before first use:

```bash
AWS_PROFILE=astral python3 eco/drone/sim/register_sim_drones.py
```

## Config

Each daemon inherits these defaults (can be overridden via env vars or CLI flags in `launch_fleet.sh`):

| Variable         | Default                                         | Purpose                          |
|------------------|-------------------------------------------------|----------------------------------|
| `GODOT_BIN`      | `~/godot4`                                      | Godot 4 binary path              |
| `GODOT_PROJECT`  | `~/eco-sim/godot`                               | Godot project directory          |
| `CERTS_DIR`      | `~/drone-api/certs`                             | IoT TLS certificates             |
| `IOT_ENDPOINT`   | `a3c6a8oie6d6k5-ats.iot.us-west-2.amazonaws.com` | AWS IoT Core endpoint          |
| `--sock`         | `tcp://127.0.0.1:9999`                          | Godot IPC address                |

`config.yaml` on the daemon host should contain `iot_endpoint` and `certs_path` if using the real drone daemon path. For the sim fleet these are passed as CLI flags by `launch_fleet.sh`.

## Troubleshooting

**Blank / black camera frames**
- The Godot process needs a display. Make sure Xvfb is running on `:99` before launching.
- Verify: `DISPLAY=:99 ~/godot4 --version` should print the Godot version without error.

**Connection refused on TCP:9999**
- Godot takes a few seconds to initialise the IPC server after startup.
- `launch_fleet.sh` polls with `nc` for up to 30 s and only starts daemons once the port is open.
- If it still fails, check `/tmp/godot_sim.log` for Godot startup errors.

**Daemon exits immediately**
- Check `/tmp/sim_daemon_<id>.log` for Python tracebacks.
- Common cause: certs missing or wrong path (`CERTS_DIR`).

**Vehicle not visible in the app**
- Run `register_sim_drones.py` — vehicles must exist in DynamoDB before the API returns them.
