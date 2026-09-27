"""The flight controller's heartbeat must not be hidden by another component
on the same system id.

Measured on the quadcopter: system 1 has two components sending HEARTBEAT at
1 Hz, back to back -- the ArduPilot FC (comp 1, autopilot=3) and an ADS-B
receiver (comp 0, autopilot=8, type=27). pymavlink caches one HEARTBEAT per
system, so the ADS-B one nearly always overwrote the FC's; is_armed() and the
GUIDED mode wait rejected it as "not the autopilot" and saw nothing. Takeoffs
failed with "Arm ACK'd but not armed" and "Mode change to GUIDED
refused/unanswered" while the FC had armed (the daemon published armed=True
in the same second).

Run: cd eco && python3 -m pytest drone/common/tests/test_fc_heartbeat_shared_sysid.py -v
"""

import sys
from pathlib import Path

import pytest
from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as mavlink

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drone_sdk  # noqa: E402

ARMED = mavlink.MAV_MODE_FLAG_SAFETY_ARMED | mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
GUIDED = 4


def _packet(src_comp, **fields):
    enc = mavlink.MAVLink(None, srcSystem=1, srcComponent=src_comp)
    return enc.heartbeat_encode(**fields).pack(enc)


def _fc_then_adsb():
    fc = _packet(1, type=mavlink.MAV_TYPE_QUADROTOR, autopilot=mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
                 base_mode=ARMED, custom_mode=GUIDED, system_status=mavlink.MAV_STATE_ACTIVE)
    adsb = _packet(0, type=27, autopilot=mavlink.MAV_AUTOPILOT_INVALID,
                   base_mode=0, custom_mode=0, system_status=mavlink.MAV_STATE_ACTIVE)
    return fc + adsb


class Link(mavutil.mavfile):
    """A pymavlink connection over a byte buffer: the real parser, cache and
    hooks, with the aircraft's traffic replayed into it."""

    def __init__(self, data):
        self.data = bytearray(data)
        mavutil.mavfile.__init__(self, None, "replay", source_system=255)
        self.target_system, self.target_component = 1, 1

    def recv(self, n=None):
        n = n or len(self.data)
        chunk, self.data[:] = bytes(self.data[:n]), self.data[n:]
        return chunk

    def write(self, buf):
        pass


@pytest.fixture
def aircraft(monkeypatch):
    link = Link(_fc_then_adsb() * 4)
    link.message_hooks.append(drone_sdk._on_message)
    monkeypatch.setattr(drone_sdk, "_fc_heartbeat", None)
    monkeypatch.setattr(drone_sdk, "_connect", lambda: link)
    monkeypatch.setattr(drone_sdk, "_ensure_streams", lambda m: None)
    return link


def test_the_adsb_heartbeat_is_what_pymavlink_caches(aircraft):
    """The premise: without our own slot, the FC's heartbeat is gone."""
    drone_sdk._pump(0.1)
    assert aircraft.sysid_state[1].messages["HEARTBEAT"].autopilot == mavlink.MAV_AUTOPILOT_INVALID


def test_armed_is_read_from_the_flight_controller(aircraft):
    assert drone_sdk.is_armed() is True


def test_the_mode_is_the_flight_controllers(aircraft):
    assert drone_sdk.get_flight_mode() == "GUIDED"
