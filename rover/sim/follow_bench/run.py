"""Run the follow-bench matrix: variants x scenarios x seeds, one headless Godot per worker.

  python -m rover.sim.follow_bench.run --workers 8 --out rover/sim/results/follow_bench
  python -m rover.sim.follow_bench.run --variants B --scenarios S2_pole --seeds 1 --workers 1
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

from .controllers import Variant

VARIANTS = {
    "B": Variant("B"),
    "B+5": Variant("B+5", f5=True),
    "1": Variant("1", f1=True),
    "1+2": Variant("1+2", f1=True, f2=True),
    "1+2+3": Variant("1+2+3", f1=True, f2=True, f3=True),
    "1+2+3+4": Variant("1+2+3+4", f1=True, f2=True, f3=True, f4=True),
    "1-5": Variant("1-5", f1=True, f2=True, f3=True, f4=True, f5=True),
}
BASE_PORT = 9500

_client = None
_proc = None


def _init(counter):
    global _client, _proc
    from rover.sim.depot_client import DepotClient
    from rover.sim.godot_launcher import launch_depot
    with counter.get_lock():
        idx = counter.value
        counter.value += 1
    port = BASE_PORT + idx
    with contextlib.redirect_stdout(io.StringIO()):
        _proc = launch_depot(seed=0, port=port, gui=False)
    _client = DepotClient(port=port)
    import atexit
    atexit.register(_proc.stop)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: (_proc.stop(), os._exit(0)))


def _task(args):
    from . import harness, scenarios
    variant_name, scenario_name, seed, extra = args
    variant = VARIANTS[variant_name]
    if extra.get("confirm_s") is not None:
        variant = Variant(**{**variant.__dict__, "confirm_s": extra["confirm_s"]})
    sc = scenarios.ALL[scenario_name]()
    from .camera import Camera
    cam = None
    if "cam_h" in extra:
        cam = Camera(height=extra["cam_h"], pitch_deg=extra["pitch"])
    t0 = time.time()
    try:
        r = harness.run(_client, variant, sc, seed, sweep_cam=cam, drop=extra.get('drop', 0.01), reid_cos=extra.get('reid', 0.3))
    except Exception as e:  # keep the matrix going; the error is a result too
        r = dict(variant=variant_name, scenario=scenario_name, seed=seed, error=repr(e))
    r.update(extra)
    r["wall_s"] = round(time.time() - t0, 1)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--scenarios", default="S1_walk,S2_pole,S2c_orbit,S3_close,S4_crate,S4c_orbit,S5_decoy")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--confirm-s", type=float, default=None)
    ap.add_argument("--drop", type=float, default=None, help="detector frame-drop probability")
    ap.add_argument("--reid", type=float, default=None, help="cosine similarity between the two people's appearance")
    ap.add_argument("--sweep", action="store_true", help="camera height x pitch sweep on S1, S3")
    ap.add_argument("--out", default="rover/sim/results/follow_bench")
    ap.add_argument("--tag", default="matrix")
    a = ap.parse_args()

    tasks = []
    if a.sweep:
        for h in (0.3, 0.5, 0.8):
            for pitch in (0, 15, 30):
                for v in ("B", "1-5"):
                    for sc in ("S1_walk", "S3_close"):
                        for seed in range(min(a.seeds, 2)):
                            tasks.append((v, sc, seed, dict(cam_h=h, pitch=pitch)))
    else:
        for v in a.variants.split(","):
            for sc in a.scenarios.split(","):
                for seed in range(a.seeds):
                    extra = {}
                    if a.confirm_s is not None:
                        extra["confirm_s"] = a.confirm_s
                    if a.drop is not None:
                        extra["drop"] = a.drop
                    if a.reid is not None:
                        extra["reid"] = a.reid
                    tasks.append((v, sc, seed, extra))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ctx = mp.get_context("spawn")
    counter = ctx.Value("i", 0)
    results = []
    t0 = time.time()
    with ctx.Pool(a.workers, initializer=_init, initargs=(counter,)) as pool:
        for i, r in enumerate(pool.imap_unordered(_task, tasks), 1):
            results.append(r)
            print(f"[{i}/{len(tasks)}] {r.get('variant')} {r.get('scenario')} s{r.get('seed')} "
                  f"{'ERR ' + r['error'] if 'error' in r else ''} ({time.time() - t0:.0f}s)", flush=True)
    path = out / f"{a.tag}.json"
    path.write_text(json.dumps(results, indent=1, default=str))
    print("wrote", path)


if __name__ == "__main__":
    main()
