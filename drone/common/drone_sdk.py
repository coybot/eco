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

try:
    import battery as _battery
except ImportError:  # installed as a package
    from . import battery as _battery

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

# Ceiling guard state
_ceiling_guard_thread = None
_ceiling_guard_stop = threading.Event()
_ceiling_guard_holding = None  # clearance (m) while the guard is freezing altitude, else None


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

# Config, certs, and logs live alongside this file by default (repo:
# drone/common/; on-device flat deploy: ~/drone-api/). Override with
# COYBOT_SDK_CONFIG_DIR or set_config_path() when running as an installed
# package with config living elsewhere (e.g. examples/, a customer's own
# project layout).
_script_dir = Path(__file__).parent.absolute()
DRONE_DIR = Path(os.environ.get("COYBOT_SDK_CONFIG_DIR", _script_dir)).absolute()


def set_config_path(path):
    """Override the directory where config.yaml, certs/, and logs live.

    Defaults to this file's own directory (or $COYBOT_SDK_CONFIG_DIR), which
    matches flat on-device deployment. Call this when running the SDK as an
    installed package pointed at a different config directory.
    """
    global DRONE_DIR
    DRONE_DIR = Path(path).absolute()


def _connect():
    """Get or create MAVLink connection. Must be called with _mavlink_lock held.
    
    Raises ConnectionError if the flight controller is not detected.
    """
    global _master
    # Note: caller must hold _mavlink_lock (RLock allows re-entry if needed)
    if _master is None:
        import os
        import yaml

        port = '/dev/ttyACM0'
        baud = 115200
        config_path = DRONE_DIR / 'config.yaml'
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f) or {}
            port = config.get('serial_port', port)
            baud = config.get('baud_rate', baud)

        # Env overrides, e.g. for SITL: COYBOT_SDK_SERIAL_PORT=tcp:127.0.0.1:5760
        port = os.environ.get('COYBOT_SDK_SERIAL_PORT', port)
        baud = int(os.environ.get('COYBOT_SDK_BAUD_RATE', baud))

        # Network URLs (SITL, MAVProxy, etc.) bypass the serial-device check.
        is_network = port.startswith(('tcp:', 'tcpin:', 'udp:', 'udpin:', 'udpout:'))
        if not is_network and not os.path.exists(port):
            raise ConnectionError(
                f"Flight controller not found at {port}. "
                f"Check USB connection and verify the serial port in config.yaml. "
                f"For SITL/simulation, set COYBOT_SDK_SERIAL_PORT to a MAVLink URL "
                f"(e.g. tcp:127.0.0.1:5760)."
            )

        print(f"Connecting to {port} at {baud}...")
        if is_network:
            _master = mavutil.mavlink_connection(port, source_system=255)
        else:
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


def _force_arm_allowed():
    """Force-arming skips every FC pre-arm check, battery included. Off unless
    config.yaml sets allow_force_arm: true (e.g. a bench rig with no GPS)."""
    return bool(_load_config().get('allow_force_arm', False))


def _send_arm(force):
    return _mav_command(
        lambda m: m.mav.command_long_send(
            m.target_system, m.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0, 1, 21196 if force else 0, 0, 0, 0, 0, 0
        ), 400)


def _arm_checked():
    """Normal arm, so the FC's pre-arm checks (battery, GPS, EKF...) apply.
    Falls back to force only when allowed. Returns (ack_ok, result)."""
    ack_ok, result = _send_arm(force=False)
    if ack_ok:
        return ack_ok, result
    reasons = _drain_statustext(timeout=1, print_msgs=False) or []
    for r in reasons:
        _log(f"  FC: {r}")
    if not _force_arm_allowed():
        _log("Arm refused by the FC's pre-arm checks (above). Not force-arming: "
             "fix the cause, or set allow_force_arm: true in config.yaml for a bench rig.")
        return ack_ok, result
    _log("WARNING: force-arming past the FC's pre-arm checks (allow_force_arm is set)")
    return _send_arm(force=True)


def arm():
    """Arm the drone motors. Returns True only if actually armed."""
    _log("Arming...")
    
    # Check if already armed
    if is_armed():
        _log("Already armed!")
        return True
    
    # Ensure DISARM_DELAY is long enough to survive waits between arm and takeoff
    _mav_send(lambda m: m.mav.param_set_send(
        m.target_system, m.target_component,
        b"DISARM_DELAY", 30.0, mavutil.mavlink.MAV_PARAM_TYPE_INT8))

    # Set STABILIZE mode first (required for arming)
    _mav_send(lambda m: m.set_mode(0))  # 0 = STABILIZE
    time.sleep(0.5)
    
    # Send arm command and wait for ACK atomically (prevents heartbeat thread from stealing ACK)
    ack_ok, result = _arm_checked()
    
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
    batt_ok, batt_reason = _battery_preflight(altitude_m)
    if not batt_ok:
        _log(f"TAKEOFF REFUSED: {batt_reason}")
        _disarm_if_on_ground()
        return False
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
        ack_ok, result = _arm_checked()
        if not ack_ok:
            _log(f"Arm failed (result={result})")
            return False

        _log("Arm ACK received, waiting for motors...")
        time.sleep(0.3)

        if not is_armed():
            _log("ERROR: Arm command ACK'd but drone not armed!")
            _drain_statustext()
            return False
        _log("Armed confirmed!")
    
    global _flight_home
    pos = _fresh_position(timeout=2.0) or _position_relative()
    _flight_home = (pos[0], pos[1]) if pos else None
    start_battery_guard()

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
    
    # Wait for the altitude to be REACHED, not for a timer: a climb blocked by
    # the ceiling guard (or anything else) used to report "Reached" anyway, and
    # the mission then flew its remaining phases from wherever it was stuck.
    timeout = max(altitude_m * 3, 10)
    _log(f"Climbing... (up to {timeout:.0f}s)")
    target = max(altitude_m - 0.5, altitude_m * 0.85)
    alt = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        pos = _fresh_position()
        if pos is not None:
            alt = pos[2]
            if alt >= target:
                _log(f"Reached {alt:.1f}m")
                return True
        time.sleep(0.5)
    why = ""
    if _ceiling_guard_holding is not None:
        why = (f" - the ceiling guard is holding altitude: the upward rangefinder reads "
               f"{_ceiling_guard_holding:.2f}m clearance")
    got = f"{alt:.1f}m" if alt is not None else "unknown altitude"
    _log(f"TAKEOFF FAILED: only reached {got} of {altitude_m}m{why}. Landing.")
    land()
    return False


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


