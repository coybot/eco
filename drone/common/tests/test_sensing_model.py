"""Stage 0: the sensing model is honest and attribution works.

- 'ideal' sensing = true perpendicular surface distance (the legacy L5 oracle).
- 'realistic' sensing = nearest sensor return only (what onboard_l5 actually has).
- The scorecard attributes each failure to a specific agent + cause.
"""
import numpy as np

from eco.drone.sim.team_world import TeamWorld, KinematicWorld, Box, Sensor


def test_realistic_clearance_is_sensor_derived_not_oracle():
    obstacles = [Box(np.array([3.0, 1.6, 0.0], np.float32),
                     np.array([0.8, 1.0, 1.0], np.float32))]
    w_ideal = TeamWorld(KinematicWorld(list(obstacles)), sensing="ideal")
    w_real = TeamWorld(KinematicWorld(list(obstacles)), sensing="realistic")
    for w in (w_ideal, w_real):
        w.add_agent("r", "rover", pos=[0.0, 0.0, 0.0], yaw=0.0, goal=[10.0, 0.0, 0.0])
    a_i = w_ideal.agents["r"]; a_r = w_real.agents["r"]
    obs_i = w_ideal.observe(a_i)
    obs_r = w_real.observe(a_r)
    # realistic clearance == nearest scan return; ideal == true surface distance
    assert abs(obs_r.min_clearance - float(obs_r.scan.min())) < 1e-6
    # the two models disagree (that IS the sim-to-real gap Stages 1-3 must close)
    assert obs_i.min_clearance != obs_r.min_clearance


def test_scorecard_attributes_failures_by_agent():
    """A realistic-sensing suite run yields per-agent failure records; ideal is clean."""
    from eco.drone.sim.scorecard import score_suite
    from eco.drone.sim.smart_layer import RuleBasedSmart
    import pathlib
    scn = str(pathlib.Path(__file__).resolve().parents[2] / "sim" / "scenarios")

    ideal = score_suite(scn, use_runtime=True, smart=RuleBasedSmart(), sensing="ideal")
    assert ideal["autonomy_level"] == 5
    assert all(len(s["failures"]) == 0 for s in ideal["scenarios"])

    real = score_suite(scn, use_runtime=True, smart=RuleBasedSmart(), sensing="realistic")
    assert real["autonomy_level"] < 5
    all_failures = [f for s in real["scenarios"] for f in s["failures"]]
    assert all_failures, "expected attributed failures under realistic sensing"
    # every record is (t, cause, agent_id, vclass, detail)
    for t, cause, aid, vclass, detail in all_failures:
        assert cause in ("collision", "near_miss", "stall", "lost", "mission_timeout")
        assert vclass in ("quadcopter", "rover")
        assert isinstance(aid, str) and aid
