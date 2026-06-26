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
            # Signal RTL by zeroing forward motion — TeamRuntime will handle goal reset
            a *= 0.0
        elif self.kind == "climb" and len(a) == 4:
            a[2] = max(float(a[2]), float(self.value))
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

    Rules applied every ADVISE_EVERY ticks:
    - Blind + moving: slow to 15% to avoid overshooting
    - Low confidence (<0.4): slow to confidence value
    - Healthy agent near stuck blind teammate: yield (speed_scale 0.5)
    - High interventions (>3) + mission incomplete: issue RTL for agents far from goal
    """

    def __init__(self):
        self._directives: list[Directive] = []
        self._tick = 0

    def tick(self, state: WorldState) -> list[Directive]:
        self._tick += 1
        if self._tick % ADVISE_EVERY != 0:
            return self._directives

        directives: list[Directive] = []
        alive = [a for a in state.agents if a.alive]

        for a in alive:
            if not a.sensor_ok:
                directives.append(Directive(a.id, "speed_scale", 0.15))
            elif a.confidence < 0.4:
                directives.append(Directive(a.id, "speed_scale", a.confidence))

        # High-intervention RTL for distant agents
        if state.interventions >= 4:
            for a in alive:
                if a.goal:
                    dist = math.dist(a.pos, a.goal)
                    if dist > 18.0 and a.sensor_ok:
                        directives.append(Directive(a.id, "rtl", True))

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
        agents.append(AgentSnapshot(
            id=a.id,
            pos=[round(float(x), 2) for x in a.pos],
            vel=[round(float(x), 2) for x in a.vel],
            goal=[round(float(x), 2) for x in a.goal] if a.goal is not None else None,
            alive=a.alive,
            sensor_ok=getattr(a, "sensor_ok", True),
            confidence=getattr(a, "loc_confidence", 1.0),
            vclass=a.vclass.name,
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
