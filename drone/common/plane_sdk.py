"""
Plane SDK - High-level interface for ArduPlane (fixed-wing) via pymavlink

Sibling to drone_sdk.py (ArduCopter). Fixed-wing flight is NOT a variant of the
copter primitives — it cannot hover, cannot yaw-in-place, cannot "land" by cutting
throttle in place, and is hand/bungee-launched rather than vertically taken off.
Every primitive here reflects that; do not reuse drone_sdk.arm()/takeoff()/land()/
set_velocity()/set_yaw() for a plane (see eco/CLAUDE.md-adjacent plan notes).

Reuses drone_sdk's connection/telemetry plumbing directly (_connect, _mav_send,
_mav_command, is_armed, get_position, get_attitude, get_battery, get_telemetry,
_set_param, disconnect) since those are airframe-agnostic — only the flight
primitives below are plane-specific. drone_sdk.goto() is ALSO reused as-is:
SET_POSITION_TARGET_GLOBAL_INT in GUIDED mode is genuinely airframe-agnostic —
for a plane, ArduPlane's GUIDED mode flies to and loiters over that point using
whatever loiter radius is currently configured (WP_LOITER_RAD), which is exactly
what orbit() below sets before calling it.

SAFETY / STATUS: this module is UNFLOWN. Parameter names below (TKOFF_*, FENCE_*,
RTL_*, WP_LOITER_RAD) are standard ArduPlane parameters as of recent firmware, but
have not been verified against the specific ArduPlane version running on this
aircraft's Pixhawk 6X. Per the fixed-wing autonomy plan (M0), this module must be
validated in ArduPlane SITL (sitl_plane.py) before ANY real-hardware use, and the
mission-item / parameter set should be re-checked against the actual firmware's
parameter list (`param show TKOFF_*` etc. via MAVProxy/QGC) before a real flight.

Units: distances/altitudes in meters, speeds in m/s, angles in degrees unless
named _rad. Altitudes are relative-to-home (AGL at launch point) unless noted.
"""

import time
import math
from pymavlink import mavutil

from drone_sdk import (  # noqa: F401 - re-exported for callers of this module
    _connect, _mav_send, _mav_recv, _mav_recv_any, _mav_command,
    _mavlink_lock, _log, _clamp, _set_param, _drain_statustext,
    _wait_for_mode, _wait_for_disarm, _check_preflight_status,
    disconnect, is_armed, get_position, get_attitude, get_battery,
    get_telemetry, get_flight_mode, goto,
)

# =============================================================================
# SAFETY LAYER 2 (plane): airspeed/altitude limits
# A fixed-wing cannot hover or slow to a stop — MIN_AIRSPEED is a hard floor,
# never a target to clamp toward zero the way copter velocity commands can.
# =============================================================================
MIN_AIRSPEED = 12.0      # m/s — below this the X8 risks stalling (verify per-airframe)
MAX_AIRSPEED = 25.0      # m/s
MIN_ALTITUDE = 15.0      # m AGL — never command below this in flight
MAX_ALTITUDE = 120.0     # m AGL
DEFAULT_LOITER_RADIUS = 60.0   # m — see turn-radius guidance in the plan; ~40-60m demo default
MIN_LOITER_RADIUS = 30.0       # m — do not go tighter without dedicated stall-margin testing

# ArduPlane fixed-wing custom_mode numbers (MAV_MODE_FLAG_CUSTOM_MODE_ENABLED set),
# for logging/diagnostics only — mode changes below use mavutil's mode_mapping()
# (name -> number) resolved against the connected FC, not these hardcoded values.
PLANE_MODES = {
    0: "MANUAL", 1: "CIRCLE", 2: "STABILIZE", 3: "TRAINING", 4: "ACRO",
    5: "FBWA", 6: "FBWB", 7: "CRUISE", 8: "AUTOTUNE", 10: "AUTO",
    11: "RTL", 12: "LOITER", 13: "TAKEOFF", 15: "GUIDED", 17: "QSTABILIZE",
    18: "QHOVER", 19: "QLOITER", 20: "QLAND", 21: "QRTL",
}

MAV_MISSION_ACCEPTED = 0


# =============================================================================
# Mission-item upload (MISSION_ITEM_INT protocol) — needed for the landing
# approach below (land()). ArduPlane has no standalone "land" flight mode the
# way ArduCopter does; it's an AUTO-mode NAV_LAND mission item. (Launch uses
# ArduPlane's native TAKEOFF mode instead — no mission upload needed there,
# see hand_launch()'s docstring for why.)
# =============================================================================

