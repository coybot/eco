"""PX4 support for drone_sdk, as pure helpers (no I/O, testable without a flight controller).

drone_sdk speaks ArduPilot's vocabulary: GUIDED, BRAKE, LAND, RTL... PX4 has no
such modes and encodes its mode in HEARTBEAT.custom_mode as
(sub_mode << 24) | (main_mode << 16). This module translates both ways, so the
rest of the SDK keeps using the ArduPilot names ("logical" modes):

    GUIDED / BRAKE  <-> PX4 AUTO.LOITER (Hold). An onboard computer steers a PX4
                        aircraft in Hold with MAV_CMD_DO_REPOSITION.
    LAND, RTL       <-> AUTO.LAND, AUTO.RTL
    AUTO            <-> AUTO.MISSION
    STABILIZE       <-> STABILIZED
"""
import math

AUTOPILOT_ARDUPILOT = 3    # MAV_AUTOPILOT_ARDUPILOTMEGA
AUTOPILOT_PX4 = 12         # MAV_AUTOPILOT_PX4
FC_AUTOPILOTS = (AUTOPILOT_ARDUPILOT, AUTOPILOT_PX4)

MAV_MODE_FLAG_CUSTOM_MODE_ENABLED = 1
MAV_CMD_DO_REPOSITION = 192

# px4_custom_mode.h
_MAIN = {1: 'MANUAL', 2: 'ALT_HOLD', 3: 'POSHOLD', 4: 'AUTO', 5: 'ACRO',
         6: 'GUIDED',          # OFFBOARD: the companion is flying it
         7: 'STABILIZE', 8: 'RATTITUDE'}
_AUTO_SUB = {1: 'GUIDED',      # READY (armed, idle on the ground)
             2: 'GUIDED',      # TAKEOFF: coybot's own takeoff, ends in Hold
             3: 'GUIDED',      # LOITER / Hold
             4: 'AUTO',        # MISSION
             5: 'RTL',
             6: 'LAND',
             7: 'RTL',         # RTGS (return to ground station)
             8: 'GUIDED',      # FOLLOW_TARGET
             9: 'LAND'}        # PRECLAND

# logical mode (what drone_sdk asks for) -> (main_mode, sub_mode) to command
_COMMAND = {
    'GUIDED': (4, 3), 'BRAKE': (4, 3), 'LOITER': (4, 3),
    'LAND': (4, 6),
    'RTL': (4, 5), 'SMART_RTL': (4, 5),
    'AUTO': (4, 4),
    'STABILIZE': (7, 0), 'STABILIZED': (7, 0), 'ALT_HOLD': (2, 0),
    'POSHOLD': (3, 0), 'MANUAL': (1, 0),
}

# The logical mode the heartbeat will show once a request has taken effect.
_CONFIRMS_AS = {'BRAKE': 'GUIDED', 'LOITER': 'GUIDED', 'SMART_RTL': 'RTL',
                'STABILIZED': 'STABILIZE'}


def is_fc_autopilot(autopilot):
    return autopilot in FC_AUTOPILOTS


def px4_mode_name(custom_mode):
    """HEARTBEAT.custom_mode of a PX4 aircraft -> logical mode name."""
    main = (int(custom_mode) >> 16) & 0xFF
    sub = (int(custom_mode) >> 24) & 0xFF
    if main == 4:
        return _AUTO_SUB.get(sub, f'AUTO_{sub}')
    return _MAIN.get(main, f'MODE_{int(custom_mode)}')


def mode_name(autopilot, base_mode, custom_mode, ardupilot_name):
    """Logical mode name. `ardupilot_name` is pymavlink's decode for ArduPilot."""
    if autopilot == AUTOPILOT_PX4:
        return px4_mode_name(custom_mode)
    return ardupilot_name


def command_mode(mode):
    """Logical mode name -> (main_mode, sub_mode) for PX4, or None if PX4 has no such mode.
    Accepts the numeric 0 drone_sdk uses for ArduPilot's STABILIZE."""
    if mode == 0:
        mode = 'STABILIZE'
    return _COMMAND.get(str(mode).upper())


def expected_mode(autopilot, mode):
    """The logical name the heartbeat shows after `mode` is requested."""
    if mode == 0:
        mode = 'STABILIZE'
    if autopilot == AUTOPILOT_PX4:
        return _CONFIRMS_AS.get(str(mode).upper(), mode)
    return mode


def send_set_mode(conn, mode):
    """Request `mode` from a PX4 aircraft via MAV_CMD_DO_SET_MODE. Returns False
    if PX4 has no equivalent of it."""
    mm = command_mode(mode)
    if mm is None:
        return False
    conn.mav.command_long_send(
        conn.target_system, conn.target_component,
        176,  # MAV_CMD_DO_SET_MODE
        0, MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mm[0], mm[1], 0, 0, 0, 0)
    return True


def home_amsl_m(global_position_int):
    """Altitude (m, AMSL) of the PX4/ArduPilot home reference, from a
    GLOBAL_POSITION_INT: AMSL altitude minus altitude above home."""
    return (global_position_int.alt - global_position_int.relative_alt) / 1000.0


def send_reposition(conn, lat, lon, alt_amsl_m):
    """Fly a PX4 aircraft to a point (MAV_CMD_DO_REPOSITION, which also puts it in
    Hold so it stays there). PX4 reads the altitude as AMSL."""
    conn.mav.command_int_send(
        conn.target_system, conn.target_component,
        0,                       # MAV_FRAME_GLOBAL: lat/lon in 1e7 deg, z AMSL
        MAV_CMD_DO_REPOSITION, 0, 0,
        -1.0,                    # ground speed: the vehicle default
        1,                       # MAV_DO_REPOSITION_FLAGS_CHANGE_MODE
        0.0,                     # loiter radius: default
        math.nan,                # yaw: unchanged
        int(round(lat * 1e7)), int(round(lon * 1e7)), float(alt_amsl_m))
