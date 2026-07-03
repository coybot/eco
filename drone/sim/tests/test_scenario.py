"""Scenario DSL + inject engine tests (Phase 3)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from scenario import Scenario, ScenarioRunner, _expand_team
from localization import LocMode

SCN_DIR = Path(__file__).resolve().parent.parent / "scenarios"


def _run(d, max_s=30):
    return ScenarioRunner(Scenario.from_dict(d)).run(max_s=max_s)


# -- DSL loading --------------------------------------------------------------
def test_team_shorthand_expands():
    roster = _expand_team(["quad x2", "rover x1"])
    assert [r["type"] for r in roster] == ["quad", "quad", "rover"]
    assert roster[0]["id"] == "quad_0" and roster[2]["id"] == "rover_0"
    assert all("pos" in r and "goal" in r for r in roster)


def test_all_seed_scenarios_load_and_run():
    files = sorted(SCN_DIR.glob("*.yaml"))
    assert len(files) >= 12, f"expected >=12 seed scenarios, found {len(files)}"
    for f in files:
        scn = Scenario.from_yaml(str(f))
        res = ScenarioRunner(scn).run(max_s=20)   # short smoke run
        assert res["scenario"] == scn.name
        assert res["t_end"] > 0


# -- trigger types ------------------------------------------------------------
def test_time_trigger_fires():
    r = ScenarioRunner(Scenario.from_dict({
        "name": "t", "team": ["quad x1"], "mission": {"type": "patrol",
        "waypoints": [[10, 0], [-10, 0]]},
        "injects": [{"at": 1.0, "do": "comms_blackout"}], "duration_s": 5}))
    r.run()
    assert any("comms_blackout" in e for _, e in r.event_log)
    assert r.comms.mesh_degraded


def test_event_trigger_fires_on_target_found():
    r = ScenarioRunner(Scenario.from_dict({
        "name": "t", "team": ["quad x1"],
        "mission": {"type": "area_search", "region": [-12, -12, 12, 12], "targets": [[6, 0]]},
        "injects": [{"on": "target_found", "do": "gps_loss", "agent": "all"}],
        "duration_s": 30}))
    r.run()
    assert r.loc.mode("quad_0") is LocMode.GPS_DENIED


def test_condition_trigger_fires_when_low_confidence():
    r = ScenarioRunner(Scenario.from_dict({
        "name": "t", "team": ["quad x1"],
        "mission": {"type": "patrol", "waypoints": [[10, 0], [-10, 0], [0, 10]]},
        "injects": [
            {"at": 1.0, "do": "gps_loss", "agent": "all"},
            {"when": "any_low_confidence", "do": "wind_shift", "vector": [1.0, 0, 0]},
        ], "duration_s": 120}))
    r.run()
    assert any("wind_shift" in e for _, e in r.event_log)
    assert float(np.linalg.norm(r.world.backend.wind)) > 0


# -- handlers per family ------------------------------------------------------
def test_agent_failure_removes_from_team():
    r = ScenarioRunner(Scenario.from_dict({
        "name": "t", "team": ["quad x2"], "mission": {"type": "goto"},
        "injects": [{"at": 0.5, "do": "agent_failure", "agent": "quad_0"}],
        "duration_s": 5}))
    r.run()
    assert not r.world.agents["quad_0"].alive
    assert "quad_0" not in [a.id for a in r.world.team_agents()]


def test_sensor_dropout_blinds_agent():
    r = ScenarioRunner(Scenario.from_dict({
        "name": "t", "team": ["rover x1"], "mission": {"type": "goto"},
        "injects": [{"at": 0.5, "do": "sensor_dropout", "agent": "rover_0"}],
        "duration_s": 5}))
    r.run()
    assert not r.world.agents["rover_0"].sensor_ok


def test_spawn_dynamic_adds_nonteam_intruder():
    r = ScenarioRunner(Scenario.from_dict({
        "name": "t", "team": ["quad x1"], "mission": {"type": "goto"},
        "injects": [{"at": 0.5, "do": "spawn_dynamic", "path": [[5, 5, 2], [-5, -5, 2]]}],
        "duration_s": 5}))
    r.run()
    intruders = [a for a in r.world.agents.values() if not a.team]
    assert len(intruders) == 1
    assert any(e == "intruder" for _, e in r.event_log)


def test_add_nofly_adds_obstacle():
    r = ScenarioRunner(Scenario.from_dict({
        "name": "t", "team": ["quad x1"], "mission": {"type": "goto"},
        "injects": [{"at": 0.5, "do": "add_nofly", "center": [3, 0, 1.5], "half": [1, 1, 3]}],
        "duration_s": 3}))
    n0 = len(r.world.backend.obstacles())
    r.run()
    assert len(r.world.backend.obstacles()) == n0 + 1


def test_move_target_relocates():
    r = ScenarioRunner(Scenario.from_dict({
        "name": "t", "team": ["quad x1"],
        "mission": {"type": "area_search", "region": [-12, -12, 12, 12], "targets": [[10, 0]]},
        "injects": [{"at": 0.5, "do": "move_target", "target": "t0", "to": [-10, 0, 0]}],
        "duration_s": 3}))
    r.run()
    assert r.mission.targets[0].pos[0] == pytest.approx(-10.0)


def test_unknown_inject_raises():
    with pytest.raises(KeyError):
        ScenarioRunner(Scenario.from_dict({
            "name": "t", "team": ["quad x1"], "mission": {"type": "goto"},
            "injects": [{"at": 0.1, "do": "frobnicate"}], "duration_s": 2})).run()
