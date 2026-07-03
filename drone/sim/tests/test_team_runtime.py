"""Team runtime coordination tests (Phase 4)."""
from __future__ import annotations

import numpy as np
import pytest

from scenario import Scenario, ScenarioRunner
from team_runtime import TeamRuntime


def _runner(name=None, d=None):
    scn = Scenario.from_yaml(f"{_SCN}/{name}.yaml") if name else Scenario.from_dict(d)
    r = ScenarioRunner(scn)
    rt = TeamRuntime(r)
    r.controller = rt.controller
    return r, rt


from pathlib import Path
_SCN = str(Path(__file__).resolve().parent.parent / "scenarios")


def test_runtime_disables_mission_autoassign():
    r, rt = _runner(d={"name": "t", "team": ["quad x2"],
                       "mission": {"type": "area_search", "region": [-12, -12, 12, 12],
                                   "targets": [[8, 0], [-8, 0]]}, "duration_s": 40})
    assert r.mission.auto_assign is False


def test_two_agents_split_two_targets():
    r, rt = _runner(d={"name": "t", "team": ["quad x2"],
                       "mission": {"type": "area_search", "region": [-12, -12, 12, 12],
                                   "targets": [[10, 0], [-10, 0]]}, "duration_s": 60})
    res = r.run()
    # auction should let the pair cover both targets
    assert res["targets_found"] == 2
    assert res["collisions"] == 0


def test_comms_blackout_still_completes_local_only():
    r, rt = _runner(d={"name": "t", "team": ["quad x2"],
                       "mission": {"type": "area_search", "region": [-12, -12, 12, 12],
                                   "targets": [[10, 0], [-10, 0]]},
                       "injects": [{"at": 0.5, "do": "comms_blackout"}],
                       "duration_s": 60})
    res = r.run()
    # local-only fallback must still cover both targets under a full mesh blackout
    assert res["targets_found"] == res["targets_total"]
    assert r.comms.mesh_degraded          # blackout fired
    assert res["comms"]["delivery_rate"] <= 1.0


def test_delivery_rate_bounded():
    r, rt = _runner(d={"name": "t", "team": ["quad x2"],
                       "mission": {"type": "area_search", "region": [-12, -12, 12, 12],
                                   "targets": [[10, 0], [-10, 0]]},
                       "injects": [{"at": 1, "do": "contested_task", "at_point": [0, 0, 2]}],
                       "duration_s": 60})
    res = r.run()
    assert 0.0 <= res["comms"]["delivery_rate"] <= 1.0
    assert res["comms"]["attempted"] >= res["comms"]["delivered"]


def test_teammate_loss_reassigns_and_completes():
    # one agent fails early; survivors must still find all targets
    r, rt = _runner(d={"name": "t", "team": ["quad x3"],
                       "mission": {"type": "area_search", "region": [-12, -12, 12, 12],
                                   "targets": [[10, 0], [-10, 0]]},
                       "injects": [{"at": 2, "do": "agent_failure", "agent": "quad_0"}],
                       "duration_s": 80})
    res = r.run()
    assert not r.world.agents["quad_0"].alive
    assert res["targets_found"] == res["targets_total"]


def test_battery_emergency_triggers_rtl():
    # battery emergency -> deterministic RTL reflex (the realistic lost-agent trigger;
    # pure VIO drift is too slow to cross CONF_RTL, so denial alone rarely RTLs).
    r, rt = _runner(d={"name": "t", "team": ["quad x2"],
                       "mission": {"type": "area_search", "region": [-12, -12, 12, 12],
                                   "targets": [[10, 0], [-10, 0]]},
                       "injects": [{"at": 1, "do": "battery_emergency", "agent": "quad_0",
                                    "home": [-12, 0, 0]}],
                       "duration_s": 60})
    r.run()
    assert rt.metrics["rtl_events"] >= 1
    # the RTL'd agent heads home, not to a task
    assert r.world.agents["quad_0"].pos[0] < 0


def test_isolation_fallback_counts():
    r, rt = _runner(d={"name": "t", "team": ["rover x2"],
                       "mission": {"type": "area_search", "region": [-12, -12, 12, 12],
                                   "targets": [[10, 10], [-10, -10]]},
                       "injects": [{"at": 0.5, "do": "comms_blackout"}],
                       "duration_s": 60})
    r.run()
    # blackout -> agents isolated -> deterministic fallback path exercised
    assert rt.metrics["isolated_agent_ticks"] > 0
