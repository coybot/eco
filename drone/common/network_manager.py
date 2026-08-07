#!/usr/bin/env python3
"""
Presidio Network Manager (Orin Nano)

Deterministic 2-state Wi-Fi controller:
  - Infrastructure Wi-Fi
  - AP mode fallback

State machine (simplified):
  Boot:
    - If known SSID available and connection succeeds -> InfraWiFi
    - Else -> APMode
  InfraWiFi:
    - If Wi-Fi disconnects and reconnect fails -> APMode
  APMode:
    - Never exit if a client is connected or mission active
    - Exit when a known SSID is in range and no clients + no mission

Mermaid (for documentation):
  stateDiagram-v2
    [*] --> InfraWiFi
    InfraWiFi --> APMode: noKnownSSIDs_or_wifiDisconnected
    APMode --> InfraWiFi: noClients_and_noMission_and_knownSSID
    APMode --> APMode: clientConnected_or_missionActive
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

logger = logging.getLogger("presidio.network")

DEFAULT_CONFIG_PATHS = [
    "/etc/presidio/network_manager.yaml",
    str(Path(__file__).with_name("network_manager.yaml")),
]

STATE_DIR = Path("/var/lib/presidio")
MISSION_ACTIVE_PATH = STATE_DIR / "mission_active.json"

HOSTAPD_CONF = Path("/run/presidio/hostapd.conf")
DNSMASQ_CONF = Path("/run/presidio/dnsmasq.conf")
HOTSPOT_CONNECTION_NAMES = {"DroneHotspot", "Hotspot"}
HOTSPOT_SSID_PREFIXES = ("Presidio-", "DroneSetup")


@dataclass
class NetworkConfig:
    interface: Optional[str] = None
    model_name: str = "Orin"
    ap_password: Optional[str] = None
    ap_channel: int = 6
    ap_ip: str = "192.168.4.1"
    ap_subnet: str = "192.168.4.0/24"
    dhcp_range_start: str = "192.168.4.10"
    dhcp_range_end: str = "192.168.4.100"
    dhcp_lease: str = "12h"
    internet_check_interval_s: int = 5
    internet_stable_seconds: int = 20
    internet_failures_to_ap: int = 1
    known_ssids: List[str] = None
    dry_run: bool = False


class PresidioNetworkManager:
    def __init__(self, config: NetworkConfig):
        self.config = config
        self.interface = config.interface or self._detect_wifi_interface()
        self.state = "infra"
        self._failure_count = 0
        self._stable_since: Optional[float] = None
        self._ap_running = False
        logger.info("Network manager initialized (iface=%s, dry_run=%s)", self.interface, self.config.dry_run)

    def run(self) -> None:
        self._ensure_state_dirs()
        self._boot_sequence()
        while True:
            try:
                self._tick()
            except Exception as exc:
                logger.exception("Unhandled error in tick: %s", exc)
            time.sleep(self.config.internet_check_interval_s)

    def _boot_sequence(self) -> None:
        self._nm_cleanup_hotspot_connections()
        known = self._get_known_ssids()
        if not known:
            logger.warning("No known SSIDs; entering AP mode")
            self._enter_ap(reason="no_known_ssids")
            return

        if self._connect_to_known_ssid(known):
            logger.info("Wi-Fi connected on boot; staying in infra")
            self.state = "infra"
            return

        logger.warning("Wi-Fi connect failed on boot; entering AP mode")
        self._enter_ap(reason="wifi_connect_failed_boot")

    def _tick(self) -> None:
        if self.state == "infra":
            if self._wifi_connected():
                logger.debug("Infra Wi-Fi connected; staying in infra")
                return
            logger.warning("Infra Wi-Fi disconnected; attempting reconnect")
            known = self._get_known_ssids()
            if known and self._connect_to_known_ssid(known):
                logger.info("Reconnected to Wi-Fi; staying in infra")
                return
            self._enter_ap(reason="wifi_disconnected_no_reconnect")
        else:
            if self._should_keep_ap():
                self._stable_since = None
                logger.debug("Keeping AP (client or mission active)")
                return

            known = self._get_known_ssids()
            if not known:
                logger.info("AP mode: no known SSIDs available")
                self._stable_since = None
                return

            if not self._known_ssid_in_range(known):
                logger.info("AP mode: known SSID not in range yet")
                self._stable_since = None
                return

            logger.info("AP mode: known SSID in range; trying infra")
            self._exit_ap(reason="known_ssid_in_range")
            if self._connect_to_known_ssid(known):
                self.state = "infra"
                logger.info("Switched to infra (Wi-Fi connected)")
                return

            self._enter_ap(reason="infra_switch_failed")

    def _ensure_state_dirs(self) -> None:
        try:
            if not self.config.dry_run:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                Path("/run/presidio").mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning("Failed creating state dirs: %s", exc)

    def _internet_check(self) -> Tuple[bool, str]:
        # Primary: HTTPS HEAD
        try:
            req = urllib.request.Request("https://1.1.1.1", method="HEAD")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if 200 <= resp.status < 400:
                    return True, "https_head"
        except Exception:
            pass

        # Fallback: DNS lookup
        try:
            socket.getaddrinfo("one.one.one.one", 53)
            return True, "dns_lookup"
        except Exception as exc:
            return False, f"dns_failed:{exc.__class__.__name__}"

    def _wifi_connected(self) -> bool:
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "GENERAL.STATE", "device", "show", self.interface],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return False
            state_line = result.stdout.strip().split(":")[-1]
            return state_line.startswith("100")
        except Exception:
            return False

    def _get_known_ssids(self) -> List[str]:
        if self.config.known_ssids:
            return list(self.config.known_ssids)
        return self._nm_list_saved_ssids()

    def _known_ssid_in_range(self, known: List[str]) -> bool:
        visible = self._nm_scan_ssids()
        return any(ssid in visible for ssid in known)

    def _connect_to_known_ssid(self, known: List[str]) -> bool:
        for ssid in known:
            logger.info("Attempting Wi-Fi connect to %s", ssid)
            if self.config.dry_run:
                return True
            result = subprocess.run(["nmcli", "connection", "up", ssid], capture_output=True, text=True, timeout=20)
            if result.returncode == 0:
                logger.info("Connected to %s", ssid)
                return True
            logger.warning("Failed to connect to %s: %s", ssid, result.stderr.strip())
        return False

    def _enter_ap(self, reason: str) -> None:
        if self.state == "ap" and self._ap_running:
            return
        logger.warning("Entering AP mode (reason=%s)", reason)
        if not self.config.dry_run:
            self._start_ap()
        self.state = "ap"
        self._ap_running = True
        self._stable_since = None

    def _exit_ap(self, reason: str) -> None:
        if not self._ap_running:
            return
        logger.warning("Exiting AP mode (reason=%s)", reason)
        if not self.config.dry_run:
            self._stop_ap()
        self._ap_running = False
        self.state = "infra"

    def _start_ap(self) -> None:
        ssid = self._build_ap_ssid()
        password = self.config.ap_password or self._default_ap_password()
        logger.info("Starting AP SSID=%s", ssid)

        subprocess.run(["nmcli", "device", "set", self.interface, "managed", "no"], capture_output=True)
        subprocess.run(["nmcli", "device", "disconnect", self.interface], capture_output=True)

        subprocess.run(["pkill", "-f", "hostapd"], capture_output=True)
        subprocess.run(["pkill", "-f", "dnsmasq"], capture_output=True)
        time.sleep(1)

        HOSTAPD_CONF.parent.mkdir(parents=True, exist_ok=True)
        hostapd_conf = self._hostapd_conf(ssid, password)
        HOSTAPD_CONF.write_text(hostapd_conf)

        subprocess.run(["ip", "link", "set", self.interface, "down"], capture_output=True)
        subprocess.run(["ip", "addr", "flush", "dev", self.interface], capture_output=True)
        subprocess.run(["ip", "addr", "add", f"{self.config.ap_ip}/24", "dev", self.interface], capture_output=True)
        subprocess.run(["ip", "link", "set", self.interface, "up"], capture_output=True)

        logger.info("Launching hostapd")
        subprocess.Popen(["hostapd", str(HOSTAPD_CONF)])

        DNSMASQ_CONF.parent.mkdir(parents=True, exist_ok=True)
        dnsmasq_conf = self._dnsmasq_conf()
        DNSMASQ_CONF.write_text(dnsmasq_conf)
        logger.info("Launching dnsmasq")
        subprocess.Popen(["dnsmasq", "-C", str(DNSMASQ_CONF), "--no-daemon", "--log-queries"])

    def _stop_ap(self) -> None:
        subprocess.run(["pkill", "-f", "hostapd"], capture_output=True)
        subprocess.run(["pkill", "-f", "dnsmasq"], capture_output=True)
        time.sleep(1)
        subprocess.run(["nmcli", "device", "set", self.interface, "managed", "yes"], capture_output=True)

    def _hostapd_conf(self, ssid: str, password: str) -> str:
        return "\n".join([
            f"interface={self.interface}",
            "driver=nl80211",
            f"ssid={ssid}",
            "hw_mode=g",
            f"channel={self.config.ap_channel}",
            "wmm_enabled=1",
            "auth_algs=1",
            "ignore_broadcast_ssid=0",
            "wpa=2",
            "wpa_key_mgmt=WPA-PSK SAE",
            f"wpa_passphrase={password}",
            "rsn_pairwise=CCMP",
            "ieee80211w=2",
            "",
        ])

    def _dnsmasq_conf(self) -> str:
        return "\n".join([
            f"interface={self.interface}",
            "bind-interfaces",
            f"dhcp-range={self.config.dhcp_range_start},{self.config.dhcp_range_end},255.255.255.0,{self.config.dhcp_lease}",
            "",
        ])

    def _default_ap_password(self) -> str:
        last4 = self._get_mac_last4()
        return f"Presidio{last4}!"

    def _build_ap_ssid(self) -> str:
        last4 = self._get_mac_last4()
        return f"Presidio-{self.config.model_name}-{last4}"

    def _get_mac_last4(self) -> str:
        try:
            with open(f"/sys/class/net/{self.interface}/address", "r") as fh:
                mac = fh.read().strip().replace(":", "")
            return mac[-4:].upper()
        except Exception:
            return "0000"

    def _should_keep_ap(self) -> bool:
        if self._mission_active():
            logger.info("AP hold: mission active")
            return True
        client_state = self._ap_clients_connected()
        if client_state is True:
            logger.info("AP hold: client connected")
            return True
        if client_state is None:
            logger.warning("AP hold: client state unknown; holding for safety")
            return True
        return False

    def _mission_active(self) -> bool:
        try:
            if not MISSION_ACTIVE_PATH.exists():
                return False
            data = json.loads(MISSION_ACTIVE_PATH.read_text() or "{}")
            return bool(data.get("active"))
        except Exception:
            return True

    def _ap_clients_connected(self) -> Optional[bool]:
        try:
            result = subprocess.run(["iw", "dev", self.interface, "station", "dump"],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                return None
            stations = [line for line in result.stdout.splitlines() if line.startswith("Station ")]
            return len(stations) > 0
        except Exception:
            return None

    def _stable_elapsed(self) -> float:
        if self._stable_since is None:
            return 0.0
        return time.monotonic() - self._stable_since

    def _detect_wifi_interface(self) -> str:
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split("\n"):
                if ":wifi" in line:
                    iface = line.split(":")[0]
                    if iface and not iface.startswith("p2p"):
                        return iface
        except Exception:
            pass
        return "wlan0"

    def _nm_list_saved_ssids(self) -> List[str]:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE,NAME", "connection", "show"],
            capture_output=True, text=True
        )
        ssids: List[str] = []
        for line in result.stdout.strip().split("\n"):
            if line.startswith("802-11-wireless:"):
                name = line.split(":", 1)[1] if ":" in line else ""
                if not name:
                    continue
                if name in HOTSPOT_CONNECTION_NAMES:
                    continue
                if name.startswith(HOTSPOT_SSID_PREFIXES):
                    continue
                ssids.append(name)
        return ssids

    def _nm_cleanup_hotspot_connections(self) -> None:
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "TYPE,NAME", "connection", "show"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.strip().split("\n"):
                if not line.startswith("802-11-wireless:"):
                    continue
                name = line.split(":", 1)[1] if ":" in line else ""
                if not name:
                    continue
                if name in HOTSPOT_CONNECTION_NAMES or name.startswith(HOTSPOT_SSID_PREFIXES):
                    logger.info("Removing hotspot connection from NM: %s", name)
                    subprocess.run(["nmcli", "connection", "delete", name], capture_output=True)
        except Exception as exc:
            logger.warning("Failed cleanup of hotspot connections: %s", exc)

    def _nm_scan_ssids(self) -> List[str]:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID", "dev", "wifi", "list", "ifname", self.interface],
            capture_output=True, text=True
        )
        ssids: List[str] = []
        for line in result.stdout.split("\n"):
            ssid = line.strip()
            if ssid:
                ssids.append(ssid)
        return ssids


def load_config() -> NetworkConfig:
    config_data = {}
    for path in DEFAULT_CONFIG_PATHS:
        if os.path.exists(path):
            with open(path, "r") as fh:
                config_data = yaml.safe_load(fh) or {}
            logger.info("Loaded network manager config from %s", path)
            break
    cfg = NetworkConfig(
        interface=config_data.get("interface"),
        model_name=config_data.get("model_name", "Orin"),
        ap_password=config_data.get("ap_password"),
        ap_channel=int(config_data.get("ap_channel", 6)),
        ap_ip=config_data.get("ap_ip", "192.168.4.1"),
        ap_subnet=config_data.get("ap_subnet", "192.168.4.0/24"),
        dhcp_range_start=config_data.get("dhcp_range_start", "192.168.4.10"),
        dhcp_range_end=config_data.get("dhcp_range_end", "192.168.4.100"),
        dhcp_lease=config_data.get("dhcp_lease", "12h"),
        internet_check_interval_s=int(config_data.get("internet_check_interval_s", 5)),
        internet_stable_seconds=int(config_data.get("internet_stable_seconds", 20)),
        internet_failures_to_ap=int(config_data.get("internet_failures_to_ap", 1)),
        known_ssids=config_data.get("known_ssids") or None,
        dry_run=bool(config_data.get("dry_run", False)),
    )
    return cfg


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    cfg = load_config()
    manager = PresidioNetworkManager(cfg)
    manager.run()


if __name__ == "__main__":
    main()
