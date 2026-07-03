"""Tests for the multi-agent rover IPPO trainer (train_ma_rover.py).

All tests run on CPU, no GPU required, no ONNX export.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

# guard: skip if torch unavailable
torch = pytest.importorskip("torch")

from eco.drone.training.train_ma_rover import (
    MultiAgentRoverEnv, build_ac, W, TEAMMATE_R, SAFE_SEP,
    COMMS_GOAL_NOISE_STD, GPS_DRIFT_RATE, DT,
)
from eco.drone.training.rover_contract import R_STATE_DIM, R_ACTION_DIM, LIDAR_MAX


DEV = torch.device("cpu")
N = 4    # env-instances (small for tests)
M = 4    # agents per env


def _make_env(stage=0, n=N, m=M, **kw):
    return MultiAgentRoverEnv(torch, n, m, DEV, seed=42, stage=stage, **kw)


def _make_w(extra=None):
    import argparse
    ap = argparse.Namespace(
        k_prog=1.5, k_time=0.02, k_jerk=0.002, k_coll=25.0, k_goal=40.0,
        k_stall=0.25, k_timeout=8.0, stall_speed=0.2, k_clear=0.5,
        clear_margin=0.5, k_cov=5.0, k_deconf=0.3, k_tcoll=15.0,
    )
    if extra:
        for k, v in extra.items():
            setattr(ap, k, v)
    return W(ap)


# --------------------------------------------------------------------------- basic shapes
def test_obs_shape():
    env = _make_env()
    o = env.obs()
    assert o.shape == (N * M, R_STATE_DIM)


def test_obs_finite():
    env = _make_env()
    o = env.obs()
    assert torch.isfinite(o).all(), "obs contains NaN/Inf"


def test_step_reward_shape():
    env = _make_env()
    w = _make_w()
    act = torch.zeros(N * M, R_ACTION_DIM)
    rew, done_env, info = env.step(act, w)
    assert rew.shape == (N * M,)
    assert done_env.shape == (N,)


# --------------------------------------------------------------------------- teammate lidar
def test_teammates_visible_in_lidar():
    """Place M-1 teammates very close; their scan values should be < LIDAR_MAX."""
    env = _make_env(n=1, m=4)
    # Pack all agents at nearly the same spot so they appear in each other's lidar
    env.x[:] = torch.tensor([0.0, 0.5, -0.5, 0.0])
    env.y[:] = torch.tensor([0.0, 0.0, 0.0, 0.5])
    env.yaw[:] = 0.0
    scan = env._lidar_scan_agent(0, torch.zeros(1, M, 2))   # (1, L)
    # At least some rays should be blocked by the 3 nearby teammates
    assert (scan < LIDAR_MAX - 0.1).any(), "teammates not detected in lidar"


def test_lidar_clear_field():
    """No obstacles, no teammates → all rays at LIDAR_MAX."""
    env = _make_env(n=1, m=2, stage=0)
    # Spread agents far apart so they don't occlude each other
    env.x[0] = 0.0; env.y[0] = 0.0
    env.x[1] = 50.0; env.y[1] = 0.0
    env.bmask[:] = 0.0
    scan = env._lidar_scan_agent(0, torch.zeros(1, 2, 2))
    assert (scan >= LIDAR_MAX - 1e-3).all(), "unexpected obstacle in clear field"


# --------------------------------------------------------------------------- goal assignment
def test_goals_assigned_nearest():
    """Two agents, two goals: each should take the nearest one."""
    env = _make_env(n=1, m=2)
    # Agent 0 near (10, 0), agent 1 near (-10, 0)
    env.x[0] = 9.0; env.y[0] = 0.0
    env.x[1] = -9.0; env.y[1] = 0.0
    env.gx[0, 0] = 10.0; env.gy[0, 0] = 0.0   # goal A near agent 0
    env.gx[0, 1] = -10.0; env.gy[0, 1] = 0.0  # goal B near agent 1
    env.goal_found[:] = False
    ag_gx, ag_gy, assigned, _ = env._assign_goals()
    assert assigned[0, 0].item() == 0, "agent 0 should claim goal A"
    assert assigned[0, 1].item() == 1, "agent 1 should claim goal B"


def test_goal_found_prevents_reassignment():
    """Once goal 0 is found, agents should claim goal 1."""
    env = _make_env(n=1, m=2)
    env.gx[0, 0] = 10.0; env.gy[0, 0] = 0.0
    env.gx[0, 1] = -10.0; env.gy[0, 1] = 0.0
    env.goal_found[0, 0] = True   # goal A already found
    _, _, assigned, _ = env._assign_goals()
    # Both agents should now target goal 1 (only unclaimed)
    assert assigned[0, 0].item() == 1
    assert assigned[0, 1].item() == 1


# --------------------------------------------------------------------------- team reward
def test_teammate_collision_penalty():
    """Stack two agents on top of each other → teammate collision penalty fires."""
    env = _make_env(n=1, m=2, stage=0)
    w = _make_w()
    env.x[0] = 0.0; env.y[0] = 0.0
    env.x[1] = 0.0; env.y[1] = 0.0  # same position → surface dist < 0
    act = torch.zeros(2, R_ACTION_DIM)
    rew, _, info = env.step(act, w)
    assert info["teammate_collisions"].sum() > 0, "stacked agents should produce teammate collision"


def test_coverage_bonus_on_goal_reach():
    """Drive an agent exactly onto its goal → reward should include coverage bonus."""
    env = _make_env(n=1, m=2, stage=0)
    w = _make_w({"k_goal": 40.0, "k_cov": 5.0})
    env.bmask[:] = 0.0
    # Place agent 0 on its goal
    env.gx[0, 0] = 0.0; env.gy[0, 0] = 0.0
    env.gx[0, 1] = 100.0; env.gy[0, 1] = 0.0  # agent 1's goal far away
    env.x[0] = 0.0; env.y[0] = 0.0   # agent 0 already at goal 0
    env.x[1] = 50.0; env.y[1] = 0.0  # agent 1 far away
    env.yaw[:] = 0.0; env.v[:] = 0.0; env.w[:] = 0.0
    act = torch.zeros(2, R_ACTION_DIM)
    rew, _, info = env.step(act, w)
    # Agent 0 reached its goal → reward should be high (goal + cov bonus)
    assert rew[0] > 30.0, f"expected high reward on goal reach, got {rew[0].item():.2f}"


# --------------------------------------------------------------------------- comms/GPS stages
def test_stage3_comms_noise_applied():
    """Stage 3: comms noise should be non-zero after first step."""
    env = _make_env(stage=3)
    w = _make_w()
    act = torch.zeros(N * M, R_ACTION_DIM)
    env.step(act, w)
    # comms_noise should have been resampled
    assert env.comms_noise.abs().sum() > 0.0, "comms noise not applied at stage 3"


def test_stage4_gps_drift_accumulates():
    """Stage 4: repeated steps should grow drift magnitude monotonically in expectation."""
    env = _make_env(stage=4)
    w = _make_w()
    act = torch.zeros(N * M, R_ACTION_DIM)
    drift_before = (env.drift_x ** 2 + env.drift_y ** 2).sum().item()
    for _ in range(20):
        env.step(act, w)
    drift_after = (env.drift_x ** 2 + env.drift_y ** 2).sum().item()
    assert drift_after > drift_before, "GPS drift did not accumulate at stage 4"


# --------------------------------------------------------------------------- model
def test_ac_forward_shape():
    ac = build_ac(torch, hidden=64).to(DEV)
    obs = torch.zeros(N * M, R_STATE_DIM)
    h_a = torch.zeros(1, N * M, 64)
    h_v = torch.zeros(1, N * M, 64)
    mu, val, h_a2, h_v2 = ac.step(obs, h_a, h_v)
    assert mu.shape == (N * M, R_ACTION_DIM)
    assert val.shape == (N * M,)
    assert h_a2.shape == (1, N * M, 64)


def test_ac_grad_flows():
    """A single PPO update should move parameters."""
    ac = build_ac(torch, hidden=64).to(DEV)
    opt = torch.optim.Adam(ac.parameters(), lr=1e-3)
    obs = torch.randn(N * M, R_STATE_DIM)
    h_a = torch.zeros(1, N * M, 64)
    h_v = torch.zeros(1, N * M, 64)
    mu, val, _, _ = ac.step(obs, h_a, h_v)
    loss = mu.pow(2).mean() + val.pow(2).mean()
    before = ac.log_std.data.clone()
    opt.zero_grad(); loss.backward(); opt.step()
    assert not torch.allclose(ac.log_std.data, before), "parameters did not update"
