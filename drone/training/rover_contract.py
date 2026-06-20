"""State/action contract for the ground rover learned policy.

Kept separate from contract.py so the quad policy and its ONNX export are untouched.

Sensor stack:
  - 360° 2D lidar: LIDAR_RAYS evenly-spaced horizontal rays (floor-to-ceiling coverage)
  - Goal vector in body frame (fwd, left) — 2D only, altitude unused
  - Odometry: linear velocity, yaw rate

State (R_STATE_DIM = 11 + LIDAR_RAYS, body frame, SI):
    0  target_fwd   forward distance to goal               (m, +fwd)
    1  target_left  left distance to goal                  (m, +left)
    2  target_up    always 0.0 (ground constrained)
    3  dist         straight-line 2D distance to goal      (m)
    4  vel_fwd      realized body-frame forward velocity   (m/s)
    5  vel_left     always 0.0 (unicycle: no lateral slip)
    6  vel_up       always 0.0
    7  yaw_err      heading error to goal, wrapped          (rad, [-pi,pi])
    8  yaw_rate     realized yaw rate                       (rad/s)
    9  altitude     always 0.0
    10 vehicle      always 1.0 (VEHICLE_ROVER)
    11..11+L-1  lidar scan, ray 0 (fwd) → L-1 (fwd-Δ°)   (m, LIDAR_MAX if clear)
                Rays are evenly spaced clockwise: ray k = k*(360/L)° from forward.

Action (R_ACTION_DIM = 2): [v_linear (m/s, fwd), yaw_rate (rad/s)]
    Unicycle model — lateral velocity is always 0. Policy never commands sideways motion.
    Deploy: map directly to /cmd_vel Twist (linear.x, angular.z).
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

# --- lidar geometry ----------------------------------------------------------
LIDAR_RAYS = 72                          # 360° / 5° per ray
LIDAR_MAX = 10.0                         # m: clip / "no obstacle"
LIDAR_ANGLES = tuple(
    2 * math.pi * i / LIDAR_RAYS for i in range(LIDAR_RAYS)
)  # body-frame angles (0=fwd, CCW positive)
# unit directions in body frame (fwd, left) for each ray
LIDAR_DIRS = np.array(
    [(math.cos(a), math.sin(a)) for a in LIDAR_ANGLES], dtype=np.float32
)  # (LIDAR_RAYS, 2)

# --- state / action ----------------------------------------------------------
_BASE_FIELDS = (
    "target_fwd", "target_left", "target_up", "dist",
    "vel_fwd", "vel_left", "vel_up",
    "yaw_err", "yaw_rate", "altitude", "vehicle",
)
LIDAR_FIELDS = tuple(f"lidar_{i}" for i in range(LIDAR_RAYS))
R_STATE_FIELDS = _BASE_FIELDS + LIDAR_FIELDS
R_ACTION_FIELDS = ("v_linear", "yaw_rate")

R_STATE_DIM = len(R_STATE_FIELDS)   # 11 + 72 = 83
R_ACTION_DIM = 2

assert R_STATE_DIM == 11 + LIDAR_RAYS

# --- normalization ------------------------------------------------------------
# Lidar observations normalised by LIDAR_MAX (mean=0, std=LIDAR_MAX gives ≈ ±1 range).
R_STATE_MEAN = np.zeros(R_STATE_DIM, dtype=np.float32)
R_STATE_STD = np.array(
    [10.0, 10.0, 1.0, 12.0,   # target fwd/left/up, dist
     2.0, 1.0, 1.0,            # vel fwd/left/up
     math.pi, 1.5, 1.0, 1.0]  # yaw_err, yaw_rate, altitude, vehicle
    + [LIDAR_MAX] * LIDAR_RAYS,
    dtype=np.float32,
)


def wrap_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def build_obs(
    target_fwd: float,
    target_left: float,
    vel_fwd: float = 0.0,
    yaw_rate: float = 0.0,
    lidar: Sequence[float] | None = None,
) -> np.ndarray:
    """Assemble a raw (unnormalised) state vector for inference / validation."""
    dist = math.hypot(target_fwd, target_left)
    yaw_err = wrap_pi(math.atan2(target_left, target_fwd)) if dist > 1e-4 else 0.0
    base = np.array([
        target_fwd, target_left, 0.0, dist,
        vel_fwd, 0.0, 0.0,
        yaw_err, yaw_rate, 0.0, 1.0,
    ], dtype=np.float32)
    if lidar is None:
        scan = np.full(LIDAR_RAYS, LIDAR_MAX, dtype=np.float32)
    else:
        scan = np.clip(np.asarray(lidar, dtype=np.float32), 0.0, LIDAR_MAX)
        assert scan.shape == (LIDAR_RAYS,), f"lidar must be {LIDAR_RAYS} values"
    return np.concatenate([base, scan])
