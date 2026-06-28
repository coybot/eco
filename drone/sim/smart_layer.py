"""Smart layer — high-level fleet advisor sitting above TeamRuntime.

Runs at ~1Hz (every ADVISE_EVERY ticks) and outputs Directives that TeamRuntime
applies as priority overrides in the next cycle. Never blocks the sim tick loop —
LLMSmart runs inference in a background thread and serves cached directives.

Three implementations:
  NullSmart        — no-op (baseline/debug)
  RuleBasedSmart   — deterministic rules, zero latency
  LLMSmart         — Qwen3-14B on hoopoe GPU via SSH, async background thread
"""
from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .team_world import Agent

ADVISE_EVERY = 20       # ticks between advise() calls (~2s at dt=0.1)
LLM_TIMEOUT_S = 3.0    # max wait for hoopoe SSH response before using last directives


# --------------------------------------------------------------------------- data model
@dataclass
class AgentSnapshot:
    id: str
    pos: list[float]
    vel: list[float]
    goal: list[float] | None
    alive: bool
    sensor_ok: bool
    confidence: float
    vclass: str
    min_scan_dist: float = 10.0   # nearest obstacle/intruder in sensor scan (m)


@dataclass
class TargetSnapshot:
    pos: list[float]
    found: bool


@dataclass
class WorldState:
    t: float
    agents: list[AgentSnapshot]
    targets: list[TargetSnapshot]
    interventions: int
    comms_delivery_rate: float
    active_injects: list[str]

    def to_prompt(self) -> str:
        """Compact text description for LLM consumption."""
        lines = [f"t={self.t:.1f}s  interventions={self.interventions}"
                 f"  comms_delivery={self.comms_delivery_rate:.2f}"
                 f"  injects=[{', '.join(self.active_injects)}]"]
        for a in self.agents:
            if not a.alive:
                lines.append(f"  {a.id} DEAD")
                continue
            g = f"→{[round(x,1) for x in a.goal]}" if a.goal else "→none"
            lines.append(
                f"  {a.id}({a.vclass}) pos={[round(x,1) for x in a.pos]}"
                f" {g}  sensor={'ok' if a.sensor_ok else 'BLIND'}"
                f"  conf={a.confidence:.2f}"
            )
        for i, tgt in enumerate(self.targets):
            lines.append(f"  target_{i} {'FOUND' if tgt.found else 'open'}"
                         f" @{[round(x,1) for x in tgt.pos]}")
        return "\n".join(lines)


@dataclass
class Directive:
    agent_id: str | None    # None = team-wide
    kind: str               # "speed_scale", "goal_override", "rtl", "yield_to", "climb"
    value: Any

    def apply(self, agent: "Agent", base_action: "np.ndarray") -> "np.ndarray":
        """Apply this directive to a computed action (in-place, returns modified copy)."""
        import numpy as np
        a = base_action.copy()
        if self.kind == "speed_scale":
            a[:3 if len(a) == 4 else 1] *= float(self.value)
        elif self.kind == "rtl" and self.value:
            a *= 0.0
        elif self.kind == "climb" and len(a) == 4:
            a[2] = max(float(a[2]), float(self.value))
        elif self.kind == "goal_override":
            import math
            goal = self.value
            dx = float(goal[0]) - float(agent.pos[0])
            dy = float(goal[1]) - float(agent.pos[1])
            dz = float(goal[2]) - float(agent.pos[2]) if len(goal) > 2 else 0.0
            dist3d = math.sqrt(dx*dx + dy*dy + dz*dz) + 1e-6
            yaw = float(agent.yaw)
            spd = min(float(agent.vclass.max_speed_mps), dist3d)
            if len(a) >= 4:
                # Holonomic 3D (quad): set body-frame vx, vy, vz
                c, s = math.cos(yaw), math.sin(yaw)
                bx =  c*dx + s*dy   # body forward
                by = -s*dx + c*dy   # body left
                mag_xy = math.sqrt(bx*bx + by*by) + 1e-6
                mag_3d = math.sqrt(bx*bx + by*by + dz*dz) + 1e-6
                scale = spd / mag_3d
                a[0] = bx * scale
                a[1] = by * scale
                a[2] = dz * scale
                a[3] = 0.0
            else:
                # Unicycle (rover): override both speed and yaw rate to drive
                # directly to goal, ignoring intruder/teammate repulsion on yaw.
                dist_xy = math.sqrt(dx*dx + dy*dy) + 1e-6
                desired_yaw = math.atan2(dy, dx)
                yaw_err = (desired_yaw - yaw + math.pi) % (2*math.pi) - math.pi
                max_yr = float(getattr(agent.vclass, 'max_yaw_rate_rads', 2.0))
                a[1] = max_yr * yaw_err / (abs(yaw_err) + 0.3)
                a[0] = spd * max(0.0, math.cos(yaw_err))
        return a


