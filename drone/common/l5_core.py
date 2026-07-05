"""L5 core — the portable, on-device autonomy controller.

This is the *shipped* copy of the L5 reactive potential-field controller that the
simulator uses to achieve L5 (0 interventions, 100% mission success, collision-free
across the 16-scenario adversarial suite). It is deliberately dependency-light
(stdlib + numpy) so it installs flat into ``~/drone-api/`` and runs on the Jetson.

**Single source of truth policy.** The controller logic, the depth/lidar ray-angle
tables, the ``Observation`` shape, and the ``Agent`` fields here are byte-for-byte
the same behaviour as ``eco/drone/sim/team_world.py``. A parity test
(``common/tests/test_l5_parity.py``) feeds identical observations to both the sim
controller and this one and asserts identical actions — so any drift is a CI
failure, not a silent divergence between "what we validated" and "what flies".

The controller consumes one struct — ``Observation`` (goal in body frame, sensed
neighbours, an obstacle scan, min clearance) — and returns a per-class action:
  - HOLONOMIC_3D quad : [vx, vy, vz, yaw_rate]   (body frame)
  - UNICYCLE_2D  rover: [v_linear, yaw_rate]

On hardware the ``Observation`` is assembled from real sensors + the peer team
link (see ``onboard_l5.py``); in sim it is assembled from ground truth. The
controller does not know or care which.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
import math

import numpy as np

# Dual import: packaged (eco.drone.common.l5_core) in the repo, flat (l5_core)
# on the device where common/*.py is installed flat into ~/drone-api/.
try:
    from .vehicle_class import (  # type: ignore
        VehicleClass, Kinematics, Sensor, Role, get_class,
    )
except ImportError:  # pragma: no cover - device flat-install path
    from vehicle_class import (  # type: ignore
        VehicleClass, Kinematics, Sensor, Role, get_class,
    )


# --------------------------------------------------------------------------- agent
@dataclass
class Agent:
    """Minimal agent state the controller + directives read.

    Mirrors the fields ``eco.drone.sim.team_world.Agent`` exposes to the controller
    (id, vclass, pos, yaw, vel, goal, alive). The sim's Agent is richer (roster
    bookkeeping); this is the lean shape the flight code populates each tick.
    """
    id: str
    vclass: VehicleClass
    pos: np.ndarray                      # world (3,) ENU meters
    yaw: float = 0.0                     # rad
    vel: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float32))
    goal: np.ndarray | None = None       # world (3,) target waypoint
    alive: bool = True

    def __post_init__(self):
        self.pos = np.asarray(self.pos, dtype=np.float32).reshape(3)
        if self.goal is not None:
            self.goal = np.asarray(self.goal, dtype=np.float32).reshape(3)


# --------------------------------------------------------------------------- observation
@dataclass
class Observation:
    """Per-agent observation assembled each tick. Engine-agnostic, layer-friendly."""
    agent_id: str
    body_target: np.ndarray              # (3,) goal in body frame (fwd,left,up)
    goal_dist: float
    neighbors: list                      # [(id, rel_pos_body(3,), rel_vel(3,), vclass), ...]
    scan: np.ndarray                     # sensing modality output (lidar ring or depth-min fan)
    min_clearance: float                 # nearest obstacle/teammate surface distance (m)
    loc_confidence: float
    inbox: list


Controller = Callable[[Agent, Observation], np.ndarray]


# --------------------------------------------------------------------------- ray tables
# These MUST match contract.py / rover_contract.py and the sim exactly — they define
# the geometric meaning of each element in Observation.scan.
_LIDAR_RAYS = 72
_LIDAR_ANGLES = tuple(2 * math.pi * i / _LIDAR_RAYS for i in range(_LIDAR_RAYS))

# Forward depth grid for quads — mirrors contract.py geometry exactly.
# 5 rows (top→bottom, ±35°) × 9 cols (left→right, ±45°) = 45 rays, row-major.
_DEPTH_COLS = 9
_DEPTH_ROWS = 5
_DEPTH_HFOV = math.radians(90.0)
_DEPTH_VFOV = math.radians(70.0)


def _make_depth_dirs() -> tuple:
    """Body-frame unit ray directions (fwd, left, up) for the 45-ray depth grid."""
    dirs = []
    for r in range(_DEPTH_ROWS):
        pitch = _DEPTH_VFOV / 2.0 - r * _DEPTH_VFOV / (_DEPTH_ROWS - 1)
        for c in range(_DEPTH_COLS):
            yaw = _DEPTH_HFOV / 2.0 - c * _DEPTH_HFOV / (_DEPTH_COLS - 1)
            ce = math.cos(pitch)
            dirs.append((ce * math.cos(yaw), ce * math.sin(yaw), math.sin(pitch)))
    return tuple(dirs)


_DEPTH_BODY_DIRS = _make_depth_dirs()   # 45 × (fwd, left, up)
# Horizontal yaw angle per ray (for 2D repulsion direction in reactive controller).
_DEPTH_YAW_ANGLES = tuple(math.atan2(dl, df) for df, dl, _ in _DEPTH_BODY_DIRS)


def lidar_ray_angles() -> tuple[float, ...]:
    return _LIDAR_ANGLES


# --------------------------------------------------------------------------- controller
def reactive_goto_controller(slow_radius: float = 2.0,
                             avoid_radius: float = 3.5) -> Controller:
    """Analytic potential-field go-to-goal with reactive teammate/obstacle avoidance.

    Body-frame goal attraction plus inverse-distance repulsion from each sensed
    neighbour and from the nearest scan return. Emits the per-class action shape
    (2D unicycle or 4D holonomic) so the same controller drives mixed teams.

    This is the controller validated to L5 in the 16-scenario suite; it is kept
    byte-identical to the sim copy by the parity test.
    """
    def _avoid_radius_for(vc: VehicleClass) -> float:
        """Effective repulsion radius: the shared default for quad/rover, turn-radius-scaled
        for fixed-wing. A banked turn at cruise speed has physical turn radius v/max_yaw_rate
        (~42 m at 25 m/s / 0.6 rad/s); reacting only at 30 m (a naive speed*time heuristic)
        leaves less room than the turn itself needs, so the plane can't clear an obstacle in
        time — it must start turning at least one turn-radius (plus margin) out."""
        if vc.kinematics is Kinematics.COORDINATED_TURN_3D:
            turn_radius = vc.max_speed_mps / max(vc.max_yaw_rate_radps, 1e-3)
            return max(avoid_radius, turn_radius * 1.5)
        return avoid_radius

    def _repulsion_body(obs: Observation, vc: VehicleClass) -> np.ndarray:
        """Net repulsion vector in body frame (fwd,left,up)."""
        ar = _avoid_radius_for(vc)
        rep = np.zeros(3, dtype=np.float32)
        # neighbors: push directly away from each, stronger when closer
        for _id, rel_body, _vel, ovc in obs.neighbors:
            d = float(np.linalg.norm(rel_body[:2]))
            safe = vc.radius_m + ovc.radius_m + 0.8
            if d < ar and d > 1e-3:
                mag = (ar - d) / ar * (1.0 + safe)
                rep[:2] -= (rel_body[:2] / d) * mag
        # static obstacle: push away from the closest scan ray's direction
        if obs.scan.size and obs.min_clearance < ar:
            ang = (lidar_ray_angles() if vc.sensor is Sensor.LIDAR_360
                   else _DEPTH_YAW_ANGLES)
            k = int(np.argmin(obs.scan))
            a = ang[k]
            mag = (ar - obs.min_clearance) / ar
            rep[0] -= math.cos(a) * mag
            rep[1] -= math.sin(a) * mag
            if vc.sensor is Sensor.FORWARD_DEPTH:
                rep[2] -= _DEPTH_BODY_DIRS[k][2] * mag   # vertical repulsion for quads
        return rep

    def ctl(agent: Agent, obs: Observation) -> np.ndarray:
        vc = agent.vclass
        tf, tl, tu = obs.body_target
        gnorm = max(1e-3, math.sqrt(tf * tf + tl * tl + tu * tu))
        attract = np.array([tf, tl, tu], dtype=np.float32) / gnorm
        rep = _repulsion_body(obs, vc)
        cmd = attract + rep
        gscale = (min(1.0, obs.goal_dist / slow_radius) if obs.goal_dist else 0.0)

        if vc.kinematics is Kinematics.UNICYCLE_2D:
            desired = math.atan2(cmd[1], cmd[0])     # body-frame desired heading
            yaw_cmd = float(np.clip(desired * 2.0, -vc.max_yaw_rate_radps,
                                    vc.max_yaw_rate_radps))
            # slow when turning hard, near goal, or crowded
            speed = vc.max_speed_mps * max(0.0, math.cos(min(abs(desired), math.pi)))
            speed *= gscale
            if obs.min_clearance < vc.radius_m + 1.0:
                speed *= 0.3
            return np.array([max(0.0, speed), yaw_cmd], dtype=np.float32)
        elif vc.kinematics is Kinematics.COORDINATED_TURN_3D:
            # Fixed-wing: hold speed near cruise, steer by banking (yaw_rate), never
            # sideslip (vy always 0 — the Dubins-airplane integrator ignores it anyway).
            ar = _avoid_radius_for(vc)
            desired = math.atan2(cmd[1], cmd[0])     # body-frame desired heading
            yaw_cmd = float(np.clip(desired * 1.5, -vc.max_yaw_rate_radps,
                                    vc.max_yaw_rate_radps))
            # never crawl toward stall; only ease off cruise near the goal
            speed = max(vc.min_speed_mps, vc.max_speed_mps * max(gscale, 0.7))
            vz = cmd[2] * vc.max_speed_mps * gscale
            # blocked ahead with little vertical cue -> climb over
            if obs.min_clearance < ar and abs(tu) < 0.5:
                vz += 0.6 * vc.max_speed_mps
            return np.array([speed, 0.0, vz, yaw_cmd], dtype=np.float32)
        else:
            v = cmd * vc.max_speed_mps
            v[:2] *= max(gscale, 0.5 if np.linalg.norm(rep) > 0 else gscale)
            v[2] = cmd[2] * vc.max_speed_mps * gscale
            # blocked ahead with little vertical cue -> climb over
            if obs.min_clearance < 1.5 and abs(tu) < 0.5:
                v[2] += 0.6 * vc.max_speed_mps
            return np.array([v[0], v[1], v[2], 0.0], dtype=np.float32)
    return ctl