def _disarm_if_on_ground():
    """Undo an arm() that a refused takeoff would otherwise leave spinning."""
    if is_armed():
        try:
            safe_disarm()
        except RuntimeError as e:
            _log(str(e))


def goto(lat, lon, alt, max_alt=MAX_ALTITUDE):
    """Fly to GPS coordinates.

    SAFETY: Altitude is clamped to MIN_ALTITUDE..max_alt range. max_alt
    defaults to this module's own copter-oriented MAX_ALTITUDE (20m), but
    plane_sdk.py's callers (orbit(), HardwareBackend.goto() for fixedwing)
    pass their own, much higher ceiling — confirmed live in ArduPlane SITL
    that leaving this at the copter default silently clamped a fixed-wing's
    40m orbit/cruise altitude down to 20m, right at the tree-clearance floor,
    with no error or warning surfaced beyond a "SAFETY: ... clamped" log line
    easy to miss.
    """
    alt = _clamp(alt, MIN_ALTITUDE, max_alt, "altitude")
    if _battery_gates_apply():
        _, _, gov = _battery_models()
        pos = _position_relative()
        if pos is not None:
            cost = gov.move_cost(_flat_dist_m(pos[0], pos[1], lat, lon), alt - pos[2])
            ok, reason = check_battery_for("goto", cost, _dist_home_m(lat, lon), alt)
            if not ok:
                _log(f"GOTO REFUSED: {reason}")
                return False
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
    """Get current GPS position. Returns (lat, lon, alt_m).

    alt_m is altitude above home (relative), matching the frame used by
    goto() — so get_position()[2] can be passed directly to goto().
    """
    _mav_send(lambda m: m.mav.request_data_stream_send(1, 1, mavutil.mavlink.MAV_DATA_STREAM_POSITION, 4, 1))
    start = time.time()
    while time.time() - start < 3:
        msg = _mav_recv('GLOBAL_POSITION_INT', timeout=0.5)
        if msg:
            return (msg.lat / 1e7, msg.lon / 1e7, msg.relative_alt / 1000)
    return (0, 0, 0)


def get_ceiling_distance():
    """Distance from drone to ceiling via upward-facing rangefinder, in meters.

    Returns None if no sensor reading is available (sensor absent or out of range).
    """
    start = time.time()
    while time.time() - start < 2:
        msg = _mav_recv('DISTANCE_SENSOR', timeout=0.5)
        if msg and msg.orientation == 25:  # MAV_SENSOR_ROTATION_PITCH_90 = upward
            if msg.current_distance < msg.max_distance:
                return msg.current_distance / 100.0  # cm → m
    return None


def _ceiling_guard_loop(min_clearance):
    """Background thread: if clearance drops below threshold, freeze altitude in place."""
    global _ceiling_guard_stop, _ceiling_guard_holding
    _log(f"Ceiling guard started (min clearance={min_clearance}m)")
    clamped = False

    while not _ceiling_guard_stop.is_set():
        try:
            with _mavlink_lock:
                conn = _connect()
                # Drain the buffer for a fresh DISTANCE_SENSOR reading
                dist_msg = None
                deadline = time.time() + 0.3
                while time.time() < deadline:
                    m = conn.recv_match(type='DISTANCE_SENSOR', blocking=False)
                    if m and m.orientation == 25 and m.current_distance < m.max_distance:
                        dist_msg = m
                        break
                    time.sleep(0.01)

                if dist_msg is None:
                    clamped = False
                    _ceiling_guard_holding = None
                    time.sleep(0.1)
                    continue

                clearance = dist_msg.current_distance / 100.0

                if clearance < min_clearance:
                    _ceiling_guard_holding = clearance
                    if not clamped:
                        _log(f"CEILING GUARD: {clearance:.2f}m clearance — holding altitude")
                        clamped = True
                    # Read current position and re-issue it as a hold target (stops ascent)
                    pos = conn.recv_match(type='GLOBAL_POSITION_INT', blocking=True, timeout=0.3)
                    if pos:
                        conn.mav.set_position_target_global_int_send(
                            0,
                            conn.target_system, conn.target_component,
                            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                            0b0000111111111000,  # position only, ignore velocity/accel/yaw
                            pos.lat, pos.lon, pos.relative_alt / 1000.0,
                            0, 0, 0, 0, 0, 0, 0, 0
                        )
                else:
                    _ceiling_guard_holding = None
                    if clamped:
                        _log(f"CEILING GUARD: clearance restored ({clearance:.2f}m), resuming")
                        clamped = False

        except Exception as e:
            _log(f"Ceiling guard error: {e}")

        time.sleep(0.1)

    _log("Ceiling guard stopped")


def start_ceiling_guard(min_clearance=0.5):
    """Start background ceiling guard. Freezes altitude if clearance drops below min_clearance (m)."""
    global _ceiling_guard_thread, _ceiling_guard_stop
    if _ceiling_guard_thread and _ceiling_guard_thread.is_alive():
        _log("Ceiling guard already running")
        return
    _ceiling_guard_stop.clear()
    _ceiling_guard_thread = threading.Thread(
        target=_ceiling_guard_loop, args=(min_clearance,), daemon=True
    )
    _ceiling_guard_thread.start()


def stop_ceiling_guard():
    """Stop the ceiling guard thread."""
    global _ceiling_guard_stop
    _ceiling_guard_stop.set()


def get_attitude():
    """Get current attitude. Returns (roll, pitch, yaw) in degrees."""
    _mav_send(lambda m: m.mav.request_data_stream_send(1, 1, mavutil.mavlink.MAV_DATA_STREAM_EXTRA1, 4, 1))
    start = time.time()
    while time.time() - start < 3:
        msg = _mav_recv('ATTITUDE', timeout=0.5)
        if msg:
            return (math.degrees(msg.roll), math.degrees(msg.pitch), math.degrees(msg.yaw))
    return (0, 0, 0)


# =============================================================================
# SAFETY LAYER 5: Battery - estimation, preflight gate, in-flight guard.
# The logic lives in battery.py (pure, testable); this is the FC I/O around it.
#
# The FC's own SYS_STATUS.battery_remaining is NOT used as the battery level:
# it is integrated current that resets to 100% at every FC boot and freezes
# when the current sensor reads ~0 A, which is how a flat pack read 87%.
# =============================================================================

