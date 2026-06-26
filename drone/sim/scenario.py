"""Scenario DSL + inject engine (Phase 3).

Generalizes the static obstacle-course dicts (training/record_comparison.py COURSES)
into a declarative, event-driven scenario: a team, a mission, timed/event/condition
"injects" (the *"suddenly X happened"* events), and success criteria. A
``ScenarioRunner`` wires up a TeamWorld + CommsFabric + LocalizationFabric, runs the
mission, fires injects, and records an event log for the scorecard (Phase 5).

A scenario (YAML or dict):

    name: comms_blackout_search
    environment: warehouse
    team: [quad x2, rover x2]
    mission: {type: area_search, region: [-10,-10, 10,10], targets: [[8,6],[ -7,4]]}
    injects:
      - {at: 30, do: comms_blackout}
      - {on: target_found, do: gps_spoof, agent: any}
      - {at: 60, do: agent_failure, agent: quad_1}
      - {on: intruder, do: noop}
    success: {targets_found: all, no_collisions: true}

Inject families (all four covered):
  comms/GPS    : comms_blackout, jamming_zone, gps_loss, gps_spoof, agent_isolated
  agent/sensor : agent_failure, sensor_dropout, battery_emergency
  dynamic world: spawn_dynamic, move_target, add_nofly, wind_shift
  coordination : contested_task, tight_deconfliction, rendezvous

Pure stdlib + numpy (+ pyyaml for file loading); deterministic given a seed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import re

import numpy as np

from .team_world import TeamWorld, KinematicWorld, Box, reactive_goto_controller
from .comms import CommsFabric, MsgType
from .localization import LocalizationFabric, LocMode


# =========================================================================== mission
@dataclass
class Target:
    id: str
    pos: np.ndarray
    found: bool = False


class Mission:
    """Lightweight mission: assigns goals and tracks completion + emits events.

    Types:
      goto        — each agent flies to its preset goal.
      area_search — agents sweep a region; a target is 'found' when any team agent
                    comes within find_radius; mission done when all targets found.
      patrol      — agents loop their waypoints (never 'complete'; bounded by time).
    """
    FIND_RADIUS = 2.0

    def __init__(self, spec: dict):
        self.type = spec.get("type", "goto")
        self.region = spec.get("region")            # [xmin,ymin,xmax,ymax]
        self.targets = [Target(f"t{i}", np.asarray(_xy3(t), np.float32))
                        for i, t in enumerate(spec.get("targets", []))]
        self.waypoints = [np.asarray(_xy3(w), np.float32)
                          for w in spec.get("waypoints", [])]
        self._wp_idx: dict[str, int] = {}
        self.auto_assign = True   # TeamRuntime sets False to own task allocation

    def assign_initial_goals(self, world: TeamWorld) -> None:
        team = world.team_agents()
        if self.type == "area_search" and self.targets:
            # round-robin the targets across the team as initial goals
            for i, a in enumerate(team):
                a.goal = self.targets[i % len(self.targets)].pos.copy()
        elif self.type == "patrol" and self.waypoints:
            for i, a in enumerate(team):
                self._wp_idx[a.id] = i % len(self.waypoints)
                a.goal = self.waypoints[self._wp_idx[a.id]].copy()
        # goto: goals already set on the roster

    def update(self, world: TeamWorld) -> list[str]:
        """Advance mission bookkeeping; return event names emitted this tick."""
        events: list[str] = []
        if self.type == "area_search":
            for tgt in self.targets:
                if tgt.found:
                    continue
                for a in world.team_agents():
                    if float(np.linalg.norm(a.pos - tgt.pos)) <= self.FIND_RADIUS:
                        tgt.found = True
                        events.append("target_found")
                        # baseline-without-runtime convenience: hop to nearest unfound.
                        # When a TeamRuntime owns allocation (auto_assign=False), skip.
                        if self.auto_assign:
                            rem = [t for t in self.targets if not t.found]
                            if rem:
                                nearest = min(rem, key=lambda t: np.linalg.norm(a.pos - t.pos))
                                a.goal = nearest.pos.copy()
                        break
        elif self.type == "patrol":
            for a in world.team_agents():
                if a.goal is not None and np.linalg.norm(a.pos - a.goal) < 1.0:
                    i = (self._wp_idx.get(a.id, 0) + 1) % len(self.waypoints)
                    self._wp_idx[a.id] = i
                    a.goal = self.waypoints[i].copy()
        return events

    @property
    def all_targets_found(self) -> bool:
        return all(t.found for t in self.targets) if self.targets else False

    def complete(self, world: TeamWorld) -> bool:
        if self.type == "area_search":
            return self.all_targets_found
        if self.type == "goto":
            return world.all_reached(tol=1.5)
        return False   # patrol runs until time cap


# =========================================================================== scenario
@dataclass
class Scenario:
    name: str
    environment: str = "none"
    team: list[dict] = field(default_factory=list)     # roster specs for TeamWorld.add_roster
    mission: dict = field(default_factory=lambda: {"type": "goto"})
    injects: list[dict] = field(default_factory=list)
    success: dict = field(default_factory=dict)
    obstacles: list[list] = field(default_factory=list)  # [[cx,cy,cz,hx,hy,hz], ...]
    duration_s: float = 120.0
    seed: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        team = _expand_team(d.get("team", []), d.get("starts"))
        return cls(
            name=d["name"], environment=d.get("environment", "none"), team=team,
            mission=d.get("mission", {"type": "goto"}), injects=d.get("injects", []),
            success=d.get("success", {}), obstacles=d.get("obstacles", []),
            duration_s=float(d.get("duration_s", 120.0)), seed=int(d.get("seed", 0)),
        )

    @classmethod
    def from_yaml(cls, path: str) -> "Scenario":
        import yaml
        with open(path) as fh:
            return cls.from_dict(yaml.safe_load(fh))


def _expand_team(team_spec, starts=None) -> list[dict]:
    """Accept ['quad x2','rover x2'] or explicit [{'id','type','pos'}...]."""
    if team_spec and isinstance(team_spec[0], dict):
        return [dict(s) for s in team_spec]
    roster: list[dict] = []
    counts: dict[str, int] = {}
    for entry in team_spec:
        m = re.match(r"\s*(\w+)\s*[x*]\s*(\d+)\s*", str(entry))
        if m:
            typ, n = m.group(1), int(m.group(2))
        else:
            typ, n = str(entry).strip(), 1
        for _ in range(n):
            idx = counts.get(typ, 0)
            counts[typ] = idx + 1
            roster.append({"id": f"{typ}_{idx}", "type": typ})
    # spread start positions on a line if none given
    starts = starts or {}
    for i, spec in enumerate(roster):
        spec.setdefault("pos", starts.get(spec["id"],
                                          [-12.0, (i - len(roster) / 2) * 2.0,
                                           2.0 if spec["type"].startswith("q") else 0.0]))
        spec.setdefault("goal", [12.0, spec["pos"][1], spec["pos"][2]])
    return roster


def _xy3(p):
    p = list(p)
    return p if len(p) == 3 else [p[0], p[1], 0.0]


# =========================================================================== injects
class InjectEngine:
    """Fires injects on time / event / condition triggers (each once)."""

    def __init__(self, runner: "ScenarioRunner", injects: list[dict]):
        self.runner = runner
        self.injects = [dict(i) for i in injects]
        self._fired = [False] * len(self.injects)
        self._predicates = _PREDICATES

    def tick(self, world: TeamWorld, events: list[str]) -> list[str]:
        fired_names: list[str] = []
        for i, inj in enumerate(self.injects):
            if self._fired[i]:
                continue
            if self._triggered(inj, world, events):
                self._fire(inj, world)
                self._fired[i] = True
                fired_names.append(inj.get("do", "noop"))
        return fired_names

    def _triggered(self, inj, world, events) -> bool:
        if "at" in inj:
            return world.t >= float(inj["at"])
        if "on" in inj:
            return inj["on"] in events
        if "when" in inj:
            pred = self._predicates.get(inj["when"])
            return bool(pred and pred(self.runner, world))
        return False

    def _fire(self, inj, world) -> None:
        handler = _HANDLERS.get(inj.get("do", "noop"))
        if handler is None:
            raise KeyError(f"unknown inject action {inj.get('do')!r}")
        handler(self.runner, world, inj)


# -- predicate vocabulary for `when:` triggers --------------------------------
def _any_low_confidence(runner, world) -> bool:
    return any(runner.loc.confidence(a) < 0.5 for a in world.team_agents())


def _any_isolated(runner, world) -> bool:
    return any(len(runner.comms.reachable(world, a.id)) == 0
               for a in world.team_agents())


_PREDICATES = {
    "any_low_confidence": _any_low_confidence,
    "any_isolated": _any_isolated,
}


# -- inject handlers (the four families) --------------------------------------
def _targets(inj, world):
    """Resolve the 'agent' field to a list of agents (id | 'all' | 'any')."""
    who = inj.get("agent", "all")
    team = world.team_agents()
    if who in ("all", None):
        return team
    if who == "any":
        return team[:1]
    return [world.agents[who]] if who in world.agents else []


def _h_comms_blackout(runner, world, inj):
    region = inj.get("region")
    if region:
        cx, cy, r = _region_center_radius(region)
        runner.comms.add_jamming_zone([cx, cy, 0.0], r)
    else:
        runner.comms.set_blackout(True)


def _h_jamming_zone(runner, world, inj):
    c = inj.get("center", [0, 0, 0])
    runner.comms.add_jamming_zone(_xy3(c), float(inj.get("radius", 10.0)))


def _h_gps_loss(runner, world, inj):
    for a in _targets(inj, world):
        runner.loc.gps_loss(a.id)


def _h_gps_spoof(runner, world, inj):
    for a in _targets(inj, world):
        runner.loc.gps_spoof(a.id, vel=inj.get("vel"))


def _h_agent_isolated(runner, world, inj):
    for a in _targets(inj, world):
        runner.comms.add_jamming_zone(a.pos.tolist(), a.vclass.comms.range_m * 1.2)


def _h_agent_failure(runner, world, inj):
    for a in _targets(inj, world):
        a.alive = False
        runner.emit("agent_lost")


def _h_sensor_dropout(runner, world, inj):
    for a in _targets(inj, world):
        a.sensor_ok = False


def _h_battery_emergency(runner, world, inj):
    home = np.asarray(_xy3(inj.get("home", [-12, 0, 0])), np.float32)
    for a in _targets(inj, world):
        a.goal = home.copy() if a.vclass.planar else np.array(
            [home[0], home[1], a.pos[2]], np.float32)
        a.rtl = True


def _h_spawn_dynamic(runner, world, inj):
    path = [np.asarray(_xy3(p), np.float32) for p in inj.get("path", [[0, 0, 2]])]
    typ = inj.get("vclass", "quad")
    speed = float(inj.get("speed", 2.0))
    iid = inj.get("id", f"intruder_{runner.n_intruders}")
    runner.n_intruders += 1
    world.add_agent(iid, typ, path[0], team=False,
                    controller=_path_controller(path, speed))
    runner.emit("intruder")


def _h_move_target(runner, world, inj):
    tid = inj.get("target", "t0")
    to = np.asarray(_xy3(inj.get("to", [0, 0, 0])), np.float32)
    for t in runner.mission.targets:
        if t.id == tid:
            t.pos = to
            # redirect any agent currently heading there
            for a in world.team_agents():
                if a.goal is not None and np.linalg.norm(a.goal - to) > 1.0:
                    pass


def _h_add_nofly(runner, world, inj):
    c = _xy3(inj.get("center", [0, 0, 1.5]))
    h = _xy3(inj.get("half", [1.0, 1.0, 3.0]))
    world.backend.add_obstacle(Box(np.asarray(c, np.float32), np.asarray(h, np.float32)))


def _h_wind_shift(runner, world, inj):
    v = _xy3(inj.get("vector", [1.0, 0.0, 0.0]))
    world.backend.wind = np.asarray(v, np.float32)


def _h_contested_task(runner, world, inj):
    """Force two agents to claim the same goal (deconfliction stress)."""
    pt = np.asarray(_xy3(inj.get("at_point", [0, 0, 2])), np.float32)
    ids = inj.get("agents", [a.id for a in world.team_agents()[:2]])
    for aid in ids:
        if aid in world.agents:
            world.agents[aid].goal = pt.copy()


def _h_tight_deconfliction(runner, world, inj):
    """Funnel the whole team through one chokepoint goal at once.

    Planar agents (rovers) use the goal as-is.  3-D agents (quads) are sent to
    the same XY but at a higher altitude so they overfly obstacles instead of
    competing for the ground-level gap.
    """
    pt = np.asarray(_xy3(inj.get("at_point", [0, 0, 2])), np.float32)
    overfly_z = float(inj.get("overfly_z", 4.5))   # m — clear a 3m wall
    for a in world.team_agents():
        if a.vclass.planar:
            a.goal = pt.copy()
        else:
            a.goal = np.array([pt[0], pt[1], overfly_z], np.float32)


def _h_rendezvous(runner, world, inj):
    pt = np.asarray(_xy3(inj.get("point", [0, 0, 2])), np.float32)
    for a in world.team_agents():
        a.goal = pt.copy()


def _h_noop(runner, world, inj):
    pass


_HANDLERS = {
    "comms_blackout": _h_comms_blackout, "jamming_zone": _h_jamming_zone,
    "gps_loss": _h_gps_loss, "gps_spoof": _h_gps_spoof,
    "agent_isolated": _h_agent_isolated,
    "agent_failure": _h_agent_failure, "sensor_dropout": _h_sensor_dropout,
    "battery_emergency": _h_battery_emergency,
    "spawn_dynamic": _h_spawn_dynamic, "move_target": _h_move_target,
    "add_nofly": _h_add_nofly, "wind_shift": _h_wind_shift,
    "contested_task": _h_contested_task, "tight_deconfliction": _h_tight_deconfliction,
    "rendezvous": _h_rendezvous, "noop": _h_noop,
}


def _region_center_radius(region):
    xmin, ymin, xmax, ymax = region
    return (xmin + xmax) / 2, (ymin + ymax) / 2, max(xmax - xmin, ymax - ymin) / 2


def _path_controller(path, speed):
    """Per-agent controller that walks an intruder along waypoints."""
    state = {"i": 0}

    def ctl(agent, obs):
        wp = path[min(state["i"], len(path) - 1)]
        d = wp - agent.pos
        if float(np.linalg.norm(d)) < 0.5 and state["i"] < len(path) - 1:
            state["i"] += 1
            wp = path[state["i"]]
            d = wp - agent.pos
        n = max(1e-3, float(np.linalg.norm(d)))
        if agent.vclass.planar:
            desired = math.atan2(d[1], d[0])
            err = (desired - agent.yaw + math.pi) % (2 * math.pi) - math.pi
            return np.array([speed, float(np.clip(err * 2, -2, 2))], np.float32)
        # holonomic intruder: body-frame velocity toward waypoint
        c, s = math.cos(-agent.yaw), math.sin(-agent.yaw)
        bx, by = c * d[0] - s * d[1], s * d[0] + c * d[1]
        return np.array([speed * bx / n, speed * by / n, speed * d[2] / n, 0.0], np.float32)
    return ctl


# =========================================================================== runner
class ScenarioRunner:
    """Builds the world+fabrics from a Scenario, runs the loop, records events."""

    def __init__(self, scenario: Scenario, controller=None, dt: float = 0.1):
        self.scenario = scenario
        backend = KinematicWorld(static_obstacles=[
            Box(np.asarray(o[:3], np.float32), np.asarray(o[3:], np.float32))
            for o in scenario.obstacles
        ])
        self.world = TeamWorld(backend, dt=dt)
        self.world.add_roster(scenario.team)
        self.comms = CommsFabric(seed=scenario.seed)
        self.loc = LocalizationFabric(seed=scenario.seed)
        self.world.comms = self.comms
        self.world.localization = self.loc
        self.mission = Mission(scenario.mission)
        self.mission.assign_initial_goals(self.world)
        self.injects = InjectEngine(self, scenario.injects)
        self.controller = controller or reactive_goto_controller()
        self.n_intruders = 0
        self.event_log: list[tuple[float, str]] = []
        self._pending_events: list[str] = []

    def emit(self, name: str) -> None:
        self._pending_events.append(name)
        self.event_log.append((round(self.world.t, 2), name))

    def step(self) -> dict:
        events = self.mission.update(self.world)
        for e in events:
            self.event_log.append((round(self.world.t, 2), e))
        events = events + self._pending_events
        self._pending_events = []
        fired = self.injects.tick(self.world, events)
        for f in fired:
            self.event_log.append((round(self.world.t, 2), f"inject:{f}"))
        info = self.world.step(self.controller)
        info["events"] = events
        info["injects_fired"] = fired
        return info

    def run(self, max_s: float | None = None) -> dict:
        max_s = max_s or self.scenario.duration_s
        total_coll = 0
        ticks = int(max_s / self.world.dt)
        for _ in range(ticks):
            info = self.step()
            total_coll += info["collisions"]
            if self.mission.complete(self.world):
                break
        return {
            "scenario": self.scenario.name,
            "t_end": round(self.world.t, 1),
            "mission_complete": self.mission.complete(self.world),
            "targets_found": sum(t.found for t in self.mission.targets),
            "targets_total": len(self.mission.targets),
            "collisions": total_coll,
            "comms": self.comms.stats.as_dict(),
            "events": list(self.event_log),
        }
