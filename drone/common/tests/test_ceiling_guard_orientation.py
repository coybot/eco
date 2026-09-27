"""The ceiling guard must read a rangefinder that points UP.

It tested for orientation 25 and called that upward; in MAVLink 25 is
PITCH_270, pointing down (up is 24, PITCH_90). The quadcopter has two
downward rangefinders (RNGFND1/2_ORIENT=25) and no upward one, so the guard
measured the ground: before takeoff it logged "CEILING GUARD: 0.39m
clearance - holding altitude" and kept re-issuing a hold at the current
altitude while the takeoff tried to climb.

Run: cd eco && python3 -m pytest drone/common/tests/test_ceiling_guard_orientation.py -v
"""

import sys
import threading
import time
from pathlib import Path

import pytest
from pymavlink.dialects.v20 import ardupilotmega as mavlink

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import drone_sdk  # noqa: E402
from test_fc_heartbeat_shared_sysid import Link  # noqa: E402

DOWN, UP = mavlink.MAV_SENSOR_ROTATION_PITCH_270, mavlink.MAV_SENSOR_ROTATION_PITCH_90


def _range(orientation, cm, sensor_id):
    enc = mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
    return enc.distance_sensor_encode(0, 20, 700, cm, 0, sensor_id, orientation, 0).pack(enc)


def _position():
    enc = mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
    return enc.global_position_int_encode(0, 384770380, -1213858850, 0, 400, 0, 0, 0, 0).pack(enc)


class Stream(Link):
    """Replays the same traffic forever, and records what is sent."""

    def __init__(self, packets):
        self.packets = packets
        self.sent = []
        Link.__init__(self, b"")

    def recv(self, n=None):
        if not self.data:
            self.data.extend(self.packets)
        return Link.recv(self, n)

    def write(self, buf):
        self.sent.append(bytes(buf))


def _run_guard(link, monkeypatch, seconds=0.6):
    link.message_hooks.append(drone_sdk._on_message)
    monkeypatch.setattr(drone_sdk, "_upward_range", None)
    monkeypatch.setattr(drone_sdk, "_ceiling_guard_holding", None)
    monkeypatch.setattr(drone_sdk, "_connect", lambda: link)
    stop = threading.Event()
    monkeypatch.setattr(drone_sdk, "_ceiling_guard_stop", stop)
    held = []
    t = threading.Thread(target=drone_sdk._ceiling_guard_loop, args=(0.5,), daemon=True)
    t.start()
    end = time.time() + seconds
    while time.time() < end:
        held.append(drone_sdk._ceiling_guard_holding)
        time.sleep(0.05)
    stop.set()
    t.join(2)
    return [h for h in held if h is not None]


def test_the_ground_under_the_aircraft_is_not_a_ceiling(monkeypatch):
    link = Stream(_range(DOWN, 39, 0) + _range(DOWN, 41, 1) + _position())
    assert _run_guard(link, monkeypatch) == []
    assert link.sent == [], "no altitude hold may be sent"


def test_an_upward_rangefinder_still_holds_altitude(monkeypatch):
    # Upward first, then a downward one: pymavlink's own cache would end on
    # the downward reading every time.
    link = Stream(_range(UP, 30, 2) + _range(DOWN, 400, 0) + _position())
    held = _run_guard(link, monkeypatch)
    assert held and held[-1] == pytest.approx(0.30)
    assert link.sent, "a hold at the current altitude is sent"
