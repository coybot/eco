"""Heterogeneous Multi-Agent IPPO — Quadcopters + Rovers, unified 4-dim policy.

Design: single GRU policy (83-dim input, 4-dim output) drives both vehicle types.
  - Rover state:  [11 base | 72 lidar rays | vehicle=1.0]
  - Quad state:   [11 base | 45 depth rays | 27 zeros pad | vehicle=0.0]
  vehicle flag (field 10) conditions the GRU on vehicle type.

  Rovers: action → [v_linear, yaw_rate] (action[1,2] masked to zero).
  Quads:  action → [vx, vy, vz, yaw_rate] (full 4-dim holonomic).

Six-stage curriculum:
  0  rovers only, open field      (warm-start from rover policy)
  1  quads only, open field       (warm-start from quad policy)
  2  1q+1r, open field            (cross-type sensing)
  3  2q+2r, obstacles             (team density)
  4  2q+2r, comms+GPS noise       (degraded info)
  5  3q+3r, full gauntlet         (all stressors)

Train on hoopoe (A100, ~4–6h):
    PYTHONPATH=. python -m drone.training.train_ma_hetero \\
        --iters 4000 --envs 512 --version hetero_v1 \\
        --out ~/drone-data/rover/models

Continue from checkpoint:
    PYTHONPATH=. python -m drone.training.train_ma_hetero \\
        --ckpt ~/drone-data/rover/models/policy_hetero_v1_ac.pt \\
        --iters 2000 --envs 512 --version hetero_v2
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .rover_contract import (
    R_STATE_DIM, LIDAR_RAYS, LIDAR_MAX, LIDAR_ANGLES, R_STATE_MEAN, R_STATE_STD,
)
from drone.common.contract import (
    STATE_DIM as Q_STATE_DIM, DEPTH_RAYS, DEPTH_MAX, DEPTH_COLS,
    YAW_OFFSETS,
)

# --------------------------------------------------------------------------- constants
DT = 0.1
MAX_STEPS = 300
REACH_R = 1.0
ROVER_R = 0.50          # physical radius for rover collision
QUAD_R = 0.15           # physical radius for quad collision
TEAMMATE_R_ROVER = 0.50
TEAMMATE_R_QUAD = 0.15
MAX_V_ROVER = 2.0
MAX_W_ROVER = 2.0
MAX_V_QUAD = 3.0
MAX_W_QUAD = 1.5
QUAD_Z_MIN = 1.5        # quads must stay above this altitude
QUAD_Z_INIT = 4.0       # starting altitude for quads
SAFE_SEP = 1.2
LIDAR_MAX_ENV = LIDAR_MAX
DEPTH_MAX_ENV = DEPTH_MAX

VEHICLE_ROVER = 1.0
VEHICLE_QUAD = 0.0

# Unified state dim = rover state dim (83); quad obs is 83 with zeros padding [56:83].
H_STATE_DIM = R_STATE_DIM   # 83

# Forward horizontal depth angles for quads (9 rays, ±45°, same as center row of 5×9 grid)
_QUAD_H_ANGLES = list(YAW_OFFSETS)  # [+45°, ..., 0°, ..., -45°] in radians
_QUAD_COS = [math.cos(a) for a in _QUAD_H_ANGLES]
_QUAD_SIN = [math.sin(a) for a in _QUAD_H_ANGLES]


def _torch():
    import torch
    return torch


# --------------------------------------------------------------------------- stage config
def _stage_config(stage: int) -> dict:
    """Return (M_q, M_r, n_obs, wind_scale, comms_noise, gps_noise) for each curriculum stage."""
    configs = [
        # stage: M_q, M_r, n_obs, wind_scale, comms_noise_std, gps_drift_scale
        (0, 2, 0, 0.0, 0.0, 0.0),  # rovers only, open
        (2, 0, 0, 0.0, 0.0, 0.0),  # quads only, open
        (1, 1, 2, 0.0, 0.0, 0.0),  # 1q+1r, sparse obs
        (2, 2, 4, 0.5, 0.0, 0.0),  # 2q+2r, dense obs
        (2, 2, 4, 1.0, 1.5, 0.08), # 2q+2r, comms+GPS
        (3, 3, 6, 1.0, 1.5, 0.08), # 3q+3r, full gauntlet
    ]
    return dict(zip(["M_q","M_r","n_obs","wind_scale","comms_noise_std","gps_drift_scale"],
                    configs[min(stage, 5)]))


# --------------------------------------------------------------------------- env
class HeteroEnv:
    """Vectorised heterogeneous (quad+rover) multi-agent env.

    Agent ordering within each env-instance: [quad_0..quad_Mq-1, rover_0..rover_Mr-1].
    Observation for all agents is 83-dim (R_STATE_DIM), with:
      - Rovers: [11 base | 72 lidar rays]  — vehicle=1.0
      - Quads:  [11 base | 9 depth_center_row replicated×5 | 27 zeros] — vehicle=0.0
    """

    K = 10   # obstacle slots per env

    def __init__(self, torch, n, M_q, M_r, dev, seed=0,
                 lidar_noise=0.05, target_noise=0.15, stage=0):
        self.t = torch
        self.n = n
        self.M_q = M_q
        self.M_r = M_r
        self.M = M_q + M_r
        self.dev = dev
        self.lidar_noise = lidar_noise
        self.target_noise = target_noise
        self.stage = stage

        la = torch.tensor(LIDAR_ANGLES, device=dev, dtype=torch.float32)
        self.ray_cos = torch.cos(la)    # (LIDAR_RAYS,)
        self.ray_sin = torch.sin(la)

        # Quad horizontal depth rays (9)
        self.q_ray_cos = torch.tensor(_QUAD_COS, device=dev, dtype=torch.float32)
        self.q_ray_sin = torch.tensor(_QUAD_SIN, device=dev, dtype=torch.float32)

        self.g = torch.Generator(device="cpu").manual_seed(seed)
        self.reset_all()

    def _rand(self, *shape, lo=0.0, hi=1.0):
        return (lo + (hi - lo) * self.t.rand(*shape, generator=self.g)).to(self.dev)

    def reset_all(self):
        t, n, M = self.t, self.n, self.M
        NM = n * M
        # Positions (all agents)
        self.x   = t.zeros(NM, device=self.dev)
        self.y   = t.zeros(NM, device=self.dev)
        self.z   = t.zeros(NM, device=self.dev)   # altitude (0 for rovers)
        self.yaw = t.zeros(NM, device=self.dev)
        self.vx  = t.zeros(NM, device=self.dev)   # body-frame velocity (x=fwd)
        self.vy  = t.zeros(NM, device=self.dev)
        self.vz  = t.zeros(NM, device=self.dev)
        self.w   = t.zeros(NM, device=self.dev)   # yaw rate
        # Goals
        self.gx  = t.zeros(NM, device=self.dev)
        self.gy  = t.zeros(NM, device=self.dev)
        self.gz  = t.zeros(NM, device=self.dev)
        # Status
        self.alive = t.ones(NM, device=self.dev)
        self.reached = t.zeros(NM, device=self.dev)
        # Obstacles (n, K)
        self.bcx  = t.zeros(n, self.K, device=self.dev)
        self.bcy  = t.zeros(n, self.K, device=self.dev)
        self.bhx  = t.zeros(n, self.K, device=self.dev)
        self.bhy  = t.zeros(n, self.K, device=self.dev)
        self.bmask = t.zeros(n, self.K, device=self.dev)
        # Episode counters
        self.steps = t.zeros(n, device=self.dev)
        self.done  = t.ones(n, device=self.dev, dtype=t.bool)
        # Degradation state
        self.comms_noise = t.zeros(NM, 2, device=self.dev)
        self.drift_x = t.zeros(NM, device=self.dev)
        self.drift_y = t.zeros(NM, device=self.dev)
        # Targets found (per-env counter)
        self.n_targets = M
        self.found = t.zeros(n, M, device=self.dev)
        self._episode_new(t.ones(n, device=self.dev, dtype=t.bool))

    def _episode_new(self, mask):
        t = self.t
        n = self.n; M = self.M; M_q = self.M_q; M_r = self.M_r
        ne = int(mask.sum().item())
        if ne == 0:
            return
        idx_env = mask.nonzero(as_tuple=True)[0]     # (ne,)

        # Place agents
        for m in range(M):
            ag_idx = idx_env * M + m
            is_quad = (m < M_q)
            # Random start positions in a strip [-12..12, -8..8]
            self.x[ag_idx] = self._rand(ne, lo=-12.0, hi=12.0)
            self.y[ag_idx] = self._rand(ne, lo=-8.0, hi=8.0)
            self.z[ag_idx] = QUAD_Z_INIT if is_quad else 0.0
            self.yaw[ag_idx] = self._rand(ne, lo=-math.pi, hi=math.pi)
            self.vx[ag_idx] = 0.0; self.vy[ag_idx] = 0.0
            self.vz[ag_idx] = 0.0; self.w[ag_idx] = 0.0
            # Goal: far side
            self.gx[ag_idx] = self.x[ag_idx] + self._rand(ne, lo=-6.0, hi=6.0)
            self.gy[ag_idx] = self.y[ag_idx] + self._rand(ne, lo=-6.0, hi=6.0)
            self.gz[ag_idx] = QUAD_Z_INIT if is_quad else 0.0
            self.reached[ag_idx] = 0.0
            self.alive[ag_idx] = 1.0
            self.drift_x[ag_idx] = 0.0
            self.drift_y[ag_idx] = 0.0
            self.comms_noise[ag_idx] = 0.0

        self.found[idx_env] = 0.0
        self.steps[idx_env] = 0.0

        # Obstacles (random boxes)
        n_obs = int(min(self.K, max(0, getattr(self, '_n_obs', 0))))
        self.bmask[idx_env] = 0.0
        for k in range(n_obs):
            self.bcx[idx_env, k] = self._rand(ne, lo=-10.0, hi=10.0)
            self.bcy[idx_env, k] = self._rand(ne, lo=-7.0, hi=7.0)
            self.bhx[idx_env, k] = self._rand(ne, lo=0.4, hi=2.0)
            self.bhy[idx_env, k] = self._rand(ne, lo=0.4, hi=2.0)
            self.bmask[idx_env, k] = 1.0

    def _lidar_rover(self, env_idx_m, ax, ay, ayaw):
        """2D lidar for one rover across n envs. Returns (n, LIDAR_RAYS)."""
        t = self.t
        n = self.n
        c, s = t.cos(ayaw), t.sin(ayaw)
        dvx = c[:, None] * self.ray_cos[None, :] - s[:, None] * self.ray_sin[None, :]
        dvy = s[:, None] * self.ray_cos[None, :] + c[:, None] * self.ray_sin[None, :]
        eps = 1e-7

        def slab(d, o, lo, hi):
            par = d.abs() < eps
            ds = t.where(par, t.full_like(d, eps), d)
            t1 = (lo - o) / ds; t2 = (hi - o) / ds
            tmin = t.minimum(t1, t2); tmax = t.maximum(t1, t2)
            inside = (o >= lo) & (o <= hi)
            big = t.full_like(tmin, 1e9)
            tmin = t.where(par, t.where(inside, -big, big), tmin)
            tmax = t.where(par, t.where(inside, big, -big), tmax)
            return tmin, tmax

        # Static obstacles
        tnx, txx = slab(dvx[:, :, None], ax[:, None, None],
                        (self.bcx - self.bhx)[:, None, :],
                        (self.bcx + self.bhx)[:, None, :])
        tny, txy = slab(dvy[:, :, None], ay[:, None, None],
                        (self.bcy - self.bhy)[:, None, :],
                        (self.bcy + self.bhy)[:, None, :])
        tmin = t.maximum(tnx, tny); tmax = t.minimum(txx, txy)
        hit = (tmax >= tmin) & (tmax >= 0) & (self.bmask[:, None, :] > 0.5)
        dist = t.where(hit, t.clamp(tmin, min=0.0), t.full_like(tmin, LIDAR_MAX_ENV))
        scan = dist.min(dim=2).values

        # All other agents as boxes (both quads and rovers)
        for m2 in range(self.M):
            ag2 = t.arange(n, device=self.dev) * self.M + m2
            if (ag2 == env_idx_m).all():
                continue
            ox = self.x[ag2]; oy = self.y[ag2]
            r = TEAMMATE_R_QUAD if m2 < self.M_q else TEAMMATE_R_ROVER
            tnx_t, txx_t = slab(dvx, ax[:, None], (ox - r)[:, None], (ox + r)[:, None])
            tny_t, txy_t = slab(dvy, ay[:, None], (oy - r)[:, None], (oy + r)[:, None])
            tmin_t = t.maximum(tnx_t, tny_t); tmax_t = t.minimum(txx_t, txy_t)
            hit_t = (tmax_t >= tmin_t) & (tmax_t >= 0)
            d_t = t.where(hit_t, t.clamp(tmin_t, min=0.0),
                          t.full_like(tmin_t, LIDAR_MAX_ENV))
            scan = t.minimum(scan, d_t.min(dim=1).values[:, None].expand_as(scan)
                             if d_t.dim() == 1 else d_t)

        return scan.clamp(0.0, LIDAR_MAX_ENV)

    def _depth_quad(self, env_idx_m, ax, ay, az, ayaw):
        """9-ray forward depth scan for one quad. Returns (n, 9) then replicated to (n,45)."""
        t = self.t
        n = self.n
        c, s = t.cos(ayaw), t.sin(ayaw)
        dvx = c[:, None] * self.q_ray_cos[None, :] - s[:, None] * self.q_ray_sin[None, :]
        dvy = s[:, None] * self.q_ray_cos[None, :] + c[:, None] * self.q_ray_sin[None, :]
        eps = 1e-7

        def slab2d(d, o, lo, hi):
            par = d.abs() < eps
            ds = t.where(par, t.full_like(d, eps), d)
            t1 = (lo - o) / ds; t2 = (hi - o) / ds
            tmin = t.minimum(t1, t2); tmax = t.maximum(t1, t2)
            inside = (o >= lo) & (o <= hi)
            big = t.full_like(tmin, 1e9)
            tmin = t.where(par, t.where(inside, -big, big), tmin)
            tmax = t.where(par, t.where(inside, big, -big), tmax)
            return tmin, tmax

        tnx, txx = slab2d(dvx[:, :, None], ax[:, None, None],
                           (self.bcx - self.bhx)[:, None, :],
                           (self.bcx + self.bhx)[:, None, :])
        tny, txy = slab2d(dvy[:, :, None], ay[:, None, None],
                           (self.bcy - self.bhy)[:, None, :],
                           (self.bcy + self.bhy)[:, None, :])
        tmin = t.maximum(tnx, tny); tmax = t.minimum(txx, txy)
        hit = (tmax >= tmin) & (tmax >= 0) & (self.bmask[:, None, :] > 0.5)
        dist = t.where(hit, t.clamp(tmin, min=0.0), t.full_like(tmin, DEPTH_MAX_ENV))
        scan9 = dist.min(dim=2).values   # (n, 9)

        # Teammates as 2D boxes at their horizontal position
        for m2 in range(self.M):
            ag2 = t.arange(n, device=self.dev) * self.M + m2
            if (ag2 == env_idx_m).all():
                continue
            ox = self.x[ag2]; oy = self.y[ag2]
            r = TEAMMATE_R_QUAD if m2 < self.M_q else TEAMMATE_R_ROVER
            tnx_t, txx_t = slab2d(dvx, ax[:, None], (ox - r)[:, None], (ox + r)[:, None])
            tny_t, txy_t = slab2d(dvy, ay[:, None], (oy - r)[:, None], (oy + r)[:, None])
            tmin_t = t.maximum(tnx_t, tny_t); tmax_t = t.minimum(txx_t, txy_t)
            hit_t = (tmax_t >= tmin_t) & (tmax_t >= 0)
            d_t = t.where(hit_t, t.clamp(tmin_t, min=0.0),
                          t.full_like(tmin_t, DEPTH_MAX_ENV))
            scan9 = t.minimum(scan9, d_t)

        # Replicate across 5 rows → 45-dim depth vector
        scan45 = scan9.repeat(1, 5)   # (n, 45) [row_0 = row_1 = ... = row_4]
        return scan45.clamp(0.0, DEPTH_MAX_ENV)

    def obs(self):
        """Returns (n*M, H_STATE_DIM=83) observations for all agents."""
        t = self.t
        n, M, M_q, M_r = self.n, self.M, self.M_q, self.M_r
        NM = n * M

        obs_list = []
        for m in range(M):
            ag_idx = t.arange(n, device=self.dev) * M + m
            is_quad = (m < M_q)
            ax = self.x[ag_idx]; ay = self.y[ag_idx]; az = self.z[ag_idx]
            ayaw = self.yaw[ag_idx]
            avx = self.vx[ag_idx]; avy = self.vy[ag_idx]
            avz = self.vz[ag_idx]; aw = self.w[ag_idx]

            # Goal + comms noise
            gxm = self.gx[ag_idx] + self.comms_noise[ag_idx, 0]
            gym = self.gy[ag_idx] + self.comms_noise[ag_idx, 1]
            gzm = self.gz[ag_idx]

            # Body-frame goal
            dx = gxm - ax + self.drift_x[ag_idx]
            dy = gym - ay + self.drift_y[ag_idx]
            dz = gzm - az
            c, s = t.cos(-ayaw), t.sin(-ayaw)
            tf = c * dx - s * dy
            tl = s * dx + c * dy
            tu = dz
            dist = t.sqrt(tf*tf + tl*tl + tu*tu + 1e-6)
            yaw_err = t.atan2(tl, tf)

            # Body-frame velocity
            vf = c * avx - s * avy
            vl = s * avx + c * avy
            vu = avz

            vehicle = t.full_like(ax, VEHICLE_QUAD if is_quad else VEHICLE_ROVER)

            base = t.stack([tf, tl, tu, dist, vf, vl, vu,
                            yaw_err, aw, az, vehicle], dim=1)   # (n, 11)

            if is_quad:
                scan45 = self._depth_quad(ag_idx, ax, ay, az, ayaw)
                if self.lidar_noise > 0:
                    scan45 = (scan45 + t.randn_like(scan45)*self.lidar_noise).clamp(0, DEPTH_MAX_ENV)
                # Pad to 72 with zeros → total = 11+72 = 83
                pad = t.zeros(n, LIDAR_RAYS - DEPTH_RAYS, device=self.dev)
                sensor = t.cat([scan45, pad], dim=1)   # (n, 72)
            else:
                ax_obs = ax + self.drift_x[ag_idx]
                ay_obs = ay + self.drift_y[ag_idx]
                scan72 = self._lidar_rover(ag_idx, ax_obs, ay_obs, ayaw)
                if self.lidar_noise > 0:
                    scan72 = (scan72 + t.randn_like(scan72)*self.lidar_noise).clamp(0, LIDAR_MAX_ENV)
                sensor = scan72   # (n, 72)

            if self.target_noise > 0:
                base[:, 0:2] += t.randn_like(base[:, 0:2]) * self.target_noise

            obs_list.append(t.cat([base, sensor], dim=1))   # (n, 83)

        stacked = t.stack(obs_list, dim=1)   # (n, M, 83)
        return stacked.view(NM, H_STATE_DIM)

    def step(self, action_nm, w):
        """action_nm: (n*M, 4). Returns reward (n,), done (n,), info dict."""
        t = self.t
        n, M, M_q = self.n, self.M, self.M_q
        NM = n * M

        self.steps += 1.0
        rew = t.zeros(n, device=self.dev)

        for m in range(M):
            ag_idx = t.arange(n, device=self.dev) * M + m
            a = action_nm[ag_idx]   # (n, 4)
            is_quad = (m < M_q)

            if is_quad:
                # Holonomic 3D integration
                vx_cmd = a[:, 0].clamp(-MAX_V_QUAD, MAX_V_QUAD)
                vy_cmd = a[:, 1].clamp(-MAX_V_QUAD, MAX_V_QUAD)
                vz_cmd = a[:, 2].clamp(-MAX_V_QUAD, MAX_V_QUAD)
                w_cmd  = a[:, 3].clamp(-MAX_W_QUAD, MAX_W_QUAD)
                yaw_new = self.yaw[ag_idx] + w_cmd * DT
                c, s = t.cos(yaw_new), t.sin(yaw_new)
                self.x[ag_idx]   += (c * vx_cmd - s * vy_cmd) * DT
                self.y[ag_idx]   += (s * vx_cmd + c * vy_cmd) * DT
                self.z[ag_idx]    = (self.z[ag_idx] + vz_cmd * DT).clamp(min=QUAD_Z_MIN)
                self.yaw[ag_idx]  = yaw_new
                self.vx[ag_idx]   = c * vx_cmd - s * vy_cmd
                self.vy[ag_idx]   = s * vx_cmd + c * vy_cmd
                self.vz[ag_idx]   = vz_cmd
                self.w[ag_idx]    = w_cmd
            else:
                # Unicycle 2D integration (rovers)
                v_cmd = a[:, 0].clamp(-MAX_V_ROVER, MAX_V_ROVER)
                w_cmd = a[:, 3].clamp(-MAX_W_ROVER, MAX_W_ROVER)
                yaw_new = self.yaw[ag_idx] + w_cmd * DT
                c, s = t.cos(yaw_new), t.sin(yaw_new)
                self.x[ag_idx]  += c * v_cmd * DT
                self.y[ag_idx]  += s * v_cmd * DT
                self.yaw[ag_idx] = yaw_new
                self.vx[ag_idx]  = c * v_cmd
                self.vy[ag_idx]  = s * v_cmd
                self.vz[ag_idx]  = t.zeros_like(v_cmd)
                self.w[ag_idx]   = w_cmd

            # Collision with static obstacles
            ax = self.x[ag_idx]; ay = self.y[ag_idx]
            r = QUAD_R if is_quad else ROVER_R
            dx = t.clamp((ax[:, None] - self.bcx).abs() - self.bhx, min=0.0)
            dy = t.clamp((ay[:, None] - self.bcy).abs() - self.bhy, min=0.0)
            surf = t.sqrt(dx*dx + dy*dy + 1e-6) - r
            coll_static = ((surf < 0) & (self.bmask > 0.5)).any(dim=1).float()
            rew -= coll_static * 3.0

            # Progress reward — sparse goal + dense shaping
            dx_g = self.gx[ag_idx] - self.x[ag_idx]
            dy_g = self.gy[ag_idx] - self.y[ag_idx]
            dz_g = self.gz[ag_idx] - self.z[ag_idx]
            dist_now = t.sqrt(dx_g*dx_g + dy_g*dy_g + dz_g*dz_g + 1e-6)
            reached_now = (dist_now < REACH_R) & (self.reached[ag_idx] < 0.5)
            self.reached[ag_idx] = t.where(reached_now, t.ones_like(self.reached[ag_idx]),
                                           self.reached[ag_idx])
            rew += reached_now.float() * 10.0
            # Dense: reward progress toward goal (potential-based shaping)
            not_reached = (self.reached[ag_idx] < 0.5)
            rew += not_reached.float() * (0.3 / (dist_now + 1.0))

        # Team deconfliction reward
        for m1 in range(M):
            for m2 in range(m1+1, M):
                i1 = t.arange(n, device=self.dev)*M + m1
                i2 = t.arange(n, device=self.dev)*M + m2
                surf = t.sqrt((self.x[i1]-self.x[i2])**2 +
                              (self.y[i1]-self.y[i2])**2 + 1e-6) - ROVER_R * 2
                touching = (surf < 0).float()
                rew -= touching * 2.0
                clearance_bonus = t.clamp(surf / SAFE_SEP, 0.0, 1.0)
                rew += clearance_bonus * 0.05

        # GPS drift update
        cfg = _stage_config(self.stage)
        dr = cfg["gps_drift_scale"]
        if dr > 0:
            self.drift_x += t.randn(NM, device=self.dev) * dr * DT
            self.drift_y += t.randn(NM, device=self.dev) * dr * DT

        # Comms noise update
        cn = cfg["comms_noise_std"]
        if cn > 0:
            mask_comms = (t.rand(NM, device=self.dev) < 0.3)
            noise = t.randn(NM, 2, device=self.dev) * cn
            self.comms_noise = t.where(mask_comms[:, None].expand_as(self.comms_noise),
                                       noise, self.comms_noise)

        done = ((self.steps >= MAX_STEPS) |
                (self.reached.view(n, M).all(dim=1)))
        return rew, done, {}

    def reset_done(self, done_env):
        self._episode_new(done_env)

    def set_stage(self, stage: int):
        self.stage = stage
        cfg = _stage_config(stage)
        self._n_obs = cfg["n_obs"]
        self.M_q = cfg["M_q"]
        self.M_r = cfg["M_r"]
        self.M = self.M_q + self.M_r


# --------------------------------------------------------------------------- policy
def build_ac(torch, hidden=256):
    import torch.nn as nn

    class HeteroAC(nn.Module):
        def __init__(self):
            super().__init__()
            self.hidden = hidden
            # Shared GRU over 83-dim input
            self.gru = nn.GRU(H_STATE_DIM, hidden, batch_first=True)
            self.actor = nn.Sequential(
                nn.Linear(hidden, 256), nn.Tanh(),
                nn.Linear(256, 128), nn.Tanh(),
                nn.Linear(128, 4),   # 4-dim action (rovers use [0,3])
            )
            self.critic = nn.Sequential(
                nn.Linear(hidden, 256), nn.Tanh(),
                nn.Linear(256, 128), nn.Tanh(),
                nn.Linear(128, 1),
            )
            # State normalization (rover stats; quads share same base fields)
            self.register_buffer("mean", torch.zeros(H_STATE_DIM))
            self.register_buffer("std", torch.ones(H_STATE_DIM))

        def _norm(self, x):
            return (x - self.mean) / (self.std + 1e-6)

        def step(self, obs, h_a, h_v):
            xn = self._norm(obs).unsqueeze(1)   # (NM, 1, 83)
            out_a, h_a_new = self.gru(xn, h_a)
            out_v, h_v_new = self.gru(xn, h_v)
            feat_a = out_a[:, 0, :]
            feat_v = out_v[:, 0, :]
            mu = torch.tanh(self.actor(feat_a))
            val = self.critic(feat_v).squeeze(-1)
            return mu, val, h_a_new, h_v_new

    return HeteroAC()


# --------------------------------------------------------------------------- training
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=4000)
    ap.add_argument("--envs", type=int, default=512)
    ap.add_argument("--rollout", type=int, default=96)
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--version", default="hetero_v1")
    ap.add_argument("--out", default=".")
    ap.add_argument("--ckpt", default=None)
    ap.add_argument("--min-reach", type=float, default=0.0,
                    help="advance curriculum only when smoothed reach > this (0=fixed schedule)")
    args = ap.parse_args()

    import torch
    import torch.nn as nn
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device={dev}  envs={args.envs}  iters={args.iters}")

    ac = build_ac(torch, args.hidden).to(dev)

    # Load norm stats from rover contract
    mean = torch.zeros(H_STATE_DIM)
    std  = torch.ones(H_STATE_DIM)
    mean[:len(R_STATE_MEAN)] = torch.tensor(R_STATE_MEAN, dtype=torch.float32)
    std[:len(R_STATE_STD)]   = torch.tensor(R_STATE_STD,  dtype=torch.float32)
    ac.mean.copy_(mean.to(dev))
    ac.std.copy_(std.to(dev))

    if args.ckpt:
        ckpt = torch.load(args.ckpt, map_location=dev, weights_only=False)
        ac.load_state_dict(ckpt["state_dict"], strict=False)
        print(f"  loaded checkpoint {args.ckpt}")

    opt = torch.optim.Adam(ac.parameters(), lr=args.lr)
    T, N = args.rollout, args.envs

    def curriculum_stage(it):
        if it < 2000:  return 0   # rovers only — learn basic navigation
        if it < 4000:  return 1   # quads only — learn 3D navigation
        if it < 6000:  return 2   # 1q+1r — learn coordination basics
        if it < 9000:  return 3   # 2q+2r + obstacles
        if it < 12000: return 4   # 2q+2r + comms/GPS
        return 5                   # 3q+3r full gauntlet

    # Start with stage 0 config
    s0 = _stage_config(0)
    M_q, M_r = s0["M_q"], s0["M_r"]
    M = M_q + M_r
    env = HeteroEnv(torch, N, M_q, M_r, dev)
    env._n_obs = s0["n_obs"]

    best_reach = 0.0
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Adaptive curriculum state
    current_stage = 0
    stage_reach_ema = 0.0
    min_reach = args.min_reach  # advance only when ema reach > this

    for it in range(1, args.iters + 1):
        # Advance stage: adaptive (--min-reach > 0) or fixed schedule
        if min_reach > 0:
            if stage_reach_ema > min_reach and current_stage < 5:
                current_stage += 1
                stage_reach_ema = 0.0
                print(f"  it={it}: reached {min_reach:.2f} → advance to stage {current_stage}")
        else:
            current_stage = curriculum_stage(it)
        stage = current_stage
        cfg = _stage_config(stage)
        new_Mq, new_Mr = cfg["M_q"], cfg["M_r"]
        if new_Mq != env.M_q or new_Mr != env.M_r:
            env = HeteroEnv(torch, N, new_Mq, new_Mr, dev, stage=stage)
            env._n_obs = cfg["n_obs"]
            M_q, M_r, M = new_Mq, new_Mr, new_Mq + new_Mr
            print(f"  it={it}: stage {stage} → {M_q}q+{M_r}r")
        env.stage = stage
        NM = N * M

        h_a = torch.zeros(1, NM, args.hidden, device=dev)
        h_v = torch.zeros(1, NM, args.hidden, device=dev)

        obs_buf   = torch.zeros(T, NM, H_STATE_DIM, device=dev)
        act_buf   = torch.zeros(T, NM, 4, device=dev)
        rew_buf   = torch.zeros(T, N, device=dev)
        done_buf  = torch.zeros(T, N, device=dev)
        val_buf   = torch.zeros(T, NM, device=dev)
        logp_buf  = torch.zeros(T, NM, device=dev)

        obs = env.obs()
        reach_sum = 0.0; coll_sum = 0.0; step_count = 0

        with torch.no_grad():
            for tick in range(T):
                mu, val, h_a, h_v = ac.step(obs, h_a, h_v)
                std_exp = 0.5 * torch.ones_like(mu)
                dist_t = torch.distributions.Normal(mu, std_exp)
                act = dist_t.sample()
                logp = dist_t.log_prob(act).sum(-1)

                obs_buf[tick] = obs
                act_buf[tick] = act
                val_buf[tick] = val
                logp_buf[tick] = logp

                rew, done, _ = env.step(act, None)
                rew_buf[tick] = rew
                done_buf[tick] = done.float()

                env.reset_done(done)
                nd = (1.0 - done.float()).view(1, N, 1)
                nd_nm = nd.expand(1, N, M).reshape(1, NM, 1)
                h_a = h_a * nd_nm
                h_v = h_v * nd_nm

                obs = env.obs()
                reach_sum += (env.reached.view(N, M).float().mean()).item()
                step_count += 1

        # GAE
        with torch.no_grad():
            _, last_val, _, _ = ac.step(obs, h_a, h_v)
        gae = torch.zeros(NM, device=dev)
        ret_buf = torch.zeros(T, NM, device=dev)
        gamma, lam = 0.99, 0.95
        for tick in reversed(range(T)):
            r_nm = rew_buf[tick].unsqueeze(1).expand(N, M).reshape(NM)
            d_nm = done_buf[tick].unsqueeze(1).expand(N, M).reshape(NM)
            nv = last_val if tick == T - 1 else val_buf[tick + 1]
            delta = r_nm + gamma * nv * (1.0 - d_nm) - val_buf[tick]
            gae = delta + gamma * lam * (1.0 - d_nm) * gae
            ret_buf[tick] = gae + val_buf[tick]

        obs_f  = obs_buf.view(T * NM, H_STATE_DIM)
        act_f  = act_buf.view(T * NM, 4)
        ret_f  = ret_buf.view(T * NM)
        logp_f = logp_buf.view(T * NM)
        adv_f  = (ret_f - val_buf.view(T * NM))
        adv_f  = (adv_f - adv_f.mean()) / (adv_f.std() + 1e-8)

        # PPO update
        clip_eps = 0.2
        for _ in range(3):
            h_a0 = torch.zeros(1, T * NM, args.hidden, device=dev)
            h_v0 = torch.zeros(1, T * NM, args.hidden, device=dev)
            mu_new, val_new, _, _ = ac.step(obs_f, h_a0, h_v0)
            std_new = 0.5 * torch.ones_like(mu_new)
            logp_new = torch.distributions.Normal(mu_new, std_new).log_prob(act_f).sum(-1)
            ratio = (logp_new - logp_f).exp()
            clipped = torch.clamp(ratio, 1 - clip_eps, 1 + clip_eps)
            actor_loss  = -torch.min(ratio * adv_f, clipped * adv_f).mean()
            critic_loss = (val_new - ret_f).pow(2).mean()
            entropy = torch.distributions.Normal(mu_new, std_new).entropy().sum(-1).mean()
            loss = actor_loss + 0.5 * critic_loss - 0.01 * entropy
            opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(ac.parameters(), 0.5)
            opt.step()

        mean_reach = reach_sum / max(step_count, 1)
        stage_reach_ema = 0.9 * stage_reach_ema + 0.1 * mean_reach
        if it % 20 == 0:
            print(f"it {it:4d}  stage={stage}  M={M_q}q+{M_r}r  "
                  f"reach={mean_reach:.2f}  ema={stage_reach_ema:.2f}  loss={loss.item():.3f}")

        if mean_reach > best_reach and stage >= 2:
            best_reach = mean_reach
            ckpt_path = out_dir / f"policy_{args.version}_ac.pt"
            torch.save({
                "state_dict": ac.state_dict(),
                "hidden": args.hidden, "state_dim": H_STATE_DIM, "action_dim": 4,
                "mean": ac.mean.cpu().numpy(), "std": ac.std.cpu().numpy(),
                "stage": stage, "version": args.version,
                "hetero": True, "M_quad_final": M_q, "M_rover_final": M_r,
            }, ckpt_path)

    # ONNX export — single file, vehicle flag conditions behavior
    _export_onnx(ac, args, out_dir)


def _export_onnx(ac, args, out_dir):
    import torch
    import torch.nn as nn

    class StepExport(nn.Module):
        def __init__(self, m):
            super().__init__()
            self.mean = m.mean; self.std = m.std
            self.gru = m.gru; self.actor = m.actor

        def forward(self, state, h_in):
            xn = (state - self.mean) / (self.std + 1e-6)
            out, hn = self.gru(xn, h_in)
            return torch.tanh(self.actor(out[:, -1, :])), hn

    model = StepExport(ac.cpu()).eval()
    dummy_state = torch.zeros(1, 1, H_STATE_DIM)
    dummy_h = torch.zeros(1, 1, args.hidden)
    path = str(out_dir / f"policy_{args.version}.onnx")
    torch.onnx.export(
        model, (dummy_state, dummy_h), path,
        input_names=["state", "h_in"], output_names=["action", "h_out"],
        dynamic_axes={"state": {0: "batch"}, "h_in": {1: "batch"},
                      "action": {0: "batch"}, "h_out": {1: "batch"}},
        opset_version=18,
    )
    print(f"  ONNX → {path}")


if __name__ == "__main__":
    main()
