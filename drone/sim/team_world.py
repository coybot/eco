"""TeamWorld — multi-agent orchestrator for heterogeneous teams.

This is the Phase-0 foundation: it runs a *team* (mixed quad/rover sets) in a
per-tick loop where every agent perceives its teammates as dynamic obstacles, and
exposes a per-agent observation (neighbor states + a comms inbox placeholder) that
the upper layers (comms, localization, scenario, team_runtime) build on.

Design:
  - **Backend-pluggable.** ``WorldBackend`` is the integrate-and-sense interface.
    ``KinematicWorld`` (default) is a pure-Python, GPU-free backend matching the
    kinematic model already proven in training (holonomic 3D quad / unicycle 2D
    rover). Isaac (IsaacVehicleBridge) and Godot (godot_engine) become alternate
    backends implementing the same interface for fidelity / rendering.
  - **Heterogeneity via VehicleClass.** No ``vtype`` string branching here — motion
    integration, sensing modality, speed caps all come from the agent's descriptor
    (vehicle_class.py).
  - **Controller-pluggable.** Each agent is driven by a ``controller(agent, obs) ->
    action`` callable. A simple analytic go-to-goal + reactive-avoid controller ships
    here so the world is runnable/testable today; the ONNX learned policies and the
    team_runtime decision layer plug into the same hook later.

Pure stdlib + numpy; runs headless on CPU.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol
import math

import numpy as np

try:  # packaged (eco.drone.sim) in the repo; flat (sim/ on path) for on-device-style tests
    from .vehicle_class import VehicleClass, Kinematics, Sensor, get_class, Role
except ImportError:
    from vehicle_class import VehicleClass, Kinematics, Sensor, get_class, Role


# --------------------------------------------------------------------------- geometry
@dataclass
class Box:
    """Axis-aligned obstacle. center (3,), half (3,). Agents are also Boxes when sensed."""
    center: np.ndarray
    half: np.ndarray

    @classmethod
    def from_agent(cls, pos: np.ndarray, radius: float) -> "Box":
        h = np.array([radius, radius, radius], dtype=np.float32)
        return cls(pos.astype(np.float32), h)


def _ray_aabb_2d(ox: float, oy: float, dx: float, dy: float, box: Box, max_d: float) -> float:
    """Distance along a 2D ray (unit dir) to a box's XY footprint, or max_d if no hit.

    Slab method, collapsed to XY (matches RoverEnv.lidar_scan but for one ray/box).
    """
    eps = 1e-6
    lo_x, hi_x = box.center[0] - box.half[0], box.center[0] + box.half[0]
    lo_y, hi_y = box.center[1] - box.half[1], box.center[1] + box.half[1]

    def slab(o, d, lo, hi):
        if abs(d) < eps:
            return (-math.inf, math.inf) if lo <= o <= hi else (math.inf, -math.inf)
        t1, t2 = (lo - o) / d, (hi - o) / d
        return (t1, t2) if t1 <= t2 else (t2, t1)

    tnx, txx = slab(ox, dx, lo_x, hi_x)
    tny, txy = slab(oy, dy, lo_y, hi_y)
    tmin, tmax = max(tnx, tny), min(txx, txy)
    if tmax < tmin or tmax < 0:
        return max_d
    t = tmin if tmin >= 0 else 0.0
    return min(t, max_d)


def _ray_aabb_3d(ox: float, oy: float, oz: float,
                 dx: float, dy: float, dz: float,
                 box: Box, max_d: float) -> float:
    """Distance along a 3D unit ray to an AABB, or max_d if no hit. Slab method."""
    eps = 1e-7
    tmin, tmax = -math.inf, math.inf
    for o, d, lo, hi in (
        (ox, dx, box.center[0] - box.half[0], box.center[0] + box.half[0]),
        (oy, dy, box.center[1] - box.half[1], box.center[1] + box.half[1]),
        (oz, dz, box.center[2] - box.half[2], box.center[2] + box.half[2]),
    ):
        if abs(d) < eps:
            if not (lo <= o <= hi):
                return max_d
        else:
            t1, t2 = (lo - o) / d, (hi - o) / d
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
    if tmax < tmin or tmax < 0:
        return max_d
    t = tmin if tmin >= 0 else 0.0
    return min(t, max_d)


# --------------------------------------------------------------------------- agent
@dataclass
class Agent:
    """Runtime state for one team member."""
    id: str
    vclass: VehicleClass
    pos: np.ndarray                      # world (3,) ENU meters
    yaw: float = 0.0                     # rad
    vel: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float32))
    goal: np.ndarray | None = None       # world (3,) target waypoint
    role: Role | None = None
    alive: bool = True
    team: bool = True                    # False = injected intruder/threat (sensed, not a teammate)
    controller: "Controller | None" = None   # per-agent override (e.g. scripted intruder path)
    sensor_ok: bool = True               # False = sensor_dropout inject (scan returns clear)
    # placeholders populated by later phases (kept here so obs shape is stable):
    inbox: list = field(default_factory=list)        # comms messages (Phase 1)
    loc_confidence: float = 1.0                       # localization confidence (Phase 2)

    def __post_init__(self):
        self.pos = np.asarray(self.pos, dtype=np.float32).reshape(3)
        if self.role is None:
            self.role = self.vclass.preferred_role


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


# --------------------------------------------------------------------------- backend
class WorldBackend(Protocol):
    """Integrate-and-sense interface. KinematicWorld is the default impl."""
    def obstacles(self) -> list[Box]: ...
    def integrate(self, agent: Agent, action: np.ndarray, dt: float) -> None: ...


class KinematicWorld:
    """Pure-Python kinematic backend (no GPU/binary). Static AABB obstacles only.

    Motion model is selected per agent from its VehicleClass.kinematics — exactly the
    integrators used in training (RoverEnv unicycle, BoxEnv holonomic).
    """
    SENSE_MAX = 10.0   # m, matches DEPTH_MAX / LIDAR_MAX

    def __init__(self, static_obstacles: list[Box] | None = None):
        self._static = list(static_obstacles or [])
        self.wind = np.zeros(3, dtype=np.float32)   # world-frame additive drift (wind_shift inject)

    def obstacles(self) -> list[Box]:
        return self._static

    def add_obstacle(self, box: Box) -> None:
        self._static.append(box)

    def integrate(self, agent: Agent, action: np.ndarray, dt: float) -> None:
        vc = agent.vclass
        if vc.kinematics is Kinematics.UNICYCLE_2D:
            v = float(np.clip(action[0], -vc.max_speed_mps, vc.max_speed_mps))
            w = float(np.clip(action[1], -vc.max_yaw_rate_radps, vc.max_yaw_rate_radps))
            agent.yaw = _wrap_pi(agent.yaw + w * dt)
            c, s = math.cos(agent.yaw), math.sin(agent.yaw)
            agent.pos[0] += (c * v + self.wind[0]) * dt
            agent.pos[1] += (s * v + self.wind[1]) * dt
            agent.pos[2] = 0.0
            agent.vel = np.array([c * v, s * v, 0.0], dtype=np.float32)
        else:  # HOLONOMIC_3D: action [vx,vy,vz,yaw_rate] in body frame
            spd = vc.max_speed_mps
            vx = float(np.clip(action[0], -spd, spd))
            vy = float(np.clip(action[1], -spd, spd))
            vz = float(np.clip(action[2], -spd, spd))
            w = float(np.clip(action[3], -vc.max_yaw_rate_radps, vc.max_yaw_rate_radps))
            agent.yaw = _wrap_pi(agent.yaw + w * dt)
            c, s = math.cos(agent.yaw), math.sin(agent.yaw)
            wvx, wvy = c * vx - s * vy, s * vx + c * vy
            agent.pos[0] += (wvx + self.wind[0]) * dt
            agent.pos[1] += (wvy + self.wind[1]) * dt
            agent.pos[2] = max(0.0, agent.pos[2] + vz * dt)
            agent.vel = np.array([wvx, wvy, vz], dtype=np.float32)


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


# 360° lidar ray bearings in body frame (0=fwd, CCW), matches rover_contract LIDAR_RAYS=72.
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


def _seg_dist_from_origin(ax, ay, az, bx, by, bz):
    """Distance from the origin (sensor) to the segment [(ax,ay,az),(bx,by,bz)]."""
    dx, dy, dz = bx - ax, by - ay, bz - az
    L2 = dx * dx + dy * dy + dz * dz
    if L2 < 1e-9:
        return math.sqrt(ax * ax + ay * ay + az * az)
    t = -(ax * dx + ay * dy + az * dz) / L2
    t = max(0.0, min(1.0, t))
    cx, cy, cz = ax + t * dx, ay + t * dy, az + t * dz
    return math.sqrt(cx * cx + cy * cy + cz * cz)


def reconstruct_surface_clearance(scan: np.ndarray, sensor: Sensor,
                                  max_d: float = 10.0) -> float:
    """Estimate the nearest *surface* distance from discrete beam returns.

    Naive ray-min (``scan.min()``) overreads clearance when a surface — a corner or
    an obstacle edge — falls *between* beams: the perpendicular distance to the
    surface is shorter than any single beam's range. This reconstructs the local
    surface by joining adjacent hits into segments and taking the perpendicular
    distance from the sensor to the nearest segment. Standard lidar/depth practice;
    recovers true-surface-like clearance a real robot can actually compute.

    Only joins *adjacent hits* (both below max range) so no-return rays don't
    fabricate a spurious near surface.
    """
    if not scan.size:
        return max_d
    best = float(scan.min())
    if sensor is Sensor.LIDAR_360:
        ang = _LIDAR_ANGLES
        n = len(scan)
        pts = [(scan[i] * math.cos(ang[i]), scan[i] * math.sin(ang[i]), 0.0)
               for i in range(n)]
        for i in range(n):
            j = (i + 1) % n
            if scan[i] >= max_d or scan[j] >= max_d:
                continue
            best = min(best, _seg_dist_from_origin(*pts[i], *pts[j]))
    else:  # FORWARD_DEPTH 5×9 grid — join horizontal + vertical neighbours
        pts = [(scan[k] * _DEPTH_BODY_DIRS[k][0],
                scan[k] * _DEPTH_BODY_DIRS[k][1],
                scan[k] * _DEPTH_BODY_DIRS[k][2]) for k in range(len(scan))]
        for r in range(_DEPTH_ROWS):
            for c in range(_DEPTH_COLS):
                k = r * _DEPTH_COLS + c
                if scan[k] >= max_d:
                    continue
                for dk in (1 if c + 1 < _DEPTH_COLS else 0,
                           _DEPTH_COLS if r + 1 < _DEPTH_ROWS else 0):
                    if dk and scan[k + dk] < max_d:
                        best = min(best, _seg_dist_from_origin(*pts[k], *pts[k + dk]))
    return best


# --------------------------------------------------------------------------- world
class TeamWorld:
    """Owns the team + backend; runs the per-tick perceive→decide→integrate loop."""

    NEIGHBOR_RANGE = 12.0   # m: teammates within this radius are sensed
    COLLISION_PAD = 0.05    # m

    def __init__(self, backend: WorldBackend | None = None, dt: float = 0.1,
                 sensing: str = "ideal"):
        self.backend = backend or KinematicWorld()
        self.dt = dt
        self.agents: dict[str, Agent] = {}
        self.t = 0.0
        self.tick = 0
        # Sensing model for the clearance the controller sees:
        #   "ideal"     — true perpendicular obstacle-surface distance (omniscient;
        #                 the published L5 baseline runs on this).
        #   "realistic" — what a real sensor gives: nearest return of the modality
        #                 scan (ray/beam), i.e. no true-surface oracle. Stages 1–3
        #                 close the gap between this and "ideal".
        self.sensing = sensing
        # Optional sensor-noise model for realistic/reconstructed modes (Stage 4):
        #   {"range_std": m, "dropout": p}. When set, each scan return gets Gaussian
        #   range noise and each ray independently drops out (→ max range) with prob p,
        #   before clearance is computed. Seeded via set_sensor_noise for reproducible
        #   Monte-Carlo. None = perfect returns (deterministic).
        self.sensor_noise: dict | None = None
        self._noise_rng = None
        # hooks injected by later phases (identity defaults keep Phase 0 standalone):
        self.comms = None          # Phase 1: CommsFabric (delivers inboxes)
        self.localization = None   # Phase 2: localization fabric (perturbs sensed pose)

    # -- team construction ------------------------------------------------------
    def add_agent(self, agent_id: str, vclass_name: str, pos, yaw: float = 0.0,
                  goal=None, role: Role | None = None, team: bool = True,
                  controller: "Controller | None" = None) -> Agent:
        vc = get_class(vclass_name)
        a = Agent(id=agent_id, vclass=vc, pos=np.asarray(pos, dtype=np.float32),
                  yaw=yaw, role=role, team=team, controller=controller,
                  goal=None if goal is None else np.asarray(goal, dtype=np.float32))
        self.agents[agent_id] = a
        return a

    def team_agents(self) -> list[Agent]:
        return [a for a in self.agents.values() if a.alive and a.team]

    def add_roster(self, roster: list[dict]) -> None:
        """roster: [{'id','type','pos'[,'yaw','goal','role']}, ...] (type = class name)."""
        for spec in roster:
            self.add_agent(spec["id"], spec["type"], spec["pos"],
                           yaw=spec.get("yaw", 0.0), goal=spec.get("goal"),
                           role=spec.get("role"))

    def live_agents(self) -> list[Agent]:
        return [a for a in self.agents.values() if a.alive]

    # -- sensing ----------------------------------------------------------------
    def _moving_obstacles(self, me: Agent) -> list[Box]:
        """Other live agents as AABB boxes (teammates are dynamic obstacles)."""
        out = []
        for other in self.agents.values():
            if other.id == me.id or not other.alive:
                continue
            out.append(Box.from_agent(other.pos, other.vclass.radius_m))
        return out

    def _neighbors(self, me: Agent) -> list:
        out = []
        c, s = math.cos(-me.yaw), math.sin(-me.yaw)
        for other in self.agents.values():
            if other.id == me.id or not other.alive:
                continue
            d = other.pos - me.pos
            if np.linalg.norm(d[:2]) > self.NEIGHBOR_RANGE:
                continue
            rel_body = np.array([c * d[0] - s * d[1], s * d[0] + c * d[1], d[2]],
                                dtype=np.float32)
            out.append((other.id, rel_body, other.vel.copy(), other.vclass))
        return out

    def set_sensor_noise(self, range_std: float = 0.0, dropout: float = 0.0,
                         seed: int = 0) -> None:
        """Enable a seeded sensor-noise model for realistic/reconstructed sensing."""
        self.sensor_noise = {"range_std": float(range_std), "dropout": float(dropout)}
        self._noise_rng = np.random.default_rng(seed)

    def _apply_sensor_noise(self, scan: np.ndarray, max_d: float) -> np.ndarray:
        if not self.sensor_noise or self._noise_rng is None:
            return scan
        rng = self._noise_rng
        std = self.sensor_noise.get("range_std", 0.0)
        drop = self.sensor_noise.get("dropout", 0.0)
        out = scan.astype(np.float32).copy()
        if std > 0:
            out = out + rng.normal(0.0, std, size=out.shape).astype(np.float32)
        if drop > 0:
            out[rng.random(out.shape) < drop] = max_d   # missed return → reports clear
        return np.clip(out, 0.0, max_d)

    def _scan(self, me: Agent, boxes: list[Box]) -> tuple[np.ndarray, float]:
        """Sensing-modality output + min clearance. Modality from VehicleClass.sensor."""
        max_d = KinematicWorld.SENSE_MAX
        n_rays = _LIDAR_RAYS if me.vclass.sensor is Sensor.LIDAR_360 else _DEPTH_COLS * _DEPTH_ROWS
        if not me.sensor_ok:   # sensor_dropout inject: blind (reports all-clear)
            return np.full(n_rays, max_d, dtype=np.float32), max_d
        px, py, pz = float(me.pos[0]), float(me.pos[1]), float(me.pos[2])
        if me.vclass.sensor is Sensor.LIDAR_360:
            angles = lidar_ray_angles()
            scan = np.full(len(angles), max_d, dtype=np.float32)
            for i, a in enumerate(angles):
                wa = me.yaw + a
                dx, dy = math.cos(wa), math.sin(wa)
                d = max_d
                for b in boxes:
                    d = min(d, _ray_aabb_2d(px, py, dx, dy, b, max_d))
                scan[i] = d
        else:
            # FORWARD_DEPTH: full 5×9 = 45-ray 2D depth grid matching contract.py.
            # Body dirs (fwd, left, up) rotated by agent yaw into world frame.
            c_yaw, s_yaw = math.cos(me.yaw), math.sin(me.yaw)
            scan = np.full(_DEPTH_COLS * _DEPTH_ROWS, max_d, dtype=np.float32)
            for i, (df, dl, du) in enumerate(_DEPTH_BODY_DIRS):
                wx = c_yaw * df - s_yaw * dl
                wy = s_yaw * df + c_yaw * dl
                d = max_d
                for b in boxes:
                    d = min(d, _ray_aabb_3d(px, py, pz, wx, wy, du, b, max_d))
                scan[i] = d
        if self.sensing in ("realistic", "reconstructed"):
            scan = self._apply_sensor_noise(scan, max_d)   # Stage 4: seeded noise/dropout
        if self.sensing == "realistic":
            # Realizable clearance: the nearest scan return only — no true-surface
            # oracle. This is what the flight code (onboard_l5) naively has.
            min_clear = float(scan.min()) if scan.size else max_d
        elif self.sensing == "reconstructed":
            # Stage 1: realizable sensing + local surface reconstruction — the
            # nearest-surface estimate a real robot computes from discrete beams.
            min_clear = reconstruct_surface_clearance(scan, me.vclass.sensor, max_d)
        else:
            min_clear = self._min_surface_dist(me, boxes)
        return scan, min_clear

    def _min_surface_dist(self, me: Agent, boxes: list[Box]) -> float:
        best = math.inf
        for b in boxes:
            dx = max(0.0, abs(me.pos[0] - b.center[0]) - b.half[0])
            dy = max(0.0, abs(me.pos[1] - b.center[1]) - b.half[1])
            dz = max(0.0, abs(me.pos[2] - b.center[2]) - b.half[2])
            best = min(best, math.sqrt(dx * dx + dy * dy + dz * dz))
        return best if best != math.inf else KinematicWorld.SENSE_MAX

    def observe(self, me: Agent) -> Observation:
        boxes = self.backend.obstacles() + self._moving_obstacles(me)
        # localization fabric (Phase 2) may perturb the pose the agent believes it has
        believed = me.pos if self.localization is None else self.localization.believed_pos(me)
        loc_conf = me.loc_confidence if self.localization is None \
            else self.localization.confidence(me)
        if me.goal is None:
            body_t = np.zeros(3, dtype=np.float32)
            gdist = 0.0
        else:
            d = me.goal - believed
            c, s = math.cos(-me.yaw), math.sin(-me.yaw)
            body_t = np.array([c * d[0] - s * d[1], s * d[0] + c * d[1], d[2]],
                              dtype=np.float32)
            gdist = float(np.linalg.norm(d))
        scan, min_clear = self._scan(me, boxes)
        return Observation(
            agent_id=me.id, body_target=body_t, goal_dist=gdist,
            neighbors=self._neighbors(me), scan=scan, min_clearance=min_clear,
            loc_confidence=loc_conf, inbox=list(me.inbox),
        )

    # -- step -------------------------------------------------------------------
    def step(self, controller: Controller) -> dict:
        """Advance one tick: localization, comms, perceive, decide, integrate."""
        if self.localization is not None:
            self.localization.update(self)     # Phase 2: advance pose estimators
        if self.comms is not None:
            self.comms.deliver(self)           # Phase 1: fill inboxes
        collisions = 0
        for a in self.live_agents():
            obs = self.observe(a)
            a._last_obs = obs                  # cache for smart layer scan access
            ctl = a.controller or controller   # per-agent override (scripted intruders)
            action = ctl(a, obs)
            self.backend.integrate(a, np.asarray(action, dtype=np.float32), self.dt)
        # collision bookkeeping (after motion)
        for a in self.live_agents():
            boxes = self.backend.obstacles() + self._moving_obstacles(a)
            if self._min_surface_dist(a, boxes) < a.vclass.radius_m + self.COLLISION_PAD:
                collisions += 1
        self.t += self.dt
        self.tick += 1
        return {"t": self.t, "tick": self.tick, "collisions": collisions}

    def all_reached(self, tol: float = 1.0) -> bool:
        for a in self.team_agents():
            if a.goal is None:
                continue
            if np.linalg.norm(a.goal - a.pos) > tol:
                return False
        return True


# --------------------------------------------------------------------------- baseline controller
def reactive_goto_controller(slow_radius: float = 2.0,
                             avoid_radius: float = 3.5) -> Controller:
    """Analytic potential-field go-to-goal with reactive teammate/obstacle avoidance.

    A dependency-free baseline so TeamWorld is runnable before the ONNX policies are
    wired in. Body-frame goal attraction plus inverse-distance repulsion from each
    sensed neighbor and from the nearest scan return. Emits the per-class action shape
    (2D unicycle or 4D holonomic) so the same controller drives mixed teams. Tight
    multi-way conflicts are the job of the learned policy + Phase-4 deconfliction; this
    baseline keeps separation in moderate density.
    """
    def _repulsion_body(obs: Observation, vc: VehicleClass) -> np.ndarray:
        """Net repulsion vector in body frame (fwd,left,up)."""
        rep = np.zeros(3, dtype=np.float32)
        # neighbors: push directly away from each, stronger when closer
        for _id, rel_body, _vel, ovc in obs.neighbors:
            d = float(np.linalg.norm(rel_body[:2]))
            safe = vc.radius_m + ovc.radius_m + 0.8
            if d < avoid_radius and d > 1e-3:
                mag = (avoid_radius - d) / avoid_radius * (1.0 + safe)
                rep[:2] -= (rel_body[:2] / d) * mag
        # static obstacle: push away from the closest scan ray's direction
        if obs.scan.size and obs.min_clearance < avoid_radius:
            ang = (lidar_ray_angles() if vc.sensor is Sensor.LIDAR_360
                   else _DEPTH_YAW_ANGLES)
            k = int(np.argmin(obs.scan))
            a = ang[k]
            mag = (avoid_radius - obs.min_clearance) / avoid_radius
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
        else:
            v = cmd * vc.max_speed_mps
            v[:2] *= max(gscale, 0.5 if np.linalg.norm(rep) > 0 else gscale)
            v[2] = cmd[2] * vc.max_speed_mps * gscale
            # blocked ahead with little vertical cue -> climb over
            if obs.min_clearance < 1.5 and abs(tu) < 0.5:
                v[2] += 0.6 * vc.max_speed_mps
            return np.array([v[0], v[1], v[2], 0.0], dtype=np.float32)
    return ctl
