#!/bin/bash
# install.sh  — bootstrap the Astral web-simulator host on Ubuntu 22.04
#
# Usage:
#   ./install.sh [--no-register] [--pool SPEC] [--godot-url URL] [--rendering-driver opengl3]
#
# Options:
#   --no-register          skip DynamoDB drone registration (video-only test)
#   --pool SPEC            daemon pool spec, default quad:5,rover:5
#   --godot-url URL        Godot 4.x Linux binary zip URL
#   --sim-secret SECRET    shared secret the Lambda sends as X-Sim-Secret header
#   --rendering-driver DRV force Godot rendering driver; use 'opengl3' on CPU-only instances
#                          (c5.xlarge etc.) — hardware GPU instances (g4dn.xlarge) leave unset
#
# After this script:
#   - Godot 4.4 at /opt/sim/godot4
#   - sim code at /opt/sim/
#   - Python venv at /opt/sim/venv
#   - systemd unit: sim-host.service (auto-starts on boot)
#   - caddy reverse-proxy on :443 -> :8080 (optional, if CADDY_DOMAIN is set)
#
# To bake an AMI after setup, stop the service:
#   sudo systemctl stop sim-host
#   sudo systemctl disable sim-host
# Then create the AMI from the AWS console / CLI.

set -euo pipefail

INSTALL_DIR="/opt/sim"
SERVICE_USER="ubuntu"
POOL="quad:5,rover:5"
NO_REGISTER=""
SIM_SECRET=""
RENDERING_DRIVER=""
CADDY_DOMAIN="${CADDY_DOMAIN:-}"
GODOT_URL="${GODOT_URL:-https://github.com/godotengine/godot-builds/releases/download/4.4-stable/Godot_v4.4-stable_linux.x86_64.zip}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-register)      NO_REGISTER="--no-register"; shift ;;
        --pool)             POOL="$2"; shift 2 ;;
        --godot-url)        GODOT_URL="$2"; shift 2 ;;
        --sim-secret)       SIM_SECRET="$2"; shift 2 ;;
        --rendering-driver) RENDERING_DRIVER="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "=== Sim Host Install ==="
echo "  install dir: $INSTALL_DIR"
echo "  pool:        $POOL"
echo "  no-register: ${NO_REGISTER:-false}"

# ── 1. System packages ────────────────────────────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    xvfb \
    x11-utils \
    libx11-6 libxext6 libxrender1 libxi6 libxrandr2 libxinerama1 libxcursor1 \
    libvulkan1 mesa-vulkan-drivers \
    libgles2 libgl1 \
    ffmpeg \
    python3 python3-venv python3-pip \
    unzip curl wget ca-certificates \
    awscli \
    2>/dev/null

# ── 2. Install directory ─────────────────────────────────────────────────────
sudo mkdir -p "$INSTALL_DIR/certs-fleet"
sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ── 3. Godot binary ──────────────────────────────────────────────────────────
if [[ ! -x "$INSTALL_DIR/godot4" ]]; then
    echo "Downloading Godot..."
    TMP_ZIP=$(mktemp /tmp/godot_XXXX.zip)
    wget -q -O "$TMP_ZIP" "$GODOT_URL"
    unzip -jo "$TMP_ZIP" "Godot_v*_linux.x86_64" -d "$INSTALL_DIR/"
    GODOT_BIN=$(ls "$INSTALL_DIR"/Godot_v*_linux.x86_64 2>/dev/null | head -1)
    [[ -z "$GODOT_BIN" ]] && { echo "ERROR: Godot binary not found in zip"; exit 1; }
    mv "$GODOT_BIN" "$INSTALL_DIR/godot4"
    chmod +x "$INSTALL_DIR/godot4"
    rm -f "$TMP_ZIP"
    echo "Godot installed at $INSTALL_DIR/godot4"
else
    echo "Godot already at $INSTALL_DIR/godot4, skipping"
fi

