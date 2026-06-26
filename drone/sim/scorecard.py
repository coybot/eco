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

from scenario import Scenario, ScenarioRunner
from team_runtime import TeamRuntime


# thresholds
NEAR_MISS_PAD = 0.4        # m beyond the two radii
STALL_WINDOW_S = 8.0       # no progress for this long = a stall
STALL_EPS = 0.5            # m of "best progress" improvement that resets the stall timer
LOST_ERR_M = 5.0           # localization error a human would have to correct
LOST_WINDOW_S = 4.0


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

    def as_dict(self) -> dict:
        return asdict(self)


class _Detector:
    """Per-run intervention/metric accumulator, fed one tick at a time."""

    def __init__(self, runner: ScenarioRunner):
        self.r = runner
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

    def update(self):
        w = self.r.world
        team = w.team_agents()
        if not team:
            return
        # --- pairwise separation (team only) ---
        tick_collision = False
        tick_near_miss = False
        for a, b in itertools.combinations(team, 2):
            d = float(np.linalg.norm(a.pos - b.pos))
            surf = d - a.vclass.radius_m - b.vclass.radius_m
            self.min_sep = min(self.min_sep, surf)
            if surf < 0:
                tick_collision = True
            elif surf < NEAR_MISS_PAD:
                tick_near_miss = True
        # agent-vs-obstacle collision
        for a in team:
            boxes = w.backend.obstacles() + w._moving_obstacles(a)
            if w._min_surface_dist(a, boxes) < a.vclass.radius_m:
                tick_collision = True
        self._edge("collision", tick_collision, "_in_collision")
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
                elif d < prev[1] - STALL_EPS:
                    self._agent_best[a.id] = (gkey, d)
                    self._last_progress_t = w.t
                    self._stalled_latched = False
            if (not self._stalled_latched
                    and w.t - self._last_progress_t > STALL_WINDOW_S):
                self.counts["stall"] += 1
                self._stalled_latched = True

        # --- lost (sustained localization error) ---
        for a in team:
            err = self.r.loc.error(a)
            if err > LOST_ERR_M:
                since = self._lost_since.setdefault(a.id, w.t)
                if w.t - since > LOST_WINDOW_S:
                    self.counts["lost"] += 1
                    self._lost_since[a.id] = w.t + 1e6   # latch off until recovery
            else:
                self._lost_since.pop(a.id, None)

        # --- rolling metrics ---
        self._loc_err_sum += sum(self.r.loc.error(a) for a in team) / len(team)
        self._conf_sum += sum(self.r.loc.confidence(a) for a in team) / len(team)
        self._samples += 1

    def _edge(self, name, active, latch_attr):
        if active and not getattr(self, latch_attr):
            self.counts[name] += 1
        setattr(self, latch_attr, active)

    def finalize(self, mission_complete: bool):
        if not mission_complete:
            self.counts["mission_timeout"] += 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def score_run(runner: ScenarioRunner, use_runtime: bool = True,
              max_s: float | None = None) -> ScenarioScore:
    if use_runtime:
        rt = TeamRuntime(runner)
        runner.controller = rt.controller
    det = _Detector(runner)
    max_s = max_s or runner.scenario.duration_s
    ticks = int(max_s / runner.world.dt)
    for _ in range(ticks):
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