def _upload_mission(items):
    """Upload a full mission (list of dicts) via the standard MISSION_COUNT /
    MISSION_REQUEST / MISSION_ITEM_INT / MISSION_ACK exchange. Blocking.

    Each item dict: {frame, command, current, autocontinue, param1..4, x, y, z}.
    x/y are lat/lon *1e7 int (MISSION_ITEM_INT convention) or 0 for non-nav
    commands; z is altitude in meters (relative frame assumed, see item['frame']).

    Returns True if the FC ACKs MAV_MISSION_ACCEPTED, False otherwise.
    """
    with _mavlink_lock:
        m = _connect()
        m.mav.mission_count_send(
            m.target_system, m.target_component, len(items),
            mavutil.mavlink.MAV_MISSION_TYPE_MISSION)

        sent = set()
        deadline = time.time() + 10
        while time.time() < deadline and len(sent) < len(items):
            msg = m.recv_match(
                type=['MISSION_REQUEST_INT', 'MISSION_REQUEST', 'MISSION_ACK'],
                blocking=True, timeout=1.0)
            if msg is None:
                continue
            if msg.get_type() == 'MISSION_ACK':
                # FC bailed early (e.g. rejected count) — treat as failure.
                return msg.type == MAV_MISSION_ACCEPTED and len(sent) == len(items)
            seq = msg.seq
            if seq in sent or seq >= len(items):
                continue
            it = items[seq]
            m.mav.mission_item_int_send(
                m.target_system, m.target_component, seq,
                it.get('frame', mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT),
                it['command'], it.get('current', 0), it.get('autocontinue', 1),
                it.get('param1', 0), it.get('param2', 0), it.get('param3', 0),
                it.get('param4', 0), it.get('x', 0), it.get('y', 0), it.get('z', 0),
                mavutil.mavlink.MAV_MISSION_TYPE_MISSION)
            sent.add(seq)

        # Final MISSION_ACK confirming the whole upload.
        ack_deadline = time.time() + 5
        while time.time() < ack_deadline:
            msg = m.recv_match(type='MISSION_ACK', blocking=True, timeout=0.5)
            if msg:
                return msg.type == MAV_MISSION_ACCEPTED
        return len(sent) == len(items)  # best-effort: all items sent, no explicit ACK seen


def _set_current_mission_item(seq=0):
    _mav_send(lambda m: m.mav.mission_set_current_send(
        m.target_system, m.target_component, seq))


def _set_mode(mode_name):
    """Set flight mode by name, resolved against the connected FC's own mode map
    (not the PLANE_MODES table above, which is diagnostic-only)."""
    m = _connect()
    mapping = m.mode_mapping()
    if mapping is None or mode_name not in mapping:
        _log(f"ERROR: mode '{mode_name}' not in FC's mode map ({mapping})")
        return False
    _mav_send(lambda m: m.set_mode(mapping[mode_name]))
    return _wait_for_mode(mode_name, timeout=5)


# =============================================================================
# Hand-launch takeoff
# =============================================================================

def configure_launch_detection(min_accel_mss=15.0, min_airspeed_mps=8.0,
                                max_launch_throttle_pct=100):
    """Set ArduPlane's hand/bungee-launch detection thresholds (TKOFF_* params).

    min_accel_mss: forward acceleration (m/s^2) that counts as "being thrown" —
        ArduPlane won't apply throttle/start the takeoff mission item until this
        (or the airspeed threshold) is exceeded, so a stationary armed aircraft
        doesn't spin up its prop unexpectedly.
    min_airspeed_mps: airspeed-based alternative/backup launch trigger.

    MUST be tuned per-airframe/launch-method (hand throw vs bungee) before a real
    flight — these are reasonable starting points, not verified for this X8 build.
    """
    ok = True
    ok &= _set_param('TKOFF_THR_MINACC', min_accel_mss)
    ok &= _set_param('TKOFF_THR_MINSPD', min_airspeed_mps)
    ok &= _set_param('TKOFF_THR_MAX', max_launch_throttle_pct)
    return ok


