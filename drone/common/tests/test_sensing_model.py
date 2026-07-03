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


def test_realistic_sensing_achieves_L5():
    """Regression lock: after Stages 1+3, the suite is L5 under BOTH sensing models
    (with the RuleBasedSmart advisor) — 0 interventions, 0 collisions, 100% success,
    no attributed failures. This is the 'L5 under realistic sensing' milestone."""
    from eco.drone.sim.scorecard import score_suite
    from eco.drone.sim.smart_layer import RuleBasedSmart
    import pathlib
    scn = str(pathlib.Path(__file__).resolve().parents[2] / "sim" / "scenarios")

    for mode in ("ideal", "realistic"):
        rep = score_suite(scn, use_runtime=True, smart=RuleBasedSmart(), sensing=mode)
        assert rep["autonomy_level"] == 5, f"{mode}: expected L5, got L{rep['autonomy_level']}"
        assert all(len(s["failures"]) == 0 for s in rep["scenarios"]), f"{mode}: unexpected failures"


def test_scorecard_attribution_is_wellformed():
    """The per-agent attribution plumbing produces well-formed records. Forced by
    running the raw reactive layer (no smart advisor) under realistic sensing, which
    is the pre-Stage-1 baseline and does fail — exercising the attribution path."""
    from eco.drone.sim.scorecard import score_suite
    import pathlib
    scn = str(pathlib.Path(__file__).resolve().parents[2] / "sim" / "scenarios")

    raw = score_suite(scn, use_runtime=True, smart=None, sensing="realistic")
    all_failures = [f for s in raw["scenarios"] for f in s["failures"]]
    assert all_failures, "expected attributed failures for the raw (no-advisor) baseline"
    for t, cause, aid, vclass, detail in all_failures:
        assert cause in ("collision", "near_miss", "stall", "lost", "mission_timeout")
        assert vclass in ("quadcopter", "rover")
        assert isinstance(aid, str) and aid
