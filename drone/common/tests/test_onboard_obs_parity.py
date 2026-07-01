"""Observation parity: the on-device build_observation() reproduces the sim's
TeamWorld.observe() for the fields that drive control.

Together with test_l5_parity (same obs -> same action, same directives), this
closes the loop: given equivalent sensing, the on-device runtime makes the same
decisions that were validated to L5. The one intentional difference is
min_clearance (sim uses true obstacle-box surface distance; device uses the
nearest scan return + teammate surface distance) — asserted close, not equal.
"""
import numpy as np

from eco.drone.sim.team_world import TeamWorld, KinematicWorld, Box
from eco.drone.common.onboard_l5 import build_observation, Pose, Peer
from eco.drone.common.l5_core import Agent as CoreAgent, get_class as core_get_class


def _build_world():
    obstacles = [
        Box(np.array([5.0, 0.0, 1.0], np.float32), np.array([0.8, 1.5, 2.2], np.float32)),
        Box(np.array([-6.0, 1.0, 1.0], np.float32), np.array([0.8, 0.3, 4.0], np.float32)),
        Box(np.array([2.0, 4.0, 1.0], np.float32), np.array([1.0, 1.0, 1.0], np.float32)),
    ]
    w = TeamWorld(backend=KinematicWorld(obstacles), dt=0.1)
    w.add_agent("quad_0", "quad", pos=[0.0, 0.0, 5.0], yaw=0.3, goal=[10.0, 2.0, 5.0])
    w.add_agent("quad_1", "quad", pos=[1.0, -2.0, 5.0], yaw=-0.4, goal=[9.0, 1.0, 5.0])
    w.add_agent("rover_0", "rover", pos=[0.5, 1.0, 0.0], yaw=1.1, goal=[8.0, 3.0, 0.0])
    return w


def test_build_observation_matches_sim_observe():
    w = _build_world()
    agents = list(w.agents.values())

    for me in agents:
        sim_obs = w.observe(me)

        # Feed the device builder the SAME ground truth the sim used:
        pose = Pose(
            pos_enu=me.pos.copy(), yaw_rad=float(me.yaw), vel_enu=me.vel.copy(),
            loc_confidence=1.0, sensor_ok=True,
        )
        scan, _ = w._scan(me, w.backend.obstacles() + w._moving_obstacles(me))
        peers = [
            Peer(id=o.id, pos_enu=o.pos.copy(), vel_enu=o.vel.copy(),
                 vclass_name=o.vclass.name, confidence=1.0, alive=True)
            for o in agents if o.id != me.id
        ]
        core_agent = CoreAgent(id=me.id, vclass=core_get_class(me.vclass.name),
                               pos=me.pos.copy(), yaw=float(me.yaw),
                               vel=me.vel.copy(), goal=me.goal.copy())
        dev_obs = build_observation(core_agent, pose, scan, peers)

        # Fields that must match exactly (they drive the controller identically):
        np.testing.assert_allclose(dev_obs.body_target, sim_obs.body_target, atol=1e-5)
        assert abs(dev_obs.goal_dist - sim_obs.goal_dist) < 1e-4
        np.testing.assert_allclose(dev_obs.scan, sim_obs.scan, atol=1e-5)

        # Neighbours: same ids, same body-frame relative positions
        sim_n = {n[0]: n[1] for n in sim_obs.neighbors}
        dev_n = {n[0]: n[1] for n in dev_obs.neighbors}
        assert set(sim_n) == set(dev_n)
        for nid in sim_n:
            np.testing.assert_allclose(dev_n[nid], sim_n[nid], atol=1e-5)

        # min_clearance: documented approximation — device uses scan+teammate, sim
        # uses box surface distance. Require the device value be a safe lower/near
        # bound (never wildly larger than the true clearance).
        assert dev_obs.min_clearance <= sim_obs.min_clearance + 1.0
