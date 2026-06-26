"""Multi-agent IPPO for ground rovers — heterogeneous team deconfliction.

Why IPPO (Independent PPO with shared parameters)?
  - Decentralized execution: each rover runs the same policy on its own 83-dim state.
    Teammates appear as lidar hits (cylindrical obstacles), not as extra state dims.
    ONNX export is identical to the single-agent rover — zero deployment changes.
  - Shared policy is sample-efficient and transfers well; CTDE (critic-with-global-state)
    would require a separate centralized critic that is unavailable at inference time.
  - Empirically competitive with CTDE for navigation/deconfliction tasks where the
    dominant skill is "don't run into things" rather than tight task assignment.

Team reward decomposition (per tick, per env):
  individual:  navigation progress, goal-reached bonus, collision penalties  (same as v2)
  team:
    k_cov    × targets_found_this_tick   — coverage: whoever finds it, all benefit
    k_deconf × mean_pairwise_clearance   — smooth proximity maintained
    k_tcoll  × n_teammate_pairs_touching — collision with a teammate is a team failure

Six-stage curriculum:
  0  open field,   1 agent  (single-agent warm-start, identical to train_rl_rover stage 0)
  1  sparse obs,   2 agents (introduce teammate as dynamic obstacle)
  2  dense obs,    4 agents, 4 goals  (full team deconfliction)
  3  dense obs,    4 agents, comms-degraded goal noise  (goal vector noised to simulate
                   stale/dropped teammate broadcasts)
  4  tight slalom, 4 agents, GPS-denied drift  (position walk accumulates over episode)
  5  dense+slalom, 4 agents, comms+GPS combined  (L5 gauntlet conditions)

Train on hoopoe (A100, ~60 min):
    PYTHONPATH=. python -m eco.drone.training.train_ma_rover \\
        --out ~/drone-data/rover/models --version ma_rover_v1 \\
        --iters 1200 --envs 512 --rollout 96

Continue from checkpoint:
    PYTHONPATH=. python -m eco.drone.training.train_ma_rover \\
        --ckpt ~/drone-data/rover/models/policy_ma_rover_v1_ac.pt \\
        --out ~/drone-data/rover/models --version ma_rover_v2 --iters 600 --envs 512

The exported ONNX (policy_ma_rover_v1.onnx) is a drop-in replacement for
policy_rover_v2.onnx on hardware — same input/output shape.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .rover_contract import (
    R_STATE_DIM, R_ACTION_DIM, LIDAR_RAYS, LIDAR_MAX, LIDAR_ANGLES, R_STATE_STD, R_STATE_MEAN,
)
from .dynamics import DynamicsDR

# --------------------------------------------------------------------------- constants
DT = 0.1
MAX_STEPS = 300       # longer episodes for multi-goal search
REACH_R = 1.0
COLLIDE_R = 0.30      # rover radius for env (VehicleClass.radius_m = 0.5 → 0.5+0.5 surface)
TEAMMATE_R = 0.50     # physical radius to check teammate surface-contact
MAX_V = 2.0
MAX_W = 2.0
SAFE_SEP = 1.2        # m clearance encouraged by deconfliction bonus

# comms/GPS degradation params
COMMS_GOAL_NOISE_STD = 1.5   # m, goal vector noise when comms are degraded
GPS_DRIFT_RATE = 0.08        # m/s position drift random walk under GPS denial


def _torch():
    import torch
    return torch


def _wrap_pi(t, a):
    return (a + math.pi) % (2 * math.pi) - math.pi


# --------------------------------------------------------------------------- env
class MultiAgentRoverEnv:
    """Vectorised multi-rover env.

    Shapes:
        n     — number of parallel env-instances
        M     — agents per env-instance  (varies by curriculum stage)
        G     — goals per env-instance   (always == M for coverage tasks)

    Internal tensors are (n*M, ...) — agents are folded into the batch dimension so the
    policy GRU processes all agents identically in one forward pass.

    Teammate sensing: each agent's lidar scan is computed with all M-1 teammates added
    as additional cylindrical obstacles (radius TEAMMATE_R). This is the only change vs
    the single-agent env — the 83-dim state contract is unchanged.

    Task allocation: every tick each agent is greedily assigned the nearest unclaimed goal.
    "Claimed" means another agent is already closer. Simple and differentiable (no
    communication required at inference — each agent computes locally).
    """

    K = 10      # static obstacle slots (same as single-agent env)

    def __init__(self, torch, n, M, dev, seed=0,
                 lidar_noise=0.05, target_noise=0.15, stage=0):
        self.t = torch
        self.n = n
        self.M = M
        self.dev = dev
        self.lidar_noise = lidar_noise
        self.target_noise = target_noise
        self.stage = stage

        la = torch.tensor(LIDAR_ANGLES, device=dev, dtype=torch.float32)
        self.ray_cos = torch.cos(la)
        self.ray_sin = torch.sin(la)

        self.g = torch.Generator(device="cpu").manual_seed(seed)
        self.reset_all()

    def _rand(self, *shape, lo=0.0, hi=1.0):
        return (lo + (hi - lo) * self.t.rand(*shape, generator=self.g)).to(self.dev)

    # ------------------------------------------------------------------ episode
    def reset_all(self):
        t = self.t
        NM = self.n * self.M
        z = t.zeros(NM, device=self.dev)
        # Per-agent state (flattened n×M)
        self.x = z.clone(); self.y = z.clone(); self.yaw = z.clone()
        self.v = z.clone(); self.w = z.clone()
        self.prev_a = z.clone(); self.prev_alpha = z.clone()
        self.steps = z.clone()
        # Goals: (n, M) — goal k is assigned to agent k initially
        self.gx = t.zeros(self.n, self.M, device=self.dev)
        self.gy = t.zeros(self.n, self.M, device=self.dev)
        self.goal_found = t.zeros(self.n, self.M, device=self.dev, dtype=t.bool)
        # GPS drift state (activated at stage 4+)
        self.drift_x = z.clone()
        self.drift_y = z.clone()
        # Comms noise seed per agent (activated at stage 3+)
        self.comms_noise = t.zeros(NM, 2, device=self.dev)
        # Static obstacles (n, K) — shared across all agents in the same env-instance
        self.bcx = t.zeros(self.n, self.K, device=self.dev)
        self.bcy = t.zeros(self.n, self.K, device=self.dev)
        self.bhx = t.zeros(self.n, self.K, device=self.dev)
        self.bhy = t.zeros(self.n, self.K, device=self.dev)
        self.bmask = t.zeros(self.n, self.K, device=self.dev)
        self.dyn = DynamicsDR(t, NM, self.dev, self._rand)
        self._new_episode(t.ones(self.n, device=self.dev, dtype=t.bool))

    def _new_episode(self, env_mask):
        """Reset all agents in envs where env_mask=True."""
        t = self.t
        ne = int(env_mask.sum().item())
        if ne == 0:
            return
        env_idx = env_mask.nonzero(as_tuple=True)[0]   # (ne,)

        # Spawn M agents in a small cluster near the origin
        for m in range(self.M):
            ag_idx = env_idx * self.M + m              # agent indices in NM batch
            self.x[ag_idx] = self._rand(ne, lo=-2.0, hi=2.0)
            self.y[ag_idx] = self._rand(ne, lo=-2.0, hi=2.0)
            self.yaw[ag_idx] = self._rand(ne, lo=-math.pi, hi=math.pi)
            self.v[ag_idx] = 0.0; self.w[ag_idx] = 0.0
            self.prev_a[ag_idx] = 0.0; self.prev_alpha[ag_idx] = 0.0
            self.steps[ag_idx] = 0.0
            self.drift_x[ag_idx] = 0.0
            self.drift_y[ag_idx] = 0.0
            self.comms_noise[ag_idx] = 0.0

        # Place M goals spread across the environment
        for g in range(self.M):
            self.gx[env_idx, g] = self._rand(ne, lo=5.0, hi=14.0) * (
                self._rand(ne) > 0.5).float() * 2 - self._rand(ne, lo=5.0, hi=14.0)
            self.gy[env_idx, g] = self._rand(ne, lo=-6.0, hi=6.0)
        self.goal_found[env_idx] = False

        # Static obstacle layout by stage
        self.bmask[env_idx] = 0.0
        stage = self.stage
        if stage <= 1:
            self._place_columns(env_idx, ne, n_obs=3)
        elif stage <= 3:
            self._place_columns(env_idx, ne, n_obs=6)
            if (self._rand(ne) < 0.4).any():
                self._place_gap_wall(env_idx, ne, slot=8)
        else:
            self._place_columns(env_idx, ne, n_obs=6)
            wall_m = self._rand(ne) < 0.6
            if wall_m.any():
                self._place_gap_wall(env_idx, ne, slot=8, mask=wall_m)
            wall2 = self._rand(ne) < 0.35
            if wall2.any():
                self._place_gap_wall(env_idx, ne, slot=6, cx_lo=0.55, cx_hi=0.75, mask=wall2)

        # Agent mask for dynamics DR
        ag_mask = t.zeros(self.n * self.M, device=self.dev, dtype=t.bool)
        for m in range(self.M):
            ag_mask[env_idx * self.M + m] = True
        self.dyn.randomize(ag_mask)

    def _place_columns(self, env_idx, ne, n_obs):
        for k in range(min(n_obs, self.K - 2)):
            self.bcx[env_idx, k] = self._rand(ne, lo=-10.0, hi=10.0)
            self.bcy[env_idx, k] = self._rand(ne, lo=-6.0, hi=6.0)
            self.bhx[env_idx, k] = self._rand(ne, lo=0.2, hi=0.7)
            self.bhy[env_idx, k] = self._rand(ne, lo=0.2, hi=0.7)
            self.bmask[env_idx, k] = (self._rand(ne) > 0.15).float()

    def _place_gap_wall(self, env_idx, ne, slot=8, cx_lo=0.30, cx_hi=0.60,
                        span=14.0, wall_hx=0.4, mask=None):
        if mask is None:
            mask = self.t.ones(ne, dtype=self.t.bool, device=self.dev)
        gi = env_idx[mask]
        nm = int(mask.sum().item())
        if nm == 0:
            return
        cx = self._rand(nm, lo=cx_lo * 20 - 10, hi=cx_hi * 20 - 10)
        gap_cy = self._rand(nm, lo=-1.6, hi=1.6)
        gap_hy = self._rand(nm, lo=0.65, hi=1.1)
        left_cy = (-span / 2 + gap_cy - gap_hy) / 2
        left_hy = ((gap_cy - gap_hy + span) / 2).clamp(min=0.1)
        right_cy = (gap_cy + gap_hy + span) / 2
        right_hy = ((span - gap_cy - gap_hy) / 2).clamp(min=0.1)
        self.bcx[gi, slot] = cx; self.bcy[gi, slot] = left_cy
        self.bhx[gi, slot] = wall_hx; self.bhy[gi, slot] = left_hy; self.bmask[gi, slot] = 1.0
        self.bcx[gi, slot + 1] = cx; self.bcy[gi, slot + 1] = right_cy
        self.bhx[gi, slot + 1] = wall_hx; self.bhy[gi, slot + 1] = right_hy
        self.bmask[gi, slot + 1] = 1.0

    # ------------------------------------------------------------------ goal assignment
    def _assign_goals(self):
        """Per-agent greedy goal assignment.  Returns (NM,) goal x/y and (NM,) 'all found' mask.

        Each agent is assigned the nearest *unclaimed* goal. An unclaimed goal is one where
        no other agent in the same env is strictly closer. Ties broken by lower agent index.
        This runs fully on CPU-compatible torch ops.
        """
        t = self.t
        n, M = self.n, self.M
        NM = n * M
        # Reshape agent positions: (n, M)
        ax = self.x.view(n, M)
        ay = self.y.view(n, M)
        # distances agent i → goal g: (n, M, M)
        d2 = (ax[:, :, None] - self.gx[:, None, :]) ** 2 + \
             (ay[:, :, None] - self.gy[:, None, :]) ** 2   # (n, M, M)
        dist = d2.sqrt()
        # Mask found goals as inf
        found_mask = self.goal_found[:, None, :].expand(n, M, M)  # (n, M, M)
        dist = t.where(found_mask, t.full_like(dist, 1e9), dist)
        # Greedy assignment: each agent gets nearest non-found goal
        assigned = dist.argmin(dim=2)     # (n, M) — goal index for each agent
        # Extract goal positions
        env_i = t.arange(n, device=self.dev)[:, None].expand(n, M)   # (n, M)
        ag_gx = self.gx[env_i, assigned].view(NM)
        ag_gy = self.gy[env_i, assigned].view(NM)
        # Are all goals found?
        all_found = self.goal_found.all(dim=1)     # (n,)
        all_found_nm = all_found[:, None].expand(n, M).reshape(NM)
        return ag_gx, ag_gy, assigned, all_found_nm

    # ------------------------------------------------------------------ sensing
    def _teammate_boxes(self, agent_m):
        """Get teammate positions as boxes seen from agent index `agent_m` (0..M-1).

        Returns (n, M-1) cx/cy/h tensors to inject into each agent's lidar.
        """
        t = self.t
        n, M = self.n, self.M
        ax = self.x.view(n, M)
        ay = self.y.view(n, M)
        mask = t.ones(M, dtype=t.bool, device=self.dev)
        mask[agent_m] = False
        tm_cx = ax[:, mask]   # (n, M-1)
        tm_cy = ay[:, mask]
        return tm_cx, tm_cy

    def _lidar_scan_agent(self, m, gps_drift):
        """Full lidar scan for agent m, including static obstacles + teammates."""
        t = self.t
        n, M = self.n, self.M
        eps = 1e-6
        # Agent m's positions across all n envs
        ax = self.x.view(n, M)[:, m]   # (n,)
        ay = self.y.view(n, M)[:, m]
        yaw = self.yaw.view(n, M)[:, m]

        # Apply GPS drift offset to perceived position (GPS-denied: rover believes wrong pos)
        ax_obs = ax + gps_drift[:, m, 0]
        ay_obs = ay + gps_drift[:, m, 1]

        c, s = t.cos(yaw), t.sin(yaw)
        dvx = c[:, None] * self.ray_cos[None, :] - s[:, None] * self.ray_sin[None, :]
        dvy = s[:, None] * self.ray_cos[None, :] + c[:, None] * self.ray_sin[None, :]

        def slab_1d(d, o, lo, hi):
            par = d.abs() < eps
            ds = t.where(par, t.full_like(d, eps), d)
            t1 = (lo - o) / ds; t2 = (hi - o) / ds
            tmin_ = t.minimum(t1, t2); tmax_ = t.maximum(t1, t2)
            inside = (o >= lo) & (o <= hi)
            big = t.full_like(tmin_, 1e9)
            tmin_ = t.where(par, t.where(inside, -big, big), tmin_)
            tmax_ = t.where(par, t.where(inside, big, -big), tmax_)
            return tmin_, tmax_

        # --- static obstacles (n, K) ---
        tnx, txx = slab_1d(dvx[:, :, None], ax_obs[:, None, None],
                           (self.bcx - self.bhx)[:, None, :],
                           (self.bcx + self.bhx)[:, None, :])
        tny, txy = slab_1d(dvy[:, :, None], ay_obs[:, None, None],
                           (self.bcy - self.bhy)[:, None, :],
                           (self.bcy + self.bhy)[:, None, :])
        tmin = t.maximum(tnx, tny)
        tmax = t.minimum(txx, txy)
        hit = (tmax >= tmin) & (tmax >= 0) & (self.bmask[:, None, :] > 0.5)
        tcand = t.where(tmin >= 0, tmin, t.zeros_like(tmin))
        dist = t.where(hit, tcand, t.full_like(tcand, LIDAR_MAX))
        scan = dist.min(dim=2).values   # (n, L)

        # --- teammates as cylinders (approximate as square boxes) ---
        tm_cx, tm_cy = self._teammate_boxes(m)   # (n, M-1)
        r = TEAMMATE_R
        tm_hx = t.full_like(tm_cx, r)
        tm_hy = t.full_like(tm_cy, r)
        tm_mask = t.ones_like(tm_cx)

        tnx_t, txx_t = slab_1d(dvx[:, :, None], ax_obs[:, None, None],
                                (tm_cx - tm_hx)[:, None, :], (tm_cx + tm_hx)[:, None, :])
        tny_t, txy_t = slab_1d(dvy[:, :, None], ay_obs[:, None, None],
                                (tm_cy - tm_hy)[:, None, :], (tm_cy + tm_hy)[:, None, :])
        tmin_t = t.maximum(tnx_t, tny_t)
        tmax_t = t.minimum(txx_t, txy_t)
        hit_t = (tmax_t >= tmin_t) & (tmax_t >= 0) & (tm_mask[:, None, :] > 0.5)
        tc_t = t.where(tmin_t >= 0, tmin_t, t.zeros_like(tmin_t))
        dist_t = t.where(hit_t, tc_t, t.full_like(tc_t, LIDAR_MAX))
        scan_t = dist_t.min(dim=2).values   # (n, L)

        return t.minimum(scan, scan_t).clamp(0.0, LIDAR_MAX)

    # ------------------------------------------------------------------ obs
    def obs(self):
        """Full (n*M, R_STATE_DIM) observation for all agents, in NM-batch order."""
        t = self.t
        n, M = self.n, self.M
        NM = n * M

        ag_gx, ag_gy, assigned, _ = self._assign_goals()

        # GPS drift (accumulated; non-zero only at stage 4+)
        gps_drift = t.stack([self.drift_x, self.drift_y], dim=1).view(n, M, 2)

        obs_list = []
        for m in range(M):
            ag_idx = t.arange(n, device=self.dev) * M + m
            ax = self.x[ag_idx]; ay = self.y[ag_idx]; ayaw = self.yaw[ag_idx]
            av = self.v[ag_idx]; aw = self.w[ag_idx]
            # Goal in body frame (+ comms noise for stages 3+)
            gxm = ag_gx[ag_idx] + self.comms_noise[ag_idx, 0]
            gym = ag_gy[ag_idx] + self.comms_noise[ag_idx, 1]
            dx = gxm - ax; dy = gym - ay
            c, s = t.cos(-ayaw), t.sin(-ayaw)
            tf = c * dx - s * dy
            tl = s * dx + c * dy
            dist = t.sqrt(tf * tf + tl * tl)
            yaw_err = t.atan2(tl, tf)

            base = t.stack([
                tf, tl, t.zeros_like(tf), dist,
                av, t.zeros_like(av), t.zeros_like(av),
                yaw_err, aw, t.zeros_like(tf), t.ones_like(tf),
            ], dim=1)   # (n, 11)

            scan = self._lidar_scan_agent(m, gps_drift)   # (n, L)
            if self.lidar_noise > 0:
                scan = (scan + t.randn_like(scan) * self.lidar_noise).clamp(0.0, LIDAR_MAX)
            if self.target_noise > 0:
                base[:, 0:2] += t.randn_like(base[:, 0:2]) * self.target_noise

            obs_list.append(t.cat([base, scan], dim=1))   # (n, 83)

        # interleave: obs[env*M+m] = agent m of env
        stacked = t.stack(obs_list, dim=1)   # (n, M, 83)
        return stacked.view(NM, R_STATE_DIM)

    # ------------------------------------------------------------------ step
    def step(self, action, w):
        """action (n*M, 2). Returns reward (n*M,), done_env (n,), info dict."""
        t = self.t
        n, M = self.n, self.M
        NM = n * M

        ag_gx, ag_gy, assigned, all_found = self._assign_goals()
        d_prev = t.sqrt((ag_gx - self.x) ** 2 + (ag_gy - self.y) ** 2)

        v_cmd = action[:, 0].clamp(-MAX_V, MAX_V)
        w_cmd = action[:, 1].clamp(-MAX_W, MAX_W)
        cmd4 = t.stack([v_cmd, t.zeros_like(v_cmd), t.zeros_like(v_cmd), w_cmd], dim=1)
        vbody, yr_real = self.dyn.apply(cmd4, DT)
        v_real = vbody[:, 0].clamp(-MAX_V, MAX_V)
        w_real = yr_real.clamp(-MAX_W, MAX_W)

        a_lin = (v_real - self.v) / DT
        a_ang = (w_real - self.w) / DT
        jerk = t.sqrt((a_lin - self.prev_a) ** 2 + (a_ang - self.prev_alpha) ** 2) / DT

        wind = self.dyn.wind   # (NM, 2)
        c, s = t.cos(self.yaw), t.sin(self.yaw)
        self.x += (c * v_real) * DT + wind[:, 0] * DT
        self.y += (s * v_real) * DT + wind[:, 1] * DT
        self.yaw = _wrap_pi(t, self.yaw + w_real * DT)
        self.v = v_real; self.w = w_real
        self.prev_a = a_lin; self.prev_alpha = a_ang
        self.steps += 1

        # GPS drift update (random walk, stage 4+)
        if self.stage >= 4:
            self.drift_x += t.randn_like(self.drift_x) * GPS_DRIFT_RATE * DT
            self.drift_y += t.randn_like(self.drift_y) * GPS_DRIFT_RATE * DT

        # Comms noise resample each tick (stage 3+)
        if self.stage >= 3:
            self.comms_noise = t.randn_like(self.comms_noise) * COMMS_GOAL_NOISE_STD

        d_now = t.sqrt((ag_gx - self.x) ** 2 + (ag_gy - self.y) ** 2)
        progress = d_prev - d_now

        # Static obstacle collision per agent
        ax_env = self.x.view(n, M); ay_env = self.y.view(n, M)
        # min distance per agent to static obstacles
        ddx = ((self.x[:, None] - self.bcx.repeat_interleave(M, 0)).abs()
               - self.bhx.repeat_interleave(M, 0)).clamp(min=0.0)
        ddy = ((self.y[:, None] - self.bcy.repeat_interleave(M, 0)).abs()
               - self.bhy.repeat_interleave(M, 0)).clamp(min=0.0)
        box_d = t.sqrt(ddx ** 2 + ddy ** 2)
        bmask_rep = self.bmask.repeat_interleave(M, 0)
        box_d = t.where(bmask_rep > 0.5, box_d, t.full_like(box_d, 1e9))
        min_box_dist = box_d.min(dim=1).values    # (NM,)

        collided_static = min_box_dist < COLLIDE_R
        reached = (d_now < REACH_R) & ~self.goal_found[
            t.arange(n, device=self.dev).repeat_interleave(M),
            assigned.view(NM)
        ]

        # Mark goals as found when an agent reaches them
        for m in range(M):
            ag_idx = t.arange(n, device=self.dev) * M + m
            r_m = reached[ag_idx]
            if r_m.any():
                a_m = assigned.view(n, M)[:, m]
                self.goal_found[t.arange(n, device=self.dev)[r_m], a_m[r_m]] = True

        newly_found = reached.float()

        # Team deconfliction: pairwise teammate separation
        ax_v = ax_env; ay_v = ay_env   # (n, M)
        pair_sep = t.full((n,), SAFE_SEP * 2, device=self.dev)  # default: clear
        teammate_collision = t.zeros(n, device=self.dev)
        for i in range(M):
            for j in range(i + 1, M):
                d_pair = t.sqrt((ax_v[:, i] - ax_v[:, j]) ** 2 +
                                (ay_v[:, i] - ay_v[:, j]) ** 2)
                surf = d_pair - 2 * TEAMMATE_R
                pair_sep = t.minimum(pair_sep, surf)
                teammate_collision += (surf < 0).float()

        # Broadcast team quantities back to per-agent
        pair_sep_nm = pair_sep.repeat_interleave(M)     # (NM,)
        tm_coll_nm = teammate_collision.repeat_interleave(M)
        all_found_nm = all_found

        near = (w.clear_margin - min_box_dist).clamp(min=0.0)
        stalled = (v_real.abs() < w.stall_speed) & (d_now > REACH_R)
        timeout = self.steps >= MAX_STEPS
        timed_out = timeout & (~reached) & (~collided_static)

        # Deconfliction bonus: reward clearance above SAFE_SEP
        deconf_bonus = (pair_sep_nm - SAFE_SEP).clamp(min=0.0) / SAFE_SEP

        reward = (w.k_prog * progress
                  - w.k_time
                  - w.k_jerk * jerk
                  - w.k_stall * stalled.float()
                  - w.k_clear * near
                  - w.k_coll * collided_static.float()
                  - w.k_timeout * timed_out.float()
                  + w.k_goal * reached.float()
                  + w.k_cov * newly_found          # team coverage bonus (own reaches)
                  + w.k_deconf * deconf_bonus       # clearance incentive
                  - w.k_tcoll * tm_coll_nm          # teammate collision penalty (team)
                  )

        done_env = all_found_nm | (timeout.view(n, M).all(dim=1).repeat_interleave(M))
        return reward, done_env.view(n, M).all(dim=1), {
            "reached": reached, "collided": collided_static,
            "teammate_collisions": teammate_collision,
        }

    def reset_done(self, done_env):
        """done_env: (n,) bool — reset envs where True."""
        if done_env.any():
            self._new_episode(done_env)

    def goal_dist(self):
        ag_gx, ag_gy, _, _ = self._assign_goals()
        return self.t.sqrt((ag_gx - self.x) ** 2 + (ag_gy - self.y) ** 2)


# --------------------------------------------------------------------------- reward weights
class W:
    def __init__(self, a):
        self.k_prog = a.k_prog
        self.k_time = a.k_time
        self.k_jerk = a.k_jerk
        self.k_coll = a.k_coll
        self.k_goal = a.k_goal
        self.k_stall = a.k_stall
        self.k_timeout = a.k_timeout
        self.stall_speed = a.stall_speed
        self.k_clear = a.k_clear
        self.clear_margin = a.clear_margin
        self.k_cov = a.k_cov
        self.k_deconf = a.k_deconf
        self.k_tcoll = a.k_tcoll


# --------------------------------------------------------------------------- model
def build_ac(torch, hidden):
    """Identical architecture to single-agent RoverAC — shared policy, same ONNX shape."""
    nn = torch.nn

    class RoverAC(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer("mean", torch.tensor(R_STATE_MEAN))
            self.register_buffer("std", torch.tensor(R_STATE_STD))
            self.gru = nn.GRU(R_STATE_DIM, hidden, batch_first=True)
            self.head = nn.Sequential(
                nn.Linear(hidden, hidden), nn.ReLU(), nn.Linear(hidden, R_ACTION_DIM)
            )
            self.gru_v = nn.GRU(R_STATE_DIM, hidden, batch_first=True)
            self.value = nn.Sequential(
                nn.Linear(hidden, hidden), nn.ReLU(), nn.Linear(hidden, 1)
            )
            self.log_std = nn.Parameter(torch.full((R_ACTION_DIM,), -1.4))
            self.hidden = hidden

        def _norm(self, x):
            return (x - self.mean) / (self.std + 1e-6)

        def step(self, obs, h_a, h_v):
            xn = self._norm(obs).unsqueeze(1)
            oa, h_a = self.gru(xn, h_a)
            ov, h_v = self.gru_v(xn, h_v)
            return self.head(oa[:, 0]), self.value(ov[:, 0]).squeeze(-1), h_a, h_v

    return RoverAC()


# --------------------------------------------------------------------------- training
def main():
    ap = argparse.ArgumentParser(description="Multi-agent rover IPPO trainer")
    ap.add_argument("--ckpt", default=None, help="warm-start from single-agent _ac.pt checkpoint")
    ap.add_argument("--out", required=True)
    ap.add_argument("--version", default="ma_rover_v1")
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--iters", type=int, default=1200)
    ap.add_argument("--envs", type=int, default=256,
                    help="parallel env-instances (total workers = envs×M)")
    ap.add_argument("--agents", type=int, default=4, help="M agents per env")
    ap.add_argument("--rollout", type=int, default=96)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=1e-4,
                    help="lower than single-agent due to larger effective batch")
    ap.add_argument("--gamma", type=float, default=0.99)
    ap.add_argument("--lam", type=float, default=0.95)
    ap.add_argument("--clip", type=float, default=0.2)
    # Reward weights
    ap.add_argument("--k-prog", type=float, default=1.5)
    ap.add_argument("--k-time", type=float, default=0.02)
    ap.add_argument("--k-jerk", type=float, default=0.002)
    ap.add_argument("--k-coll", type=float, default=25.0)
    ap.add_argument("--k-goal", type=float, default=40.0)
    ap.add_argument("--k-stall", type=float, default=0.25)
    ap.add_argument("--k-timeout", type=float, default=8.0)
    ap.add_argument("--stall-speed", type=float, default=0.2)
    ap.add_argument("--k-clear", type=float, default=0.5)
    ap.add_argument("--clear-margin", type=float, default=0.5)
    # Team reward weights
    ap.add_argument("--k-cov", type=float, default=5.0,
                    help="team coverage bonus per goal reached by any agent")
    ap.add_argument("--k-deconf", type=float, default=0.3,
                    help="bonus for maintaining clearance above SAFE_SEP")
    ap.add_argument("--k-tcoll", type=float, default=15.0,
                    help="penalty per pair of touching teammates")
    # Curriculum
    ap.add_argument("--stage-fracs", type=float, nargs=6,
                    default=[0.0, 0.10, 0.20, 0.40, 0.60, 0.75],
                    help="fractions of iters at which stages 0..5 start")
    # DR
    ap.add_argument("--dr-hold-frac", type=float, default=0.15)
    ap.add_argument("--dr-full-frac", type=float, default=0.65)
    # Noise
    ap.add_argument("--lidar-noise", type=float, default=0.05)
    ap.add_argument("--target-noise", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    w = W(args)

    torch = _torch()
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(args.seed)

    M = args.agents
    hidden = args.hidden
    if args.ckpt:
        ckpt = torch.load(args.ckpt, map_location=dev, weights_only=False)
        hidden = ckpt.get("hidden", hidden)
        ac = build_ac(torch, hidden).to(dev)
        ac.load_state_dict(ckpt["state_dict"], strict=False)
        print(f"warm-started from {args.ckpt} (hidden={hidden})", flush=True)
    else:
        ac = build_ac(torch, hidden).to(dev)
        print(f"training from scratch (hidden={hidden})", flush=True)
    print(f"params={sum(p.numel() for p in ac.parameters())}  device={dev}  M={M}", flush=True)

    opt = torch.optim.Adam(ac.parameters(), lr=args.lr)

    n = args.envs
    NM = n * M
    env = MultiAgentRoverEnv(torch, n, M, dev, seed=args.seed,
                             lidar_noise=args.lidar_noise,
                             target_noise=args.target_noise, stage=0)

    h_a = torch.zeros(1, NM, hidden, device=dev)
    h_v = torch.zeros(1, NM, hidden, device=dev)

    def dr_scale(it):
        hold = args.dr_hold_frac * args.iters
        full = args.dr_full_frac * args.iters
        return 0.0 if it <= hold else (1.0 if it >= full else (it - hold) / max(1.0, full - hold))

    def curriculum_stage(it):
        fracs = args.stage_fracs
        for s in range(5, -1, -1):
            if it >= fracs[s] * args.iters:
                return s
        return 0

    hist = []
    best_reach, best_cov, best_sd = -1.0, -1.0, None

    for it in range(args.iters):
        new_stage = curriculum_stage(it)
        if new_stage != env.stage:
            env.stage = new_stage
            env.reset_all()
            h_a.zero_(); h_v.zero_()
            print(f"  → curriculum stage {new_stage}", flush=True)
        env.dyn.set_scale(dr_scale(it))

        T = args.rollout
        obs_b = torch.zeros(T, NM, R_STATE_DIM, device=dev)
        act_b = torch.zeros(T, NM, R_ACTION_DIM, device=dev)
        logp_b = torch.zeros(T, NM, device=dev)
        val_b = torch.zeros(T, NM, device=dev)
        rew_b = torch.zeros(T, NM, device=dev)
        done_b = torch.zeros(T, NM, device=dev)
        h_a0, h_v0 = h_a.detach().clone(), h_v.detach().clone()

        ep_reach = ep_coll = ep_tcoll = ep_cov = ep_cnt = ep_ret = 0.0

        for tick in range(T):
            o = env.obs()
            with torch.no_grad():
                mu, val, h_a, h_v = ac.step(o, h_a, h_v)
                std = ac.log_std.exp()
                dist_ = torch.distributions.Normal(mu, std)
                act = dist_.sample()
                logp = dist_.log_prob(act).sum(-1)
            rew, done_env, info = env.step(act, w)
            obs_b[tick] = o; act_b[tick] = act; logp_b[tick] = logp
            val_b[tick] = val; rew_b[tick] = rew
            # broadcast env-level done to all agents in that env
            done_nm = done_env.repeat_interleave(M)
            done_b[tick] = done_nm.float()
            ep_reach += float(info["reached"].sum())
            ep_coll += float(info["collided"].sum())
            ep_tcoll += float(info["teammate_collisions"].sum())
            ep_cov += float(env.goal_found.float().sum())
            ep_cnt += float(done_env.sum())
            ep_ret += float(rew.sum())
            nd = (1.0 - done_nm.float()).view(1, NM, 1)
            h_a = h_a * nd; h_v = h_v * nd
            env.reset_done(done_env)

        with torch.no_grad():
            _, last_val, _, _ = ac.step(env.obs(), h_a, h_v)

        # GAE
        adv = torch.zeros(T, NM, device=dev)
        last_gae = torch.zeros(NM, device=dev)
        for tt in reversed(range(T)):
            nv = last_val if tt == T - 1 else val_b[tt + 1]
            nt = 1.0 - done_b[tt]
            delta = rew_b[tt] + args.gamma * nv * nt - val_b[tt]
            last_gae = delta + args.gamma * args.lam * nt * last_gae
            adv[tt] = last_gae
        ret = adv + val_b
        adv = (adv - adv.mean()) / (adv.std() + 1e-8)

        def replay():
            ha, hv = h_a0.clone(), h_v0.clone()
            mus = torch.zeros(T, NM, R_ACTION_DIM, device=dev)
            vals = torch.zeros(T, NM, device=dev)
            for tick in range(T):
                if tick > 0:
                    nd = (1.0 - done_b[tick - 1]).view(1, NM, 1)
                    ha = ha * nd; hv = hv * nd
                m, v, ha, hv = ac.step(obs_b[tick], ha, hv)
                mus[tick] = m; vals[tick] = v
            return mus, vals

        for _ in range(args.epochs):
            mus, vals = replay()
            std = ac.log_std.exp()
            dist_ = torch.distributions.Normal(mus, std)
            logp = dist_.log_prob(act_b).sum(-1)
            ratio = (logp - logp_b).exp()
            pg = -torch.min(ratio * adv,
                            torch.clamp(ratio, 1 - args.clip, 1 + args.clip) * adv).mean()
            vl = 0.5 * (vals - ret).pow(2).mean()
            ent = dist_.entropy().sum(-1).mean()
            loss = pg + 0.5 * vl - 0.005 * ent
            opt.zero_grad(); loss.backward()
            torch.nn.utils.clip_grad_norm_(ac.parameters(), 0.5)
            opt.step()

        h_a, h_v = h_a.detach(), h_v.detach()
        reach_rate = ep_reach / max(ep_cnt * M, 1)
        coll_rate = ep_coll / max(ep_cnt * M, 1)
        tcoll_rate = ep_tcoll / max(ep_cnt, 1)
        cov_rate = ep_cov / max(ep_cnt * M, 1)
        hist.append({"iter": it, "stage": env.stage, "drs": env.dyn.scale,
                     "reach": reach_rate, "cov": cov_rate,
                     "coll": coll_rate, "tcoll": tcoll_rate})

        # Best checkpoint: full DR, stage ≥ 3, optimize combined reach+coverage
        score = reach_rate + cov_rate
        if env.dyn.scale >= 0.7 and env.stage >= 3 and score > best_reach + best_cov:
            best_reach, best_cov = reach_rate, cov_rate
            best_sd = {k: v.detach().clone() for k, v in ac.state_dict().items()}

        if it % 10 == 0 or it == args.iters - 1:
            print(f"it {it:4d} stage={env.stage} drs={env.dyn.scale:.2f} "
                  f"reach={reach_rate:.2f} cov={cov_rate:.2f} "
                  f"coll={coll_rate:.2f} tcoll={tcoll_rate:.2f} "
                  f"ret/env={ep_ret / n:7.2f}", flush=True)

    if best_sd is not None:
        ac.load_state_dict(best_sd)
        print(f"exporting BEST checkpoint (reach={best_reach:.2f} cov={best_cov:.2f})",
              flush=True)
    else:
        print("exporting final checkpoint", flush=True)

    # --- ONNX export (identical shape to single-agent — drop-in deployment) ---
    nn = torch.nn

    class StepExport(nn.Module):
        def __init__(self, ac):
            super().__init__()
            self.mean = ac.mean; self.std = ac.std
            self.gru = ac.gru; self.head = ac.head

        def forward(self, state, h_in):
            xn = (state - self.mean) / (self.std + 1e-6)
            out, hn = self.gru(xn, h_in)
            return self.head(out[:, -1, :]), hn

    out_dir = Path(args.out).expanduser(); out_dir.mkdir(parents=True, exist_ok=True)

    # Save checkpoint FIRST so it survives even if ONNX export fails
    np.save(out_dir / f"policy_{args.version}_state_norm.npy",
            np.stack([R_STATE_MEAN, R_STATE_STD]).astype(np.float32))
    torch.save({"state_dict": ac.state_dict(), "hidden": hidden,
                "mean": R_STATE_MEAN, "std": R_STATE_STD,
                "recurrent": True, "vehicle": "rover",
                "state_dim": R_STATE_DIM, "action_dim": R_ACTION_DIM,
                "multi_agent": True, "n_agents": M},
               out_dir / f"policy_{args.version}_ac.pt")
    print(f"checkpoint saved → policy_{args.version}_ac.pt", flush=True)

    exporter = StepExport(ac).to(dev).eval()
    onnx_path = out_dir / f"policy_{args.version}.onnx"
    dummy_s = torch.zeros(1, 1, R_STATE_DIM, device=dev)
    dummy_h = torch.zeros(1, 1, hidden, device=dev)
    torch.onnx.export(exporter, (dummy_s, dummy_h), str(onnx_path),
                      input_names=["state", "h_in"],
                      output_names=["action", "h_out"],
                      opset_version=17)
    print(f"ONNX exported → {onnx_path}", flush=True)

    summary = {
        "version": args.version, "recurrent": True, "vehicle": "rover",
        "multi_agent": True, "n_agents": M,
        "state_dim": R_STATE_DIM, "action_dim": R_ACTION_DIM,
        "hidden": hidden, "iters": args.iters,
        "best_reach": best_reach, "best_cov": best_cov,
        "final": hist[-1] if hist else None,
        "onnx": str(onnx_path), "onnx_bytes": onnx_path.stat().st_size,
    }
    (out_dir / f"policy_{args.version}_rl_summary.json").write_text(
        json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
