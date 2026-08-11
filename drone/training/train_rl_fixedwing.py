"""PPO training for the fixed-wing mid-level pilot (Dubins-airplane kinematics).

Same STATE_DIM/ACTION_DIM (56 -> 4) and network shape as the quad (train_rl.py), so this reuses
`build_actor_critic` / `W` / the export path unchanged — only the *environment* differs:

  - Physical limits (cruise speed, stall margin, bank-limited turn rate, bounded climb angle) come
    straight from the FIXEDWING descriptor in vehicle_class.py, not duplicated here. This keeps
    the training env consistent with the same coordinated-turn integrator used in team_world.py /
    l5_core.py (KinematicWorld.integrate, Kinematics.COORDINATED_TURN_3D branch): forward speed
    is clamped to [min_speed_mps, max_speed_mps] (never zero/reverse — can't hover), yaw rate is
    bank-limited (falls off ~1/v), and climb is capped by a fixed flight-path angle.
  - Depth range is DEPTH_MAX_FW (fw_contract.py, 80 m) instead of the quad's 10 m — needed lookahead
    at 25 m/s cruise — and the obstacle course / goal distances are scaled to match (hundreds of
    metres, building-scale obstacles) rather than the quad's tight indoor gauntlet.
  - No BC warm-start: unlike train_rl.py (which fine-tunes an existing BC checkpoint), there is no
    fixed-wing expert/demo pipeline yet, so this trains the actor-critic from scratch with PPO
    against the same shaped reward (progress / time / jerk / collision / goal). If convergence is
    poor, a BC warm-start stage (mirroring train.py + expert.py for quad) is the natural next step.

Run on hoopoe:
    PYTHONPATH=. python -m drone.training.train_rl_fixedwing \
        --out ~/drone-data/fw_v1/models --version fw_v1 --iters 800 --envs 512
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .fw_contract import (STATE_DIM, ACTION_DIM, DEPTH_RAYS, RAY_DIRS,
                          VEHICLE_FIXEDWING, DEPTH_MAX_FW)
from .dynamics import DynamicsDR
from .train_rl import build_actor_critic, W, torch_cos, torch_sin, torch_atan2, wrap_pi_t

try:
    from ..common.vehicle_class import get_class
except ImportError:
    from common.vehicle_class import get_class

FW = get_class("fixedwing")   # single source of truth for physical limits

SEQ_LEN = 16
DT = 0.1
MAX_STEPS = 600          # per-episode cap (60 s) — enough to cover a few-hundred-metre course
REACH = 10.0             # m: arrival bubble (can't stop precisely at speed)
COLLIDE_R = FW.radius_m + 0.5
K_OBS = 6                # scattered building/terrain obstacles per episode
GOAL_FWD = (150.0, 400.0)     # m
GOAL_LATERAL = (-60.0, 60.0)  # m
ALT_BAND = (40.0, 100.0)      # m AGL, within FW.ceiling_m


def _torch():
    import torch
    return torch


class FixedWingEnv:
    """Vectorized coordinated-turn (Dubins-airplane) env with scattered box obstacles."""

    def __init__(self, torch, n, dev, k_boxes=K_OBS, seed=0, depth_noise=0.0, target_noise=0.0):
        self.t = torch
        self.n = n
        self.dev = dev
        self.K = k_boxes
        self.depth_noise = depth_noise
        self.target_noise = target_noise
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
        self.x = z.clone(); self.y = z.clone()
        self.z = self._rand(self.n, lo=ALT_BAND[0], hi=ALT_BAND[1])
        self.gx = z.clone(); self.gy = z.clone(); self.galt = z.clone(); self.yaw = z.clone()
        self.bcx = t.zeros(self.n, self.K, device=self.dev)
        self.bcy = t.zeros(self.n, self.K, device=self.dev)
        self.bcz = t.zeros(self.n, self.K, device=self.dev)
        self.bhx = t.zeros(self.n, self.K, device=self.dev)
        self.bhy = t.zeros(self.n, self.K, device=self.dev)
        self.bhz = t.zeros(self.n, self.K, device=self.dev)
        self.bmask = t.zeros(self.n, self.K, device=self.dev)
        self.vx = z.clone() + FW.min_speed_mps; self.vy = z.clone(); self.vz = z.clone()
        self.prev_ax = z.clone(); self.prev_ay = z.clone(); self.prev_az = z.clone()
        self.yr = z.clone()
        self.steps = z.clone()
        self.win = None
        self.dyn = DynamicsDR(t, self.n, self.dev, self._rand)
        self._new_episode(t.ones(self.n, device=self.dev, dtype=t.bool))

    def _new_episode(self, mask):
        t = self.t
        n = int(mask.sum().item())
        if n == 0:
            return
        idx = mask.nonzero(as_tuple=True)[0]
        gx = self._rand(n, lo=GOAL_FWD[0], hi=GOAL_FWD[1])
        gy = self._rand(n, lo=GOAL_LATERAL[0], hi=GOAL_LATERAL[1])
        galt = self._rand(n, lo=ALT_BAND[0], hi=ALT_BAND[1])
        self.x[idx] = 0.0
        self.y[idx] = 0.0
        self.gx[idx] = gx
        self.gy[idx] = gy
        self.galt[idx] = galt
        # initial heading roughly toward goal (a plane can't turn-in-place like a quad)
        self.yaw[idx] = torch_atan2(t, gy, gx) + self._rand(n, lo=-0.5, hi=0.5)

        # scattered building/terrain obstacles between start and goal
        for k in range(self.K):
            self.bcx[idx, k] = gx * self._rand(n, lo=0.2, hi=0.85)
            self.bcy[idx, k] = self._rand(n, lo=-40.0, hi=40.0)
            self.bcz[idx, k] = self._rand(n, lo=0.0, hi=60.0)   # ground-based obstacle height
            self.bhx[idx, k] = self._rand(n, lo=5.0, hi=20.0)
            self.bhy[idx, k] = self._rand(n, lo=5.0, hi=20.0)
            self.bhz[idx, k] = self._rand(n, lo=10.0, hi=60.0) / 2.0
            self.bmask[idx, k] = (self._rand(n) > 0.15).float()

        self.vx[idx] = FW.min_speed_mps * 0 + (FW.min_speed_mps + FW.max_speed_mps) / 2.0
        self.vy[idx] = 0.0
        self.vz[idx] = 0.0
        self.steps[idx] = 0.0
        self.dyn.randomize(mask)

    def depth_grid(self):
        """(n, R) forward ranges via batched 3D ray-AABB, clipped to DEPTH_MAX_FW."""
        t = self.t
        eps = 1e-6
        c, s = torch_cos(t, self.yaw), torch_sin(t, self.yaw)
        fwd, left, up = self.ray_fwd, self.ray_left, self.ray_up
        dvx = c[:, None] * fwd[None, :] - s[:, None] * left[None, :]
        dvy = s[:, None] * fwd[None, :] + c[:, None] * left[None, :]
        dvz = up[None, :].expand(self.n, -1)

        def slab(d, o, lo, hi):
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
        dist = t.where(hit, tcand, t.full_like(tcand, DEPTH_MAX_FW))
        return dist.min(dim=2).values.clamp(0.0, DEPTH_MAX_FW)

    def min_box_dist(self):
        t = self.t
        ddx = ((self.x[:, None] - self.bcx).abs() - self.bhx).clamp(min=0.0)
        ddy = ((self.y[:, None] - self.bcy).abs() - self.bhy).clamp(min=0.0)
        ddz = ((self.z[:, None] - self.bcz).abs() - self.bhz).clamp(min=0.0)
        d = t.sqrt(ddx * ddx + ddy * ddy + ddz * ddz)
        d = t.where(self.bmask > 0.5, d, t.full_like(d, 1e9))
        return d.min(dim=1).values

    def obs(self):
        """(n, STATE_DIM) raw state matching contract order, vehicle flag = VEHICLE_FIXEDWING."""
        t = self.t
        dx, dy, dz = self.gx - self.x, self.gy - self.y, self.galt - self.z
        c, s = torch_cos(t, -self.yaw), torch_sin(t, -self.yaw)
        tf = c * dx - s * dy
        tl = s * dx + c * dy
        tu = dz
        dist = t.sqrt(tf * tf + tl * tl + tu * tu)
        yaw_err = torch_atan2(t, tl, tf)
        veh = t.full_like(tf, VEHICLE_FIXEDWING)
        base = t.stack([tf, tl, tu, dist, self.vx, self.vy, self.vz,
                        yaw_err, self.yr, self.z, veh], dim=1)
        grid = self.depth_grid()
        if self.depth_noise > 0:
            grid = (grid + t.randn_like(grid) * self.depth_noise).clamp(0.0, DEPTH_MAX_FW)
        if self.target_noise > 0:
            base[:, 0:3] = base[:, 0:3] + t.randn_like(base[:, 0:3]) * self.target_noise
        return t.cat([base, grid], dim=1)

    def goal_dist(self):
        t = self.t
        return t.sqrt((self.gx - self.x) ** 2 + (self.gy - self.y) ** 2 + (self.galt - self.z) ** 2)

    def step(self, action, w):
        """action (n,4) body-frame [vx,vy(ignored),vz,yaw_rate]. Coordinated-turn realization
        matches KinematicWorld.integrate's COORDINATED_TURN_3D branch, then passed through the
        same DR velocity-tracking layer as the quad env."""
        t = self.t
        d_prev = self.goal_dist()

        v_cmd = action[:, 0].clamp(FW.min_speed_mps, FW.max_speed_mps)
        v_ref = FW.min_speed_mps
        yaw_limit = FW.max_yaw_rate_radps * (v_ref / v_cmd.clamp(min=v_ref))
        yr_cmd = t.max(t.min(action[:, 3], yaw_limit), -yaw_limit)
        climb_cap = v_cmd * math.tan(FW.max_climb_angle_rad)
        vz_cmd = t.max(t.min(action[:, 2], climb_cap), -climb_cap)
        cmd = t.stack([v_cmd, t.zeros_like(v_cmd), vz_cmd, yr_cmd], dim=1)

        vbody, yr_real = self.dyn.apply(cmd, DT)
        rvx, rvy, rvz = vbody[:, 0], vbody[:, 1], vbody[:, 2]

        ax = (rvx - self.vx) / DT; ay = (rvy - self.vy) / DT; az = (rvz - self.vz) / DT
        jx = (ax - self.prev_ax) / DT; jy = (ay - self.prev_ay) / DT; jz = (az - self.prev_az) / DT
        jerk = t.sqrt(jx * jx + jy * jy + jz * jz)

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
        box_dist = self.min_box_dist()
        collided = box_dist < COLLIDE_R
        reached = d_now < REACH
        timeout = self.steps >= MAX_STEPS

        near = (w.clear_margin - box_dist).clamp(min=0.0)
        speed_real = t.sqrt(rvx * rvx + rvy * rvy + rvz * rvz)
        stalled = speed_real < w.stall_speed
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
            self.z[idx] = self._rand(len(idx), lo=ALT_BAND[0], hi=ALT_BAND[1])
            self.prev_ax[idx] = 0; self.prev_ay[idx] = 0; self.prev_az[idx] = 0
            self._new_episode(done)
            if self.win is not None:
                self.win[idx] = self.obs()[idx][:, None, :].repeat(1, SEQ_LEN, 1)

    def push_window(self, o):
        if self.win is None:
            self.win = o[:, None, :].repeat(1, SEQ_LEN, 1)
        else:
            self.win = self.t.cat([self.win[:, 1:, :], o[:, None, :]], dim=1)
        return self.win


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--version", default="fw_v1")
    ap.add_argument("--iters", type=int, default=800)
    ap.add_argument("--envs", type=int, default=512)
    ap.add_argument("--rollout", type=int, default=64)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--minibatch", type=int, default=8192)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--gamma", type=float, default=0.99)
    ap.add_argument("--lam", type=float, default=0.95)
    ap.add_argument("--clip", type=float, default=0.2)
    ap.add_argument("--k-prog", type=float, default=0.3)
    ap.add_argument("--k-time", type=float, default=0.05)
    ap.add_argument("--k-jerk", type=float, default=0.005)
    ap.add_argument("--k-coll", type=float, default=25.0)
    ap.add_argument("--k-goal", type=float, default=30.0)
    ap.add_argument("--k-stall", type=float, default=0.4)
    ap.add_argument("--stall-speed", type=float, default=FW.min_speed_mps * 0.9,
                    help="realized speed (m/s) below which the plane counts as stalled")
    ap.add_argument("--k-timeout", type=float, default=12.0)
    ap.add_argument("--k-clear", type=float, default=0.4)
    ap.add_argument("--clear-margin", type=float, default=10.0,
                    help="obstacle clearance buffer (m) — larger than the quad's given cruise speed")
    ap.add_argument("--k-alt", type=float, default=0.0)
    ap.add_argument("--depth-noise", type=float, default=0.5)
    ap.add_argument("--target-noise", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dr-hold-frac", type=float, default=0.35)
    ap.add_argument("--dr-full-frac", type=float, default=0.8)
    args = ap.parse_args()
    w = W(args)

    torch = _torch()
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(args.seed)

    ac = build_actor_critic(torch, args.hidden).to(dev)
    print(f"fresh actor-critic ({sum(p.numel() for p in ac.parameters())} params), no BC warm-start")

    opt = torch.optim.Adam(ac.parameters(), lr=args.lr)
    env = FixedWingEnv(torch, args.envs, dev, seed=args.seed,
                       depth_noise=args.depth_noise, target_noise=args.target_noise)
    o = env.obs(); win = env.push_window(o)

    def policy(win, sample=True):
        mean, value = ac(win)
        std = ac.log_std.exp()
        dist = torch.distributions.Normal(mean, std)
        act = dist.sample() if sample else mean
        logp = dist.log_prob(act).sum(-1)
        return act, logp, value

    def dr_schedule(it):
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
        if env.dyn.scale >= 0.8 and reach_rate > best_reach:
            best_reach = reach_rate
            best_ac = {k: v.detach().clone() for k, v in ac.state_dict().items()}
        if it % 10 == 0 or it == args.iters - 1:
            print(f"it {it:3d} drs={env.dyn.scale:.2f} reach={reach_rate:.2f} coll={coll_rate:.2f} "
                  f"ret/env={ep_ret/args.envs:7.2f} std={ac.log_std.exp().mean().item():.2f}",
                  flush=True)

    if best_ac is not None:
        ac.load_state_dict(best_ac)
        print(f"exporting BEST DR-robust checkpoint (reach={best_reach:.2f} at full DR)", flush=True)
    else:
        print("no high-DR checkpoint beat baseline; exporting final", flush=True)

    out = Path(args.out).expanduser(); out.mkdir(parents=True, exist_ok=True)
    ac.eval()
    onnx_path = out / f"policy_{args.version}.onnx"
    dummy = torch.zeros(1, SEQ_LEN, STATE_DIM, device=dev)

    class _MeanOnly(torch.nn.Module):
        """Wraps ActorCritic to export only the deterministic mean action (matches policy_fw.onnx
        interface expected by vehicle_class.py / reactive_planner.py: state window -> action)."""
        def __init__(self, ac):
            super().__init__()
            self.ac = ac

        def forward(self, x):
            mean, _ = self.ac(x)
            return mean

    torch.onnx.export(_MeanOnly(ac), dummy, str(onnx_path),
                      input_names=["state_window"], output_names=["action"],
                      dynamic_axes={"state_window": {0: "batch"}, "action": {0: "batch"}},
                      opset_version=17)
    torch.save({"state_dict": ac.state_dict(), "hidden": args.hidden},
              out / f"policy_{args.version}_ac.pt")
    summary = {"version": args.version, "iters": args.iters, "envs": args.envs,
               "weights": vars(args), "final": hist[-1] if hist else None,
               "onnx": str(onnx_path), "onnx_bytes": onnx_path.stat().st_size}
    (out / f"policy_{args.version}_rl_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["final"], indent=2))


if __name__ == "__main__":
    main()
