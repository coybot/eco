"""Team runtime — decentralized coordination baseline (Phase 4).

A per-agent decision layer that sits *above* the navigation policy/controller and
*below* the mission. It is deliberately rule-based (not learned): the goal of
Milestone 1 is a sensible baseline whose autonomy we can measure, then beat with a
learned policy later.

What it does each tick, per agent, using only what that agent can know:
  - **Shared blackboard over comms.** Ingest inbox (teammate positions, task claims,
    target sightings); broadcast its own. When comms die the blackboard simply goes
    stale and the agent falls back to local-only behavior — no special-casing.
  - **Task allocation (auction).** Open tasks = mission targets not yet found. Each
    agent bids its distance; lowest bid (id tie-break) wins. When isolated (no one
    reachable) it uses a deterministic id-rank fallback so two cut-off agents don't
    both abandon the same task.
  - **Deconfliction.** Yields (slows) to a higher-priority neighbor that is close and
    ahead; the underlying potential-field controller does the geometric avoidance.
  - **Contingency reflexes.** Low localization confidence → slow down; very low → RTL.
    Sensor dropout → slow. Teammate loss → its tasks return to the open pool and get
    re-auctioned automatically.

Wire it up: ``rt = TeamRuntime(runner); runner.controller = rt.controller``.
The runtime disables the Mission's own goal auto-assignment so it owns allocation.

Pure stdlib + numpy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from .team_world import reactive_goto_controller
from .comms import MsgType


# thresholds
CONF_SLOW = 0.6        # below this, scale speed by confidence
CONF_RTL = 0.30        # below this, return to launch (lost)
BROADCAST_EVERY = 5    # ticks between self-broadcasts (bandwidth-friendly)
CLAIM_STALE_S = 3.0    # forget claims older than this


@dataclass
class _Belief:
    """One agent's local world model, updated from comms (degrades when comms die)."""
    teammate_pos: dict = field(default_factory=dict)     # id -> (np.ndarray, t)
    claims: dict = field(default_factory=dict)           # task_id -> (agent_id, bid, t)
    sightings: dict = field(default_factory=dict)        # target_id -> np.ndarray
    my_task: str | None = None
    isolated_ticks: int = 0
    yield_ticks: int = 0          # consecutive ticks spent yielding (yield-deadlock guard)
    convoy_hold_ticks: int = 0    # ticks in convoy-queue mode (chokepoint serialization)
    sensor_blind_ticks: int = 0   # consecutive ticks with sensor_ok=False


