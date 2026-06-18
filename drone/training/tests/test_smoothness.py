"""Phase 0/1 verification: the expert is smooth (bounded accel & jerk) and converges,
the dataset is well-formed in the data_recorder schema, and windowing respects episodes.

These run without torch (training/export parity is checked separately in train.py when torch
is present). Run:  python -m pytest eco/drone/training/tests -q
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from eco.drone.training.contract import (
    STATE_DIM, ACTION_DIM, STATE_FIELDS, VEHICLE_QUAD, VEHICLE_ROVER, build_state, wrap_pi,
)
from eco.drone.training.expert import make_expert, ExpertLimits
from eco.drone.training import dataset as ds


DT = 0.1


def _simulate(vehicle, gx, gy, gz, limits, ticks=2000):
    """Closed-loop world-frame rollout, returning the commanded-velocity trace."""
    expert = make_expert(vehicle, limits)
    x = y = 0.0
    z = 2.0 if vehicle == VEHICLE_QUAD else 0.5
    yaw = 0.0
    vx_w = vy_w = vz_w = 0.0
    goal_alt = z + gz
    vels = []
    final_dist = None
    for _ in range(ticks):
        dx, dy, dz = gx - x, gy - y, goal_alt - z
        c, s = math.cos(-yaw), math.sin(-yaw)
        tf = c * dx - s * dy
        tl = s * dx + c * dy
        tu = dz
        dist = math.sqrt(tf * tf + tl * tl + tu * tu)
        final_dist = dist
        bvf = c * vx_w - s * vy_w
        bvl = s * vx_w + c * vy_w
        st = build_state((tf, tl, tu), (bvf, bvl, vz_w), expert._prev_yaw_rate,
                         altitude_m=z, vehicle=vehicle)
        a = expert.step(st, dt=DT)
        vels.append(np.array(a[:3], dtype=float))
        avx, avy, avz, ayr = (float(v) for v in a)
        cc, ss = math.cos(yaw), math.sin(yaw)
        vx_w = cc * avx - ss * avy
        vy_w = ss * avx + cc * avy
        vz_w = avz
        x += vx_w * DT
        y += vy_w * DT
        z += vz_w * DT
        yaw = wrap_pi(yaw + ayr * DT)
    return np.array(vels), final_dist


@pytest.mark.parametrize("vehicle", [VEHICLE_QUAD, VEHICLE_ROVER])
def test_bounded_accel_and_jerk(vehicle):
    lim = ExpertLimits()
    vels, _ = _simulate(vehicle, gx=10.0, gy=4.0, gz=(2.0 if vehicle == VEHICLE_QUAD else 0.0), limits=lim)
    accel = np.diff(vels, axis=0) / DT
    jerk = np.diff(accel, axis=0) / DT
    amax = np.linalg.norm(accel, axis=1).max()
    jmax = np.linalg.norm(jerk, axis=1).max()
    # small numerical tolerance on the per-tick limiters
    assert amax <= lim.max_accel + 1e-3, f"accel {amax} exceeds {lim.max_accel}"
    assert jmax <= lim.max_jerk + 1e-2, f"jerk {jmax} exceeds {lim.max_jerk}"


@pytest.mark.parametrize("vehicle", [VEHICLE_QUAD, VEHICLE_ROVER])
def test_converges_to_target(vehicle):
    lim = ExpertLimits()
    _, final_dist = _simulate(vehicle, gx=8.0, gy=-3.0,
                              gz=(1.5 if vehicle == VEHICLE_QUAD else 0.0), limits=lim)
    assert final_dist <= lim.reach_threshold + 0.3, f"did not converge: {final_dist:.2f} m"


def test_starts_from_rest():
    """First command must be small (no instantaneous jump from standstill)."""
    expert = make_expert(VEHICLE_QUAD)
    st = build_state((10.0, 0.0, 0.0), (0, 0, 0), 0.0, altitude_m=2.0,
                     vehicle=VEHICLE_QUAD)
    a = expert.step(st, dt=DT)
    speed = float(np.linalg.norm(a[:3]))
    assert speed <= expert.lim.max_accel * DT + 1e-6


def test_altitude_floor_blocks_descent():
    expert = make_expert(VEHICLE_QUAD, ExpertLimits())
    # target is below us, but we are under the altitude floor -> no descent commanded
    st = build_state((0.0, 0.0, -5.0), (0, 0, 0), 0.0,                     altitude_m=0.3, vehicle=VEHICLE_QUAD)
    a = expert.step(st, dt=DT)
    assert a[2] >= -1e-6, f"commanded descent below floor: vz={a[2]}"


def test_rover_is_planar():
    """Rover never commands vertical or lateral body velocity."""
    expert = make_expert(VEHICLE_ROVER)
    for _ in range(20):
        st = build_state((5.0, 5.0, 0.0), (0, 0, 0), expert._prev_yaw_rate,
                         altitude_m=0.5, vehicle=VEHICLE_ROVER)
        a = expert.step(st, dt=DT)
        assert abs(a[1]) < 1e-6 and abs(a[2]) < 1e-6


def test_dataset_schema_and_npz(tmp_path):
    summary = ds.generate(tmp_path, episodes=40, seed=1)
    assert summary["samples"] > 0

    d = np.load(tmp_path / "dataset.npz")
    assert d["X"].shape[1] == STATE_DIM
    assert d["Y"].shape[1] == ACTION_DIM
    assert d["X"].shape[0] == d["Y"].shape[0] == d["ep"].shape[0]
    assert np.isfinite(d["X"]).all() and np.isfinite(d["Y"]).all()
    # state-norm well-formed
    assert (d["state_std"] > 0).all()

    # plan.jsonl matches the data_recorder.record_plan schema
    lines = (tmp_path / "plan.jsonl").read_text().splitlines()
    assert lines
    row = json.loads(lines[0])
    for key in ("kind", "seq", "episode", "vehicle_type", "source",
                "target_xyz", "min_clearance_m", "altitude_m", "step"):
        assert key in row, f"missing {key}"
    assert row["kind"] == "plan" and row["source"] == "sim"
    assert set(row["step"]) == {"vx", "vy", "vz", "yaw_rate"}


def test_windowing_respects_episodes():
    from eco.drone.training.train import build_windows, SEQ_LEN
    # two episodes of length 3 and 4
    X = np.arange(7 * STATE_DIM, dtype=np.float32).reshape(7, STATE_DIM)
    Y = np.zeros((7, ACTION_DIM), dtype=np.float32)
    ep = np.array([0, 0, 0, 1, 1, 1, 1], dtype=np.int32)
    Xw, Yw = build_windows(X, Y, ep, SEQ_LEN)
    assert Xw.shape == (7, SEQ_LEN, STATE_DIM)
    # first frame of episode 1 (index 3) must be left-padded with itself only,
    # never bleeding episode 0's last frame
    win_ep1_start = Xw[3]
    assert np.allclose(win_ep1_start[-1], X[3])
    assert np.allclose(win_ep1_start[0], X[3])  # pad repeats the episode's first frame


def test_build_state_bearing():
    st = build_state((0.0, 1.0, 0.0))  # target directly to the left
    assert abs(st.yaw_err - math.pi / 2) < 1e-6
    assert st.vehicle == VEHICLE_QUAD
    assert len(st.to_vec()) == STATE_DIM
    assert list(STATE_FIELDS)[3] == "dist" and abs(st.dist - 1.0) < 1e-6


def _simulate_with_boxes(boxes, gx, gy, gz=0.0, z0=2.0, ticks=900):
    """Closed-loop 3D rollout against prism obstacles, ray-casting the depth grid each tick."""
    from eco.drone.training.world3d import depth_grid, min_dist_to_boxes
    expert = make_expert(VEHICLE_QUAD)
    x = y = 0.0
    z = z0
    yaw = math.atan2(gy, gx)
    vx_w = vy_w = vz_w = 0.0
    goal_alt = max(0.6, z0 + gz)
    min_clear_to_box = math.inf
    dist = math.inf
    for _ in range(ticks):
        dx, dy, dz = gx - x, gy - y, goal_alt - z
        c, s = math.cos(-yaw), math.sin(-yaw)
        tf, tl, tu = c * dx - s * dy, s * dx + c * dy, dz
        dist = math.sqrt(tf * tf + tl * tl + tu * tu)
        bvf = c * vx_w - s * vy_w
        bvl = s * vx_w + c * vy_w
        fan = depth_grid(x, y, z, yaw, boxes)
        st = build_state((tf, tl, tu), (bvf, bvl, vz_w), expert._prev_yaw_rate,
                         depth_fan=fan, altitude_m=z, vehicle=VEHICLE_QUAD)
        a = expert.step(st, dt=DT)
        avx, avy, avz, ayr = (float(v) for v in a)
        cc, ss = math.cos(yaw), math.sin(yaw)
        vx_w = cc * avx - ss * avy
        vy_w = ss * avx + cc * avy
        vz_w = avz
        x += vx_w * DT
        y += vy_w * DT
        z = max(0.2, z + vz_w * DT)
        yaw = wrap_pi(yaw + ayr * DT)
        min_clear_to_box = min(min_clear_to_box, min_dist_to_boxes(x, y, z, boxes))
        if dist <= expert.lim.reach_threshold:
            break
    return dist, min_clear_to_box


@pytest.mark.parametrize("boxes,gx,gy,gz", [
    ([], 10.0, 0.0, 0.0),                                              # free space
    ([(5, 0, 2, 1, 1, 1)], 10.0, 0.0, 0.0),                           # prism dead-ahead (around)
    ([(5, 0, 1.0, 1.0, 2.5, 1.0)], 10.0, 0.0, 0.0),                   # low wall (over)
    ([(4, 0.8, 2, 0.7, 0.8, 1.2), (7, -0.8, 2.5, 0.7, 0.8, 1.2)], 11.0, 0.0, 0.0),  # 3D slalom
])
def test_expert_avoids_obstacles(boxes, gx, gy, gz):
    """The expert reaches the goal AND never penetrates a prism (clears it by a margin)."""
    from eco.drone.training.world3d import Box3D
    bs = [Box3D(*b) for b in boxes]
    final_dist, min_clear = _simulate_with_boxes(bs, gx, gy, gz)
    assert final_dist <= 0.7, f"did not reach goal: {final_dist:.2f} m"
    if bs:
        assert min_clear > 0.2, f"collided with obstacle (min clearance {min_clear:.2f} m)"
