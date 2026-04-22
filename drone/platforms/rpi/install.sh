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
cp "$COMMON_DIR/daemon.py" "$INSTALL_DIR/"
cp "$COMMON_DIR/drone_sdk.py" "$INSTALL_DIR/"
cp "$COMMON_DIR/motor_test.py" "$INSTALL_DIR/"
cp "$COMMON_DIR/arm_disarm.py" "$INSTALL_DIR/"
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
After=network-online.target
Wants=network-online.target

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
sudo systemctl daemon-reload

# Add user to dialout group for serial access
sudo usermod -aG dialout $USER

echo ""
echo "========================================="
echo "✅ Installation complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Edit config: nano $INSTALL_DIR/config.yaml"
echo "2. Add certificates to: $INSTALL_DIR/certs/"
echo "3. Start: sudo systemctl start drone-api"
echo "4. Enable on boot: sudo systemctl enable drone-api"
echo ""
echo "NOTE: You may need to log out and back in for dialout group to take effect."
echo ""

