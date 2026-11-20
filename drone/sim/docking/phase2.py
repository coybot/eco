"""Phase 2 exit checks: vehicles, scenario, carrying capacity, minimum safe cruise speed.

    python3 drone/sim/docking/phase2.py --workers 16 [--out DIR] [--quick]

Writes to results/phase2/:
  carry_table_{baseline,conservative}.csv  analytic masses, stall and minimum safe speeds
  stall_check.csv                          simulated 1-g stall speed vs wing-loading formula
  min_safe_sweep.csv                       6-DOF straight flight in turbulence, per speed/mass
  min_safe_summary.csv                     the chosen minimum safe cruise speed per stage
  delivery_batch.csv                       end-to-end: route in gusts, release, pad, takeoff
  summary.json                             exit criteria and the numbers behind them
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys

import numpy as np

_SIM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SIM not in sys.path:
    sys.path.insert(0, _SIM)

from flight.batch import run_batch, write_csv          # noqa: E402
from docking.delivery_run import load_cfg, run_delivery  # noqa: E402
from docking.performance import (carry_table, sim_min_safe_trial, sim_stall_speed)  # noqa: E402

ALPHA_MARGIN_DEG = 3.0       # peak alpha in turbulence must stay this far below the CLmax angle


def delivery_trial(seed: int, airspeed_light_m_s: float = 20.0, airspeed_moderate_m_s: float = 24.0) -> dict:
    """Even seeds light turbulence (W20 15 kt), odd seeds moderate (30 kt). The mothership
    flies the minimum safe cruise speed for the turbulence it is in."""
    rng = np.random.default_rng(seed + 9000)
    moderate = seed % 2 == 1
    w20 = 15.4 if moderate else 7.7
    V = airspeed_moderate_m_s if moderate else airspeed_light_m_s
    r = run_delivery(seed, wind20_m_s=w20, mean_wind_m_s=float(rng.uniform(0, 6)), airspeed_m_s=V)
    r["turbulence_label"] = "moderate" if moderate else "light"
    return r


def sweep_trial(seed: int, airspeed_m_s: float, n_quads: int, wind20_m_s: float, clmax_case: str) -> dict:
    r = sim_min_safe_trial(seed, airspeed_m_s, n_quads, wind20_m_s, clmax_case)
    return r


def _sweep_job(args):
    seed, V, n, w20, case = args
    r = sweep_trial(seed, V, n, w20, case)
    r["seed_id"] = seed
    return r


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(_SIM, "docking", "results", "phase2"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    cfg = load_cfg()
    summary = {}

    # 1. carrying capacity and analytic speeds, baseline and conservative CLmax
    for case in ("baseline", "conservative"):
        c = copy.deepcopy(cfg)
        if case == "conservative":
            c["quadplane"]["aero"]["alpha_stall_deg"] = 15.0
        rows = carry_table(c)
        write_csv(rows, os.path.join(args.out, f"carry_table_{case}.csv"))
        summary[f"carry_{case}"] = rows

    # 2. simulated 1-g stall speed vs formula
    st = []
    for case in ("baseline", "conservative"):
        for n in (2, 1, 0):
            r = sim_stall_speed(cfg, n, case)
            r.update({"clmax_case_label": case, "quads_count": n,
                      "error_frac": r["stall_speed_sim_m_s"] / r["stall_speed_analytic_m_s"] - 1})
            st.append(r)
    write_csv(st, os.path.join(args.out, "stall_check.csv"))
    summary["stall_check"] = st

    # 3. minimum safe cruise speed in design turbulence (6-DOF, 10 seeds per point)
    import multiprocessing as mp
    seeds = range(1, 4 if args.quick else 11)
    speeds = np.arange(16.0, 30.01, 0.5)
    TURB = {"light": 7.7, "moderate": 15.4}           # W20 15 kt / 30 kt (MIL-HDBK-1797)
    jobs = [(s, float(V), n, w20, case) for case in ("baseline", "conservative") for n in (2, 1, 0)
            for w20 in TURB.values() for V in speeds for s in seeds]
    with mp.get_context("spawn").Pool(args.workers) as pool:
        rows = pool.map(_sweep_job, jobs, chunksize=4)
    rows.sort(key=lambda r: (r["clmax_case_label"], r["wind20_m_s"], r["quads_count"], r["airspeed_cmd_m_s"], r["seed_id"]))
    write_csv(rows, os.path.join(args.out, "min_safe_sweep.csv"))
    summ = []
    for case, turb in [(c_, t_) for c_ in ("baseline", "conservative") for t_ in TURB]:
        ct = {r["quads_count"]: r for r in summary[f"carry_{case}"]}
        for n in (2, 1, 0):
            sub = [r for r in rows if r["clmax_case_label"] == case and r["quads_count"] == n
                   and r["wind20_m_s"] == TURB[turb]]
            ok_speeds = []
            for V in speeds:
                pts = [r for r in sub if abs(r["airspeed_cmd_m_s"] - V) < 1e-6]
                if all(r["alpha_max_deg"] <= r["alpha_clmax_deg"] - ALPHA_MARGIN_DEG for r in pts):
                    ok_speeds.append(float(V))
            # lowest speed from which every higher speed in the sweep is also safe
            v_sim = float("nan")
            for V in sorted(ok_speeds):
                if all(v in ok_speeds for v in speeds if v >= V):
                    v_sim = V
                    break
            row = ct[2]
            stage = "full" if n == 2 else ("after_release_1" if n == 1 else "after_release_2")
            v13 = row[f"{stage}_min_cruise_1p3_vs_m_s"]
            from docking.performance import gust_margin_speed_m_s
            from flight.wind import mil_hdbk_1797_low_altitude
            from flight.aero import FixedWingAero
            cc = copy.deepcopy(cfg)
            if case == "conservative":
                cc["quadplane"]["aero"]["alpha_stall_deg"] = 15.0
            clm = FixedWingAero(cc["quadplane"]["aero"]).cl_max()[0]
            sw = mil_hdbk_1797_low_altitude(50.0, TURB[turb])["sigma_w_m_s"]
            vg = gust_margin_speed_m_s(row[f"{stage}_mass_kg"], 1.0, clm, cfg["quadplane"]["aero"]["cl_alpha_per_rad"],
                                       sw, cfg["quadplane"]["design_gust_k_sigma_nd"], cfg["environment"]["air_density_kg_m3"])
            summ.append({"clmax_case_label": case, "turbulence_label": turb, "quads_count": n, "mass_kg": row[f"{stage}_mass_kg"],
                         "stall_speed_m_s": row[f"{stage}_stall_speed_m_s"], "min_cruise_1p3_vs_m_s": v13,
                         "min_cruise_gust_m_s": vg, "min_cruise_sim_m_s": v_sim,
                         "no_safe_speed_in_sweep_bool": math.isnan(v_sim),
                         "min_safe_cruise_m_s": max(v13, vg, v_sim if not math.isnan(v_sim) else float(speeds[-1]))})
    write_csv(summ, os.path.join(args.out, "min_safe_summary.csv"))
    summary["min_safe"] = summ
    def pick(turb):
        v = next(r for r in summ if r["clmax_case_label"] == "baseline" and r["quads_count"] == 2
                 and r["turbulence_label"] == turb)["min_safe_cruise_m_s"]
        return math.ceil(v * 2) / 2.0
    v_light, v_mod = pick("light"), pick("moderate")
    summary["mothership_airspeed_for_runs_m_s"] = {"light": v_light, "moderate": v_mod}

    # 4. end-to-end delivery in gusts (route, release, pad, takeoff)
    n_del = 6 if args.quick else 40
    drows = run_batch(delivery_trial, list(range(1, n_del + 1)), workers=args.workers,
                      out_csv=os.path.join(args.out, "delivery_batch.csv"),
                      common={"airspeed_light_m_s": v_light, "airspeed_moderate_m_s": v_mod})
    ok = [r for r in drows if r["reached_hold_bool"] and r["strikes_count"] == 0 and r["building_hits_count"] == 0]
    summary["delivery"] = {
        "runs_count": len(drows), "success_count": len(ok),
        "strikes_total_count": sum(r["strikes_count"] for r in drows),
        "building_hits_total_count": sum(r["building_hits_count"] for r in drows),
        "ms_alpha_max_deg": max(r["ms_alpha_max_deg"] for r in drows),
        "ms_alt_dev_max_m": max(r["ms_alt_dev_max_m"] for r in drows),
        "ms_airspeed_min_m_s": min(r["ms_airspeed_min_m_s"] for r in drows),
        "ms_pitch_dev_after_release_max_deg": max(r["ms_pitch_dev_after_release_max_deg"] for r in drows),
        "ms_alt_dev_after_release_max_m": max(r["ms_alt_dev_after_release_max_m"] for r in drows),
        "min_clear_tail_m": min(r["min_clear_tail_m"] for r in drows),
        "min_clear_lift_prop_m": min(r["min_clear_lift_prop_m"] for r in drows),
        "min_clear_pusher_m": min(r["min_clear_pusher_m"] for r in drows),
    }
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1, default=float)
    print(json.dumps({k: v for k, v in summary.items() if k in ("min_safe", "delivery", "mothership_airspeed_for_runs_m_s")},
                     indent=1, default=float))
    print("stall check:", [(r["clmax_case_label"], r["quads_count"], round(r["error_frac"], 4)) for r in st])
    exit_ok = (len(ok) == len(drows))
    print("EXIT (delivery all succeed, no strikes):", "PASS" if exit_ok else "FAIL")
    return 0 if exit_ok else 1


if __name__ == "__main__":
    sys.exit(main())
