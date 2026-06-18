"""Behavioral-clone a RECURRENT policy with a carried GRU hidden state (no fixed window).

The window policy (train.py) re-decides from the last SEQ_LEN frames each tick; it can't hold a
multi-stage commitment ("I ducked under the ceiling, I'm now committed to climbing over the next
wall") across the gauntlet. This trains the SAME GRU+head but threads the hidden state across the
whole episode (BPTT over full episodes), and exports a STEP-mode ONNX:

    inputs  : state (1,1,STATE_DIM), h_in (1,1,hidden)
    outputs : action (1,ACTION_DIM), h_out (1,1,hidden)

so deploy carries h tick-to-tick (reset to zeros at episode start). State contract + internal
mean/std normalization are unchanged, so the depth-grid input is identical to the window policy.

Run on hoopoe:
    PYTHONPATH=. python -m eco.drone.training.train_rnn \
        --data ~/drone-data/bc_v12/dataset.npz --out ~/drone-data/bc_v12/models \
        --version v14rnn --epochs 35 --hidden 128
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .contract import STATE_DIM, ACTION_DIM, ACTION_FIELDS


def _require_torch():
    try:
        import torch  # noqa: F401
    except ImportError as e:  # pragma: no cover
        raise SystemExit("PyTorch required: uv pip install torch onnx onnxruntime") from e
    import torch
    return torch


def make_rnn(torch, hidden: int = 128):
    nn = torch.nn

    class PolicyRNN(nn.Module):
        """GRU + MLP head with a carried hidden state. forward returns per-timestep actions + hn."""
        def __init__(self):
            super().__init__()
            self.register_buffer("mean", torch.zeros(STATE_DIM))
            self.register_buffer("std", torch.ones(STATE_DIM))
            self.gru = nn.GRU(STATE_DIM, hidden, batch_first=True)
            self.head = nn.Sequential(
                nn.Linear(hidden, hidden), nn.ReLU(),
                nn.Linear(hidden, ACTION_DIM),
            )

        def forward(self, x, h0=None):           # x: (B,T,STATE_DIM); h0: (1,B,hidden) or None
            x = (x - self.mean) / self.std
            out, hn = self.gru(x, h0)
            return self.head(out), hn            # (B,T,ACTION_DIM), (1,B,hidden)

    return PolicyRNN()


def build_episode_batches(X, Y, ep, max_len=600):
    """Group contiguous-episode frames into a list of (seq_x, seq_y) arrays (variable length)."""
    eps = []
    n = X.shape[0]
    start = 0
    for i in range(1, n + 1):
        if i == n or ep[i] != ep[i - 1]:
            sx, sy = X[start:i], Y[start:i]
            # chunk over-long episodes so padding/memory stay bounded (state carries within chunk)
            for c in range(0, len(sx), max_len):
                eps.append((sx[c:c + max_len], sy[c:c + max_len]))
            start = i
    return eps


def _pad_batch(torch, eps, idx, dev):
    lens = [len(eps[i][0]) for i in idx]
    T = max(lens)
    B = len(idx)
    bx = torch.zeros(B, T, STATE_DIM, device=dev)
    by = torch.zeros(B, T, ACTION_DIM, device=dev)
    mask = torch.zeros(B, T, device=dev)
    for j, i in enumerate(idx):
        sx, sy = eps[i]
        L = len(sx)
        bx[j, :L] = torch.from_numpy(np.ascontiguousarray(sx)).to(dev)
        by[j, :L] = torch.from_numpy(np.ascontiguousarray(sy)).to(dev)
        mask[j, :L] = 1.0
    return bx, by, mask


def train(data, out_dir, epochs=35, batch_eps=64, lr=1e-3, hidden=128, val_frac=0.1,
          seed=0, version="v14rnn"):
    torch = _require_torch()
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    d = np.load(Path(data).expanduser())
    X, Y, ep = d["X"], d["Y"], d["ep"]
    mean, std = d["state_mean"], d["state_std"]
    eps = build_episode_batches(X, Y, ep)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(eps))
    n_val = int(len(eps) * val_frac)
    val_idx, tr_idx = list(perm[:n_val]), list(perm[n_val:])

    model = make_rnn(torch, hidden=hidden).to(dev)
    model.mean.copy_(torch.from_numpy(mean).to(dev))
    model.std.copy_(torch.from_numpy(std).to(dev))
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    lossf = torch.nn.SmoothL1Loss(reduction="none")

    def masked_loss(bx, by, mask):
        pred, _ = model(bx)
        per = lossf(pred, by).mean(-1) * mask
        return per.sum() / mask.sum().clamp(min=1.0)

    hist = []
    for epoch in range(epochs):
        model.train()
        rng.shuffle(tr_idx)
        tot = 0.0; nb = 0
        for b in range(0, len(tr_idx), batch_eps):
            idx = tr_idx[b:b + batch_eps]
            bx, by, mask = _pad_batch(torch, eps, idx, dev)
            opt.zero_grad()
            loss = masked_loss(bx, by, mask)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += float(loss); nb += 1
        model.eval()
        with torch.no_grad():
            vtot = 0.0; vnb = 0
            for b in range(0, len(val_idx), batch_eps):
                idx = val_idx[b:b + batch_eps]
                if not idx:
                    break
                bx, by, mask = _pad_batch(torch, eps, idx, dev)
                vtot += float(masked_loss(bx, by, mask)); vnb += 1
        tr_loss = tot / max(nb, 1); val_loss = vtot / max(vnb, 1) if n_val else float("nan")
        hist.append({"epoch": epoch, "train": tr_loss, "val": val_loss})
        print(f"epoch {epoch:3d}  train {tr_loss:.5f}  val {val_loss:.5f}", flush=True)

    out = Path(out_dir).expanduser(); out.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "hidden": hidden,
                "mean": mean, "std": std, "recurrent": True}, out / f"policy_{version}.pt")

    # ---- export STEP-mode ONNX: (state, h_in) -> (action, h_out) ----
    class StepPolicy(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m
        def forward(self, state, h_in):          # state (1,1,STATE_DIM), h_in (1,1,hidden)
            act, hn = self.m(state, h_in)
            return act[:, -1, :], hn             # (1,ACTION_DIM), (1,1,hidden)

    model.eval()
    step = StepPolicy(model).to(dev).eval()
    onnx_path = out / f"policy_{version}.onnx"
    dummy_s = torch.zeros(1, 1, STATE_DIM, device=dev)
    dummy_h = torch.zeros(1, 1, hidden, device=dev)
    torch.onnx.export(
        step, (dummy_s, dummy_h), str(onnx_path),
        input_names=["state", "h_in"], output_names=["action", "h_out"],
        opset_version=17,
    )
    np.save(out / f"policy_{version}_state_norm.npy", np.stack([mean, std]).astype(np.float32))

    parity = None
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        s = X[:1].astype(np.float32)[None]; h = np.zeros((1, 1, hidden), np.float32)
        a_onnx, _ = sess.run(None, {"state": s, "h_in": h})
        with torch.no_grad():
            a_ref = step(torch.from_numpy(s).to(dev), torch.from_numpy(h).to(dev))[0].cpu().numpy()
        parity = float(np.abs(a_ref - a_onnx).max())
    except Exception as e:  # pragma: no cover
        parity = f"skipped: {e}"

    summary = {"version": version, "recurrent": True, "hidden": hidden, "epochs": epochs,
               "episodes": len(eps), "final": hist[-1] if hist else None,
               "onnx": str(onnx_path), "onnx_parity_max_abs": parity,
               "action_fields": list(ACTION_FIELDS), "onnx_bytes": onnx_path.stat().st_size}
    (out / f"policy_{version}_train_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def main():
    ap = argparse.ArgumentParser(description="BC-train a recurrent (carried-state) pilot policy.")
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="eco/drone/models")
    ap.add_argument("--epochs", type=int, default=35)
    ap.add_argument("--batch-eps", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--version", default="v14rnn")
    args = ap.parse_args()
    train(args.data, args.out, epochs=args.epochs, batch_eps=args.batch_eps, lr=args.lr,
          hidden=args.hidden, version=args.version)


if __name__ == "__main__":
    main()
