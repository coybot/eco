"""Behavioral-clone a small temporal policy and export it for the Orin Nano.

Architecture: whiten state -> single-layer GRU over a short history window -> MLP head ->
[vx, vy, vz, yaw_rate]. The GRU gives temporal smoothness (the output depends on recent
history, not just the current frame); the net is a few hundred KB, so it TensorRT-compiles
trivially on the Orin -- same deploy path as the existing policy_v1 ONNX asset.

Inputs come from dataset.py's dataset.npz. Windows are built within episode boundaries (the
`ep` array) so history never bleeds across episodes.

Export: ONNX with input (1, SEQ_LEN, STATE_DIM) -> output (1, ACTION_DIM), plus
`policy_v2_state_norm.npy` (shape (2, STATE_DIM): mean, std) co-located, matching the
policy_v1 convention so on-device loading is unchanged.

--- Run on hoopoe (A100), not locally ---

Deploy the training package to hoopoe:
    just -f eco/drone/training/justfile deploy

Then on hoopoe (or via `just -f eco/drone/training/justfile train`):
    # 1. generate dataset
    cd ~/presidio-training
    uv run python -m dataset --out ~/drone-data/bc_v2 --episodes 4000

    # 2. train + export
    uv run python -m train --data ~/drone-data/bc_v2/dataset.npz \
        --out ~/drone-data/bc_v2/models --epochs 30

    # 3. pull the exported models back (see justfile `pull` target)

The Isaac venv at /opt/ml/isaac-sim-env/ has numpy/torch; if not, install with:
    uv pip install --python /opt/ml/isaac-sim-env/bin/python3 torch onnx onnxruntime
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .contract import STATE_DIM, ACTION_DIM, ACTION_FIELDS

SEQ_LEN = 16  # history window fed to the GRU (1.6 s at 10 Hz) — anticipation/memory


def _require_torch():
    try:
        import torch  # noqa: F401
    except ImportError as e:  # pragma: no cover - environment dependent
        raise SystemExit(
            "PyTorch is required for training/export. Install on the 5090 box:\n"
            "  uv pip install torch onnx onnxruntime"
        ) from e
    import torch
    return torch


def build_windows(X: np.ndarray, Y: np.ndarray, ep: np.ndarray, seq_len: int):
    """Build (N, seq_len, STATE_DIM) windows -> (N, ACTION_DIM) targets, within episodes.

    Early frames in an episode are left-padded by repeating the first frame so every tick
    yields a training sample (matches runtime, which left-pads its ring buffer at takeoff).
    """
    xs, ys = [], []
    n = X.shape[0]
    start = 0
    for i in range(n + 1):
        if i == n or (i > 0 and ep[i] != ep[i - 1]):
            seg_x = X[start:i]
            seg_y = Y[start:i]
            for t in range(len(seg_x)):
                lo = t - seq_len + 1
                if lo < 0:
                    pad = np.repeat(seg_x[0:1], -lo, axis=0)
                    win = np.concatenate([pad, seg_x[0:t + 1]], axis=0)
                else:
                    win = seg_x[lo:t + 1]
                xs.append(win)
                ys.append(seg_y[t])
            start = i
    return np.asarray(xs, dtype=np.float32), np.asarray(ys, dtype=np.float32)


def make_model(torch, hidden: int = 64):
    nn = torch.nn

    class Policy(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer("mean", torch.zeros(STATE_DIM))
            self.register_buffer("std", torch.ones(STATE_DIM))
            self.gru = nn.GRU(STATE_DIM, hidden, batch_first=True)
            self.head = nn.Sequential(
                nn.Linear(hidden, hidden), nn.ReLU(),
                nn.Linear(hidden, ACTION_DIM),
            )

        def forward(self, x):  # x: (B, T, STATE_DIM)
            x = (x - self.mean) / self.std
            out, _ = self.gru(x)
            return self.head(out[:, -1, :])

    return Policy()


def train(
    data: str | Path,
    out_dir: str | Path,
    epochs: int = 30,
    batch_size: int = 512,
    lr: float = 1e-3,
    hidden: int = 64,
    val_frac: float = 0.1,
    seed: int = 0,
    version: str = "v2",
) -> dict:
    torch = _require_torch()
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    d = np.load(Path(data).expanduser())
    X, Y, ep = d["X"], d["Y"], d["ep"]
    mean, std = d["state_mean"], d["state_std"]
    Xw, Yw = build_windows(X, Y, ep, SEQ_LEN)

    g = torch.Generator().manual_seed(seed)
    n = Xw.shape[0]
    perm = torch.randperm(n, generator=g).numpy()
    Xw, Yw = Xw[perm], Yw[perm]
    n_val = int(n * val_frac)
    tx = torch.from_numpy(Xw[n_val:]).to(dev)
    ty = torch.from_numpy(Yw[n_val:]).to(dev)
    vx = torch.from_numpy(Xw[:n_val]).to(dev)
    vy = torch.from_numpy(Yw[:n_val]).to(dev)

    model = make_model(torch, hidden=hidden).to(dev)
    model.mean.copy_(torch.from_numpy(mean).to(dev))
    model.std.copy_(torch.from_numpy(std).to(dev))
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    lossf = torch.nn.SmoothL1Loss()

    history = []
    for epoch in range(epochs):
        model.train()
        idx = torch.randperm(tx.shape[0], device=dev)
        total = 0.0
        for b in range(0, tx.shape[0], batch_size):
            sel = idx[b:b + batch_size]
            opt.zero_grad()
            pred = model(tx[sel])
            loss = lossf(pred, ty[sel])
            loss.backward()
            opt.step()
            total += float(loss) * len(sel)
        train_loss = total / tx.shape[0]
        model.eval()
        with torch.no_grad():
            if n_val:
                # batch the val forward — a full-set forward OOMs at larger hidden sizes
                vtot = 0.0
                for b in range(0, vx.shape[0], batch_size):
                    pv = model(vx[b:b + batch_size])
                    vtot += float(lossf(pv, vy[b:b + batch_size])) * pv.shape[0]
                val_loss = vtot / vx.shape[0]
            else:
                val_loss = float("nan")
        history.append({"epoch": epoch, "train": train_loss, "val": val_loss})
        print(f"epoch {epoch:3d}  train {train_loss:.5f}  val {val_loss:.5f}")

    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    onnx_path = out / f"policy_{version}.onnx"
    norm_path = out / f"policy_{version}_state_norm.npy"

    # torch checkpoint so the RL fine-tune (train_rl.py) can warm-start from BC
    torch.save({"state_dict": model.state_dict(), "hidden": hidden,
                "mean": mean, "std": std}, out / f"policy_{version}.pt")

    model.eval()
    dummy = torch.zeros(1, SEQ_LEN, STATE_DIM, device=dev)
    torch.onnx.export(
        model, dummy, str(onnx_path),
        input_names=["state_window"], output_names=["action"],
        dynamic_axes={"state_window": {0: "batch"}, "action": {0: "batch"}},
        opset_version=17,
    )
    np.save(norm_path, np.stack([mean, std]).astype(np.float32))

    # parity check against onnxruntime if available
    parity = None
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        probe = Xw[:256].astype(np.float32)
        with torch.no_grad():
            ref = model(torch.from_numpy(probe).to(dev)).cpu().numpy()
        got = sess.run(["action"], {"state_window": probe})[0]
        parity = float(np.abs(ref - got).max())
    except Exception as e:  # pragma: no cover
        parity = f"skipped: {e}"

    summary = {
        "version": version, "samples": int(n), "seq_len": SEQ_LEN,
        "hidden": hidden, "epochs": epochs,
        "final": history[-1] if history else None,
        "onnx": str(onnx_path), "state_norm": str(norm_path),
        "onnx_parity_max_abs": parity,
        "action_fields": list(ACTION_FIELDS),
        "onnx_bytes": onnx_path.stat().st_size,
    }
    (out / f"policy_{version}_train_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="BC-train the smooth mid-level pilot policy.")
    ap.add_argument("--data", required=True, help="path to dataset.npz from dataset.py")
    ap.add_argument("--out", default="eco/drone/models")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=512)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--version", default="v2")
    args = ap.parse_args()
    train(
        args.data, args.out, epochs=args.epochs, batch_size=args.batch_size,
        lr=args.lr, hidden=args.hidden, version=args.version,
    )


if __name__ == "__main__":
    main()