def hand_launch(altitude_m, pitch_deg=15.0, loiter_distance_m=200.0,
                 launch_timeout_s=30, climb_timeout_s=60):
    """Arm for a hand/bungee launch and climb to altitude_m (relative), using
    ArduPlane's native TAKEOFF flight mode.

    Real ArduPlane hand-launch flow (NOT a copter-style vertical takeoff):
      1. Set TAKEOFF-mode params (TKOFF_ALT/TKOFF_DIST/TKOFF_LVL_PITCH), switch
         to TAKEOFF mode, arm.
      2. ArduPilot will NOT apply throttle until launch is detected — this is
         the SAME underlying gate (suppress_throttle()/auto_takeoff_check(),
         driven by configure_launch_detection()'s TKOFF_THR_MINACC/MINSPD) as
         the classic AUTO+NAV_TAKEOFF-mission-item approach; TAKEOFF mode does
         NOT relax or bypass launch detection (confirmed by reading ArduPlane's
         servos.cpp — both paths call the identical suppress_throttle() gate).
         Its advantage here is purely operational: no MISSION_COUNT/REQUEST/
         ITEM upload round-trip needed over a real companion-computer link —
         one mode-set + a few params, fewer failure points. (An earlier version
         of this function used the mission-upload approach; switched after
         confirming in ArduPlane's own source that it bought nothing for
         launch-detection reliability, only complexity — see the fixed-wing
         autonomy plan's M0 notes.)
      3. Physically throw/launch the aircraft. FC detects real acceleration/
         airspeed via that gate, begins the takeoff climb-out automatically.
      4. Poll relative altitude until it reaches ~95% of altitude_m. TAKEOFF
         mode then loiters at loiter_distance_m from the launch point (using
         WP_LOITER_RAD, same as orbit() below) once altitude is reached.

    Returns True if altitude was reached within climb_timeout_s, False otherwise
    (caller should treat False as an abort — do not proceed to further commands).
    """
    altitude_m = _clamp(altitude_m, MIN_ALTITUDE, MAX_ALTITUDE, "altitude")
    _log(f"Configuring TAKEOFF mode (climb to {altitude_m}m, "
         f"loiter {loiter_distance_m}m out, pitch {pitch_deg} deg)...")

    ok = True
    ok &= _set_param('TKOFF_ALT', altitude_m)
    ok &= _set_param('TKOFF_DIST', loiter_distance_m)
    ok &= _set_param('TKOFF_LVL_PITCH', pitch_deg)
    if not ok:
        _log("WARNING: one or more TAKEOFF-mode params not ACKed")

    _log("Running preflight checks...")
    ok, issues = _check_preflight_status()
    if not ok:
        for issue in issues:
            _log(f"  WARNING: {issue}")

    _log("Setting TAKEOFF mode...")
    if not _set_mode('TAKEOFF'):
        _log("ERROR: failed to enter TAKEOFF mode")
        return False

    if is_armed():
        _log("Already armed, skipping arm step")
    else:
        _log(f"Arming (launch detection: waiting for throw/acceleration, "
             f"timeout {launch_timeout_s}s)...")
        ack_ok, result = _mav_command(
            lambda m: m.mav.command_long_send(
                m.target_system, m.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0, 1, 0, 0, 0, 0, 0, 0), 400)
        if not ack_ok:
            _log("Normal arm failed, trying force-arm...")
            ack_ok, result = _mav_command(
                lambda m: m.mav.command_long_send(
                    m.target_system, m.target_component,
                    mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                    0, 1, 21196, 0, 0, 0, 0, 0), 400)
        if not ack_ok:
            _log(f"Arm failed! (result={result})")
            _drain_statustext()
            return False
        _log("Armed — waiting for launch (throw the aircraft now)...")

    # Wait for the FC to detect the throw and begin climbing (relative_alt rising).
    t0 = time.time()
    launched = False
    while time.time() - t0 < launch_timeout_s:
        msg = _mav_recv('GLOBAL_POSITION_INT', timeout=0.5)
        if msg and msg.relative_alt / 1000.0 > 1.5:
            launched = True
            break
    if not launched:
        _log("ERROR: no climb detected within launch_timeout_s — launch not "
             "registered (check TKOFF_THR_MINACC/MINSPD and that the aircraft "
             "was actually thrown)")
        return False

    _log(f"Launch detected, climbing to {altitude_m}m...")
    t0 = time.time()
    while time.time() - t0 < climb_timeout_s:
        msg = _mav_recv('GLOBAL_POSITION_INT', timeout=0.5)
        if msg and msg.relative_alt / 1000.0 >= altitude_m * 0.95:
            _log(f"Reached {altitude_m}m")
            return True
    _log("WARNING: climb timeout before reaching target altitude")
    return False


# =============================================================================
# Orbit (circle a point) — a fixed-wing cannot loiter via a single "hold here"
# command the way a copter can; it flies a continuous circle of a set radius.
# =============================================================================

