"""Frame conventions and quaternion helpers.

World: ENU (x east, y north, z up). Body: FLU (x forward, y left, z up).
Quaternions are [w, x, y, z], rotating body vectors into the world: v_w = R(q) v_b.

Euler angles (for configs and logs only): yaw psi about world z (ENU, 0 = facing east,
positive counter-clockwise from above), pitch theta positive NOSE-UP, roll phi positive
RIGHT-WING-DOWN. With body FLU this is R = Rz(psi) Ry(-theta) Rx(phi).
"""
from __future__ import annotations

import math

import numpy as np

GRAVITY_M_S2 = 9.80665


def quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ])


def quat_conj(q: np.ndarray) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]])


def quat_normalize(q: np.ndarray) -> np.ndarray:
    return q / math.sqrt(float(q @ q))


def quat_from_rotvec(r: np.ndarray) -> np.ndarray:
    """Exact quaternion of a rotation vector (axis * angle, rad)."""
    ang = math.sqrt(float(r @ r))
    if ang < 1e-12:
        return np.array([1.0, 0.5 * r[0], 0.5 * r[1], 0.5 * r[2]])
    s = math.sin(0.5 * ang) / ang
    return np.array([math.cos(0.5 * ang), r[0] * s, r[1] * s, r[2] * s])


def quat_to_rotvec(q: np.ndarray) -> np.ndarray:
    q = q if q[0] >= 0 else -q
    v = q[1:]
    s = math.sqrt(float(v @ v))
    if s < 1e-12:
        return 2.0 * v
    return v * (2.0 * math.atan2(s, q[0]) / s)


def quat_to_rot(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def rot_to_quat(R: np.ndarray) -> np.ndarray:
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = [0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        q = [(R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s]
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        q = [(R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s]
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        q = [(R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s]
    return quat_normalize(np.array(q))


def quat_from_euler(roll_rad: float, pitch_rad: float, yaw_rad: float) -> np.ndarray:
    """R = Rz(yaw) Ry(-pitch) Rx(roll); pitch nose-up positive, roll right-wing-down."""
    qz = np.array([math.cos(yaw_rad / 2), 0.0, 0.0, math.sin(yaw_rad / 2)])
    qy = np.array([math.cos(-pitch_rad / 2), 0.0, math.sin(-pitch_rad / 2), 0.0])
    qx = np.array([math.cos(roll_rad / 2), math.sin(roll_rad / 2), 0.0, 0.0])
    return quat_mul(quat_mul(qz, qy), qx)


def euler_from_rot(R: np.ndarray) -> tuple[float, float, float]:
    """(roll, pitch, yaw) in rad, inverse of quat_from_euler."""
    pitch = math.asin(max(-1.0, min(1.0, R[2, 0])))      # R[2,0] = sin(theta) for Ry(-theta)
    roll = math.atan2(R[2, 1], R[2, 2])
    yaw = math.atan2(R[1, 0], R[0, 0])
    return roll, pitch, yaw


def tilt_rad(R: np.ndarray) -> float:
    """Angle between body z (thrust axis of a multirotor) and world up."""
    return math.acos(max(-1.0, min(1.0, R[2, 2])))


def wrap_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def skew(v: np.ndarray) -> np.ndarray:
    return np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])
