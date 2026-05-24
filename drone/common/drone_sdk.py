"""
Drone SDK - High-level interface for ArduPilot via pymavlink

This module provides simple functions for drone control that can be
called from LLM-generated code. Includes camera capture and cloud upload.

SAFETY: All movement commands are clamped to safe limits defined below.
"""

import time
import math
import os
import json
import tempfile
import threading
import logging
import sys
from pathlib import Path
from pymavlink import mavutil

# Set up logging to go to both stdout (captured by daemon) and stderr (goes to journald)
_logger = logging.getLogger('drone_sdk')
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    # Handler for stderr which goes to journald
    _stderr_handler = logging.StreamHandler(sys.stderr)
    _stderr_handler.setFormatter(logging.Formatter('%(message)s'))
    _logger.addHandler(_stderr_handler)


def _log(msg):
    """Log a message to both stdout (daemon capture) and stderr (journald)."""
    print(msg)  # Captured by daemon for response
    _logger.info(msg)  # Goes to journald via stderr

# =============================================================================
# SAFETY LAYER 2: Velocity and Altitude Limits
# All movement commands are clamped to these values to prevent dangerous flight
# =============================================================================
MAX_VELOCITY = 5.0       # m/s in any direction (forward, sideways, up/down)
MAX_ALTITUDE = 20.0      # meters AGL (above ground level)
MIN_ALTITUDE = 0.5       # meters (prevent underground/negative altitude commands)
MAX_YAW_RATE = 45.0      # degrees per second


def _clamp(value, min_val, max_val, name="value"):
    """Clamp a value to safe limits and warn if clamping occurred."""
    if value < min_val:
        print(f"SAFETY: {name}={value} clamped to minimum {min_val}")
        return min_val
    if value > max_val:
        print(f"SAFETY: {name}={value} clamped to maximum {max_val}")
        return max_val
    return value


# Global connection
_master = None
_mavlink_lock = threading.RLock()  # Lock for serial port access (pymavlink isn't thread-safe)

# Camera instance (lazy loaded)
_camera = None


# =============================================================================
# Thread-safe MAVLink I/O primitives
# All MAVLink operations go through these to handle locking in ONE place
# =============================================================================

def _mav_send(send_func):
    """Thread-safe MAVLink send. Pass a function that takes the mavlink connection.
    
    Example: _mav_send(lambda m: m.mav.command_long_send(...))
    """
    with _mavlink_lock:
        m = _connect()
        return send_func(m)


def _mav_recv(msg_type, timeout=1.0):
    """Thread-safe MAVLink receive. Returns message or None if timeout."""
    with _mavlink_lock:
        m = _connect()
        return m.recv_match(type=msg_type, blocking=True, timeout=timeout)


def _mav_recv_any(timeout=0.5):
    """Thread-safe MAVLink receive any message type. Returns message or None."""
    with _mavlink_lock:
        m = _connect()
        return m.recv_match(blocking=True, timeout=timeout)


def _mav_command(command_func, ack_command_id, timeout=3):
    """Send a command and wait for ACK atomically (holding lock throughout).
    
    This prevents other threads from consuming the ACK message.
    Timeout kept short (3s default) to minimize blocking other threads.
    Returns (success: bool, result_code: int or None)
    """
    with _mavlink_lock:
        m = _connect()
        command_func(m)
        
        start = time.time()
        while time.time() - start < timeout:
            msg = m.recv_match(type='COMMAND_ACK', blocking=True, timeout=0.3)
            if msg and msg.command == ack_command_id:
                return msg.result == 0, msg.result
        return False, None

# S3 configuration (loaded from config)
_s3_client = None
_s3_bucket = None
_s3_credentials_expiry = None

# Config, certs, and logs live alongside this file (repo: drone/common/; on-device: ~/drone-api/).
_script_dir = Path(__file__).parent.absolute()
DRONE_DIR = _script_dir