def orbit(lat, lon, radius_m=DEFAULT_LOITER_RADIUS, alt_m=None, clockwise=True):
    """Command a loiter circle centered on (lat, lon).

    Sets WP_LOITER_RAD (ArduPlane's convention: sign of the radius parameter
    encodes direction — negative = counter-clockwise), then reuses drone_sdk's
    goto() to command GUIDED flight to the center point. ArduPlane's GUIDED
    mode flies to and then loiters around a commanded point using whatever
    WP_LOITER_RAD is currently set — this is standard, well-documented ArduPlane
    companion-computer behavior, not a bespoke primitive.

    radius_m is clamped to >= MIN_LOITER_RADIUS (see the plan's turn-radius
    analysis — tighter turns risk stalling depending on bank-angle limits and
    airspeed; do not fly below this without dedicated bench/SITL validation of
    this airframe's actual stall margin at the desired bank angle).
    alt_m: relative altitude to hold; if None, holds current altitude.
    """
    radius_m = max(radius_m, MIN_LOITER_RADIUS)
    signed_radius = radius_m if clockwise else -radius_m
    if not _set_param('WP_LOITER_RAD', signed_radius):
        _log("WARNING: WP_LOITER_RAD not ACKed — loiter radius may not be set correctly")

    if not _set_mode('GUIDED'):
        _log("ERROR: failed to enter GUIDED mode for orbit")
        return False

    if alt_m is None:
        _, _, alt_m = get_position()
    alt_m = _clamp(alt_m, MIN_ALTITUDE, MAX_ALTITUDE, "altitude")
    goto(lat, lon, alt_m)
    _log(f"Orbiting ({lat}, {lon}) radius={radius_m}m alt={alt_m}m "
         f"({'CW' if clockwise else 'CCW'})")
    return True


def wait_for_orbit_laps(n_laps, timeout_s=180):
    """Block until the aircraft has completed n_laps full circles (or timeout).

    ArduPlane's GUIDED loiter gives no direct "laps completed" telemetry, so this
    infers it from unwrapped heading change: accumulate signed yaw deltas from
    ATTITUDE messages and count full 360-degree rotations. Best-effort — GPS/wind
    drift can make the true ground-track lap count differ slightly from the
    heading-based count; treat as approximate for coverage/counting purposes
    (matching the "count ± uncertainty" honesty elsewhere in this plan).

    Returns True if n_laps were completed within timeout_s, False on timeout.
    """
    accumulated_deg = 0.0
    last_yaw = None
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        msg = _mav_recv('ATTITUDE', timeout=0.5)
        if msg is None:
            continue
        yaw_deg = math.degrees(msg.yaw)
        if last_yaw is not None:
            delta = yaw_deg - last_yaw
            # unwrap to [-180, 180]
            while delta > 180:
                delta -= 360
            while delta < -180:
                delta += 360
            accumulated_deg += delta
        last_yaw = yaw_deg
        if abs(accumulated_deg) >= n_laps * 360.0:
            return True
    return False


# =============================================================================
# Landing approach — AUTO-mode NAV_LAND mission item, NOT a copter "LAND mode".
# A flying wing lands via a straight-in glide approach into wind; it cannot
# choose an arbitrary touchdown point. Caller must supply an approach point and
# heading (into-wind heading is a real-world decision made at flight time from
# a wind reading — this module cannot infer wind and will not guess it).
# =============================================================================

