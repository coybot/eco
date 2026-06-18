"""PPO fine-tune of the BC policy with a collision/time/smoothness reward (plan Phase 2).

BC only imitates the expert's *actions* — it has no notion of "a collision is bad" or "reach
the goal sooner." This stage warm-starts from the BC policy and optimizes an explicit reward:

    reward = K_PROG * progress_toward_goal
           - K_TIME                       (per-tick: rewards reaching sooner)
           - K_JERK * ||jerk||            (keeps motion smooth / analog)
           - K_COLL  on collision  (episode ends)
           + K_GOAL  on reaching   (episode ends)

Collisions and time are weighted heavily per request. The environment is the same kinematic
box-world the BC data came from (obstacle avoidance is geometric, so kinematics suffice here),
vectorized in torch on the GPU. The exported artifact is the SAME policy_v3.onnx interface —
the mean (deterministic) network — so the Orin deploy path is unchanged.

Run on hoopoe:
    PYTHONPATH=. python -m eco.drone.training.train_rl \
        --bc-ckpt ~/drone-data/bc_v3/models/policy_v3.pt \
        --out ~/drone-data/bc_v3/models --version v3 \
        --iters 300 --envs 512 --k-coll 25 --k-time 0.05 --k-prog 1.5
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .contract import (STATE_DIM, ACTION_DIM, DEPTH_RAYS, RAY_DIRS, DEPTH_MAX,
                       ACTION_FIELDS)
from .dynamics import DynamicsDR

SEQ_LEN = 16
DT = 0.1
MAX_STEPS = 250          # per-episode cap (25 s)
REACH = 1.0
COLLIDE_R = 0.3
MAX_SPEED = 3.0


def _torch():
    import torch
    return torch


# --------------------------------------------------------------------------- env
class BoxEnv:
    """Vectorized kinematic quad env with random box obstacles, all in torch."""

    def __init__(self, torch, n, dev, k_boxes=8, seed=0):
        self.t = torch
        self.n = n
        self.dev = dev
        self.K = k_boxes
        rd = torch.tensor(RAY_DIRS, device=dev, dtype=torch.float32)  # (R,3) fwd,left,up
        self.ray_fwd = rd[:, 0]; self.ray_left = rd[:, 1]; self.ray_up = rd[:, 2]
        self.g = torch.Generator(device="cpu").manual_seed(seed)
        self.reset_all()

    def _rand(self, *shape, lo=0.0, hi=1.0):
        t = self.t
        return (lo + (hi - lo) * t.rand(*shape, generator=self.g)).to(self.dev)

    def reset_all(self):
        t = self.t
        z = t.zeros(self.n, device=self.dev)
        self.x = z.clone(); self.y = z.clone(); self.z = self._rand(self.n, lo=1.5, hi=3.0)
        self.gx = z.clone(); self.gy = z.clone(); self.galt = z.clone(); self.yaw = z.clone()
        self.bcx = t.zeros(self.n, self.K, device=self.dev)
        self.bcy = t.zeros(self.n, self.K, device=self.dev)
        self.bcz = t.zeros(self.n, self.K, device=self.dev)
        self.bhx = t.zeros(self.n, self.K, device=self.dev)
        self.bhy = t.zeros(self.n, self.K, device=self.dev)
        self.bhz = t.zeros(self.n, self.K, device=self.dev)
        self.bmask = t.zeros(self.n, self.K, device=self.dev)
        self.vx = z.clone(); self.vy = z.clone(); self.vz = z.clone()
        self.prev_ax = z.clone(); self.prev_ay = z.clone(); self.prev_az = z.clone()
        self.yr = z.clone()
        self.steps = z.clone()
        self.win = None
        self.dyn = DynamicsDR(t, self.n, self.dev, self._rand)
        self._new_episode(t.ones(self.n, device=self.dev, dtype=t.bool))

    def _new_episode(self, mask):
        """(Re)initialize pose/goal/boxes for envs where mask is True (velocities zeroed by caller)."""
        t = self.t
        n = int(mask.sum().item())
        if n == 0:
            return
        idx = mask.nonzero(as_tuple=True)[0]
        # Goals lie along +x (lateral/vertical offset) so axis-aligned wall/window obstacles sit
        # perpendicular to the path; initial heading randomized (turn-then-go robustness).
        gx = self._rand(n, lo=6.0, hi=15.0)
        gy = self._rand(n, lo=-3.0, hi=3.0)
        gz = self._rand(n, lo=-2.5, hi=2.5)
        self.x[idx] = 0.0
        self.y[idx] = 0.0
        self.gx[idx] = gx
        self.gy[idx] = gy
        galt = (self.z[idx] + gz).clamp(min=0.6)
        self.galt[idx] = galt
        aligned = self._rand(n, lo=-0.6, hi=0.6)
        full = self._rand(n, lo=-math.pi, hi=math.pi)
        self.yaw[idx] = t.where(self._rand(n) < 0.6, aligned, full)

        z0 = self.z[idx]
        band_lo = t.minimum(z0, galt)
        band_hi = t.maximum(z0, galt)
        SPAN, TALL, HXW = 8.0, 3.5, 0.5

        def set_barrier(m, slot, cx, force_over=None):
            """Wide corridor-spanning over/under wall into `slot` for env-subset mask m.
            force_over: None=random, True=floor wall (over), False=ceiling (under)."""
            gi = idx[m]
            over = (self._rand(n) < 0.5) if force_over is None else \
                   (self._rand(n) < (1.0 if force_over else 0.0))
            top = band_hi + self._rand(n, lo=0.4, hi=1.1)
            botc = self._rand(n, lo=1.3, hi=1.9)
            topc = botc + self._rand(n, lo=2.0, hi=3.5)
            cz = t.where(over, top / 2, (botc + topc) / 2)
            hz = t.where(over, top / 2, (topc - botc) / 2)
            self.bcx[gi, slot] = cx[m]; self.bcy[gi, slot] = 0.0
            self.bcz[gi, slot] = cz[m]; self.bhx[gi, slot] = 0.6
            self.bhy[gi, slot] = SPAN / 2; self.bhz[gi, slot] = hz[m]
            self.bmask[gi, slot] = 1.0

        def set_window(m, s0, cx):
            """Full wall + central opening across slots [s0..s0+3] for env-subset mask m."""
            gi = idx[m]
            cyo = self._rand(n, lo=-1.0, hi=1.0)
            czo = (band_lo + (band_hi - band_lo) * self._rand(n)).clamp(1.5, 3.3)
            ohy = self._rand(n, lo=0.95, hi=1.3)
            ohz = self._rand(n, lo=0.85, hi=1.15)
            cy = [(-SPAN + (cyo - ohy)) / 2, ((cyo + ohy) + SPAN) / 2, cyo, cyo]
            hy = [((cyo - ohy) + SPAN) / 2, (SPAN - (cyo + ohy)) / 2, ohy, ohy]
            cz = [czo, czo, (czo - ohz) / 2, czo + ohz + TALL / 2]
            hz = [czo * 0 + TALL, czo * 0 + TALL, (czo - ohz) / 2, czo * 0 + TALL / 2]
            for k in range(4):
                self.bcx[gi, s0 + k] = cx[m]; self.bcy[gi, s0 + k] = cy[k][m]
                self.bcz[gi, s0 + k] = cz[k][m]; self.bhx[gi, s0 + k] = HXW
                self.bhy[gi, s0 + k] = hy[k][m]; self.bhz[gi, s0 + k] = hz[k][m]
                self.bmask[gi, s0 + k] = 1.0

        def mask_off(m, slots):
            for k in slots:
                self.bmask[idx[m], k] = 0.0

        mode = self._rand(n)                       # <.16 seq, <.38 window, <.60 barrier, else scattered
        # ---- default: scattered prisms (bob & weave) across all slots ----
        for k in range(self.K):
            self.bcx[idx, k] = gx * self._rand(n, lo=0.25, hi=0.85)
            self.bcy[idx, k] = self._rand(n, lo=-2.0, hi=2.0)
            self.bcz[idx, k] = (band_lo + (band_hi - band_lo) * self._rand(n)
                                + self._rand(n, lo=-1.0, hi=1.0)).clamp(min=0.4)
            self.bhx[idx, k] = self._rand(n, lo=0.3, hi=1.0)
            self.bhy[idx, k] = self._rand(n, lo=0.3, hi=1.2)
            self.bhz[idx, k] = self._rand(n, lo=0.3, hi=1.2)
            self.bmask[idx, k] = (self._rand(n) > 0.12).float()

        # SEQUENCE: the hard chain UNDER -> OVER -> THROUGH (the gauntlet pattern), so the big
        # duck->climb vertical swing right before a window is in-distribution.
        seq = mode < 0.18
        set_barrier(seq, 0, gx * self._rand(n, lo=0.20, hi=0.34), force_over=False)   # under
        set_barrier(seq, 1, gx * self._rand(n, lo=0.42, hi=0.56), force_over=True)     # over
        set_window(seq, 2, gx * self._rand(n, lo=0.66, hi=0.82))                       # through
        mask_off(seq, [6, 7])

        win = (mode >= 0.18) & (mode < 0.40)        # single WINDOW wall
        set_window(win, 0, gx * self._rand(n, lo=0.4, hi=0.65))
        mask_off(win, [4, 5, 6, 7])

        barr = (mode >= 0.40) & (mode < 0.62)       # single wide BARRIER
        set_barrier(barr, 0, gx * self._rand(n, lo=0.4, hi=0.65))
        mask_off(barr, list(range(1, self.K)))

        self.steps[idx] = 0.0
        self.dyn.randomize(mask)   # fresh DR dynamics params + zeroed realized state

    def depth_grid(self):
        """(n, R) forward ranges via batched 3D ray-AABB. Rays = camera grid rotated by yaw."""
        t = self.t
        eps = 1e-6
        c, s = torch_cos(t, self.yaw), torch_sin(t, self.yaw)        # (n,)
        fwd, left, up = self.ray_fwd, self.ray_left, self.ray_up     # (R,)
        # world ray directions per env (n,R)
        dvx = c[:, None] * fwd[None, :] - s[:, None] * left[None, :]
        dvy = s[:, None] * fwd[None, :] + c[:, None] * left[None, :]
        dvz = up[None, :].expand(self.n, -1)

        def slab(d, o, lo, hi):                              # d (n,R,1), o (n,1,1), lo/hi (n,1,K)
            par = d.abs() < eps
            ds = t.where(par, t.full_like(d, eps), d)
            t1 = (lo - o) / ds
            t2 = (hi - o) / ds
            tmin = t.minimum(t1, t2)
            tmax = t.maximum(t1, t2)
            inside = (o >= lo) & (o <= hi)
            big = t.full_like(tmin, 1e9)
            tmin = t.where(par, t.where(inside, -big, big), tmin)
            tmax = t.where(par, t.where(inside, big, -big), tmax)
            return tmin, tmax

        tnx, txx = slab(dvx[:, :, None], self.x[:, None, None],
                        (self.bcx - self.bhx)[:, None, :], (self.bcx + self.bhx)[:, None, :])
        tny, txy = slab(dvy[:, :, None], self.y[:, None, None],
                        (self.bcy - self.bhy)[:, None, :], (self.bcy + self.bhy)[:, None, :])
        tnz, txz = slab(dvz[:, :, None], self.z[:, None, None],
                        (self.bcz - self.bhz)[:, None, :], (self.bcz + self.bhz)[:, None, :])
        tmin = t.maximum(t.maximum(tnx, tny), tnz)
        tmax = t.minimum(t.minimum(txx, txy), txz)
        hit = (tmax >= tmin) & (tmax >= 0) & (self.bmask[:, None, :] > 0.5)
        tcand = t.where(tmin >= 0, tmin, t.zeros_like(tmin))
        dist = t.where(hit, tcand, t.full_like(tcand, DEPTH_MAX))
        return dist.min(dim=2).values.clamp(0.0, DEPTH_MAX)     # (n,R)

    def min_box_dist(self):
        t = self.t
        ddx = ((self.x[:, None] - self.bcx).abs() - self.bhx).clamp(min=0.0)
        ddy = ((self.y[:, None] - self.bcy).abs() - self.bhy).clamp(min=0.0)
        ddz = ((self.z[:, None] - self.bcz).abs() - self.bhz).clamp(min=0.0)
        d = t.sqrt(ddx * ddx + ddy * ddy + ddz * ddz)
        d = t.where(self.bmask > 0.5, d, t.full_like(d, 1e9))
        return d.min(dim=1).values

    def obs(self):
        """(n, STATE_DIM) raw state matching contract order."""
        t = self.t
        dx, dy, dz = self.gx - self.x, self.gy - self.y, self.galt - self.z
        c, s = torch_cos(t, -self.yaw), torch_sin(t, -self.yaw)
        tf = c * dx - s * dy
        tl = s * dx + c * dy
        tu = dz
        dist = t.sqrt(tf * tf + tl * tl + tu * tu)
        yaw_err = torch_atan2(t, tl, tf)
        veh = t.zeros_like(tf)
        base = t.stack([tf, tl, tu, dist, self.vx, self.vy, self.vz,
                        yaw_err, self._yaw_rate(), self.z, veh], dim=1)  # (n,11)
        grid = self.depth_grid()
        return t.cat([base, grid], dim=1)

    def _yaw_rate(self):
        if not hasattr(self, "yr"):
            self.yr = self.t.zeros(self.n, device=self.dev)
        return self.yr

    def goal_dist(self):
        t = self.t
        return t.sqrt((self.gx - self.x) ** 2 + (self.gy - self.y) ** 2 + (self.galt - self.z) ** 2)

    def step(self, action, w):
        """action (n,4) body-frame [vx,vy,vz,yaw_rate]. Returns reward (n,), done (n,)."""
        t = self.t
        d_prev = self.goal_dist()
        vx, vy, vz, yr = action[:, 0], action[:, 1], action[:, 2], action[:, 3]
        # clamp commanded translational speed to MAX_SPEED
        spd = t.sqrt(vx * vx + vy * vy + vz * vz).clamp(min=1e-6)
        scale = (MAX_SPEED / spd).clamp(max=1.0)
        cmd = t.stack([vx * scale, vy * scale, vz * scale, yr], dim=1)

        # DR dynamics: realize the command through lag/accel-cap/latency; add world wind.
        vbody, yr_real = self.dyn.apply(cmd, DT)
        rvx, rvy, rvz = vbody[:, 0], vbody[:, 1], vbody[:, 2]

        # jerk (smoothness) on the REALIZED velocity
        ax = (rvx - self.vx) / DT; ay = (rvy - self.vy) / DT; az = (rvz - self.vz) / DT
        jx = (ax - self.prev_ax) / DT; jy = (ay - self.prev_ay) / DT; jz = (az - self.prev_az) / DT
        jerk = t.sqrt(jx * jx + jy * jy + jz * jz)

        # integrate realized body velocity + wind (world frame)
        c, s = torch_cos(t, self.yaw), torch_sin(t, self.yaw)
        wind = self.dyn.wind
        self.x = self.x + (c * rvx - s * rvy) * DT + wind[:, 0] * DT
        self.y = self.y + (s * rvx + c * rvy) * DT + wind[:, 1] * DT
        self.z = self.z + rvz * DT
        self.yaw = wrap_pi_t(t, self.yaw + yr_real * DT)
        self.vx, self.vy, self.vz = rvx, rvy, rvz
        self.prev_ax, self.prev_ay, self.prev_az = ax, ay, az
        self.yr = yr_real
        self.steps = self.steps + 1

        d_now = self.goal_dist()
        progress = d_prev - d_now
        collided = self.min_box_dist() < COLLIDE_R
        reached = d_now < REACH
        timeout = self.steps >= MAX_STEPS

        reward = (w.k_prog * progress
                  - w.k_time
                  - w.k_jerk * jerk
                  - w.k_coll * collided.float()
                  + w.k_goal * reached.float())
        done = collided | reached | timeout
        return reward, done, {"reached": reached, "collided": collided}

    def reset_done(self, done):
        if done.any():
            # fresh z for the reset envs, then reinit pose/goal/boxes
            idx = done.nonzero(as_tuple=True)[0]
            self.z[idx] = self._rand(len(idx), lo=1.5, hi=3.0)
            self.vx[idx] = 0; self.vy[idx] = 0; self.vz[idx] = 0
            self.prev_ax[idx] = 0; self.prev_ay[idx] = 0; self.prev_az[idx] = 0
            self.yr[idx] = 0 if hasattr(self, "yr") else 0
            self._new_episode(done)
            if self.win is not None:
                self.win[idx] = self.obs()[idx][:, None, :].repeat(1, SEQ_LEN, 1)

    def push_window(self, o):
        if self.win is None:
            self.win = o[:, None, :].repeat(1, SEQ_LEN, 1)
        else:
            self.win = self.t.cat([self.win[:, 1:, :], o[:, None, :]], dim=1)
        return self.win


def torch_cos(t, x): return t.cos(x)
def torch_sin(t, x): return t.sin(x)
def torch_atan2(t, a, b): return t.atan2(a, b)
def wrap_pi_t(t, a): return (a + math.pi) % (2 * math.pi) - math.pi


# --------------------------------------------------------------------------- model
def build_actor_critic(torch, hidden):
    nn = torch.nn

    class ActorCritic(nn.Module):
        """SEPARATE actor and critic trunks. A shared GRU lets large value-target gradients
        (goal +30 / collision -25) corrupt the policy features and causes run-to-run collapse;
        decoupling them makes PPO stable here. Actor (gru+head) warm-starts from BC; critic fresh.
        """
        def __init__(self):
            super().__init__()
            self.register_buffer("mean", torch.zeros(STATE_DIM))
            self.register_buffer("std", torch.ones(STATE_DIM))
            self.gru = nn.GRU(STATE_DIM, hidden, batch_first=True)            # actor trunk
            self.head = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(),
                                      nn.Linear(hidden, ACTION_DIM))
            self.gru_v = nn.GRU(STATE_DIM, hidden, batch_first=True)          # critic trunk (separate)
            self.value = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(),
                                       nn.Linear(hidden, 1))
            self.log_std = nn.Parameter(torch.full((ACTION_DIM,), -1.4))  # std~0.25: gentle fine-tune

        def _norm(self, x):
            return (x - self.mean) / self.std

        def forward(self, x):
            xn = self._norm(x)
            fa, _ = self.gru(xn)
            fv, _ = self.gru_v(xn)
            return self.head(fa[:, -1, :]), self.value(fv[:, -1, :]).squeeze(-1)

    return ActorCritic()


class W:  # reward weights
    def __init__(self, a):
        self.k_prog = a.k_prog
        self.k_time = a.k_time
        self.k_jerk = a.k_jerk
        self.k_coll = a.k_coll
        self.k_goal = a.k_goal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bc-ckpt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--version", default="v3")
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--envs", type=int, default=512)
    ap.add_argument("--rollout", type=int, default=64)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--minibatch", type=int, default=8192)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--gamma", type=float, default=0.99)
    ap.add_argument("--lam", type=float, default=0.95)
    ap.add_argument("--clip", type=float, default=0.2)
    ap.add_argument("--k-prog", type=float, default=1.5)
    ap.add_argument("--k-time", type=float, default=0.05)
    ap.add_argument("--k-jerk", type=float, default=0.005)
    ap.add_argument("--k-coll", type=float, default=25.0)
    ap.add_argument("--k-goal", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dr-hold-frac", type=float, default=0.35,
                    help="fraction of iters held at kinematic (scale 0) before DR ramps")
    ap.add_argument("--dr-full-frac", type=float, default=0.8,
                    help="fraction of iters by which DR reaches full scale 1.0")
    args = ap.parse_args()
    w = W(args)

    torch = _torch()
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(args.seed)

    ckpt = torch.load(args.bc_ckpt, map_location=dev, weights_only=False)  # contains numpy mean/std
    hidden = ckpt["hidden"]
    ac = build_actor_critic(torch, hidden).to(dev)
    ac.load_state_dict(ckpt["state_dict"], strict=False)   # gru+head+mean+std; value/log_std fresh
    print(f"warm-started actor from BC ({sum(p.numel() for p in ac.parameters())} params)")

    opt = torch.optim.Adam(ac.parameters(), lr=args.lr)
    env = BoxEnv(torch, args.envs, dev, seed=args.seed)
    o = env.obs(); win = env.push_window(o)

    def policy(win, sample=True):
        mean, value = ac(win)
        std = ac.log_std.exp()
        dist = torch.distributions.Normal(mean, std)
        act = dist.sample() if sample else mean
        logp = dist.log_prob(act).sum(-1)
        return act, logp, value

    def dr_schedule(it):
        # hold at kinematic (scale 0) until the policy masters the easy task, THEN ramp DR to
        # full. Injecting DR before the policy is solid destabilizes PPO into collapse.
        hold = args.dr_hold_frac * args.iters
        full = args.dr_full_frac * args.iters
        if it <= hold:
            return 0.0
        if it >= full:
            return 1.0
        return (it - hold) / max(1.0, full - hold)

    hist = []
    best_reach = -1.0
    best_ac = None
    for it in range(args.iters):
        env.dyn.set_scale(dr_schedule(it))
        obs_b, act_b, logp_b, val_b, rew_b, done_b = [], [], [], [], [], []
        ep_reach, ep_coll, ep_ret, ep_cnt = 0.0, 0.0, 0.0, 0
        for _ in range(args.rollout):
            with torch.no_grad():
                act, logp, value = policy(win)
            rew, done, info = env.step(act, w)
            obs_b.append(win.clone()); act_b.append(act); logp_b.append(logp)
            val_b.append(value); rew_b.append(rew); done_b.append(done.float())
            ep_reach += float(info["reached"].sum()); ep_coll += float(info["collided"].sum())
            ep_cnt += float(done.sum()); ep_ret += float(rew.sum())
            env.reset_done(done)
            o = env.obs(); win = env.push_window(o)
        with torch.no_grad():
            _, _, last_val = policy(win)

        # GAE
        T = args.rollout
        adv = torch.zeros(T, args.envs, device=dev)
        lastgae = torch.zeros(args.envs, device=dev)
        for tt in reversed(range(T)):
            nextval = last_val if tt == T - 1 else val_b[tt + 1]
            nonterm = 1.0 - done_b[tt]
            delta = rew_b[tt] + args.gamma * nextval * nonterm - val_b[tt]
            lastgae = delta + args.gamma * args.lam * nonterm * lastgae
            adv[tt] = lastgae
        val_stack = torch.stack(val_b)
        ret = adv + val_stack

        b_obs = torch.cat(obs_b).reshape(-1, SEQ_LEN, STATE_DIM)
        b_act = torch.cat(act_b).reshape(-1, ACTION_DIM)
        b_logp = torch.cat(logp_b).reshape(-1)
        b_adv = adv.reshape(-1)
        b_ret = ret.reshape(-1)
        b_adv = (b_adv - b_adv.mean()) / (b_adv.std() + 1e-8)

        N = b_obs.shape[0]
        for _ in range(args.epochs):
            perm = torch.randperm(N, device=dev)
            for st in range(0, N, args.minibatch):
                mb = perm[st:st + args.minibatch]
                mean, value = ac(b_obs[mb])
                std = ac.log_std.exp()
                dist = torch.distributions.Normal(mean, std)
                logp = dist.log_prob(b_act[mb]).sum(-1)
                ratio = (logp - b_logp[mb]).exp()
                s1 = ratio * b_adv[mb]
                s2 = torch.clamp(ratio, 1 - args.clip, 1 + args.clip) * b_adv[mb]
                pg = -torch.min(s1, s2).mean()
                vl = 0.5 * (value - b_ret[mb]).pow(2).mean()
                ent = dist.entropy().sum(-1).mean()
                loss = pg + 0.5 * vl - 0.005 * ent
                opt.zero_grad(); loss.backward()
                torch.nn.utils.clip_grad_norm_(ac.parameters(), 0.5)
                opt.step()

        reach_rate = ep_reach / max(ep_cnt, 1)
        coll_rate = ep_coll / max(ep_cnt, 1)
        hist.append({"iter": it, "reach": reach_rate, "coll": coll_rate,
                     "ret_per_env": ep_ret / args.envs})
        # keep the best DR-robust checkpoint (evaluated at high DR scale), so a late collapse
        # doesn't lose a good policy.
        if env.dyn.scale >= 0.8 and reach_rate > best_reach:
            best_reach = reach_rate
            best_ac = {k: v.detach().clone() for k, v in ac.state_dict().items()}
        if it % 10 == 0 or it == args.iters - 1:
            print(f"it {it:3d} drs={env.dyn.scale:.2f} reach={reach_rate:.2f} coll={coll_rate:.2f} "
                  f"ret/env={ep_ret/args.envs:7.2f} std={ac.log_std.exp().mean().item():.2f}",
                  flush=True)

    # restore the best DR-robust checkpoint for export (guards against a late collapse)
    if best_ac is not None:
        ac.load_state_dict(best_ac)
        print(f"exporting BEST DR-robust checkpoint (reach={best_reach:.2f} at full DR)", flush=True)
    else:
        print("no high-DR checkpoint beat baseline; exporting final", flush=True)

    # ---- export the deterministic mean net as policy_v3.onnx (BC-compatible) ----
    from .train import make_model
    out = Path(args.out).expanduser(); out.mkdir(parents=True, exist_ok=True)
    bc = make_model(torch, hidden=hidden).to(dev)
    sd = bc.state_dict()
    acsd = ac.state_dict()
    for k in sd:
        if k in acsd:
            sd[k] = acsd[k]
    bc.load_state_dict(sd)
    bc.eval()
    onnx_path = out / f"policy_{args.version}.onnx"
    dummy = torch.zeros(1, SEQ_LEN, STATE_DIM, device=dev)
    torch.onnx.export(bc, dummy, str(onnx_path),
                      input_names=["state_window"], output_names=["action"],
                      dynamic_axes={"state_window": {0: "batch"}, "action": {0: "batch"}},
                      opset_version=17)
    np.save(out / f"policy_{args.version}_state_norm.npy",
            np.stack([ckpt["mean"], ckpt["std"]]).astype(np.float32))
    # full actor-critic checkpoint so a later run can warm-start from this RL policy
    torch.save({"state_dict": ac.state_dict(), "hidden": hidden,
                "mean": ckpt["mean"], "std": ckpt["std"]}, out / f"policy_{args.version}_ac.pt")
    summary = {"version": args.version, "iters": args.iters, "envs": args.envs,
               "weights": vars(args), "final": hist[-1] if hist else None,
               "onnx": str(onnx_path), "onnx_bytes": onnx_path.stat().st_size}
    (out / f"policy_{args.version}_rl_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["final"], indent=2))


if __name__ == "__main__":
    main()