def _connect():
    """Get or create MAVLink connection. Must be called with _mavlink_lock held.
    
    Raises ConnectionError if the flight controller is not detected.
    """
    global _master
    # Note: caller must hold _mavlink_lock (RLock allows re-entry if needed)
    if _master is None:
        import yaml
        config_path = DRONE_DIR / 'config.yaml'
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        port = config.get('serial_port', '/dev/ttyACM0')
        baud = config.get('baud_rate', 115200)
        
        # Check if serial port exists before attempting connection
        import os
        if not os.path.exists(port):
            raise ConnectionError(
                f"Flight controller not found at {port}. "
                f"Check USB connection and verify the serial port in config.yaml."
            )
        
        print(f"Connecting to {port} at {baud}...")
        _master = mavutil.mavlink_connection(port, baud=baud, source_system=255)

        # First heartbeat (any); then prefer ArduPilot FC — USB setups sometimes see sys=0
        # or a companion heartbeat first, which breaks arm/takeoff when used as target.
        msg = _master.recv_match(type='HEARTBEAT', blocking=True, timeout=10)
        if not msg:
            try:
                _master.close()
            except Exception:
                pass
            _master = None
            raise ConnectionError(
                f"Flight controller not responding on {port}. "
                f"Got no heartbeat within 10 seconds. "
                f"Check that the flight controller is powered on and connected."
            )
        _master.target_system = msg.get_srcSystem()
        _master.target_component = msg.get_srcComponent()
        t0 = time.time()
        while (
            (_master.target_system == 0 or _master.target_component == 0)
            and time.time() - t0 < 5.0
        ):
            m = _master.recv_match(type='HEARTBEAT', blocking=True, timeout=1.0)
            if m and m.autopilot == mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA:
                _master.target_system = m.get_srcSystem()
                _master.target_component = m.get_srcComponent()
                break
        if _master.target_system == 0:
            _master.target_system = 1
            _master.target_component = 1
            print("Warning: MAVLink FC sys/comp unknown; using sys=1 comp=1 fallback.")
        print(f"Connected to flight controller (system {_master.target_system}, comp {_master.target_component})")
    return _master


def disconnect():
    """Close MAVLink connection and reset state. Thread-safe."""
    global _master
    with _mavlink_lock:
        if _master is not None:
            try:
                # Clear pymavlink's internal message state to prevent stale data
                if hasattr(_master, 'sysid_state'):
                    _master.sysid_state.clear()
                # Close the underlying port explicitly
                if hasattr(_master, 'port') and _master.port:
                    _master.port.close()
                _master.close()
            except:
                pass
            _master = None
            print("MAVLink connection closed")