_battery_state_lock = threading.Lock()
_battery_cfg = None
_battery_estimator = None
_battery_governor = None
_battery_guard_thread = None
_battery_guard_stop = threading.Event()
_battery_guard_trip = None   # None, or {'action': 'return_home'|'land_now', 'reason': str}
_flight_home = None          # (lat, lon) captured at takeoff/arm, for trip-home cost
_last_fc_seed = 0.0

# ArduPilot-specific: set the FC's remaining % (ardupilotmega.xml).
_MAV_CMD_BATTERY_RESET = 42651


def _battery_models():
    """(config, estimator, governor), built lazily from config.yaml `battery:`."""
    global _battery_cfg, _battery_estimator, _battery_governor
    with _battery_state_lock:
        if _battery_cfg is None:
            _battery_cfg = _battery.BatteryConfig.from_dict(_load_config().get('battery'))
            _battery_estimator = _battery.BatteryEstimator(_battery_cfg)
            _battery_governor = _battery.BatteryGovernor(_battery_cfg)
        return _battery_cfg, _battery_estimator, _battery_governor


def set_battery_pack(cells=None, capacity_mah=None):
    """Override the pack's cell count / capacity (the app's battery settings)."""
    cfg, est, _ = _battery_models()
    with _battery_state_lock:
        if cells:
            cfg.cells = int(cells)
        if capacity_mah:
            cfg.capacity_mah = float(capacity_mah)


def _battery_gates_apply():
    """Only a multirotor's flight is priced by battery.py's hover-based model."""
    vt = str(_load_config().get('vehicle_type', 'quadcopter')).lower()
    return vt in ('quadcopter', 'quad', 'copter', 'multirotor')


