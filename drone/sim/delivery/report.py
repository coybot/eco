"""Delivery experiments (addendum section 4): every scenario, >= 300 trials, plus the
ablations and comparisons, written as CSV with unit-suffixed columns.

    python -m delivery.report run --exp baseline --trials 300 --workers 28
    python -m delivery.report summarise

Experiments (paired: every arm uses the same scenario seeds):
  baseline      beacon on the railing, landing to release
  radar1_off    radar 1 (forward/down) removed
  radar2_off    radar 2 (forward/up) removed
  wire_x0.5     metal-wire detection range halved
  wire_x2       ... doubled
  mount_wall    beacon on the back wall instead of the railing
  method_line   lower the drink on a line instead of landing
  vel_only      own navigation from radar velocity (+ IMU, barometer) alone: no ranging
                radio, no long-range radio, no scan matching -- does velocity alone
                control drift?
  reuse         second visit, reusing the path stored from the first (against the beacon
                ID), checking it while flying
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

_SIM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SIM not in sys.path:
    sys.path.insert(0, _SIM)

from flight.batch import run_batch, write_csv  # noqa: E402

from .scenarios import SCENARIOS  # noqa: E402

OUT = os.path.join(_SIM, "docking", "results", "delivery")

EXPS = {
    "baseline": {},
    "radar1_off": {"radars": [1]},
    "radar2_off": {"radars": [0]},
    "wire_x0.5": {"wire_scale": 0.5},
    "wire_x2": {"wire_scale": 2.0},
    "mount_wall": {"mount": "wall"},
    "method_line": {"method": "line"},
    "vel_only": {"radar_velocity_only": True},
    "reuse": {"reuse": True},
}


def task(tid, exp="baseline"):
    from .trial import run_trial
    sid = SCENARIOS[tid // 100000]
    seed = tid % 100000
    opts = dict(EXPS[exp])
    try:
        if opts.pop("reuse", False):
            first = run_trial(sid, seed, opts)
            r = run_trial(sid, seed, opts, prior_path=first["rec_path"] if first["row"]["delivered_bool"] else None,
                          visit=2)["row"]
            r["first_visit_delivered_bool"] = first["row"]["delivered_bool"]
            r["first_visit_time_to_delivery_s"] = first["row"]["time_to_delivery_s"]
            r["first_visit_energy_total_j"] = first["row"]["energy_total_j"]
        else:
            r = run_trial(sid, seed, opts)["row"]
        r["error_label"] = "none"
    except Exception as e:                      # keep the batch going; the row says what broke
        import traceback
        r = dict(scenario_label=sid, seed_nd=seed, error_label=(type(e).__name__ + ":" + str(e))[:120].replace(",", ";"))
        traceback.print_exc()
    r["experiment_label"] = exp
    return r


def run(exp, trials, workers, scen=None, seed0=0):
    scen = scen or SCENARIOS
    tids = [SCENARIOS.index(s) * 100000 + seed0 + k for s in scen for k in range(trials)]
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"{exp}.csv")
    rows = run_batch(task, tids, workers=workers, out_csv=out, common={"exp": exp}, progress=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "summarise"])
    ap.add_argument("--exp", default="baseline")
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--scenarios", default=None)
    a = ap.parse_args()
    if a.cmd == "run":
        for e in a.exp.split(","):
            run(e, a.trials, a.workers, a.scenarios.split(",") if a.scenarios else None)
    else:
        from .summarise import main as sm
        sm()


if __name__ == "__main__":
    main()
