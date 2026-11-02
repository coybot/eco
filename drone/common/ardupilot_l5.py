"""ArduPilot adapters for the on-device L5 runtime (``onboard_l5``).

Implements the telemetry, sensor and actuator provider Protocols against an
ArduCopter quadcopter or an ArduRover rover, so ``OnboardL5Runtime`` can fly the
real vehicles with the sim-validated controller.

**One MAVLink connection.** The flight controller's serial port is exclusive and
``drone_sdk`` already owns it, so nothing here opens a connection. Everything goes
through drone_sdk's own thread-safe helpers: ``_mav_send`` to send, ``_fc_cached``
to read the newest message of a type from the autopilot's system id, ``_pump`` to
parse what is waiting on the link. Those are private to drone_sdk; they are used
rather than copied so the locking and the shared-sysid heartbeat handling stay in
one place. Tests pass a fake with the same few attributes as ``sdk=``.

**No pymavlink import.** The handful of MAVLink constants needed are spelled out
below (they are fixed by the MAVLink common spec / ArduPilot dialect), so this
module and its tests import without pymavlink, as CI runs.

Frames (see onboard_l5's module docstring for the controller side):
  * MAVLink LOCAL_POSITION_NED is metres north/east/down of the EKF ORIGIN, which
    is where the EKF first got a position, not where the vehicle took off. The
    world frame here is ENU metres from the TAKEOFF point (team_link's pos_enu,
    drone_sdk.get_local_pose), so the takeoff point is located in the EKF frame
    once (``ArduPilotTelemetry.resolve_origin``) and subtracted.
  * ATTITUDE.yaw is a NED heading (0 = north, clockwise). ENU yaw is
    0 = east, counter-clockwise: yaw_enu = pi/2 - yaw_ned.
  * Controller yaw rates are ENU (counter-clockwise positive); MAVLink's are about
    the down axis (clockwise positive), so the actuator negates them.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

try:
    from .vehicle_class import Kinematics, Sensor, get_class, ray_table, _DEPTH_COLS, \
        _DEPTH_ROWS, _DEPTH_HFOV, _DEPTH_VFOV, _LIDAR_RAYS
    from .onboard_l5 import Pose
except ImportError:  # pragma: no cover - device flat-install path
    from vehicle_class import Kinematics, Sensor, get_class, ray_table, _DEPTH_COLS, \
        _DEPTH_ROWS, _DEPTH_HFOV, _DEPTH_VFOV, _LIDAR_RAYS
    from onboard_l5 import Pose

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- MAVLink constants
MAV_AUTOPILOT_ARDUPILOTMEGA = 3
MAV_MODE_FLAG_SAFETY_ARMED = 128
MAV_MODE_FLAG_CUSTOM_MODE_ENABLED = 1
MAV_FRAME_BODY_OFFSET_NED = 9
MAV_CMD_SET_MESSAGE_INTERVAL = 511

MSG_ATTITUDE = 30
MSG_LOCAL_POSITION_NED = 32
MSG_GLOBAL_POSITION_INT = 33
MSG_EKF_STATUS_REPORT = 193

# MAV_TYPE values each controller kinematics may command. Anything else (a plane,
# a PX4 board, an ADS-B receiver's heartbeat) is not the vehicle this was built for.
_MULTIROTOR_TYPES = frozenset((2, 3, 4, 13, 14, 15, 29, 35))  # quad coax heli hexa octo tri dodeca deca
_GROUND_TYPES = frozenset((10, 11))                              # ground rover, surface boat
# GUIDED's custom_mode number differs per ArduPilot vehicle. GUIDED_NOGPS (copter 20)
# is deliberately absent: it takes attitude targets, not velocity.
_GUIDED_MODE = {Kinematics.HOLONOMIC_3D: 4, Kinematics.UNICYCLE_2D: 15}

# SET_POSITION_TARGET_LOCAL_NED type_mask: bits SET = fields IGNORED. Use vx,vy,vz and
# yaw_rate; ignore position (0-2), acceleration (6-8) and yaw (10). Same mask as
# run_prompt.send_velocity. ArduRover reads this combination as speed + turn rate.
_VEL_YAWRATE_MASK = 0b0000_0101_1100_0111

# EKF_STATUS_REPORT.flags (ArduPilot dialect EKF_STATUS_FLAGS)
EKF_VELOCITY_HORIZ = 2
EKF_POS_HORIZ_REL = 8
EKF_POS_HORIZ_ABS = 16
EKF_CONST_POS_MODE = 128
EKF_UNINITIALIZED = 1024
EKF_GPS_GLITCH = 32768

# Below the smart layer's 0.4 gate and team_runtime's CONF_RTL (0.30): an EKF whose
# health we cannot see is treated as one that is struggling, not as a healthy one.
EKF_UNKNOWN_CONF = 0.3

_M_PER_DEG_LAT = 111320.0


def _load_sdk(sdk):
    if sdk is not None:
        return sdk
    import drone_sdk  # device flat install; the daemon puts drone/common on sys.path
    return drone_sdk


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def ned_yaw_to_enu(yaw_ned: float) -> float:
    """NED heading (0 = north, clockwise) -> ENU yaw (0 = east, counter-clockwise)."""
    return _wrap_pi(math.pi / 2.0 - yaw_ned)


def ekf_confidence(ekf) -> float:
    """Map an EKF_STATUS_REPORT to the 0..1 loc_confidence the controller gates on.

    0.0 when the EKF has no horizontal position (neither relative nor absolute),
    is uninitialised, or is in constant-position mode (it is pretending the vehicle
    is not moving). Otherwise from the worst of the velocity and horizontal-position
    innovation test ratios: ArduPilot treats <= 0.5 as good and 1.0 as failing its
    innovation check, so confidence is 1.0 up to 0.5 and falls linearly to 0 at 1.0.
    A GPS glitch, or no horizontal velocity estimate, caps it at EKF_UNKNOWN_CONF.

    Like every variance -> confidence map this is a heuristic; the 0.4 / 0.6 / 0.30
    gates downstream were tuned against the sim's localisation model, not this.
    """
    if ekf is None:
        return EKF_UNKNOWN_CONF
    flags = int(getattr(ekf, "flags", 0))
    if (flags & EKF_UNINITIALIZED or flags & EKF_CONST_POS_MODE
            or not flags & (EKF_POS_HORIZ_REL | EKF_POS_HORIZ_ABS)):
        return 0.0
    worst = max(float(getattr(ekf, "velocity_variance", 0.0)),
                float(getattr(ekf, "pos_horiz_variance", 0.0)))
    conf = 1.0 if worst <= 0.5 else max(0.0, (1.0 - worst) / 0.5)
    if flags & EKF_GPS_GLITCH or not flags & EKF_VELOCITY_HORIZ:
        conf = min(conf, EKF_UNKNOWN_CONF)
    return float(conf)


class TelemetryUnavailable(RuntimeError):
    """The flight controller has not sent a fresh enough pose to fly on."""


# --------------------------------------------------------------------------- telemetry
class ArduPilotTelemetry:
    """``onboard_l5.TelemetryProvider`` from ArduPilot's own EKF.

    Pose from LOCAL_POSITION_NED + ATTITUDE, confidence from EKF_STATUS_REPORT.
    ``pose()`` never blocks on the link: it parses what is already waiting
    (drone_sdk._pump(0)) and reads the newest cached messages. A pose older than
    ``max_age_s`` raises TelemetryUnavailable rather than flying on stale state.

    ``origin_ned`` is where the takeoff point sits in the EKF frame. Leave it None to
    have :meth:`resolve_origin` work it out from GLOBAL_POSITION_INT and the takeoff
    lat/lon drone_sdk recorded (``flight_home()``); pass it to pin it (tests, SITL).

    ``sensor_health`` (e.g. ``DepthScanSensor.healthy``) supplies Pose.sensor_ok.
    """

    STREAM_REFRESH_S = 10.0

    def __init__(self, sdk=None, origin_ned=None, max_age_s: float = 1.0,
                 sensor_health: Optional[Callable[[], bool]] = None,
                 stream_hz: float = 10.0, clock: Callable[[], float] = time.time):
        self.sdk = _load_sdk(sdk)
        self.max_age_s = max_age_s
        self.sensor_health = sensor_health
        self.stream_hz = stream_hz
        self.clock = clock
        self._origin = None if origin_ned is None else tuple(float(x) for x in origin_ned)
        self._last_stream_request = -1e9
        self.last_pose: Optional[Pose] = None
        self.last_attitude = None   # (roll, pitch) rad, FRD: right-wing-down / nose-up positive

    # ------------------------------------------------------------------ link
    def _request_streams(self) -> None:
        """Ask for the messages this reads at the control rate. drone_sdk's own
        stream request (4 Hz position) is too slow for a 10 Hz loop and does not
        include EKF_STATUS_REPORT at all (ArduPilot sends that in EXTRA3)."""
        now = self.clock()
        if now - self._last_stream_request < self.STREAM_REFRESH_S:
            return
        self._last_stream_request = now
        fast_us = int(1e6 / max(self.stream_hz, 0.5))
        rates = ((MSG_LOCAL_POSITION_NED, fast_us), (MSG_ATTITUDE, fast_us),
                 (MSG_GLOBAL_POSITION_INT, fast_us), (MSG_EKF_STATUS_REPORT, 500000))

        def send(m):
            for msg_id, interval_us in rates:
                m.mav.command_long_send(m.target_system, m.target_component,
                                        MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                                        msg_id, interval_us, 0, 0, 0, 0, 0)
        try:
            self.sdk._mav_send(send)
        except Exception as e:
            logger.warning(f"Could not request telemetry streams: {e}")

    def _drain(self) -> None:
        try:
            self.sdk._pump(0.0)
        except TypeError:
            # pymavlink's per-system cache raises TypeError on some instance-field
            # messages; drone_sdk patches the cache, this is the belt to its braces.
            pass

    def _cached(self, msg_type: str, max_age: float):
        try:
            return self.sdk._fc_cached(msg_type, max_age=max_age)
        except TypeError:
            return None

    # ------------------------------------------------------------------ origin
    def resolve_origin(self):
        """The takeoff point's (north, east, down) in the EKF frame, or None.

        From one GLOBAL_POSITION_INT and LOCAL_POSITION_NED pair: the vehicle is at
        local (n, e, d) and also (lat - home_lat, lon - home_lon) metres from the
        takeoff point and relative_alt above it, so the takeoff point is the
        difference. The pair is aligned on the FC's own time_boot_ms using the
        local velocity. Flat-earth metres, as drone_sdk.get_local_pose uses.
        """
        if self._origin is not None:
            return self._origin
        self._request_streams()
        self._drain()
        lp = self._cached("LOCAL_POSITION_NED", self.max_age_s)
        gp = self._cached("GLOBAL_POSITION_INT", self.max_age_s)
        home = None
        try:
            home = self.sdk.flight_home()
        except Exception:
            home = None
        if lp is None or gp is None or not home:
            return None
        # drone_sdk.flight_home() returns {"lat", "lon"}; accept a (lat, lon) pair too.
        if isinstance(home, dict):
            home_lat, home_lon = float(home["lat"]), float(home["lon"])
        else:
            home_lat, home_lon = float(home[0]), float(home[1])
        dt = (float(gp.time_boot_ms) - float(lp.time_boot_ms)) / 1000.0
        n = float(lp.x) + float(lp.vx) * dt
        e = float(lp.y) + float(lp.vy) * dt
        d = float(lp.z) + float(lp.vz) * dt
        north_of_home = (gp.lat / 1e7 - home_lat) * _M_PER_DEG_LAT
        east_of_home = (gp.lon / 1e7 - home_lon) * _M_PER_DEG_LAT * math.cos(math.radians(home_lat))
        up_of_home = gp.relative_alt / 1000.0
        self._origin = (n - north_of_home, e - east_of_home, d + up_of_home)
        logger.info(f"Takeoff point in the EKF frame: N {self._origin[0]:.2f} m, "
                    f"E {self._origin[1]:.2f} m, D {self._origin[2]:.2f} m")
        return self._origin

    # ------------------------------------------------------------------ provider
    def pose(self) -> Pose:
        self._request_streams()
        self._drain()
        origin = self.resolve_origin()
        if origin is None:
            raise TelemetryUnavailable("takeoff point unknown: no position fix or no recorded home")
        lp = self._cached("LOCAL_POSITION_NED", self.max_age_s)
        att = self._cached("ATTITUDE", self.max_age_s)
        if lp is None or att is None:
            raise TelemetryUnavailable(
                f"no {'LOCAL_POSITION_NED' if lp is None else 'ATTITUDE'} "
                f"from the flight controller in the last {self.max_age_s:.1f} s")
        n0, e0, d0 = origin
        pos = np.array([float(lp.y) - e0, float(lp.x) - n0, -(float(lp.z) - d0)], dtype=np.float32)
        vel = np.array([float(lp.vy), float(lp.vx), -float(lp.vz)], dtype=np.float32)
        conf = ekf_confidence(self._cached("EKF_STATUS_REPORT", 3.0))
        sensor_ok = True
        if self.sensor_health is not None:
            try:
                sensor_ok = bool(self.sensor_health())
            except Exception:
                sensor_ok = False
        self.last_attitude = (float(att.roll), float(att.pitch))
        self.last_pose = Pose(pos_enu=pos, yaw_rad=ned_yaw_to_enu(float(att.yaw)), vel_enu=vel,
                              loc_confidence=conf, sensor_ok=sensor_ok)
        return self.last_pose

    def altitude_m(self) -> Optional[float]:
        """Height above the takeoff point from the last pose, or None."""
        return None if self.last_pose is None else float(self.last_pose.pos_enu[2])

    def tilt(self):
        """(roll, pitch) from the last pose, for DepthScanSensor's levelling."""
        return self.last_attitude