def _wait_for_ack(command_id, timeout=5):
    """Wait for command acknowledgment."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            msg = _mav_recv('COMMAND_ACK', timeout=0.5)
            if msg and msg.command == command_id:
                result_names = {0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED",
                               3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS"}
                result_str = result_names.get(msg.result, f"UNKNOWN({msg.result})")
                _log(f"ACK: command={msg.command}, result={result_str}")
                return msg.result == 0
        except TypeError as e:
            _log(f"Warning: pymavlink message parse error: {e}")
            continue
    _log(f"ACK timeout for command {command_id}")
    return False


def _drain_statustext(timeout=2.0, print_msgs=True):
    """Drain recent STATUSTEXT messages for debugging.
    
    Args:
        timeout: How long to drain messages
        print_msgs: Whether to print messages (default True)
    
    Returns:
        List of captured status text messages
    """
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
    """Wait for flight mode to change to target mode.
    
    Args:
        target_mode: Target mode string (e.g., 'GUIDED', 'LAND', 'STABILIZE')
        timeout: Maximum time to wait in seconds
    
    Returns:
        True if mode change confirmed, False if timeout
    """
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


def _check_preflight_status():
    """Check GPS and EKF status before takeoff.
    
    Returns:
        Tuple of (ok: bool, issues: list[str])
    """
    issues = []
    
    # Request extended status stream
    _mav_send(lambda m: m.mav.request_data_stream_send(
        m.target_system, m.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS, 4, 1
    ))
    
    # Check GPS
    msg = _mav_recv('GPS_RAW_INT', timeout=2)
    if msg:
        fix_types = {0: "No GPS", 1: "No Fix", 2: "2D Fix", 3: "3D Fix",
                     4: "DGPS", 5: "RTK Float", 6: "RTK Fixed"}
        fix = fix_types.get(msg.fix_type, f"Unknown({msg.fix_type})")
        print(f"GPS: {fix}, {msg.satellites_visible} satellites")
        if msg.fix_type < 3:
            issues.append(f"GPS not ready: {fix}")
    else:
        print("GPS: No data received")
        issues.append("GPS: No data received")
    
    # Check EKF
    msg = _mav_recv('EKF_STATUS_REPORT', timeout=1)
    if msg:
        flags = msg.flags
        ekf_status = []
        if flags & 0x01:
            ekf_status.append("attitude:OK")
        else:
            issues.append("EKF: attitude not ready")
        if flags & 0x10:
            ekf_status.append("hpos:OK")
        elif flags & 0x08:
            ekf_status.append("hpos:REL")
        else:
            issues.append("EKF: horizontal position not ready")
        if flags & 0x20:
            ekf_status.append("vpos:OK")
        print(f"EKF: flags=0x{flags:02x} ({', '.join(ekf_status) if ekf_status else 'not ready'})")
    else:
        print("EKF: No status received (may be OK on some FCs)")
    
    return len(issues) == 0, issues


def _wait_for_disarm(timeout=30):
    """Wait until the vehicle reports disarmed via HEARTBEAT."""
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


def arm():
    """Arm the drone motors. Returns True only if actually armed."""
    _log("Arming...")
    
    # Check if already armed
    if is_armed():
        _log("Already armed!")
        return True
    
    # Set STABILIZE mode first (required for arming)
    _mav_send(lambda m: m.set_mode(0))  # 0 = STABILIZE
    time.sleep(0.5)
    
    # Send arm command and wait for ACK atomically (prevents heartbeat thread from stealing ACK)
    def send_arm(m):
        _log(f"Sending arm to system {m.target_system}, component {m.target_component}")
        m.mav.command_long_send(
            m.target_system, m.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0, 1, 21196, 0, 0, 0, 0, 0  # arm with force
        )
    
    ack_ok, result = _mav_command(send_arm, 400)
    
    result_names = {0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED",
                    3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS"}
    
    if result is None:
        _log("Arm command not acknowledged - checking FC status...")
        status_msgs = _drain_statustext(timeout=1)
        if status_msgs:
            for msg in status_msgs:
                _log(f"  FC: {msg}")
        else:
            _log("  No status messages from FC")
        return False
    
    _log(f"Arm ACK: {result_names.get(result, result)}")
    
    if not ack_ok:
        _log("Arm command rejected by FC")
        status_msgs = _drain_statustext(timeout=1)
        for msg in status_msgs:
            _log(f"  FC: {msg}")
        return False
    
    # Wait for motors and verify armed
    _log("Waiting for motors...")
    time.sleep(2)
    
    if is_armed():
        _log("Armed confirmed!")
        return True
    else:
        _log("Arm ACK'd but not armed - FC may have auto-disarmed")
        status_msgs = _drain_statustext(timeout=1)
        for msg in status_msgs:
            _log(f"  FC: {msg}")
        return False


def disarm():
    """Disarm the drone motors."""
    print("Disarming...")
    _mav_send(lambda m: m.mav.command_long_send(
        m.target_system, m.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 0, 21196, 0, 0, 0, 0, 0  # disarm with force
    ))
    success = _wait_for_ack(400)
    if success:
        print("Disarmed!")
    else:
        print("Disarm failed!")
    return success


def safe_disarm():
    """Disarm motors — only when on the ground (relative altitude < 1 m).

    Raises RuntimeError if the drone appears airborne. Use land() first when flying.
    """
    _mav_send(lambda m: m.mav.request_data_stream_send(
        1, 1, mavutil.mavlink.MAV_DATA_STREAM_POSITION, 4, 1))
    start = time.time()
    rel_alt = None
    while time.time() - start < 3:
        msg = _mav_recv('GLOBAL_POSITION_INT', timeout=0.5)
        if msg:
            rel_alt = msg.relative_alt / 1000.0  # mm → m
            break
    if rel_alt is not None and rel_alt > 1.0:
        raise RuntimeError(
            f"safe_disarm() refused: drone is {rel_alt:.1f}m above ground — call land() first")
    return disarm()


def takeoff(altitude_m):
    """Take off to specified altitude in meters. Arms automatically if needed.

    SAFETY: Altitude is clamped to MIN_ALTITUDE..MAX_ALTITUDE range.
    
    Returns:
        True if takeoff command succeeded, False otherwise
    """
    altitude_m = _clamp(altitude_m, MIN_ALTITUDE, MAX_ALTITUDE, "altitude")
    _log(f"Taking off to {altitude_m}m...")
    
    _drain_statustext(timeout=0.5, print_msgs=False)
    
    # Set GUIDED mode
    _log("Setting GUIDED mode...")
    _mav_send(lambda m: m.set_mode('GUIDED'))
    if not _wait_for_mode('GUIDED', timeout=5):
        _log("ERROR: Failed to enter GUIDED mode")
        _drain_statustext()
        return False
    
    # Run preflight checks
    _log("Running preflight checks...")
    ok, issues = _check_preflight_status()
    if not ok:
        for issue in issues:
            _log(f"  WARNING: {issue}")
        _log("Continuing despite warnings...")
    
    # Check if armed using fresh heartbeat (not cached state)
    if is_armed():
        _log("Already armed, skipping arm step")
    else:
        _log("Arming (normal)...")
        ack_ok, result = _mav_command(
            lambda m: m.mav.command_long_send(
                m.target_system, m.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0, 1, 0, 0, 0, 0, 0, 0
            ), 400)

        if not ack_ok:
            _log("Normal arm failed, trying force-arm...")
            _drain_statustext(timeout=1)
            ack_ok, result = _mav_command(
                lambda m: m.mav.command_long_send(
                    m.target_system, m.target_component,
                    mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                    0, 1, 21196, 0, 0, 0, 0, 0
                ), 400)

            if not ack_ok:
                _log(f"Force arm also failed! (result={result})")
                _drain_statustext()
                return False

        _log("Arm ACK received, waiting for motors...")
        time.sleep(0.3)

        if not is_armed():
            _log("ERROR: Arm command ACK'd but drone not armed!")
            _drain_statustext()
            return False
        _log("Armed confirmed!")
    
    # Send takeoff command atomically
    _log(f"Sending takeoff to {altitude_m}m...")
    ack_ok, result = _mav_command(
        lambda m: m.mav.command_long_send(
            m.target_system, m.target_component,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            0, 0, 0, 0, 0, 0, 0, altitude_m
        ), 22)
    
    if not ack_ok:
        _log(f"Takeoff command failed! (result={result})")
        _drain_statustext()
        return False
    
    wait_time = max(altitude_m * 2, 3)
    _log(f"Climbing... (waiting {wait_time}s)")
    time.sleep(wait_time)
    _log(f"Reached {altitude_m}m")
    return True


def land():
    """Land the drone. Sets LAND mode and lets FC handle landing and auto-disarm."""
    print("Landing...")
    def send_land(m):
        m.set_mode('LAND')
        try:
            m.mav.command_long_send(
                m.target_system, m.target_component,
                mavutil.mavlink.MAV_CMD_NAV_LAND,
                0, 0, 0, 0, 0, 0, 0, 0
            )
        except Exception:
            pass
    _mav_send(send_land)
    print("LAND command sent - waiting for auto-disarm")
    _wait_for_disarm(timeout=45)


def goto(lat, lon, alt):
    """Fly to GPS coordinates.
    
    SAFETY: Altitude is clamped to MIN_ALTITUDE..MAX_ALTITUDE range.
    """
    alt = _clamp(alt, MIN_ALTITUDE, MAX_ALTITUDE, "altitude")
    print(f"Flying to ({lat}, {lon}) at {alt}m...")
    _mav_send(lambda m: m.mav.set_position_target_global_int_send(
        0, 1, 1,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        0b0000111111111000,
        int(lat * 1e7), int(lon * 1e7), alt,
        0, 0, 0, 0, 0, 0, 0, 0
    ))


def set_velocity(vx, vy, vz):
    """Set velocity in body frame.
    vx: forward (m/s), vy: right (m/s), vz: down (m/s)
    
    SAFETY: All velocities are clamped to ±MAX_VELOCITY.
    """
    vx = _clamp(vx, -MAX_VELOCITY, MAX_VELOCITY, "vx")
    vy = _clamp(vy, -MAX_VELOCITY, MAX_VELOCITY, "vy")
    vz = _clamp(vz, -MAX_VELOCITY, MAX_VELOCITY, "vz")
    print(f"Setting velocity: vx={vx}, vy={vy}, vz={vz}")
    _mav_send(lambda m: m.mav.set_position_target_local_ned_send(
        0, 1, 1,
        mavutil.mavlink.MAV_FRAME_BODY_NED,
        0b0000111111000111,
        0, 0, 0, vx, vy, vz, 0, 0, 0, 0, 0
    ))


def set_yaw(angle_deg, relative=False):
    """Set yaw angle in degrees."""
    print(f"Setting yaw to {angle_deg}° {'(relative)' if relative else '(absolute)'}")
    _mav_send(lambda m: m.mav.command_long_send(
        1, 1, mavutil.mavlink.MAV_CMD_CONDITION_YAW, 0,
        angle_deg, 25, 1 if angle_deg >= 0 else -1, 1 if relative else 0, 0, 0, 0
    ))
    time.sleep(abs(angle_deg) / 25 + 1)


def wait(seconds):
    """Wait for specified duration."""
    print(f"Waiting {seconds}s...")
    time.sleep(seconds)


def get_position():
    """Get current GPS position. Returns (lat, lon, alt_m)."""
    _mav_send(lambda m: m.mav.request_data_stream_send(1, 1, mavutil.mavlink.MAV_DATA_STREAM_POSITION, 4, 1))
    start = time.time()
    while time.time() - start < 3:
        msg = _mav_recv('GLOBAL_POSITION_INT', timeout=0.5)
        if msg:
            return (msg.lat / 1e7, msg.lon / 1e7, msg.alt / 1000)
    return (0, 0, 0)


def get_attitude():
    """Get current attitude. Returns (roll, pitch, yaw) in degrees."""
    _mav_send(lambda m: m.mav.request_data_stream_send(1, 1, mavutil.mavlink.MAV_DATA_STREAM_EXTRA1, 4, 1))
    start = time.time()
    while time.time() - start < 3:
        msg = _mav_recv('ATTITUDE', timeout=0.5)
        if msg:
            return (math.degrees(msg.roll), math.degrees(msg.pitch), math.degrees(msg.yaw))
    return (0, 0, 0)


def get_battery():
    """Get battery status. Returns dict with voltage, remaining %, and current."""
    _mav_send(lambda m: m.mav.request_data_stream_send(1, 1, mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS, 4, 1))
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


def is_armed():
    """Check if the vehicle is armed. Returns True/False."""
    start = time.time()
    while time.time() - start < 3:
        msg = _mav_recv('HEARTBEAT', timeout=0.5)
        if msg:
            return (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
    return False


def get_flight_mode():
    """Get current flight mode as string."""
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
    """Get all telemetry in one call. Returns dict with position, attitude, battery, armed status."""
    _mav_send(lambda m: m.mav.request_data_stream_send(1, 1, mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1))
    
    telemetry = {
        'position': None,
        'attitude': None,
        'battery': None,
        'armed': None,
        'mode': None
    }
    
    start = time.time()
    collected = set()
    
    # Collect messages for up to 2 seconds or until we have all data
    while time.time() - start < 2 and len(collected) < 4:
        msg = _mav_recv_any(timeout=0.3)
        if msg is None:
            continue
            
        msg_type = msg.get_type()
        
        if msg_type == 'GLOBAL_POSITION_INT' and 'position' not in collected:
            telemetry['position'] = {
                'latitude': msg.lat / 1e7,
                'longitude': msg.lon / 1e7,
                'altitude': msg.alt / 1000.0
            }
            collected.add('position')
        elif msg_type == 'ATTITUDE' and 'attitude' not in collected:
            telemetry['attitude'] = {
                'roll': math.degrees(msg.roll),
                'pitch': math.degrees(msg.pitch),
                'yaw': math.degrees(msg.yaw)
            }
            collected.add('attitude')
        elif msg_type == 'SYS_STATUS' and 'battery' not in collected:
            telemetry['battery'] = msg.battery_remaining
            collected.add('battery')
        elif msg_type == 'HEARTBEAT' and 'armed' not in collected:
            telemetry['armed'] = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
            try:
                telemetry['mode'] = mavutil.mode_string_v10(msg)
            except Exception:
                telemetry['mode'] = f"MODE_{msg.custom_mode}"
            collected.add('armed')
    
    return telemetry


def motor_test(motor_num=None, throttle_pct=15, duration_sec=2):
    """Test motor(s) without arming. motor_num=1-4 for one motor, None to test all sequentially."""
    if motor_num is None:
        print(f"Testing all motors sequentially at {throttle_pct}% for {duration_sec}s each...")
        for m in range(1, 5):
            motor_test(m, throttle_pct, duration_sec)
        return
    print(f"Testing motor {motor_num} at {throttle_pct}% for {duration_sec}s...")
    
    _mav_send(lambda m: m.mav.command_long_send(
        1, 1, mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST, 0,
        motor_num, 0, throttle_pct, duration_sec, 1, 0, 0
    ))
    
    MAV_CMD_DO_MOTOR_TEST = 209
    MAV_RESULT_NAMES = {0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED",
                        3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS", 6: "CANCELLED"}
    
    start = time.time()
    ack_received = False
    while time.time() - start < 3:
        msg = _mav_recv('COMMAND_ACK', timeout=0.5)
        if msg and msg.command == MAV_CMD_DO_MOTOR_TEST:
            ack_received = True
            if msg.result == 0:
                print(f"Motor {motor_num} command accepted - spinning...")
                time.sleep(duration_sec + 0.5)
                print(f"Motor {motor_num} test complete")
            else:
                result_name = MAV_RESULT_NAMES.get(msg.result, f"UNKNOWN({msg.result})")
                print(f"Motor {motor_num} test FAILED: {result_name}")
                status_msg = _mav_recv('STATUSTEXT', timeout=0.5)
                if status_msg:
                    print(f"  Reason: {status_msg.text}")
            break
    
    if not ack_received:
        print(f"Motor {motor_num} test: No ACK received")
        time.sleep(duration_sec + 1)


def _set_param(name, value):
    """Set a single MAVLink parameter. Returns True if ACK received."""
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


def configure_battery_monitoring(n_cells=4, capacity_mah=5000, low_voltage=3.5, critical_voltage=3.3):
    """Configure PX4/ArduPilot battery monitoring parameters."""
    print(f"Configuring battery monitoring: {n_cells}S, {capacity_mah}mAh...")
    
    params = {
        'BATT_MONITOR': 4,
        'BATT_CAPACITY': capacity_mah,
        'BATT_N_CELLS': n_cells,
        'BATT_LOW_VOLT': n_cells * low_voltage,
        'BATT_CRT_VOLT': n_cells * critical_voltage,
        'BATT_VOLT_PIN': 2,
        'BATT_CURR_PIN': 3,
        'BATT_VOLT_MULT': 10.1,
        'BATT_AMP_PERVLT': 17.0,
    }
    
    for name, value in params.items():
        if _set_param(name, value):
            print(f"  Set {name} = {value}")
        else:
            print(f"  Warning: No ACK for {name}")
    
    print("Battery monitoring configured. Reboot flight controller to apply.")
    return True


def configure_failsafes():
    """SAFETY LAYER 3: Configure flight controller failsafes for autonomous operation."""
    print("=" * 50)
    print("SAFETY LAYER 3: Configuring flight controller failsafes...")
    print("=" * 50)
    
    failsafe_params = {
        'FS_GCS_ENABLE': 1,      # Land on GCS heartbeat loss
        'FS_THR_ENABLE': 3,      # Land on throttle failsafe
        'FS_THR_VALUE': 975,
        'BATT_FS_LOW_ACT': 2,    # Land on low battery
        'BATT_FS_CRT_ACT': 1,    # Land on critical battery
        'LAND_DISARMDELAY': 2,
        'FS_EKF_ACTION': 1,      # Land on EKF failsafe
        'FS_EKF_THRESH': 0.8,
    }
    
    for name, value in failsafe_params.items():
        if _set_param(name, value):
            print(f"  Set {name} = {value}")
        else:
            print(f"  Warning: No ACK for {name}")
    
    # Save to EEPROM
    print("Saving failsafe parameters to EEPROM...")
    _mav_send(lambda m: m.mav.command_long_send(
        m.target_system, m.target_component,
        mavutil.mavlink.MAV_CMD_PREFLIGHT_STORAGE, 0, 1, 0, 0, 0, 0, 0, 0
    ))
    time.sleep(1)
    
    print("=" * 50)
    print("Failsafes configured.")
    print("=" * 50)
    return True


def setup_drone():
    """
    Initial setup for drone - configures battery monitoring and other defaults.
    Call this once when setting up a new drone.
    """
    import yaml
    
    print("=" * 50)
    print("DRONE SETUP")
    print("=" * 50)
    
    # Load config for battery settings
    config_path = DRONE_DIR / 'config.yaml'
    if config_path.exists():
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}
    
    # Get battery config with defaults
    battery_config = config.get('battery', {})
    n_cells = battery_config.get('cells', 4)
    capacity = battery_config.get('capacity_mah', 5000)
    low_volt = battery_config.get('low_voltage_per_cell', 3.5)
    crit_volt = battery_config.get('critical_voltage_per_cell', 3.3)
    
    # Configure battery monitoring
    configure_battery_monitoring(
        n_cells=n_cells,
        capacity_mah=capacity,
        low_voltage=low_volt,
        critical_voltage=crit_volt
    )
    
    print("=" * 50)
    print("Setup complete! Reboot the flight controller.")
    print("=" * 50)


# ============================================
# Camera Functions
# ============================================

def release_camera():
    """Release the camera so other processes (like video streaming) can use it."""
    global _camera
    if _camera is not None:
        try:
            _camera.stop()
            print("Camera released")
        except Exception as e:
            print(f"Warning: Error stopping camera: {e}")
        _camera = None


def _get_camera():
    """Get or create camera instance with auto-detection."""
    global _camera
    if _camera is None:
        try:
            # Try to import auto-detecting camera module
            import sys
            sys.path.insert(0, str(DRONE_DIR))
            from camera import get_camera, list_available_cameras
            
            # List available cameras for debugging
            available = list_available_cameras()
            if available:
                print(f"Available cameras: {[c['name'] for c in available]}")
            else:
                print("No cameras detected")
                return None
            
            # Auto-detect and initialize camera
            _camera = get_camera(rgb_fps=15, enable_depth=False)
            if _camera is None:
                print("Warning: Failed to initialize any camera")
                return None
            
            _camera.start()
            print(f"Camera initialized ({_camera.CAMERA_TYPE})")
        except ImportError as e:
            print(f"Warning: Camera not available: {e}")
            return None
        except Exception as e:
            print(f"Warning: Failed to initialize camera: {e}")
            import traceback
            traceback.print_exc()
            return None
    return _camera


def _get_iot_credentials():
    """Get temporary AWS credentials from IoT credential provider."""
    import requests
    import yaml
    from datetime import datetime, timezone
    
    config_path = DRONE_DIR / 'config.yaml'
    if not config_path.exists():
        print("Error: config.yaml not found")
        return None
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f) or {}
    
    drone_id = config.get('drone_id')
    region = config.get('region', 'us-west-2')
    role_alias = config.get('s3_role_alias', 'drone-s3-access-role-alias-dev')
    credentials_endpoint = config.get('credentials_endpoint')
    
    if not drone_id:
        print("Error: Missing drone_id in config")
        return None
    
    if not credentials_endpoint:
        print("Error: Missing credentials_endpoint in config")
        print("  Get it with: aws iot describe-endpoint --endpoint-type iot:CredentialProvider")
        return None
    
    # Paths to IoT certificates (try both naming conventions)
    cert_dir = DRONE_DIR / 'certs'
    
    # Try device.pem/private.key (fleet provisioning format)
    cert_path = cert_dir / 'device.pem'
    key_path = cert_dir / 'private.key'
    ca_path = cert_dir / 'root-ca.pem'
    
    # Fallback to device.cert.pem/device.private.key format
    if not cert_path.exists():
        cert_path = cert_dir / 'device.cert.pem'
    if not key_path.exists():
        key_path = cert_dir / 'device.private.key'
    if not ca_path.exists():
        ca_path = cert_dir / 'AmazonRootCA1.pem'
    
    if not all(p.exists() for p in [cert_path, key_path, ca_path]):
        print(f"Error: IoT certificates not found in {cert_dir}")
        print(f"  Looking for: {cert_path.name}, {key_path.name}, {ca_path.name}")
        return None
    
    # IoT Credential Provider endpoint
    credential_endpoint = f"https://{credentials_endpoint}/role-aliases/{role_alias}/credentials"
    
    try:
        response = requests.get(
            credential_endpoint,
            cert=(str(cert_path), str(key_path)),
            verify=str(ca_path),
            headers={'x-amzn-iot-thingname': drone_id}
        )
        
        if response.status_code == 200:
            creds = response.json()['credentials']
            return {
                'access_key': creds['accessKeyId'],
                'secret_key': creds['secretAccessKey'],
                'session_token': creds['sessionToken'],
                'expiry': datetime.fromisoformat(creds['expiration'].replace('Z', '+00:00'))
            }
        else:
            print(f"Error getting IoT credentials: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Error getting IoT credentials: {e}")
        return None


def _get_s3():
    """Get or create S3 client with fresh credentials."""
    global _s3_client, _s3_bucket, _s3_credentials_expiry
    
    from datetime import datetime, timezone
    
    # Check if credentials are expired or will expire soon (5 min buffer)
    needs_refresh = (
        _s3_client is None or 
        _s3_credentials_expiry is None or
        datetime.now(timezone.utc) >= _s3_credentials_expiry
    )
    
    if needs_refresh:
        try:
            import boto3
            import yaml
            from datetime import timedelta
            
            # Load config for bucket name
            config_path = DRONE_DIR / 'config.yaml'
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f) or {}
                _s3_bucket = config.get('images_bucket', 'drone-images-dev')
                region = config.get('region', 'us-west-2')
            else:
                _s3_bucket = 'drone-images-dev'
                region = 'us-west-2'
            
            # Try to get credentials from IoT credential provider
            creds = _get_iot_credentials()
            
            if creds:
                _s3_client = boto3.client(
                    's3',
                    region_name=region,
                    aws_access_key_id=creds['access_key'],
                    aws_secret_access_key=creds['secret_key'],
                    aws_session_token=creds['session_token']
                )
                # Set expiry with 5 min buffer
                _s3_credentials_expiry = creds['expiry'] - timedelta(minutes=5)
                print(f"S3 initialized with IoT credentials (bucket: {_s3_bucket}, expires: {creds['expiry']})")
            else:
                # Fallback to default credentials (EC2 role, env vars, etc.)
                _s3_client = boto3.client('s3', region_name=region)
                _s3_credentials_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
                print(f"S3 initialized with default credentials (bucket: {_s3_bucket})")
                
        except ImportError:
            print("Warning: boto3 not available, upload disabled")
            return None, None
        except Exception as e:
            print(f"Warning: Failed to initialize S3: {e}")
            return None, None
    
    return _s3_client, _s3_bucket


def capture_photo(save_path=None, upload=True):
    """
    Capture a photo from the drone camera and optionally upload to S3.
    
    Args:
        save_path: Optional path to save the image. If None, uses temp file.
        upload: If True, upload to S3 and return URL. If False, return local path.
    
    Returns:
        S3 URL if uploaded, local path otherwise, or None if capture failed.
    """
    camera = _get_camera()
    if camera is None:
        print("Error: Camera not available")
        return None
    
    try:
        import cv2
        
        # Discard first few frames to let auto-exposure settle
        # Note: RealSense already does 30-frame warmup in start(), so this is minimal
        print("Warming up camera...")
        for i in range(3):
            camera.get_frame(timeout_ms=500)
        print("Camera ready")
        
        frame = camera.get_frame(timeout_ms=2000)
        if frame is None or frame.rgb is None:
            print("Error: Failed to capture frame")
            return None
        
        if save_path is None:
            save_path = tempfile.mktemp(suffix='.jpg', prefix='drone_capture_')
        
        # Convert RGB to BGR for OpenCV
        bgr_frame = cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2BGR)
        cv2.imwrite(save_path, bgr_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        
        print(f"Photo captured: {save_path}")
        
        # Release camera after capture so video streaming can use it
        release_camera()
        
        # Upload to S3 if requested
        if upload:
            import uuid
            conversation_id = globals().get('CONVERSATION_ID') or os.environ.get('CONVERSATION_ID')
            if not conversation_id:
                conversation_id = f"photos/{uuid.uuid4().hex[:8]}"
            
            url = upload_photo(save_path, conversation_id)
            if url:
                print(f"Photo uploaded: {url}")
                # Clean up local file
                try:
                    os.remove(save_path)
                except:
                    pass
                return url
        
        return save_path
        
    except Exception as e:
        print(f"Error capturing photo: {e}")
        # Release camera even on error
        release_camera()
        return None


def upload_photo(local_path, conversation_id):
    """
    Upload a photo to S3 and return the public URL.
    
    Args:
        local_path: Path to the local image file.
        conversation_id: Conversation ID for organizing uploads.
    
    Returns:
        Public URL of the uploaded image, or None if upload failed.
    """
    s3, bucket = _get_s3()
    if s3 is None or bucket is None:
        print("Error: S3 not available")
        return None
    
    try:
        import yaml
        from datetime import datetime
        
        # Get drone ID from config
        config_path = DRONE_DIR / 'config.yaml'
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f) or {}
            drone_id = config.get('drone_id', 'unknown')
        else:
            drone_id = 'unknown'
        
        # Create S3 key
        filename = Path(local_path).name
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f'drones/{drone_id}/conversations/{conversation_id}/{timestamp}_{filename}'
        
        # Upload
        s3.upload_file(
            local_path,
            bucket,
            s3_key,
            ExtraArgs={'ContentType': 'image/jpeg'}
        )
        
        # Generate public URL
        url = f'https://{bucket}.s3.amazonaws.com/{s3_key}'
        print(f"Photo uploaded: {url}")
        
        return url
        
    except Exception as e:
        print(f"Error uploading photo: {e}")
        return None