# --------------------------------------------------------------------------- implementations
class NullSmart:
    """No-op baseline."""
    tick_count = 0

    def tick(self, world_state: WorldState) -> list[Directive]:
        return []

    def directives_for(self, agent_id: str) -> list[Directive]:
        return []


class RuleBasedSmart:
    """Deterministic rule-based smart layer. Zero latency, always available.

    Runs every tick (not every ADVISE_EVERY) for time-critical safety rules,
    plus a slower strategic pass every ADVISE_EVERY ticks for goal/RTL logic.

    Safety rules (every tick):
    - Blind agent: speed_scale 0.12
    - Low confidence (<0.4): speed_scale = confidence
    - Near-collision: agent moving fast toward an intruder/obstacle → emergency brake
    - Quad low altitude + moving down: climb 4.0

    Strategic rules (every ADVISE_EVERY):
    - Stall detected (speed ≈ 0, goal dist unchanged 10+ ticks): goal_override nudge
    - High interventions + far from goal: RTL
    - Quads blocked by dense scan ahead: climb 5.5
    """

    STALL_SPEED_THRESH = 0.05   # m/s — effectively stationary
    RECOVERY_TICKS = 35          # ticks to keep nudge active

    def __init__(self):
        self._directives: list[Directive] = []
        self._tick = 0
        self._prev_goal_dist: dict[str, float] = {}
        self._stall_ticks: dict[str, int] = {}
        # persistent recovery: aid -> (waypoint, ticks_remaining)
        self._recovery: dict[str, tuple] = {}
        # altitude separation: quad_id -> ticks of elevated flight remaining
        self._alt_sep: dict[str, int] = {}

    def tick(self, state: WorldState) -> list[Directive]:
        self._tick += 1
        directives: list[Directive] = []
        alive = [a for a in state.agents if a.alive]
        quads = [a for a in alive if a.vclass in ("quad", "quadcopter")]

        # --- safety rules (every tick) ---
        for a in alive:
            aid = a.id

            if not a.sensor_ok:
                directives.append(Directive(aid, "speed_scale", 0.12))
                continue

            if a.confidence < 0.4:
                directives.append(Directive(aid, "speed_scale", max(0.1, a.confidence)))
                continue

            speed = math.sqrt(sum(v*v for v in a.vel))

            # Quad altitude separation: when two quads are within 3m of each other
            # AND approaching (relative velocity closing), lower-priority quad climbs.
            # Only when min_scan_dist > 4m (open sky — not in a ceiling-constrained area).
            if a.vclass in ("quad", "quadcopter") and a.min_scan_dist > 4.0:
                my_rank = sorted(q.id for q in quads).index(aid)
                for b in quads:
                    if b.id == aid:
                        continue
                    dx = b.pos[0] - a.pos[0]
                    dy = b.pos[1] - a.pos[1]
                    sep = math.sqrt(dx*dx + dy*dy + (b.pos[2]-a.pos[2])**2)
                    if sep < 3.0:
                        # Check closing (relative vel dot relative pos < 0)
                        rvx = b.vel[0] - a.vel[0]
                        rvy = b.vel[1] - a.vel[1]
                        closing = dx*rvx + dy*rvy < 0  # positive = moving apart
                        b_rank = sorted(q.id for q in quads).index(b.id)
                        if my_rank > b_rank and (closing or sep < 1.8):
                            self._alt_sep[aid] = 50
                            break

            if aid in self._alt_sep:
                ticks = self._alt_sep[aid]
                if ticks > 0:
                    directives.append(Directive(aid, "climb", 6.5))
                    self._alt_sep[aid] = ticks - 1
                else:
                    del self._alt_sep[aid]

            # Quad low altitude guard
            if a.vclass in ("quad", "quadcopter") and len(a.pos) > 2 and a.pos[2] < 2.5:
                if len(a.vel) > 2 and a.vel[2] < -0.2:
                    directives.append(Directive(aid, "climb", 4.0))

            # Stall tracking (speed-based, reliable)
            if a.goal:
                cur_dist = math.dist(a.pos, a.goal)
                prev_dist = self._prev_goal_dist.get(aid, cur_dist)
                if cur_dist < 1.5:
                    self._stall_ticks[aid] = 0  # at goal — not a stall
                elif speed < self.STALL_SPEED_THRESH and abs(cur_dist - prev_dist) < 0.1:
                    self._stall_ticks[aid] = self._stall_ticks.get(aid, 0) + 1
                else:
                    self._stall_ticks[aid] = 0
                self._prev_goal_dist[aid] = cur_dist

            # Apply persistent recovery waypoint if active (every tick)
            if aid in self._recovery:
                wp, ticks_left = self._recovery[aid]
                if ticks_left > 0:
                    directives.append(Directive(aid, "goal_override", wp))
                    self._recovery[aid] = (wp, ticks_left - 1)
                else:
                    del self._recovery[aid]
            elif a.goal and a.confidence >= 0.4:
                # Near-goal lock: override intruder-induced repulsion/emergency-brake
                # when almost at goal. Guards against teammate proximity to avoid
                # suppressing legitimate collision-avoidance brakes.
                goal_dist = math.dist(a.pos, a.goal)
                if goal_dist < 2.0:
                    min_team_d = min(
                        (math.sqrt(sum((bp - ap) ** 2 for bp, ap in zip(b.pos, a.pos)))
                         for b in alive if b.id != aid),
                        default=99.0
                    )
                    if min_team_d > 2.0:
                        directives.append(Directive(aid, "goal_override", a.goal))

        # --- strategic rules (every ADVISE_EVERY ticks) ---
        if self._tick % ADVISE_EVERY == 0:
            for a in alive:
                aid = a.id
                if not a.sensor_ok or a.confidence < 0.4:
                    continue

                stall = self._stall_ticks.get(aid, 0)
                if stall >= 15 and aid not in self._recovery and a.goal:
                    gx = float(a.goal[0])
                    gy = float(a.goal[1])
                    gz = float(a.goal[2]) if len(a.goal) > 2 else 2.0
                    px, py, pz = float(a.pos[0]), float(a.pos[1]), float(a.pos[2])
                    dx, dy = gx - px, gy - py
                    d = math.sqrt(dx*dx + dy*dy) or 1.0
                    nx = px - dy/d * 3.0
                    ny = py + dx/d * 3.0
                    nz = max(gz, pz + 4.0) if a.vclass in ("quad", "quadcopter") else gz
                    self._recovery[aid] = ([nx, ny, nz], self.RECOVERY_TICKS)
                    self._stall_ticks[aid] = 0

                if state.interventions >= 5 and a.goal and aid not in self._recovery:
                    if math.dist(a.pos, a.goal) > 20.0:
                        directives.append(Directive(aid, "rtl", True))

        self._directives = directives
        return directives

    def directives_for(self, agent_id: str) -> list[Directive]:
        return [d for d in self._directives
                if d.agent_id is None or d.agent_id == agent_id]