# --------------------------------------------------------------------------- actuator
class ArduPilotActuator:
    """``onboard_l5.Actuator``: body-frame velocity setpoints in GUIDED.

    Controller action -> SET_POSITION_TARGET_LOCAL_NED, MAV_FRAME_BODY_OFFSET_NED
    (velocity relative to the vehicle's heading; x fwd, y RIGHT, z DOWN):
        quad  [vx, vy, vz, yaw_rate] (fwd, left, up; ENU rate) -> (vx, -vy, -vz), -yaw_rate
        rover [v, yaw_rate]                                    -> (v, 0, 0),     -yaw_rate
    ArduRover takes velocity + yaw_rate as "speed and turn rate".

    Refuses (sends nothing, returns False, sets ``last_refusal``) unless the
    autopilot's own fresh heartbeat says: ArduPilot, the system id this was built
    against, a MAV_TYPE matching the controller's kinematics, armed, in GUIDED, and
    the pilot has not taken control from the transmitter. Setpoints are clamped to
    the vehicle class's and drone_sdk's speed limits, and a quad's climb/descent is
    stopped at the altitude limits.

    ArduCopter drops a GUIDED velocity target after ~3 s without a new one, so a
    loop that stops sending leaves the copter braking to a hover, not flying on.
    """

    HEARTBEAT_MAX_AGE_S = 1.5

    def __init__(self, vclass_name: str, sdk=None, expected_sysid: Optional[int] = None,
                 altitude_fn: Optional[Callable[[], Optional[float]]] = None,
                 max_alt_m: Optional[float] = None, min_alt_m: Optional[float] = None):
        self.sdk = _load_sdk(sdk)
        self.vclass = get_class(vclass_name)
        kin = self.vclass.kinematics
        if kin not in _GUIDED_MODE:
            raise ValueError(f"ArduPilotActuator flies quads and rovers, not {self.vclass.name}")
        self._types = _MULTIROTOR_TYPES if kin is Kinematics.HOLONOMIC_3D else _GROUND_TYPES
        self._guided = _GUIDED_MODE[kin]
        if expected_sysid is None:
            # The connection drone_sdk resolved to the autopilot (see drone_sdk._connect).
            expected_sysid = self.sdk._mav_send(lambda m: m.target_system)
        self.expected_sysid = int(expected_sysid)
        self.altitude_fn = altitude_fn
        sdk_max_alt = float(getattr(self.sdk, "MAX_ALTITUDE", 20.0))
        self.max_alt_m = min(sdk_max_alt, max_alt_m) if max_alt_m is not None else sdk_max_alt
        self.min_alt_m = float(min_alt_m if min_alt_m is not None
                               else getattr(self.sdk, "MIN_ALTITUDE", 0.5))
        self.max_speed = min(self.vclass.max_speed_mps, float(getattr(self.sdk, "MAX_VELOCITY", 5.0)))
        self.max_yaw_rate = min(self.vclass.max_yaw_rate_radps,
                                math.radians(float(getattr(self.sdk, "MAX_YAW_RATE", 45.0))))
        self.last_refusal: Optional[str] = None
        self.last_setpoint: Optional[tuple] = None   # (vx, vy, vz, yaw_rate) as sent, MAVLink body
        self._told = None

    def ready(self):
        """(ok, reason): may this command the vehicle right now?"""
        try:
            pilot = self.sdk.pilot_control() if hasattr(self.sdk, "pilot_control") else None
        except Exception:
            pilot = None
        if pilot:
            return False, f"the pilot has control ({pilot})"
        hb = None
        try:
            hb = self.sdk._fc_cached("HEARTBEAT", max_age=self.HEARTBEAT_MAX_AGE_S)
        except TypeError:
            hb = None
        if hb is None:
            return False, "no fresh autopilot heartbeat"
        if int(hb.autopilot) != MAV_AUTOPILOT_ARDUPILOTMEGA:
            return False, f"autopilot {hb.autopilot} is not ArduPilot"
        src = hb.get_srcSystem() if hasattr(hb, "get_srcSystem") else self.expected_sysid
        if int(src) != self.expected_sysid:
            return False, f"heartbeat is from system {src}, built for {self.expected_sysid}"
        if int(hb.type) not in self._types:
            return False, f"MAV_TYPE {hb.type} is not a {self.vclass.name}"
        if not int(hb.base_mode) & MAV_MODE_FLAG_SAFETY_ARMED:
            return False, "not armed"
        if not (int(hb.base_mode) & MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
                and int(hb.custom_mode) == self._guided):
            return False, f"not in GUIDED (custom_mode {hb.custom_mode})"
        return True, ""

    def _refuse(self, why: str) -> bool:
        self.last_refusal = why
        if why != self._told:
            logger.warning(f"L5 setpoint refused: {why}")
            self._told = why
        return False

    def _send_setpoint(self, vx: float, vy: float, vz: float, yaw_rate: float) -> None:
        def send(m):
            m.mav.set_position_target_local_ned_send(
                0, m.target_system, m.target_component,
                MAV_FRAME_BODY_OFFSET_NED, _VEL_YAWRATE_MASK,
                0, 0, 0, vx, vy, vz, 0, 0, 0, 0, yaw_rate)
        self.sdk._mav_send(send)
        self.last_setpoint = (vx, vy, vz, yaw_rate)

    def send(self, action, vclass, dt: float) -> bool:
        if vclass.kinematics is not self.vclass.kinematics:
            return self._refuse(f"action for a {vclass.name}, built for a {self.vclass.name}")
        ok, why = self.ready()
        if not ok:
            return self._refuse(why)
        a = np.asarray(action, dtype=np.float64)
        if self.vclass.kinematics is Kinematics.UNICYCLE_2D:
            vx = float(np.clip(a[0], -self.max_speed, self.max_speed))
            vy = vz = 0.0
            rate = float(np.clip(a[1], -self.max_yaw_rate, self.max_yaw_rate))
        else:
            v = a[:3]
            speed = float(np.linalg.norm(v))
            if speed > self.max_speed:
                v = v * (self.max_speed / speed)   # scale, don't clip per axis: keeps direction
            vx, vleft, vup = float(v[0]), float(v[1]), float(v[2])
            alt = self.altitude_fn() if self.altitude_fn is not None else None
            if alt is not None and ((vup > 0 and alt >= self.max_alt_m)
                                    or (vup < 0 and alt <= self.min_alt_m)):
                vup = 0.0
            vy, vz = -vleft, -vup
            rate = float(np.clip(a[3], -self.max_yaw_rate, self.max_yaw_rate))
        self._send_setpoint(vx, vy, vz, -rate)
        self.last_refusal = None
        self._told = None
        return True

    def stop(self) -> bool:
        """Zero velocity: copter brakes to a hover, rover stops. Sent whenever this is
        still the vehicle it was built for and in GUIDED; armed is not required."""
        ok, why = self.ready()
        if not ok and why not in ("not armed",):
            return self._refuse(why)
        self._send_setpoint(0.0, 0.0, 0.0, 0.0)
        return True


