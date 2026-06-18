"""Recurrent PPO for ground rover: 360° lidar + unicycle kinematics.

Design choices vs the quad (train_rl_rnn.py):
  - **360° lidar** (72 rays, 5° spacing) — full situational awareness, sees behind/beside,
    avoids the "commit to the wrong side" failures that plague forward-only cameras.
  - **Unicycle kinematics** — action = [v_linear, yaw_rate]; no lateral slip. Matches
    differential-drive hardware (Jetson Orin Nano rover).
  - **Separate rover contract** (rover_contract.py) — R_STATE_DIM=83, R_ACTION_DIM=2.
    The quad ONNX/contract is untouched.
  - **Same recurrent PPO** (carry-state GRU, separate actor/critic) — proven stable on the
    drone. T=96 so the policy sees full episode gradient.
  - **Four-stage curriculum** (open → sparse columns → dense columns+walls → tight slalom)
    with DR ramping on dynamics after the basic task is mastered.
  - **DynamicsDR** reused — wheel lag, accel cap, latency, heading drift (wind→yaw bias).

Nav2 integration (Jetson deploy):
    This policy runs as the local reactive planner inside Nav2. Nav2's global planner
    (A* on a cost map) supplies the next waypoint; our policy replaces DWB to execute it.
    Wire plan.v_linear → cmd_vel.linear.x, plan.yaw_rate → cmd_vel.angular.z.

Train on hoopoe (no BC warm-start needed, trains from scratch in ~30 min on A100):
    PYTHONPATH=. python -m eco.drone.training.train_rl_rover \\
        --out ~/drone-data/rover/models --version rover_v1 \\
        --iters 800 --envs 512 --rollout 96

Continue from a checkpoint:
    PYTHONPATH=. python -m eco.drone.training.train_rl_rover \\
        --ckpt ~/drone-data/rover/models/policy_rover_v1_ac.pt \\
        --out ~/drone-data/rover/models --version rover_v2 --iters 400 --envs 512
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
DT = 0.1          # simulation timestep (10 Hz)
MAX_STEPS = 250   # episode cap (25 s)
REACH_R = 1.0     # goal-reached radius (m)
COLLIDE_R = 0.30  # collision radius (m)
MAX_V = 2.0       # linear speed cap (m/s)
MAX_W = 2.0       # yaw-rate cap (rad/s)


def _torch():
    import torch
    return torch


def _wrap_pi(t, a):
    return (a + math.pi) % (2 * math.pi) - math.pi


# --------------------------------------------------------------------------- env
class RoverEnv:
    """Vectorised unicycle rover env with 360° lidar sensing, all in torch.

    Obstacles are floor-to-ceiling 2D prisms (arbitrary rectangles in XY). The lidar casts
    LIDAR_RAYS horizontal rays from the rover position using batched 2D ray-AABB intersection —
    equivalent to the quad's 3D slab code but with Z collapsed.

    Curriculum (controlled by self.stage 0..3):
        0 — open field (no obstacles), learn go-to-goal
        1 — sparse random columns (3-4 per env)
        2 — dense columns + occasional gap-walls (6-8 per env)
        3 — tight slalom: two or three corridor walls with offset gaps

    DR scale (DynamicsDR.scale) ramps separately from curriculum stage.
    """

    K = 10   # max obstacle slots per env (not all used at lower stages)

    def __init__(self, torch, n, dev, seed=0,
                 lidar_noise=0.05, target_noise=0.15, stage=0):
        self.t = torch
        self.n = n
        self.dev = dev
        self.lidar_noise = lidar_noise
        self.target_noise = target_noise
        self.stage = stage

        # precompute per-ray directions in body frame (fwd=+x, left=+y)
        la = torch.tensor(LIDAR_ANGLES, device=dev, dtype=torch.float32)  # (L,)
        self.ray_cos = torch.cos(la)   # (L,) body-frame fwd component
        self.ray_sin = torch.sin(la)   # (L,) body-frame left component

        self.g = torch.Generator(device="cpu").manual_seed(seed)
        self.reset_all()

    def _rand(self, *shape, lo=0.0, hi=1.0):
        t = self.t
        return (lo + (hi - lo) * t.rand(*shape, generator=self.g)).to(self.dev)

    def reset_all(self):
        t = self.t
        z = t.zeros(self.n, device=self.dev)
        self.x = z.clone(); self.y = z.clone(); self.yaw = z.clone()
        self.gx = z.clone(); self.gy = z.clone()
        self.v = z.clone(); self.w = z.clone()   # realized linear speed, yaw rate
        self.prev_a = z.clone(); self.prev_alpha = z.clone()  # for jerk
        self.steps = z.clone()
        # Obstacle slots: each box has centre (cx,cy) and half-extents (hx,hy)
        self.bcx = t.zeros(self.n, self.K, device=self.dev)
        self.bcy = t.zeros(self.n, self.K, device=self.dev)
        self.bhx = t.zeros(self.n, self.K, device=self.dev)
        self.bhy = t.zeros(self.n, self.K, device=self.dev)
        self.bmask = t.zeros(self.n, self.K, device=self.dev)
        self.dyn = DynamicsDR(t, self.n, self.dev, self._rand)
        self._new_episode(t.ones(self.n, device=self.dev, dtype=t.bool))

    # ---------------------------------------------------------------------- episode
    def _new_episode(self, mask):
        t = self.t
        n = int(mask.sum().item())
        if n == 0:
            return
        idx = mask.nonzero(as_tuple=True)[0]

        # Goal: ahead and to the side (not too close)
        gx = self._rand(n, lo=7.0, hi=15.0)
        gy = self._rand(n, lo=-3.0, hi=3.0)
        self.x[idx] = 0.0; self.y[idx] = 0.0
        self.gx[idx] = gx; self.gy[idx] = gy
        # Initial heading: mostly aligned with goal, sometimes random
        aligned = self._rand(n, lo=-0.5, hi=0.5)
        full = self._rand(n, lo=-math.pi, hi=math.pi)
        self.yaw[idx] = t.where(self._rand(n) < 0.65, aligned, full)

        # Clear all slots then populate based on stage
        self.bmask[idx] = 0.0

        if self.stage == 0:
            pass  # no obstacles

        elif self.stage == 1:
            # 3-4 scattered columns
            n_obs = 4
            for k in range(n_obs):
                self.bcx[idx, k] = gx * self._rand(n, lo=0.20, hi=0.82)
                self.bcy[idx, k] = self._rand(n, lo=-2.5, hi=2.5)
                self.bhx[idx, k] = self._rand(n, lo=0.2, hi=0.6)
                self.bhy[idx, k] = self._rand(n, lo=0.2, hi=0.6)
                self.bmask[idx, k] = (self._rand(n) > 0.20).float()

        elif self.stage == 2:
            # Dense columns + occasional walls
            for k in range(8):
                self.bcx[idx, k] = gx * self._rand(n, lo=0.15, hi=0.90)
                self.bcy[idx, k] = self._rand(n, lo=-3.0, hi=3.0)
                self.bhx[idx, k] = self._rand(n, lo=0.2, hi=0.8)
                self.bhy[idx, k] = self._rand(n, lo=0.2, hi=0.8)
                self.bmask[idx, k] = (self._rand(n) > 0.12).float()
            # 40% chance of a gap wall
            wall = self._rand(n) < 0.4
            if wall.any():
                self._place_gap_wall(idx, n, wall, gx, slot=8)

        else:  # stage 3: slalom gauntlet
            # Full curriculum: 40% double-wall slalom, 20% single wall, 40% dense columns
            mode = self._rand(n)
            # Default: dense columns
            for k in range(self.K):
                self.bcx[idx, k] = gx * self._rand(n, lo=0.18, hi=0.90)
                self.bcy[idx, k] = self._rand(n, lo=-3.0, hi=3.0)
                self.bhx[idx, k] = self._rand(n, lo=0.2, hi=0.9)
                self.bhy[idx, k] = self._rand(n, lo=0.2, hi=0.9)
                self.bmask[idx, k] = (self._rand(n) > 0.12).float()
            # double-wall slalom (40%)
            seq = mode < 0.40
            self._place_gap_wall(idx, n, seq, gx, cx_frac_lo=0.25, cx_frac_hi=0.42, slot=0)
            self._place_gap_wall(idx, n, seq, gx, cx_frac_lo=0.52, cx_frac_hi=0.72, slot=2)
            for k in range(4, self.K):
                self.bmask[idx[seq], k] = 0.0
            # single wall (20%)
            single = (mode >= 0.40) & (mode < 0.60)
            self._place_gap_wall(idx, n, single, gx, cx_frac_lo=0.35, cx_frac_hi=0.60, slot=0)
            for k in range(2, self.K):
                self.bmask[idx[single], k] = 0.0

        self.steps[idx] = 0.0
        self.v[idx] = 0.0; self.w[idx] = 0.0
        self.prev_a[idx] = 0.0; self.prev_alpha[idx] = 0.0
        self.dyn.randomize(mask)

    def _place_gap_wall(self, idx, n, m, gx,
                        cx_frac_lo=0.35, cx_frac_hi=0.65,
                        slot=0, span=10.0, wall_hx=0.4):
        """Place a corridor-spanning wall with a navigable gap into slots [slot, slot+1]."""
        t = self.t
        if not m.any():
            return
        gi = idx[m]
        cx = gx[m] * self._rand(n, lo=cx_frac_lo, hi=cx_frac_hi)
        gap_cy = self._rand(n, lo=-1.4, hi=1.4)
        gap_hy = self._rand(n, lo=0.65, hi=1.00)   # rover half-width ~0.3m, gap must fit

        # Left wall segment
        left_cy = (-span / 2 + gap_cy - gap_hy) / 2
        left_hy = ((gap_cy - gap_hy + span) / 2).clamp(min=0.1)
        # Right wall segment
        right_cy = (gap_cy + gap_hy + span) / 2
        right_hy = ((span - gap_cy - gap_hy) / 2).clamp(min=0.1)

        self.bcx[gi, slot] = cx[m]; self.bcy[gi, slot] = left_cy[m]
        self.bhx[gi, slot] = wall_hx; self.bhy[gi, slot] = left_hy[m]; self.bmask[gi, slot] = 1.0

        self.bcx[gi, slot + 1] = cx[m]; self.bcy[gi, slot + 1] = right_cy[m]
        self.bhx[gi, slot + 1] = wall_hx; self.bhy[gi, slot + 1] = right_hy[m]
        self.bmask[gi, slot + 1] = 1.0

    # ---------------------------------------------------------------------- sensing
    def lidar_scan(self):
        """(n, LIDAR_RAYS) via batched 2D ray-AABB. Fully vectorised, no loops."""
        t = self.t
        eps = 1e-6
        c, s = t.cos(self.yaw), t.sin(self.yaw)   # (n,)
        # World-frame ray directions: rotate body-frame (cos_a, sin_a) by yaw
        # dvx (n,L), dvy (n,L)
        dvx = c[:, None] * self.ray_cos[None, :] - s[:, None] * self.ray_sin[None, :]
        dvy = s[:, None] * self.ray_cos[None, :] + c[:, None] * self.ray_sin[None, :]

        def slab_1d(d, o, lo, hi):
            """d (n,L,1), o (n,1,1), lo/hi (n,1,K) → tmin,tmax (n,L,K)"""
            par = d.abs() < eps
            ds = t.where(par, t.full_like(d, eps), d)
            t1 = (lo - o) / ds; t2 = (hi - o) / ds
            tmin = t.minimum(t1, t2); tmax = t.maximum(t1, t2)
            inside = (o >= lo) & (o <= hi)
            big = t.full_like(tmin, 1e9)
            tmin = t.where(par, t.where(inside, -big, big), tmin)
            tmax = t.where(par, t.where(inside, big, -big), tmax)
            return tmin, tmax

        tnx, txx = slab_1d(dvx[:, :, None], self.x[:, None, None],
                           (self.bcx - self.bhx)[:, None, :],
                           (self.bcx + self.bhx)[:, None, :])
        tny, txy = slab_1d(dvy[:, :, None], self.y[:, None, None],
                           (self.bcy - self.bhy)[:, None, :],
                           (self.bcy + self.bhy)[:, None, :])
        tmin = t.maximum(tnx, tny)
        tmax = t.minimum(txx, txy)
        hit = (tmax >= tmin) & (tmax >= 0) & (self.bmask[:, None, :] > 0.5)
        tcand = t.where(tmin >= 0, tmin, t.zeros_like(tmin))
        dist = t.where(hit, tcand, t.full_like(tcand, LIDAR_MAX))
        return dist.min(dim=2).values.clamp(0.0, LIDAR_MAX)   # (n, LIDAR_RAYS)

    def min_box_dist(self):
        """(n,) minimum 2D distance from rover to any obstacle surface."""
        t = self.t
        ddx = ((self.x[:, None] - self.bcx).abs() - self.bhx).clamp(min=0.0)
        ddy = ((self.y[:, None] - self.bcy).abs() - self.bhy).clamp(min=0.0)
        d = t.sqrt(ddx * ddx + ddy * ddy)
        d = t.where(self.bmask > 0.5, d, t.full_like(d, 1e9))
        return d.min(dim=1).values

    def goal_dist(self):
        t = self.t
        return t.sqrt((self.gx - self.x) ** 2 + (self.gy - self.y) ** 2)

    # ---------------------------------------------------------------------- obs
    def obs(self):
        """(n, R_STATE_DIM) raw state matching rover_contract order."""
        t = self.t
        dx, dy = self.gx - self.x, self.gy - self.y
        # Rotate world-frame goal vector into body frame
        c, s = t.cos(-self.yaw), t.sin(-self.yaw)
        tf = c * dx - s * dy         # forward component
        tl = s * dx + c * dy         # left component
        dist = t.sqrt(tf * tf + tl * tl)
        yaw_err = t.atan2(tl, tf)

        base = t.stack([
            tf, tl, t.zeros_like(tf), dist,
            self.v, t.zeros_like(self.v), t.zeros_like(self.v),
            yaw_err, self.w, t.zeros_like(tf), t.ones_like(tf),
        ], dim=1)  # (n, 11)

        scan = self.lidar_scan()   # (n, LIDAR_RAYS)
        if self.lidar_noise > 0:
            scan = (scan + t.randn_like(scan) * self.lidar_noise).clamp(0.0, LIDAR_MAX)
        if self.target_noise > 0:
            base[:, 0:2] += t.randn_like(base[:, 0:2]) * self.target_noise

        return t.cat([base, scan], dim=1)   # (n, R_STATE_DIM)

    # ---------------------------------------------------------------------- step
    def step(self, action, w):
        """action (n,2): [v_cmd, yaw_rate_cmd]. Returns reward (n,), done (n,), info."""
        t = self.t
        d_prev = self.goal_dist()

        v_cmd = action[:, 0].clamp(-MAX_V, MAX_V)
        w_cmd = action[:, 1].clamp(-MAX_W, MAX_W)
        # DynamicsDR expects a 4-dim command; pack unicycle into [vx,vy=0,vz=0,yaw_rate]
        cmd4 = t.stack([v_cmd, t.zeros_like(v_cmd), t.zeros_like(v_cmd), w_cmd], dim=1)
        vbody, yr_real = self.dyn.apply(cmd4, DT)
        v_real = vbody[:, 0].clamp(-MAX_V, MAX_V)
        w_real = yr_real.clamp(-MAX_W, MAX_W)

        # Jerk (smoothness penalty)
        a_lin = (v_real - self.v) / DT
        a_ang = (w_real - self.w) / DT
        jerk = t.sqrt((a_lin - self.prev_a) ** 2 + (a_ang - self.prev_alpha) ** 2) / DT

        # Unicycle integration (+ wind treated as forward/lateral additive drift)
        wind = self.dyn.wind   # (n,2) world-frame
        c, s = t.cos(self.yaw), t.sin(self.yaw)
        self.x += (c * v_real) * DT + wind[:, 0] * DT
        self.y += (s * v_real) * DT + wind[:, 1] * DT
        self.yaw = _wrap_pi(t, self.yaw + w_real * DT)
        self.v = v_real; self.w = w_real
        self.prev_a = a_lin; self.prev_alpha = a_ang
        self.steps += 1

        d_now = self.goal_dist()
        progress = d_prev - d_now
        box_dist = self.min_box_dist()
        collided = box_dist < COLLIDE_R
        reached = d_now < REACH_R
        timeout = self.steps >= MAX_STEPS

        near = (w.clear_margin - box_dist).clamp(min=0.0)
        stalled = (v_real.abs() < w.stall_speed) & (d_now > REACH_R)
        timed_out = timeout & (~reached) & (~collided)

        reward = (w.k_prog * progress
                  - w.k_time
                  - w.k_jerk * jerk
                  - w.k_stall * stalled.float()
                  - w.k_clear * near
                  - w.k_coll * collided.float()
                  - w.k_timeout * timed_out.float()
                  + w.k_goal * reached.float())
        done = collided | reached | timeout
        return reward, done, {"reached": reached, "collided": collided}

    def reset_done(self, done):
        if done.any():
            self._new_episode(done)


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


# --------------------------------------------------------------------------- model
def build_ac(torch, hidden):
    nn = torch.nn

    class RoverAC(nn.Module):
        """Separate actor/critic GRUs — same design as the quad to keep training stable."""

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
            """One tick. obs (n, R_STATE_DIM), h_* (1,n,hidden) → mean, value, h_a', h_v'."""
            xn = self._norm(obs).unsqueeze(1)
            oa, h_a = self.gru(xn, h_a)
            ov, h_v = self.gru_v(xn, h_v)
            return self.head(oa[:, 0]), self.value(ov[:, 0]).squeeze(-1), h_a, h_v

    return RoverAC()


# --------------------------------------------------------------------------- training
def main():
    ap = argparse.ArgumentParser(description="Rover recurrent PPO trainer")
    ap.add_argument("--ckpt", default=None, help="continue from _ac.pt checkpoint")
    ap.add_argument("--out", required=True)
    ap.add_argument("--version", default="rover_v1")
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--iters", type=int, default=800)
    ap.add_argument("--envs", type=int, default=512)
    ap.add_argument("--rollout", type=int, default=96,
                    help="must be ≥ max episode length; 96 = 9.6s at 10 Hz")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--gamma", type=float, default=0.99)
    ap.add_argument("--lam", type=float, default=0.95)
    ap.add_argument("--clip", type=float, default=0.2)
    # Reward weights
    ap.add_argument("--k-prog", type=float, default=1.5)
    ap.add_argument("--k-time", type=float, default=0.03)
    ap.add_argument("--k-jerk", type=float, default=0.003)
    ap.add_argument("--k-coll", type=float, default=25.0)
    ap.add_argument("--k-goal", type=float, default=40.0)
    ap.add_argument("--k-stall", type=float, default=0.3)
    ap.add_argument("--k-timeout", type=float, default=10.0)
    ap.add_argument("--stall-speed", type=float, default=0.2)
    ap.add_argument("--k-clear", type=float, default=0.6)
    ap.add_argument("--clear-margin", type=float, default=0.5,
                    help="keep rover ≥ this many metres from obstacle surfaces")
    # Curriculum
    ap.add_argument("--curriculum", action="store_true", default=True,
                    help="ramp stage 0→3 over training (recommended)")
    ap.add_argument("--stage", type=int, default=None,
                    help="fix obstacle stage (overrides curriculum)")
    ap.add_argument("--stage-fracs", type=float, nargs=4,
                    default=[0.10, 0.25, 0.50, 0.75],
                    help="fractions of iters at which stages 0,1,2,3 start")
    # Domain randomization
    ap.add_argument("--dr-hold-frac", type=float, default=0.25)
    ap.add_argument("--dr-full-frac", type=float, default=0.70)
    # Noise
    ap.add_argument("--lidar-noise", type=float, default=0.05,
                    help="std (m) of Gaussian noise on lidar ranges (RPLidar noise ~0.03-0.05m)")
    ap.add_argument("--target-noise", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    w = W(args)

    torch = _torch()
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(args.seed)

    hidden = args.hidden
    if args.ckpt:
        ckpt = torch.load(args.ckpt, map_location=dev, weights_only=False)
        hidden = ckpt.get("hidden", hidden)
        ac = build_ac(torch, hidden).to(dev)
        ac.load_state_dict(ckpt["state_dict"], strict=False)
        print(f"loaded checkpoint {args.ckpt} (hidden={hidden})", flush=True)
    else:
        ac = build_ac(torch, hidden).to(dev)
        print(f"training rover policy from scratch (hidden={hidden})", flush=True)
    print(f"params={sum(p.numel() for p in ac.parameters())}  device={dev}", flush=True)

    opt = torch.optim.Adam(ac.parameters(), lr=args.lr)

    # Initial stage: start at 0 (open field) unless fixed
    init_stage = args.stage if args.stage is not None else 0
    env = RoverEnv(torch, args.envs, dev, seed=args.seed,
                   lidar_noise=args.lidar_noise, target_noise=args.target_noise,
                   stage=init_stage)

    n = args.envs
    h_a = torch.zeros(1, n, hidden, device=dev)
    h_v = torch.zeros(1, n, hidden, device=dev)

    def dr_scale(it):
        hold = args.dr_hold_frac * args.iters
        full = args.dr_full_frac * args.iters
        if it <= hold:
            return 0.0
        if it >= full:
            return 1.0
        return (it - hold) / max(1.0, full - hold)

    def curriculum_stage(it):
        if args.stage is not None:
            return args.stage
        fracs = args.stage_fracs
        for s in range(3, -1, -1):
            if it >= fracs[s] * args.iters:
                return s
        return 0

    hist = []
    best_reach, best_sd = -1.0, None

    for it in range(args.iters):
        # Curriculum + DR schedule
        new_stage = curriculum_stage(it)
        if new_stage != env.stage:
            env.stage = new_stage
            env.reset_all()   # re-draw episodes with new obstacle density
            h_a.zero_(); h_v.zero_()
            print(f"  → curriculum stage {new_stage}", flush=True)
        env.dyn.set_scale(dr_scale(it))

        T = args.rollout
        obs_b = torch.zeros(T, n, R_STATE_DIM, device=dev)
        act_b = torch.zeros(T, n, R_ACTION_DIM, device=dev)
        logp_b = torch.zeros(T, n, device=dev)
        val_b = torch.zeros(T, n, device=dev)
        rew_b = torch.zeros(T, n, device=dev)
        done_b = torch.zeros(T, n, device=dev)
        h_a0, h_v0 = h_a.detach().clone(), h_v.detach().clone()

        ep_reach = ep_coll = ep_cnt = ep_ret = 0.0
        for t in range(T):
            o = env.obs()
            with torch.no_grad():
                mu, val, h_a, h_v = ac.step(o, h_a, h_v)
                std = ac.log_std.exp()
                dist_ = torch.distributions.Normal(mu, std)
                act = dist_.sample()
                logp = dist_.log_prob(act).sum(-1)
            rew, done, info = env.step(act, w)
            obs_b[t] = o; act_b[t] = act; logp_b[t] = logp
            val_b[t] = val; rew_b[t] = rew; done_b[t] = done.float()
            ep_reach += float(info["reached"].sum())
            ep_coll += float(info["collided"].sum())
            ep_cnt += float(done.sum()); ep_ret += float(rew.sum())
            nd = (1.0 - done.float()).view(1, n, 1)
            h_a = h_a * nd; h_v = h_v * nd
            env.reset_done(done)

        with torch.no_grad():
            _, last_val, _, _ = ac.step(env.obs(), h_a, h_v)

        # GAE
        adv = torch.zeros(T, n, device=dev)
        last_gae = torch.zeros(n, device=dev)
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
            mus = torch.zeros(T, n, R_ACTION_DIM, device=dev)
            vals = torch.zeros(T, n, device=dev)
            for t in range(T):
                if t > 0:
                    nd = (1.0 - done_b[t - 1]).view(1, n, 1)
                    ha = ha * nd; hv = hv * nd
                m, v, ha, hv = ac.step(obs_b[t], ha, hv)
                mus[t] = m; vals[t] = v
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
        reach_rate = ep_reach / max(ep_cnt, 1)
        coll_rate = ep_coll / max(ep_cnt, 1)
        hist.append({"iter": it, "stage": env.stage, "drs": env.dyn.scale,
                     "reach": reach_rate, "coll": coll_rate})
        # Save best checkpoint at full DR / highest obstacle stage
        if env.dyn.scale >= 0.8 and env.stage >= 2 and reach_rate > best_reach:
            best_reach = reach_rate
            best_sd = {k: v.detach().clone() for k, v in ac.state_dict().items()}
        if it % 10 == 0 or it == args.iters - 1:
            print(f"it {it:3d} stage={env.stage} drs={env.dyn.scale:.2f} "
                  f"reach={reach_rate:.2f} coll={coll_rate:.2f} "
                  f"ret/env={ep_ret / n:7.2f} "
                  f"std={ac.log_std.exp().mean().item():.3f}", flush=True)

    if best_sd is not None:
        ac.load_state_dict(best_sd)
        print(f"exporting BEST checkpoint (reach={best_reach:.2f})", flush=True)
    else:
        print("exporting final checkpoint", flush=True)

    # --- ONNX export: (state[1,1,83], h_in[1,1,256]) → (action[1,2], h_out[1,1,256]) ---
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
    exporter = StepExport(ac).to(dev).eval()
    onnx_path = out_dir / f"policy_{args.version}.onnx"
    dummy_s = torch.zeros(1, 1, R_STATE_DIM, device=dev)
    dummy_h = torch.zeros(1, 1, hidden, device=dev)
    torch.onnx.export(exporter, (dummy_s, dummy_h), str(onnx_path),
                      input_names=["state", "h_in"],
                      output_names=["action", "h_out"],
                      opset_version=17)

    np.save(out_dir / f"policy_{args.version}_state_norm.npy",
            np.stack([R_STATE_MEAN, R_STATE_STD]).astype(np.float32))
    torch.save({"state_dict": ac.state_dict(), "hidden": hidden,
                "mean": R_STATE_MEAN, "std": R_STATE_STD,
                "recurrent": True, "vehicle": "rover",
                "state_dim": R_STATE_DIM, "action_dim": R_ACTION_DIM},
               out_dir / f"policy_{args.version}_ac.pt")

    summary = {
        "version": args.version, "recurrent": True, "vehicle": "rover",
        "state_dim": R_STATE_DIM, "action_dim": R_ACTION_DIM,
        "lidar_rays": LIDAR_RAYS, "hidden": hidden,
        "iters": args.iters, "best_reach": best_reach,
        "final": hist[-1] if hist else None,
        "onnx": str(onnx_path), "onnx_bytes": onnx_path.stat().st_size,
    }
    (out_dir / f"policy_{args.version}_rl_summary.json").write_text(
        json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
