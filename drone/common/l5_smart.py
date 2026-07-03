"""L5 smart layer — the fleet advisor, shipped for on-device use.

Vendored, dependency-light copy of the ``RuleBasedSmart`` advisor in
``eco/drone/sim/smart_layer.py`` — the fleet-coordination layer that, sitting on
top of the ``reactive_goto_controller``, produces the L5 result (0 interventions
across the 16-scenario suite). It runs safety rules every tick (blind/low-confidence
slow-down, quad altitude separation, low-altitude climb guard, near-goal lock) and
strategic rules every ADVISE_EVERY ticks (stall recovery nudges, RTL).

Kept byte-identical to the sim advisor by ``common/tests/test_l5_parity.py``.
Stdlib-only (math, heapq) so it installs flat on the Jetson. ``LLMSmart`` (the
Qwen-on-hoopoe variant) is intentionally omitted — the device runs rules only.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    try:
        from .l5_core import Agent
    except ImportError:  # pragma: no cover
        from l5_core import Agent

ADVISE_EVERY = 20       # ticks between advise() calls (~2s at dt=0.1)



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
    obstacles: list[list[float]] = field(default_factory=list)  # [cx,cy,cz,hx,hy,hz]

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
                max_yr = float(getattr(agent.vclass, 'max_yaw_rate_radps', 2.0))
                a[1] = max_yr * yaw_err / (abs(yaw_err) + 0.3)
                a[0] = spd * max(0.0, math.cos(yaw_err))
        return a


# --------------------------------------------------------------------------- path helpers

def _direct_path_hits_obstacle(
    px: float, py: float, gx: float, gy: float,
    obstacles: list[list[float]], agent_radius: float,
) -> bool:
    """Return True if the straight line from (px,py) to (gx,gy) passes
    within agent_radius of any obstacle's 2-D footprint."""
    dx, dy = gx - px, gy - py
    dist = math.sqrt(dx**2 + dy**2)
    if dist < 0.5:
        return False
    n = max(3, int(dist / 0.5))
    for i in range(1, n):
        t = i / n
        sx, sy = px + dx * t, py + dy * t
        for obs in obstacles:
            cx, cy, hx, hy = obs[0], obs[1], obs[3], obs[4]
            if abs(sx - cx) < hx + agent_radius and abs(sy - cy) < hy + agent_radius:
                return True
    return False


# --------------------------------------------------------------------------- A* path planner
_GRID_RES = 0.75  # m per grid cell


def _astar_waypoint(
    px: float, py: float,
    gx: float, gy: float,
    obstacles: list[list[float]],
    agent_radius: float,
    lookahead_m: float = 3.0,
) -> list[float] | None:
    """Return next waypoint along an A* path from (px,py) toward (gx,gy).

    Builds a 2-D grid padded by agent_radius around each obstacle and runs A*.
    Returns the world-frame [wx, wy] ~lookahead_m ahead on the path, or None
    if the goal is already very close or no path found.
    """
    if math.sqrt((gx-px)**2 + (gy-py)**2) < 1.5:
        return None

    res = _GRID_RES
    margin = 3.0
    x0 = min(px, gx) - margin
    y0 = min(py, gy) - margin
    x1 = max(px, gx) + margin
    y1 = max(py, gy) + margin
    nx = max(4, int((x1 - x0) / res) + 1)
    ny = max(4, int((y1 - y0) / res) + 1)

    # Build blocked-cell set
    blocked: set[tuple[int, int]] = set()
    pad = agent_radius + 0.1
    for obs in obstacles:
        cx, cy, _cz, hx, hy = obs[0], obs[1], obs[2], obs[3], obs[4]
        # Grid cells whose centers are within pad of the obstacle footprint
        ix_lo = max(0, int((cx - hx - pad - x0) / res) - 1)
        ix_hi = min(nx - 1, int((cx + hx + pad - x0) / res) + 1)
        iy_lo = max(0, int((cy - hy - pad - y0) / res) - 1)
        iy_hi = min(ny - 1, int((cy + hy + pad - y0) / res) + 1)
        for ix in range(ix_lo, ix_hi + 1):
            for iy in range(iy_lo, iy_hi + 1):
                wx = x0 + ix * res
                wy = y0 + iy * res
                dx = max(0.0, abs(wx - cx) - hx)
                dy = max(0.0, abs(wy - cy) - hy)
                if math.sqrt(dx*dx + dy*dy) < pad:
                    blocked.add((ix, iy))

    def to_grid(wx: float, wy: float) -> tuple[int, int]:
        return (int((wx - x0) / res), int((wy - y0) / res))

    def to_world(ix: int, iy: int) -> tuple[float, float]:
        return (x0 + ix * res, y0 + iy * res)

    si, sj = to_grid(px, py)
    gi, gj = to_grid(gx, gy)
    si = max(0, min(nx-1, si)); sj = max(0, min(ny-1, sj))
    gi = max(0, min(nx-1, gi)); gj = max(0, min(ny-1, gj))

    if (si, sj) == (gi, gj):
        return None

    # A* search
    DIRS = [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]
    COSTS = [1.0, 1.0, 1.0, 1.0, 1.414, 1.414, 1.414, 1.414]
    g_cost: dict[tuple[int,int], float] = {(si, sj): 0.0}
    came_from: dict[tuple[int,int], tuple[int,int]] = {}
    h = lambda i, j: math.sqrt((i-gi)**2 + (j-gj)**2)
    heap = [(h(si, sj), si, sj)]

    while heap:
        f, ci, cj = heapq.heappop(heap)
        if (ci, cj) == (gi, gj):
            break
        if f > g_cost.get((ci, cj), math.inf) + h(ci, cj) + 0.001:
            continue
        for (di, dj), cost in zip(DIRS, COSTS):
            ni, nj = ci + di, cj + dj
            if not (0 <= ni < nx and 0 <= nj < ny):
                continue
            if (ni, nj) in blocked:
                continue
            ng = g_cost[(ci, cj)] + cost
            if ng < g_cost.get((ni, nj), math.inf):
                g_cost[(ni, nj)] = ng
                came_from[(ni, nj)] = (ci, cj)
                heapq.heappush(heap, (ng + h(ni, nj), ni, nj))
    else:
        return None  # no path found

    # Reconstruct path
    path = []
    cur = (gi, gj)
    while cur in came_from:
        path.append(cur)
        cur = came_from[cur]
    path.reverse()

    if not path:
        return None

    # Pick first waypoint at lookahead_m distance along path
    dist = 0.0
    prev = (si, sj)
    for cell in path:
        wx, wy = to_world(*cell)
        dx = wx - to_world(*prev)[0]
        dy = wy - to_world(*prev)[1]
        dist += math.sqrt(dx*dx + dy*dy)
        prev = cell
        if dist >= lookahead_m:
            return [wx, wy]
    # Path shorter than lookahead: return goal
    return [gx, gy]


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
        self._reset_state()

    def _reset_state(self):
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
                # team_runtime already applies 0.08x scale for blind agents;
                # no further speed_scale needed here (stacking would be too slow)
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
                if goal_dist < 3.0:
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