# --------------------------------------------------------------------------- depth sensor
@dataclass
class DepthFrame:
    """One depth image and what is needed to back-project it."""
    depth: np.ndarray     # (H, W) z-depth in raw units, 0 = no return
    scale_m: float        # metres per raw unit (RealSense: depth_scale, usually 0.001)
    fx: float
    fy: float
    ppx: float
    ppy: float
    stamp: float          # time.monotonic() when the frame was captured


def _rot_left(angle: float) -> np.ndarray:
    """Rotation about the body LEFT axis (FLU), nose-up positive."""
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]])


def _rot_fwd(angle: float) -> np.ndarray:
    """Rotation about the body FORWARD axis (FLU), right-wing-down positive."""
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


class DepthScanSensor:
    """``onboard_l5.SensorProvider`` from a depth camera (RealSense D435i).

    Back-projects a strided subset of the depth image into a level, yaw-aligned body
    frame (x fwd, y left, z up — the controller's) and reduces it to the class's ray
    layout from ``vehicle_class.ray_table``:

      * quad (FORWARD_DEPTH, 45 rays): each point goes to the nearest cell of the
        5x9 grid (+35..-35 deg pitch, +45..-45 deg yaw); a ray's value is a low
        percentile of the ranges in its cell.
      * rover (LIDAR_360, 72 rays): points are binned by horizontal angle into the
        5 deg ring; value is horizontal range. Ground points (lower than
        ``mount_height_m - ground_margin_m`` below the camera) and points more than
        ``max_obstacle_up_m`` above it are discarded, so ``mount_height_m`` is
        required for a rover.

    Rays with no points (outside the lens, or no valid return) are ``sense_range_m``,
    which the SensorProvider contract defines as clear. A D435i covers ~87 x 58 deg,
    so only the 3 middle rows x 7 centre columns of the quad grid are ever seen, and
    a rover sees only the ~87 deg ahead of it: everything else always reads clear.

    ``mount_pitch_deg`` is the optical axis's pitch from body-forward (0 forward,
    -90 straight down: a down-looking camera sees no forward obstacles at all).
    ``tilt_fn`` -> (roll, pitch) levels the points for the vehicle's own attitude
    (ArduPilotTelemetry.tilt); ``altitude_fn`` lets a quad drop ground returns.

    ``frame_source()`` returns the newest DepthFrame or None and must not block.
    A frame older than ``max_age_s`` (or none) makes :meth:`healthy` False and the
    scan all-clear, matching the sim's frame-dropout semantics: Pose.sensor_ok
    carries the blindness, not a stale scan.
    """

    def __init__(self, vclass_name: str, frame_source: Callable[[], Optional[DepthFrame]],
                 mount_pitch_deg: float = 0.0, mount_height_m: Optional[float] = None,
                 max_age_s: float = 0.5, stride: int = 4, percentile: float = 5.0,
                 min_points: int = 3, ground_margin_m: float = 0.15,
                 max_obstacle_up_m: float = 1.0,
                 tilt_fn: Optional[Callable[[], Optional[tuple]]] = None,
                 altitude_fn: Optional[Callable[[], Optional[float]]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.vclass = get_class(vclass_name)
        if self.vclass.sensor not in (Sensor.FORWARD_DEPTH, Sensor.LIDAR_360):
            raise ValueError(f"DepthScanSensor builds FORWARD_DEPTH or LIDAR_360 scans, "
                             f"not {self.vclass.sensor}")
        if self.vclass.sensor is Sensor.LIDAR_360 and mount_height_m is None:
            raise ValueError("a rover's depth scan needs mount_height_m to tell ground from obstacles")
        self.n_rays = ray_table(self.vclass.sensor).n_rays
        self.frame_source = frame_source
        self.mount = math.radians(mount_pitch_deg)
        self.mount_height_m = mount_height_m
        self.max_age_s = max_age_s
        self.stride = max(1, int(stride))
        self.percentile = percentile
        self.min_points = max(1, int(min_points))
        self.ground_margin_m = ground_margin_m
        self.max_obstacle_up_m = max_obstacle_up_m
        self.tilt_fn = tilt_fn
        self.altitude_fn = altitude_fn
        self.clock = clock
        self.sense_max = float(self.vclass.sense_range_m)
        # Camera axes in body FLU: optical z = forward pitched by the mount, x = right, y = down.
        rm = _rot_left(self.mount)
        self._cam_to_body = np.stack([rm @ np.array([0.0, -1.0, 0.0]),     # cam x (right)
                                      rm @ np.array([0.0, 0.0, -1.0]),     # cam y (down)
                                      rm @ np.array([1.0, 0.0, 0.0])], 1)  # cam z (optical)
        self._last_ok = False

    def healthy(self) -> bool:
        """Was the last scan built from a fresh frame?"""
        return self._last_ok

    def _clear(self) -> np.ndarray:
        return np.full(self.n_rays, self.sense_max, dtype=np.float32)

    def points(self, frame: DepthFrame) -> np.ndarray:
        """(N, 3) level body-frame points (fwd, left, up) metres from the camera."""
        d = np.asarray(frame.depth)[::self.stride, ::self.stride].astype(np.float64) * frame.scale_m
        vs, us = np.nonzero(d > 0)
        z = d[vs, us]
        u = us * self.stride
        v = vs * self.stride
        cam = np.stack([(u - frame.ppx) / frame.fx * z, (v - frame.ppy) / frame.fy * z, z], 1)
        pts = cam @ self._cam_to_body.T
        tilt = self.tilt_fn() if self.tilt_fn is not None else None
        if tilt is not None:
            roll, pitch = tilt
            pts = pts @ (_rot_left(pitch) @ _rot_fwd(roll)).T
        return pts

    def _reduce(self, keys: np.ndarray, vals: np.ndarray, n: int) -> np.ndarray:
        out = self._clear()
        if keys.size == 0:
            return out
        order = np.argsort(keys, kind="stable")
        keys, vals = keys[order], vals[order]
        starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
        ends = np.r_[starts[1:], keys.size]
        for s, e in zip(starts, ends):
            if e - s >= self.min_points:
                out[int(keys[s])] = min(self.sense_max, float(np.percentile(vals[s:e], self.percentile)))
        return out

    def scan(self) -> np.ndarray:
        frame = None
        try:
            frame = self.frame_source()
        except Exception as e:
            logger.warning(f"Depth frame source failed: {e}")
        if frame is None or self.clock() - float(frame.stamp) > self.max_age_s:
            self._last_ok = False
            return self._clear()
        pts = self.points(frame)
        self._last_ok = True
        if pts.size == 0:
            return self._clear()
        fwd, left, up = pts[:, 0], pts[:, 1], pts[:, 2]
        if self.vclass.sensor is Sensor.LIDAR_360:
            keep = (up > -(self.mount_height_m - self.ground_margin_m)) & (up < self.max_obstacle_up_m)
            fwd, left = fwd[keep], left[keep]
            rng = np.hypot(fwd, left)
            az = np.arctan2(left, fwd)
            k = np.round(az / (2.0 * math.pi / _LIDAR_RAYS)).astype(np.int64) % _LIDAR_RAYS
            return self._reduce(k, rng, _LIDAR_RAYS)
        if self.altitude_fn is not None:
            alt = self.altitude_fn()
            if alt is not None:
                keep = up > -(alt - self.ground_margin_m)
                fwd, left, up = fwd[keep], left[keep], up[keep]
        rng = np.sqrt(fwd * fwd + left * left + up * up)
        az = np.arctan2(left, fwd)
        el = np.arctan2(up, np.hypot(fwd, left))
        col_step = _DEPTH_HFOV / (_DEPTH_COLS - 1)
        row_step = _DEPTH_VFOV / (_DEPTH_ROWS - 1)
        col = np.round((_DEPTH_HFOV / 2.0 - az) / col_step).astype(np.int64)
        row = np.round((_DEPTH_VFOV / 2.0 - el) / row_step).astype(np.int64)
        inside = (fwd > 0) & (col >= 0) & (col < _DEPTH_COLS) & (row >= 0) & (row < _DEPTH_ROWS)
        return self._reduce(row[inside] * _DEPTH_COLS + col[inside], rng[inside], self.n_rays)


class RealSenseDepthSource:
    """A non-blocking DepthFrame source over pyrealsense2 (D435i depth stream).

    Grabs frames on its own thread and keeps the newest, so ``__call__`` costs
    nothing on the control loop. Depth-only, not aligned to colour: the intrinsics
    are the depth stream's own. UNTESTED ON HARDWARE. The camera must not be held
    by anything else (drone_sdk.release_camera() first; on the quadcopter the
    cam-split service also reads the D435i's colour node).
    """

    def __init__(self, serial: Optional[str] = None, width: int = 640, height: int = 480,
                 fps: int = 15):
        self.serial, self.width, self.height, self.fps = serial, width, height, fps
        self._latest: Optional[DepthFrame] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pipe = None

    def start(self) -> "RealSenseDepthSource":
        import pyrealsense2 as rs  # only on the aircraft
        pipe, cfg = rs.pipeline(), rs.config()
        if self.serial:
            cfg.enable_device(str(self.serial))
        cfg.enable_stream(rs.stream.depth, self.width, self.height, rs.format.z16, self.fps)
        profile = pipe.start(cfg)
        scale = float(profile.get_device().first_depth_sensor().get_depth_scale())
        intr = profile.get_stream(rs.stream.depth).as_video_stream_profile().get_intrinsics()
        self._pipe = pipe

        def grab():
            while not self._stop.is_set():
                try:
                    frames = pipe.wait_for_frames(1000)
                    df = frames.get_depth_frame()
                    if df:
                        self._latest = DepthFrame(np.asanyarray(df.get_data()).copy(), scale,
                                                  intr.fx, intr.fy, intr.ppx, intr.ppy,
                                                  time.monotonic())
                except Exception as e:
                    logger.debug(f"RealSense depth grab: {e}")
        self._thread = threading.Thread(target=grab, name="l5-depth", daemon=True)
        self._thread.start()
        return self

    def __call__(self) -> Optional[DepthFrame]:
        return self._latest

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)
        if self._pipe is not None:
            try:
                self._pipe.stop()
            except Exception:
                pass
            self._pipe = None