def _read_battery_raw(max_wait=1.5):
    """One fresh FC battery reading as a battery.BatterySample.

    Pumps the link briefly and then reads pymavlink's latest-message cache, so
    it does not hold the serial lock for seconds the way a filtered
    recv_match loop would (the in-flight guard calls this every 2 s).
    """
    with _mavlink_lock:
        m = _connect()
        m.mav.request_data_stream_send(
            m.target_system, m.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS, 4, 1)
        start = time.time()
        while time.time() - start < max_wait:
            msg = m.recv_match(blocking=True, timeout=0.1)
            if msg is not None and msg.get_type() == 'SYS_STATUS':
                break
        cache = dict(m.messages)

    def fresh(name, age=5.0):
        msg = cache.get(name)
        if msg is None:
            return None
        ts = getattr(msg, '_timestamp', None)
        return msg if ts is None or time.time() - ts <= age else None

    sys_status = fresh('SYS_STATUS')
    batt_status = fresh('BATTERY_STATUS')
    hb = fresh('HEARTBEAT')
    sample = _battery.BatterySample()
    if sys_status is not None:
        sample.voltage = sys_status.voltage_battery / 1000.0 if sys_status.voltage_battery not in (-1, 65535) else 0.0
        sample.current_a = sys_status.current_battery / 100.0 if sys_status.current_battery != -1 else None
        sample.fc_remaining = sys_status.battery_remaining
    if batt_status is not None and batt_status.current_consumed != -1:
        sample.consumed_mah = float(batt_status.current_consumed)
    if hb is not None:
        sample.armed = (hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
    else:
        sample.armed = is_armed()
    scales = _battery_scales()
    if scales.get('BATT_VOLT_MULT') and sample.voltage:
        sample.volt_adc_v = sample.voltage / scales['BATT_VOLT_MULT']
    if scales.get('BATT_AMP_PERVLT') and sample.current_a is not None:
        sample.curr_adc_v = sample.current_a / scales['BATT_AMP_PERVLT'] + (scales.get('BATT_AMP_OFFSET') or 0.0)
    return sample


_battery_scale_cache = {'t': 0.0, 'values': {}}


def _battery_scales(max_age_s=300.0):
    """The FC's battery input scales (cached): what turns a reading back into
    the raw pin voltage, so a pin pinned at the ADC rail can be recognised."""
    c = _battery_scale_cache
    if not c['values'] or time.time() - c['t'] > max_age_s:
        try:
            values = {n: _get_param(n) for n in ('BATT_VOLT_MULT', 'BATT_AMP_PERVLT', 'BATT_AMP_OFFSET')}
            if any(v is not None for v in values.values()):
                c['values'], c['t'] = values, time.time()
        except Exception:
            pass
    return c['values']


def get_battery():
    """Battery state. Keys: voltage (V), current (A or None), remaining (%, or -1
    when it cannot be determined), source, warnings, consumed_mah, fc_remaining,
    armed.

    `remaining` is this SDK's estimate (resting-voltage curve on the ground,
    verified current count or sag-compensated voltage in the air) - not the
    FC's own counter, which is reported separately as fc_remaining.
    """
    _, estimator, _ = _battery_models()
    sample = _read_battery_raw()
    with _battery_state_lock:
        est = estimator.update(sample)
    return {
        'voltage': sample.voltage or 0,
        'remaining': est.pct if est.pct is not None else -1,
        'current': sample.current_a,
        'consumed_mah': sample.consumed_mah,
        'fc_remaining': est.fc_remaining,
        'source': est.source,
        'warnings': est.warnings,
        'armed': sample.armed,
    }


def _position_relative():
    """(lat, lon, rel_alt_m) or None, from the latest GLOBAL_POSITION_INT."""
    try:
        with _mavlink_lock:
            msg = _connect().messages.get('GLOBAL_POSITION_INT')
        if msg is None:
            return None
        return msg.lat / 1e7, msg.lon / 1e7, msg.relative_alt / 1000.0
    except Exception:
        return None


def _fresh_position(timeout=1.0):
    """(lat, lon, rel_alt_m) from a NEW GLOBAL_POSITION_INT, or None.
    Unlike get_position() this never returns a (0, 0, 0) placeholder."""
    with _mavlink_lock:
        m = _connect()
        msg = m.recv_match(type='GLOBAL_POSITION_INT', blocking=True, timeout=timeout)
    if msg is None:
        return None
    return msg.lat / 1e7, msg.lon / 1e7, msg.relative_alt / 1000.0


def _offset_origin():
    """Where north/east offsets are measured from: the takeoff point."""
    global _flight_home
    if _flight_home is None:
        pos = _fresh_position(timeout=2.0)
        if pos is None:
            return None
        _flight_home = (pos[0], pos[1])
    return _flight_home


def get_local_pose():
    """(east_m, north_m, up_m, yaw_rad) relative to the takeoff point, or None.
    The Backend seam's ENU pose, straight from the flight controller - for
    flying without Nav2."""
    origin = _offset_origin()
    pos = _fresh_position()
    if origin is None or pos is None:
        return None
    north = (pos[0] - origin[0]) * 111320.0
    east = (pos[1] - origin[1]) * 111320.0 * math.cos(math.radians(origin[0]))
    with _mavlink_lock:
        m = _connect()
        att = m.recv_match(type='ATTITUDE', blocking=True, timeout=1.0) \
            or m.messages.get('ATTITUDE')
    if att is None:
        # Without a heading "forward" would silently mean north; say so instead.
        return None
    return east, north, pos[2], att.yaw  # ATTITUDE.yaw: 0 = north, clockwise


def goto_offset(north_m, east_m, alt_m, tol_m=1.0, timeout_s=None):
    """Fly to a north/east offset (m) from the takeoff point at alt_m and WAIT
    until the aircraft is actually there. Returns True only on arrival.

    Without Nav2 this is the only real way to move to an offset: the Nav2
    bridge's fallback when ROS is absent merely sleeps and reports success.
    Returns False if refused (battery), stopped by the battery guard, or not
    arrived within timeout_s (default: generous for the distance).
    """
    origin = _offset_origin()
    start = _fresh_position(timeout=2.0)
    if origin is None or start is None:
        _log("GOTO FAILED: no position from the flight controller")
        return False
    lat = origin[0] + north_m / 111320.0
    lon = origin[1] + east_m / (111320.0 * math.cos(math.radians(origin[0])))
    dist = _flat_dist_m(start[0], start[1], lat, lon)
    if timeout_s is None:
        timeout_s = 15.0 + 3.0 * (dist + abs(alt_m - start[2])) / 2.0
    if goto(lat, lon, alt_m) is False:
        return False
    alt_target = _clamp(alt_m, MIN_ALTITUDE, MAX_ALTITUDE, "altitude")
    deadline = time.time() + timeout_s
    pos = start
    while time.time() < deadline:
        if _battery_guard_trip:
            _log(f"GOTO STOPPED: {_battery_guard_trip['reason']}")
            return False
        p = _fresh_position()
        if p is not None:
            pos = p
            if (_flat_dist_m(p[0], p[1], lat, lon) <= tol_m
                    and abs(p[2] - alt_target) <= max(1.0, tol_m)):
                _log(f"Arrived at N={north_m:.1f} E={east_m:.1f} alt={p[2]:.1f}m")
                return True
        time.sleep(0.5)
    left = _flat_dist_m(pos[0], pos[1], lat, lon)
    why = ""
    if _ceiling_guard_holding is not None:
        why = f" (ceiling guard holding: {_ceiling_guard_holding:.2f}m clearance)"
    _log(f"GOTO FAILED: still {left:.1f}m from N={north_m:.1f} E={east_m:.1f} "
         f"after {timeout_s:.0f}s{why}")
    return False


def _flat_dist_m(lat1, lon1, lat2, lon2):
    dn = (lat2 - lat1) * 111320.0
    de = (lon2 - lon1) * 111320.0 * math.cos(math.radians(lat1))
    return math.hypot(dn, de)


def _dist_home_m(lat, lon):
    if _flight_home is None or lat is None:
        return 0.0
    return _flat_dist_m(_flight_home[0], _flight_home[1], lat, lon)


def battery_status(planned_alt_m=5.0):
    """Everything the operator needs before and during a flight, in one dict.

    can_takeoff/takeoff_reason: the preflight verdict for `planned_alt_m`.
    in_flight: the standing must-we-go-home verdict (meaningful when armed).
    actions_left: roughly how many ~30 s flying actions remain before the
    trip home eats into the reserve.
    """
    cfg, _, gov = _battery_models()
    b = get_battery()
    pct = b['remaining'] if b['remaining'] >= 0 else None
    pos = _position_relative()
    alt = max(0.0, pos[2]) if pos else 0.0
    dist_home = _dist_home_m(pos[0], pos[1]) if pos else 0.0
    pre = gov.preflight(pct, planned_alt_m)
    flight = gov.in_flight(pct, dist_home, alt)
    return {
        **b,
        'can_takeoff': pre.ok,
        'takeoff_reason': pre.reason,
        'in_flight': flight.to_dict(),
        'actions_left': gov.actions_left(pct, dist_home, alt),
        'reserve_pct': cfg.landing_reserve_pct,
        'min_takeoff_pct': cfg.min_takeoff_pct,
        'guard': dict(_battery_guard_trip) if _battery_guard_trip else None,
    }


def _battery_preflight(altitude_m):
    """(ok, reason) for taking off to altitude_m on the current pack."""
    if not _battery_gates_apply():
        return True, ""
    _, _, gov = _battery_models()
    b = get_battery()
    for w in b['warnings']:
        _log(f"  BATTERY: {w}")
    pct = b['remaining'] if b['remaining'] >= 0 else None
    v = gov.preflight(pct, altitude_m)
    src = b['source']
    if pct is not None:
        _log(f"Battery: {pct:.0f}% ({b['voltage']:.2f} V, from {src})")
    return v.ok, v.reason


def check_battery_for(kind, cost_pct, dist_home_after_m=None, alt_after_m=None):
    """Governor check for an action about to run. Returns (ok, reason).

    Always ok on the ground and for non-multirotor vehicles. Callers that know
    where the action leaves the aircraft pass it; otherwise "here" is assumed.
    """
    if not _battery_gates_apply():
        return True, ""
    _, _, gov = _battery_models()
    b = get_battery()
    if not b['armed']:
        return True, ""
    pct = b['remaining'] if b['remaining'] >= 0 else None
    pos = _position_relative()
    if dist_home_after_m is None:
        dist_home_after_m = _dist_home_m(pos[0], pos[1]) if pos else 0.0
    if alt_after_m is None:
        alt_after_m = max(0.0, pos[2]) if pos else 0.0
    v = gov.check_action(pct, cost_pct, dist_home_after_m, alt_after_m, kind=kind)
    return v.ok, v.reason


def battery_guard_tripped():
    """None, or {'action', 'reason'} once the in-flight guard has sent the
    aircraft home or down. Mission code must stop commanding when set."""
    return dict(_battery_guard_trip) if _battery_guard_trip else None


def _battery_state_path():
    return DRONE_DIR / 'battery_state.json'


def _save_last_flight(consumed_mah):
    try:
        _battery_state_path().write_text(json.dumps({
            'last_flight_consumed_mah': consumed_mah,
            'recorded_at': time.time(),
        }))
    except Exception as e:
        _log(f"Could not record flight mAh: {e}")


def _set_mode_confirmed(mode, timeout=3):
    _mav_send(lambda m: m.set_mode(mode))
    return _wait_for_mode(mode, timeout=timeout)


def _battery_guard_loop(interval_s):
    """While armed: if the governor says go home / land now, make the FC do it.

    Escalates only (ok -> RTL -> LAND), never back. Needs two consecutive bad
    readings before acting so a single sag spike in a climb can't trigger it.
    RTL needs a position fix; if the FC refuses RTL it lands instead.
    """
    global _battery_guard_trip, _flight_home
    _, estimator, gov = _battery_models()
    _log("Battery guard started")
    strikes = 0
    was_armed = False
    started = time.time()
    while not _battery_guard_stop.is_set():
        try:
            b = get_battery()
            if b['armed']:
                was_armed = True
                pct = b['remaining'] if b['remaining'] >= 0 else None
                pos = _position_relative()
                if _flight_home is None and pos:
                    _flight_home = (pos[0], pos[1])
                alt = max(0.0, pos[2]) if pos else 0.0
                dist_home = _dist_home_m(pos[0], pos[1]) if pos else 0.0
                v = gov.in_flight(pct, dist_home, alt)
                strikes = strikes + 1 if not v.ok else 0
                current = (_battery_guard_trip or {}).get('action')
                rank = {'ok': 0, 'return_home': 1, 'land_now': 2}
                if strikes >= 2 and rank.get(v.action, 0) > rank.get(current, 0):
                    _log(f"BATTERY GUARD: {v.reason}")
                    if v.action == 'return_home' and _set_mode_confirmed('RTL'):
                        _battery_guard_trip = {'action': 'return_home', 'reason': v.reason}
                    else:
                        if v.action == 'return_home':
                            _log("BATTERY GUARD: FC refused RTL - landing here instead")
                        _set_mode_confirmed('LAND')
                        _battery_guard_trip = {'action': 'land_now', 'reason': v.reason}
            elif was_armed:
                # Landed and disarmed: record the flight's mAh for current-sensor
                # calibration, then stop - the next takeoff starts a new guard.
                if estimator.last_flight_consumed_mah is not None:
                    _save_last_flight(estimator.last_flight_consumed_mah)
                break
            elif time.time() - started > 120:
                break  # never armed (takeoff refused or failed)
        except Exception as e:
            _log(f"Battery guard error: {e}")
        _battery_guard_stop.wait(interval_s)
    _log("Battery guard stopped")


def start_battery_guard(interval_s=2.0):
    """Start the in-flight battery guard (takeoff() does this itself)."""
    global _battery_guard_thread, _battery_guard_trip
    if not _battery_gates_apply():
        return
    if _battery_guard_thread and _battery_guard_thread.is_alive():
        return
    _battery_guard_trip = None
    _battery_guard_stop.clear()
    _battery_guard_thread = threading.Thread(
        target=_battery_guard_loop, args=(interval_s,), daemon=True)
    _battery_guard_thread.start()


def stop_battery_guard():
    _battery_guard_stop.set()


def seed_fc_battery_from_voltage(min_gap_pct=5.0, min_interval_s=120.0):
    """On the ground, correct the FC's own remaining % from resting voltage.

    ArduPilot's counter restarts at 100% every boot whatever pack is fitted;
    this re-seeds it (MAV_CMD_BATTERY_RESET) so the FC's own mAh failsafes
    (BATT_LOW_MAH/BATT_CRT_MAH/BATT_ARM_MAH) start from the truth. Only acts
    disarmed, on a plausible voltage, when the FC is off by min_gap_pct.
    Returns the seeded % or None.
    """
    global _last_fc_seed
    if time.time() - _last_fc_seed < min_interval_s:
        return None
    b = get_battery()
    if b['armed'] or b['source'] != 'voltage_rest' or b['fc_remaining'] is None:
        return None
    if abs(b['fc_remaining'] - b['remaining']) < min_gap_pct:
        return None
    pct = int(round(b['remaining']))
    ok, result = _mav_command(lambda m: m.mav.command_long_send(
        m.target_system, m.target_component, _MAV_CMD_BATTERY_RESET, 0,
        1, pct, 0, 0, 0, 0, 0), _MAV_CMD_BATTERY_RESET)
    _last_fc_seed = time.time()
    if ok:
        _log(f"Seeded FC battery to {pct}% from resting voltage (FC said {b['fc_remaining']}%)")
        return pct
    return None


def _get_param(name, timeout=2.0):
    """Read one FC parameter. Returns its float value or None."""
    with _mavlink_lock:
        m = _connect()
        m.mav.param_request_read_send(m.target_system, m.target_component,
                                      name.encode('utf-8'), -1)
        start = time.time()
        while time.time() - start < timeout:
            msg = m.recv_match(type='PARAM_VALUE', blocking=True, timeout=0.3)
            if msg and msg.param_id.rstrip('\x00') == name:
                return float(msg.param_value)
    return None


def _average_voltage(seconds=3.0):
    vs = []
    end = time.time() + seconds
    while time.time() < end:
        s = _read_battery_raw(max_wait=0.6)
        if s.voltage:
            vs.append(s.voltage)
    return sum(vs) / len(vs) if vs else 0.0


def _esc_voltage(seconds=1.5):
    """Mean pack voltage reported by ESC telemetry (BLHeli32/AM32), or None."""
    vals = []
    end = time.time() + seconds
    while time.time() < end:
        msg = _mav_recv('ESC_TELEMETRY_1_TO_4', timeout=0.3)
        if msg:
            vals.extend(v / 100.0 for v in msg.voltage if v)
    return sum(vals) / len(vals) if vals else None


def battery_diagnostics():
    """Read the FC's battery-monitor setup and say what, if anything, is wrong.

    No multimeter needed: it cross-checks the FC's voltage against the LiPo
    plausibility band and ESC telemetry (if the ESCs report voltage), reports
    the board name from the FC banner so pin numbers can be looked up, and
    flags the Pixhawk-1 pin/scale values older versions of this SDK wrote to
    every board.
    """
    cfg, _, _ = _battery_models()
    names = ['BATT_MONITOR', 'BATT_VOLT_PIN', 'BATT_CURR_PIN', 'BATT_VOLT_MULT',
             'BATT_AMP_PERVLT', 'BATT_AMP_OFFSET', 'BATT_CAPACITY', 'BATT_LOW_VOLT',
             'BATT_CRT_VOLT', 'BATT_LOW_MAH', 'BATT_CRT_MAH', 'BATT_ARM_VOLT',
             'BATT_ARM_MAH', 'BATT_FS_LOW_ACT', 'BATT_FS_CRT_ACT', 'ARMING_CHECK']
    params = {n: _get_param(n) for n in names}

    # The FC prints its board name in the banner it sends on this request.
    _drain_statustext(timeout=0.2, print_msgs=False)
    _mav_send(lambda m: m.mav.command_long_send(
        m.target_system, m.target_component,
        mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES, 0, 1, 0, 0, 0, 0, 0, 0))
    banner = _drain_statustext(timeout=2.0, print_msgs=False)

    b = get_battery()
    esc_v = _esc_voltage()
    findings = list(b['warnings'])
    if params['BATT_MONITOR'] == 0:
        findings.append("BATT_MONITOR=0: the FC is not monitoring the battery at all")
    elif params['BATT_MONITOR'] == 3:
        findings.append("BATT_MONITOR=3 (voltage only): the FC's own % and mAh "
                        "failsafes cannot work; this SDK estimates from voltage")
    if (params['BATT_VOLT_PIN'], params['BATT_CURR_PIN'],
            params['BATT_VOLT_MULT'], params['BATT_AMP_PERVLT']) == (2.0, 3.0, 10.1, 17.0):
        findings.append(
            "BATT_VOLT_PIN/CURR_PIN/VOLT_MULT/AMP_PERVLT are 2/3/10.1/17.0 - the "
            "Pixhawk-1 + 3DR power module values older versions of this SDK wrote "
            "to every board. Unless this is that hardware, look up the pins for the "
            "board named below and set battery.fc_pins in config.yaml")
    if esc_v and b['voltage']:
        diff = b['voltage'] - esc_v
        if abs(diff) > 0.03 * esc_v + 0.2:
            findings.append(
                f"FC battery monitor reads {b['voltage']:.2f} V but ESC telemetry reads "
                f"{esc_v:.2f} V - BATT_VOLT_MULT is off; calibrate_voltage(use_esc=True) fixes it")
    if params['ARMING_CHECK'] == 0:
        findings.append("ARMING_CHECK=0: the FC's own pre-arm checks (battery included) are off")
    return {
        'board_banner': banner,
        'params': params,
        'battery': b,
        'esc_voltage': esc_v,
        'cells': cfg.cells,
        'findings': findings,
    }


def calibrate_voltage(reference_v=None, full_charge=False, use_esc=False):
    """Correct BATT_VOLT_MULT without a multimeter. Disarmed only.

    Pick ONE reference:
      full_charge=True  - pack has just come off a LiPo charger that
                          terminated normally: it is at 4.20 V/cell.
      use_esc=True      - the ESCs' own voltage telemetry.
      reference_v=15.9  - the pack voltage the charger's display showed.
    Refuses corrections over 15%: that's a wrong pin or cell count, not scale.
    """
    cfg, _, _ = _battery_models()
    if is_armed():
        raise RuntimeError("calibrate_voltage() needs the aircraft disarmed and resting")
    if full_charge:
        reference_v = _battery.FULL_CHARGE_CELL_V * cfg.cells
        label = f"full charge ({cfg.cells}S x {_battery.FULL_CHARGE_CELL_V} V)"
    elif use_esc:
        reference_v = _esc_voltage(seconds=3.0)
        if not reference_v:
            raise RuntimeError("ESCs report no voltage telemetry - use full_charge or reference_v")
        label = "ESC telemetry"
    elif reference_v:
        label = "supplied reference"
    else:
        raise ValueError("give reference_v, full_charge=True or use_esc=True")
    measured = _average_voltage()
    ratio = _battery.voltage_mult_correction(measured, float(reference_v))
    old = _get_param('BATT_VOLT_MULT')
    if old is None:
        raise RuntimeError("could not read BATT_VOLT_MULT from the FC")
    new = round(old * ratio, 4)
    if not _set_param('BATT_VOLT_MULT', new):
        raise RuntimeError("FC did not acknowledge BATT_VOLT_MULT")
    after = _average_voltage(seconds=2.0)
    _log(f"BATT_VOLT_MULT {old} -> {new}: FC read {measured:.2f} V, {label} "
         f"{reference_v:.2f} V, now reads {after:.2f} V")
    return {'old': old, 'new': new, 'measured_v': measured,
            'reference_v': reference_v, 'now_reads_v': after}


def calibrate_current(charger_mah, fc_consumed_mah=None):
    """Correct BATT_AMP_PERVLT from what the charger put back after a flight.

    The standard no-meter method: fly, land, recharge, and give the mAh the
    charger reports. The FC's count for that flight is recorded automatically
    at disarm (battery_state.json); pass fc_consumed_mah to override.
    """
    if fc_consumed_mah is None:
        try:
            state = json.loads(_battery_state_path().read_text())
            fc_consumed_mah = state['last_flight_consumed_mah']
        except Exception:
            raise RuntimeError("no recorded flight mAh - fly once with the battery "
                               "guard running, or pass fc_consumed_mah")
    ratio = _battery.current_scale_correction(float(fc_consumed_mah), float(charger_mah))
    old = _get_param('BATT_AMP_PERVLT')
    if old is None:
        raise RuntimeError("could not read BATT_AMP_PERVLT from the FC")
    new = round(old * ratio, 3)
    if not _set_param('BATT_AMP_PERVLT', new):
        raise RuntimeError("FC did not acknowledge BATT_AMP_PERVLT")
    _log(f"BATT_AMP_PERVLT {old} -> {new}: FC counted {fc_consumed_mah:.0f} mAh, "
         f"charger put back {charger_mah:.0f} mAh")
    return {'old': old, 'new': new, 'fc_consumed_mah': fc_consumed_mah,
            'charger_mah': charger_mah}


def is_armed():
    """Check if the vehicle is armed using a fresh heartbeat.

    Drains any stale queued heartbeats first, then waits for the next one
    from the FC so the result reflects current state, not buffered state.
    """
    try:
        with _mavlink_lock:
            conn = _connect()
    except Exception:
        return False
    # Drain anything already in the buffer so we read a fresh heartbeat
    deadline = time.time() + 3.0
    last = None
    while time.time() < deadline:
        msg = conn.recv_match(type='HEARTBEAT', blocking=False)
        if msg is None:
            if last is not None:
                # Buffer drained — last heartbeat is the freshest we have
                break
            # Nothing queued yet — block briefly for the next one
            msg = conn.recv_match(type='HEARTBEAT', blocking=True, timeout=0.5)
            if msg:
                last = msg
                # Keep draining in case more are queued
                continue
        else:
            last = msg
    if last is None:
        return False
    return (last.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0


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


def configure_battery_monitoring(n_cells=4, capacity_mah=5000, low_voltage=3.5, critical_voltage=3.3,
                                 fc_pins=None):
    """Configure ArduPilot battery monitoring and its battery failsafes.

    Deliberately does NOT write BATT_VOLT_PIN / BATT_CURR_PIN / BATT_VOLT_MULT /
    BATT_AMP_PERVLT unless fc_pins gives them: those are properties of the
    board and power module, and the board's firmware defaults are right far
    more often than any constant here. (This used to write the Pixhawk-1 +
    3DR power-module values 2/3/10.1/17.0 to every board, which leaves most
    modern FCs reading ~0 A - and a battery % that never moves.)
    fc_pins: optional {'volt_pin', 'curr_pin', 'volt_mult', 'amp_per_volt'}.
    Scale errors are better fixed with calibrate_voltage()/calibrate_current().
    """
    cfg, _, _ = _battery_models()
    print(f"Configuring battery monitoring: {n_cells}S, {capacity_mah}mAh...")

    params = {
        'BATT_CAPACITY': capacity_mah,
        # Loaded-voltage failsafes (the FC sees voltage under load in flight).
        'BATT_LOW_VOLT': round(n_cells * low_voltage, 2),
        'BATT_CRT_VOLT': round(n_cells * critical_voltage, 2),
        # mAh failsafes: RTL with reserve+5% of the pack left, land at critical.
        # Only meaningful with a working current sensor and a seeded FC counter
        # (seed_fc_battery_from_voltage); 0 would disable them.
        'BATT_LOW_MAH': int(capacity_mah * (cfg.landing_reserve_pct + 5.0) / 100.0),
        'BATT_CRT_MAH': int(capacity_mah * cfg.critical_pct / 100.0),
        # The FC's own refusal to arm on a low pack, from resting voltage.
        'BATT_ARM_VOLT': round(n_cells * _battery.cell_voltage_for_pct(cfg.min_takeoff_pct), 2),
    }
    if (_get_param('BATT_MONITOR') or 0) == 0:
        params['BATT_MONITOR'] = 4  # analog voltage + current; needs an FC reboot
    pin_keys = {'volt_pin': 'BATT_VOLT_PIN', 'curr_pin': 'BATT_CURR_PIN',
                'volt_mult': 'BATT_VOLT_MULT', 'amp_per_volt': 'BATT_AMP_PERVLT'}
    for key, name in pin_keys.items():
        if fc_pins and fc_pins.get(key) is not None:
            params[name] = fc_pins[key]

    for name, value in params.items():
        if _set_param(name, value):
            print(f"  Set {name} = {value}")
        else:
            print(f"  Warning: No ACK for {name}")

    print("Battery monitoring configured. Reboot the flight controller if "
          "BATT_MONITOR or a pin changed.")
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
        'BATT_FS_LOW_ACT': 2,    # RTL on low battery (ArduCopter: 1=Land, 2=RTL)
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
        critical_voltage=crit_volt,
        fc_pins=battery_config.get('fc_pins'),
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


def look_around(directions=4):
    """
    Rotate to N evenly-spaced headings and capture a photo at each.

    Returns a list of S3 URLs (or local paths if upload fails).
    Rotates relative to current heading so it always completes a full 360°.
    """
    urls = []
    angle_step = 360.0 / directions
    for i in range(directions):
        if i > 0:
            set_yaw(angle_step, relative=True)
            wait(1.5)  # stabilise after rotation
        url = capture_photo(upload=True)
        if url:
            urls.append(url)
    return urls


def _load_config():
    import yaml
    config_path = DRONE_DIR / 'config.yaml'
    if config_path.exists():
        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}


# Extension -> Content-Type. Kept explicit rather than using mimetypes so the
# set of things this SDK will upload is visible and reviewable in one place.
_CONTENT_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.mp4': 'video/mp4',
}


