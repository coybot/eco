#!/usr/bin/env bash
# Bring up a Godot sim fleet on macOS: Godot (rendered, TCP IPC :9999) + one
# sim_drone_daemon.py per vehicle, each connecting to AWS IoT with its own cert.
# This is the local-Mac counterpart of godot/launch_fleet.sh (which targets a
# headless Linux/Xvfb GPU host).
#
#   FLEET=quad:1 ENV=plaza bash launch_fleet_mac.sh
#
# Explicit-id mode (for app E2E tests — ids you can register under your own user
# instead of the deterministic sim-quadcopter-001 slots that may be owned already):
#   DRONES="sim-quadcopter-mac01:quadcopter" ENV=plaza bash launch_fleet_mac.sh
# Each id is spawned into Godot at runtime via the IPC `spawn` op, then gets its
# own daemon. Provision its cert first: provision_sim_certs.sh <id>.
#
# Notes:
#   * Godot is launched RENDERED (not --headless). macOS headless uses the dummy
#     renderer, which leaves per-vehicle SubViewport camera textures blank — so
#     look_around / video would be black. A normal window renders via Metal.
#   * Vehicle ids are deterministic and match fleet.py / the GDScript roster:
#     quad:1 -> sim-quadcopter-001. Certs must already exist at
#     $CERTS_BASE/<id>/ (run provision_sim_certs.sh first).
#   * Stop everything: bash launch_fleet_mac.sh stop
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GODOT_BIN="${GODOT_BIN:-/opt/homebrew/bin/godot}"
GODOT_PROJECT="${GODOT_PROJECT:-$SCRIPT_DIR/godot}"
VENV_PY="${VENV_PY:-$SCRIPT_DIR/.venv-mac/bin/python}"
CERTS_BASE="${CERTS_BASE:-$HOME/eco-certs-fleet}"
# shellcheck source=secrets_env.sh
source "$SCRIPT_DIR/secrets_env.sh"
IOT_ENDPOINT="${IOT_ENDPOINT:-YOUR_IOT_ENDPOINT.iot.us-west-2.amazonaws.com}"
FLEET="${FLEET:-quad:1}"
ENV="${ENV:-plaza}"
IPC_PORT="${IPC_PORT:-9999}"
LOGDIR="${LOGDIR:-${TMPDIR:-/tmp}}"

stop() {
  echo "Stopping Godot + sim daemons…"
  pkill -f "[s]im_drone_daemon.py" 2>/dev/null || true
  pkill -f "[P]residioEcoSim" 2>/dev/null || true
  pkill -f "godot .*${GODOT_PROJECT##*/}" 2>/dev/null || true
  echo "stopped."
}

if [[ "${1:-}" == "stop" ]]; then stop; exit 0; fi

[[ -x "$GODOT_BIN" ]] || { echo "ERROR: godot not at $GODOT_BIN (set GODOT_BIN)"; exit 1; }
[[ -x "$VENV_PY" ]] || { echo "ERROR: venv python not at $VENV_PY — create .venv-mac"; exit 1; }

# In explicit-id mode Godot starts with no roster vehicles; we spawn them by id.
DRONES="${DRONES:-}"
GODOT_FLEET="$FLEET"
[[ -n "$DRONES" ]] && GODOT_FLEET="quad:0"

# 1. Launch Godot (rendered) in the background.
echo "Starting Godot (fleet=$GODOT_FLEET env=$ENV ipc=$IPC_PORT)…"
"$GODOT_BIN" --path "$GODOT_PROJECT" -- \
    --fleet "$GODOT_FLEET" --env "$ENV" --ipc-port "$IPC_PORT" \
    >"$LOGDIR/godot_sim.log" 2>&1 &
GODOT_PID=$!
echo "Godot PID $GODOT_PID  (log: $LOGDIR/godot_sim.log)"

# 2. Wait for the IPC port.
echo "Waiting for Godot IPC :$IPC_PORT…"
for _ in $(seq 1 40); do
  if nc -z 127.0.0.1 "$IPC_PORT" 2>/dev/null; then break; fi
  if ! kill -0 "$GODOT_PID" 2>/dev/null; then
    echo "ERROR: Godot exited early — see $LOGDIR/godot_sim.log"; exit 1
  fi
  sleep 1
done
nc -z 127.0.0.1 "$IPC_PORT" 2>/dev/null || { echo "ERROR: IPC :$IPC_PORT never opened"; exit 1; }
echo "IPC up."

# 3. Determine the vehicle list. Explicit-id mode: "id:type,..." (spawned into
#    Godot below). Roster mode: deterministic ids from the shared parser.
if [[ -n "$DRONES" ]]; then
  ROSTER_LINES="$(printf '%s' "$DRONES" | tr ',' '\n' | sed 's/:/ /')"
  # Spawn each explicit id into the running Godot world.
  echo "$ROSTER_LINES" | while read -r ID VTYPE; do
    [[ -z "$ID" ]] && continue
    "$VENV_PY" -c "import sys; sys.path.insert(0,'$SCRIPT_DIR')
from engine_client import EngineClient
EngineClient('tcp://127.0.0.1:$IPC_PORT').spawn('$ID', '${VTYPE:-quadcopter}')
print('spawned $ID into Godot')"
  done
else
  ROSTER_LINES="$("$VENV_PY" -c "import sys; sys.path.insert(0,'$SCRIPT_DIR')
from fleet import parse_roster
for v in parse_roster('$FLEET'): print(v['id'], v['type'])")"
fi

# 4. One daemon per vehicle.
echo "$ROSTER_LINES" | while read -r ID VTYPE; do
  [[ -z "$ID" ]] && continue
  VTYPE="${VTYPE:-quadcopter}"
  CDIR="$CERTS_BASE/$ID"
  if [[ ! -f "$CDIR/device.pem" ]]; then
    echo "WARNING: no cert for $ID at $CDIR — run provision_sim_certs.sh $ID (skipping)"; continue
  fi
  echo "Starting daemon $ID ($VTYPE)…"
  "$VENV_PY" "$SCRIPT_DIR/sim_drone_daemon.py" \
      --drone-id "$ID" --vehicle "$VTYPE" \
      --sock "tcp://127.0.0.1:$IPC_PORT" \
      --certs-dir "$CDIR" --iot-endpoint "$IOT_ENDPOINT" \
      >"$LOGDIR/sim_daemon_${ID}.log" 2>&1 &
  echo "  PID $! (log: $LOGDIR/sim_daemon_${ID}.log)"
done

echo
echo "Fleet up. Tail a daemon: tail -f $LOGDIR/sim_daemon_<id>.log"
echo "Stop everything:        bash $0 stop"
