"""Recurrent PPO fine-tune: carries a GRU hidden state across the episode (no fixed window).

Reuses the vectorized BoxEnv from train_rl.py (same obstacles / reward shaping / DR / altitude cap)
but the policy keeps a per-env hidden state threaded across timesteps instead of re-reading a
16-frame window. Warm-starts the actor from a recurrent BC checkpoint (train_rnn.py). Exports the
same STEP-mode ONNX as train_rnn: (state, h_in) -> (action, h_out), so the carry-state deploy path
(LearnedPlanner, auto-detected) is unchanged.

PPO over recurrence (simple correct form): collect a rollout storing per-step obs/actions/logp/
value/reward/done plus the hidden state at rollout start; for each PPO epoch, REPLAY the GRU over
the whole rollout from that start hidden with a manual per-timestep loop that ZEROS the hidden at
done boundaries — no minibatch-chunk bookkeeping. Separate actor/critic GRUs (a shared trunk
collapses PPO here).

Run on hoopoe:
    PYTHONPATH=. python -m drone.training.train_rl_rnn \
        --bc-ckpt ~/drone-data/bc_v12/models/policy_v14rnn.pt \
        --out ~/drone-data/bc_v12/models --version v16rnn_dr --iters 800 --envs 384
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from drone.common.contract import STATE_DIM, ACTION_DIM
from .train_rl import BoxEnv, W, DT, MAX_SPEED


def _torch():
    import torch
    return torch


def build_ac(torch, hidden):
    nn = torch.nn

    class ActorCriticRNN(nn.Module):
        """Separate actor/critic GRUs with carried hidden state. Actor warm-starts from BC."""
        def __init__(self):
            super().__init__()
            self.register_buffer("mean", torch.zeros(STATE_DIM))
            self.register_buffer("std", torch.ones(STATE_DIM))
            self.gru = nn.GRU(STATE_DIM, hidden, batch_first=True)            # actor
            self.head = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(),
                                      nn.Linear(hidden, ACTION_DIM))
            self.gru_v = nn.GRU(STATE_DIM, hidden, batch_first=True)          # critic
            self.value = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(),
                                       nn.Linear(hidden, 1))
            self.log_std = nn.Parameter(torch.full((ACTION_DIM,), -1.4))
            self.hidden = hidden

        def _norm(self, x):
            return (x - self.mean) / self.std

        def step(self, obs, h_a, h_v):
            """One tick. obs (n,STATE); h_* (1,n,hidden) -> mean (n,A), value (n), h_a', h_v'."""
            xn = self._norm(obs).unsqueeze(1)            # (n,1,STATE)
            oa, h_a = self.gru(xn, h_a)
            ov, h_v = self.gru_v(xn, h_v)
            return self.head(oa[:, 0]), self.value(ov[:, 0]).squeeze(-1), h_a, h_v

    return ActorCriticRNN()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bc-ckpt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--version", default="v16rnn_dr")
    ap.add_argument("--iters", type=int, default=800)
    ap.add_argument("--envs", type=int, default=384)
    ap.add_argument("--rollout", type=int, default=48)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--gamma", type=float, default=0.99)
    ap.add_argument("--lam", type=float, default=0.95)
    ap.add_argument("--clip", type=float, default=0.2)
    ap.add_argument("--k-prog", type=float, default=1.5)
    ap.add_argument("--k-time", type=float, default=0.05)
    ap.add_argument("--k-jerk", type=float, default=0.005)
    ap.add_argument("--k-coll", type=float, default=25.0)
    ap.add_argument("--k-goal", type=float, default=30.0)
    ap.add_argument("--k-stall", type=float, default=0.4)
    ap.add_argument("--k-timeout", type=float, default=12.0)
    ap.add_argument("--stall-speed", type=float, default=0.4)
    ap.add_argument("--k-alt", type=float, default=0.45)
    ap.add_argument("--k-clear", type=float, default=0.8)
    ap.add_argument("--clear-margin", type=float, default=0.6)
    ap.add_argument("--k-above", type=float, default=0.0)
    ap.add_argument("--above-margin", type=float, default=1.0)
    ap.add_argument("--depth-noise", type=float, default=0.07)
    ap.add_argument("--target-noise", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dr-hold-frac", type=float, default=0.35)
    ap.add_argument("--dr-full-frac", type=float, default=0.8)
    args = ap.parse_args()
    w = W(args)

    torch = _torch()
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(args.seed)

    ckpt = torch.load(args.bc_ckpt, map_location=dev, weights_only=False)
    hidden = ckpt["hidden"]
    ac = build_ac(torch, hidden).to(dev)
    ac.load_state_dict(ckpt["state_dict"], strict=False)  # actor gru+head+mean/std; critic fresh
    print(f"warm-started recurrent actor from BC ({sum(p.numel() for p in ac.parameters())} params)",
          flush=True)

    opt = torch.optim.Adam(ac.parameters(), lr=args.lr)
    env = BoxEnv(torch, args.envs, dev, seed=args.seed,
                 depth_noise=args.depth_noise, target_noise=args.target_noise)
    n = args.envs
    h_a = torch.zeros(1, n, hidden, device=dev)
    h_v = torch.zeros(1, n, hidden, device=dev)

    def dr_schedule(it):
        hold, full = args.dr_hold_frac * args.iters, args.dr_full_frac * args.iters
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
                dist = torch.distributions.Normal(mean, std)
                act = dist.sample()
                logp = dist.log_prob(act).sum(-1)
            rew, done, info = env.step(act, w)
            obs_b[t] = o; act_b[t] = act; logp_b[t] = logp; val_b[t] = value
            rew_b[t] = rew; done_b[t] = done.float()
            ep_reach += float(info["reached"].sum()); ep_coll += float(info["collided"].sum())
            ep_cnt += float(done.sum()); ep_ret += float(rew.sum())
            # reset hidden for envs that just terminated (next tick starts a fresh episode)
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
            """Recompute logp/value over the whole rollout, resetting hidden at done boundaries."""
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
            dist = torch.distributions.Normal(means, std)
            logp = dist.log_prob(act_b).sum(-1)
            ratio = (logp - logp_b).exp()
            s1 = ratio * adv
            s2 = torch.clamp(ratio, 1 - args.clip, 1 + args.clip) * adv
            pg = -torch.min(s1, s2).mean()
            vl = 0.5 * (vals - ret).pow(2).mean()
            ent = dist.entropy().sum(-1).mean()
            loss = pg + 0.5 * vl - 0.005 * ent
            opt.zero_grad(); loss.backward()
            torch.nn.utils.clip_grad_norm_(ac.parameters(), 0.5)
            opt.step()

        h_a, h_v = h_a.detach(), h_v.detach()
        reach_rate = ep_reach / max(ep_cnt, 1); coll_rate = ep_coll / max(ep_cnt, 1)
        hist.append({"iter": it, "reach": reach_rate, "coll": coll_rate})
        if env.dyn.scale >= 0.8 and reach_rate > best_reach:
            best_reach = reach_rate
            best_sd = {k: v.detach().clone() for k, v in ac.state_dict().items()}
        if it % 10 == 0 or it == args.iters - 1:
            print(f"it {it:3d} drs={env.dyn.scale:.2f} reach={reach_rate:.2f} coll={coll_rate:.2f} "
                  f"ret/env={ep_ret/n:7.2f} std={ac.log_std.exp().mean().item():.2f}", flush=True)

    if best_sd is not None:
        ac.load_state_dict(best_sd)
        print(f"exporting BEST DR-robust checkpoint (reach={best_reach:.2f})", flush=True)

    # ---- export STEP-mode ONNX (state,h_in)->(action,h_out), matching train_rnn ----
    nn = torch.nn

    class StepExport(nn.Module):
        def __init__(self, ac):
            super().__init__()
            self.mean = ac.mean; self.std = ac.std
            self.gru = ac.gru; self.head = ac.head
        def forward(self, state, h_in):              # state (1,1,STATE), h_in (1,1,hidden)
            xn = (state - self.mean) / self.std
            out, hn = self.gru(xn, h_in)
            return self.head(out[:, -1, :]), hn

    out = Path(args.out).expanduser(); out.mkdir(parents=True, exist_ok=True)
    step = StepExport(ac).to(dev).eval()
    onnx_path = out / f"policy_{args.version}.onnx"
    ds = torch.zeros(1, 1, STATE_DIM, device=dev); dh = torch.zeros(1, 1, hidden, device=dev)
    torch.onnx.export(step, (ds, dh), str(onnx_path),
                      input_names=["state", "h_in"], output_names=["action", "h_out"],
                      opset_version=17)
    np.save(out / f"policy_{args.version}_state_norm.npy",
            np.stack([ckpt["mean"], ckpt["std"]]).astype(np.float32))
    torch.save({"state_dict": ac.state_dict(), "hidden": hidden,
                "mean": ckpt["mean"], "std": ckpt["std"], "recurrent": True},
               out / f"policy_{args.version}_ac.pt")
    summary = {"version": args.version, "recurrent": True, "iters": args.iters,
               "final": hist[-1] if hist else None, "onnx": str(onnx_path),
               "onnx_bytes": onnx_path.stat().st_size}
    (out / f"policy_{args.version}_rl_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["final"], indent=2), flush=True)


if __name__ == "__main__":
    main()
