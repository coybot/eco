#!/usr/bin/env bash
# Operator-storyboard local demo (Part 2 — user/demo tooling).
#
# Brings up the whole stack on THIS Mac as a stand-in for the airlink-orin GCS,
# so the DroneOperator app (simulator or a real iPad on the same LAN) can drive:
#   iOS app  ->  GCS (planner model)  ->  fixed-wing sim drones (vision model)
#
#   * mosquitto            broker on 0.0.0.0:1883  (so a real iPad can reach it)
#   * GCS server           HTTP on 0.0.0.0:8080, planner = Ollama qwen2.5:7b
#   * Godot sim (GUI)       surveil_truck world + 2 fixed-wing drones
#   * fw_gcs_daemon x2      real MissionLoop + Ollama vision model (qwen2.5vl)
#
# Both models are pinned in Ollama so nothing cold-loads mid-demo. Prints the
# LAN IP + operator pairing token and the exact settings to type into the app.
#
# Usage:  bash drone/sim/run_local_demo.sh            # 2 drones, GUI
#         DRONES=1 bash drone/sim/run_local_demo.sh   # single drone (faster)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_DIR="${DATA_DIR:-$HOME/.presidio-demo}"
LOG_DIR="$DATA_DIR/logs"; mkdir -p "$LOG_DIR" "$DATA_DIR/gcs-data"
MOSQ="${MOSQ:-/opt/homebrew/opt/mosquitto/sbin/mosquitto}"
PLANNER_MODEL="${PLANNER_MODEL:-qwen2.5:7b-instruct}"
VLM_MODEL="${VLM_MODEL:-qwen2.5vl:7b}"
DATUM_LAT="${DATUM_LAT:-37.405}"; DATUM_LON="${DATUM_LON:--122.100}"
DRONES="${DRONES:-2}"
# Drone perception brain: "vlm" = real on-device vision model (the default —
# it reads the actual objective, so ANY natural-language command works, not just
# the truck search); "oracle" = deterministic sim-truth executor (faster but
# scripted to the truck scenario, so other commands are ignored). The GCS
# PLANNER is a real model either way.
BRAIN="${BRAIN:-vlm}"
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1)"

echo "==> stopping any previous demo processes"
pkill -f "run_fw_gcs_demo" 2>/dev/null || true
pkill -f "video_bridge" 2>/dev/null || true
pkill -9 -f "godot" 2>/dev/null || true
pkill -f "server.py --config $DATA_DIR" 2>/dev/null || true
pkill -f "mosquitto -c $DATA_DIR" 2>/dev/null || true
sleep 2

echo "==> writing configs"
cat > "$DATA_DIR/mosquitto.conf" <<EOF
listener 1883 0.0.0.0
allow_anonymous true
persistence false
EOF
cat > "$DATA_DIR/gcs-config.yaml" <<EOF
data_dir: $DATA_DIR/gcs-data
http: { host: 0.0.0.0, port: 8080 }
mqtt: { host: 127.0.0.1, port: 1883, username: null, password: null }
llm:  { provider: openai, base_url: http://localhost:11434/v1, model: $PLANNER_MODEL, api_key: null, timeout_s: 120 }
operators: [ operator1 ]
EOF

echo "==> ensuring Ollama is up and pinning both models in memory"
curl -s -o /dev/null http://localhost:11434/api/tags || { nohup ollama serve >"$LOG_DIR/ollama.log" 2>&1 & sleep 3; }
for m in "$PLANNER_MODEL" "$VLM_MODEL"; do
  curl -s http://localhost:11434/api/generate -d "{\"model\":\"$m\",\"prompt\":\"ok\",\"keep_alive\":-1,\"stream\":false}" \
    -o /dev/null -m 300 && echo "   pinned $m"
done

echo "==> starting mosquitto (0.0.0.0:1883)"
nohup "$MOSQ" -c "$DATA_DIR/mosquitto.conf" >"$LOG_DIR/mosquitto.log" 2>&1 &
sleep 1

echo "==> starting GCS server (0.0.0.0:8080)"
( cd "$REPO/gcs" && nohup python3 server.py --config "$DATA_DIR/gcs-config.yaml" >"$LOG_DIR/gcs.log" 2>&1 & )
for i in $(seq 1 30); do curl -s http://localhost:8080/healthz | grep -q ok && break; sleep 1; done
TOKEN="$(python3 -c "import json;print(json.load(open('$DATA_DIR/gcs-data/auth.json'))['operators']['operator1'])")"

echo "==> registering drones (idempotent)"
for d in alpha bravo; do
  curl -s -o /dev/null -X POST http://localhost:8080/drones -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" -d "{\"droneId\":\"$d\",\"name\":\"$d\"}"
done

echo "==> launching Godot + $DRONES fixed-wing drone(s) with the vision model"
( cd "$REPO" && VLM_BASE_URL=http://localhost:11434/v1 VLM_MODEL="$VLM_MODEL" \
  nohup python3 drone/sim/run_fw_gcs_demo.py \
    --mqtt-host 127.0.0.1 --mqtt-port 1883 --alpha-token "$TOKEN" --bravo-token "$TOKEN" \
    --drones "$DRONES" --brain "$BRAIN" --gui true --godot-port 9978 \
    --datum-lat "$DATUM_LAT" --datum-lon "$DATUM_LON" \
    --target-label "pickup truck,truck,car" \
    --images-base-url "http://127.0.0.1:8080" >"$LOG_DIR/sim.log" 2>&1 & )
for i in $(seq 1 40); do grep -q "daemon up" "$LOG_DIR/sim.log" 2>/dev/null && break; sleep 1; done

echo "==> starting live video bridge (MJPEG on 0.0.0.0:8091)"
( cd "$REPO" && nohup python3 drone/sim/video_bridge.py \
    --godot-host 127.0.0.1 --godot-port 9978 \
    --http-host 0.0.0.0 --http-port 8091 --fps 6 \
    >"$LOG_DIR/video.log" 2>&1 & )
for i in $(seq 1 20); do curl -s http://localhost:8091/healthz | grep -q ok && break; sleep 1; done

cat <<EOF

============================================================
  DEMO READY
============================================================
  In the DroneOperator app -> Settings -> Ground Control Station:
     Host / IP     : $LAN_IP
     HTTP port     : 8080
     MQTT port     : 1883
     Pairing token : $TOKEN
  Save, then open the Mission tab. Live drone video (PiP) is served
  from http://$LAN_IP:8091 and picked up automatically by the app.

  Logs:   $LOG_DIR/{gcs,sim,video,mosquitto,ollama}.log
  Watch:  tail -f $LOG_DIR/sim.log
  Stop:   pkill -f run_fw_gcs_demo; pkill -9 -f godot; pkill -f video_bridge; \\
          pkill -f 'server.py --config $DATA_DIR'; pkill -f 'mosquitto -c $DATA_DIR'
============================================================
EOF
