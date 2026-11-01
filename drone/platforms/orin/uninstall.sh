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

# Camera split (installed only when a D435i was present)
sudo systemctl stop cam-split-watchdog.timer cam-split 2>/dev/null || true
sudo systemctl disable cam-split-watchdog.timer cam-split 2>/dev/null || true
sudo rm -f /etc/systemd/system/cam-split.service \
    /etc/systemd/system/cam-split-watchdog.service \
    /etc/systemd/system/cam-split-watchdog.timer \
    /usr/local/bin/cam-split-watchdog.py \
    /etc/udev/rules.d/60-eco-camera.rules \
    /etc/udev/rules.d/99-d435i-color.rules \
    /etc/modprobe.d/eco-v4l2loopback.conf \
    /etc/modules-load.d/eco-v4l2loopback.conf

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
