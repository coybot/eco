"""Comms fabric — abstract inter-agent link model (Phase 1).

Mediates *all* inter-agent messages so "comm-denied" becomes a first-class,
scriptable condition. Today everything routes through AWS IoT/MQTT
(fleet_mqtt.py); here we introduce a transport abstraction with a peer-to-peer
mesh that degrades realistically, plus a cloud transport that can simply be
switched off.

Link model (per directed link, evaluated at delivery time):
  - **range:** sender's VehicleClass comms range (vehicle_class.CommsProfile).
  - **drop probability:** rises with distance toward the range limit.
  - **latency:** fixed base + per-distance term; messages deliver later in sim time.
  - **bandwidth cap:** per-sender messages/sec; excess is dropped (queue pressure).
  - **jamming zones:** spherical regions; a link is nulled if either endpoint is
    inside, or (for LOS-required classes) if the segment passes through one.
  - **LOS occlusion:** for los_required classes, static world obstacles between
    sender and recipient block the link.

Designed so a higher-fidelity ``RFPropagationFabric`` can subclass ``CommsFabric``
and override ``_link_quality`` without touching callers.

Pure stdlib + numpy; deterministic given a seed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import random

import numpy as np


# --------------------------------------------------------------------------- messages
class MsgType(str, Enum):
    """Minimal team protocol — kept tiny so bandwidth caps bite realistically."""
    POSITION = "position"          # periodic self-position broadcast
    TASK_CLAIM = "task_claim"      # "I am taking task T"
    TASK_RELEASE = "task_release"  # "I am dropping task T"
    SIGHTING = "sighting"          # "target/intruder seen at X"
    HELP = "help"                  # "I need assistance / I am stuck"
    HEARTBEAT = "heartbeat"        # liveness ping


@dataclass
class Message:
    mtype: MsgType
    sender: str
    payload: dict
    t_sent: float
    dest: str | None = None        # None = broadcast to all in range
    size: int = 1                  # abstract bandwidth units
    _deliver_at: float = 0.0       # set when queued (t_sent + latency)


@dataclass
class JammingZone:
    center: np.ndarray             # (3,)
    radius: float

    def contains(self, p: np.ndarray) -> bool:
        return float(np.linalg.norm(np.asarray(p) - self.center)) <= self.radius

    def blocks_segment(self, a: np.ndarray, b: np.ndarray) -> bool:
        return _segment_hits_sphere(np.asarray(a), np.asarray(b), self.center, self.radius)


@dataclass
class CommsStats:
    sent: int = 0          # send() calls (one per broadcast/unicast)
    attempted: int = 0     # per-recipient delivery attempts (broadcast = many)
    delivered: int = 0
    dropped_range: int = 0
    dropped_prob: int = 0
    dropped_jam: int = 0
    dropped_los: int = 0
    dropped_bandwidth: int = 0
    dropped_cloud_down: int = 0

    @property
    def dropped(self) -> int:
        return (self.dropped_range + self.dropped_prob + self.dropped_jam
                + self.dropped_los + self.dropped_bandwidth + self.dropped_cloud_down)

    @property
    def delivery_rate(self) -> float:
        """Fraction of per-recipient delivery attempts that succeeded (<=1)."""
        return self.delivered / self.attempted if self.attempted else 1.0

    def as_dict(self) -> dict:
        return {
            "sent": self.sent, "attempted": self.attempted,
            "delivered": self.delivered, "dropped": self.dropped,
            "delivery_rate": round(self.delivery_rate, 3),
            "dropped_range": self.dropped_range, "dropped_prob": self.dropped_prob,
            "dropped_jam": self.dropped_jam, "dropped_los": self.dropped_los,
            "dropped_bandwidth": self.dropped_bandwidth,
            "dropped_cloud_down": self.dropped_cloud_down,
        }


# --------------------------------------------------------------------------- transports
class Transport(str, Enum):
    MESH = "mesh"     # peer-to-peer through the link model
    CLOUD = "cloud"   # via AWS IoT/MQTT (fleet_mqtt) — perfect when up, null when down


# --------------------------------------------------------------------------- fabric
class CommsFabric:
    """Routes messages between agents under the abstract link model.

    Wire into TeamWorld: ``world.comms = fabric``. ``TeamWorld.step`` calls
    ``fabric.deliver(world)`` each tick to move due messages into agent inboxes.
    Agents enqueue with ``fabric.send(...)``.
    """

    BASE_LATENCY_S = 0.05
    LATENCY_PER_M_S = 0.001

    def __init__(self, seed: int = 0, los_obstacles: bool = True,
                 default_transport: Transport = Transport.MESH):
        self._q: list[Message] = []
        self._rng = random.Random(seed)
        self.jamming: list[JammingZone] = []
        self.cloud_up = True                 # comm-denied scenarios flip this off
        self.mesh_degraded = False           # global mesh kill switch (full blackout)
        self.los_obstacles = los_obstacles   # honor static obstacle occlusion for LOS classes
        self.default_transport = default_transport
        self.stats = CommsStats()
        self._bw_window: dict[str, list[float]] = {}   # sender -> recent send times

    # -- scenario inject hooks (Phase 3 calls these) ----------------------------
    def add_jamming_zone(self, center, radius: float) -> JammingZone:
        z = JammingZone(np.asarray(center, dtype=np.float32), float(radius))
        self.jamming.append(z)
        return z

    def clear_jamming(self) -> None:
        self.jamming.clear()

    def set_blackout(self, on: bool) -> None:
        """Full comms blackout: cloud down + mesh degraded (local-info-only operation)."""
        self.cloud_up = not on
        self.mesh_degraded = on

    # -- send / deliver ---------------------------------------------------------
    def send(self, world, sender: str, mtype: MsgType, payload: dict,
             dest: str | None = None, size: int = 1,
             transport: Transport | None = None) -> None:
        self.stats.sent += 1
        transport = transport or self.default_transport
        # bandwidth: per-sender messages within the last second
        a = world.agents.get(sender)
        if a is not None:
            cap = a.vclass.comms.bandwidth_msgs_per_s
            win = self._bw_window.setdefault(sender, [])
            win.append(world.t)
            self._bw_window[sender] = [t for t in win if t > world.t - 1.0]
            if len(self._bw_window[sender]) > cap:
                self.stats.dropped_bandwidth += 1
                return
        latency = self.BASE_LATENCY_S
        msg = Message(mtype=mtype, sender=sender, payload=dict(payload),
                      t_sent=world.t, dest=dest, size=size)
        msg._deliver_at = world.t + latency
        msg.payload["_transport"] = transport.value
        self._q.append(msg)

    def deliver(self, world) -> None:
        """Move all due messages into recipient inboxes, applying the link model."""
        if not self._q:
            return
        due = [m for m in self._q if m._deliver_at <= world.t + 1e-9]
        self._q = [m for m in self._q if m._deliver_at > world.t + 1e-9]
        for m in due:
            transport = Transport(m.payload.get("_transport", self.default_transport.value))
            sender = world.agents.get(m.sender)
            if sender is None or not sender.alive:
                continue
            recipients = ([world.agents[m.dest]] if m.dest and m.dest in world.agents
                          else [a for a in world.agents.values() if a.id != m.sender])
            for r in recipients:
                if not r.alive:
                    continue
                self.stats.attempted += 1
                if self._delivers(world, sender, r, transport):
                    r.inbox.append(m)
                    self.stats.delivered += 1

    # -- link evaluation (override _link_quality for an RF model) ----------------
    def _delivers(self, world, sender, recip, transport: Transport) -> bool:
        if transport is Transport.CLOUD:
            if not self.cloud_up:
                self.stats.dropped_cloud_down += 1
                return False
            return True   # cloud is a perfect relay when up
        # mesh path
        if self.mesh_degraded:
            self.stats.dropped_jam += 1   # global blackout counts as jam-class loss
            return False
        sp, rp = sender.pos, recip.pos
        d = float(np.linalg.norm(rp - sp))
        rng = sender.vclass.comms.range_m
        if d > rng:
            self.stats.dropped_range += 1
            return False
        # jamming zones
        for z in self.jamming:
            if z.contains(sp) or z.contains(rp) or z.blocks_segment(sp, rp):
                self.stats.dropped_jam += 1
                return False
        # LOS occlusion for classes that require it
        if sender.vclass.comms.los_required and self.los_obstacles:
            for b in world.backend.obstacles():
                if _segment_hits_aabb(sp, rp, b.center, b.half):
                    self.stats.dropped_los += 1
                    return False
        # distance-based drop probability (0 at zero range, ~0.4 at the limit)
        p_drop = 0.4 * (d / rng) ** 2
        if self._rng.random() < p_drop:
            self.stats.dropped_prob += 1
            return False
        return True

    def reachable(self, world, sender_id: str) -> set[str]:
        """IDs currently reachable from sender over mesh (ignores drop probability)."""
        out: set[str] = set()
        s = world.agents.get(sender_id)
        if s is None or self.mesh_degraded:
            return out
        for r in world.agents.values():
            if r.id == sender_id or not r.alive:
                continue
            d = float(np.linalg.norm(r.pos - s.pos))
            if d > s.vclass.comms.range_m:
                continue
            if any(z.contains(s.pos) or z.contains(r.pos) or z.blocks_segment(s.pos, r.pos)
                   for z in self.jamming):
                continue
            if s.vclass.comms.los_required and self.los_obstacles and \
               any(_segment_hits_aabb(s.pos, r.pos, b.center, b.half)
                   for b in world.backend.obstacles()):
                continue
            out.add(r.id)
        return out


# --------------------------------------------------------------------------- geometry helpers
def _segment_hits_sphere(a: np.ndarray, b: np.ndarray, c: np.ndarray, r: float) -> bool:
    ab = b - a
    t = 0.0 if np.dot(ab, ab) < 1e-9 else float(np.clip(np.dot(c - a, ab) / np.dot(ab, ab), 0, 1))
    closest = a + t * ab
    return float(np.linalg.norm(closest - c)) <= r


def _segment_hits_aabb(a: np.ndarray, b: np.ndarray, center: np.ndarray,
                       half: np.ndarray) -> bool:
    """Slab test: does segment a->b intersect the AABB [center±half]?"""
    a = np.asarray(a, dtype=np.float64)
    d = np.asarray(b, dtype=np.float64) - a
    lo = np.asarray(center) - np.asarray(half)
    hi = np.asarray(center) + np.asarray(half)
    tmin, tmax = 0.0, 1.0
    for i in range(3):
        if abs(d[i]) < 1e-9:
            if a[i] < lo[i] or a[i] > hi[i]:
                return False
        else:
            t1, t2 = (lo[i] - a[i]) / d[i], (hi[i] - a[i]) / d[i]
            if t1 > t2:
                t1, t2 = t2, t1
            tmin, tmax = max(tmin, t1), min(tmax, t2)
            if tmin > tmax:
                return False
    return True
