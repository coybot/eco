#!/usr/bin/env python3
"""
WiFi Manager - Handles WiFi configuration on Raspberry Pi and Jetson Orin Nano

Supports:
- Checking if WiFi is configured
- Saving WiFi credentials
- Connecting to WiFi networks
- Creating AP hotspot for provisioning
"""

import subprocess
import os
import logging
import time
from pathlib import Path
from typing import Optional, Tuple, List, Dict

logger = logging.getLogger(__name__)

# Paths for WiFi configuration
WPA_SUPPLICANT_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"
NETWORK_MANAGER_CONN_DIR = "/etc/NetworkManager/system-connections"
HOTSPOT_CONNECTION_NAMES = {"DroneHotspot", "Hotspot"}
HOTSPOT_CONNECTION_PREFIXES = ("Presidio-", "DroneSetup")


class WiFiManager:
    """Cross-platform WiFi manager for RPi5 and Orin Nano."""
    
    def __init__(self, interface: str = None):
        self.interface = interface or self._detect_wifi_interface()
        self.backend = self._detect_backend()
        logger.info(f"WiFi manager using interface: {self.interface}, backend: {self.backend}")
    
    def _detect_wifi_interface(self) -> str:
        """Auto-detect the WiFi interface name."""
        # Common interface names to try
        candidates = ["wlan0", "wlP1p1s0", "wlp0s20f3", "wifi0"]
        
        # First, try to get it from NetworkManager
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"],
                capture_output=True, text=True
            )
            for line in result.stdout.strip().split("\n"):
                if ":wifi" in line:
                    iface = line.split(":")[0]
                    if iface and not iface.startswith("p2p"):
                        logger.info(f"Detected WiFi interface from nmcli: {iface}")
                        return iface
        except Exception:
            pass
        
        # Fallback: check /sys/class/net for wireless interfaces
        for iface in candidates:
            if os.path.exists(f"/sys/class/net/{iface}/wireless"):
                logger.info(f"Detected WiFi interface from sysfs: {iface}")
                return iface
        
        # Last resort: check all interfaces
        try:
            for iface in os.listdir("/sys/class/net"):
                if os.path.exists(f"/sys/class/net/{iface}/wireless"):
                    logger.info(f"Detected WiFi interface: {iface}")
                    return iface
        except Exception:
            pass
        
        logger.warning("Could not detect WiFi interface, defaulting to wlan0")
        return "wlan0"
    
    def _detect_backend(self) -> str:
        """Detect whether system uses NetworkManager or wpa_supplicant."""
        # Check if NetworkManager is running
        result = subprocess.run(
            ["systemctl", "is-active", "NetworkManager"],
            capture_output=True, text=True
        )
        if result.stdout.strip() == "active":
            return "networkmanager"
        return "wpa_supplicant"
    
    def is_wifi_configured(self) -> bool:
        """Check if WiFi credentials are saved."""
        if self.backend == "networkmanager":
            return self._nm_has_wifi_connection()
        else:
            return self._wpa_has_wifi_connection()
    
    def _nm_has_wifi_connection(self) -> bool:
        """Check NetworkManager for saved WiFi connections (excluding hotspots).

        Delegates to _nm_list_wifi_connections rather than repeating the scan and
        the hotspot filter: the duplicate copy here drifted, missing the
        HOTSPOT_CONNECTION_PREFIXES entry for our own AP name, so a leftover
        hotspot profile read as real WiFi and suppressed provisioning for good.
        """
        names = self._nm_list_wifi_connections()
        if names:
            logger.info(f"Found WiFi connection: {names[0]}")
        return bool(names)
    
    def _wpa_has_wifi_connection(self) -> bool:
        """Check wpa_supplicant.conf for saved networks."""
        if not os.path.exists(WPA_SUPPLICANT_CONF):
            return False
        
        with open(WPA_SUPPLICANT_CONF, 'r') as f:
            content = f.read()
        
        return "network={" in content
    
    def get_mac_address(self) -> str:
        """Get the WiFi interface MAC address."""
        try:
            with open(f"/sys/class/net/{self.interface}/address", 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            # Try alternate interface names
            for iface in ["wlan0", "wlp0s20f3", "wifi0"]:
                try:
                    with open(f"/sys/class/net/{iface}/address", 'r') as f:
                        self.interface = iface
                        return f.read().strip()
                except FileNotFoundError:
                    continue
            return "00:00:00:00:00:00"
    
    def get_hotspot_name(self) -> str:
        """Generate hotspot name from model and MAC address."""
        mac = self.get_mac_address().replace(":", "")
        model = self._get_model_name()
        return f"Presidio-{model}-{mac[-4:].upper()}"

    def get_hotspot_password(self) -> str:
        """
        Get or generate a cryptographically random hotspot password.
        Password is stored persistently so it remains stable during provisioning.
        Password is regenerated after each successful WiFi configuration.
        """
        import secrets
        password_file = Path("/var/lib/presidio/hotspot_password")
        
        # Try to read existing password
        try:
            if password_file.exists():
                password = password_file.read_text().strip()
                if len(password) >= 12:
                    return password
        except Exception:
            pass
        
        # Generate new cryptographically random password
        # Format: 4 random alphanumeric groups separated by hyphens (easy to type)
        import string
        chars = string.ascii_letters + string.digits
        password = '-'.join(
            ''.join(secrets.choice(chars) for _ in range(4))
            for _ in range(3)
        )  # e.g., "Ab3d-Ef5h-Ij7k"
        
        # Save password with secure permissions
        try:
            password_file.parent.mkdir(parents=True, exist_ok=True)
            password_file.write_text(password)
            os.chmod(password_file, 0o600)
            logger.info(f"Generated new hotspot password (stored at {password_file})")
        except Exception as e:
            logger.warning(f"Could not persist hotspot password: {e}")
        
        return password
    
    def regenerate_hotspot_password(self) -> str:
        """Force regeneration of hotspot password (call after successful WiFi setup)."""
        password_file = Path("/var/lib/presidio/hotspot_password")
        try:
            if password_file.exists():
                password_file.unlink()
        except Exception:
            pass
        return self.get_hotspot_password()

    def _get_model_name(self) -> str:
        try:
            with open("/proc/device-tree/model", "r") as f:
                model = f.read().strip().split("\x00")[0]
            return model.replace(" ", "")[:12] or "Drone"
        except Exception:
            return "Drone"
    
    def save_wifi_credentials(self, ssid: str, password: str) -> bool:
        """Save WiFi credentials to the system."""
        try:
            if self.backend == "networkmanager":
                return self._nm_save_wifi(ssid, password)
            else:
                return self._wpa_save_wifi(ssid, password)
        except Exception as e:
            logger.error(f"Failed to save WiFi credentials: {e}")
            return False

    def list_saved_networks(self) -> List[str]:
        """List saved WiFi network names (SSIDs/connection names)."""
        if self.backend == "networkmanager":
            return self._nm_list_wifi_connections()
        return self._wpa_list_wifi_networks()

    def delete_network(self, ssid: str) -> bool:
        """Delete a saved WiFi network by SSID/connection name."""
        ssid = (ssid or "").strip()
        if not ssid:
            return False
        try:
            if self.backend == "networkmanager":
                result = subprocess.run(
                    ["nmcli", "connection", "delete", ssid],
                    capture_output=True,
                    text=True,
                    timeout=15
                )
                if result.returncode != 0:
                    logger.warning(f"nmcli delete failed for {ssid}: {result.stderr.strip()}")
                return result.returncode == 0
            else:
                # For wpa_supplicant backend, we'll rewrite config elsewhere; here we do a best-effort no-op.
                return True
        except Exception as e:
            logger.error(f"Failed to delete network {ssid}: {e}")
            return False

    def upsert_network(self, ssid: str, password: str, priority: int = 0, enabled: bool = True) -> bool:
        """Create or update a WiFi network entry."""
        ssid = (ssid or "").strip()
        if not ssid:
            return False
        password = password or ""

        if self.backend == "networkmanager":
            return self._nm_upsert_wifi(ssid, password, priority=priority, enabled=enabled)
        else:
            # For wpa_supplicant, we generally apply the full list by rewriting config.
            return self._wpa_save_wifi(ssid, password)

    def apply_network_list(self, networks: List[Dict]) -> bool:
        """
        Reconcile saved WiFi networks to match the desired list.

        Expected shape:
          [{"ssid": "...", "password": "...", "priority": 0, "enabled": True}, ...]
        """
        try:
            # Normalize
            desired = []
            for i, n in enumerate(networks or []):
                ssid = (n.get("ssid") or "").strip()
                if not ssid:
                    continue
                desired.append({
                    "ssid": ssid,
                    "password": str(n.get("password") or ""),
                    "priority": int(n.get("priority", i)),
                    "enabled": bool(n.get("enabled", True)),
                })
            desired.sort(key=lambda x: x["priority"])
            for i, n in enumerate(desired):
                n["priority"] = i

            if self.backend == "networkmanager":
                return self._nm_apply_network_list(desired)
            else:
                return self._wpa_apply_network_list(desired)
        except Exception as e:
            logger.error(f"Failed to apply network list: {e}")
            return False
    
    def _nm_save_wifi(self, ssid: str, password: str) -> bool:
        """Save WiFi using NetworkManager."""
        # Delete existing connection with same name if exists
        subprocess.run(
            ["nmcli", "connection", "delete", ssid],
            capture_output=True
        )
        
        # Add new connection
        result = subprocess.run([
            "nmcli", "connection", "add",
            "type", "wifi",
            "con-name", ssid,
            "ssid", ssid,
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.psk", password,
            "connection.autoconnect", "yes",
            "connection.autoconnect-priority", "100"
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"nmcli add failed: {result.stderr}")
            return False
        
        return True

    def _is_provisioning_connection(self, name: str) -> bool:
        if not name:
            return True
        if name in HOTSPOT_CONNECTION_NAMES:
            return True
        return any(name.startswith(prefix) for prefix in HOTSPOT_CONNECTION_PREFIXES)

    def _nm_list_wifi_connections(self) -> List[str]:
        """List NetworkManager WiFi connections (excluding provisioning/hotspot)."""
        result = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE,NAME", "connection", "show"],
            capture_output=True,
            text=True
        )
        names: List[str] = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            if line.startswith("802-11-wireless:"):
                name = line.split(":", 1)[1] if ":" in line else ""
                if not name or self._is_provisioning_connection(name):
                    continue
                names.append(name)
        return names

    def _nm_upsert_wifi(self, ssid: str, password: str, priority: int, enabled: bool) -> bool:
        """Create or replace a WiFi connection with the desired autoconnect priority."""
        # Delete existing connection with same name if exists
        subprocess.run(["nmcli", "connection", "delete", ssid], capture_output=True)

        # Map our priority (0 = highest) to NM autoconnect-priority (higher wins).
        nm_prio = max(min(100 - int(priority), 999), -999)
        autoconnect = "yes" if enabled else "no"

        result = subprocess.run([
            "nmcli", "connection", "add",
            "type", "wifi",
            "con-name", ssid,
            "ssid", ssid,
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.psk", password,
            "connection.autoconnect", autoconnect,
            "connection.autoconnect-priority", str(nm_prio)
        ], capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"nmcli upsert failed: {result.stderr}")
            return False
        return True

    def _nm_apply_network_list(self, desired: List[Dict]) -> bool:
        """Apply desired networks using NetworkManager."""
        desired_ssids = {n["ssid"] for n in desired}

        # Delete any non-desired WiFi connections (excluding provisioning/hotspot)
        for existing in self._nm_list_wifi_connections():
            if existing not in desired_ssids:
                logger.info(f"Removing WiFi connection not in desired list: {existing}")
                subprocess.run(["nmcli", "connection", "delete", existing], capture_output=True)

        # Upsert desired connections with priorities
        ok = True
        for n in desired:
            logger.info(f"Upserting WiFi connection: {n['ssid']} (priority={n['priority']}, enabled={n['enabled']})")
            if not self._nm_upsert_wifi(n["ssid"], n["password"], priority=n["priority"], enabled=n["enabled"]):
                ok = False

        # Best-effort: bring up highest-priority enabled network
        for n in desired:
            if n["enabled"]:
                self.connect_to_wifi(n["ssid"])
                break

        return ok
    
    def _wpa_save_wifi(self, ssid: str, password: str) -> bool:
        """Save WiFi using wpa_supplicant."""
        # Generate PSK
        result = subprocess.run(
            ["wpa_passphrase", ssid, password],
            capture_output=True, text=True
        )
        
        if result.returncode != 0:
            logger.error(f"wpa_passphrase failed: {result.stderr}")
            return False
        
        # Parse the network block (remove plaintext password comment)
        network_block = "\n".join(
            line for line in result.stdout.split("\n")
            if not line.strip().startswith("#")
        )
        
        # Read existing config
        existing = ""
        if os.path.exists(WPA_SUPPLICANT_CONF):
            with open(WPA_SUPPLICANT_CONF, 'r') as f:
                existing = f.read()
        
        # Ensure header exists
        if "ctrl_interface=" not in existing:
            existing = """ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US

"""
        
        # Append network block
        with open(WPA_SUPPLICANT_CONF, 'w') as f:
            f.write(existing.rstrip() + "\n\n" + network_block + "\n")
        
        return True

    def _wpa_list_wifi_networks(self) -> List[str]:
        """Best-effort list of SSIDs from wpa_supplicant.conf."""
        if not os.path.exists(WPA_SUPPLICANT_CONF):
            return []
        try:
            ssids = []
            with open(WPA_SUPPLICANT_CONF, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('ssid="') and line.endswith('"'):
                        ssids.append(line[len('ssid="'):-1])
            return ssids
        except Exception:
            return []

    def _wpa_apply_network_list(self, desired: List[Dict]) -> bool:
        """Rewrite wpa_supplicant.conf to match desired list (enabled networks only)."""
        header = """ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US

"""
        blocks: List[str] = []
        for n in desired:
            if not n.get("enabled", True):
                continue
            ssid = n["ssid"]
            password = n["password"]
            prio = int(n.get("priority", 0))
            result = subprocess.run(
                ["wpa_passphrase", ssid, password],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                logger.error(f"wpa_passphrase failed for {ssid}: {result.stderr}")
                return False
            # Remove plaintext password comment and add priority field.
            raw = "\n".join(line for line in result.stdout.split("\n") if not line.strip().startswith("#"))
            # Insert priority before closing brace if present.
            raw_lines = raw.splitlines()
            out_lines = []
            for line in raw_lines:
                if line.strip() == "}":
                    out_lines.append(f"    priority={prio}")
                out_lines.append(line)
            blocks.append("\n".join(out_lines).strip())

        try:
            with open(WPA_SUPPLICANT_CONF, "w") as f:
                f.write(header)
                if blocks:
                    f.write("\n\n".join(blocks) + "\n")
        except Exception as e:
            logger.error(f"Failed writing {WPA_SUPPLICANT_CONF}: {e}")
            return False

        # Reconfigure to apply
        subprocess.run(["wpa_cli", "-i", self.interface, "reconfigure"], capture_output=True)
        time.sleep(3)
        # Best-effort connect check
        return True
    
    def connect_to_wifi(self, ssid: Optional[str] = None) -> bool:
        """Connect to the configured WiFi network."""
        try:
            if self.backend == "networkmanager":
                if ssid:
                    result = subprocess.run(
                        ["nmcli", "connection", "up", ssid],
                        capture_output=True, text=True
                    )
                else:
                    result = subprocess.run(
                        ["nmcli", "device", "wifi", "connect"],
                        capture_output=True, text=True
                    )
                return result.returncode == 0
            else:
                # Restart wpa_supplicant to pick up new config
                subprocess.run(["wpa_cli", "-i", self.interface, "reconfigure"])
                time.sleep(5)
                return self._check_connection()
        except Exception as e:
            logger.error(f"Failed to connect to WiFi: {e}")
            return False
    
    def _check_connection(self) -> bool:
        """Check if we have an IP address."""
        result = subprocess.run(
            ["ip", "addr", "show", self.interface],
            capture_output=True, text=True
        )
        return "inet " in result.stdout
    
    def start_hotspot(self, ssid: Optional[str] = None, password: Optional[str] = None) -> bool:
        """Start WiFi AP hotspot for provisioning."""
        if ssid is None:
            ssid = self.get_hotspot_name()
        if password is None:
            password = self.get_hotspot_password()
        
        logger.info(f"Starting hotspot: {ssid}")
        
        try:
            if self.backend == "networkmanager":
                return self._nm_start_hotspot(ssid, password)
            else:
                return self._hostapd_start_hotspot(ssid, password)
        except Exception as e:
            logger.error(f"Failed to start hotspot: {e}")
            return False
    
    def _nm_start_hotspot(self, ssid: str, password: Optional[str]) -> bool:
        """Start hotspot using hostapd (more reliable than NetworkManager)."""
        # NetworkManager's hotspot mode hangs on some devices, use hostapd instead
        return self._hostapd_start_hotspot(ssid, password)
    
    def _hostapd_start_hotspot(self, ssid: str, password: Optional[str]) -> bool:
        """Start hotspot using hostapd (more reliable than NetworkManager)."""
        logger.info(f"Starting hostapd hotspot: {ssid}")
        
        # Stop NetworkManager from managing this interface
        subprocess.run(["nmcli", "device", "set", self.interface, "managed", "no"], 
                      capture_output=True, timeout=10)
        subprocess.run(["nmcli", "device", "disconnect", self.interface], 
                      capture_output=True, timeout=10)
        
        # Kill any existing hostapd/dnsmasq
        subprocess.run(["killall", "hostapd"], capture_output=True)
        subprocess.run(["killall", "dnsmasq"], capture_output=True)
        time.sleep(1)
        
        # Create hostapd config
        hostapd_conf = f"""interface={self.interface}
driver=nl80211
ssid={ssid}
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
"""
        hostapd_conf += f"""wpa=2
wpa_passphrase={password}
wpa_key_mgmt=WPA-PSK SAE
rsn_pairwise=CCMP
ieee80211w=2
"""
        
        # Write config
        with open("/tmp/hostapd.conf", 'w') as f:
            f.write(hostapd_conf)
        
        # Configure interface
        subprocess.run(["ip", "link", "set", self.interface, "down"], capture_output=True)
        subprocess.run(["ip", "addr", "flush", "dev", self.interface], capture_output=True)
        subprocess.run(["ip", "addr", "add", "192.168.4.1/24", "dev", self.interface], capture_output=True)
        subprocess.run(["ip", "link", "set", self.interface, "up"], capture_output=True)
        
        # Start hostapd
        logger.info("Starting hostapd...")
        self._hostapd_proc = subprocess.Popen(
            ["hostapd", "/tmp/hostapd.conf"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        time.sleep(2)
        
        if self._hostapd_proc.poll() is not None:
            # hostapd exited, get error
            _, stderr = self._hostapd_proc.communicate()
            logger.error(f"hostapd failed: {stderr.decode()}")
            return False
        
        # Start dnsmasq for DHCP
        logger.info("Starting dnsmasq...")
        # Kill system dnsmasq if running
        subprocess.run(["systemctl", "stop", "dnsmasq"], capture_output=True)
        
        with open("/tmp/dnsmasq.conf", 'w') as f:
            f.write(f"""interface={self.interface}
bind-interfaces
dhcp-range=192.168.4.10,192.168.4.100,255.255.255.0,12h
""")
        
        self._dnsmasq_proc = subprocess.Popen(
            ["dnsmasq", "-C", "/tmp/dnsmasq.conf", "--no-daemon", "--log-queries"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        time.sleep(1)
        
        if self._dnsmasq_proc.poll() is not None:
            _, stderr = self._dnsmasq_proc.communicate()
            logger.error(f"dnsmasq failed: {stderr.decode()}")
            # Still return True if hostapd is running (DHCP just won't work)
        
        logger.info(f"Hotspot '{ssid}' started successfully")
        return True
    
    def stop_hotspot(self) -> bool:
        """Stop the WiFi hotspot."""
        try:
            if self.backend == "networkmanager":
                # Disconnect hotspot
                subprocess.run(
                    ["nmcli", "connection", "down", "Hotspot"],
                    capture_output=True
                )
                subprocess.run(
                    ["nmcli", "connection", "delete", "Hotspot"],
                    capture_output=True
                )
            else:
                # Stop hostapd and dnsmasq
                if hasattr(self, '_hostapd_proc'):
                    self._hostapd_proc.terminate()
                if hasattr(self, '_dnsmasq_proc'):
                    self._dnsmasq_proc.terminate()
                
                subprocess.run(["killall", "hostapd"], capture_output=True)
                subprocess.run(["killall", "dnsmasq"], capture_output=True)
                
                # Restart wpa_supplicant
                subprocess.run(["systemctl", "start", "wpa_supplicant"], capture_output=True)
            
            return True
        except Exception as e:
            logger.error(f"Failed to stop hotspot: {e}")
            return False
    
    def get_ip_address(self) -> Optional[str]:
        """Get current IP address of WiFi interface."""
        try:
            result = subprocess.run(
                ["ip", "-4", "addr", "show", self.interface],
                capture_output=True, text=True
            )
            for line in result.stdout.split("\n"):
                if "inet " in line:
                    # Extract IP from "inet 192.168.1.100/24 ..."
                    return line.strip().split()[1].split("/")[0]
        except Exception:
            pass
        return None


if __name__ == "__main__":
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