def _content_type_for(filename):
    return _CONTENT_TYPES.get(Path(filename).suffix.lower(), 'application/octet-stream')


def _media_key(conversation_id, filename):
    """Build the S3 key for a media object.

    This layout is a contract, not an implementation detail: the cloud's
    upload_url_handler builds the identical key, and generate_presigned_url
    parses it back into bucket+key by string splitting. Change it in one place
    only and the app silently gets dead links.
    """
    config = _load_config()
    drone_id = config.get('drone_id', 'unknown')
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'drones/{drone_id}/conversations/{conversation_id}/{timestamp}_{filename}'


def upload_photo(local_path, conversation_id):
    """
    Upload a photo and return its public URL - to S3 (control_plane: aws,
    default) or to a local Ground Control Station's HTTP API
    (control_plane: gcs, see eco/gcs/README.md).

    Thin wrapper over upload_media. Kept with this exact name and signature
    because it is named in aws/src/handler.py's system prompts and therefore
    appears verbatim in LLM-generated code.

    Args:
        local_path: Path to the local image file.
        conversation_id: Conversation ID for organizing uploads.

    Returns:
        Public URL of the uploaded image, or None if upload failed.
    """
    return upload_media(local_path, conversation_id)


def upload_media(local_path, conversation_id, content_type=None):
    """Upload any media file (photo or video) and return its public URL."""
    config = _load_config()
    filename = Path(local_path).name
    key = _media_key(conversation_id, filename)
    content_type = content_type or _content_type_for(filename)

    if config.get('control_plane') == 'gcs':
        return _upload_media_gcs(local_path, key, config.get('gcs', {}) or {}, content_type)
    return _upload_media_s3(local_path, key, content_type)


