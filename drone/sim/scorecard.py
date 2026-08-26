"""Autonomy scorecard — the L-level metric (Phase 5).

Reach/collision rates don't tell you the autonomy *level*. The quantity that does is
**interventions required**: how many times a human would have had to step in. This
module runs a scenario tick-by-tick, detects intervention episodes operationally, and
rolls everything into a per-scenario score plus a suite-level L0–L5 rating.

Intervention episodes (rising-edge counted, not per-tick):
  - collision        — a team agent contacts an obstacle/teammate.
  - near_miss        — team agents pass within NEAR_MISS_PAD of each other.
  - stall            — best team progress-to-goal flat for STALL_WINDOW_S (mission live).
  - lost             — a team agent's true localization error exceeds LOST_ERR_M,
                       sustained (the spoof / bad-VIO case a human would catch).
  - mission_timeout  — scenario ran out of time with the mission incomplete.

Other reported metrics: mission success, safety (collisions, min separation), team
efficiency (time, redundant visits), comms resilience (delivery rate), localization
robustness (mean error / confidence).

Run one:   score_run(ScenarioRunner(...), use_runtime=True)
Run suite: score_suite("scenarios", use_runtime=True)
CLI:       python -m scorecard            (from sim/, prints the suite scorecard)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, asdict
from pathlib import Path
import itertools
import json

import numpy as np

try:  # packaged (eco.drone.sim) vs flat (sim/ on path) — see conftest
    from .scenario import Scenario, ScenarioRunner
    from .team_runtime import TeamRuntime
    from .sensor_model import IMPAIRMENTS
except ImportError:
    from scenario import Scenario, ScenarioRunner
    from team_runtime import TeamRuntime
    from sensor_model import IMPAIRMENTS


# thresholds
NEAR_MISS_PAD = 0.4        # m beyond the two radii
STALL_WINDOW_S = 8.0       # no progress for this long = a stall
STALL_EPS = 0.5            # m of "best progress" improvement that resets the stall timer
LOST_ERR_M = 5.0           # localization error a human would have to correct
LOST_WINDOW_S = 4.0


@dataclass(frozen=True)
class ScoreThresholds:
    """Intervention-detection thresholds, in absolute metres and seconds.

    These are detector sensitivities, not vehicle properties, so they belong to the
    *scenario* rather than the class: the same Crazyflie in a warehouse and in a
    bedroom warrants different near-miss padding. Defaults are the historical outdoor
    literals above, so an existing scenario with no ``thresholds:`` key scores identically.
    """
    near_miss_pad_m: float = NEAR_MISS_PAD
    stall_window_s: float = STALL_WINDOW_S
    stall_eps_m: float = STALL_EPS
    lost_err_m: float = LOST_ERR_M
    lost_window_s: float = LOST_WINDOW_S


DEFAULT_THRESHOLDS = ScoreThresholds()

# Indoor preset. A 5 m "lost" threshold cannot detect anything in an 8x6 m apartment, and a
# 0.5 m stall epsilon is half a room. Scaled to a ~0.065 m vehicle moving at 1 m/s.
INDOOR_THRESHOLDS = ScoreThresholds(
    near_miss_pad_m=0.12,
    stall_window_s=6.0,
    stall_eps_m=0.15,
    lost_err_m=0.75,
    lost_window_s=3.0,
)

PRESETS: dict[str, ScoreThresholds] = {
    "default": DEFAULT_THRESHOLDS,
    "indoor": INDOOR_THRESHOLDS,
}


def get_thresholds(spec) -> ScoreThresholds:
    """Resolve a preset name, a dict of overrides, or a ScoreThresholds to thresholds."""
    if spec is None or spec == "":
        return DEFAULT_THRESHOLDS
    if isinstance(spec, ScoreThresholds):
        return spec
    if isinstance(spec, dict):
        return ScoreThresholds(**{**DEFAULT_THRESHOLDS.__dict__, **spec})
    try:
        return PRESETS[spec]
    except KeyError:
        raise KeyError(f"unknown threshold preset {spec!r}; known: {sorted(PRESETS)}") from None


@dataclass
class ScenarioScore:
    scenario: str
    mission_complete: bool
    targets_found: int
    targets_total: int
    t_end: float
    interventions: int
    intervention_breakdown: dict
    collisions: int
    min_separation: float
    comms_delivery_rate: float
    mean_loc_error: float
    mean_confidence: float
    failures: list = field(default_factory=list)  # [(t, cause, agent_id, vclass, detail), ...]

    def as_dict(self) -> dict:
        return asdict(self)


class _Detector:
    """Per-run intervention/metric accumulator, fed one tick at a time."""

    def __init__(self, runner: ScenarioRunner, th: ScoreThresholds = DEFAULT_THRESHOLDS):
        self.r = runner
        self.th = th
        self.counts = {"collision": 0, "near_miss": 0, "stall": 0,
                       "lost": 0, "mission_timeout": 0}
        # rising-edge latches
        self._in_collision = False
        self._in_near_miss = False
        self._lost_since: dict[str, float] = {}
        self._agent_best: dict[str, tuple] = {}   # id -> (goal_key, best_dist)
        self._last_progress_t = 0.0
        self._last_targets_found = 0
        self._stalled_latched = False
        # rolling metrics
        self.min_sep = float("inf")
        self._loc_err_sum = 0.0
        self._conf_sum = 0.0
        self._samples = 0
        # per-agent, per-cause attribution: [(t, cause, agent_id, vclass, detail), ...]
        self.failures: list = []

    def update(self):
        w = self.r.world
        team = w.team_agents()
        if not team:
            return
        # --- pairwise separation (team only) ---
        tick_collision = False
        tick_near_miss = False
        collision_detail = None   # (agent_id, vclass, cause) of the first culprit this tick
        for a, b in itertools.combinations(team, 2):
            d = float(np.linalg.norm(a.pos - b.pos))
            surf = d - a.vclass.radius_m - b.vclass.radius_m
            self.min_sep = min(self.min_sep, surf)
            if surf < 0:
                tick_collision = True
                if collision_detail is None:
                    collision_detail = (a.id, a.vclass.name, f"teammate:{b.id}")
            elif surf < self.th.near_miss_pad_m:
                tick_near_miss = True
        # agent-vs-obstacle collision
        for a in team:
            boxes = w.backend.obstacles() + w._moving_obstacles(a)
            if w._min_surface_dist(a, boxes) < a.vclass.radius_m:
                tick_collision = True
                if collision_detail is None:
                    collision_detail = (a.id, a.vclass.name, "obstacle")
        self._edge("collision", tick_collision, "_in_collision", collision_detail)
        self._edge("near_miss", tick_near_miss, "_in_near_miss")

        # --- stall: no *mission* progress for a window while mission incomplete ---
        # Progress = any agent shrinking its CURRENT-goal distance (reset when its goal
        # changes, so reassignment doesn't read as a stall) OR a new target found.
        if not self.r.mission.complete(w):
            found = sum(t.found for t in self.r.mission.targets)
            if found > self._last_targets_found:
                self._last_targets_found = found
                self._last_progress_t = w.t
                self._stalled_latched = False
            for a in team:
                if a.goal is None:
                    continue
                gkey = (round(float(a.goal[0]), 1), round(float(a.goal[1]), 1),
                        round(float(a.goal[2]), 1))
                d = float(np.linalg.norm(a.goal - a.pos))
                prev = self._agent_best.get(a.id)
                if prev is None or prev[0] != gkey:
                    self._agent_best[a.id] = (gkey, d)            # new goal: reset baseline
                elif d < prev[1] - self.th.stall_eps_m:
                    self._agent_best[a.id] = (gkey, d)
                    self._last_progress_t = w.t
                    self._stalled_latched = False
            if (not self._stalled_latched
                    and w.t - self._last_progress_t > self.th.stall_window_s):
                self.counts["stall"] += 1
                self._stalled_latched = True

        # --- lost (sustained localization error) ---
        for a in team:
            err = self.r.loc.error(a)
            if err > self.th.lost_err_m:
                since = self._lost_since.setdefault(a.id, w.t)
                if w.t - since > self.th.lost_window_s:
                    self.counts["lost"] += 1
                    self._lost_since[a.id] = w.t + 1e6   # latch off until recovery
            else:
                self._lost_since.pop(a.id, None)

        # --- rolling metrics ---
        self._loc_err_sum += sum(self.r.loc.error(a) for a in team) / len(team)
        self._conf_sum += sum(self.r.loc.confidence(a) for a in team) / len(team)
        self._samples += 1

    def _edge(self, name, active, latch_attr, detail=None):
        if active and not getattr(self, latch_attr):
            self.counts[name] += 1
            if detail is not None:
                aid, vclass, cause = detail
                self.failures.append((round(self.r.world.t, 1), name, aid, vclass, cause))
        setattr(self, latch_attr, active)

    def finalize(self, mission_complete: bool):
        if not mission_complete:
            self.counts["mission_timeout"] += 1
            # attribute the timeout to the agent(s) still short of goal
            w = self.r.world
            for a in w.team_agents():
                if a.goal is not None and float(np.linalg.norm(a.goal - a.pos)) > 1.5:
                    self.failures.append((round(w.t, 1), "mission_timeout", a.id,
                                          a.vclass.name, "short_of_goal"))

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def score_run(runner: ScenarioRunner, use_runtime: bool = True,
              max_s: float | None = None, smart=None, thresholds=None) -> ScenarioScore:
    if smart is not None and hasattr(smart, "_reset_state"):
        smart._reset_state()  # clear per-scenario stale state (stall_ticks, recovery, etc.)
    rt = None
    if use_runtime:
        rt = TeamRuntime(runner)
        base_ctrl = rt.controller
        if smart is not None:
            def _smart_controller(agent, obs, _bc=base_ctrl, _sm=smart, _rt=rt,
                                  _det_ref=[0]):
                action = _bc(agent, obs)
                try:
                    from .smart_layer import build_world_state
                except ImportError:
                    from smart_layer import build_world_state
                # build world state once per tick (cached on runner by tick number)
                if not hasattr(runner, "_smart_ws_cache") or runner._smart_ws_cache[0] != runner.world.tick:
                    runner._smart_ws_cache = (runner.world.tick,
                                              build_world_state(runner, getattr(runner, "_smart_intv", 0)))
                ws = runner._smart_ws_cache[1]
                directives = _sm.tick(ws)
                a = np.asarray(action, dtype=np.float32).copy()
                for d in directives:
                    if d.agent_id is None or d.agent_id == agent.id:
                        a = d.apply(agent, a)
                return a
            runner.controller = _smart_controller
        else:
            runner.controller = base_ctrl
    # The scenario's own `thresholds:` wins over the suite-level default, so an indoor
    # YAML carries its detector sensitivity with it.
    det = _Detector(runner, get_thresholds(runner.scenario.thresholds or thresholds))
    max_s = max_s or runner.scenario.duration_s
    ticks = int(max_s / runner.world.dt)
    for _ in range(ticks):
        if smart is not None:
            runner._smart_intv = det.total
        runner.step()
        det.update()
        if runner.mission.complete(runner.world):
            break
    complete = runner.mission.complete(runner.world)
    det.finalize(complete)
    n = max(det._samples, 1)
    return ScenarioScore(
        scenario=runner.scenario.name,
        mission_complete=complete,
        targets_found=sum(t.found for t in runner.mission.targets),
        targets_total=len(runner.mission.targets),
        t_end=round(runner.world.t, 1),
        interventions=det.total,
        intervention_breakdown=dict(det.counts),
        collisions=det.counts["collision"],
        min_separation=round(det.min_sep, 2) if det.min_sep != float("inf") else None,
        comms_delivery_rate=runner.comms.stats.delivery_rate,
        mean_loc_error=round(det._loc_err_sum / n, 2),
        mean_confidence=round(det._conf_sum / n, 2),
        failures=list(det.failures),
    )


# --------------------------------------------------------------------------- rubric
def autonomy_level(scores: list[ScenarioScore]) -> tuple[int, str]:
    """Map suite results to L0–L5. L5 = self-governing across the whole suite."""
    n = len(scores)
    if n == 0:
        return 0, "no scenarios"
    success = sum(s.mission_complete for s in scores) / n
    mean_intv = sum(s.interventions for s in scores) / n
    any_collision = any(s.collisions for s in scores)
    if mean_intv == 0 and success == 1.0 and not any_collision:
        return 5, "0 interventions, 100% success, collision-free across suite"
    if mean_intv <= 0.5 and success >= 0.9:
        return 4, f"mean {mean_intv:.2f} interventions/scenario, {success:.0%} success"
    if mean_intv <= 2.0 and success >= 0.75:
        return 3, f"mean {mean_intv:.2f} interventions/scenario, {success:.0%} success"
    if mean_intv <= 5.0 and success >= 0.5:
        return 2, f"mean {mean_intv:.2f} interventions/scenario, {success:.0%} success"
    if success > 0:
        return 1, f"mean {mean_intv:.2f} interventions/scenario, {success:.0%} success"
    return 0, "no missions completed"


def score_suite(scenarios_dir: str = "scenarios", use_runtime: bool = True,
                smart=None, sensing: str | None = None, thresholds=None,
                impairment=None, impair_seed: int | None = None) -> dict:
    # sensing=None means "let each scenario's YAML decide, else ideal" — see ScenarioRunner.
    files = sorted(Path(scenarios_dir).glob("*.yaml"))
    scores = [score_run(ScenarioRunner(Scenario.from_yaml(str(f)), sensing=sensing,
                                       impairment=impairment, impair_seed=impair_seed),
                        use_runtime=use_runtime, smart=smart, thresholds=thresholds)
              for f in files]
    level, rationale = autonomy_level(scores)
    return {
        "autonomy_level": level,
        "rationale": rationale,
        "n_scenarios": len(scores),
        "mission_success_rate": round(sum(s.mission_complete for s in scores) / max(len(scores), 1), 3),
        "total_interventions": sum(s.interventions for s in scores),
        "mean_interventions": round(sum(s.interventions for s in scores) / max(len(scores), 1), 2),
        "scenarios": [s.as_dict() for s in scores],
    }


def _print_suite(report: dict):
    print(f"\n{'='*72}")
    print(f"  AUTONOMY SCORECARD — Level L{report['autonomy_level']}")
    print(f"  {report['rationale']}")
    print(f"{'='*72}")
    print(f"  scenarios={report['n_scenarios']}  "
          f"success={report['mission_success_rate']:.0%}  "
          f"total_interventions={report['total_interventions']}  "
          f"mean={report['mean_interventions']}/scenario\n")
    hdr = f"  {'scenario':28s} {'done':5s} {'intv':4s} {'breakdown':28s} {'minSep':7s} {'deliv':6s}"
    print(hdr)
    print(f"  {'-'*len(hdr)}")
    for s in report["scenarios"]:
        bd = ",".join(f"{k[:4]}{v}" for k, v in s["intervention_breakdown"].items() if v)
        print(f"  {s['scenario']:28s} {str(s['mission_complete'])[0]:5s} "
              f"{s['interventions']:<4d} {bd:28s} "
              f"{str(s['min_separation']):7s} {s['comms_delivery_rate']:.2f}")


def _load_rover_onnx_controller(onnx_path: str):
    """Wrap an ONNX rover policy (83-dim state, 2D unicycle action)."""
    try:
        import onnxruntime as ort
    except ImportError:
        raise ImportError("onnxruntime required — pip install onnxruntime")

    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    in0, in1 = sess.get_inputs()[0], sess.get_inputs()[1]
    hidden = in1.shape[-1]
    states: dict[str, np.ndarray] = {}

    def controller(agent, obs_obj):
        import math
        aid = agent.id
        if aid not in states:
            states[aid] = np.zeros((1, 1, hidden), dtype=np.float32)
        bt = np.asarray(obs_obj.body_target, dtype=np.float32)
        tf, tl = float(bt[0]), float(bt[1])
        dist = float(obs_obj.goal_dist)
        yaw_err = math.atan2(tl, tf) if dist > 1e-4 else 0.0
        lidar = np.asarray(obs_obj.scan, dtype=np.float32)
        if len(lidar) != 72:
            lidar = np.full(72, 10.0, dtype=np.float32)
        raw = np.array([tf, tl, 0.0, dist, 0.0, 0.0, 0.0, yaw_err, 0.0, 0.0, 1.0],
                       dtype=np.float32)
        state = np.concatenate([raw, lidar])[None, None, :]   # (1,1,83)
        action, h_out = sess.run(None, {in0.name: state, in1.name: states[aid]})
        states[aid] = h_out
        return np.clip(action[0, 0], [-2.0, -2.0], [2.0, 2.0]).astype(np.float32)

    return controller


def _load_quad_onnx_controller(onnx_path: str):
    """Wrap an ONNX quad policy (56-dim state, 4D holonomic action).

    Observation construction matches contract.py exactly:
      [0:3]   body_target (fwd, left, up)
      [3]     goal_dist
      [4:7]   body-frame velocity
      [7]     yaw_err
      [8]     yaw_rate (0 — not tracked in headless sim)
      [9]     altitude
      [10]    vehicle = 0.0 (quad)
      [11:56] depth grid (45 rays, row-major)
    """
    try:
        import onnxruntime as ort
    except ImportError:
        raise ImportError("onnxruntime required — pip install onnxruntime")

    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    in0, in1 = sess.get_inputs()[0], sess.get_inputs()[1]
    hidden = in1.shape[-1]
    states: dict[str, np.ndarray] = {}

    def controller(agent, obs_obj):
        import math
        aid = agent.id
        if aid not in states:
            states[aid] = np.zeros((1, 1, hidden), dtype=np.float32)
        bt = np.asarray(obs_obj.body_target, dtype=np.float32)
        tf, tl, tu = float(bt[0]), float(bt[1]), float(bt[2])
        dist = float(obs_obj.goal_dist)
        yaw_err = math.atan2(tl, tf) if dist > 1e-4 else 0.0
        # body-frame velocity: rotate world vel by -yaw
        c, s = math.cos(-agent.yaw), math.sin(-agent.yaw)
        wv = agent.vel
        vf = c * float(wv[0]) - s * float(wv[1])
        vl = s * float(wv[0]) + c * float(wv[1])
        vu = float(wv[2])
        depth45 = np.asarray(obs_obj.scan, dtype=np.float32)
        if len(depth45) != 45:
            depth45 = np.full(45, 10.0, dtype=np.float32)
        raw = np.array([tf, tl, tu, dist, vf, vl, vu, yaw_err, 0.0,
                        float(agent.pos[2]), 0.0], dtype=np.float32)
        expected_dim = int(in0.shape[-1]) if in0.shape[-1] else 56
        if expected_dim == 83:
            # Unified hetero policy: pad depth45 to 72 with zeros
            sensor = np.concatenate([depth45, np.zeros(27, dtype=np.float32)])
        else:
            sensor = depth45
        state = np.concatenate([raw, sensor])[None, None, :]   # (1,1,expected_dim)
        action, h_out = sess.run(None, {in0.name: state, in1.name: states[aid]})
        states[aid] = h_out
        spd = agent.vclass.max_speed_mps
        return np.clip(action[0, 0], [-spd, -spd, -spd, -2.0],
                       [spd, spd, spd, 2.0]).astype(np.float32)

    return controller


def _load_fixedwing_onnx_controller(onnx_path: str, seq_len: int = 16):
    """Wrap an ONNX fixed-wing policy (56-dim state, 4D coordinated-turn action).

    State layout is identical to the quad's (contract.py) — differing only in the
    `vehicle` flag (VEHICLE_FIXEDWING=2.0, fw_contract.py) and the depth range
    (DEPTH_MAX_FW=80.0 vs the quad's 10.0). team_world already senses fixed-wing
    agents out to their sense_range_m=80 (vehicle_class.FIXEDWING), so obs_obj.scan
    is already in that range; only the fallback fill value (used if a scan is ever
    malformed) differs here.

    Unlike the quad/rover policies (which carry a GRU hidden state across steps,
    inputs `(state, h_in)` -> `(action, h_out)`), the shipped fixed-wing exports
    (policy_fw.onnx, policy_fw_v1.onnx) are **window-mode**: a single
    `state_window` input of shape (batch, seq_len, 56) holding the last `seq_len`
    raw states, matching drone/common/reactive_planner.py's LearnedPlanner (same
    SEQ_LEN=16, same "roll the ring buffer, model normalizes internally"
    contract). Detected by input name so a future recurrent fixed-wing export
    (input `h_in` present) is handled the same way the quad/rover loaders are.

    Action is clipped to the fixed-wing envelope, not the quad's symmetric one:
    forward speed to [min_speed_mps, max_speed_mps] (never zero/reverse — a
    fixed-wing can't hover), yaw_rate to ±max_yaw_rate_radps. vy is unused by the
    coordinated-turn integrator; vz gets a final climb-angle clip there too.
    """
    try:
        import onnxruntime as ort
    except ImportError:
        raise ImportError("onnxruntime required — pip install onnxruntime")

    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    in_names = [i.name for i in sess.get_inputs()]
    recurrent = "h_in" in in_names
    STATE_DIM = 56
    VEHICLE_FIXEDWING = 2.0
    DEPTH_MAX_FW = 80.0

    if recurrent:
        in0 = sess.get_inputs()[in_names.index("state")] if "state" in in_names else sess.get_inputs()[0]
        in1 = sess.get_inputs()[in_names.index("h_in")]
        hidden = in1.shape[-1]
        hidden_states: dict[str, np.ndarray] = {}
    else:
        in0 = sess.get_inputs()[0]
        windows: dict[str, np.ndarray] = {}

    def _build_state_vec(agent, obs_obj) -> np.ndarray:
        import math
        bt = np.asarray(obs_obj.body_target, dtype=np.float32)
        tf, tl, tu = float(bt[0]), float(bt[1]), float(bt[2])
        dist = float(obs_obj.goal_dist)
        yaw_err = math.atan2(tl, tf) if dist > 1e-4 else 0.0
        c, s = math.cos(-agent.yaw), math.sin(-agent.yaw)
        wv = agent.vel
        vf = c * float(wv[0]) - s * float(wv[1])
        vl = s * float(wv[0]) + c * float(wv[1])
        vu = float(wv[2])
        depth45 = np.asarray(obs_obj.scan, dtype=np.float32)
        if len(depth45) != 45:
            depth45 = np.full(45, DEPTH_MAX_FW, dtype=np.float32)
        raw = np.array([tf, tl, tu, dist, vf, vl, vu, yaw_err, 0.0,
                        float(agent.pos[2]), VEHICLE_FIXEDWING], dtype=np.float32)
        return np.concatenate([raw, depth45])   # (56,)

    def controller(agent, obs_obj):
        aid = agent.id
        state_vec = _build_state_vec(agent, obs_obj)

        if recurrent:
            if aid not in hidden_states:
                hidden_states[aid] = np.zeros((1, 1, hidden), dtype=np.float32)
            inp = state_vec[None, None, :]   # (1,1,56)
            action, h_out = sess.run(None, {in0.name: inp, in1.name: hidden_states[aid]})
            hidden_states[aid] = h_out
            out = action[0, 0].astype(np.float32).copy()
        else:
            if aid not in windows:
                windows[aid] = np.zeros((seq_len, STATE_DIM), dtype=np.float32)
            windows[aid] = np.roll(windows[aid], -1, axis=0)
            windows[aid][-1] = state_vec
            inp = windows[aid][None, :, :]   # (1, seq_len, 56)
            action = sess.run(None, {in0.name: inp})[0]
            out = action[0].astype(np.float32).copy()

        vc = agent.vclass
        out[0] = float(np.clip(out[0], vc.min_speed_mps, vc.max_speed_mps))
        out[1] = float(np.clip(out[1], -vc.max_speed_mps, vc.max_speed_mps))
        out[2] = float(np.clip(out[2], -vc.max_speed_mps, vc.max_speed_mps))
        out[3] = float(np.clip(out[3], -vc.max_yaw_rate_radps, vc.max_yaw_rate_radps))
        return out

    return controller


# Keep the old name as an alias so any external callers don't break.
_load_onnx_controller = _load_rover_onnx_controller


def compare_runs(scenarios_dir: str, rover_onnx: str | None = None,
                 quad_onnx: str | None = None,
                 fw_onnx: str | None = None,
                 out_path: str | None = None) -> dict:
    """Run the full suite twice — once with reactive baseline, once with ONNX policies.

    rover_onnx: optional path to rover ONNX (83-dim → 2D unicycle).
    quad_onnx:  optional path to quad ONNX (56-dim → 4D holonomic).
    fw_onnx:    optional path to fixed-wing ONNX (56-dim → 4D coordinated-turn).
    Any class omitted here falls back to TeamRuntime (same as baseline) — this is
    what makes a fixed-wing-only suite (no rover in the roster) usable without a
    dummy rover_onnx path.

    Returns a comparison dict with per-scenario delta in interventions and a
    headline delta_level (positive = policy improved the autonomy level).
    """
    try:
        from .vehicle_class import Kinematics
    except ImportError:
        from vehicle_class import Kinematics
    files = sorted(Path(scenarios_dir).glob("*.yaml"))

    def _run_suite(use_onnx=False):
        scores = []
        for f in files:
            runner = ScenarioRunner(Scenario.from_yaml(str(f)))
            rt = TeamRuntime(runner)
            if use_onnx:
                rover_ctrl = _load_rover_onnx_controller(rover_onnx) if rover_onnx else None
                quad_ctrl = (_load_quad_onnx_controller(quad_onnx)
                             if quad_onnx else None)
                fw_ctrl = (_load_fixedwing_onnx_controller(fw_onnx)
                           if fw_onnx else None)
                _base = rt.controller

                def _mixed(agent, obs, _rc=rover_ctrl, _qc=quad_ctrl, _fc=fw_ctrl, _bc=_base):
                    if agent.vclass.kinematics is Kinematics.UNICYCLE_2D:
                        if _rc is None:
                            return _bc(agent, obs)
                        action = np.asarray(_rc(agent, obs), np.float32).copy()
                        if not getattr(agent, "sensor_ok", True):
                            action[0] *= 0.08
                            action[1] *= 0.2
                        for _nid, rel_body, _vel, _ovc in obs.neighbors:
                            if (float(rel_body[0]) > 0.3
                                    and abs(float(rel_body[1])) < 0.8
                                    and float(np.linalg.norm(rel_body[:2])) < 2.0):
                                action[0] *= 0.4
                                break
                        return action
                    elif agent.vclass.kinematics is Kinematics.COORDINATED_TURN_3D:
                        if _fc is None:
                            return _bc(agent, obs)
                        action = np.asarray(_fc(agent, obs), np.float32).copy()
                        if not getattr(agent, "sensor_ok", True):
                            # Blind fixed-wing: no "almost stop" option (can't drop
                            # below stall) — the safe degradation is flying more
                            # conservatively straight, not slower.
                            action[3] *= 0.3
                        return action
                    elif _qc is not None:
                        action = np.asarray(_qc(agent, obs), np.float32).copy()
                        if not getattr(agent, "sensor_ok", True):
                            action[:3] *= 0.08   # blind quad: almost stop
                        return action
                    return _bc(agent, obs)

                runner.controller = _mixed
            else:
                runner.controller = rt.controller
            scores.append(score_run(runner, use_runtime=False))
        return scores

    baseline_scores = _run_suite(use_onnx=False)
    policy_scores = _run_suite(use_onnx=True)

    base_level, base_rat = autonomy_level(baseline_scores)
    pol_level, pol_rat = autonomy_level(policy_scores)

    comparison = {
        "baseline": {
            "autonomy_level": base_level, "rationale": base_rat,
            "mean_interventions": round(sum(s.interventions for s in baseline_scores) / max(len(baseline_scores), 1), 2),
            "mission_success_rate": round(sum(s.mission_complete for s in baseline_scores) / max(len(baseline_scores), 1), 3),
        },
        "policy": {
            "autonomy_level": pol_level, "rationale": pol_rat,
            "rover_onnx": rover_onnx, "quad_onnx": quad_onnx, "fw_onnx": fw_onnx,
            "mean_interventions": round(sum(s.interventions for s in policy_scores) / max(len(policy_scores), 1), 2),
            "mission_success_rate": round(sum(s.mission_complete for s in policy_scores) / max(len(policy_scores), 1), 3),
        },
        "delta_level": pol_level - base_level,
        "scenarios": [
            {
                "name": b.scenario,
                "baseline_interventions": b.interventions,
                "baseline_breakdown": b.intervention_breakdown,
                "baseline_complete": b.mission_complete,
                "policy_interventions": p.interventions,
                "policy_breakdown": p.intervention_breakdown,
                "policy_complete": p.mission_complete,
                "delta": p.interventions - b.interventions,
            }
            for b, p in zip(baseline_scores, policy_scores)
        ],
    }
    if out_path:
        Path(out_path).write_text(json.dumps(comparison, indent=2))
        print(f"  wrote {out_path}")
    return comparison


def _print_comparison(cmp: dict):
    print(f"\n{'='*72}")
    print(f"  POLICY COMPARISON")
    b, p = cmp["baseline"], cmp["policy"]
    print(f"  baseline : L{b['autonomy_level']} — {b['rationale']}")
    print(f"  policy   : L{p['autonomy_level']} — {p['rationale']}")
    delta = cmp["delta_level"]
    sign = "+" if delta >= 0 else ""
    print(f"  Δ level  : {sign}{delta}  ({b['mean_interventions']:.2f} → {p['mean_interventions']:.2f} intv/scenario)")
    print(f"{'='*72}")
    print(f"  {'scenario':28s} {'base':5s} {'policy':6s} {'Δ':4s}")
    print(f"  {'-'*50}")
    for s in cmp["scenarios"]:
        d = s["delta"]
        sign = "+" if d > 0 else (" " if d == 0 else "")
        print(f"  {s['name']:28s} {s['baseline_interventions']:<5d} {s['policy_interventions']:<6d} {sign}{d}")


def _record_comparison_videos(scenarios_dir, rover_onnx, quad_onnx, video_dir, renderer, smart,
                              fw_onnx=None):
    try:
        from .vehicle_class import Kinematics
    except ImportError:
        from vehicle_class import Kinematics
    files = sorted(Path(scenarios_dir).glob("*.yaml"))
    rover_ctrl = _load_rover_onnx_controller(rover_onnx) if rover_onnx else None
    quad_ctrl = _load_quad_onnx_controller(quad_onnx) if quad_onnx else None
    fw_ctrl = _load_fixedwing_onnx_controller(fw_onnx) if fw_onnx else None
    for f in files:
        runner = ScenarioRunner(Scenario.from_yaml(str(f)))
        rt = TeamRuntime(runner)
        _base = rt.controller

        def _mixed(agent, obs, _rc=rover_ctrl, _qc=quad_ctrl, _fc=fw_ctrl, _bc=_base):
            if agent.vclass.kinematics is Kinematics.UNICYCLE_2D:
                if _rc is None:
                    return _bc(agent, obs)
                action = np.asarray(_rc(agent, obs), np.float32).copy()
                if not getattr(agent, "sensor_ok", True):
                    action[0] *= 0.08; action[1] *= 0.2
                for _nid, rel_body, _vel, _ovc in obs.neighbors:
                    if (float(rel_body[0]) > 0.3 and abs(float(rel_body[1])) < 0.8
                            and float(np.linalg.norm(rel_body[:2])) < 2.0):
                        action[0] *= 0.4; break
                return action
            elif agent.vclass.kinematics is Kinematics.COORDINATED_TURN_3D:
                if _fc is None:
                    return _bc(agent, obs)
                action = np.asarray(_fc(agent, obs), np.float32).copy()
                if not getattr(agent, "sensor_ok", True):
                    action[3] *= 0.3
                return action
            elif _qc is not None:
                action = np.asarray(_qc(agent, obs), np.float32).copy()
                if not getattr(agent, "sensor_ok", True):
                    action[:3] *= 0.08
                return action
            return _bc(agent, obs)

        runner.controller = _mixed
        det = _Detector(runner)
        max_s = runner.scenario.duration_s
        ticks = int(max_s / runner.world.dt)
        frames = []
        renderer.reset(runner.scenario.name, runner.world.backend.obstacles())
        for _ in range(ticks):
            runner.step()
            det.update()
            frames.append(renderer.capture_frame(
                list(runner.world.agents.values()), det.total, runner.world.t))
            if runner.mission.complete(runner.world):
                break
        out_path = str(Path(video_dir) / f"{runner.scenario.name}.mp4")
        renderer.save_mp4(frames, out_path)
        print(f"  video → {out_path}")


def _record_baseline_videos(scenarios_dir, video_dir, renderer, smart):
    files = sorted(Path(scenarios_dir).glob("*.yaml"))
    for f in files:
        runner = ScenarioRunner(Scenario.from_yaml(str(f)))
        rt = TeamRuntime(runner)
        runner.controller = rt.controller
        det = _Detector(runner)
        ticks = int(runner.scenario.duration_s / runner.world.dt)
        frames = []
        renderer.reset(runner.scenario.name, runner.world.backend.obstacles())
        for _ in range(ticks):
            runner.step()
            det.update()
            frames.append(renderer.capture_frame(
                list(runner.world.agents.values()), det.total, runner.world.t))
            if runner.mission.complete(runner.world):
                break
        out_path = str(Path(video_dir) / f"{runner.scenario.name}_baseline.mp4")
        renderer.save_mp4(frames, out_path)
        print(f"  video → {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Autonomy scorecard CLI")
    ap.add_argument("--scenarios", default=None, help="directory of YAML scenarios")
    ap.add_argument("--rover-policy", "--policy", default=None, dest="rover_policy",
                    help="ONNX rover policy (83-dim → 2D) to compare against baseline")
    ap.add_argument("--quad-policy", default=None,
                    help="ONNX quad policy (56-dim → 4D); omit to keep quads on TeamRuntime")
    ap.add_argument("--fw-policy", default=None,
                    help="ONNX fixed-wing policy (56-dim → 4D coordinated-turn); "
                         "omit to keep fixed-wing agents on TeamRuntime")
    ap.add_argument("--record-video", default=None, metavar="DIR",
                    help="save per-scenario overhead MP4s to this directory")
    ap.add_argument("--out", default=None, help="write report JSON to this path")
    ap.add_argument("--smart-layer", default=None, choices=["rule", "llm"],
                    help="enable smart layer (rule=RuleBasedSmart, llm=LLMSmart via hoopoe)")
    # Defaults are None so "not passed" is distinguishable from "passed the default value":
    # an explicit flag must beat the scenario YAML's own sensing block, or ablation runs
    # silently report the YAML's config instead of the one you asked for.
    ap.add_argument("--sensing", default=None,
                    choices=["ideal", "realistic", "reconstructed"],
                    help="clearance sensing model: ideal=true-surface oracle (legacy L5 "
                         "baseline); realistic=nearest sensor return only (what hardware "
                         "actually has); reconstructed=surface fitted from adjacent beam "
                         "returns (implemented in team_world, previously unreachable here)")
    ap.add_argument("--thresholds", default=None, choices=sorted(PRESETS),
                    help="intervention-detection thresholds: default=outdoor literals; "
                         "indoor=scaled for a room-sized world (a 5 m 'lost' threshold "
                         "cannot detect anything in an 8x6 m apartment)")
    ap.add_argument("--impairment", default=None, choices=sorted(IMPAIRMENTS),
                    help="per-ray-group sensor impairment (sensor_model.py). "
                         "crazyflie_decks = monocular-depth scale bias + latency + "
                         "correlated frame dropout on the camera rays, metric ToF beams")
    ap.add_argument("--impair-seed", type=int, default=None,
                    help="seed for the impairment model; sweep it to report a distribution "
                         "rather than one lucky run")
    args = ap.parse_args()

    _here = Path(__file__).resolve().parent
    scn_dir = args.scenarios or str(_here / "scenarios")

    # Renderer (optional)
    renderer = None
    if args.record_video:
        try:
            from .render import OverheadRenderer
            renderer = OverheadRenderer()
            Path(args.record_video).mkdir(parents=True, exist_ok=True)
        except ImportError:
            print("  [warn] matplotlib not available — --record-video ignored")

    # Smart layer (optional)
    smart = None
    if args.smart_layer:
        from .smart_layer import RuleBasedSmart, LLMSmart
        smart = LLMSmart() if args.smart_layer == "llm" else RuleBasedSmart()

    if args.rover_policy or args.quad_policy or args.fw_policy:
        cmp = compare_runs(scn_dir, rover_onnx=args.rover_policy,
                           quad_onnx=args.quad_policy,
                           fw_onnx=args.fw_policy,
                           out_path=args.out)
        _print_comparison(cmp)
        if renderer and args.record_video:
            _record_comparison_videos(scn_dir, args.rover_policy,
                                      args.quad_policy, args.record_video, renderer, smart,
                                      fw_onnx=args.fw_policy)
    else:
        rep = score_suite(scn_dir, use_runtime=True, smart=smart, sensing=args.sensing,
                          thresholds=args.thresholds, impairment=args.impairment,
                          impair_seed=args.impair_seed)
        _print_suite(rep)
        out = args.out or str(_here / "scorecard_baseline.json")
        Path(out).write_text(json.dumps(rep, indent=2))
        print(f"\n  wrote {out}")
        if renderer and args.record_video:
            _record_baseline_videos(scn_dir, args.record_video, renderer, smart)