def score_suite(scenarios_dir: str = "scenarios", use_runtime: bool = True) -> dict:
    files = sorted(Path(scenarios_dir).glob("*.yaml"))
    scores = [score_run(ScenarioRunner(Scenario.from_yaml(str(f))), use_runtime=use_runtime)
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


def _load_onnx_controller(onnx_path: str):
    """Return a controller function that wraps an ONNX rover policy.

    The ONNX model has inputs (state[1,1,83], h_in[1,1,H]) and outputs
    (action[1,2], h_out[1,1,H]).  Hidden size is inferred from a dry run.

    The returned controller matches the signature expected by ScenarioRunner:
        controller(agent, obs) -> np.ndarray[2]  (v_linear, yaw_rate)
    """
    try:
        import onnxruntime as ort
    except ImportError:
        raise ImportError("onnxruntime is required to load a policy ONNX — pip install onnxruntime")

    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    in0 = sess.get_inputs()[0]
    in1 = sess.get_inputs()[1]
    hidden = in1.shape[-1]
    states: dict[str, np.ndarray] = {}   # per-agent GRU hidden state

    def controller(agent, obs_obj):
        import math
        aid = agent.id
        if aid not in states:
            states[aid] = np.zeros((1, 1, hidden), dtype=np.float32)
        # Observation has body_target (3,), goal_dist, scan — use directly
        bt = np.asarray(obs_obj.body_target, dtype=np.float32)
        tf, tl = float(bt[0]), float(bt[1])
        dist = float(obs_obj.goal_dist)
        yaw_err = math.atan2(tl, tf) if dist > 1e-4 else 0.0
        lidar = np.asarray(obs_obj.scan, dtype=np.float32) if obs_obj.scan is not None else np.full(72, 10.0, dtype=np.float32)
        if len(lidar) != 72:
            lidar = np.full(72, 10.0, dtype=np.float32)
        raw = np.array([tf, tl, 0.0, dist, 0.0, 0.0, 0.0, yaw_err, 0.0, 0.0, 1.0],
                       dtype=np.float32)
        state = np.concatenate([raw, lidar])[None, None, :]  # (1,1,83)
        action, h_out = sess.run(None, {in0.name: state, in1.name: states[aid]})
        states[aid] = h_out
        act2 = np.clip(action[0], [-2.0, -2.0], [2.0, 2.0])
        from vehicle_class import Kinematics
        if agent.vclass.kinematics is Kinematics.HOLONOMIC_3D:
            # pad unicycle [v, w] → holonomic [vx, 0, 0, w]
            return np.array([act2[0], 0.0, 0.0, act2[1]], dtype=np.float32)
        return act2

    return controller


def compare_runs(scenarios_dir: str, onnx_path: str, out_path: str | None = None) -> dict:
    """Run the full suite twice — once with reactive baseline, once with ONNX policy.

    Returns a comparison dict with per-scenario delta in interventions and a
    headline delta_level (positive = ONNX policy improved the autonomy level).
    """
    files = sorted(Path(scenarios_dir).glob("*.yaml"))

    def _run_suite(use_onnx=False):
        scores = []
        for f in files:
            runner = ScenarioRunner(Scenario.from_yaml(str(f)))
            rt = TeamRuntime(runner)
            if use_onnx:
                onnx_ctrl = _load_onnx_controller(onnx_path)
                from vehicle_class import Kinematics
                _base = rt.controller
                def _mixed(agent, obs, _oc=onnx_ctrl, _bc=_base):
                    if agent.vclass.kinematics is Kinematics.UNICYCLE_2D:
                        return _oc(agent, obs)
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
            "onnx_path": onnx_path,
            "mean_interventions": round(sum(s.interventions for s in policy_scores) / max(len(policy_scores), 1), 2),
            "mission_success_rate": round(sum(s.mission_complete for s in policy_scores) / max(len(policy_scores), 1), 3),
        },
        "delta_level": pol_level - base_level,
        "scenarios": [
            {
                "name": b.scenario,
                "baseline_interventions": b.interventions,
                "policy_interventions": p.interventions,
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Autonomy scorecard CLI")
    ap.add_argument("--scenarios", default=None, help="directory of YAML scenarios")
    ap.add_argument("--policy", default=None,
                    help="ONNX rover policy to compare against reactive baseline")
    ap.add_argument("--out", default=None, help="write report JSON to this path")
    args = ap.parse_args()

    _here = Path(__file__).resolve().parent
    scn_dir = args.scenarios or str(_here / "scenarios")

    if args.policy:
        cmp = compare_runs(scn_dir, args.policy, out_path=args.out)
        _print_comparison(cmp)
    else:
        rep = score_suite(scn_dir, use_runtime=True)
        _print_suite(rep)
        out = args.out or str(_here / "scorecard_baseline.json")
        Path(out).write_text(json.dumps(rep, indent=2))
        print(f"\n  wrote {out}")