def upload_video_bytes(mp4_bytes, conversation_id, label='clip'):
    """Upload in-memory MP4 bytes and return the public URL.

    Bytes rather than a path because video_record.encode_mp4() returns bytes -
    writing them to a temp file just to read them back would add a failure mode
    (and the cleanup that capture_photo has to do) for nothing. Mirrors
    sim/cloud_creds.upload_mp4_to_s3 on the hardware path.
    """
    if not mp4_bytes:
        return None
    config = _load_config()
    key = _media_key(conversation_id, f'{label}.mp4')

    if config.get('control_plane') == 'gcs':
        return _upload_bytes_gcs(mp4_bytes, key, config.get('gcs', {}) or {}, 'video/mp4')

    s3, bucket = _get_s3()
    if s3 is None or bucket is None:
        print("Error: S3 not available")
        return None
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=mp4_bytes, ContentType='video/mp4')
        url = f'https://{bucket}.s3.amazonaws.com/{key}'
        print(f"Video uploaded: {url} ({len(mp4_bytes)} bytes)")
        return url
    except Exception as e:
        print(f"Error uploading video: {e}")
        return None


def _upload_media_s3(local_path, s3_key, content_type='image/jpeg'):
    s3, bucket = _get_s3()
    if s3 is None or bucket is None:
        print("Error: S3 not available")
        return None

    try:
        s3.upload_file(
            local_path,
            bucket,
            s3_key,
            ExtraArgs={'ContentType': content_type}
        )

        # Generate public URL
        url = f'https://{bucket}.s3.amazonaws.com/{s3_key}'
        print(f"Photo uploaded: {url}")

        return url

    except Exception as e:
        print(f"Error uploading photo: {e}")
        return None


