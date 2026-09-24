"""
MAVLink connection primitives shared by all MAVLink-based vehicles.

Provides thread-safe send/receive helpers, connection management, and
read-only telemetry queries (position, attitude, battery, armed status).
Vehicle-specific commands (arm, takeoff, drive…) live in the concrete
vehicle module (quadcopter.py, etc.).
"""

import time
import math
import logging
import sys
import threading
from pathlib import Path
from pymavlink import mavutil

# DRONE_DIR is the common/ directory (parent of vehicles/)
DRONE_DIR = Path(__file__).parent.parent.absolute()

_logger = logging.getLogger('drone_sdk')
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter('%(message)s'))
    _logger.addHandler(_h)


def _log(msg):
    print(msg)
    _logger.info(msg)


# =============================================================================
# Safety constants
# =============================================================================
MAX_VELOCITY = 5.0    # m/s (any axis)
MAX_ALTITUDE = 20.0   # m AGL
MIN_ALTITUDE = 0.5    # m
MAX_YAW_RATE = 45.0   # deg/s


def _clamp(value, min_val, max_val, name="value"):
    if value < min_val:
        print(f"SAFETY: {name}={value} clamped to minimum {min_val}")
        return min_val
    if value > max_val:
        print(f"SAFETY: {name}={value} clamped to maximum {max_val}")
        return max_val
    return value


# =============================================================================
# Connection state
# =============================================================================
_master = None
_mavlink_lock = threading.RLock()


def _connect():
    """Return MAVLink connection, creating it if needed. Caller must hold _mavlink_lock."""
    global _master
    if _master is None:
        import yaml
        import os
        config_path = DRONE_DIR / 'config.yaml'
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        port = config.get('serial_port', '/dev/ttyACM0')
        baud = config.get('baud_rate', 115200)

        if not os.path.exists(port):
            raise ConnectionError(
                f"Flight controller not found at {port}. "
                f"Check USB connection and serial_port in config.yaml."
            )

        print(f"Connecting to {port} at {baud}...")
        _master = mavutil.mavlink_connection(port, baud=baud, source_system=255)

        deadline = time.time() + 10.0
        fc_heartbeat = None
        while time.time() < deadline:
            msg = _master.recv_match(type='HEARTBEAT', blocking=True, timeout=1.0)
            if msg and _is_flight_controller_heartbeat(msg):
                fc_heartbeat = msg
                break

        if fc_heartbeat is None:
            try:
                _master.close()
            except Exception:
                pass
            _master = None
            raise ConnectionError(
                f"Flight controller not responding on {port} — "
                f"no ArduPilot autopilot heartbeat in 10 s."
            )

        _master.target_system = fc_heartbeat.get_srcSystem()
        _master.target_component = fc_heartbeat.get_srcComponent()

        print(f"Connected to flight controller "
              f"(system {_master.target_system}, comp {_master.target_component})")
    return _master


def disconnect():
    """Close MAVLink connection and reset state. Thread-safe."""
    global _master
    with _mavlink_lock:
        if _master is not None:
            try:
                if hasattr(_master, 'sysid_state'):
                    _master.sysid_state.clear()
                if hasattr(_master, 'port') and _master.port:
                    _master.port.close()
                _master.close()
            except Exception:
                pass
            _master = None
            print("MAVLink connection closed")


# =============================================================================
# Thread-safe MAVLink I/O primitives
# =============================================================================

def _is_flight_controller_heartbeat(msg):
    """Return whether a heartbeat identifies the ArduPilot autopilot component."""
    return (
        msg.get_srcSystem() != 0
        and msg.autopilot == mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA
        and msg.get_srcComponent()
        == getattr(mavutil.mavlink, 'MAV_COMP_ID_AUTOPILOT1', 1)
    )


def _is_target_heartbeat(conn, msg):
    return (
        msg.get_srcSystem() == conn.target_system
        and msg.get_srcComponent() == conn.target_component
    )


