# Eco sim host (Isaac Sim)

Runs **one process per simulated vehicle** on the Isaac Sim host (hoopoe) and makes a sim drone
behave **exactly like an IRL drone**: commands arrive over AWS IoT **MQTT** (outbound, cert-auth —
no tunnel), live video streams to the app as a KVS WebRTC master (also cert-auth). The vehicle is an
Isaac-shipped asset driven kinematically.

```
iOS app ──NL──▶ cloud ──IoT MQTT (drone/{id}/chat/{cid}/command)──▶ sim_bridge ─▶ Isaac Sim
iOS app ◀── IoT MQTT (…/response, image_urls) ◀── sim_bridge ◀── photo→S3
iOS app ◀═══ KVS WebRTC video ═══ sim_bridge (master) ◀── vehicle camera
```

No inbound reachability is needed — identical connection model to the IRL drone (`daemon.py`).

## Vehicles (Isaac assets)
- **quadcopter** → Crazyflie `Isaac/Robots/Bitcraze/Crazyflie/cf2x.usd`
- **rover** → NVIDIA Nova Carter `Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd`
  (falls back to Carter v1 → Jetbot if absent)

Both are moved **kinematically** (pose integrated toward goals; physics timeline not played) — see
`isaac_vehicle.py`. Pegasus only ships Iris/Pegasus airframes, so we don't use it.

## Files
- `isaac_vehicle.py` — `IsaacVehicleBridge`: spawn Crazyflie/Carter USD, attach RGB camera, kinematic
  motion. Same method surface the old Pegasus bridge exposed.
- `sim_sdk.py` — `SimWorker` (main-thread Isaac owner) + the cloud-generated SDK surface
  (`arm/takeoff/land/goto/set_velocity/set_yaw/capture_photo/look_around`; rover `drive/turn/goto`).
  `capture_photo` uploads a JPEG to S3 and returns the URL (IRL parity).
- `sim_mqtt.py` — AWS IoT MQTT client (mirrors `daemon.py`): subscribes to command topics, runs code
  via `SimWorker`, publishes `…/response` + heartbeats.
- `sim_video_producer.py` — KVS WebRTC master on `drone-{id}-dev`, frames from the worker.
- `cloud_creds.py` — IoT credential-provider auth + S3 upload helpers (shared).
- `sim_bridge.py` — wires it together; `SimWorker` on main thread, MQTT/video on bg threads.

## Run (Isaac venv on hoopoe — NOT Docker)
```bash
PY=/opt/ml/isaac-sim-env/bin/python3
# one-time deps (then ALWAYS re-pin numpy<2 — av/awsiotsdk pull numpy>=2 which breaks Isaac 5.1):
uv pip install --python $PY aiortc av awsiotsdk && uv pip install --python $PY "numpy<2"

ISHMAEL_HARNESS=$HOME/code/ishmael/swarm_eval/harness $PY \
  ~/code/ishmael/eco_sim/sim_bridge.py \
  --env office --drone-id sim-quadcopter-test --vehicle quadcopter \
  --certs-dir ~/eco-certs
# defaults come from IOT_ENDPOINT / CREDENTIALS_ENDPOINT, or ~/.config/presidio/secrets.env
# (see secrets.env.example); pass --iot-endpoint / --credentials-endpoint to override
# --vehicle rover  for the Nova Carter.  --http-debug  re-enables a local /execute server.
```
Drone-id must match the app's generated id (`sim-{vehicle}-{suffix}`).

## Credentials — same as IRL drones (no static keys)
The vehicle uses an **IoT device certificate** in `--certs-dir` (`device.pem`/`private.key`/
`root-ca.pem`) to mint temporary AWS creds via the IoT credential provider — for KVS video
(`drone-video-role-alias-dev`) and S3 photo upload (`drone-s3-access-role-alias-dev`). The cert's
thing must have `drone-policy-dev` attached. Provision one like an IRL drone (fleet provisioning) or
via the IoT control plane. Test thing `sim-quadcopter-test` is already provisioned.

## Environments
`office`, `warehouse`, `hospital` map to Omniverse USD scenes; `hangar`/`outdoor` currently fall back
to warehouse (add USDs to `_ENV_USD_MAP` in `isaac_vehicle.py` for distinct scenes).

## Cloud side
`eco/aws/src/conversations.py` no longer special-cases `droneType=="sim"` — sim commands flow through
the normal capability-based path and are published to IoT MQTT like any drone. **Deploy with SAM**
(`cd eco/aws && AWS_PROFILE=presidio sam build && sam deploy`).
