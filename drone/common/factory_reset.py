#!/usr/bin/env python3
"""
Factory Reset - Clears WiFi configuration to allow re-provisioning.

Use this when:
- Moving drone to a new WiFi network
- Transferring ownership to a new user
- Troubleshooting connection issues
"""

import os
import subprocess
import sys
from pathlib import Path

# Configuration files to clear
WPA_SUPPLICANT_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"
NETWORK_MANAGER_CONN_DIR = "/etc/NetworkManager/system-connections"
DRONE_CONFIG = Path(__file__).parent / "config.yaml"


def confirm(message: str) -> bool:
    """Ask for confirmation."""
    response = input(f"{message} [y/N]: ").strip().lower()
    return response in ('y', 'yes')


def clear_wpa_supplicant():
    """Clear wpa_supplicant networks."""
    if os.path.exists(WPA_SUPPLICANT_CONF):
        print(f"Clearing {WPA_SUPPLICANT_CONF}...")
        # Keep header, remove networks
        header = """ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US
"""
        try:
            with open(WPA_SUPPLICANT_CONF, 'w') as f:
                f.write(header)
            print("  ✓ wpa_supplicant cleared")
            return True
        except PermissionError:
            print("  ✗ Permission denied. Run with sudo.")
            return False
    else:
        print(f"  - {WPA_SUPPLICANT_CONF} not found, skipping")
        return True


def clear_network_manager():
    """Clear NetworkManager WiFi connections."""
    if os.path.isdir(NETWORK_MANAGER_CONN_DIR):
        print(f"Clearing NetworkManager connections...")
        count = 0
        for f in os.listdir(NETWORK_MANAGER_CONN_DIR):
            path = os.path.join(NETWORK_MANAGER_CONN_DIR, f)
            # Only delete WiFi connections
            try:
                with open(path, 'r') as file:
                    if 'type=wifi' in file.read():
                        os.remove(path)
                        count += 1
            except:
                pass
        print(f"  ✓ Cleared {count} WiFi connections")
        return True
    else:
        print(f"  - NetworkManager not found, skipping")
        return True


def clear_drone_config():
    """Clear user-specific config from config.yaml."""
    if DRONE_CONFIG.exists():
        print(f"Clearing {DRONE_CONFIG}...")
        # Keep only essential config, remove user_id
        try:
            import yaml
            with open(DRONE_CONFIG, 'r') as f:
                config = yaml.safe_load(f) or {}
            
            # Remove user-specific fields
            config.pop('user_id', None)
            
            with open(DRONE_CONFIG, 'w') as f:
                yaml.dump(config, f)
            
            print("  ✓ Drone config cleared")
            return True
        except Exception as e:
            print(f"  ✗ Error: {e}")
            return False
    else:
        print(f"  - {DRONE_CONFIG} not found")
        return True


def restart_services():
    """Restart networking services."""
    print("Restarting networking services...")
    
    # Try NetworkManager first
    result = subprocess.run(
        ["systemctl", "restart", "NetworkManager"],
        capture_output=True
    )
    if result.returncode == 0:
        print("  ✓ NetworkManager restarted")
        return True
    
    # Try wpa_supplicant
    result = subprocess.run(
        ["systemctl", "restart", "wpa_supplicant"],
        capture_output=True
    )
    if result.returncode == 0:
        print("  ✓ wpa_supplicant restarted")
        return True
    
    print("  - Could not restart services, reboot recommended")
    return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Factory reset drone WiFi configuration")
    parser.add_argument("--force", "-f", action="store_true", help="Skip confirmation")
    args = parser.parse_args()
    
    print("=" * 50)
    print("DRONE FACTORY RESET")
    print("=" * 50)
    print()
    print("This will:")
    print("  • Clear all saved WiFi networks")
    print("  • Remove user association")
    print("  • Enable provisioning mode on next boot")
    print()
    
    if not args.force and not confirm("Are you sure you want to reset?"):
        print("Cancelled.")
        return
    
    print()
    
    success = True
    success &= clear_wpa_supplicant()
    success &= clear_network_manager()
    success &= clear_drone_config()
    
    print()
    
    if success:
        restart_services()
        print()
        print("=" * 50)
        print("FACTORY RESET COMPLETE")
        print("=" * 50)
        print()
        print("Next steps:")
        print("  1. Reboot the drone: sudo reboot")
        print("  2. After reboot, connect to the drone's WiFi hotspot")
        print("  3. Use the app to set up the drone again")
    else:
        print()
        print("Reset completed with errors.")
        print("You may need to run this script with sudo.")


if __name__ == "__main__":
    main()

