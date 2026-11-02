"""
VIO bridge - stream visual(-inertial) odometry to the flight controller.

Takes odometry from a pluggable source (a ROS 2 nav_msgs/Odometry topic from
Isaac ROS cuVSLAM or OpenVINS, a plain callback/queue, or a simulated source
for SITL and tests), converts it to the frames ArduPilot expects, and streams
MAVLink ODOMETRY (default) or VISION_POSITION_ESTIMATE + VISION_SPEED_ESTIMATE
at a fixed rate over drone_sdk's own MAVLink connection - the flight
controller's serial port is exclusive, so there is no second link to open.

A watchdog stops sending the moment the odometry stalls, goes stale, jumps
without a reset, or its quality/covariance collapses. Silence is the signal
ArduPilot acts on: its visual odometry is unhealthy 300 ms after the last
message (AP_VISUALODOM_TIMEOUT_MS) and the EKF falls back (dead reckoning,
then its EKF failsafe, FS_EKF_ACTION). Resuming after an outage always bumps
reset_counter, so the EKF takes the new pose as a reset instead of rejecting
it as a huge innovation.

FRAMES
------
What ArduPilot 4.7.1 accepts (libraries/GCS_MAVLink/GCS_Common.cpp,
GCS_MAVLINK::handle_odometry): ODOMETRY with frame_id = MAV_FRAME_LOCAL_FRD
and child_frame_id = MAV_FRAME_BODY_FRD, anything else is dropped silently.
  * x, y, z     position in the EKF's local frame: x "north", y "east", z DOWN
                (m). With EK3_SRC1_YAW = 6 (ExternalNav) "north" is whatever
                the odometry's x axis is; the EKF adopts the source's heading.
  * q           attitude, body FRD -> local frame, (w, x, y, z).
  * vx, vy, vz  velocity in BODY FRD (x forward, y right, z down), m/s. The FC
                rotates it into the local frame with q (vel = q * vel).
  * pose_covariance  21-element upper triangle of the 6x6 (x, y, z, roll,
                pitch, yaw) covariance. ArduPilot uses only
                posErr = sqrt(c[0] + c[6] + c[11]) and
                angErr = sqrt(c[15] + c[18] + c[20]); NaN in c[0] = unknown,
                and VISO_POS_M_NSE / VISO_YAW_M_NSE are used instead.
  * quality     -1 failed, 0 unknown, 1 worst .. 100 best; the FC consumes the
                message only if quality >= VISO_QUAL_MIN.
  * position is that of the SENSOR: the EKF corrects for the lever arm itself
    with VISO_POS_X/Y/Z (AP_NavEKF3_PosVelFusion.cpp). If your source already
    reports base_link, set those to 0.
  * The MAVLink VISO backend never applies VISO_ORIENT (AP_VisualOdom_MAV.cpp
    has no rotation), so every rotation happens here.

What the sources say, and what FrameConvention converts:
  world frame (gravity aligned, z up or down):
    'NED' / 'FRD'  x north|forward, y east|right, z down      -> unchanged
    'ENU'          x east, y north, z up (REP-103 geographic)  -> (y, x, -z)
    'FLU'          x forward-at-start, y left, z up (REP-103
                   odom/map of a VIO: cuVSLAM odom, OpenVINS)  -> (x, -y, -z)
  child (body) frame of the pose and twist:
    'FRD'          aircraft body (x forward, y right, z down)
    'FLU'          ROS base_link (x forward, y left, z up)
    'OPTICAL'      camera optical frame (x right, y down, z out of the lens)
  mount_rpy_deg: how the child frame's FRD axes sit in the vehicle body,
    (roll, pitch, yaw) in degrees, ZYX. A forward camera is (0, 0, 0); one
    looking straight down with the top of the image toward the nose is
    (0, -90, 0) (pitch -90: the optical axis turns from forward to down).

  If R_s rotates child vectors into the source world, the vehicle attitude is
      R_nb = C_W . R_s . (M . C_S)^T
  where C_W maps the source world into NED, C_S maps the child axes onto the
  sensor's FRD axes, and M is the mount rotation. Positions map with C_W,
  child-frame twists with M . C_S. tests/test_vio_bridge.py checks all of it
  with rotations.

TIMESTAMPS
----------
Samples carry time.monotonic() seconds of when the pose was VALID (not when
it was sent). time_usec goes out as that, in microseconds, and must only
increase - out-of-order samples are dropped. The FC maps it onto its own
clock with its jitter-correction filter (correct_offboard_timestamp_usec_to_ms),
which estimates the offset from the minimum observed transport lag, so a
constant pipeline latency is absorbed into that offset: it must be given to
the EKF as VISO_DELAY_MS (measure it). No TIMESYNC exchange is needed.
ros_stamp_to_monotonic() puts ROS header stamps on this clock.

USE
---
    import vio_bridge
    bridge = vio_bridge.VioBridge(vio_bridge.FrameConvention('FLU', 'FLU'))
    bridge.start()                                   # 30 Hz, drone_sdk's link
    src = vio_bridge.Ros2OdometrySource(bridge, '/visual_slam/tracking/odometry')
    ...
    bridge.healthy, bridge.health()                  # watchdog state
    bridge.stop(); src.stop()

Python 3.8 (the companion's): no 3.9+ syntax or stdlib.
"""
from __future__ import annotations

