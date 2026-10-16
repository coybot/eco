"""The SDK flies PX4 as well as ArduPilot.

The PX4 test aircraft's CubeOrange runs PX4 (HEARTBEAT autopilot=12). The SDK only accepted
ArduPilot's heartbeat, threw PX4's away, and the app showed "no telemetry data".
These pin the autopilot-neutral pieces that need no flight controller.

Run: cd eco && python3 -m pytest drone/common/tests/test_px4_compat.py -v
"""

import io
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pymavlink.dialects.v20 import ardupilotmega as mavlink

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drone_sdk  # noqa: E402
import px4_compat as px4  # noqa: E402


def px4_custom(main, sub=0):
    return (sub << 24) | (main << 16)


def heartbeat(autopilot, custom_mode=0, base_mode=0, system=1, component=1):
    mav = mavlink.MAVLink(io.BytesIO(), srcSystem=system, srcComponent=component)
    msg = mav.heartbeat_encode(2, autopilot, base_mode, custom_mode, 4, 3)
    msg.pack(mav)
    return mavlink.MAVLink(None).parse_char(msg.get_msgbuf())


@pytest.mark.parametrize('main,sub,logical', [
    (4, 3, 'GUIDED'),    # Hold
    (4, 2, 'GUIDED'),    # Takeoff ends in Hold
    (4, 6, 'LAND'), (4, 5, 'RTL'), (4, 4, 'AUTO'),
    (6, 0, 'GUIDED'),    # Offboard
    (3, 0, 'POSHOLD'), (7, 0, 'STABILIZE'), (1, 0, 'MANUAL'),
])
def test_px4_modes_decode_to_the_sdks_names(main, sub, logical):
    assert px4.px4_mode_name(px4_custom(main, sub)) == logical


@pytest.mark.parametrize('logical', ['GUIDED', 'BRAKE', 'LAND', 'RTL', 'SMART_RTL', 'AUTO', 'STABILIZE', 0])
def test_every_mode_the_sdk_asks_for_round_trips(logical):
    """Request a mode, decode the heartbeat PX4 would then send: it must match
    what _wait_for_mode waits for, or every mode change times out."""
    main, sub = px4.command_mode(logical)
    shown = px4.px4_mode_name(px4_custom(main, sub))
    assert shown == px4.expected_mode(px4.AUTOPILOT_PX4, logical)


def test_ardupilot_modes_are_untouched():
    assert px4.expected_mode(px4.AUTOPILOT_ARDUPILOT, 'BRAKE') == 'BRAKE'
    assert px4.expected_mode(px4.AUTOPILOT_ARDUPILOT, 'GUIDED') == 'GUIDED'


def test_both_autopilots_are_flight_controllers_but_the_adsb_receiver_is_not():
    assert drone_sdk._is_flight_controller_heartbeat(heartbeat(px4.AUTOPILOT_PX4))
    assert drone_sdk._is_flight_controller_heartbeat(heartbeat(px4.AUTOPILOT_ARDUPILOT))
    assert not drone_sdk._is_flight_controller_heartbeat(heartbeat(8, component=0))   # ADS-B
    assert not drone_sdk._is_flight_controller_heartbeat(heartbeat(px4.AUTOPILOT_PX4, system=0))


def test_mode_string_decodes_the_right_vocabulary_per_autopilot():
    assert drone_sdk._mode_string(heartbeat(px4.AUTOPILOT_PX4, px4_custom(4, 6))) == 'LAND'
    assert drone_sdk._mode_string(heartbeat(px4.AUTOPILOT_ARDUPILOT, 4, base_mode=1)) == 'GUIDED'   # ArduCopter 4


class FakeConn:
    target_system, target_component = 1, 1

    def __init__(self):
        self.sent = []
        outer = self

        class Mav:
            def command_long_send(self, *a):
                outer.sent.append(('long', a))

            def command_int_send(self, *a):
                outer.sent.append(('int', a))
        self.mav = Mav()


def test_set_mode_is_do_set_mode_with_px4_main_and_sub():
    c = FakeConn()
    assert px4.send_set_mode(c, 'LAND')
    kind, a = c.sent[0]
    assert kind == 'long' and a[2] == 176
    assert a[4:7] == (px4.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, 4, 6)   # custom flag, AUTO, LAND


def test_a_mode_px4_lacks_is_reported_not_sent():
    c = FakeConn()
    assert not px4.send_set_mode(c, 'ACRO_HELI')
    assert c.sent == []


def test_reposition_is_a_command_int_in_1e7_degrees_with_amsl_altitude():
    c = FakeConn()
    px4.send_reposition(c, 37.7749, -122.4194, 55.5)
    kind, a = c.sent[0]
    assert kind == 'int'
    assert a[3] == 192                                   # MAV_CMD_DO_REPOSITION
    assert a[6] == -1.0 and a[7] == 1                    # default speed, change to Hold
    assert math.isnan(a[9])                              # yaw unchanged
    assert (a[10], a[11], a[12]) == (377749000, -1224194000, 55.5)


def test_home_amsl_is_alt_minus_height_above_home():
    gpi = SimpleNamespace(alt=60500, relative_alt=10500)   # mm
    assert px4.home_amsl_m(gpi) == pytest.approx(50.0)


def test_ardupilot_only_calls_are_skipped_on_px4(monkeypatch):
    monkeypatch.setattr(drone_sdk, '_connect_locked', lambda: None)
    monkeypatch.setattr(drone_sdk, '_autopilot', px4.AUTOPILOT_PX4)
    assert drone_sdk.configure_failsafes() is False
    assert drone_sdk.configure_battery_monitoring() is False
    assert drone_sdk.battery_diagnostics() is None


def test_pymavlink_instance_cache_bug_does_not_escape(monkeypatch):
    """A type first cached without instance data, then received with one, used
    to raise TypeError out of recv_match and abort arm()."""
    from pymavlink import mavutil
    first = SimpleNamespace(_instance_field='id', id=None, _instances=None)
    again = SimpleNamespace(_instance_field='id', id=1, _instances=None)
    messages = {}
    mavutil.add_message(messages, 'BATTERY_STATUS', first)
    mavutil.add_message(messages, 'BATTERY_STATUS', again)   # raised TypeError before
    # The newest message is what a reader gets: the same object through our
    # workaround (pymavlink <= 2.4.49), a copy through pymavlink 2.4.50's own fix.
    assert messages['BATTERY_STATUS'].id == 1
