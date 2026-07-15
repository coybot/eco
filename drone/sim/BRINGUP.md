# Eco sim bridge — bring-up runbook

How to start simulated drones so they appear **Online** in the app and respond to commands.
Future chat shortcut: *"bring up the sim bridge(s) on hoopoe"* — an agent should read this file.

A **bridge** = one `sim_bridge.py` process on the GPU host **hoopoe** that runs one Isaac Sim
vehicle, connects to AWS IoT over MQTT (cert auth), and streams video over KVS. Nothing persists in
the process — stopping it loses nothing; the cert + cloud registration stay. Connect to hoopoe via
SSH (creds in private notes / memory: user `yusuf`).

Host paths: code `~/code/ishmael/eco_sim/`, Isaac venv python `/opt/ml/isaac-sim-env/bin/python3`,
certs `~/eco-certs` (quad) and `~/eco-certs-rover` (rover).

---

## 1. Bring up the existing two drones

**Quadcopter (Crazyflie, `sim-quadcopter-test`):**
```bash
cd ~/code/ishmael/eco_sim
ISHMAEL_HARNESS=$HOME/code/ishmael/swarm_eval/harness \
WARP_CUDA_DEVICES=0 \
  /opt/ml/isaac-sim-env/bin/python3 -u sim_bridge.py \
  --env office --drone-id sim-quadcopter-test --vehicle quadcopter \
  --certs-dir ~/eco-certs  > /tmp/sim_quad.log 2>&1 &
```

**Rover (Nova Carter, `sim-rover-test`):**
```bash
cd ~/code/ishmael/eco_sim
ISHMAEL_HARNESS=$HOME/code/ishmael/swarm_eval/harness \
WARP_CUDA_DEVICES=1 \
  /opt/ml/isaac-sim-env/bin/python3 -u sim_bridge.py \
  --env office --drone-id sim-rover-test --vehicle rover \
  --certs-dir ~/eco-certs-rover  > /tmp/sim_rover.log 2>&1 &
```

- `--env`: `office | warehouse | hospital | hangar | outdoor`.
- `WARP_CUDA_DEVICES` pins each bridge to a GPU (hoopoe has 2). Run **both** together → both Online.
- Boot takes ~40 s. Check: `grep -E "ready: env|mqtt. connected" /tmp/sim_quad.log`.
- The app shows the drone **Online** within ~10 s of the first heartbeat.

**Stop a bridge** (never `pkill -f sim_bridge.py` from the same shell — it kills your shell):
```bash
pkill -9 -f "[s]im_bridge"            # stops ALL bridges
```
After any restart the cloud marks the drone Offline for ~10–30 s (heartbeat TTL) and rejects
commands until a fresh heartbeat lands — wait before sending.

---

## 2. Add a NEW sim drone (one-time per drone)

Three steps: provision a cert, register it to a user, then launch a bridge for it.

```bash
# (run with AWS_PROFILE=astral on a machine with the AWS CLI)
ID=sim-quadcopter-001                 # your new drone id
AWS_PROFILE=astral aws iot create-thing --thing-name $ID
ARN=$(AWS_PROFILE=astral aws iot create-keys-and-certificate --set-as-active \
  --certificate-pem-outfile device.pem --private-key-outfile private.key \
  --query certificateArn --output text)
AWS_PROFILE=astral aws iot attach-policy --policy-name drone-policy-dev --target "$ARN"
AWS_PROFILE=astral aws iot attach-thing-principal --thing-name $ID --principal "$ARN"
curl -s https://www.amazontrust.com/repository/AmazonRootCA1.pem -o root-ca.pem
# copy device.pem/private.key/root-ca.pem to hoopoe:~/eco-certs-$ID/

# register it to the app user (sub = that user's Cognito sub; harun's is 28a1f320-...-a5d9)
AWS_PROFILE=astral aws dynamodb put-item --table-name drone-registry-dev --item '{
  "userId":{"S":"<USER_SUB>"},"droneId":{"S":"'$ID'"},"name":{"S":"Quad 001"},
  "registeredAt":{"S":"2026-01-01T00:00:00"},"status":{"S":"registered"},
  "droneType":{"S":"sim"},"vehicleType":{"S":"quadcopter"},
  "simEnvironment":{"S":"office"},"isaacHost":{"S":"hoopoe"}}'
```
Then launch a bridge with `--drone-id $ID --certs-dir ~/eco-certs-$ID`.
(The app can also add a sim drone from the UI, but it generates a random id; provisioning a matching
cert for that random id is the catch — easier to pre-provision ids as above.)

---

## 2b. Fleet: many drones in ONE Isaac world (Phase 1, working)

