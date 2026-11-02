"""ardupilot_l5 through the REAL drone_sdk helpers and pymavlink's parser.

test_ardupilot_l5.py pins the frame maths against a duck-typed fake. This one
checks the integration that fake cannot: that the adapters read what drone_sdk's
_pump / _fc_cached actually leave in pymavlink's cache, and that the setpoint
they hand to drone_sdk._mav_send encodes to the SET_POSITION_TARGET_LOCAL_NED
the autopilot will parse. Skipped where pymavlink is not installed.

Run: cd eco && python3 -m pytest drone/common/tests/test_ardupilot_l5_pymavlink.py -v
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

mavutil = pytest.importorskip("pymavlink.mavutil")
from pymavlink.dialects.v20 import ardupilotmega as mavlink  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drone_sdk  # noqa: E402
from ardupilot_l5 import ArduPilotActuator, ArduPilotTelemetry  # noqa: E402
from vehicle_class import get_class  # noqa: E402

ARMED_GUIDED = mavlink.MAV_MODE_FLAG_SAFETY_ARMED | mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED


class Link(mavutil.mavfile):
    """A pymavlink connection over byte buffers: the real parser, cache and hooks."""

    def __init__(self):
        self.data = bytearray()
        self.written = bytearray()
        mavutil.mavfile.__init__(self, None, "replay", source_system=255)
        self.target_system, self.target_component = 1, 1
        self._enc = mavlink.MAVLink(None, srcSystem=1, srcComponent=1)

    def feed(self, msg):
        self.data += msg.pack(self._enc)

    def recv(self, n=None):
        n = n or len(self.data)
        chunk, self.data[:] = bytes(self.data[:n]), self.data[n:]
        return chunk

    def write(self, buf):
        self.written += buf

    def sent(self, name):
        parser = mavlink.MAVLink(None)
        out = parser.parse_buffer(bytes(self.written)) or []
        return [m for m in out if m.get_type() == name]


@pytest.fixture
def link(monkeypatch):
    lk = Link()
    lk.message_hooks.append(drone_sdk._on_message)
    monkeypatch.setattr(drone_sdk, "_fc_heartbeat", None)
    monkeypatch.setattr(drone_sdk, "_pilot_control", None)
    monkeypatch.setattr(drone_sdk, "_flight_home", (37.0, -122.0))
    monkeypatch.setattr(drone_sdk, "_connect", lambda: lk)
    monkeypatch.setattr(drone_sdk, "_ensure_streams", lambda m: None)
    return lk


def _feed_state(lk, custom_mode=4):
    lk.feed(mavlink.MAVLink_heartbeat_message(mavlink.MAV_TYPE_QUADROTOR,
                                              mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
                                              ARMED_GUIDED, custom_mode, mavlink.MAV_STATE_ACTIVE, 3))
    # Home is the EKF origin here; vehicle 4 m north, 2 m east, 5 m up of it, heading east.
    lk.feed(mavlink.MAVLink_local_position_ned_message(1000, 4.0, 2.0, -5.0, 0.0, 1.0, 0.0))
    lk.feed(mavlink.MAVLink_attitude_message(1000, 0.0, 0.0, math.pi / 2, 0.0, 0.0, 0.0))
    lat = 37.0 + 4.0 / 111320.0
    lon = -122.0 + 2.0 / (111320.0 * math.cos(math.radians(37.0)))
    lk.feed(mavlink.MAVLink_global_position_int_message(1000, int(lat * 1e7), int(lon * 1e7),
                                                        5000, 5000, 0, 100, 0, 9000))
    lk.feed(mavlink.MAVLink_ekf_status_report_message(1 | 2 | 8 | 16, 0.1, 0.1, 0.1, 0.1, 0.0))


def test_telemetry_reads_drone_sdks_cache(link):
    _feed_state(link)
    p = ArduPilotTelemetry(sdk=drone_sdk).pose()
    np.testing.assert_allclose(p.pos_enu, [2.0, 4.0, 5.0], atol=0.05)
    np.testing.assert_allclose(p.vel_enu, [1.0, 0.0, 0.0], atol=1e-6)
    assert abs(p.yaw_rad) < 1e-6, "heading east is ENU yaw 0"
    assert p.loc_confidence == 1.0
    intervals = [m for m in link.sent("COMMAND_LONG") if m.command == mavlink.MAV_CMD_SET_MESSAGE_INTERVAL]
    assert {int(m.param1) for m in intervals} >= {30, 32, 33, 193}


def test_actuator_setpoint_encodes_for_the_autopilot(link):
    _feed_state(link)
    drone_sdk._pump(0.0)
    act = ArduPilotActuator("quadcopter", sdk=drone_sdk)
    assert act.expected_sysid == 1
    assert act.send(np.array([1.0, 0.5, 0.25, 0.0]), get_class("quadcopter"), 0.1), act.last_refusal
    sp = link.sent("SET_POSITION_TARGET_LOCAL_NED")[-1]
    assert sp.coordinate_frame == mavlink.MAV_FRAME_BODY_OFFSET_NED
    assert (sp.target_system, sp.target_component) == (1, 1)
    assert (sp.vx, sp.vy, sp.vz) == pytest.approx((1.0, -0.5, -0.25))


def test_actuator_refuses_outside_guided_through_the_real_heartbeat(link):
    _feed_state(link, custom_mode=5)   # LOITER
    drone_sdk._pump(0.0)
    act = ArduPilotActuator("quadcopter", sdk=drone_sdk)
    assert act.send(np.zeros(4), get_class("quadcopter"), 0.1) is False
    assert link.sent("SET_POSITION_TARGET_LOCAL_NED") == []
