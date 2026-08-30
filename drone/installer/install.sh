#!/bin/bash
#
# Drone API Installer
# One-liner installation: sudo /bin/bash -c "$(curl -fsSL https://astral-drone-installer.s3.amazonaws.com/install.sh)"
#
set -e

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo ""
    echo "❌ Please run as root:"
    echo ""
    echo "   sudo /bin/bash -c \"\$(curl -fsSL https://astral-drone-installer.s3.amazonaws.com/install.sh)\""
    echo ""
    exit 1
fi

# Get the actual user (not root) for file ownership
ACTUAL_USER="${SUDO_USER:-$USER}"
ACTUAL_HOME=$(eval echo "~$ACTUAL_USER")

# Configuration
S3_BASE="https://astral-drone-installer.s3.amazonaws.com"
INSTALL_DIR="$ACTUAL_HOME/drone-api"
# Replace when building a hosted installer artifact for your fleet.
IOT_ENDPOINT="${ASTRAL_IOT_ENDPOINT:-REPLACE.iot.us-west-2.amazonaws.com}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
echo "╔════════════════════════════════════════════╗"
echo "║         🚁 Drone API Installer             ║"
echo "╚════════════════════════════════════════════╝"
echo -e "${NC}"

# Clean up any existing installation (but keep WiFi for now - we need it to download!)
echo -e "${CYAN}Cleaning up old installation...${NC}"
systemctl stop drone-api 2>/dev/null || true
systemctl disable drone-api 2>/dev/null || true
rm -f /etc/systemd/system/drone-api.service
systemctl daemon-reload 2>/dev/null || true
sleep 1
rm -rf "$INSTALL_DIR"
rm -f /etc/drone-id
echo "  ✓ Clean slate"

# Create directories
echo -e "${CYAN}Creating directories...${NC}"
mkdir -p "$INSTALL_DIR"/{certs,logs}
cd "$INSTALL_DIR"

# Download scripts
echo -e "${CYAN}Downloading drone software...${NC}"
for script in daemon.py drone_sdk.py provisioning.py wifi_manager.py factory_reset.py motor_test.py arm_disarm.py fleet_provisioning.py video_producer.py perception.py reasoning_loop.py backends.py vehicle_class.py mission_vocab.py search_patterns.py situation.py spatial_memory.py data_recorder.py; do
    echo "  → $script"
    curl -fsSL "$S3_BASE/scripts/$script" -o "$script"
done

# Download camera module
echo -e "${CYAN}Downloading camera module...${NC}"
mkdir -p camera/common camera/intelD435i camera/oakdlite
for module in __init__.py common/__init__.py common/auto.py common/base.py intelD435i/__init__.py intelD435i/camera.py oakdlite/__init__.py oakdlite/camera.py; do
    echo "  → camera/$module"
    curl -fsSL "$S3_BASE/scripts/camera/$module" -o "camera/$module"
done

