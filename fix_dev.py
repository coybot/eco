import pathlib

# ---------- wifi_manager: make the passphrase readable by the installer ----------
p = pathlib.Path('drone/common/wifi_manager.py')
s = p.read_text()
old = '''if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    wm = WiFiManager()
    print(f"MAC: {wm.get_mac_address()}")
    print(f"Hotspot name: {wm.get_hotspot_name()}")
    print(f"WiFi configured: {wm.is_wifi_configured()}")
'''
new = '''if __name__ == "__main__":
    import sys

    wm = WiFiManager()

    # --passphrase prints the setup passphrase and nothing else, so install.sh can
    # capture it. Kept behind a flag: the default output is safe to paste into a
    # terminal share, this line is not.
    if "--passphrase" in sys.argv:
        print(wm.get_hotspot_password())
        sys.exit(0)

    logging.basicConfig(level=logging.INFO)
    print(f"MAC: {wm.get_mac_address()}")
    print(f"Hotspot name: {wm.get_hotspot_name()}")
    print(f"WiFi configured: {wm.is_wifi_configured()}")
    print("Setup passphrase: run with --passphrase (needs root)")
'''
assert old in s
s = s.replace(old, new)
p.write_text(s)

BANNER = '''
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
'''

# ---------- orin installer ----------
p = pathlib.Path('drone/platforms/orin/install.sh')
s = p.read_text()
old = '''echo ""
echo "========================================="
echo "✅ Installation complete!"
echo "========================================="
echo ""
'''
new = old + BANNER
assert old in s
s = s.replace(old, new, 1)
p.write_text(s)

# ---------- rpi installer ----------
p = pathlib.Path('drone/platforms/rpi/install.sh')
s = p.read_text()
old = '''cp "$COMMON_DIR/daemon.py" "$INSTALL_DIR/"
cp "$COMMON_DIR/drone_sdk.py" "$INSTALL_DIR/"'''
new = '''cp "$COMMON_DIR/daemon.py" "$INSTALL_DIR/"
cp "$COMMON_DIR/drone_sdk.py" "$INSTALL_DIR/"
# daemon.py imports provisioning at module scope, which reaches wifi_manager
cp "$COMMON_DIR/provisioning.py" "$INSTALL_DIR/"
cp "$COMMON_DIR/wifi_manager.py" "$INSTALL_DIR/"'''
assert old in s
s = s.replace(old, new)

old = '''echo ""
echo "========================================="
echo "✅ Installation complete!"
echo "========================================="
echo ""
'''
new = old + BANNER
assert old in s
s = s.replace(old, new, 1)
p.write_text(s)
print("device ok")