def _mav_send(send_func):
    with _mavlink_lock:
        m = _connect()
        return send_func(m)


def _mav_recv(msg_type, timeout=1.0):
    with _mavlink_lock:
        m = _connect()
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = m.recv_match(
                type=msg_type,
                blocking=True,
                timeout=min(0.5, max(0.0, deadline - time.time())),
            )
            if msg is None:
                continue
            if msg_type != 'HEARTBEAT' or _is_target_heartbeat(m, msg):
                return msg
        return None


def _mav_recv_any(timeout=0.5):
    with _mavlink_lock:
        m = _connect()
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = m.recv_match(
                blocking=True,
                timeout=min(0.5, max(0.0, deadline - time.time())),
            )
            if msg is None:
                continue
            if msg.get_type() != 'HEARTBEAT' or _is_target_heartbeat(m, msg):
                return msg
        return None


def _mav_command(command_func, ack_command_id, timeout=3):
    """Send a command and wait for ACK atomically. Returns (success, result_code)."""
    with _mavlink_lock:
        m = _connect()
        command_func(m)
        start = time.time()
        while time.time() - start < timeout:
            msg = m.recv_match(type='COMMAND_ACK', blocking=True, timeout=0.3)
            if msg and msg.command == ack_command_id:
                return msg.result == 0, msg.result
        return False, None


