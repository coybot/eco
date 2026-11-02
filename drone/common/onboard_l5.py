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

**Team allocation.** Where the goal comes from is pluggable: pass a
``TeamAllocator`` (plus the ``team_link`` it talks over) and every tick the loop
asks it for this vehicle's goal and broadcasts whatever messages it returns.
``StaticGoalAllocator`` is the trivial one (a pre-briefed goal list, in order);
the real negotiated allocator lives in ``team_alloc.py``. Without an allocator
the loop flies the fixed goal it was constructed with, exactly as before.

Frames:
  * World: ENU metres from takeoff origin (matches team_link pos_enu).
  * Body:  x=forward, y=left, z=up (controller convention).
  * MAVLink body velocity: x=forward, y=right, z=down — the actuator converts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional, Protocol
import logging
import math
import time

import numpy as np

try:
    from .l5_core import (
        Agent, Observation, reactive_goto_controller, get_class,
        VehicleClass, Kinematics, Sensor,
        _LIDAR_RAYS, _DEPTH_COLS, _DEPTH_ROWS,
    )
    from .l5_smart import RuleBasedSmart, WorldState, AgentSnapshot, snapshot_caps
except ImportError:  # pragma: no cover - device flat-install path
    from l5_core import (
        Agent, Observation, reactive_goto_controller, get_class,
        VehicleClass, Kinematics, Sensor,
        _LIDAR_RAYS, _DEPTH_COLS, _DEPTH_ROWS,
    )
    from l5_smart import RuleBasedSmart, WorldState, AgentSnapshot, snapshot_caps

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from .team_link import PeerState

logger = logging.getLogger(__name__)

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


class TeamAllocator(Protocol):
    """Decides this vehicle's goal from what the team link says. Called once per tick.

    ``me`` is this vehicle's AgentSnapshot (the same one the smart layer sees),
    ``peers`` is ``TeamLink.peers()`` (live teammates only), and ``inbox`` is every
    team-link message received since the previous call, normalised so each dict has
    ``type``, ``sender`` and ``stamp`` alongside its payload fields. Exceptions are
    not caught here: an allocator that fails stops the loop, and the caller's
    failure path decides what the vehicle does.
    """

    def step(self, now_s: float, me: "AgentSnapshot", peers: "dict[str, PeerState]",
             inbox: "list[dict]") -> "tuple[Optional[list[float]], list[dict]]":
        """Return (goal_enu or None to hold, messages to broadcast this tick)."""
        ...


class StaticGoalAllocator:
    """The trivial TeamAllocator: fly a pre-briefed goal list in order, then hold.

    Ignores the team entirely (no messages in, none out). A goal counts as reached
    within ``arrive_tol_m`` (default: the vehicle class's own ``goal_lock_m``, the
    radius the smart layer already treats as "at goal"). ``done`` turns True once
    the last goal is reached; from then on ``step`` returns None (hold).
    """

    def __init__(self, goals, arrive_tol_m: Optional[float] = None):
        self.goals = [[float(x) for x in g] for g in goals]
        self.arrive_tol_m = arrive_tol_m
        self.index = 0

    @property
    def done(self) -> bool:
        return self.index >= len(self.goals)

    def step(self, now_s, me, peers, inbox):
        tol = self.arrive_tol_m if self.arrive_tol_m is not None else me.goal_lock_m
        while not self.done:
            g = self.goals[self.index]
            if float(np.linalg.norm(np.asarray(me.pos, dtype=np.float64)
                                    - np.asarray(g, dtype=np.float64))) >= tol:
                return list(g), []
            self.index += 1
        return None, []


def normalise_team_msg(msg: dict) -> dict:
    """team_link datagrams carry ``from`` / ``t``; the allocator contract is
    ``sender`` / ``stamp``. Fill those in without dropping anything. ``t`` is the
    SENDER's wall-clock send time (seconds since the epoch), not this vehicle's
    monotonic clock. A payload's own ``stamp`` (SIGHTING has one) wins over it."""
    out = dict(msg)
    out.setdefault("sender", msg.get("from"))
    out.setdefault("stamp", msg.get("t"))
    return out


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

    sense_max = agent.vclass.sense_range_m
    scan = np.asarray(scan, dtype=np.float32)
    if not pose.sensor_ok:  # sensor dropout: report all-clear (matches sim blind path)
        n_rays = _LIDAR_RAYS if agent.vclass.sensor is Sensor.LIDAR_360 \
            else _DEPTH_COLS * _DEPTH_ROWS
        scan = np.full(n_rays, sense_max, dtype=np.float32)
        min_clear = sense_max
    else:
        min_clear = float(scan.min()) if scan.size else sense_max
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
        **snapshot_caps(agent.vclass),
    )