import math
import queue
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]
Quat = Tuple[float, float, float, float]       # (w, x, y, z)
Mat3 = Tuple[Vec3, Vec3, Vec3]                 # row-major

# MAVLink enum values used here (common.xml). Spelled out so this module
# imports without pymavlink; tests compare them against pymavlink's.
MAV_FRAME_LOCAL_FRD = 20
MAV_FRAME_BODY_FRD = 12
MAV_ESTIMATOR_TYPE_VIO = 3
MAV_CMD_SET_EKF_SOURCE_SET = 42007

NAN = float('nan')


def _log(msg):
    print(f"VIO: {msg}")


# =============================================================================
# Rotation math. Pure Python: 3x3 row-major tuples and (w, x, y, z) unit
# quaternions, both meaning "rotate child-frame vectors into the parent frame".
# =============================================================================

IDENTITY: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def mat_mul(a: Mat3, b: Mat3) -> Mat3:
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
                 for i in range(3))


def mat_T(a: Mat3) -> Mat3:
    return tuple(tuple(a[j][i] for j in range(3)) for i in range(3))


def mat_vec(a: Mat3, v: Sequence[float]) -> Vec3:
    return tuple(a[i][0] * v[0] + a[i][1] * v[1] + a[i][2] * v[2] for i in range(3))


def quat_normalize(q: Sequence[float]) -> Quat:
    n = math.sqrt(sum(c * c for c in q))
    if not n or not math.isfinite(n):
        raise ValueError(f"quaternion {tuple(q)} has no direction")
    return tuple(c / n for c in q)


def quat_to_matrix(q: Sequence[float]) -> Mat3:
    w, x, y, z = quat_normalize(q)
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
            (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
            (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)))