def _upload_media_gcs(local_path, image_key, gcs_config, content_type='image/jpeg'):
    """HTTP PUT to the GCS's own /images/{key} route (gcs/http_api.py),
    authenticated with this drone's own pairing token - the GCS-mode
    counterpart of _upload_media_s3's IoT-role-credentialed S3 upload."""
    try:
        with open(local_path, 'rb') as f:
            return _upload_bytes_gcs(f.read(), image_key, gcs_config, content_type)
    except Exception as e:
        print(f"Error reading {local_path} for GCS upload: {e}")
        return None


def _upload_bytes_gcs(data, image_key, gcs_config, content_type):
    images_base_url = gcs_config.get('images_base_url')
    auth_token = gcs_config.get('auth_token')
    if not images_base_url or not auth_token:
        print("Error: gcs.images_base_url / gcs.auth_token not configured for control_plane: gcs")
        return None

    import urllib.request

    url = f"{images_base_url.rstrip('/')}/images/{image_key}"
    try:
        req = urllib.request.Request(url, data=data, method='PUT')
        req.add_header('Content-Type', content_type)
        req.add_header('Authorization', f'Bearer {auth_token}')
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                print(f"Error uploading media to GCS: HTTP {resp.status}")
                return None
        print(f"Media uploaded: {url}")
        return url
    except Exception as e:
        print(f"Error uploading media to GCS: {e}")
        return None