class LLMSmart:
    """Calls Qwen3-14B on hoopoe GPU via SSH for multi-agent reasoning.

    Inference runs in a background thread; the sim tick loop is never blocked.
    Stale directives are served until a fresh response arrives.
    Falls back to RuleBasedSmart on SSH error or timeout.
    """

    SYSTEM_PROMPT = """\
You are the fleet commander for a heterogeneous team of autonomous drones (quads) and rovers.
Given a snapshot of fleet state, output a JSON array of directives.

Each directive: {"agent_id": "<id or null for team>", "kind": "<kind>", "value": <val>}
Kinds: speed_scale (0.0–1.5), rtl (true/false), climb (meters), yield_to (<agent_id>).

Rules:
- Blind agents (sensor='BLIND') should get speed_scale 0.1
- Low confidence (<0.4) agents: speed_scale = confidence value
- If an agent is far from goal and interventions > 3: consider rtl true
- Quads stuck behind rovers: climb 6.0
- Output [] if no directives needed.
Output valid JSON only, no explanation.
"""

    def __init__(self, hoopoe_host: str = "100.102.133.78",
                 proxy_cmd: str = "nc -X 5 -x 127.0.0.1:1080 %h %p",
                 model: str = "qwen3-14b"):
        self._host = hoopoe_host
        self._proxy = proxy_cmd
        self._model = model
        self._fallback = RuleBasedSmart()
        self._directives: list[Directive] = []
        self._lock = threading.Lock()
        self._pending = False
        self._tick = 0

    def tick(self, state: WorldState) -> list[Directive]:
        self._tick += 1
        # Delegate to fallback for non-advise ticks
        self._fallback.tick(state)
        if self._tick % ADVISE_EVERY != 0:
            with self._lock:
                return list(self._directives) or self._fallback.directives_for(None)

        if not self._pending:
            self._pending = True
            t = threading.Thread(target=self._infer, args=(state,), daemon=True)
            t.start()

        with self._lock:
            return list(self._directives) if self._directives \
                else self._fallback.directives_for(None)

    def _infer(self, state: WorldState) -> None:
        try:
            directives = self._call_hoopoe(state)
        except Exception:
            directives = self._fallback.directives_for(None)
        with self._lock:
            self._directives = directives
        self._pending = False

    def _call_hoopoe(self, state: WorldState) -> list[Directive]:
        import subprocess
        prompt = state.to_prompt()
        payload = json.dumps({"system": self.SYSTEM_PROMPT, "user": prompt,
                              "model": self._model, "max_tokens": 256})
        # Remote one-liner: reads JSON from stdin, runs inference, prints response JSON
        remote_cmd = (
            "python3 -c \""
            "import sys,json,subprocess;"
            "p=json.loads(sys.stdin.read());"
            "r=subprocess.run(['ollama','run',p['model']],"
            "input=p['system']+'\\n\\nFleet state:\\n'+p['user'],"
            "capture_output=True,text=True,timeout=10);"
            "print(r.stdout.strip())"
            "\""
        )
        ssh_cmd = [
            "ssh",
            "-o", f"ProxyCommand={self._proxy}",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=4",
            f"yusuf@{self._host}",
            remote_cmd,
        ]
        result = subprocess.run(ssh_cmd, input=payload, capture_output=True,
                                text=True, timeout=LLM_TIMEOUT_S + 1)
        raw = result.stdout.strip()
        # Parse JSON array from response (extract first [...] block)
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start < 0 or end <= start:
            return []
        arr = json.loads(raw[start:end])
        return [Directive(d.get("agent_id"), d["kind"], d["value"]) for d in arr]

    def directives_for(self, agent_id: str) -> list[Directive]:
        with self._lock:
            directives = list(self._directives)
        if not directives:
            return self._fallback.directives_for(agent_id)
        return [d for d in directives
                if d.agent_id is None or d.agent_id == agent_id]


