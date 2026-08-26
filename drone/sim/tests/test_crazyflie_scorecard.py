"""Indoor scenario plumbing and the Crazyflie scorecard floor.

The scorecard assertion is deliberately a **floor**, not L5. Pinning an aspirational level
before it is achieved gives a permanently red test, which destroys the signal it was supposed
to carry. The committed baseline JSON records the actual result; tighten this when the number
actually improves.
"""
import json
from pathlib import Path

import pytest

import vehicle_class as vc
from scenario import Scenario, ScenarioRunner, _room_boxes, _default_start_alt
from scorecard import score_suite, get_thresholds, DEFAULT_THRESHOLDS, INDOOR_THRESHOLDS
from smart_layer import RuleBasedSmart

INDOOR_DIR = Path(__file__).resolve().parents[1] / "scenarios_indoor"


# =========================================================== scenario plumbing
def test_default_start_alt_no_longer_keys_off_the_name():
    """The floor-spawn bug: start altitude was `2.0 if type.startswith("q") else 0.0`, so
    "crazyflie" spawned at z=0 — inside the floor — while "quad" got 2.0."""
    assert _default_start_alt("quadcopter") == 2.0
    assert _default_start_alt("rover") == 0.0          # ground vehicle, unchanged
    cf = _default_start_alt("crazyflie")
    assert cf > 0.0, "an aerial class must not spawn on the floor"
    assert cf <= 0.5 * vc.CRAZYFLIE.ceiling_m + 1e-9   # and not at 91% of its ceiling
    assert _default_start_alt("not_a_real_class") == 0.0


def test_room_expands_to_walls_and_ceiling():
    boxes = _room_boxes({"size": [4.0, 3.0], "height": 2.4, "wall_thickness": 0.1,
                         "ceiling": True})
    assert len(boxes) == 5                              # 4 walls + ceiling
    ceiling = boxes[-1]
    assert ceiling[2] == pytest.approx(2.45)            # sits just above room height
    without = _room_boxes({"size": [4.0, 3.0], "height": 2.4, "ceiling": False})
    assert len(without) == 4


def test_room_ceiling_is_visible_to_the_up_facing_tof():
    """Without a ceiling box, "indoors" is an open field with walls and ray 49 is meaningless."""
    sc = Scenario.from_dict({
        "name": "t", "room": {"size": [4.0, 3.0], "height": 2.4, "ceiling": True},
        "team": [{"id": "cf_0", "type": "crazyflie", "pos": [0, 0, 1.0]}],
    })
    r = ScenarioRunner(sc)
    scan, _ = r.world._scan(r.world.agents["cf_0"], r.world.backend.obstacles())
    assert scan[49] < 2.0, "up-facing ToF should see the ceiling"


def test_scenario_carries_its_own_sensing_but_explicit_args_win():
    """Precedence: explicit caller > scenario YAML > default.

    If the YAML won unconditionally, `--sensing ideal` would silently do nothing on these
    scenarios and an ablation run would report the impaired number as if it were the oracle.
    """
    sc = Scenario.from_yaml(str(INDOOR_DIR / "cf_hover_room.yaml"))
    assert sc.sensing["mode"] == "realistic"
    assert ScenarioRunner(sc).world.sensing == "realistic"          # YAML applies
    assert ScenarioRunner(sc, sensing="ideal").world.sensing == "ideal"   # caller overrides
    assert ScenarioRunner(sc).world.impairment_model is not None
    assert ScenarioRunner(sc, impairment="none").world.impairment_model is None


def test_goal_tol_and_find_radius_thread_through():
    sc = Scenario.from_yaml(str(INDOOR_DIR / "cf_flow_drift.yaml"))
    assert sc.goal_tol_m == 0.25
    assert sc.find_radius_m == 0.4
    r = ScenarioRunner(sc)
    assert r.mission.goal_tol == 0.25
    assert r.mission.find_radius == 0.4


def test_outdoor_scenarios_keep_default_tolerances():
    """No `goal_tol_m` / `thresholds` key must mean exactly the historical behaviour."""
    outdoor = Path(__file__).resolve().parents[1] / "scenarios" / "gauntlet_alpha.yaml"
    sc = Scenario.from_yaml(str(outdoor))
    assert sc.goal_tol_m == 1.5
    assert sc.find_radius_m is None
    assert sc.thresholds == ""
    assert get_thresholds(sc.thresholds) is DEFAULT_THRESHOLDS
    assert ScenarioRunner(sc).mission.find_radius == 2.0     # Mission.FIND_RADIUS


def test_indoor_thresholds_are_actually_stricter():
    d, i = DEFAULT_THRESHOLDS, INDOOR_THRESHOLDS
    assert i.near_miss_pad_m < d.near_miss_pad_m
    assert i.stall_eps_m < d.stall_eps_m
    assert i.lost_err_m < d.lost_err_m
    # a 5 m "lost" threshold cannot detect anything inside an 8x6 m apartment
    assert i.lost_err_m < 1.0


def test_all_indoor_scenarios_load_and_use_the_crazyflie():
    files = sorted(INDOOR_DIR.glob("*.yaml"))
    assert len(files) == 8
    for f in files:
        sc = Scenario.from_yaml(str(f))
        assert sc.team, f"{f.name}: empty roster"
        for spec in sc.team:
            assert vc.get_class(spec["type"]).name == "crazyflie"
        assert sc.thresholds == "indoor", f"{f.name}: should use indoor thresholds"
        # every agent must start inside the room and below its ceiling
        for spec in sc.team:
            assert 0.0 < spec["pos"][2] < vc.CRAZYFLIE.ceiling_m, f"{f.name}: bad start alt"


# =========================================================== the deliverable
def test_crazyflie_suite_meets_its_floor():
    """Runs the indoor suite end to end. Floor, not aspiration — see module docstring."""
    rep = score_suite(str(INDOOR_DIR), use_runtime=True, smart=RuleBasedSmart(),
                      impair_seed=0)
    assert rep["n_scenarios"] == 8
    assert rep["mission_success_rate"] >= 0.5, rep["rationale"]
    assert rep["autonomy_level"] >= 3, rep["rationale"]
    for s in rep["scenarios"]:
        assert s["min_separation"] is None or s["min_separation"] >= 0.0
        for _t, cause, _aid, vclass, _detail in s["failures"]:
            assert cause in ("collision", "near_miss", "stall", "lost", "mission_timeout")
            assert vclass == "crazyflie"


def test_committed_baseline_still_reproduces():
    """The committed scorecard is a reproducibility claim; a silent drift is a real change."""
    baseline = Path(__file__).resolve().parents[1] / "scorecard_crazyflie.json"
    if not baseline.exists():
        pytest.skip("no committed crazyflie baseline yet")
    want = json.loads(baseline.read_text())
    rep = score_suite(str(INDOOR_DIR), use_runtime=True, smart=RuleBasedSmart(),
                      impair_seed=0)
    assert rep["autonomy_level"] == want["autonomy_level"]
    assert rep["total_interventions"] == want["total_interventions"]
    assert rep["mission_success_rate"] == want["mission_success_rate"]
