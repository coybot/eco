"""is_wifi_configured() decides whether a drone opens its setup hotspot at all.

A false positive here is silent and total: the daemon skips provisioning, so a
drone with no usable network never advertises an AP and there is no way in
without a shell. The check has to ignore our own hotspot profiles, and that
filter has already drifted once -- _nm_has_wifi_connection kept a hand-rolled
copy of it that missed the AP-name prefix.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import wifi_manager  # noqa: E402


@pytest.fixture
def manager(monkeypatch):
    """A NetworkManager-backed WiFiManager whose nmcli output we control."""
    m = wifi_manager.WiFiManager.__new__(wifi_manager.WiFiManager)
    m.backend = "networkmanager"
    m.interface = "wlan0"

    def set_connections(lines):
        def fake_run(cmd, **kwargs):
            out = "\n".join(lines) if "connection" in cmd else ""
            return types.SimpleNamespace(stdout=out, stderr="", returncode=0)
        monkeypatch.setattr(wifi_manager.subprocess, "run", fake_run)
        return m

    return set_connections


@pytest.mark.parametrize("name", [
    "Coybot-NVIDIAJetson-4F2A",   # current AP naming: model + MAC suffix
    "Coybot-Drone-0000",
    "DroneSetup-4F2A",            # legacy AP naming, still seen on old devices
    "DroneHotspot",
    "Hotspot",
])
def test_our_own_hotspot_profiles_do_not_count_as_configured_wifi(manager, name):
    m = manager([f"802-11-wireless:{name}"])
    assert m.is_wifi_configured() is False, (
        f"{name} is one of our provisioning APs; counting it as saved WiFi stops "
        "the drone from ever opening the setup hotspot again"
    )


@pytest.mark.parametrize("lines,expected", [
    (["802-11-wireless:HomeWiFi"], True),
    (["802-11-wireless:Coybot-X-1", "802-11-wireless:HomeWiFi"], True),
    (["802-3-ethernet:Wired connection 1"], False),   # wired is not WiFi
    ([""], False),
    ([], False),
])
def test_real_networks_and_non_wifi(manager, lines, expected):
    m = manager(lines)
    assert m.is_wifi_configured() is expected


def test_hotspot_prefixes_cover_the_name_we_actually_broadcast():
    """The filter and the AP name are set in different places; keep them in step."""
    m = wifi_manager.WiFiManager.__new__(wifi_manager.WiFiManager)
    m.backend = "networkmanager"
    m.interface = "wlan0"
    m.get_mac_address = lambda: "aa:bb:cc:dd:4f:2a"
    m._get_model_name = lambda: "NVIDIAJetson"

    ssid = m.get_hotspot_name()
    assert ssid.startswith(wifi_manager.HOTSPOT_CONNECTION_PREFIXES), (
        f"get_hotspot_name() returns {ssid!r}, which none of "
        f"{wifi_manager.HOTSPOT_CONNECTION_PREFIXES} matches -- a leftover profile "
        "with that name would read as real WiFi"
    )