def _peer_snapshot(p: Peer) -> AgentSnapshot:
    return AgentSnapshot(
        id=p.id, pos=[float(x) for x in p.pos_enu], vel=[float(x) for x in p.vel_enu],
        goal=None, alive=p.alive, sensor_ok=True, confidence=p.confidence,
        vclass=get_class(p.vclass_name).name,
        min_scan_dist=get_class(p.vclass_name).sense_range_m,
        **snapshot_caps(get_class(p.vclass_name)),
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
                 use_smart: bool = True,
                 allocator: Optional[TeamAllocator] = None,
                 team_link=None,
                 clock: Callable[[], float] = time.monotonic):
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
        # Optional: the goal comes from the allocator each tick, and its messages go out
        # over team_link (a team_link.TeamLink, or anything with its public API). With
        # an allocator but no link the vehicle allocates alone: no peers, no inbox.
        self.allocator = allocator
        self.team_link = team_link
        self.clock = clock
        self.last_action: np.ndarray | None = None
        self.last_obs: Observation | None = None
        self.last_out: list = []          # messages the allocator asked to send last tick
        self._dropped_types: set = set()  # message types the link has no public sender for

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
        if self.team_link is not None:
            # What teammates' TeamLinkPeers will read back as this vehicle's Peer.
            self.team_link.update_self(
                [float(x) for x in pose.pos_enu], math.degrees(float(pose.yaw_rad)),
                [float(x) for x in pose.vel_enu], float(pose.loc_confidence))

        peers = self.peers.peers()
        scan = self.sensor.scan()
        if self.allocator is not None:
            self._allocate(pose, scan)
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

    # ------------------------------------------------------------------ allocation
    def _allocate(self, pose: Pose, scan: np.ndarray) -> None:
        """Ask the allocator for this tick's goal; send what it wants said."""
        link = self.team_link
        inbox = [normalise_team_msg(m) for m in link.drain_inbox()] if link is not None else []
        link_peers = link.peers() if link is not None else {}
        scan = np.asarray(scan, dtype=np.float32)
        min_scan = float(scan.min()) if scan.size else self.agent.vclass.sense_range_m
        me = _agent_snapshot(self.agent, pose, min_scan)
        goal, out = self.allocator.step(self.clock(), me, link_peers, inbox)
        self.set_goal(goal)
        self.last_out = list(out or [])
        for msg in self.last_out:
            self._broadcast(msg)

    def _broadcast(self, msg: dict) -> None:
        """Send one allocator message through team_link's public API.

        ``TeamLink.send(msg)`` carries any message dict unchanged. A link without it
        (older team_link, or a minimal fake) gets the typed senders for TASK_CLAIM /
        TASK_DONE / SIGHTING. Anything that does not go out is logged once per type,
        so a message type that never leaves the vehicle fails loudly, not silently.
        """
        link = self.team_link
        if link is None:
            return
        mtype = msg.get("type")
        sent = True
        if callable(getattr(link, "send", None)):
            sent = link.send(msg) is not False
        elif mtype == "TASK_CLAIM":
            link.send_task_claim(msg["claim_id"])
        elif mtype == "TASK_DONE":
            link.send_task_done(msg["claim_id"])
        elif mtype == "SIGHTING":
            link.send_sighting(msg["target_id"], list(msg["pos_enu"]))
        else:
            sent = False
        if not sent and mtype not in self._dropped_types:
            self._dropped_types.add(mtype)
            logger.warning(f"team_link did not send the allocator's {mtype!r} message "
                           f"(further {mtype} failures are not logged)")


# --------------------------------------------------------------------------- team link peers
class TeamLinkPeers:
    """``PeerProvider`` over ``team_link.TeamLink``: its live peers as ``Peer``s.

    The vehicle class is what the teammate itself announces (``PeerState.vclass_name``),
    else the pre-briefed roster's (``{drone_id: "quadcopter" | "rover" | ...}``), else
    ``default_vclass``. A peer that has
    announced RTL is still reported alive: it is still a body in the air to avoid.
    """

    def __init__(self, link, roster_vclass: Optional[dict] = None,
                 default_vclass: str = "quadcopter"):
        self.link = link
        self.roster_vclass = dict(roster_vclass or {})
        self.default_vclass = default_vclass

    def _vclass(self, pid: str, announced) -> str:
        if announced:
            try:
                get_class(announced)
                return announced
            except KeyError:
                pass   # a class this build does not know: trust the briefing instead
        return self.roster_vclass.get(pid, self.default_vclass)

    def peers(self) -> list[Peer]:
        out = []
        for pid, st in self.link.peers().items():
            out.append(Peer(
                id=pid,
                pos_enu=np.asarray(st.pos_enu, dtype=np.float32).reshape(3),
                vel_enu=np.asarray(st.vel, dtype=np.float32).reshape(3),
                vclass_name=self._vclass(pid, getattr(st, "vclass_name", None)),
                confidence=float(st.confidence), alive=True,
            ))
        return out


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
