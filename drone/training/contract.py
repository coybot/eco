"""Shared state/action contract for the learned mid-level pilot (quad + rover).

Single source of truth for what the policy sees and emits; mirrors the ONNX + state-norm
deploy path on the Orin Nano.

policy_v4 upgrades perception from a single horizontal depth fan to a **2D depth grid**
(DEPTH_ROWS vertical x DEPTH_COLS horizontal), like a real forward depth camera. This lets the
policy choose a path in 3D — go left/right AND over/under (or any blend) — around arbitrary
rectangular-prism obstacles, instead of only steering laterally. The grid is forward-facing
(matches one onboard camera); FOV is widened so vertical gaps are sensable.

State (STATE_DIM, body frame, SI units before normalization):
    0  target_fwd   forward distance to current target           (m, +ahead)
    1  target_left  left distance to target                      (m, +left)
    2  target_up    up distance to target                        (m, +up)
    3  dist         straight-line distance to target             (m, >=0)
    4  vel_fwd      current body-frame forward velocity          (m/s)
    5  vel_left     current body-frame left velocity             (m/s)
    6  vel_up       current body-frame up velocity               (m/s)
    7  yaw_err      heading error to target bearing, wrapped      (rad, [-pi,pi])
    8  yaw_rate     current yaw rate                              (rad/s)
    9  altitude     altitude above takeoff (AGL)                  (m)
    10 vehicle      vehicle kind: 0.0 quad, 1.0 rover             (flag)
    11..11+R-1  depth grid, row-major (top->bottom, left->right)  (m, DEPTH_MAX if clear)

Action (4-dim, body frame): [vx (fwd), vy (left), vz (up, +climb), yaw_rate].
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np

# --- vehicle kinds --------------------------------------------------------
VEHICLE_QUAD = 0.0
VEHICLE_ROVER = 1.0

# --- depth grid geometry --------------------------------------------------
DEPTH_COLS = 9                       # horizontal samples
DEPTH_ROWS = 5                       # vertical samples
DEPTH_RAYS = DEPTH_COLS * DEPTH_ROWS  # 45 (flattened, row-major: top->bottom, left->right)
DEPTH_HFOV = math.radians(90.0)      # wide so lateral gaps are well covered
DEPTH_VFOV = math.radians(70.0)      # wide enough to sense over/under
DEPTH_MAX = 10.0                     # m: clip / "no obstacle" value

# per-ray bearings: yaw offset (+left) per column, pitch offset (+up) per row
YAW_OFFSETS = tuple(
    (DEPTH_HFOV / 2.0) - c * (DEPTH_HFOV / (DEPTH_COLS - 1)) for c in range(DEPTH_COLS)
)  # [+45° .. 0 .. -45°]
PITCH_OFFSETS = tuple(
    (DEPTH_VFOV / 2.0) - r * (DEPTH_VFOV / (DEPTH_ROWS - 1)) for r in range(DEPTH_ROWS)
)  # [+35° (top) .. 0 .. -35° (bottom)]

# unit ray directions in body frame (fwd, left, up), flattened row-major (45, 3)
def _ray_dirs():
    dirs = np.zeros((DEPTH_RAYS, 3), dtype=np.float32)
    i = 0
    for pitch in PITCH_OFFSETS:
        for yaw in YAW_OFFSETS:
            ce = math.cos(pitch)
            dirs[i] = (ce * math.cos(yaw), ce * math.sin(yaw), math.sin(pitch))
            i += 1
    return dirs

RAY_DIRS = _ray_dirs()   # (DEPTH_RAYS, 3) fwd/left/up

# --- dimensions / field order (do not reorder; the ONNX export depends on it) ---
_BASE_FIELDS = (
    "target_fwd", "target_left", "target_up", "dist",
    "vel_fwd", "vel_left", "vel_up",
    "yaw_err", "yaw_rate", "altitude", "vehicle",
)
DEPTH_FIELDS = tuple(f"d{i}" for i in range(DEPTH_RAYS))
STATE_FIELDS = _BASE_FIELDS + DEPTH_FIELDS
ACTION_FIELDS = ("vx", "vy", "vz", "yaw_rate")

STATE_DIM = len(STATE_FIELDS)    # 11 + DEPTH_RAYS
ACTION_DIM = len(ACTION_FIELDS)  # 4

assert STATE_DIM == 11 + DEPTH_RAYS, "state contract changed; bump policy version + retrain"


def wrap_pi(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


@dataclass
class State:
    target_fwd: float
    target_left: float
    target_up: float
    dist: float
    vel_fwd: float
    vel_left: float
    vel_up: float
    yaw_err: float
    yaw_rate: float
    altitude: float
    vehicle: float
    depth: np.ndarray  # (DEPTH_RAYS,) flattened depth grid

    def to_vec(self) -> np.ndarray:
        base = [
            self.target_fwd, self.target_left, self.target_up, self.dist,
            self.vel_fwd, self.vel_left, self.vel_up,
            self.yaw_err, self.yaw_rate, self.altitude, self.vehicle,
        ]
        return np.concatenate(
            [np.asarray(base, dtype=np.float32),
             np.asarray(self.depth, dtype=np.float32)]
        )

    @classmethod
    def from_vec(cls, v: Sequence[float]) -> "State":
        v = np.asarray(v, dtype=np.float32)
        return cls(*[float(x) for x in v[:11]], depth=v[11:11 + DEPTH_RAYS].copy())

    @property
    def min_clearance(self) -> float:
        return float(np.min(self.depth))


def clear_grid() -> np.ndarray:
    return np.full(DEPTH_RAYS, DEPTH_MAX, dtype=np.float32)


def build_state(
    target_xyz: tuple[float, float, float] | None,
    velocity_fwd_left_up: tuple[float, float, float] = (0.0, 0.0, 0.0),
    yaw_rate: float = 0.0,
    depth_fan: Sequence[float] | None = None,   # name kept for call-site compatibility
    altitude_m: float | None = None,
    vehicle: float = VEHICLE_QUAD,
) -> State:
    """Assemble a State. `depth_fan` is the DEPTH_RAYS flattened depth grid (None = clear)."""
    if target_xyz is None:
        tf = tl = tu = 0.0
        dist = 0.0
        yaw_err = 0.0
    else:
        tf, tl, tu = (float(x) for x in target_xyz)
        dist = math.sqrt(tf * tf + tl * tl + tu * tu)
        yaw_err = wrap_pi(math.atan2(tl, tf)) if (tf or tl) else 0.0

    vf, vl, vu = (float(x) for x in velocity_fwd_left_up)
    if depth_fan is None:
        depth = clear_grid()
    else:
        depth = np.asarray(depth_fan, dtype=np.float32).reshape(-1)
        assert depth.shape == (DEPTH_RAYS,), f"depth grid must be {DEPTH_RAYS} values"
        depth = np.clip(depth, 0.0, DEPTH_MAX)
    altitude = 0.0 if altitude_m is None else float(altitude_m)

    return State(
        target_fwd=tf, target_left=tl, target_up=tu, dist=dist,
        vel_fwd=vf, vel_left=vl, vel_up=vu,
        yaw_err=yaw_err, yaw_rate=float(yaw_rate),
        altitude=altitude, vehicle=float(vehicle), depth=depth,
    )


# --- normalization --------------------------------------------------------
DEFAULT_STATE_MEAN = np.zeros(STATE_DIM, dtype=np.float32)
DEFAULT_STATE_STD = np.array(
    [10.0, 10.0, 5.0, 12.0, 3.0, 3.0, 2.0, math.pi, 1.0, 10.0, 1.0]
    + [DEPTH_MAX] * DEPTH_RAYS,
    dtype=np.float32,
)


def normalize(x: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    return (x - mean) / np.maximum(std, 1e-6)
