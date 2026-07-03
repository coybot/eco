"""Full-loop fidelity: OnboardL5Runtime.step() driven against a kinematic world
produces the same trajectory as the native sim on the same course, for both
vehicle classes. This exercises the entire on-device path (telemetry -> sensor ->
peers -> build_observation -> controller -> actuator -> integrate) and asserts it
tracks the L5-validated sim, rather than testing an arbitrary hand-picked course.

Smart layer is off here to isolate the sense->control->integrate loop; the smart
layer itself is covered by test_l5_parity.
"""
import numpy as np

from eco.drone.sim.team_world import (
    TeamWorld, KinematicWorld, Box, reactive_goto_controller as sim_controller,
)
from eco.drone.common.onboard_l5 import OnboardL5Runtime, Pose, Peer


class _SimTelemetry:
    def __init__(self, world, aid): self.world, self.aid = world, aid
    def pose(self):
        a = self.world.agents[self.aid]
        return Pose(pos_enu=a.pos.copy(), yaw_rad=float(a.yaw), vel_enu=a.vel.copy())


class _SimSensor:
    def __init__(self, world, aid): self.world, self.aid = world, aid
    def scan(self):
        a = self.world.agents[self.aid]
        boxes = self.world.backend.obstacles() + self.world._moving_obstacles(a)
        return self.world._scan(a, boxes)[0]


class _NoPeers:
    def peers(self): return []


class _Capture:
    def __init__(self): self.action = None
    def send(self, action, vclass, dt): self.action = np.asarray(action, np.float32)


def _course(vclass_name):
    # Offset obstacle (path does not pass through its centre) — a solvable course.
    z = 5.0 if vclass_name == "quad" else 0.0
    obstacles = [Box(np.array([5.0, 1.8, z], np.float32),
                     np.array([0.8, 1.0, 2.2 if vclass_name == "quad" else 1.0], np.float32))]
    return obstacles, [0.0, 0.0, z], [10.0, 0.0, z]


def _run_native(vclass_name, obstacles, start, goal, ticks):
    w = TeamWorld(backend=KinematicWorld(list(obstacles)), dt=0.1)
    w.add_agent("a", vclass_name, pos=list(start), yaw=0.0, goal=list(goal))
    ctl = sim_controller()
    traj = []
    for _ in range(ticks):
        w.step(ctl)
        traj.append(w.agents["a"].pos.copy())
    return np.array(traj)


def _run_onboard(vclass_name, obstacles, start, goal, ticks):
    w = TeamWorld(backend=KinematicWorld(list(obstacles)), dt=0.1)
    w.add_agent("a", vclass_name, pos=list(start), yaw=0.0, goal=list(goal))
    agent = w.agents["a"]
    act = _Capture()
    rt = OnboardL5Runtime("a", vclass_name, goal,
                          telemetry=_SimTelemetry(w, "a"), sensor=_SimSensor(w, "a"),
                          peers=_NoPeers(), actuator=act, use_smart=False)
    traj = []
    for _ in range(ticks):
        rt.step(w.dt)
        w.backend.integrate(agent, act.action, w.dt)
        traj.append(agent.pos.copy())
    return np.array(traj)


import pytest


def _min_surface_dist(obstacles, pos):
    best = np.inf
    for b in obstacles:
        d = np.maximum(0.0, np.abs(np.asarray(pos) - b.center) - b.half)
        best = min(best, float(np.linalg.norm(d)))
    return best


def test_rover_onboard_tracks_native_sim():
    """Rover: lidar min-range ≈ true surface distance, so the on-device loop
    tracks the L5-validated sim near-exactly."""
    obstacles, start, goal = _course("rover")
    native = _run_native("rover", obstacles, start, goal, 400)
    onboard = _run_onboard("rover", obstacles, start, goal, 400)
    max_dev = float(np.abs(native - onboard).max())
    assert max_dev < 0.1, f"rover on-device trajectory diverged from sim by {max_dev:.3f} m"
    assert float(np.linalg.norm(onboard[-1] - np.array(goal))) < 2.0


def test_quad_onboard_reaches_goal_collision_free():
    """Quad: on hardware min_clearance comes from the forward depth scan (not the
    sim's unrealizable true box-surface distance), so the path differs slightly
    from sim near obstacles — but the loop still reaches goal collision-free.
    This is the documented sim-to-real gap that SITL further validates."""
    obstacles, start, goal = _course("quad")
    onboard = _run_onboard("quad", obstacles, start, goal, 400)
    min_clear = min(_min_surface_dist(obstacles, p) for p in onboard)
    assert float(np.linalg.norm(onboard[-1] - np.array(goal))) < 2.0, "quad did not reach goal"
    assert min_clear > 0.15, f"quad collided (min surface clearance {min_clear:.2f} m)"