# ── 4. Sim code (assumes repo already synced to $INSTALL_DIR/src/) ───────────
SIM_SRC="$INSTALL_DIR/src"
# Key sim files expected:
for f in sim_session_server.py engine_client.py fleet.py sim_drone_daemon.py sim_control.py godot_engine.py; do
    if [[ ! -f "$SIM_SRC/$f" ]]; then
        echo "ERROR: $SIM_SRC/$f missing — rsync the sim code first:"
        echo "  rsync -av eco/drone/sim/ ubuntu@HOST:$SIM_SRC/"
        echo "  rsync -av eco/drone/common/ ubuntu@HOST:$SIM_SRC/"
        exit 1
    fi
done

# Godot project symlink — engine_client.py expects godot/ next to sim_session_server.py
if [[ ! -L "$SIM_SRC/godot" && ! -d "$SIM_SRC/godot" ]]; then
    echo "ERROR: $SIM_SRC/godot missing — rsync the Godot project:"
    echo "  rsync -av eco/drone/sim/godot/ ubuntu@HOST:$SIM_SRC/godot/"
    exit 1
fi

# ── 5. Python venv ────────────────────────────────────────────────────────────
VENV="$INSTALL_DIR/venv"
if [[ ! -d "$VENV" ]]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q \
    aiohttp \
    boto3 \
    paho-mqtt \
    opencv-python-headless \
    requests

echo "Python venv ready at $VENV"

# ── 6. Systemd service ────────────────────────────────────────────────────────
# Build the ExecStart command
EXEC_CMD="$VENV/bin/python $SIM_SRC/sim_session_server.py"
EXEC_CMD="$EXEC_CMD --port 8080"
EXEC_CMD="$EXEC_CMD --certs-base $INSTALL_DIR/certs-fleet"
EXEC_CMD="$EXEC_CMD --pool $POOL"
EXEC_CMD="$EXEC_CMD --default-env office"
EXEC_CMD="$EXEC_CMD --xvfb"
EXEC_CMD="$EXEC_CMD --godot $INSTALL_DIR/godot4"
EXEC_CMD="$EXEC_CMD --python $VENV/bin/python"
[[ -n "$NO_REGISTER" ]]      && EXEC_CMD="$EXEC_CMD $NO_REGISTER"
[[ -n "$RENDERING_DRIVER" ]] && EXEC_CMD="$EXEC_CMD --rendering-driver $RENDERING_DRIVER"

# Env block
ENV_BLOCK="Environment=SIM_IDLE_TIMEOUT=120"
ENV_BLOCK="$ENV_BLOCK\nEnvironment=SIM_MAX_LIFETIME=1200"
ENV_BLOCK="$ENV_BLOCK\nEnvironment=SIM_SELF_STOP=1"
ENV_BLOCK="$ENV_BLOCK\nEnvironment=VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json"
[[ -n "$SIM_SECRET" ]] && ENV_BLOCK="$ENV_BLOCK\nEnvironment=SIM_SHARED_SECRET=$SIM_SECRET"

sudo tee /etc/systemd/system/sim-host.service > /dev/null << EOF
[Unit]
Description=Astral Web Simulator Host (Godot + session server)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$SIM_SRC
$(echo -e "$ENV_BLOCK")
# Import any new/changed assets before launching (headless, no GPU needed).
# Idempotent: quick no-op when nothing changed; essential after rsync'ing new GLBs.
ExecStartPre=$INSTALL_DIR/godot4 --headless --path $SIM_SRC/godot --import --quit
ExecStart=$EXEC_CMD
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
# Give Godot + Xvfb time to come up (up to 3 min before systemd gives up)
TimeoutStartSec=180
# Fast shutdown: SIGTERM → wait 10s → SIGKILL (Godot/Xvfb hold X11 resources)
TimeoutStopSec=10
KillMode=mixed

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable sim-host

echo ""
echo "=== Install complete ==="
echo "Next steps:"
echo "  1. Copy IoT certs (if using full mode):"
echo "       rsync -av ~/eco-certs-fleet/ ubuntu@HOST:$INSTALL_DIR/certs-fleet/"
echo "  2. Start the service:"
echo "       sudo systemctl start sim-host"
echo "  3. Check status:"
echo "       sudo systemctl status sim-host"
echo "       journalctl -u sim-host -f"
echo "  4. Health check (wait ~60s for Godot to load):"
echo "       curl http://localhost:8080/healthz"