# Download certificates (claim cert for fleet provisioning)
echo -e "${CYAN}Downloading certificates...${NC}"
curl -fsSL "$S3_BASE/certs/root-ca.pem" -o certs/root-ca.pem
curl -fsSL "$S3_BASE/certs/claim-cert.pem" -o certs/claim-cert.pem
curl -fsSL "$S3_BASE/certs/claim-private.key" -o certs/claim-private.key
chmod 600 certs/*.key certs/*.pem

# Create config
echo -e "${CYAN}Creating configuration...${NC}"
cat > config.yaml <<EOF
# Drone API Configuration (auto-generated)
region: "us-west-2"
iot_endpoint: "$IOT_ENDPOINT"
serial_port: "/dev/ttyACM0"
baud_rate: 115200
log_level: "INFO"
EOF

# Install system dependencies
echo -e "${CYAN}Installing system dependencies...${NC}"
apt-get update -qq
apt-get install -y -qq hostapd > /dev/null 2>&1
systemctl unmask hostapd 2>/dev/null || true
systemctl stop hostapd 2>/dev/null || true
echo "  ✓ hostapd installed"

# Download requirements.txt
echo -e "${CYAN}Downloading requirements...${NC}"
curl -fsSL "$S3_BASE/scripts/requirements.txt" -o requirements.txt

# Install Python dependencies using uv (fast!) or pip
echo -e "${CYAN}Setting up Python environment...${NC}"
python3 -m venv venv
source venv/bin/activate

# Try uv first (10-100x faster), fall back to pip
if command -v uv &> /dev/null; then
    echo "  Using uv (fast mode)"
    uv pip install -r requirements.txt
else
    # Install uv first if pip is available
    echo "  Installing uv..."
    pip install -q uv 2>/dev/null && uv pip install -r requirements.txt || {
        echo "  Falling back to pip..."
        pip install -q --upgrade pip
        pip install -q -r requirements.txt
    }
fi

# Verify the flat install contains the complete mission import path before the
# service is started. The camera package is checked separately because it is
# imported lazily by PerceptionService and would otherwise evade this import.
echo -e "${CYAN}Verifying mission installation...${NC}"
for script in perception.py reasoning_loop.py backends.py vehicle_class.py mission_vocab.py search_patterns.py situation.py spatial_memory.py data_recorder.py; do
    test -f "$INSTALL_DIR/$script"
done
test -f "$INSTALL_DIR/camera/__init__.py"
python3 -c "import perception, reasoning_loop; print('  Mission imports OK')"

# Run fleet provisioning to get unique certificate
echo -e "${CYAN}Running fleet provisioning...${NC}"
if python3 fleet_provisioning.py; then
    # Update /etc/drone-id with the thing name from provisioning
    THING_NAME=$(cat certs/thing-name.txt 2>/dev/null)
    if [ -n "$THING_NAME" ]; then
        echo "$THING_NAME" > /etc/drone-id
        echo "  Registered as: $THING_NAME"
    fi
else
    echo -e "${YELLOW}⚠️  Fleet provisioning skipped (will retry on first boot)${NC}"
fi

# Fix ownership (we're running as root but want user to own the files)
chown -R "$ACTUAL_USER:$ACTUAL_USER" "$INSTALL_DIR"

# Create systemd service
echo -e "${CYAN}Installing systemd service...${NC}"
cat > /etc/systemd/system/drone-api.service <<EOF
[Unit]
Description=Drone API Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/daemon.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# Clear WiFi NOW (after everything is downloaded/installed)
echo -e "${CYAN}Clearing WiFi for hotspot mode...${NC}"
# NetworkManager connections (skip docker/tailscale)
for conn in $(nmcli -t -f NAME connection show | grep -v docker | grep -v tailscale | grep -v lo); do
    nmcli connection delete "$conn" 2>/dev/null || true
done
# wpa_supplicant (if used)
if [ -f /etc/wpa_supplicant/wpa_supplicant.conf ]; then
    cat > /etc/wpa_supplicant/wpa_supplicant.conf <<WPAEOF
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US
WPAEOF
fi
echo "  ✓ WiFi cleared - hotspot will start"

# Enable and start service (will start hotspot since no WiFi)
systemctl daemon-reload
systemctl enable drone-api
systemctl start drone-api

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         ✅ Installation Complete!          ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""
echo -e "The drone daemon is now running."
echo ""
echo -e "${CYAN}If WiFi is not configured, the drone will start a hotspot.${NC}"
echo -e "${CYAN}Connect to the hotspot with your phone and use the app to finish setup.${NC}"
echo ""
echo -e "Useful commands:"
echo -e "  ${YELLOW}sudo systemctl status drone-api${NC}  - Check status"
echo -e "  ${YELLOW}sudo journalctl -u drone-api -f${NC}  - View logs"
echo -e "  ${YELLOW}cat $INSTALL_DIR/logs/drone.log${NC}  - View drone log"
echo ""
