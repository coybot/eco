#!/bin/bash
# install.sh - Set up the drone daemon on Raspberry Pi

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DRONE_DIR="$( cd "$SCRIPT_DIR/../.." && pwd )"
COMMON_DIR="$DRONE_DIR/common"
INSTALL_DIR="$HOME/drone-api"

echo "========================================="
echo "Installing Drone API Daemon (Raspberry Pi)"
echo "========================================="
echo "Source: $DRONE_DIR"
echo "Install to: $INSTALL_DIR"
echo ""

# Create install directory
mkdir -p "$INSTALL_DIR/logs"

# Copy common files
echo "Copying common files..."
# Copy every module, not a hand-maintained list. The list drifted: nine modules
# that daemon.py and reasoning_loop.py import at module scope were never
# installed, so on a real drone the mission path died at dispatch with
# ModuleNotFoundError and the app just showed the mission stuck in progress.
# All of drone/common is 772K; there is nothing to be saved by picking.
cp "$COMMON_DIR"/*.py "$INSTALL_DIR/"
cp -r "$COMMON_DIR/certs" "$INSTALL_DIR/"

# Copy or create config
if [ -f "$COMMON_DIR/config.yaml" ]; then
    cp "$COMMON_DIR/config.yaml" "$INSTALL_DIR/"
elif [ -f "$COMMON_DIR/config.yaml.example" ]; then
    cp "$COMMON_DIR/config.yaml.example" "$INSTALL_DIR/config.yaml"
    echo "⚠️  Created config.yaml from template - please edit it!"
fi

# Check for certificates
if [ ! -f "$INSTALL_DIR/certs/device.pem" ]; then
    echo "⚠️  Warning: No certificates found in certs/"
    echo "   You need to download IoT Core certificates first."
    echo ""
fi

# Install system dependencies (Raspberry Pi specific)
echo "Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y python3-venv python3-pip

# Create virtual environment
echo "Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

# Install dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install pymavlink pyserial awsiotsdk pyyaml

# Create systemd service
echo "Creating systemd service..."
SERVICE_FILE="/etc/systemd/system/drone-api.service"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Drone API Daemon
# time-sync as well as network: these boards have no RTC, so the clock reads
# 1970 until NTP lands. mTLS to AWS IoT fails against a wrong clock
# (AWS_IO_TLS_ERROR_NEGOTIATION_FAILURE), the daemon exits 1, and systemd
# restarts it - so every single boot logged a crash before connecting.
After=network-online.target time-sync.target
Wants=network-online.target time-sync.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/daemon.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
# time-sync.target is only reached if something provides it. Wants= does not
# install or enable an NTP client, so make sure one is actually running.
if ! timedatectl show -p NTP --value 2>/dev/null | grep -q yes; then
    sudo systemctl enable --now systemd-timesyncd 2>/dev/null || true
    sudo timedatectl set-ntp true 2>/dev/null || true
fi

# Keep logs across reboots. Without /var/log/journal, journald stores to /run
# and every power-off erases the flight logs - on a vehicle that is the only
# record of what it just did, and drones get power-cycled constantly.
sudo mkdir -p /var/log/journal
sudo sed -i "s/^#\?Storage=.*/Storage=persistent/; s/^#\?SystemMaxUse=.*/SystemMaxUse=500M/" /etc/systemd/journald.conf
sudo systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true
sudo systemctl restart systemd-journald

sudo systemctl daemon-reload

# Add user to dialout group for serial access
sudo usermod -aG dialout $USER

echo ""
echo "========================================="
echo "✅ Installation complete!"
echo "========================================="
echo ""

# The setup passphrase is generated on-device and is the only way into a drone
# with no shell access, so print it here: this is the operator's one chance to
# write it on the airframe before the hotspot needs it.
HOTSPOT_SSID="$(sudo python3 "$INSTALL_DIR/wifi_manager.py" 2>/dev/null | sed -n 's/^Hotspot name: //p')"
HOTSPOT_PSK="$(sudo python3 "$INSTALL_DIR/wifi_manager.py" --passphrase 2>/dev/null)"
if [ -n "$HOTSPOT_SSID" ] && [ -n "$HOTSPOT_PSK" ]; then
    echo "WiFi setup network - write these on the airframe:"
    echo "  Network:    $HOTSPOT_SSID"
    echo "  Passphrase: $HOTSPOT_PSK"
    echo ""
    echo "The iOS app asks for both. The passphrase also authorizes setup, and is"
    echo "regenerated after each successful WiFi configuration."
    echo ""
fi
echo "Next steps:"
echo "1. Edit config: nano $INSTALL_DIR/config.yaml"
echo "2. Add certificates to: $INSTALL_DIR/certs/"
echo "3. Start: sudo systemctl start drone-api"
echo "4. Enable on boot: sudo systemctl enable drone-api"
echo ""
echo "NOTE: You may need to log out and back in for dialout group to take effect."
echo ""

