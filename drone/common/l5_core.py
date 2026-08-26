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
        ray_table, lidar_ray_angles,
        _LIDAR_RAYS, _LIDAR_ANGLES, _DEPTH_COLS, _DEPTH_ROWS,
        _DEPTH_HFOV, _DEPTH_VFOV, _DEPTH_BODY_DIRS, _DEPTH_YAW_ANGLES,
    )
except ImportError:  # pragma: no cover - device flat-install path
    from vehicle_class import (  # type: ignore
        VehicleClass, Kinematics, Sensor, Role, get_class,
        ray_table, lidar_ray_angles,
        _LIDAR_RAYS, _LIDAR_ANGLES, _DEPTH_COLS, _DEPTH_ROWS,
        _DEPTH_HFOV, _DEPTH_VFOV, _DEPTH_BODY_DIRS, _DEPTH_YAW_ANGLES,
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
# Ray geometry lives in vehicle_class (one definition, imported by both this module and the
# sim copy) and is re-exported here under the historical names: onboard_l5 and the sim tests
# import _DEPTH_BODY_DIRS / _DEPTH_COLS / _LIDAR_RAYS from here by name.
# Resolve per-class geometry with ray_table(vc.sensor), never by assuming 45 rays.


# --------------------------------------------------------------------------- controller
def reactive_goto_controller(slow_radius: float | None = None,
                             avoid_radius: float | None = None) -> Controller:
    """Analytic potential-field go-to-goal with reactive teammate/obstacle avoidance.

    Body-frame goal attraction plus inverse-distance repulsion from each sensed
    neighbour and from the nearest scan return. Emits the per-class action shape
    (2D unicycle or 4D holonomic) so the same controller drives mixed teams.

    ``slow_radius`` / ``avoid_radius`` default to the *vehicle class's* length scales
    (``vc.slow_radius_m`` / ``vc.avoid_radius_m``), which carry the historically-tuned
    3.5/2.0 m values for the outdoor classes. Passing them explicitly still overrides,
    as the SITL harnesses and unit tests do. Scale must be per-class because a 0.065 m
    micro-UAV in a 2.4 m room is permanently repelled by every wall at a 3.5 m radius.

    This is the controller validated to L5 in the 16-scenario suite; it is kept
    byte-identical to the sim copy by the parity test.
    """
    def _avoid_radius_for(vc: VehicleClass) -> float:
        """Effective repulsion radius: the class's own scale for quad/rover/micro, then
        turn-radius-scaled for fixed-wing. A banked turn at cruise speed has physical turn
        radius v/max_yaw_rate (~42 m at 25 m/s / 0.6 rad/s); reacting only at 30 m (a naive
        speed*time heuristic) leaves less room than the turn itself needs, so the plane can't
        clear an obstacle in time — it must start turning at least one turn-radius (plus
        margin) out."""
        base = avoid_radius if avoid_radius is not None else vc.avoid_radius_m
        if vc.kinematics is Kinematics.COORDINATED_TURN_3D:
            turn_radius = vc.max_speed_mps / max(vc.max_yaw_rate_radps, 1e-3)
            return max(base, turn_radius * 1.5)
        return base

    def _repulsion_body(obs: Observation, vc: VehicleClass) -> np.ndarray:
        """Net repulsion vector in body frame (fwd,left,up)."""
        ar = _avoid_radius_for(vc)
        rep = np.zeros(3, dtype=np.float32)
        # neighbors: push directly away from each, stronger when closer
        for _id, rel_body, _vel, ovc in obs.neighbors:
            d = float(np.linalg.norm(rel_body[:2]))
            safe = vc.radius_m + ovc.radius_m + vc.neighbor_safe_pad_m
            if d < ar and d > 1e-3:
                mag = (ar - d) / ar * (1.0 + safe)
                rep[:2] -= (rel_body[:2] / d) * mag
        # static obstacle: push away from the closest scan ray's direction
        if obs.scan.size and obs.min_clearance < ar:
            rt = ray_table(vc.sensor)
            k = int(np.argmin(obs.scan))
            a = rt.yaw_angles[k]
            mag = (ar - obs.min_clearance) / ar
            rep[0] -= math.cos(a) * mag
            rep[1] -= math.sin(a) * mag
            if rt.vertical_cue:
                # vertical repulsion where the rays carry pitch (depth grid, hybrid);
                # a horizontal lidar ring has no vertical cue to offer.
                rep[2] -= rt.dirs[k][2] * mag
        return rep

    def ctl(agent: Agent, obs: Observation) -> np.ndarray:
        vc = agent.vclass
        tf, tl, tu = obs.body_target
        gnorm = max(1e-3, math.sqrt(tf * tf + tl * tl + tu * tu))
        attract = np.array([tf, tl, tu], dtype=np.float32) / gnorm
        rep = _repulsion_body(obs, vc)
        cmd = attract + rep
        sr = slow_radius if slow_radius is not None else vc.slow_radius_m
        gscale = (min(1.0, obs.goal_dist / sr) if obs.goal_dist else 0.0)

        if vc.kinematics is Kinematics.UNICYCLE_2D:
            desired = math.atan2(cmd[1], cmd[0])     # body-frame desired heading
            yaw_cmd = float(np.clip(desired * 2.0, -vc.max_yaw_rate_radps,
                                    vc.max_yaw_rate_radps))
            # slow when turning hard, near goal, or crowded
            speed = vc.max_speed_mps * max(0.0, math.cos(min(abs(desired), math.pi)))
            speed *= gscale
            if obs.min_clearance < vc.radius_m + vc.clearance_slow_pad_m:
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
            if obs.min_clearance < vc.climb_over_clearance_m and abs(tu) < 0.5:
                v[2] += 0.6 * vc.max_speed_mps
            return np.array([v[0], v[1], v[2], 0.0], dtype=np.float32)
    return ctl
