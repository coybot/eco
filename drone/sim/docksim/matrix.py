"""Spec section 13 test matrix: launch and recovery trials over every cell, CSV out.

    python -m docksim.matrix run --exp baseline --trials 200 --workers 30
    python -m docksim.matrix run --exp baseline --trials 20 --cells "hover=moderate,wind=6"

Each trial is one launch (with the cell's box load) and one recovery to a pocket chosen for the
cell's parked-neighbour count. Seeds are (trial, cell) so every cell is independent and any row
can be re-run alone with ``python -m docksim.trial``.
Experiments (EXPS) change one thing against the baseline.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import os
import time
import traceback
from multiprocessing import get_context

from .config import load_cfg
from .trial import run_launch, run_recovery

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docking", "results", "dock")

EXPS = {
    "baseline": dict(),                                         # user's 3-anchor triangle, spec sequence
    "spec4": dict(cell=dict(anchor_set="spec4")),               # the spec's four anchors (adds A4 at the dock rear)
    "sidestep": dict(opts=dict(launch_sidestep=True)),          # launch: rise past the lip, step off the post, climb
    "stiff_x": dict(opts=dict(comply_x=False)),                 # no x compliance on the approach (spec as written)
    "magnet8": dict(opts=dict(magnet_scale=8 / 12)),
    "magnet16": dict(opts=dict(magnet_scale=16 / 12)),
}


def cells(cfg, only=None):
    m = cfg["dock_matrix"]
    keys = ["hover", "wind", "noise", "anchor_loss", "parked_neighbours", "launch_box"]
    vals = [m["hover_level"], m["wind_mean_m_s"], m["ranging_noise_scale"], m["anchor_loss"], m["parked_neighbours"],
            m["launch_box"]]
    out = []
    for i, combo in enumerate(itertools.product(*vals)):
        c = dict(zip(keys, combo))
        c["cell_id"] = i
        if only and any(str(c[k]) != v for k, v in only.items()):
            continue
        out.append(c)
    return out


def _task(a):
    exp, cell, seed = a
    cfg = load_cfg()
    e = EXPS[exp]
    cell = dict(cell, **e.get("cell", {}))
    opts = dict(e.get("opts", {}))
    rows = []
    for kind, f in (("launch", run_launch), ("recovery", run_recovery)):
        try:
            r = f(cfg, cell, seed, opts=opts)
            r["error_label"] = "none"
        except Exception as ex:                                  # keep the batch alive; record the error
            r = dict(kind=kind, seed=seed, error_label=repr(ex)[:200], traceback=traceback.format_exc()[-400:])
        r.update(exp=exp, cell_id=cell["cell_id"], hover=cell["hover"], wind_m_s=cell["wind"],
                 noise_scale=cell["noise"], anchor_loss_label=cell["anchor_loss"],
                 parked_neighbours=cell["parked_neighbours"], launch_box_cell_bool=cell["launch_box"])
        rows.append(r)
    return rows


def run(exp, trials, workers, only=None, seed0=1):
    cfg = load_cfg()
    cs = cells(cfg, only)
    tasks = [(exp, c, s) for s in range(seed0, seed0 + trials) for c in cs]
    os.makedirs(OUT, exist_ok=True)
    paths = {k: os.path.join(OUT, f"{exp}_{k}.csv") for k in ("launch", "recovery")}
    writers, files = {}, {}
    t0 = time.time()
    n = 0
    with get_context("spawn").Pool(workers) as pool:
        for rows in pool.imap_unordered(_task, tasks, chunksize=2):
            for r in rows:
                k = r["kind"]
                if k not in writers:
                    files[k] = open(paths[k], "w", newline="")
                    writers[k] = csv.DictWriter(files[k], fieldnames=list(r.keys()), extrasaction="ignore")
                    writers[k].writeheader()
                writers[k].writerow(r)
            n += 1
            if n % 200 == 0 or n == len(tasks):
                for f in files.values():
                    f.flush()
                el = time.time() - t0
                print(f"  {n}/{len(tasks)}  {el:.0f} s  eta {el / n * (len(tasks) - n) / 60:.0f} min", flush=True)
    for f in files.values():
        f.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--exp", default="baseline", help="one or more of %s, comma separated" % ",".join(EXPS))
    ap.add_argument("--trials", default="200", help="trials per cell; one value or one per experiment")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--cells", default="", help='filter, e.g. "hover=moderate,wind=6"')
    a = ap.parse_args()
    only = dict(kv.split("=") for kv in a.cells.split(",")) if a.cells else None
    exps = a.exp.split(",")
    trials = [int(x) for x in a.trials.split(",")]
    trials = trials * len(exps) if len(trials) == 1 else trials
    for e, n in zip(exps, trials):
        print(f"== {e}: {n} trials per cell", flush=True)
        run(e, n, a.workers, only)


if __name__ == "__main__":
    main()