# --------------------------------------------------------------------------- world state builder
def build_world_state(runner, interventions: int) -> WorldState:
    """Construct a WorldState snapshot from a live ScenarioRunner."""
    world = runner.world
    agents = []
    for a in world.agents.values():
        # Get min scan distance from last observation
        last_obs = getattr(a, "_last_obs", None)
        if last_obs is not None and hasattr(last_obs, "scan") and last_obs.scan is not None:
            import numpy as _np
            min_scan = float(_np.min(last_obs.scan)) if len(last_obs.scan) > 0 else 10.0
        else:
            min_scan = 10.0
        agents.append(AgentSnapshot(
            id=a.id,
            pos=[round(float(x), 2) for x in a.pos],
            vel=[round(float(x), 2) for x in a.vel],
            goal=[round(float(x), 2) for x in a.goal] if a.goal is not None else None,
            alive=a.alive,
            sensor_ok=getattr(a, "sensor_ok", True),
            confidence=getattr(a, "loc_confidence", 1.0),
            vclass=a.vclass.name,
            min_scan_dist=round(min_scan, 2),
        ))
    targets = []
    for t in runner.mission.targets:
        targets.append(TargetSnapshot(
            pos=[round(float(x), 2) for x in t.pos],
            found=t.found,
        ))
    active = getattr(runner, "_fired_injects", [])
    return WorldState(
        t=round(world.t, 1),
        agents=agents,
        targets=targets,
        interventions=interventions,
        comms_delivery_rate=runner.comms.stats.delivery_rate,
        active_injects=active,
    )
