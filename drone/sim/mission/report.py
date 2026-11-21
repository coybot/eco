"""Balcony missions over scenarios B1-B6 and variants; CSV + per-scenario tables.

    python -m mission.report run --exp baseline --trials 300 --workers 30
    python -m mission.report summary
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import time
from collections import Counter, defaultdict
from multiprocessing import get_context

from docksim.config import load_cfg
from delivery.summarise import wilson

from .scenarios import SCENARIOS
from .sim import run_mission

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docking", "results", "mission")
EXPS = {
    "baseline": dict(),                                 # 0.6 m sign triangle, no sign barometer
    "sign0.3": dict(sign_side_m=0.3, variant_id=1),
    "sign1.0": dict(sign_side_m=1.0, variant_id=2),
    "sign_baro": dict(sign_baro=True, variant_id=3),
    "uwb_x2": dict(uwb_noise_scale=2.0, variant_id=4),
}


def _task(a):
    exp, sid, seed = a
    try:
        r = run_mission(load_cfg(), sid, seed, opts=dict(EXPS[exp]))
        r["error_label"] = "none"
    except Exception as ex:
        r = dict(scenario_label=sid, seed=seed, error_label=repr(ex)[:200])
    r["exp"] = exp
    return r


def run(exp, trials, workers):
    tasks = [(exp, sid, s) for s in range(1, trials + 1) for sid in SCENARIOS]
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{exp}.csv")
    t0 = time.time()
    rows = []
    with get_context("spawn").Pool(workers) as pool:
        for i, r in enumerate(pool.imap_unordered(_task, tasks, chunksize=4)):
            rows.append(r)
            if (i + 1) % 300 == 0:
                print(f"  {i + 1}/{len(tasks)} {time.time() - t0:.0f} s", flush=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _b(x):
    return str(x).strip().lower() in ("true", "1")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def summary():
    res = {}
    lines = []
    for path in sorted(glob.glob(os.path.join(OUT, "*.csv"))):
        exp = os.path.basename(path)[:-4]
        with open(path) as f:
            rows = [r for r in csv.DictReader(f) if r.get("error_label", "none") == "none"]
        by = defaultdict(list)
        for r in rows:
            by[r["scenario_label"]].append(r)
        res[exp] = {}
        lines += [f"\n## {exp}\n", "| scen | n | delivered | returned | both | collisions (kind) | gave up | "
                  "drop err med/p90 m | gate err med m | handover err med/p90 m | time med s | energy med Wh |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for sid in SCENARIOS + ["ALL"]:
            rs = rows if sid == "ALL" else by.get(sid, [])
            n = len(rs)
            if not n:
                continue
            d = sum(_b(r["delivered_bool"]) for r in rs)
            rt = sum(_b(r["returned_bool"]) for r in rs)
            ok = sum(_b(r["mission_ok_bool"]) for r in rs)
            col = Counter(r["collision_label"] for r in rs if r["collision_label"])
            gu = Counter(r["gave_up_label"] for r in rs if r["gave_up_label"])

            def q(k, p):
                xs = sorted(x for x in (_f(r[k]) for r in rs) if x is not None)
                return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None
            v = dict(n=n, delivered=d / n, delivered_ci95=wilson(d, n), returned=rt / n, both=ok / n,
                     both_ci95=wilson(ok, n), collisions=dict(col), gave_up=dict(gu),
                     drop_err_med=q("drop_err_m", 0.5), drop_err_p90=q("drop_err_m", 0.9),
                     gate_err_med=q("gate_err_m", 0.5), handover_err_med=q("handover_err_m", 0.5),
                     handover_err_p90=q("handover_err_m", 0.9), time_med=q("time_s", 0.5),
                     energy_med=q("energy_wh", 0.5), energy_p90=q("energy_wh", 0.9))
            res[exp][sid] = v
            fm = lambda x, nd=2: "-" if x is None else f"{x:.{nd}f}"
            lines.append(f"| {sid} | {n} | {v['delivered']:.3f} | {v['returned']:.3f} | {v['both']:.3f} | "
                         f"{sum(col.values())} {dict(col) if col else ''} | {sum(gu.values())} | "
                         f"{fm(v['drop_err_med'])}/{fm(v['drop_err_p90'])} | {fm(v['gate_err_med'])} | "
                         f"{fm(v['handover_err_med'])}/{fm(v['handover_err_p90'])} | {fm(v['time_med'], 0)} | "
                         f"{fm(v['energy_med'], 1)} |")
    with open(os.path.join(OUT, "summary.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    with open(os.path.join(OUT, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("--exp", default="baseline", choices=list(EXPS))
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.exp, a.trials, a.workers)
    else:
        summary()


if __name__ == "__main__":
    main()