class TeamRuntime:
    def __init__(self, runner, nav_controller=None):
        self.runner = runner
        self.world = runner.world
        self.comms = runner.comms
        self.loc = runner.loc
        self.mission = runner.mission
        self.nav = nav_controller or reactive_goto_controller()
        self.belief: dict[str, _Belief] = {a.id: _Belief() for a in self.world.agents.values()}
        self.home = {a.id: a.pos.copy() for a in self.world.agents.values()}
        # static id rank for deterministic isolation fallback
        self._rank = {aid: i for i, aid in enumerate(sorted(self.belief))}
        # take over goal assignment from the mission
        self.mission.auto_assign = False
        # metrics surfaced to the scorecard (Phase 5)
        self.metrics = {"rtl_events": 0, "isolated_agent_ticks": 0, "reassignments": 0}

    # -- task model -------------------------------------------------------------
    def _open_tasks(self) -> list:
        """Mission targets not yet found, as (id, pos)."""
        return [(t.id, t.pos) for t in getattr(self.mission, "targets", []) if not t.found]

    # -- per-agent controller ---------------------------------------------------
    def controller(self, agent, obs):
        b = self.belief[agent.id]
        self._ingest(agent, b)
        self._broadcast(agent, b)
        self._allocate(agent, b)
        self._set_goal(agent, b)
        action = self.nav(agent, obs)
        return self._modulate(agent, obs, action)

    # -- comms ------------------------------------------------------------------
    def _ingest(self, agent, b: _Belief):
        for m in agent.inbox:
            if m.mtype is MsgType.POSITION:
                b.teammate_pos[m.sender] = (np.asarray(m.payload["pos"], np.float32), m.t_sent)
            elif m.mtype is MsgType.TASK_CLAIM:
                tid = m.payload["task"]
                prev = b.claims.get(tid)
                bid = m.payload["bid"]
                if prev is None or bid < prev[1] or (bid == prev[1] and m.sender < prev[0]):
                    b.claims[tid] = (m.sender, bid, m.t_sent)
            elif m.mtype is MsgType.SIGHTING:
                b.sightings[m.payload["target"]] = np.asarray(m.payload["pos"], np.float32)
        agent.inbox.clear()
        # expire stale claims
        now = self.world.t
        b.claims = {k: v for k, v in b.claims.items() if now - v[2] <= CLAIM_STALE_S}

    def _broadcast(self, agent, b: _Belief):
        if self.world.tick % BROADCAST_EVERY != self._rank[agent.id] % BROADCAST_EVERY:
            return
        self.comms.send(self.world, agent.id, MsgType.POSITION, {"pos": agent.pos.tolist()})
        if b.my_task is not None:
            bid = self._bid(agent, b.my_task)
            self.comms.send(self.world, agent.id, MsgType.TASK_CLAIM,
                            {"task": b.my_task, "bid": bid})

    # -- allocation -------------------------------------------------------------
    def _bid(self, agent, task_id) -> float:
        for tid, pos in self._open_tasks():
            if tid == task_id:
                return float(np.linalg.norm(agent.pos - pos))
        return math.inf

    def _allocate(self, agent, b: _Belief):
        open_tasks = self._open_tasks()
        if not open_tasks:
            b.my_task = None
            return
        reachable = self.comms.reachable(self.world, agent.id)
        isolated = len(reachable) == 0
        if isolated:
            b.isolated_ticks += 1
            self.metrics["isolated_agent_ticks"] += 1
            # deterministic id-rank fallback: pick a task by my static rank
            ids = sorted(t[0] for t in open_tasks)
            chosen = ids[self._rank[agent.id] % len(ids)]
        else:
            # greedy: take the open task where I'm the best bidder not already
            # better-claimed by someone else
            ranked = sorted(open_tasks, key=lambda tp: np.linalg.norm(agent.pos - tp[1]))
            chosen = None
            for tid, pos in ranked:
                my_bid = float(np.linalg.norm(agent.pos - pos))
                claim = b.claims.get(tid)
                if claim is None or claim[0] == agent.id or my_bid < claim[1] or (
                        my_bid == claim[1] and agent.id <= claim[0]):
                    chosen = tid
                    break
            if chosen is None:
                chosen = ranked[0][0]
        if chosen != b.my_task and b.my_task is not None:
            self.metrics["reassignments"] += 1
        b.my_task = chosen

    def _set_goal(self, agent, b: _Belief):
        # contingency: RTL overrides task
        if getattr(agent, "rtl", False) or self.loc.confidence(agent) < CONF_RTL:
            if not getattr(agent, "_rtl_counted", False):
                self.metrics["rtl_events"] += 1
                agent._rtl_counted = True
            home = self.home[agent.id]
            agent.goal = home.copy() if agent.vclass.planar else np.array(
                [home[0], home[1], agent.pos[2]], np.float32)
            return
        if b.my_task is not None:
            for tid, pos in self._open_tasks():
                if tid == b.my_task:
                    agent.goal = pos.copy()
                    return
        # no tasks (e.g. goto/patrol missions): keep whatever goal mission set

    # Convoy queuing: planar agents serialise through narrow gaps.
    # Lower-priority agent waits until the higher-priority one is _CONVOY_CLEAR m ahead,
    # measured in forward body-frame (x > 0). Timeout prevents infinite hold.
    _CONVOY_CLOSE = 1.5    # m — within this lateral+forward distance triggers convoy
    _CONVOY_CLEAR = 3.0    # m forward before follower is released
    _CONVOY_TIMEOUT = 60   # 6 s timeout — force through if leader is stuck too

    def _modulate(self, agent, obs, action):
        b = self.belief[agent.id]
        scale = 1.0
        conf = self.loc.confidence(agent)
        if conf < CONF_SLOW:
            scale *= max(0.3, conf)

        # ---- sensor blind: hold then creep ----
        sensor_ok = getattr(agent, "sensor_ok", True)
        if not sensor_ok:
            b.sensor_blind_ticks += 1
            if b.sensor_blind_ticks < int(3.0 / max(self.world.dt, 1e-3)):
                return np.zeros_like(np.asarray(action, np.float32))
            scale *= 0.25
        else:
            b.sensor_blind_ticks = 0

        # ---- convoy queuing (planar agents only) ----
        # Only activates when a higher-priority planar neighbour is directly ahead
        # and very close — indicating both are approaching the same gap.
        if agent.vclass.planar and b.convoy_hold_ticks < self._CONVOY_TIMEOUT:
            holding = False
            for nid, rel_body, _vel, _ovc in obs.neighbors:
                # Must be ahead (positive body-x) and laterally aligned (same gap)
                fwd = float(rel_body[0])
                lat = abs(float(rel_body[1]))
                dist = float(np.linalg.norm(rel_body[:2]))
                if (fwd > 0 and lat < 0.8 and dist < self._CONVOY_CLOSE
                        and self._rank.get(nid, 1e9) < self._rank[agent.id]):
                    # hold until leader is _CONVOY_CLEAR m ahead in forward direction
                    if fwd < self._CONVOY_CLEAR:
                        scale *= 0.0
                        b.convoy_hold_ticks += 1
                        holding = True
                    break
            if not holding:
                b.convoy_hold_ticks = 0
        elif b.convoy_hold_ticks >= self._CONVOY_TIMEOUT:
            b.convoy_hold_ticks = 0

        # ---- jammed-isolated: slow down to avoid blind collisions ----
        reachable = self.comms.reachable(self.world, agent.id)
        if len(reachable) == 0:
            scale *= 0.5   # half speed when comms cut — reduce blind nav collisions

        # ---- yield with timeout (non-convoy: wide-space deconfliction) ----
        # Only applies when NOT already in convoy hold, preventing double-application.
        if scale > 0 and b.yield_ticks < 15:
            for nid, rel_body, _vel, _ovc in obs.neighbors:
                ahead = float(rel_body[0]) > 0 and abs(float(rel_body[1])) < 1.5
                close = float(np.linalg.norm(rel_body[:2])) < 2.5
                if ahead and close and self._rank.get(nid, 1e9) < self._rank[agent.id]:
                    scale *= 0.4
                    b.yield_ticks += 1
                    break
            else:
                b.yield_ticks = 0
        elif b.yield_ticks >= 15:
            b.yield_ticks = 0

        # ---- apply scale ----
        action = np.asarray(action, np.float32).copy()
        if agent.vclass.planar:
            action[0] *= scale
        else:
            action[:3] *= scale
        return action