def matrix_to_quat(r: Mat3) -> Quat:
    """Shepperd's method; returns w >= 0."""
    tr = r[0][0] + r[1][1] + r[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = (0.25 * s, (r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s)
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        q = ((r[2][1] - r[1][2]) / s, 0.25 * s, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s)
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        q = ((r[0][2] - r[2][0]) / s, (r[0][1] + r[1][0]) / s, 0.25 * s, (r[1][2] + r[2][1]) / s)
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
        q = ((r[1][0] - r[0][1]) / s, (r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, 0.25 * s)
    q = quat_normalize(q)
    return q if q[0] >= 0 else tuple(-c for c in q)


def euler_to_matrix(roll: float, pitch: float, yaw: float) -> Mat3:
    """Aerospace ZYX (yaw, then pitch, then roll), radians: body -> parent."""
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return ((cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
            (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
            (-sp, cp * sr, cp * cr))


def matrix_to_euler(r: Mat3) -> Vec3:
    """(roll, pitch, yaw) radians, ZYX - what VISION_POSITION_ESTIMATE carries."""
    pitch = math.asin(max(-1.0, min(1.0, -r[2][0])))
    return math.atan2(r[2][1], r[2][2]), pitch, math.atan2(r[1][0], r[0][0])


# Source world -> NED/FRD local frame (C_W).
WORLD_FRAMES = {
    'NED': IDENTITY,
    'FRD': IDENTITY,
    'ENU': ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0)),
    'FLU': ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),
}
# Child axes -> the sensor's own FRD axes (C_S).
CHILD_FRAMES = {
    'FRD': IDENTITY,
    'FLU': ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),
    # optical x right, y down, z out of the lens -> forward = z, right = x, down = y
    'OPTICAL': ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
}


class FrameConvention:
    """How a source's poses are expressed, and their conversion to NED / FRD.

    world, child: keys of WORLD_FRAMES / CHILD_FRAMES (see the module doc).
    mount_rpy_deg: the child frame's FRD axes in the vehicle body (ZYX deg).
    velocity_frame: 'child' (nav_msgs/Odometry twist, the ROS convention) or
        'world' (a source that reports velocity in its world frame).
    """

    def __init__(self, world: str = 'FLU', child: str = 'FLU',
                 mount_rpy_deg: Sequence[float] = (0.0, 0.0, 0.0), velocity_frame: str = 'child'):
        world, child = world.upper(), child.upper()
        if world not in WORLD_FRAMES:
            raise ValueError(f"world frame {world!r}: expected one of {sorted(WORLD_FRAMES)}")
        if child not in CHILD_FRAMES:
            raise ValueError(f"child frame {child!r}: expected one of {sorted(CHILD_FRAMES)}")
        if velocity_frame not in ('child', 'world'):
            raise ValueError("velocity_frame must be 'child' or 'world'")
        self.world, self.child, self.velocity_frame = world, child, velocity_frame
        self.mount_rpy_deg = tuple(float(a) for a in mount_rpy_deg)
        self._c_w = WORLD_FRAMES[world]
        mount = euler_to_matrix(*(math.radians(a) for a in self.mount_rpy_deg))
        self._r_body_child = mat_mul(mount, CHILD_FRAMES[child])   # child vector -> body FRD

    def __repr__(self):
        return (f"FrameConvention(world={self.world!r}, child={self.child!r}, "
                f"mount_rpy_deg={self.mount_rpy_deg}, velocity_frame={self.velocity_frame!r})")

    def attitude(self, q_source: Sequence[float]) -> Mat3:
        """Body FRD -> NED rotation from the source's child -> world quaternion."""
        r_s = quat_to_matrix(q_source)
        return mat_mul(mat_mul(self._c_w, r_s), mat_T(self._r_body_child))

    def position(self, p_source: Sequence[float]) -> Vec3:
        return mat_vec(self._c_w, p_source)

    def body_velocity(self, v_source: Sequence[float], r_nb: Mat3) -> Vec3:
        """Velocity in body FRD (the ODOMETRY convention)."""
        if self.velocity_frame == 'world':
            return mat_vec(mat_T(r_nb), mat_vec(self._c_w, v_source))
        return mat_vec(self._r_body_child, v_source)

    def body_rates(self, w_source: Sequence[float], r_nb: Mat3) -> Vec3:
        return self.body_velocity(w_source, r_nb)

    def pose_cov(self, cov: Optional[Sequence[float]]) -> Optional[List[List[float]]]:
        """6x6 pose covariance in NED (x, y, z, rot about x, y, z)."""
        return _rotate_cov6(cov, self._c_w, self._c_w)

    def twist_cov(self, cov: Optional[Sequence[float]], r_nb: Mat3) -> Optional[List[List[float]]]:
        """6x6 twist covariance in body FRD (vx, vy, vz, wx, wy, wz)."""
        if self.velocity_frame == 'world':
            r = mat_mul(mat_T(r_nb), self._c_w)
            return _rotate_cov6(cov, r, r)
        return _rotate_cov6(cov, self._r_body_child, self._r_body_child)


def _as_cov6(cov: Sequence[float]) -> List[List[float]]:
    """36 row-major (ROS) or 6 diagonal variances -> 6x6."""
    cov = [float(c) for c in cov]
    if len(cov) == 36:
        return [cov[i * 6:(i + 1) * 6] for i in range(6)]
    if len(cov) == 6:
        return [[cov[i] if i == j else 0.0 for j in range(6)] for i in range(6)]
    raise ValueError(f"covariance needs 36 (6x6 row-major) or 6 (diagonal) values, got {len(cov)}")


def _rotate_cov6(cov, r_lin: Mat3, r_ang: Mat3) -> Optional[List[List[float]]]:
    if cov is None:
        return None
    c = _as_cov6(cov)
    if not all(math.isfinite(v) for row in c for v in row):
        return None
    t = [[0.0] * 6 for _ in range(6)]
    for i in range(3):
        for j in range(3):
            t[i][j] = r_lin[i][j]
            t[3 + i][3 + j] = r_ang[i][j]
    tc = [[sum(t[i][k] * c[k][j] for k in range(6)) for j in range(6)] for i in range(6)]
    return [[sum(tc[i][k] * t[j][k] for k in range(6)) for j in range(6)] for i in range(6)]


def upper_triangle(c6: Optional[List[List[float]]]) -> List[float]:
    """The 21-value upper triangle MAVLink carries; NaN first value = unknown."""
    if c6 is None:
        return [NAN] + [0.0] * 20
    return [c6[i][j] for i in range(6) for j in range(i, 6)]


def position_std_m(c6: Optional[List[List[float]]]) -> Optional[float]:
    """sqrt(trace of the position block): exactly ArduPilot's posErr."""
    if c6 is None:
        return None
    return math.sqrt(max(0.0, c6[0][0] + c6[1][1] + c6[2][2]))


def ros_stamp_to_monotonic(stamp_s: float, ros_now_s: float, mono_now_s: Optional[float] = None) -> float:
    """A ROS header stamp on time.monotonic()'s clock: now, minus how old the
    stamp is by the ROS clock (wall or sim time). A stamp from the future
    (clock skew between hosts) counts as now."""
    if mono_now_s is None:
        mono_now_s = time.monotonic()
    return mono_now_s - max(0.0, ros_now_s - stamp_s)


@dataclass
class OdomSample:
    """One odometry reading, in the source's own frames (see FrameConvention).

    t: time.monotonic() seconds at which the pose was valid.
    orientation: (w, x, y, z), child -> world.
    velocity / angular_velocity: in the child frame (or world, per the
        convention's velocity_frame); None if the source has none.
    pose_covariance / velocity_covariance: 36 row-major or 6 diagonal values
        in the source's frames; None if unknown.
    quality: 0..100 (100 best), None if the source does not say.
    reset_counter: the source's own reset count, if it has one; any change
        is a reset. reset: True on the first sample after a source reset.
    """
    t: float
    position: Sequence[float]
    orientation: Sequence[float]
    velocity: Optional[Sequence[float]] = None
    angular_velocity: Optional[Sequence[float]] = None
    pose_covariance: Optional[Sequence[float]] = None
    velocity_covariance: Optional[Sequence[float]] = None
    quality: Optional[int] = None
    reset_counter: Optional[int] = None
    reset: bool = False


@dataclass
class _Converted:
    t: float
    pos: Vec3
    r_nb: Mat3
    q: Quat
    vel: Optional[Vec3]
    rates: Optional[Vec3]
    pose_cov: Optional[List[List[float]]]
    twist_cov: Optional[List[List[float]]]
    quality: Optional[int]
    arrived: float


def drone_sdk_sender():
    """drone_sdk's streaming send: the FC link the rest of the SDK uses,
    without queueing behind a thread that holds its receive lock."""
    try:
        import drone_sdk
    except ImportError:  # installed as a package
        from . import drone_sdk
    return drone_sdk._mav_send_stream


class VioBridge:
    """Converts OdomSamples and streams them to the FC at `rate_hz`.

    Feed it with submit() from any thread (or a source class below), and
    either start() its own thread or call step() yourself.

    message: 'odometry' (MAVLink 2 ODOMETRY, preferred) or 'vision'
        (VISION_POSITION_ESTIMATE + VISION_SPEED_ESTIMATE; ArduPilot reads no
        quality from these, so only this bridge's own gate protects it).
    sender: callable(fn) that runs fn(mavlink_connection) - defaults to
        drone_sdk's link. Tests pass a fake.
    Watchdog (any of these makes it unhealthy and stops sending):
        stall_timeout_s  no new sample for this long (below ArduPilot's 300 ms)
        max_latency_s    newest sample older than this when it would be sent
        min_quality      reported quality below this (0..100)
        max_pos_std_m    sqrt(trace) of the position covariance above this
        jump_m           position off its velocity prediction by more than
                         this without a reset (a glitch or an unflagged
                         relocalisation)
    recover_samples: consecutive good samples needed to resume; resuming
        bumps reset_counter.
    fallback_source_set: if set (2 or 3), ask the FC to switch EKF source set
        (MAV_CMD_SET_EKF_SOURCE_SET) when the odometry is lost. Never switches
        back on its own - call select_source_set(1) once you trust it again.
    """

    def __init__(self, convention: Optional[FrameConvention] = None, rate_hz: float = 30.0,
                 message: str = 'odometry', sender: Optional[Callable] = None,
                 stall_timeout_s: float = 0.25, max_latency_s: float = 0.3,
                 min_quality: int = 20, max_pos_std_m: float = 1.0, jump_m: float = 0.5,
                 recover_samples: int = 5, fallback_source_set: Optional[int] = None,
                 clock: Callable[[], float] = time.monotonic,
                 on_health_change: Optional[Callable[[bool, str], None]] = None,
                 log: Callable[[str], None] = _log):
        if message not in ('odometry', 'vision'):
            raise ValueError("message must be 'odometry' or 'vision'")
        if rate_hz <= 0 or rate_hz > 50:
            # EKF3 drops ExtNav samples closer than 20 ms (extNavIntervalMin_ms)
            raise ValueError("rate_hz must be in (0, 50]")
        if fallback_source_set not in (None, 2, 3):
            raise ValueError("fallback_source_set must be None, 2 or 3")
        self.convention = convention or FrameConvention()
        self.rate_hz = float(rate_hz)
        self.message = message
        self._sender = sender
        self.stall_timeout_s = stall_timeout_s
        self.max_latency_s = max_latency_s
        self.min_quality = min_quality
        self.max_pos_std_m = max_pos_std_m
        self.jump_m = jump_m
        self.recover_samples = max(1, int(recover_samples))
        self.fallback_source_set = fallback_source_set
        self._clock = clock
        self._on_health_change = on_health_change
        self._log = log

        self._lock = threading.Lock()
        self._latest: Optional[_Converted] = None      # newest accepted sample
        self._prev: Optional[_Converted] = None        # the one before it (jump check)
        self._source_reset_counter = None
        self._reset_counter = 0                        # uint8 sent to the FC
        self._pending_reset = False                    # bump before the next send
        self._last_sent_t = None
        self._last_dropped_t = None
        self._good_streak = 0
        self._sample_problem = None                    # why the newest sample is bad
        self._healthy = False
        self._reason = 'no odometry yet'
        self._ever_sent = False
        self._link_mavlink1_warned = False
        self.stats = {'received': 0, 'sent': 0, 'dropped_out_of_order': 0, 'dropped_invalid': 0,
                      'dropped_unhealthy': 0, 'resets': 0, 'send_errors': 0, 'health_changes': 0}
        self._send_times = []
        self._thread = None
        self._stop = threading.Event()

    # -- input -----------------------------------------------------------------
    def submit(self, sample: OdomSample) -> bool:
        """Hand the bridge a sample (thread-safe). False if it was rejected."""
        now = self._clock()
        try:
            conv = self._convert(sample, now)
        except (ValueError, TypeError) as e:
            with self._lock:
                self.stats['dropped_invalid'] += 1
            self._log_once(f"rejected a sample: {e}")
            return False
        with self._lock:
            self.stats['received'] += 1
            if self._latest is not None and conv.t <= self._latest.t:
                self.stats['dropped_out_of_order'] += 1
                return False
            reset = bool(sample.reset)
            if sample.reset_counter is not None:
                if (self._source_reset_counter is not None
                        and sample.reset_counter != self._source_reset_counter):
                    reset = True
                self._source_reset_counter = sample.reset_counter
            if reset:
                self._pending_reset = True
            self._sample_problem = self._judge(conv, reset)
            if self._sample_problem is None:
                self._good_streak += 1
            else:
                self._good_streak = 0
            self._prev, self._latest = self._latest, conv
        return True

    def submit_pose(self, t, position, orientation, **kw) -> bool:
        """submit() without building an OdomSample: same fields as keywords."""
        return self.submit(OdomSample(t=t, position=position, orientation=orientation, **kw))

    def force_reset(self):
        """The next sample goes out as a reset (e.g. the SLAM map relocalised)."""
        with self._lock:
            self._pending_reset = True

    def _convert(self, s: OdomSample, now: float) -> _Converted:
        vals = list(s.position) + list(s.orientation)
        if len(s.position) != 3 or len(s.orientation) != 4:
            raise ValueError("position needs 3 values and orientation 4 (w, x, y, z)")
        if not all(math.isfinite(float(v)) for v in vals) or not math.isfinite(float(s.t)):
            raise ValueError("non-finite position, orientation or time")
        c = self.convention
        r_nb = c.attitude(s.orientation)
        vel = rates = None
        if s.velocity is not None and all(math.isfinite(float(v)) for v in s.velocity):
            vel = c.body_velocity(s.velocity, r_nb)
        if s.angular_velocity is not None and all(math.isfinite(float(v)) for v in s.angular_velocity):
            rates = c.body_rates(s.angular_velocity, r_nb)
        quality = None if s.quality is None else int(max(-1, min(100, s.quality)))
        return _Converted(t=float(s.t), pos=c.position(s.position), r_nb=r_nb,
                          q=matrix_to_quat(r_nb), vel=vel, rates=rates,
                          pose_cov=c.pose_cov(s.pose_covariance),
                          twist_cov=c.twist_cov(s.velocity_covariance, r_nb),
                          quality=quality, arrived=now)

    def _judge(self, conv: _Converted, reset: bool) -> Optional[str]:
        """Why this sample must not be used, or None. Caller holds the lock."""
        if conv.quality is not None and conv.quality < self.min_quality:
            return f"quality {conv.quality} < {self.min_quality}"
        std = position_std_m(conv.pose_cov)
        if std is not None and std > self.max_pos_std_m:
            return f"position std {std:.2f} m > {self.max_pos_std_m:g} m"
        prev = self._latest
        if (not reset and self.jump_m and prev is not None
                and conv.t - prev.t <= self.stall_timeout_s):
            dt = conv.t - prev.t
            pred = prev.pos
            if prev.vel is not None:
                v_ned = mat_vec(prev.r_nb, prev.vel)
                pred = tuple(prev.pos[i] + v_ned[i] * dt for i in range(3))
            jump = math.sqrt(sum((conv.pos[i] - pred[i]) ** 2 for i in range(3)))
            if jump > self.jump_m:
                return f"position jumped {jump:.2f} m without a reset"
        return None

    # -- watchdog + output -----------------------------------------------------
    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def reset_counter(self) -> int:
        return self._reset_counter

    def health(self) -> dict:
        """Snapshot for logs / telemetry."""
        now = self._clock()
        with self._lock:
            latest = self._latest
            times = list(self._send_times)
            out = {
                'healthy': self._healthy,
                'reason': self._reason,
                'reset_counter': self._reset_counter,
                'last_sample_age_s': None if latest is None else round(now - latest.t, 3),
                'last_quality': None if latest is None else latest.quality,
                'last_position_ned': None if latest is None else latest.pos,
                'message': self.message,
                'rate_hz_target': self.rate_hz,
                'stats': dict(self.stats),
            }
        out['rate_hz_measured'] = (round((len(times) - 1) / (times[-1] - times[0]), 1)
                                   if len(times) > 1 and times[-1] > times[0] else 0.0)
        return out

    def step(self, now: Optional[float] = None) -> bool:
        """One tick: update health; send the newest sample if it is new and
        good. Returns True if something was sent."""
        now = self._clock() if now is None else now
        with self._lock:
            latest = self._latest
            problem = None
            if latest is None:
                problem = 'no odometry yet'
            elif now - latest.arrived > self.stall_timeout_s:
                problem = f"stalled: no sample for {now - latest.arrived:.2f} s"
                self._good_streak = 0         # recovery counts samples after the outage
            elif now - latest.t > self.max_latency_s:
                problem = f"stale: newest sample is {now - latest.t:.2f} s old"
                self._good_streak = 0
            elif self._sample_problem is not None:
                problem = self._sample_problem
            elif not self._healthy and self._good_streak < self.recover_samples:
                problem = (f"recovering: {self._good_streak}/{self.recover_samples} good samples"
                           if self._ever_sent else
                           f"starting: {self._good_streak}/{self.recover_samples} good samples")
            changed = self._set_health(problem is None, problem or 'ok')
            if problem is not None:
                if latest is not None and latest.t != self._last_dropped_t and (
                        self._last_sent_t is None or latest.t > self._last_sent_t):
                    self.stats['dropped_unhealthy'] += 1
                    self._last_dropped_t = latest.t
                send = None
            elif self._last_sent_t is not None and latest.t <= self._last_sent_t:
                send = None                                  # nothing new
            else:
                if changed and self._ever_sent:
                    self._pending_reset = True               # resuming after an outage
                if self._pending_reset:
                    self._reset_counter = (self._reset_counter + 1) % 256
                    self._pending_reset = False
                    self.stats['resets'] += 1
                self._last_sent_t = latest.t
                send = (latest, self._reset_counter)
        if changed:
            self._health_changed()
        if send is None:
            return False
        return self._send(*send)

    def _set_health(self, ok: bool, reason: str) -> bool:
        """Caller holds the lock. True if healthy flipped."""
        self._reason = reason
        if ok == self._healthy:
            return False
        self._healthy = ok
        self.stats['health_changes'] += 1
        return True

    def _health_changed(self):
        ok, reason = self._healthy, self._reason
        self._log(("healthy - streaming to the flight controller" if ok
                   else f"UNHEALTHY ({reason}) - stopped sending; the EKF falls back"))
        if not ok and self._ever_sent and self.fallback_source_set:
            threading.Thread(target=self.select_source_set, args=(self.fallback_source_set,),
                             daemon=True).start()
        if self._on_health_change is not None:
            try:
                self._on_health_change(ok, reason)
            except Exception as e:
                self._log(f"on_health_change raised {e!r}")

    def _send(self, s: _Converted, reset_counter: int) -> bool:
        sender = self._sender or drone_sdk_sender()
        try:
            sender(lambda m: self._emit(m, s, reset_counter))
        except Exception as e:
            with self._lock:
                self.stats['send_errors'] += 1
            self._log_once(f"send failed: {e!r}")
            return False
        with self._lock:
            self.stats['sent'] += 1
            self._ever_sent = True
            self._send_times.append(self._clock())
            del self._send_times[:-int(self.rate_hz * 2) - 1]
        return True

    def _emit(self, m, s: _Converted, reset_counter: int):
        usec = int(s.t * 1e6)
        quality = 0 if s.quality is None else s.quality
        message = self.message
        if message == 'odometry' and not hasattr(m.mav, 'odometry_send'):
            # ODOMETRY is MAVLink 2 only; a MAVLink 1 link cannot carry it.
            if not self._link_mavlink1_warned:
                self._log("link is MAVLink 1 (no ODOMETRY) - sending VISION_POSITION_ESTIMATE instead; "
                          "set SERIALn_PROTOCOL 2 on the FC port")
                self._link_mavlink1_warned = True
            message = 'vision'
        if message == 'odometry':
            vel = s.vel if s.vel is not None else (NAN, NAN, NAN)
            rates = s.rates if s.rates is not None else (NAN, NAN, NAN)
            m.mav.odometry_send(
                usec, MAV_FRAME_LOCAL_FRD, MAV_FRAME_BODY_FRD,
                s.pos[0], s.pos[1], s.pos[2], list(s.q),
                vel[0], vel[1], vel[2], rates[0], rates[1], rates[2],
                upper_triangle(s.pose_cov), upper_triangle(s.twist_cov),
                reset_counter, MAV_ESTIMATOR_TYPE_VIO, quality)
            return
        roll, pitch, yaw = matrix_to_euler(s.r_nb)
        try:
            m.mav.vision_position_estimate_send(usec, s.pos[0], s.pos[1], s.pos[2], roll, pitch, yaw,
                                                upper_triangle(s.pose_cov), reset_counter)
        except TypeError:   # MAVLink 1 dialect: no covariance / reset_counter extensions
            m.mav.vision_position_estimate_send(usec, s.pos[0], s.pos[1], s.pos[2], roll, pitch, yaw)
        if s.vel is None:
            return
        v_ned = mat_vec(s.r_nb, s.vel)
        cov9 = [NAN] + [0.0] * 8
        if s.twist_cov is not None:
            lin = [[s.twist_cov[i][j] for j in range(3)] for i in range(3)]
            rl = [[sum(s.r_nb[i][k] * lin[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
            cov9 = [sum(rl[i][k] * s.r_nb[j][k] for k in range(3)) for i in range(3) for j in range(3)]
        try:
            m.mav.vision_speed_estimate_send(usec, v_ned[0], v_ned[1], v_ned[2], cov9, reset_counter)
        except TypeError:
            m.mav.vision_speed_estimate_send(usec, v_ned[0], v_ned[1], v_ned[2])

    def _log_once(self, msg):
        if getattr(self, '_last_logged', None) != msg:
            self._last_logged = msg
            self._log(msg)

    def select_source_set(self, n: int) -> bool:
        """Ask the FC to use EKF source set n (1..3). True if it accepted."""
        try:
            try:
                import drone_sdk
            except ImportError:
                from . import drone_sdk
            ok = drone_sdk._set_ekf_source_set(n)
        except Exception as e:
            self._log(f"could not switch EKF source set to {n}: {e!r}")
            return False
        self._log(f"EKF source set {n} {'selected' if ok else 'REFUSED by the FC'}")
        return ok

    # -- thread ----------------------------------------------------------------
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='vio-bridge', daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    def _run(self):
        period = 1.0 / self.rate_hz
        next_t = self._clock()
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as e:   # keep the watchdog alive whatever a sample did
                self._log_once(f"tick failed: {e!r}")
            next_t += period
            delay = next_t - self._clock()
            if delay < -period:      # fell far behind (suspended): do not burst
                next_t = self._clock()
                delay = 0.0
            self._stop.wait(max(0.0, delay))


# =============================================================================
# Sources
# =============================================================================

class QueueSource:
    """Drains a queue.Queue of OdomSample (or dicts of its fields) into a
    bridge on its own thread: the hand-off for a producer that should not
    call the bridge directly (another thread's callback, a decoder loop)."""

    def __init__(self, bridge: VioBridge, q: Optional[queue.Queue] = None):
        self.bridge = bridge
        self.queue = q if q is not None else queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='vio-queue', daemon=True)
        self._thread.start()

    def put(self, sample) -> None:
        """Non-blocking: when the queue is full the oldest sample goes."""
        while True:
            try:
                self.queue.put_nowait(sample)
                return
            except queue.Full:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    pass

    def _run(self):
        while not self._stop.is_set():
            try:
                item = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if isinstance(item, dict):
                item = OdomSample(**item)
            self.bridge.submit(item)

    def stop(self):
        self._stop.set()
        self._thread.join(1.0)


ROS_AVAILABLE = False
try:  # same pattern as nav2_bridge.py: the module works without ROS
    import rclpy  # noqa: F401
    from nav_msgs.msg import Odometry as _RosOdometry  # noqa: F401
    ROS_AVAILABLE = True
except ImportError:
    pass


def odometry_msg_to_sample(msg, mono_t: float, quality: Optional[int] = None) -> OdomSample:
    """nav_msgs/Odometry -> OdomSample. ROS quaternions are (x, y, z, w);
    the twist is in child_frame_id, the pose in header.frame_id."""
    p, o = msg.pose.pose.position, msg.pose.pose.orientation
    tl, ta = msg.twist.twist.linear, msg.twist.twist.angular
    pose_cov = list(msg.pose.covariance)
    twist_cov = list(msg.twist.covariance)
    # An all-zero covariance in ROS means "not filled in", not "perfect".
    return OdomSample(
        t=mono_t, position=(p.x, p.y, p.z), orientation=(o.w, o.x, o.y, o.z),
        velocity=(tl.x, tl.y, tl.z), angular_velocity=(ta.x, ta.y, ta.z),
        pose_covariance=pose_cov if any(pose_cov) else None,
        velocity_covariance=twist_cov if any(twist_cov) else None,
        quality=quality)


class Ros2OdometrySource:
    """Subscribes a nav_msgs/Odometry topic and feeds the bridge.

    Isaac ROS cuVSLAM: '/visual_slam/tracking/odometry' (odom frame FLU,
    child base_link FLU - FrameConvention('FLU', 'FLU')). OpenVINS:
    '/ov_msckf/odomimu' (gravity-aligned global frame, z up; child is the
    IMU frame - give its mount). Neither publishes a quality in Odometry:
    pass set_quality() from the tracker's status topic if you have one,
    otherwise the covariance gate is what protects the FC.

    node: an existing rclpy Node to attach to (then you spin it); without one
    this creates its own node and spins it on a thread.
    """

    def __init__(self, bridge: VioBridge, topic: str = '/visual_slam/tracking/odometry',
                 node=None, qos_depth: int = 10):
        if not ROS_AVAILABLE:
            raise ImportError("rclpy / nav_msgs not importable: source ROS 2 (and the "
                              "drone-api venv's .pth link) or use QueueSource / submit()")
        import rclpy
        from nav_msgs.msg import Odometry
        self.bridge = bridge
        self._quality = None
        self._own = node is None
        if self._own:
            if not rclpy.ok():
                rclpy.init()
            node = rclpy.create_node('coybot_vio_bridge')
        self.node = node
        self._sub = node.create_subscription(Odometry, topic, self._on_msg, qos_depth)
        self._thread = None
        if self._own:
            from rclpy.executors import SingleThreadedExecutor
            self._executor = SingleThreadedExecutor()
            self._executor.add_node(node)
            self._thread = threading.Thread(target=self._executor.spin, name='vio-ros', daemon=True)
            self._thread.start()

    def set_quality(self, quality: Optional[int]):
        """Tracker quality 0..100 (None: unknown) for the following samples."""
        self._quality = quality

    def _on_msg(self, msg):
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        ros_now = self.node.get_clock().now().nanoseconds * 1e-9
        t = ros_stamp_to_monotonic(stamp, ros_now) if stamp > 0 else time.monotonic()
        self.bridge.submit(odometry_msg_to_sample(msg, t, self._quality))

    def stop(self):
        try:
            self.node.destroy_subscription(self._sub)
        except Exception:
            pass
        if self._own:
            try:
                self._executor.shutdown()
                self.node.destroy_node()
            except Exception:
                pass


class NoisyTruthSource:
    """A simulated VIO for SITL and tests: ground truth plus what real VIO
    gets wrong, published in a chosen ROS-style convention so the bridge's
    conversion is exercised end to end.

    truth(): returns (t_monotonic, pos_ned, q_body_to_ned (w,x,y,z),
    vel_ned) or None. Errors, all seeded:
      pos_noise_m          white noise on every position
      drift_m_per_sqrt_s   random-walk position bias (z at z_drift_scale)
      yaw_drift_deg_per_sqrt_s  random-walk heading error of the VIO world
                           frame (rotates position and attitude together)
      vel_noise_mps        white noise on body velocity
    The covariance sent grows with the accumulated drift, like a real
    filter's would. inject_jump()/stall() script failures; drift_ned()
    reports the true error for scoring.
    """

    def __init__(self, bridge: VioBridge, truth: Callable, rate_hz: float = 30.0,
                 convention: Optional[FrameConvention] = None, pos_noise_m: float = 0.02,
                 drift_m_per_sqrt_s: float = 0.02, z_drift_scale: float = 0.5,
                 yaw_drift_deg_per_sqrt_s: float = 0.05, vel_noise_mps: float = 0.03,
                 quality: int = 90, seed: int = 0, clock: Callable[[], float] = time.monotonic):
        self.bridge = bridge
        self.truth = truth
        self.rate_hz = rate_hz
        self.convention = convention or bridge.convention
        self.pos_noise_m = pos_noise_m
        self.drift = drift_m_per_sqrt_s
        self.z_drift_scale = z_drift_scale
        self.yaw_drift = math.radians(yaw_drift_deg_per_sqrt_s)
        self.vel_noise = vel_noise_mps
        self.quality = quality
        self._rng = random.Random(seed)
        self._clock = clock
        self._bias = [0.0, 0.0, 0.0]
        self._yaw_bias = 0.0
        self._last_t = None
        self._stall_until = 0.0
        self._reset_counter = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        # inverse conversions: NED/FRD -> the published convention
        self._c_w_inv = mat_T(WORLD_FRAMES[self.convention.world])
        self._r_body_child = self.convention._r_body_child

    def drift_ned(self) -> Tuple[Vec3, float]:
        """(position bias in NED m, heading bias rad) at this moment."""
        with self._lock:
            return tuple(self._bias), self._yaw_bias

    def believed(self, pos_ned: Sequence[float]) -> Vec3:
        """Where this VIO (without its white noise) places a true NED position."""
        bias, yaw_bias = self.drift_ned()
        p = mat_vec(euler_to_matrix(0.0, 0.0, yaw_bias), pos_ned)
        return tuple(p[i] + bias[i] for i in range(3))

    def inject_jump(self, offset_ned: Sequence[float], flag_reset: bool = True):
        """The VIO map relocalises: its frame shifts by offset_ned. With
        flag_reset the source says so (reset_counter changes)."""
        with self._lock:
            for i in range(3):
                self._bias[i] += offset_ned[i]
            if flag_reset:
                self._reset_counter += 1

    def stall(self, seconds: float):
        """Publish nothing for `seconds` (a frozen VIO pipeline)."""
        self._stall_until = self._clock() + seconds

    def sample(self) -> Optional[OdomSample]:
        """One reading built from truth() now (None if truth has none)."""
        tr = self.truth()
        if tr is None:
            return None
        t, pos, q, vel = tr
        with self._lock:
            if self._last_t is not None and t <= self._last_t:
                return None                     # no new truth since the last sample
            dt = 0.0 if self._last_t is None else t - self._last_t
            self._last_t = t
            g = self._rng.gauss
            k = self.drift * math.sqrt(dt)
            self._bias[0] += g(0.0, k)
            self._bias[1] += g(0.0, k)
            self._bias[2] += g(0.0, k * self.z_drift_scale)
            self._yaw_bias += g(0.0, self.yaw_drift * math.sqrt(dt))
            bias, yaw_bias, counter = tuple(self._bias), self._yaw_bias, self._reset_counter
            noise = tuple(g(0.0, self.pos_noise_m) for _ in range(3))
            vnoise = tuple(g(0.0, self.vel_noise) for _ in range(3))
        rz = euler_to_matrix(0.0, 0.0, yaw_bias)
        r_nb = quat_to_matrix(q)
        p_vio = tuple(v + b + n for v, b, n in zip(mat_vec(rz, pos), bias, noise))
        r_vio = mat_mul(rz, r_nb)
        v_body = tuple(v + n for v, n in zip(mat_vec(mat_T(r_nb), vel), vnoise))
        # publish in the source convention: invert FrameConvention's mapping
        r_src = mat_mul(mat_mul(self._c_w_inv, r_vio), self._r_body_child)
        p_src = mat_vec(self._c_w_inv, p_vio)
        if self.convention.velocity_frame == 'world':
            v_src = mat_vec(self._c_w_inv, mat_vec(r_vio, v_body))
        else:
            v_src = mat_vec(mat_T(self._r_body_child), v_body)
        drift_mag = math.sqrt(sum(b * b for b in bias))
        var = self.pos_noise_m ** 2 + (0.1 * drift_mag) ** 2
        return OdomSample(t=t, position=p_src, orientation=matrix_to_quat(r_src), velocity=v_src,
                          pose_covariance=[var, var, var, 1e-4, 1e-4, 1e-4],
                          velocity_covariance=[self.vel_noise ** 2] * 3 + [1e-4] * 3,
                          quality=self.quality, reset_counter=counter)

    def step(self) -> bool:
        if self._clock() < self._stall_until:
            return False
        s = self.sample()
        return s is not None and self.bridge.submit(s)

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='vio-sim', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(1.0)

    def _run(self):
        period = 1.0 / self.rate_hz
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as e:
                _log(f"simulated source failed: {e!r}")
            self._stop.wait(period)