# ---------------------------------------------------------------------------
# Recording — capture that runs while the vehicle moves.
#
# These live here, not in LLM-generated code, because daemon.execute_code's
# deny-list blocks file writes (\bopen\s*\([^)]*['"][wa]) and subprocess use.
# Anything that touches the disk has to be an SDK verb.
#
# One recorder per process: the camera is a single exclusive device, so a
# second concurrent recording is a bug in the caller, not a use case.
# ---------------------------------------------------------------------------
_recorder = None


def _sdk_frame_source():
    """Frame source for the SDK's own recordings: the SDK camera directly.

    The mission loop passes backends.Backend.capture_frame instead, which
    prefers PerceptionService. This path is for the codegen route, which has no
    backend object.
    """
    camera = _get_camera()
    if camera is None:
        return None
    return camera.get_frame(timeout_ms=500)


def start_recording(mode="video", fps=6.0, interval_s=3.0, max_seconds=120.0):
    """Start recording in the background and return immediately.

    Flight commands issued after this run while the recording continues. Call
    stop_recording() to finish and upload. mode is "video" (one MP4) or
    "photos" (a still every interval_s).

    Returns True if recording started, False if one was already running.
    """
    global _recorder
    if _recorder is not None and _recorder.is_recording:
        print("Warning: already recording; ignoring start_recording()")
        return False
    # In the air a recording is paid for in hover time; on the ground it's free.
    if _battery_gates_apply():
        _, _, gov = _battery_models()
        ok, reason = check_battery_for("start_recording", gov.hover_cost(max_seconds))
        if not ok:
            _log(f"RECORDING REFUSED: {reason}")
            return False
    from media_recorder import MediaRecorder

    _recorder = MediaRecorder(
        _sdk_frame_source,
        mode=mode,
        fps=fps,
        interval_s=interval_s,
        max_seconds=max_seconds,
    )
    _recorder.start()
    return True


def stop_recording(conversation_id=None):
    """Stop the recording, encode, upload, and return the list of URLs.

    Safe to call when nothing is recording (returns []), because the mission
    loop's cleanup path calls it unconditionally.
    """
    global _recorder
    if _recorder is None:
        return []
    recorder = _recorder
    _recorder = None

    artifacts = recorder.stop()
    # Hand the camera back so the WebRTC producer can reclaim it, exactly as
    # capture_photo does after a single still.
    release_camera()
    if not artifacts:
        if recorder.error:
            print(f"Recording produced nothing: {recorder.error}")
        return []

    if conversation_id is None:
        conversation_id = globals().get('CONVERSATION_ID') or os.environ.get('CONVERSATION_ID')
    if not conversation_id:
        import uuid
        conversation_id = f"recordings/{uuid.uuid4().hex[:8]}"

    urls = []
    for artifact in artifacts:
        if isinstance(artifact, (bytes, bytearray)):
            url = upload_video_bytes(bytes(artifact), conversation_id)
        else:
            url = upload_media(str(artifact), conversation_id)
            try:
                os.remove(str(artifact))
            except OSError:
                pass
        if url:
            urls.append(url)
    return urls


def record_video(seconds=10.0, fps=6.0):
    """Record a fixed-length clip here and now, upload it, return the URL.

    The blocking one-shot form, for "take a 5 second video" with no flying in
    between. Mirrors sim_sdk.record_video. Use start_recording/stop_recording
    when the capture has to overlap a flight.
    """
    if not start_recording(mode="video", fps=fps, max_seconds=seconds):
        return None
    wait(seconds)
    urls = stop_recording()
    return urls[0] if urls else None


def is_recording():
    """True while a recording started by start_recording() is still running."""
    return _recorder is not None and _recorder.is_recording
