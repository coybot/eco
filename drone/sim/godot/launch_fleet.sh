#!/usr/bin/env bash
# Launch the full Godot sim fleet on hoopoe (or any Linux box with Godot 4 installed).
# Usage: bash launch_fleet.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIM_DIR="$(dirname "$SCRIPT_DIR")"

GODOT_BIN="${GODOT_BIN:-$HOME/godot4}"
GODOT_PROJECT="${GODOT_PROJECT:-$HOME/eco-sim/godot}"
CERTS_DIR="${CERTS_DIR:-$HOME/drone-api/certs}"
# shellcheck source=../secrets_env.sh
source "$SIM_DIR/secrets_env.sh"
IOT_ENDPOINT="${IOT_ENDPOINT:-YOUR_IOT_ENDPOINT.iot.us-west-2.amazonaws.com}"
SOCK="tcp://127.0.0.1:9999"

# --- 1. Kill any existing Godot instance ---
pkill -f godot4 || true
sleep 1

# --- 2. Start Godot headless in background ---
echo "Starting Godot sim..."
DISPLAY=:99 "$GODOT_BIN" --path "$GODOT_PROJECT" -- \
    --fleet quad:3,rover:2 --env plaza \
    >"$TMPDIR/godot_sim.log" 2>&1 &
GODOT_PID=$!
echo "Godot PID: $GODOT_PID  (log: /tmp/godot_sim.log)"

# --- 3. Wait for TCP:9999 to be open (max 30s) ---
echo "Waiting for Godot IPC port 9999..."
DEADLINE=$((SECONDS + 30))
while ! nc -z 127.0.0.1 9999 2>/dev/null; do
    if [[ $SECONDS -ge $DEADLINE ]]; then
        echo "ERROR: Godot IPC port 9999 not open after 30s. Check /tmp/godot_sim.log" >&2
        exit 1
    fi
    sleep 1
done
echo "Port 9999 open."

# --- 4. Start one sim_drone_daemon.py per vehicle ---
start_daemon() {
    local DRONE_ID="$1"
    local VEHICLE="$2"
    echo "Starting daemon: $DRONE_ID ($VEHICLE)"
    python3 "$SIM_DIR/sim_drone_daemon.py" \
        --drone-id "$DRONE_ID" \
        --vehicle "$VEHICLE" \
        --sock "$SOCK" \
        --certs-dir "$CERTS_DIR" \
        --iot-endpoint "$IOT_ENDPOINT" \
        >/tmp/sim_daemon_${DRONE_ID}.log 2>&1 &
}

start_daemon sim-quadcopter-01 quadcopter
start_daemon sim-quadcopter-02 quadcopter
start_daemon sim-quadcopter-03 quadcopter
start_daemon sim-rover-04     rover
start_daemon sim-rover-05     rover

# --- 5. Done ---
echo "Fleet up. Daemons: sim-quadcopter-01..03, sim-rover-04..05"
echo "Logs: /tmp/sim_daemon_<id>.log"
