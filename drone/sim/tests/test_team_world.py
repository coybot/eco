"""TeamWorld multi-agent foundation tests (Phase 0)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from team_world import (
    TeamWorld, KinematicWorld, Box, Agent, reactive_goto_controller, lidar_ray_angles,
)
from vehicle_class import get_class, Kinematics


def _ring_world(n_each=3, R=8.0, obstacle=True):
    static = ([Box(np.array([0, 0, 1.0], np.float32), np.array([0.6, 0.6, 3.0], np.float32))]
              if obstacle else [])
    w = TeamWorld(KinematicWorld(static_obstacles=static), dt=0.1)
    specs = []
    for i in range(n_each):
        specs.append((f"quad_{i}", "quad"))
        specs.append((f"rover_{i}", "rover"))
    m = len(specs)
    for i, (aid, typ) in enumerate(specs):
        th = 2 * math.pi * i / m
        z = 2.0 if typ.startswith("q") else 0.0
        pos = [R * math.cos(th), R * math.sin(th), z]
        goal = [-R * math.cos(th), -R * math.sin(th), z]
        yaw = math.atan2(goal[1] - pos[1], goal[0] - pos[0])
        w.add_agent(aid, typ, pos, yaw=yaw, goal=goal)
    return w


def test_mixed_roster_uses_descriptors():
    w = _ring_world(n_each=1, obstacle=False)
    q = w.agents["quad_0"]
    r = w.agents["rover_0"]
    assert q.vclass.kinematics is Kinematics.HOLONOMIC_3D
    assert r.vclass.kinematics is Kinematics.UNICYCLE_2D


def test_neighbors_sensed_in_body_frame():
    w = TeamWorld(KinematicWorld(), dt=0.1)
    a = w.add_agent("a", "quad", [0, 0, 2], yaw=0.0, goal=[10, 0, 2])
    w.add_agent("b", "quad", [3, 0, 2], yaw=0.0)             # directly ahead in world +x
    obs = w.observe(a)
    ids = [n[0] for n in obs.neighbors]
    assert "b" in ids
    rel = dict((n[0], n[1]) for n in obs.neighbors)["b"]
    # b is straight ahead -> body fwd ~3, left ~0
    assert rel[0] == pytest.approx(3.0, abs=1e-4)
    assert abs(rel[1]) < 1e-4


def test_teammate_is_a_sensed_obstacle():
    w = TeamWorld(KinematicWorld(), dt=0.1)
    a = w.add_agent("a", "rover", [0, 0, 0], yaw=0.0, goal=[10, 0, 0])
    w.add_agent("b", "rover", [2.0, 0, 0], yaw=0.0)          # 2m ahead
    obs = w.observe(a)
    # forward lidar ray should report the teammate well under SENSE_MAX
    fwd_idx = 0  # ray 0 == forward
    assert obs.scan[fwd_idx] < KinematicWorld.SENSE_MAX
    assert obs.min_clearance < 2.0


def test_unicycle_vs_holonomic_integration():
    w = TeamWorld(KinematicWorld(), dt=0.1)
    rover = w.add_agent("r", "rover", [0, 0, 0], yaw=0.0)
    quad = w.add_agent("q", "quad", [0, 0, 2], yaw=0.0)
    # rover: pure spin command must NOT translate
    w.backend.integrate(rover, np.array([0.0, 1.0], np.float32), 0.1)
    assert np.linalg.norm(rover.pos[:2]) < 1e-5
    # quad: vertical command changes altitude only
    z0 = quad.pos[2]
    w.backend.integrate(quad, np.array([0.0, 0.0, 1.0, 0.0], np.float32), 0.1)
    assert quad.pos[2] > z0
    assert np.linalg.norm(quad.pos[:2]) < 1e-5


def test_six_mixed_agents_reach_without_collision():
    """Phase-0 'done when': 5+ mixed agents avoid each other and reach goals."""
    w = _ring_world(n_each=3, obstacle=True)
    assert len(w.agents) == 6
    ctl = reactive_goto_controller()
    total_coll = 0
    reached_tick = None
    for k in range(1500):
        info = w.step(ctl)
        total_coll += info["collisions"]
        if w.all_reached(tol=1.2):
            reached_tick = k
            break
    assert reached_tick is not None, "team did not all reach goals"
    assert total_coll == 0, f"expected no collisions, got {total_coll}"


def test_lidar_angles_count():
    assert len(lidar_ray_angles()) == 72
    assert lidar_ray_angles()[0] == 0.0
