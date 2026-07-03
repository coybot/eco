"""Parity gate: the shipped on-device controller == the L5-validated sim controller.

`common/l5_core.py` is a vendored copy of the reactive potential-field controller in
`eco/drone/sim/team_world.py` (the one validated to L5 across the 16-scenario suite).
This test feeds identical observations to both and asserts identical actions, so any
edit that makes the flight controller diverge from the validated sim controller fails
CI instead of silently shipping unvalidated behaviour.
"""
import numpy as np
import pytest

from eco.drone.sim.team_world import (
    reactive_goto_controller as sim_controller,
    Observation as SimObservation,
    Agent as SimAgent,
)
from eco.drone.sim import vehicle_class as sim_vc

from eco.drone.common.l5_core import (
    reactive_goto_controller as core_controller,
    Observation as CoreObservation,
    Agent as CoreAgent,
)
from eco.drone.common import vehicle_class as core_vc


def _make_obs(cls, agent_id, body_target, goal_dist, neighbors, scan, min_clear):
    return cls(
        agent_id=agent_id,
        body_target=np.asarray(body_target, dtype=np.float32),
        goal_dist=float(goal_dist),
        neighbors=neighbors,
        scan=np.asarray(scan, dtype=np.float32),
        min_clearance=float(min_clear),
        loc_confidence=1.0,
        inbox=[],
    )


@pytest.mark.parametrize("vclass_name", ["quad", "rover"])
def test_controller_parity(vclass_name):
    rng = np.random.default_rng(1382)
    sim_ctl = sim_controller()
    core_ctl = core_controller()

    sim_class = sim_vc.get_class(vclass_name)
    core_class = core_vc.get_class(vclass_name)
    n_rays = 72 if vclass_name == "rover" else 45

    for _ in range(500):
        pos = rng.uniform(-10, 10, size=3).astype(np.float32)
        yaw = float(rng.uniform(-np.pi, np.pi))
        body_target = rng.uniform(-8, 8, size=3).astype(np.float32)
        goal_dist = float(rng.uniform(0, 12))
        scan = rng.uniform(0.2, 10.0, size=n_rays).astype(np.float32)
        min_clear = float(scan.min())

        # 0–3 neighbours, each with a body-frame relative position + velocity
        neighbors_raw = []
        for j in range(rng.integers(0, 4)):
            rel = rng.uniform(-4, 4, size=3).astype(np.float32)
            vel = rng.uniform(-2, 2, size=3).astype(np.float32)
            other = "rover" if rng.random() < 0.5 else "quad"
            neighbors_raw.append((rel, vel, other))

        sim_neigh = [(f"n{j}", rel, vel, sim_vc.get_class(o))
                     for j, (rel, vel, o) in enumerate(neighbors_raw)]
        core_neigh = [(f"n{j}", rel, vel, core_vc.get_class(o))
                      for j, (rel, vel, o) in enumerate(neighbors_raw)]

        sim_agent = SimAgent(id="a", vclass=sim_class, pos=pos.copy(), yaw=yaw)
        core_agent = CoreAgent(id="a", vclass=core_class, pos=pos.copy(), yaw=yaw)

        sim_obs = _make_obs(SimObservation, "a", body_target, goal_dist,
                            sim_neigh, scan, min_clear)
        core_obs = _make_obs(CoreObservation, "a", body_target, goal_dist,
                             core_neigh, scan, min_clear)

        a_sim = sim_ctl(sim_agent, sim_obs)
        a_core = core_ctl(core_agent, core_obs)

        np.testing.assert_allclose(
            a_core, a_sim, rtol=0, atol=0,
            err_msg=f"{vclass_name}: core controller diverged from sim controller",
        )


# --------------------------------------------------------------------------- smart layer
from eco.drone.sim.smart_layer import (
    RuleBasedSmart as SimSmart,
    WorldState as SimWorldState,
    AgentSnapshot as SimSnap,
)
from eco.drone.common.l5_smart import (
    RuleBasedSmart as CoreSmart,
    WorldState as CoreWorldState,
    AgentSnapshot as CoreSnap,
)


def _snap(cls, aid, pos, vel, goal, sensor_ok, conf, vclass, min_scan):
    return cls(id=aid, pos=list(pos), vel=list(vel),
               goal=(list(goal) if goal is not None else None),
               alive=True, sensor_ok=sensor_ok, confidence=conf,
               vclass=vclass, min_scan_dist=min_scan)


def test_smart_layer_parity():
    """RuleBasedSmart directives must be identical between sim and shipped copy."""
    rng = np.random.default_rng(77)
    sim_smart = SimSmart()
    core_smart = CoreSmart()

    # Drive both advisors through the same 120-tick scripted world sequence.
    agents = [
        ("quad_0", "quad"), ("quad_1", "quad"),
        ("rover_0", "rover"), ("rover_1", "rover"),
    ]
    goals = {aid: rng.uniform(-15, 15, size=3).astype(float) for aid, _ in agents}
    pos = {aid: rng.uniform(-15, 15, size=3).astype(float) for aid, _ in agents}

    for tick in range(120):
        sim_snaps, core_snaps = [], []
        for aid, vc in agents:
            # random-walk the world; occasionally blind / low-confidence / stalled
            pos[aid] = pos[aid] + rng.uniform(-0.3, 0.3, size=3)
            vel = rng.uniform(-1.5, 1.5, size=3)
            if rng.random() < 0.1:
                vel[:] = 0.0  # stall
            sensor_ok = rng.random() > 0.1
            conf = float(rng.uniform(0.2, 1.0))
            min_scan = float(rng.uniform(0.5, 10.0))
            args = (aid, pos[aid], vel, goals[aid], sensor_ok, conf, vc, min_scan)
            sim_snaps.append(_snap(SimSnap, *args))
            core_snaps.append(_snap(CoreSnap, *args))

        interventions = int(rng.integers(0, 8))
        sim_ws = SimWorldState(t=tick*0.1, agents=sim_snaps, targets=[],
                               interventions=interventions, comms_delivery_rate=0.9,
                               active_injects=[])
        core_ws = CoreWorldState(t=tick*0.1, agents=core_snaps, targets=[],
                                 interventions=interventions, comms_delivery_rate=0.9,
                                 active_injects=[])

        sim_dirs = sim_smart.tick(sim_ws)
        core_dirs = core_smart.tick(core_ws)

        sim_key = [(d.agent_id, d.kind, _norm(d.value)) for d in sim_dirs]
        core_key = [(d.agent_id, d.kind, _norm(d.value)) for d in core_dirs]
        assert sim_key == core_key, f"tick {tick}: smart-layer directives diverged"


def _norm(v):
    if isinstance(v, (list, tuple)):
        return tuple(round(float(x), 6) for x in v)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 6)
    return v
