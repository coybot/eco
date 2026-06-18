"""Recurrent PPO for ground rover (differential/holonomic drive, 2D kinematic world).

Architecture mirrors train_rl_rnn.py (separate actor/critic GRUs, carry-state PPO).
Key differences from the quad BoxEnv:
  - Ground-plane only: rover at fixed z=CAMERA_Z=0.5m; no vertical dynamics
  - Floor-to-ceiling prism obstacles (bhz=5): lateral avoidance only
  - vehicle=VEHICLE_ROVER (1.0) in every observation
  - vz component of action is zeroed; altitude=0; target_up=0

Training from scratch:
    PYTHONPATH=. python -m eco.drone.training.train_rl_rover \\
        --out ~/drone-data/rover/models --version rover_v1 --iters 600 --envs 256

Warm-start from an existing RNN checkpoint:
    PYTHONPATH=. python -m eco.drone.training.train_rl_rover \\
        --bc-ckpt ~/drone-data/rover/models/policy_rover_v0.pt \\
        --out ~/drone-data/rover/models --version rover_v1 --iters 600 --envs 256
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .contract import (STATE_DIM, ACTION_DIM, DEPTH_RAYS, RAY_DIRS, DEPTH_MAX, VEHICLE_ROVER)
from .train_rl import W, DT, MAX_SPEED, torch_cos, torch_sin, torch_atan2, wrap_pi_t
from .dynamics import DynamicsDR

REACH = 1.0
COLLIDE_R = 0.35
MAX_STEPS = 300
CAMERA_Z = 0.5      # rover camera height above ground (depth-ray origin)


def _torch():
    import torch
    return torch


# --------------------------------------------------------------------------- env
class RoverEnv:
    """Vectorized kinematic rover env with random 2D column/wall obstacles, all in torch.

    The rover stays at z=CAMERA_Z so the batched 3D ray-AABB code from BoxEnv works unchanged:
    obstacles are floor-to-ceiling prisms (bhz=5). vehicle=VEHICLE_ROVER in all observations.
    """

    def __init__(self, torch, n, dev, k_boxes=10, seed=0, depth_noise=0.0, target_noise=0.0):
        self.t = torch
        self.n = n
        self.dev = dev
        self.K = k_boxes
        self.depth_noise = depth_noise
        self.target_noise = target_noise
        rd = torch.tensor(RAY_DIRS, device=dev, dtype=torch.float32)  # (R,3)
        self.ray_fwd = rd[:, 0]; self.ray_left = rd[:, 1]; self.ray_up = rd[:, 2]
        self.g = torch.Generator(device="cpu").manual_seed(seed)
        self.reset_all()

    def _rand(self, *shape, lo=0.0, hi=1.0):
        t = self.t
        return (lo + (hi - lo) * t.rand(*shape, generator=self.g)).to(self.dev)

    def reset_all(self):
        t = self.t
        z = t.zeros(self.n, device=self.dev)
        self.x = z.clone(); self.y = z.clone()
        self.gx = z.clone(); self.gy = z.clone()
        self.yaw = z.clone()
        self.bcx = t.zeros(self.n, self.K, device=self.dev)
        self.bcy = t.zeros(self.n, self.K, device=self.dev)
        self.bhx = t.zeros(self.n, self.K, device=self.dev)
        self.bhy = t.zeros(self.n, self.K, device=self.dev)
        self.bmask = t.zeros(self.n, self.K, device=self.dev)
        self.vx = z.clone(); self.vy = z.clone()
        self.prev_ax = z.clone(); self.prev_ay = z.clone()
        self.yr = z.clone()
        self.steps = z.clone()
        self.dyn = DynamicsDR(t, self.n, self.dev, self._rand)
        self._new_episode(t.ones(self.n, device=self.dev, dtype=t.bool))

    def _new_episode(self, mask):
        t = self.t
        n = int(mask.sum().item())
        if n == 0:
            return
        idx = mask.nonzero(as_tuple=True)[0]
        gx = self._rand(n, lo=6.0, hi=14.0)
        gy = self._rand(n, lo=-3.0, hi=3.0)
        self.x[idx] = 0.0; self.y[idx] = 0.0
        self.gx[idx] = gx; self.gy[idx] = gy
        aligned = self._rand(n, lo=-0.6, hi=0.6)
        full = self._rand(n, lo=-math.pi, hi=math.pi)
        self.yaw[idx] = t.where(self._rand(n) < 0.6, aligned, full)

        SPAN = 10.0   # wall lateral span in 2D rover world
        HXW = 0.4     # wall depth half-width

        # Default: scattered column prisms across all slots
        for k in range(self.K):
            self.bcx[idx, k] = gx * self._rand(n, lo=0.20, hi=0.88)
            self.bcy[idx, k] = self._rand(n, lo=-2.8, hi=2.8)
            self.bhx[idx, k] = self._rand(n, lo=0.2, hi=0.9)
            self.bhy[idx, k] = self._rand(n, lo=0.2, hi=0.9)
            self.bmask[idx, k] = (self._rand(n) > 0.12).float()

        mode = self._rand(n)

        def set_wall_gap(m, s0, cx):
            """Corridor wall + navigable gap using slots [s0, s0+1]."""
            gi = idx[m]
            gap_y = self._rand(n, lo=-1.3, hi=1.3)    # gap center
            gap_hy = self._rand(n, lo=0.65, hi=0.95)  # gap half-width
            # left segment
            left_cy = ((-SPAN + (gap_y - gap_hy)) / 2)
            left_hy = ((gap_y - gap_hy + SPAN) / 2).clamp(min=0.05)
            self.bcx[gi, s0] = cx[m]; self.bcy[gi, s0] = left_cy[m]
            self.bhx[gi, s0] = HXW; self.bhy[gi, s0] = left_hy[m]
            self.bmask[gi, s0] = 1.0
            # right segment
            right_cy = ((gap_y + gap_hy + SPAN) / 2)
            right_hy = ((SPAN - (gap_y + gap_hy)) / 2).clamp(min=0.05)
            self.bcx[gi, s0 + 1] = cx[m]; self.bcy[gi, s0 + 1] = right_cy[m]
            self.bhx[gi, s0 + 1] = HXW; self.bhy[gi, s0 + 1] = right_hy[m]
            self.bmask[gi, s0 + 1] = 1.0

        def mask_off(m, slots):
            for k in slots:
                self.bmask[idx[m], k] = 0.0

        # 35%: two gap walls in sequence (slalom / gauntlet)
        seq = mode < 0.35
        set_wall_gap(seq, 0, gx * self._rand(n, lo=0.25, hi=0.42))
        set_wall_gap(seq, 2, gx * self._rand(n, lo=0.52, hi=0.72))
        mask_off(seq, list(range(4, self.K)))

        # 20%: single gap wall
        single = (mode >= 0.35) & (mode < 0.55)
        set_wall_gap(single, 0, gx * self._rand(n, lo=0.35, hi=0.60))
        mask_off(single, list(range(2, self.K)))

        self.steps[idx] = 0.0
        self.dyn.randomize(mask)

    def depth_grid(self):
        """(n, R) depth ranges from rover camera at z=CAMERA_Z against floor-to-ceiling prisms."""
        t = self.t
        eps = 1e-6
        c, s = torch_cos(t, self.yaw), torch_sin(t, self.yaw)
        fwd, left, up = self.ray_fwd, self.ray_left, self.ray_up

        dvx = c[:, None] * fwd[None, :] - s[:, None] * left[None, :]
        dvy = s[:, None] * fwd[None, :] + c[:, None] * left[None, :]
        dvz = up[None, :].expand(self.n, -1)

        # Boxes are floor-to-ceiling prisms: cz=0, hz=5 (spans well above/below camera)
        rover_z = t.full((self.n,), CAMERA_Z, device=self.dev)
        box_cz = t.zeros(self.n, self.K, device=self.dev)
        box_hz = t.full((self.n, self.K), 5.0, device=self.dev)

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

        tnx, txx = slab(dvx[:, :, None], self.x[:, None, None],
                        (self.bcx - self.bhx)[:, None, :], (self.bcx + self.bhx)[:, None, :])
        tny, txy = slab(dvy[:, :, None], self.y[:, None, None],
                        (self.bcy - self.bhy)[:, None, :], (self.bcy + self.bhy)[:, None, :])
        tnz, txz = slab(dvz[:, :, None], rover_z[:, None, None],
                        (box_cz - box_hz)[:, None, :], (box_cz + box_hz)[:, None, :])
        tmin = t.maximum(t.maximum(tnx, tny), tnz)
        tmax = t.minimum(t.minimum(txx, txy), txz)
        hit = (tmax >= tmin) & (tmax >= 0) & (self.bmask[:, None, :] > 0.5)
        tcand = t.where(tmin >= 0, tmin, t.zeros_like(tmin))
        dist = t.where(hit, tcand, t.full_like(tcand, DEPTH_MAX))
        return dist.min(dim=2).values.clamp(0.0, DEPTH_MAX)

    def min_box_dist(self):
        """2D distance from rover footprint to nearest obstacle surface."""
        t = self.t
        ddx = ((self.x[:, None] - self.bcx).abs() - self.bhx).clamp(min=0.0)
        ddy = ((self.y[:, None] - self.bcy).abs() - self.bhy).clamp(min=0.0)
        d = t.sqrt(ddx * ddx + ddy * ddy)
        d = t.where(self.bmask > 0.5, d, t.full_like(d, 1e9))
        return d.min(dim=1).values

    def goal_dist(self):
        t = self.t
        return t.sqrt((self.gx - self.x) ** 2 + (self.gy - self.y) ** 2)

    def obs(self):
        """(n, STATE_DIM) raw state matching contract order. target_up=vel_up=altitude=0."""
        t = self.t
        dx, dy = self.gx - self.x, self.gy - self.y
        c, s = torch_cos(t, -self.yaw), torch_sin(t, -self.yaw)
        tf = c * dx - s * dy
        tl = s * dx + c * dy
        tu = t.zeros_like(tf)
        dist = t.sqrt(tf * tf + tl * tl)
        yaw_err = torch_atan2(t, tl, tf)
        vehicle = t.full_like(tf, VEHICLE_ROVER)
        base = t.stack([tf, tl, tu, dist,
                        self.vx, self.vy, t.zeros_like(self.vx),
                        yaw_err, self.yr, t.zeros_like(tf), vehicle], dim=1)
        grid = self.depth_grid()
        if self.depth_noise > 0:
            grid = (grid + t.randn_like(grid) * self.depth_noise).clamp(0.0, DEPTH_MAX)
        if self.target_noise > 0:
            base[:, 0:2] = base[:, 0:2] + t.randn_like(base[:, 0:2]) * self.target_noise
        return t.cat([base, grid], dim=1)

    def step(self, action, w):
        """action (n,4): [vx,vy,_vz_ignored,yaw_rate]. Returns reward (n,), done (n,)."""
        t = self.t
        d_prev = self.goal_dist()
        vx, vy, yr = action[:, 0], action[:, 1], action[:, 3]
        spd = t.sqrt(vx * vx + vy * vy).clamp(min=1e-6)
        scale = (MAX_SPEED / spd).clamp(max=1.0)
        cmd = t.stack([vx * scale, vy * scale, t.zeros_like(vx), yr], dim=1)

        vbody, yr_real = self.dyn.apply(cmd, DT)
        rvx, rvy = vbody[:, 0], vbody[:, 1]

        ax = (rvx - self.vx) / DT; ay = (rvy - self.vy) / DT
        jx = (ax - self.prev_ax) / DT; jy = (ay - self.prev_ay) / DT
        jerk = t.sqrt(jx * jx + jy * jy)

        c, s = torch_cos(t, self.yaw), torch_sin(t, self.yaw)
        wind = self.dyn.wind
        self.x = self.x + (c * rvx - s * rvy) * DT + wind[:, 0] * DT
        self.y = self.y + (s * rvx + c * rvy) * DT + wind[:, 1] * DT
        self.yaw = wrap_pi_t(t, self.yaw + yr_real * DT)
        self.vx, self.vy = rvx, rvy
        self.prev_ax, self.prev_ay = ax, ay
        self.yr = yr_real
        self.steps = self.steps + 1

        d_now = self.goal_dist()
        progress = d_prev - d_now
        box_dist = self.min_box_dist()
        collided = box_dist < COLLIDE_R
        reached = d_now < REACH
        timeout = self.steps >= MAX_STEPS

        near = (w.clear_margin - box_dist).clamp(min=0.0)
        speed_real = t.sqrt(rvx * rvx + rvy * rvy)
        stalled = (speed_real < w.stall_speed) & (d_now > REACH)
        timed_out_unreached = timeout & (~reached) & (~collided)

        reward = (w.k_prog * progress
                  - w.k_time
                  - w.k_jerk * jerk
                  - w.k_stall * stalled.float()
                  - w.k_clear * near
                  - w.k_coll * collided.float()
                  - w.k_timeout * timed_out_unreached.float()
                  + w.k_goal * reached.float())
        done = collided | reached | timeout
        return reward, done, {"reached": reached, "collided": collided}

    def reset_done(self, done):
        if done.any():
            idx = done.nonzero(as_tuple=True)[0]
            self.vx[idx] = 0; self.vy[idx] = 0
            self.prev_ax[idx] = 0; self.prev_ay[idx] = 0
            self.yr[idx] = 0
            self._new_episode(done)


# --------------------------------------------------------------------------- model
def build_ac(torch, hidden):
    nn = torch.nn

    class ActorCriticRNN(nn.Module):
        """Separate actor/critic GRUs with carried hidden state across timesteps."""
        def __init__(self):
            super().__init__()
            self.register_buffer("mean", torch.zeros(STATE_DIM))
            self.register_buffer("std", torch.ones(STATE_DIM))
            self.gru = nn.GRU(STATE_DIM, hidden, batch_first=True)
            self.head = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(),
                                      nn.Linear(hidden, ACTION_DIM))
            self.gru_v = nn.GRU(STATE_DIM, hidden, batch_first=True)
            self.value = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(),
                                       nn.Linear(hidden, 1))
            self.log_std = nn.Parameter(torch.full((ACTION_DIM,), -1.4))
            self.hidden = hidden

        def _norm(self, x):
            return (x - self.mean) / self.std

        def step(self, obs, h_a, h_v):
            xn = self._norm(obs).unsqueeze(1)
            oa, h_a = self.gru(xn, h_a)
            ov, h_v = self.gru_v(xn, h_v)
            return self.head(oa[:, 0]), self.value(ov[:, 0]).squeeze(-1), h_a, h_v

    return ActorCriticRNN()


# --------------------------------------------------------------------------- training
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bc-ckpt", default=None,
                    help="optional: warm-start from an existing RNN checkpoint")
    ap.add_argument("--out", required=True)
    ap.add_argument("--version", default="rover_v1")
    ap.add_argument("--hidden", type=int, default=256,
                    help="GRU hidden size (ignored if --bc-ckpt given)")
    ap.add_argument("--iters", type=int, default=600)
    ap.add_argument("--envs", type=int, default=256)
    ap.add_argument("--rollout", type=int, default=64)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--gamma", type=float, default=0.99)
    ap.add_argument("--lam", type=float, default=0.95)
    ap.add_argument("--clip", type=float, default=0.2)
    ap.add_argument("--k-prog", type=float, default=1.5)
    ap.add_argument("--k-time", type=float, default=0.05)
    ap.add_argument("--k-jerk", type=float, default=0.005)
    ap.add_argument("--k-coll", type=float, default=25.0)
    ap.add_argument("--k-goal", type=float, default=40.0)
    ap.add_argument("--k-stall", type=float, default=0.4)
    ap.add_argument("--k-timeout", type=float, default=12.0)
    ap.add_argument("--stall-speed", type=float, default=0.3)
    ap.add_argument("--k-alt", type=float, default=0.0,
                    help="unused for rover; kept so W() is happy")
    ap.add_argument("--k-clear", type=float, default=0.6)
    ap.add_argument("--clear-margin", type=float, default=0.6)
    ap.add_argument("--k-above", type=float, default=0.0)
    ap.add_argument("--above-margin", type=float, default=1.0)
    ap.add_argument("--depth-noise", type=float, default=0.07)
    ap.add_argument("--target-noise", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dr-hold-frac", type=float, default=0.30)
    ap.add_argument("--dr-full-frac", type=float, default=0.75)
    args = ap.parse_args()
    w = W(args)

    torch = _torch()
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(args.seed)

    if args.bc_ckpt:
        ckpt = torch.load(args.bc_ckpt, map_location=dev, weights_only=False)
        hidden = ckpt["hidden"]
        ac = build_ac(torch, hidden).to(dev)
        ac.load_state_dict(ckpt["state_dict"], strict=False)
        mean_np = ckpt.get("mean", np.zeros(STATE_DIM, dtype=np.float32))
        std_np = ckpt.get("std", np.ones(STATE_DIM, dtype=np.float32))
        print(f"warm-started from {args.bc_ckpt} (hidden={hidden})", flush=True)
    else:
        hidden = args.hidden
        ac = build_ac(torch, hidden).to(dev)
        mean_np = np.zeros(STATE_DIM, dtype=np.float32)
        std_np = np.ones(STATE_DIM, dtype=np.float32)
        print(f"training rover policy from scratch (hidden={hidden})", flush=True)

    print(f"params={sum(p.numel() for p in ac.parameters())}  device={dev}", flush=True)

    opt = torch.optim.Adam(ac.parameters(), lr=args.lr)
    env = RoverEnv(torch, args.envs, dev, seed=args.seed,
                   depth_noise=args.depth_noise, target_noise=args.target_noise)
    n = args.envs
    h_a = torch.zeros(1, n, hidden, device=dev)
    h_v = torch.zeros(1, n, hidden, device=dev)

    def dr_schedule(it):
        hold = args.dr_hold_frac * args.iters
        full = args.dr_full_frac * args.iters
        if it <= hold:
            return 0.0
        if it >= full:
            return 1.0
        return (it - hold) / max(1.0, full - hold)

    hist = []
    best_reach, best_sd = -1.0, None
    for it in range(args.iters):
        env.dyn.set_scale(dr_schedule(it))
        T = args.rollout
        obs_b = torch.zeros(T, n, STATE_DIM, device=dev)
        act_b = torch.zeros(T, n, ACTION_DIM, device=dev)
        logp_b = torch.zeros(T, n, device=dev)
        val_b = torch.zeros(T, n, device=dev)
        rew_b = torch.zeros(T, n, device=dev)
        done_b = torch.zeros(T, n, device=dev)
        h_a0, h_v0 = h_a.detach().clone(), h_v.detach().clone()

        ep_reach = ep_coll = ep_cnt = ep_ret = 0.0
        for t in range(T):
            o = env.obs()
            with torch.no_grad():
                mean, value, h_a, h_v = ac.step(o, h_a, h_v)
                std = ac.log_std.exp()
                dist_ = torch.distributions.Normal(mean, std)
                act = dist_.sample()
                logp = dist_.log_prob(act).sum(-1)
            rew, done, info = env.step(act, w)
            obs_b[t] = o; act_b[t] = act; logp_b[t] = logp
            val_b[t] = value; rew_b[t] = rew; done_b[t] = done.float()
            ep_reach += float(info["reached"].sum())
            ep_coll += float(info["collided"].sum())
            ep_cnt += float(done.sum())
            ep_ret += float(rew.sum())
            nd = (1.0 - done.float()).view(1, n, 1)
            h_a = h_a * nd; h_v = h_v * nd
            env.reset_done(done)

        with torch.no_grad():
            o = env.obs()
            _, last_val, _, _ = ac.step(o, h_a, h_v)

        # GAE
        adv = torch.zeros(T, n, device=dev)
        lastgae = torch.zeros(n, device=dev)
        for tt in reversed(range(T)):
            nextval = last_val if tt == T - 1 else val_b[tt + 1]
            nonterm = 1.0 - done_b[tt]
            delta = rew_b[tt] + args.gamma * nextval * nonterm - val_b[tt]
            lastgae = delta + args.gamma * args.lam * nonterm * lastgae
            adv[tt] = lastgae
        ret = adv + val_b
        adv = (adv - adv.mean()) / (adv.std() + 1e-8)

        def replay():
            ha, hv = h_a0.clone(), h_v0.clone()
            means = torch.zeros(T, n, ACTION_DIM, device=dev)
            vals = torch.zeros(T, n, device=dev)
            for t in range(T):
                if t > 0:
                    nd = (1.0 - done_b[t - 1]).view(1, n, 1)
                    ha = ha * nd; hv = hv * nd
                m, v, ha, hv = ac.step(obs_b[t], ha, hv)
                means[t] = m; vals[t] = v
            return means, vals

        for _ in range(args.epochs):
            means, vals = replay()
            std = ac.log_std.exp()
            dist_ = torch.distributions.Normal(means, std)
            logp = dist_.log_prob(act_b).sum(-1)
            ratio = (logp - logp_b).exp()
            s1 = ratio * adv
            s2 = torch.clamp(ratio, 1 - args.clip, 1 + args.clip) * adv
            pg = -torch.min(s1, s2).mean()
            vl = 0.5 * (vals - ret).pow(2).mean()
            ent = dist_.entropy().sum(-1).mean()
            loss = pg + 0.5 * vl - 0.005 * ent
            opt.zero_grad(); loss.backward()
            torch.nn.utils.clip_grad_norm_(ac.parameters(), 0.5)
            opt.step()

        h_a, h_v = h_a.detach(), h_v.detach()
        reach_rate = ep_reach / max(ep_cnt, 1)
        coll_rate = ep_coll / max(ep_cnt, 1)
        hist.append({"iter": it, "reach": reach_rate, "coll": coll_rate})
        if env.dyn.scale >= 0.8 and reach_rate > best_reach:
            best_reach = reach_rate
            best_sd = {k: v.detach().clone() for k, v in ac.state_dict().items()}
        if it % 10 == 0 or it == args.iters - 1:
            print(f"it {it:3d} drs={env.dyn.scale:.2f} reach={reach_rate:.2f} "
                  f"coll={coll_rate:.2f} ret/env={ep_ret/n:7.2f} "
                  f"std={ac.log_std.exp().mean().item():.2f}", flush=True)

    if best_sd is not None:
        ac.load_state_dict(best_sd)
        print(f"exporting BEST DR-robust checkpoint (reach={best_reach:.2f})", flush=True)

    nn = torch.nn

    class StepExport(nn.Module):
        def __init__(self, ac):
            super().__init__()
            self.mean = ac.mean; self.std = ac.std
            self.gru = ac.gru; self.head = ac.head

        def forward(self, state, h_in):
            xn = (state - self.mean) / self.std
            out, hn = self.gru(xn, h_in)
            return self.head(out[:, -1, :]), hn

    out_dir = Path(args.out).expanduser(); out_dir.mkdir(parents=True, exist_ok=True)
    step_export = StepExport(ac).to(dev).eval()
    onnx_path = out_dir / f"policy_{args.version}.onnx"
    ds = torch.zeros(1, 1, STATE_DIM, device=dev)
    dh = torch.zeros(1, 1, hidden, device=dev)
    torch.onnx.export(step_export, (ds, dh), str(onnx_path),
                      input_names=["state", "h_in"], output_names=["action", "h_out"],
                      opset_version=17)
    np.save(out_dir / f"policy_{args.version}_state_norm.npy",
            np.stack([mean_np, std_np]).astype(np.float32))
    torch.save({"state_dict": ac.state_dict(), "hidden": hidden,
                "mean": mean_np, "std": std_np, "recurrent": True},
               out_dir / f"policy_{args.version}_ac.pt")
    summary = {"version": args.version, "recurrent": True, "vehicle": "rover",
               "iters": args.iters, "final": hist[-1] if hist else None,
               "onnx": str(onnx_path), "onnx_bytes": onnx_path.stat().st_size}
    (out_dir / f"policy_{args.version}_rl_summary.json").write_text(
        json.dumps(summary, indent=2))
    print(json.dumps(summary["final"], indent=2), flush=True)


if __name__ == "__main__":
    main()