def _wait_for_ack(command_id, timeout=5):
    start = time.time()
    while time.time() - start < timeout:
        try:
            msg = _mav_recv('COMMAND_ACK', timeout=0.5)
            if msg and msg.command == command_id:
                result_names = {0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED",
                                3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS"}
                _log(f"ACK: command={msg.command}, result={result_names.get(msg.result, msg.result)}")
                return msg.result == 0
        except TypeError as e:
            _log(f"Warning: pymavlink parse error: {e}")
    _log(f"ACK timeout for command {command_id}")
    return False


def _drain_statustext(timeout=2.0, print_msgs=True):
    start = time.time()
    seen = set()
    while time.time() - start < timeout:
        try:
            msg = _mav_recv('STATUSTEXT', timeout=0.1)
            if msg:
                text = getattr(msg, "text", None)
                if text and text not in seen:
                    seen.add(text)
                    if print_msgs:
                        print(f"STATUSTEXT: {text}")
        except Exception:
            break
    return list(seen)


def _wait_for_mode(target_mode, timeout=5):
    start = time.time()
    while time.time() - start < timeout:
        msg = _mav_recv('HEARTBEAT', timeout=0.5)
        if msg:
            try:
                current = mavutil.mode_string_v10(msg)
                if current == target_mode:
                    print(f"Mode confirmed: {current}")
                    return True
                print(f"Mode: {current} (waiting for {target_mode})")
            except Exception:
                pass
    print(f"Mode change to {target_mode} timed out!")
    return False


def _wait_for_disarm(timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        try:
            msg = _mav_recv('HEARTBEAT', timeout=0.5)
            if msg:
                armed = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
                if not armed:
                    print("Disarmed (landing complete)")
                    return True
        except Exception:
            pass
    print("Landing timeout - still armed")
    return False


def _set_param(name, value):
    _mav_send(lambda m: m.mav.param_set_send(
        m.target_system, m.target_component,
        name.encode('utf-8'), float(value),
        mavutil.mavlink.MAV_PARAM_TYPE_REAL32
    ))
    start = time.time()
    while time.time() - start < 2:
        msg = _mav_recv('PARAM_VALUE', timeout=0.5)
        if msg and msg.param_id.rstrip('\x00') == name:
            return True
    return False


# =============================================================================
# Shared telemetry (works for any MAVLink vehicle)
# =============================================================================

def wait(seconds):
    print(f"Waiting {seconds}s...")
    time.sleep(seconds)


def is_armed():
    """Check armed status from a fresh heartbeat."""
    with _mavlink_lock:
        try:
            conn = _connect()
        except Exception:
            return False

        deadline = time.time() + 3.0
        while time.time() < deadline:
            if conn.recv_match(type='HEARTBEAT', blocking=False) is None:
                break

        while time.time() < deadline:
            msg = conn.recv_match(
                type='HEARTBEAT',
                blocking=True,
                timeout=min(0.5, max(0.0, deadline - time.time())),
            )
            if msg and _is_target_heartbeat(conn, msg):
                return (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
        return False


def get_position():
    """Return (lat, lon, alt_m_relative) from GPS."""
    _mav_send(lambda m: m.mav.request_data_stream_send(
        1, 1, mavutil.mavlink.MAV_DATA_STREAM_POSITION, 4, 1))
    start = time.time()
    while time.time() - start < 3:
        msg = _mav_recv('GLOBAL_POSITION_INT', timeout=0.5)
        if msg:
            return (msg.lat / 1e7, msg.lon / 1e7, msg.relative_alt / 1000)
    return (0, 0, 0)


def get_attitude():
    """Return (roll, pitch, yaw) in degrees."""
    _mav_send(lambda m: m.mav.request_data_stream_send(
        1, 1, mavutil.mavlink.MAV_DATA_STREAM_EXTRA1, 4, 1))
    start = time.time()
    while time.time() - start < 3:
        msg = _mav_recv('ATTITUDE', timeout=0.5)
        if msg:
            return (math.degrees(msg.roll), math.degrees(msg.pitch), math.degrees(msg.yaw))
    return (0, 0, 0)


def get_battery():
    """Return {'voltage': V, 'remaining': %, 'current': A_or_None}."""
    _mav_send(lambda m: m.mav.request_data_stream_send(
        1, 1, mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS, 4, 1))
    start = time.time()
    while time.time() - start < 3:
        msg = _mav_recv('SYS_STATUS', timeout=0.5)
        if msg:
            return {
                'voltage': msg.voltage_battery / 1000.0,
                'remaining': msg.battery_remaining,
                'current': msg.current_battery / 100.0 if msg.current_battery != -1 else None
            }
    return {'voltage': 0, 'remaining': -1, 'current': None}


def get_flight_mode():
    """Return current flight mode string."""
    start = time.time()
    while time.time() - start < 3:
        msg = _mav_recv('HEARTBEAT', timeout=0.5)
        if msg:
            try:
                return mavutil.mode_string_v10(msg)
            except Exception:
                return f"MODE_{msg.custom_mode}"
    return "UNKNOWN"


def get_telemetry():
    """Return combined telemetry dict."""
    _mav_send(lambda m: m.mav.request_data_stream_send(
        1, 1, mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1))

    telemetry = {'position': None, 'attitude': None, 'battery': None, 'armed': None, 'mode': None}
    start = time.time()
    collected = set()

    while time.time() - start < 2 and len(collected) < 4:
        msg = _mav_recv_any(timeout=0.3)
        if msg is None:
            continue
        t = msg.get_type()
        if t == 'GLOBAL_POSITION_INT' and 'position' not in collected:
            telemetry['position'] = {
                'latitude': msg.lat / 1e7,
                'longitude': msg.lon / 1e7,
                'altitude': msg.alt / 1000.0,
            }
            collected.add('position')
        elif t == 'ATTITUDE' and 'attitude' not in collected:
            telemetry['attitude'] = {
                'roll': math.degrees(msg.roll),
                'pitch': math.degrees(msg.pitch),
                'yaw': math.degrees(msg.yaw),
            }
            collected.add('attitude')
        elif t == 'SYS_STATUS' and 'battery' not in collected:
            telemetry['battery'] = msg.battery_remaining
            collected.add('battery')
        elif t == 'HEARTBEAT' and 'armed' not in collected:
            telemetry['armed'] = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
            try:
                telemetry['mode'] = mavutil.mode_string_v10(msg)
            except Exception:
                telemetry['mode'] = f"MODE_{msg.custom_mode}"
            collected.add('armed')

    return telemetry
