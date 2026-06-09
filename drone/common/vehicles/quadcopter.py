"""
Quadcopter vehicle SDK — ArduPilot / MAVLink flight stack.

All flight-specific operations: arm, takeoff, land, velocity, yaw,
ceiling guard, motor test, failsafe configuration.
"""

import time
import threading
from pymavlink import mavutil

from .mavlink import (
    _log, _clamp,
    MAX_VELOCITY, MAX_ALTITUDE, MIN_ALTITUDE, MAX_YAW_RATE,
    _mavlink_lock, _connect,
    _mav_send, _mav_recv, _mav_recv_any, _mav_command,
    _wait_for_ack, _drain_statustext, _wait_for_mode, _wait_for_disarm,
    _set_param,
    is_armed, get_position, get_attitude, get_battery, get_telemetry,
    get_flight_mode, wait,
)

# Ceiling guard state
_ceiling_guard_thread = None
_ceiling_guard_stop = threading.Event()


# =============================================================================
# Arming / disarming
# =============================================================================

def arm():
    """Arm the drone motors. Returns True only if actually armed."""
    _log("Arming...")

    if is_armed():
        _log("Already armed!")
        return True

    _mav_send(lambda m: m.mav.param_set_send(
        m.target_system, m.target_component,
        b"DISARM_DELAY", 30.0, mavutil.mavlink.MAV_PARAM_TYPE_INT8))

    _mav_send(lambda m: m.set_mode(0))  # STABILIZE
    time.sleep(0.5)

    def send_arm(m):
        _log(f"Sending arm to system {m.target_system}, component {m.target_component}")
        m.mav.command_long_send(
            m.target_system, m.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0, 1, 21196, 0, 0, 0, 0, 0
        )

    ack_ok, result = _mav_command(send_arm, 400)

    result_names = {0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED",
                    3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS"}

    if result is None:
        _log("Arm command not acknowledged - checking FC status...")
        for msg in _drain_statustext(timeout=1):
            _log(f"  FC: {msg}")
        return False

    _log(f"Arm ACK: {result_names.get(result, result)}")

    if not ack_ok:
        _log("Arm command rejected by FC")
        for msg in _drain_statustext(timeout=1):
            _log(f"  FC: {msg}")
        return False

    _log("Waiting for motors...")
    time.sleep(2)

    if is_armed():
        _log("Armed confirmed!")
        return True
    else:
        _log("Arm ACK'd but not armed - FC may have auto-disarmed")
        for msg in _drain_statustext(timeout=1):
            _log(f"  FC: {msg}")
        return False


def disarm():
    """Disarm the drone motors."""
    print("Disarming...")
    _mav_send(lambda m: m.mav.command_long_send(
        m.target_system, m.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 0, 21196, 0, 0, 0, 0, 0
    ))
    success = _wait_for_ack(400)
    print("Disarmed!" if success else "Disarm failed!")
    return success


def safe_disarm():
    """Disarm only when on the ground (relative altitude < 1 m)."""
    _mav_send(lambda m: m.mav.request_data_stream_send(
        1, 1, mavutil.mavlink.MAV_DATA_STREAM_POSITION, 4, 1))
    start = time.time()
    rel_alt = None
    while time.time() - start < 3:
        msg = _mav_recv('GLOBAL_POSITION_INT', timeout=0.5)
        if msg:
            rel_alt = msg.relative_alt / 1000.0
            break
    if rel_alt is not None and rel_alt > 1.0:
        raise RuntimeError(
            f"safe_disarm() refused: drone is {rel_alt:.1f}m above ground — call land() first")
    return disarm()


def stop():
    """For a quadcopter, stop means land."""
    land()


# =============================================================================
# Flight commands
# =============================================================================

def takeoff(altitude_m):
    """Take off to specified altitude (m). Arms automatically if needed."""
    altitude_m = _clamp(altitude_m, MIN_ALTITUDE, MAX_ALTITUDE, "altitude")
    _log(f"Taking off to {altitude_m}m...")

    _drain_statustext(timeout=0.5, print_msgs=False)

    _log("Setting GUIDED mode...")
    _mav_send(lambda m: m.set_mode('GUIDED'))
    if not _wait_for_mode('GUIDED', timeout=5):
        _log("ERROR: Failed to enter GUIDED mode")
        _drain_statustext()
        return False

    _log("Running preflight checks...")
    ok, issues = _check_preflight_status()
    if not ok:
        for issue in issues:
            _log(f"  WARNING: {issue}")
        _log("Continuing despite warnings...")

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
            _log("ERROR: Arm ACK'd but drone not armed!")
            _drain_statustext()
            return False
        _log("Armed confirmed!")

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
    """Set LAND mode and wait for auto-disarm."""
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
    """Fly to GPS coordinates. Altitude clamped to safe range."""
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
    """Set body-frame velocity. vx=forward, vy=right, vz=down (m/s)."""
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


# =============================================================================
# Preflight checks
# =============================================================================

def _check_preflight_status():
    """Check GPS and EKF status. Returns (ok, issues)."""
    issues = []

    _mav_send(lambda m: m.mav.request_data_stream_send(
        m.target_system, m.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS, 4, 1
    ))

    msg = _mav_recv('GPS_RAW_INT', timeout=2)
    if msg:
        fix_types = {0: "No GPS", 1: "No Fix", 2: "2D Fix", 3: "3D Fix",
                     4: "DGPS", 5: "RTK Float", 6: "RTK Fixed"}
        fix = fix_types.get(msg.fix_type, f"Unknown({msg.fix_type})")
        print(f"GPS: {fix}, {msg.satellites_visible} satellites")
        if msg.fix_type < 3:
            issues.append(f"GPS not ready: {fix}")
    else:
        issues.append("GPS: No data received")

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

    return len(issues) == 0, issues


# =============================================================================
# Ceiling guard
# =============================================================================

def get_ceiling_distance():
    """Distance to ceiling via upward rangefinder (m), or None if unavailable."""
    start = time.time()
    while time.time() - start < 2:
        msg = _mav_recv('DISTANCE_SENSOR', timeout=0.5)
        if msg and msg.orientation == 25:  # MAV_SENSOR_ROTATION_PITCH_90
            if msg.current_distance < msg.max_distance:
                return msg.current_distance / 100.0
    return None


def _ceiling_guard_loop(min_clearance):
    global _ceiling_guard_stop
    _log(f"Ceiling guard started (min clearance={min_clearance}m)")
    clamped = False

    while not _ceiling_guard_stop.is_set():
        try:
            with _mavlink_lock:
                conn = _connect()
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
                    time.sleep(0.1)
                    continue

                clearance = dist_msg.current_distance / 100.0

                if clearance < min_clearance:
                    if not clamped:
                        _log(f"CEILING GUARD: {clearance:.2f}m clearance — holding altitude")
                        clamped = True
                    pos = conn.recv_match(type='GLOBAL_POSITION_INT', blocking=True, timeout=0.3)
                    if pos:
                        conn.mav.set_position_target_global_int_send(
                            0,
                            conn.target_system, conn.target_component,
                            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                            0b0000111111111000,
                            pos.lat, pos.lon, pos.relative_alt / 1000.0,
                            0, 0, 0, 0, 0, 0, 0, 0
                        )
                else:
                    if clamped:
                        _log(f"CEILING GUARD: clearance restored ({clearance:.2f}m), resuming")
                        clamped = False

        except Exception as e:
            _log(f"Ceiling guard error: {e}")

        time.sleep(0.1)

    _log("Ceiling guard stopped")


def start_ceiling_guard(min_clearance=0.5):
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
    global _ceiling_guard_stop
    _ceiling_guard_stop.set()


# =============================================================================
# Motor test
# =============================================================================

def motor_test(motor_num=None, throttle_pct=15, duration_sec=2):
    """Test motor(s) without arming. motor_num=1-4 or None for all sequential."""
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
                print(f"Motor {motor_num} test FAILED: {MAV_RESULT_NAMES.get(msg.result, msg.result)}")
                status_msg = _mav_recv('STATUSTEXT', timeout=0.5)
                if status_msg:
                    print(f"  Reason: {status_msg.text}")
            break

    if not ack_received:
        print(f"Motor {motor_num} test: No ACK received")
        time.sleep(duration_sec + 1)


# =============================================================================
# Battery and failsafe configuration
# =============================================================================

def configure_battery_monitoring(n_cells=4, capacity_mah=5000, low_voltage=3.5, critical_voltage=3.3):
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
    """Configure flight controller failsafes for autonomous operation."""
    print("=" * 50)
    print("Configuring flight controller failsafes...")

    failsafe_params = {
        'FS_GCS_ENABLE': 1,
        'FS_THR_ENABLE': 3,
        'FS_THR_VALUE': 975,
        'BATT_FS_LOW_ACT': 2,
        'BATT_FS_CRT_ACT': 1,
        'LAND_DISARMDELAY': 2,
        'FS_EKF_ACTION': 1,
        'FS_EKF_THRESH': 0.8,
    }
    for name, value in failsafe_params.items():
        if _set_param(name, value):
            print(f"  Set {name} = {value}")
        else:
            print(f"  Warning: No ACK for {name}")

    _mav_send(lambda m: m.mav.command_long_send(
        m.target_system, m.target_component,
        mavutil.mavlink.MAV_CMD_PREFLIGHT_STORAGE, 0, 1, 0, 0, 0, 0, 0, 0
    ))
    time.sleep(1)
    print("Failsafes configured.")
    return True


def setup_drone():
    """Initial setup: configure battery monitoring. Call once on a new build."""
    import yaml
    from .mavlink import DRONE_DIR

    print("=" * 50)
    print("DRONE SETUP")
    print("=" * 50)

    config_path = DRONE_DIR / 'config.yaml'
    config = {}
    if config_path.exists():
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f) or {}

    battery_config = config.get('battery', {})
    configure_battery_monitoring(
        n_cells=battery_config.get('cells', 4),
        capacity_mah=battery_config.get('capacity_mah', 5000),
        low_voltage=battery_config.get('low_voltage_per_cell', 3.5),
        critical_voltage=battery_config.get('critical_voltage_per_cell', 3.3),
    )

    print("=" * 50)
    print("Setup complete! Reboot the flight controller.")
    print("=" * 50)