`fleet_bridge.py` runs N mixed vehicles in a single Isaac world over one MQTT
connection (one fleet cert) — each drone is IRL-identical to the cloud/app.

```bash
# 1) register the fleet in the cloud (host WITH AWS creds, e.g. your Mac):
#    ids are deterministic: sim-quadcopter-001..NNN, sim-rover-001..NNN
AWS_PROFILE=astral python3 fleet.py --fleet quad:10,rover:10 --user-sub <COGNITO_SUB>
#    (no boto3 on the Mac? register rows with `aws dynamodb put-item` instead — see git history)

# 2) launch the fleet on hoopoe (one process, one world):
cd ~/code/ishmael/eco_sim
ISHMAEL_HARNESS=$HOME/code/ishmael/swarm_eval/harness \
  /opt/ml/isaac-sim-env/bin/python3 -u fleet_bridge.py \
  --fleet quad:10,rover:10 --env office \
  --certs-dir ~/eco-certs --thing-name sim-quadcopter-test > /tmp/fleet.log 2>&1 &
```

- One fleet cert serves all drones: `drone-policy-dev` is wildcard-scoped, so the
  single connection subscribes `drone/+/chat/+/command` (+ video) and publishes
  every drone's status/response. No per-drone certs.
- Verified: 20 vehicles Online, individually controllable, concurrent fan-out to
  8 at once. On-demand video: a vehicle only renders/streams when the app opens
  its viewer (publishes `drone/{id}/video/command` start) — bounds GPU cost.
- **Test publishes must use `--qos 1`** (CLI default is QoS0 = lossy). The real
  cloud already publishes at QoS1.

## 2c. Fleet via process separation (RECOMMENDED — reliable at scale)

`fleet_bridge.py` (one process, many MQTT connections) is **superseded**: many MQTT
connections in one Python process starve on the GIL. The reliable, IRL-faithful
way is **one engine process (owns Isaac) + one daemon process per drone (own MQTT
connection)**, talking over a Unix socket.

```bash
# per-drone certs must exist at ~/eco-certs-fleet/{drone_id}/ (provision like §2;
# 20 already provisioned). Then one command launches engine + all daemons:
cd ~/code/ishmael/eco_sim
/opt/ml/isaac-sim-env/bin/python3 launch_fleet.py \
  --fleet quad:10,rover:10 --env office --certs-base ~/eco-certs-fleet
```
- `sim_engine.py` = Isaac world + IPC (`/tmp/sim_engine.sock`), no MQTT.
- `sim_drone_daemon.py` = one drone, one MQTT connection, commands via engine IPC.
- Logs: `/tmp/sim_engine.log`, `/tmp/daemon-{id}.log`, `/tmp/launch.log`.
- Verified: group fan-out to 8 drones at 20-drone scale, all receive instantly.
- Stop: `pkill -f '[l]aunch_fleet'; pkill -f '[s]im_engine'; pkill -f '[s]im_drone_daemon'`.

## 3. Scaling to many (10 … 1000)

The current design is **one process = one Isaac instance = one vehicle**. That's fine for a handful
(roughly 1–3 instances per GPU, limited mostly by rendering/video). It does **not** scale to 1000 as
1000 processes. To go big, the architecture changes along three axes:

1. **Many vehicles per Isaac world (biggest win).** Isaac/the existing swarm_eval `IsaacSimBridge`
   already spawns N vehicles in a *single* world with per-vehicle cameras. Refactor so one process
   manages N vehicles: N IoT MQTT identities (or one connection multiplexing `drone/+/...` topics),
   per-vehicle kinematic state, and per-vehicle video **only when a viewer is watching**. One GPU
   world can hold tens of kinematic vehicles if most aren't streaming video.

2. **Video is the expensive part.** Live WebRTC + rendering per vehicle is what limits density.
   At fleet scale you stream on-demand for the few vehicles actually being viewed; the rest run
   headless (physics/commands only), which is cheap. Command/telemetry (MQTT) scales to thousands
   easily — that's just AWS IoT.

3. **Horizontal across GPUs/hosts.** For 1000: a fleet of GPU machines, each running one world with
   M vehicles, plus an orchestrator that assigns drone-ids to hosts and launches workers. Provision
   certs in bulk via **AWS IoT fleet provisioning** (claim cert → per-device cert) instead of the
   manual CLI in §2, and bulk-write the registry. Naming: `sim-quadcopter-0001 … -1000`.

**Rule of thumb:** commands/telemetry for thousands = trivial (IoT MQTT). Thousands of *rendered,
video-streaming* Isaac vehicles = a GPU fleet + the multi-vehicle-per-world refactor above. If you
want this, it's a real project — ask and we'll design the multi-agent bridge + orchestrator.