def land(approach_lat, approach_lon, heading_deg, alt_m=0.0, glide_start_alt_m=30.0):
    """Fly an AUTO-mode landing approach and belly-land.

    approach_lat/lon: point on the extended runway/approach centerline (i.e.
        the point the aircraft should be flying toward as it descends) — pick
        this so the line from a point behind it, through it, points into wind.
    heading_deg: the approach/landing heading (should be into wind).
    alt_m: touchdown altitude (0.0 = ground level relative to home, if the
        landing site is at the same elevation as the launch point — set
        otherwise if not).
    glide_start_alt_m: altitude of a DO_LAND_START point placed behind the
        approach point, giving the aircraft room to establish the final glide
        slope before touchdown (not a sharp dive to ground level).
    """
    frame = mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT
    # Back the DO_LAND_START/approach point off along the reciprocal of heading_deg
    # so there's a real glide slope, not an instantaneous altitude drop at the
    # landing point itself. ~1 degree latitude ~= 111,320m; good enough locally.
    back_off_m = 150.0
    brg_rad = math.radians((heading_deg + 180.0) % 360.0)
    dlat = (back_off_m * math.cos(brg_rad)) / 111320.0
    dlon = (back_off_m * math.sin(brg_rad)) / (111320.0 * math.cos(math.radians(approach_lat)))
    start_lat, start_lon = approach_lat + dlat, approach_lon + dlon

    items = [
        {
            'frame': frame, 'command': mavutil.mavlink.MAV_CMD_DO_LAND_START,
            'current': 1, 'autocontinue': 1,
            'x': int(start_lat * 1e7), 'y': int(start_lon * 1e7), 'z': glide_start_alt_m,
        },
        {
            'frame': frame, 'command': mavutil.mavlink.MAV_CMD_NAV_LAND,
            'current': 0, 'autocontinue': 1,
            'param4': heading_deg,
            'x': int(approach_lat * 1e7), 'y': int(approach_lon * 1e7), 'z': alt_m,
        },
    ]
    _log(f"Uploading landing approach (heading {heading_deg} deg into wind)...")
    if not _upload_mission(items):
        _log("ERROR: landing mission upload not accepted")
        return False
    _set_current_mission_item(0)

    if not _set_mode('AUTO'):
        _log("ERROR: failed to enter AUTO mode for landing")
        return False

    _log("Landing approach commanded — waiting for touchdown/disarm...")
    return _wait_for_disarm(timeout=90)


def rtl(wait_for_loiter_s=10):
    """Return-to-launch. Sets RTL mode; ArduPlane loiters at the home point at
    RTL_ALTITUDE by default (does NOT auto-land unless RTL_AUTOLAND is
    configured on this specific airframe/firmware — verify before relying on
    it to land itself). Use land() explicitly afterward for a controlled
    approach, per the plan's "come back == RTL, landing is separate" design.
    """
    if not _set_mode('RTL'):
        _log("ERROR: failed to enter RTL mode")
        return False
    time.sleep(wait_for_loiter_s)
    return True


def get_wind_estimate(timeout=2.0):
    """Read ArduPlane's live wind estimate via the MAVLink WIND message, if the
    FC is producing one (needs an airspeed sensor + EKF wind estimation).

    Returns (direction_from_deg, speed_mps) — direction is where the wind is
    blowing FROM (0=N), matching the convention callers need to compute an
    into-wind approach heading (approach_heading = (direction_from_deg + 180)
    % 360). Returns None if no WIND message arrives within `timeout` — callers
    (HardwareBackend.land()) must NOT fabricate a heading in that case; a
    genuinely unknown wind means a genuinely unknown safe landing direction.
    """
    msg = _mav_recv('WIND', timeout=timeout)
    if msg is None:
        return None
    return (float(msg.direction), float(msg.speed))


# =============================================================================
# Safety envelope: geofence + hard altitude floor. This is the real guarantee
# behind "climb over the trees" — perception only *triggers* the climb; the FC
# enforces the floor regardless of what the companion computer decides.
# =============================================================================

def configure_safety_envelope(fence_radius_m, fence_max_alt_m, min_alt_floor_m,
                               action='RTL'):
    """Configure ArduPilot's own geofence + altitude floor, independent of any
    companion-computer logic. Per the plan: "autonomy only biases within an
    envelope the flight controller enforces" — this is that envelope.

    fence_radius_m/fence_max_alt_m: standard ArduPlane polygon/circle+altitude
        fence (FENCE_*).
    min_alt_floor_m: minimum altitude fence (requires a firmware version with
        FENCE_TYPE altitude-min support; verify on this Pixhawk 6X's ArduPlane
        version before relying on it — older firmware only supports max-alt).
    action: 'RTL' (default, safest) or 'REPORT' — verify FENCE_ACTION's valid
        values against the connected firmware.
    """
    action_map = {'REPORT': 0, 'RTL': 1, 'RTL_OR_LAND': 4}
    ok = True
    ok &= _set_param('FENCE_ENABLE', 1)
    ok &= _set_param('FENCE_TYPE', 1 | 2 | 4)  # bitmask: altitude | circle | polygon (verify)
    ok &= _set_param('FENCE_RADIUS', fence_radius_m)
    ok &= _set_param('FENCE_ALT_MAX', fence_max_alt_m)
    ok &= _set_param('FENCE_ALT_MIN', min_alt_floor_m)
    ok &= _set_param('FENCE_ACTION', action_map.get(action, 1))
    if not ok:
        _log("WARNING: one or more geofence parameters not ACKed — do not fly "
             "until this is resolved (silent no-ack here means the fence may "
             "not be enforced)")
    return ok
