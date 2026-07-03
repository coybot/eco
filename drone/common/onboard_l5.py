"""On-device L5 runtime — runs the L5-validated controller on real hardware.

This is the adapter that turns "the L5 controller is in the repo" into "this
aircraft actually runs the L5 controller". Each vehicle (quad or rover) runs its
own instance of this loop, decentralised: it senses locally, learns its teammates'
positions over the peer link (``team_link.py``), decides with the exact
``reactive_goto_controller`` + ``RuleBasedSmart`` that scored L5 in sim, and emits
a body-frame velocity setpoint to the flight controller.

The controller/advisor code is imported from ``l5_core`` / ``l5_smart``, which a
parity test pins byte-for-byte to the simulator. So the decision logic that flies
is identical to the decision logic that was validated — the only sim-to-real gap
is in how the ``Observation`` is assembled from real sensors instead of ground
truth (documented per field below).

**Testability.** All I/O is injected via small provider objects (telemetry,
sensor, peers, actuator). That lets the SITL harness and unit tests drive the
exact flight loop with no hardware, and lets the Jetson wiring stay thin.

Frames:
  * World: ENU metres from takeoff origin (matches team_link pos_enu).
  * Body:  x=forward, y=left, z=up (controller convention).
  * MAVLink body velocity: x=forward, y=right, z=down — the actuator converts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol
import math

import numpy as np

try:
    from .l5_core import (
        Agent, Observation, reactive_goto_controller, get_class,
        VehicleClass, Kinematics, Sensor,
        _LIDAR_RAYS, _DEPTH_COLS, _DEPTH_ROWS,
    )
    from .l5_smart import RuleBasedSmart, WorldState, AgentSnapshot
except ImportError:  # pragma: no cover - device flat-install path
    from l5_core import (
        Agent, Observation, reactive_goto_controller, get_class,
        VehicleClass, Kinematics, Sensor,
        _LIDAR_RAYS, _DEPTH_COLS, _DEPTH_ROWS,
    )
    from l5_smart import RuleBasedSmart, WorldState, AgentSnapshot

SENSE_MAX = 10.0        # m, matches KinematicWorld.SENSE_MAX / DEPTH_MAX / LIDAR_MAX
NEIGHBOR_RANGE = 12.0   # m, matches TeamWorld.NEIGHBOR_RANGE


# --------------------------------------------------------------------------- providers
@dataclass
class Pose:
    pos_enu: np.ndarray      # (3,) world ENU metres
    yaw_rad: float
    vel_enu: np.ndarray      # (3,) world ENU m/s
    loc_confidence: float = 1.0
    sensor_ok: bool = True


@dataclass
class Peer:
    id: str
    pos_enu: np.ndarray      # (3,)
    vel_enu: np.ndarray      # (3,)
    vclass_name: str         # "quad" | "rover" | ...
    confidence: float = 1.0
    alive: bool = True


class TelemetryProvider(Protocol):
    def pose(self) -> Pose: ...


class SensorProvider(Protocol):
    def scan(self) -> np.ndarray:
        """Return the class-appropriate ray array in the controller's body
        convention: 45 rays (5×9 depth grid, _DEPTH_BODY_DIRS order) for a quad,
        72 rays (lidar ring, index i = body angle 2πi/72, 0=forward CCW) for a
        rover. Missing/out-of-range returns are SENSE_MAX (clear)."""
        ...


class PeerProvider(Protocol):
    def peers(self) -> list[Peer]: ...


class Actuator(Protocol):
    def send(self, action: np.ndarray, vclass: VehicleClass, dt: float) -> None: ...


# --------------------------------------------------------------------------- helpers
def _world_to_body(d: np.ndarray, yaw: float) -> np.ndarray:
    """Rotate a world ENU vector into body frame (x=fwd,y=left,z=up).

    Identical rotation to TeamWorld._neighbors / observe: c,s = cos(-yaw),sin(-yaw).
    """
    c, s = math.cos(-yaw), math.sin(-yaw)
    return np.array([c * d[0] - s * d[1], s * d[0] + c * d[1], d[2]], dtype=np.float32)


def build_observation(agent: Agent, pose: Pose, scan: np.ndarray,
                      peers: list[Peer]) -> Observation:
    """Assemble the exact Observation the controller expects, from real inputs.

    Field-by-field this mirrors TeamWorld.observe; the documented sim-to-real
    differences are:
      * min_clearance: sim uses true obstacle-box surface distance; on hardware we
        use the nearest scan return (obstacles) combined with teammate surface
        distance from the peer link — the best available real analog.
      * scan: from the depth camera / lidar rather than ray-cast AABBs.
    """
    believed = np.asarray(pose.pos_enu, dtype=np.float32).reshape(3)

    # body_target + goal_dist
    if agent.goal is None:
        body_t = np.zeros(3, dtype=np.float32)
        gdist = 0.0
    else:
        d = np.asarray(agent.goal, dtype=np.float32) - believed
        body_t = _world_to_body(d, pose.yaw_rad)
        gdist = float(np.linalg.norm(d))

    # neighbors within range, relative pos rotated to body frame
    neighbors = []
    teammate_surf = math.inf
    for p in peers:
        if not p.alive:
            continue
        d = np.asarray(p.pos_enu, dtype=np.float32).reshape(3) - believed
        if float(np.linalg.norm(d[:2])) > NEIGHBOR_RANGE:
            continue
        rel_body = _world_to_body(d, pose.yaw_rad)
        ovc = get_class(p.vclass_name)
        neighbors.append((p.id, rel_body, np.asarray(p.vel_enu, dtype=np.float32), ovc))
        # centre-to-centre minus both radii ≈ surface distance to this teammate
        surf = float(np.linalg.norm(d)) - agent.vclass.radius_m - ovc.radius_m
        teammate_surf = min(teammate_surf, max(0.0, surf))

    scan = np.asarray(scan, dtype=np.float32)
    if not pose.sensor_ok:  # sensor dropout: report all-clear (matches sim blind path)
        n_rays = _LIDAR_RAYS if agent.vclass.sensor is Sensor.LIDAR_360 \
            else _DEPTH_COLS * _DEPTH_ROWS
        scan = np.full(n_rays, SENSE_MAX, dtype=np.float32)
        min_clear = SENSE_MAX
    else:
        min_clear = float(scan.min()) if scan.size else SENSE_MAX
        min_clear = min(min_clear, teammate_surf)

    return Observation(
        agent_id=agent.id, body_target=body_t, goal_dist=gdist,
        neighbors=neighbors, scan=scan, min_clearance=min_clear,
        loc_confidence=pose.loc_confidence, inbox=[],
    )


def _agent_snapshot(agent: Agent, pose: Pose, min_scan: float) -> AgentSnapshot:
    return AgentSnapshot(
        id=agent.id, pos=[float(x) for x in pose.pos_enu],
        vel=[float(x) for x in pose.vel_enu],
        goal=(None if agent.goal is None else [float(x) for x in agent.goal]),
        alive=True, sensor_ok=pose.sensor_ok, confidence=pose.loc_confidence,
        vclass=agent.vclass.name, min_scan_dist=min_scan,
    )


def _peer_snapshot(p: Peer) -> AgentSnapshot:
    return AgentSnapshot(
        id=p.id, pos=[float(x) for x in p.pos_enu], vel=[float(x) for x in p.vel_enu],
        goal=None, alive=p.alive, sensor_ok=True, confidence=p.confidence,
        vclass=get_class(p.vclass_name).name, min_scan_dist=SENSE_MAX,
    )


# --------------------------------------------------------------------------- runtime
class OnboardL5Runtime:
    """Per-vehicle decentralised L5 loop. One instance per aircraft.

    Wire it with a TelemetryProvider (flight-controller pose), a SensorProvider
    (depth/lidar), a PeerProvider (team_link), and an Actuator (MAVLink velocity).
    Call :meth:`step` at your control rate (e.g. 10 Hz).
    """

    def __init__(self, agent_id: str, vclass_name: str, goal_enu,
                 telemetry: TelemetryProvider, sensor: SensorProvider,
                 peers: PeerProvider, actuator: Actuator,
                 use_smart: bool = True):
        self.agent = Agent(id=agent_id, vclass=get_class(vclass_name),
                           pos=np.zeros(3, dtype=np.float32),
                           goal=None if goal_enu is None
                           else np.asarray(goal_enu, dtype=np.float32))
        self.telemetry = telemetry
        self.sensor = sensor
        self.peers = peers
        self.actuator = actuator
        self.controller = reactive_goto_controller()
        self.smart = RuleBasedSmart() if use_smart else None
        self.last_action: np.ndarray | None = None
        self.last_obs: Observation | None = None

    def set_goal(self, goal_enu) -> None:
        self.agent.goal = None if goal_enu is None else np.asarray(goal_enu, dtype=np.float32)

    def at_goal(self, tol: float = 1.5) -> bool:
        if self.agent.goal is None:
            return True
        return float(np.linalg.norm(self.agent.goal - self.agent.pos)) < tol

    def step(self, dt: float) -> np.ndarray:
        """One control tick: sense → decide (controller + smart) → actuate."""
        pose = self.telemetry.pose()
        self.agent.pos = np.asarray(pose.pos_enu, dtype=np.float32).reshape(3)
        self.agent.yaw = float(pose.yaw_rad)
        self.agent.vel = np.asarray(pose.vel_enu, dtype=np.float32).reshape(3)

        peers = self.peers.peers()
        scan = self.sensor.scan()
        obs = build_observation(self.agent, pose, scan, peers)
        action = self.controller(self.agent, obs)

        # Fleet advisor: build the world state this agent can see, tick, apply own directives.
        if self.smart is not None:
            snaps = [_agent_snapshot(self.agent, pose, obs.min_clearance)]
            snaps += [_peer_snapshot(p) for p in peers]
            ws = WorldState(
                t=0.0, agents=snaps, targets=[], interventions=0,
                comms_delivery_rate=1.0, active_injects=[],
            )
            self.smart.tick(ws)
            for d in self.smart.directives_for(self.agent.id):
                action = d.apply(self.agent, action)

        self.actuator.send(action, self.agent.vclass, dt)
        self.last_action = action
        self.last_obs = obs
        return action


# --------------------------------------------------------------------------- MAVLink actuator
class MavlinkActuator:
    """Maps a controller action to a MAVLink body-frame velocity setpoint.

    Controller body frame is (fwd, left, up); MAVLink BODY_OFFSET_NED is
    (fwd, right, down) — same convention run_prompt.send_velocity uses:
        quad  [vx,vy,vz,yaw_rate] -> send_velocity(vx, -vy, -vz, yaw_rate)
        rover [v, yaw_rate]        -> send_velocity(v, 0, 0, yaw_rate)
    """

    def __init__(self, mav, send_velocity: Callable | None = None):
        self.mav = mav
        if send_velocity is None:
            try:
                from run_prompt import send_velocity as _sv  # device flat import
            except ImportError:
                from .run_prompt import send_velocity as _sv  # type: ignore
            send_velocity = _sv
        self._send_velocity = send_velocity

    def send(self, action: np.ndarray, vclass: VehicleClass, dt: float) -> None:
        a = np.asarray(action, dtype=np.float32)
        if vclass.kinematics is Kinematics.UNICYCLE_2D:
            v, yaw_rate = float(a[0]), float(a[1])
            self._send_velocity(self.mav, v, 0.0, 0.0, yaw_rate, dt)
        else:
            vx, vy, vz, yaw_rate = (float(a[0]), float(a[1]), float(a[2]), float(a[3]))
            # body (fwd,left,up) -> MAVLink body-NED (fwd,right,down)
            self._send_velocity(self.mav, vx, -vy, -vz, yaw_rate, dt)
