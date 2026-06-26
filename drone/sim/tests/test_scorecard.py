"""Autonomy scorecard tests (Phase 5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from scenario import Scenario, ScenarioRunner
from scorecard import score_run, score_suite, autonomy_level, ScenarioScore

SCN = str(Path(__file__).resolve().parent.parent / "scenarios")


def _mk(**kw):
    base = dict(scenario="s", mission_complete=True, targets_found=0, targets_total=0,
                t_end=1.0, interventions=0, intervention_breakdown={}, collisions=0,
                min_separation=1.0, comms_delivery_rate=1.0, mean_loc_error=0.0,
                mean_confidence=1.0)
    base.update(kw)
    return ScenarioScore(**base)


def test_clean_scenario_zero_interventions():
    # open-field goto, no injects -> should be intervention-free
    s = score_run(ScenarioRunner(Scenario.from_dict({
        "name": "clean", "team": ["quad x2"], "mission": {"type": "goto"},
        "duration_s": 40})), use_runtime=True)
    assert s.mission_complete
    assert s.interventions == 0
    assert s.collisions == 0


def test_score_has_all_fields():
    s = score_run(ScenarioRunner(Scenario.from_yaml(f"{SCN}/gps_spoof_recon.yaml")))
    d = s.as_dict()
    for k in ("interventions", "intervention_breakdown", "min_separation",
              "comms_delivery_rate", "mean_loc_error", "mean_confidence"):
        assert k in d


def test_spoof_raises_localization_error():
    # gps spoof -> belief diverges from truth -> mean_loc_error grows
    s = score_run(ScenarioRunner(Scenario.from_yaml(f"{SCN}/gps_spoof_recon.yaml")))
    assert s.mean_loc_error > 0.0


def test_chokepoint_records_collisions():
    s = score_run(ScenarioRunner(Scenario.from_yaml(f"{SCN}/chokepoint_deconfliction.yaml")))
    # tight 5-agent funnel is the hard case -> collisions expected for the baseline
    assert s.collisions >= 1
    assert s.interventions >= 1


def test_rubric_l5_requires_perfection():
    scores = [_mk(interventions=0, collisions=0, mission_complete=True) for _ in range(5)]
    level, _ = autonomy_level(scores)
    assert level == 5


def test_rubric_demotes_on_collision():
    scores = [_mk(interventions=0, collisions=1, mission_complete=True) for _ in range(5)]
    level, _ = autonomy_level(scores)
    assert level < 5


def test_rubric_l1_low_success():
    scores = ([_mk(mission_complete=False, interventions=6) for _ in range(8)]
              + [_mk(mission_complete=True) for _ in range(2)])
    level, _ = autonomy_level(scores)
    assert level <= 1


def test_suite_runs_and_levels():
    rep = score_suite(SCN, use_runtime=True)
    assert rep["n_scenarios"] >= 12
    assert 0 <= rep["autonomy_level"] <= 5
    assert "mean_interventions" in rep
