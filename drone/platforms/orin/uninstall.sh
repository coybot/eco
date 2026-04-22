#!/bin/bash
# uninstall.sh - Remove the drone daemon from Orin Nano

set -e

INSTALL_DIR="$HOME/drone-api"

echo "========================================="
echo "Uninstalling Drone API Daemon"
echo "========================================="

# Stop and disable service
echo "Stopping service..."
sudo systemctl stop drone-api 2>/dev/null || true
sudo systemctl disable drone-api 2>/dev/null || true

# Remove service file
echo "Removing systemd service..."
sudo rm -f /etc/systemd/system/drone-api.service
sudo systemctl daemon-reload

echo ""
echo "✅ Service removed!"
echo ""
echo "The drone-api folder is still at: $INSTALL_DIR"
echo "To fully remove: rm -rf $INSTALL_DIR"
echo ""
