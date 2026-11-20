"""Phase 4: test the recommended design.

    python3 drone/sim/docking/phase4.py --workers 28 [--only mc,sweeps,wake,e2e]

  mc      >= 1000 Monte Carlo trials of the recommended design with mass, drag area,
          thrust, motor lag, sensor delays, mean wind, turbulence level and discrete
          gusts all randomised (MC_SPEC). Pass: >= 95 % captured within 3 attempts and zero
          tail / propeller strikes.
  sweeps  one parameter at a time, the rest randomised as in mc: mothership airspeed,
          gust strength (W20), sensor delay, capture (mouth) size.
  wake    raise the near-body flow gain (UNVALIDATED model) until capture < 95 %.
  e2e     the full scenario end to end: release over the street, balcony delivery, take
          off, climb, rendezvous with the next pass, dock.
Writes results/phase4/*.csv and summary.json.
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

from flight.batch import run_batch, write_csv   # noqa: E402
from docking.docking_trial import DEFAULT_RANDOM  # noqa: E402
from docking.exp_common import trial              # noqa: E402

V_DOCK = 19.0

# Wider than the Phase 3 randomisation, per the task: every listed parameter varies.
MC_SPEC = dict(DEFAULT_RANDOM)
MC_SPEC.update({
    "quad_mass_scale_nd": {"dist": "uniform", "low": 0.93, "high": 1.07},
    "quad_cda_scale_nd": {"dist": "uniform", "low": 0.75, "high": 1.25},
    "thrust_scale_nd": {"dist": "uniform", "low": 0.85, "high": 1.05},
    "motor_tau_scale_nd": {"dist": "uniform", "low": 0.7, "high": 1.5},
    "vision_delay_s": {"dist": "uniform", "low": 0.025, "high": 0.09},
    "link_latency_s": {"dist": "uniform", "low": 0.02, "high": 0.12},
    "mean_wind_m_s": {"dist": "uniform", "low": 0.0, "high": 8.0},
    "start_offset_m": {"dist": "uniform", "low": 0.0, "high": 1.0},
    "start_vel_offset_m_s": {"dist": "uniform", "low": 0.0, "high": 1.0},
})


def mc_trial(seed: int, w20_max_m_s: float = 7.7, gust_max_m_s: float = 3.0, **kw) -> dict:
    """One Monte Carlo trial: the turbulence level and a discrete 1-cosine gust are drawn
    here (from a stream separate from the vehicle draws)."""
    rng = np.random.default_rng(seed + 7_000_000)
    w20 = float(rng.uniform(0.0, w20_max_m_s))
    gust = {"amplitude_m_s": float(rng.uniform(0.0, gust_max_m_s)), "dir_deg": float(rng.uniform(0, 360)),
            "elev_deg": float(rng.uniform(-45, 45)), "start_s": float(rng.uniform(2.0, 20.0)),
            "duration_s": float(rng.uniform(0.5, 3.0))}
    kw.setdefault("airspeed_m_s", V_DOCK)
    r = trial(seed, wind20_m_s=w20, random_spec=MC_SPEC, discrete_gust=gust, **kw)
    r["mc_w20_m_s"] = w20
    r["mc_gust_amplitude_m_s"] = gust["amplitude_m_s"]
    return r


def summarise(rows, x=None):
    n = len(rows)
    cap = sum(r["captured_bool"] for r in rows)
    k = sum(r["strikes_count"] > 0 for r in rows)
    p = cap / n
    lo = (p + 1.96 ** 2 / (2 * n) - 1.96 * math.sqrt(p * (1 - p) / n + 1.96 ** 2 / (4 * n * n))) / (1 + 1.96 ** 2 / n)
    out = {"trials_count": n, "capture_frac": p, "capture_wilson95_low_frac": lo, "strikes_count": int(k),
           "first_attempt_frac": sum(r["captured_bool"] and r["attempts_count"] == 1 for r in rows) / n,
           "airframe_contact_trials_count": int(sum(r["airframe_contacts_count"] > 0 for r in rows)),
           "overload_count": int(sum(r["overload_bool"] for r in rows))}
    if x is not None:
        out["x_nd"] = x
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(_SIM, "docking", "results", "phase4"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--n_mc", type=int, default=1200)
    ap.add_argument("--n_sweep", type=int, default=200)
    ap.add_argument("--only", default="mc,sweeps,wake,e2e")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    spath = os.path.join(args.out, "summary.json")
    S = json.load(open(spath)) if os.path.exists(spath) else {}
    only = args.only.split(",")

    def dump():
        with open(spath, "w") as f:
            json.dump(S, f, indent=1)

    if "mc" in only:
        rows = run_batch(mc_trial, list(range(20000, 20000 + args.n_mc)), workers=args.workers,
                         out_csv=os.path.join(args.out, "monte_carlo.csv"), progress=True)
        S["monte_carlo"] = summarise(rows)
        S["monte_carlo"]["pass_bool"] = S["monte_carlo"]["capture_frac"] >= 0.95 and S["monte_carlo"]["strikes_count"] == 0
        dump()
        print("MC", S["monte_carlo"], flush=True)

    if "sweeps" in only:
        seeds = list(range(30000, 30000 + args.n_sweep))
        sweeps = {
            "sweep_airspeed": [(v, {"airspeed_m_s": v, "design_over": {"axis_pitch_deg": round(math.degrees(math.atan2(0.5 * 1.225 * 0.05 * v * v, 9.80665)), 1)}})
                               for v in (19.0, 20.0, 21.0, 22.0, 23.0, 25.0)],
            "sweep_gust": [(w, {"w20_max_m_s": 1e-9, "wind20_fixed_m_s": w}) for w in (0.0, 4.0, 7.7, 11.0, 15.4, 20.0)],
            "sweep_sensor_delay": [(k, {"sensor_delay_scale": k}) for k in (0.5, 1.0, 2.0, 3.0, 4.0)],
            "sweep_mouth_radius": [(r, {"design_over": {"mouth_radius_m": r}}) for r in (0.08, 0.10, 0.12, 0.15, 0.20, 0.25)],
        }
        for key, pts in sweeps.items():
            S[key] = []
            allr = []
            for x, kw in pts:
                rows = run_batch(sweep_trial, seeds, workers=args.workers, common={"kw": kw})
                for r in rows:
                    r["sweep_x_nd"] = x
                    r["row_id"] = len(allr)
                    allr.append(r)
                s = summarise(rows, x)
                S[key].append(s)
                print(key, x, s, flush=True)
                dump()
            write_csv(allr, os.path.join(args.out, f"{key}.csv"))

    if "wake" in only:
        seeds = list(range(40000, 40000 + args.n_sweep))
        S["wake_margin"] = []
        allr = []
        threshold = None
        for g in (0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0):
            rows = run_batch(sweep_trial, seeds, workers=args.workers, common={"kw": {"wake_gain": g}})
            for r in rows:
                r["sweep_x_nd"] = g
                r["row_id"] = len(allr)
                allr.append(r)
            s = summarise(rows, g)
            S["wake_margin"].append(s)
            print("wake", g, s, flush=True)
            if threshold is None and s["capture_frac"] < 0.95:
                threshold = g
            dump()
            if s["capture_frac"] < 0.6:
                break
        S["wake_threshold_gain_nd"] = threshold
        write_csv(allr, os.path.join(args.out, "wake_margin.csv"))
        dump()

    if "clips" in only:
        make_clips(args.out)

    if "e2e" in only:
        from docking.e2e_run import run_e2e
        rows = run_batch(run_e2e, list(range(50000, 50000 + 24)), workers=args.workers,
                         out_csv=os.path.join(args.out, "end_to_end.csv"))
        S["end_to_end"] = {"runs_count": len(rows), "complete_count": int(sum(r["docked_bool"] for r in rows)),
                           "strikes_count": int(sum(r["strikes_count"] for r in rows))}
        dump()
        print("E2E", S["end_to_end"], flush=True)
    return 0


def make_clips(out_dir):
    """Record one success and one failure for the clips (plots.py renders them)."""
    from docking.delivery_run import load_cfg
    from docking.exp_common import design
    cfg = load_cfg()
    d = os.path.join(out_dir, "clips")
    os.makedirs(d, exist_ok=True)
    cases = [("success_pitched_under", "pitched_under", {}, "Recommended design: capture"),
             ("failure_level_out", "level_under_levelout", {}, "Rejected: level dock, level out at the last moment")]
    for fname, dname, over, title in cases:
        for seed in range(60000, 60040):
            r = trial(seed, dname, design_over=over, airspeed_m_s=V_DOCK, wind20_m_s=7.7, record=True)
            ok = r["captured_bool"] if fname.startswith("success") else (not r["captured_bool"])
            if ok and r["_rows"]:
                break
        dd = design(dname, **over)
        n = dd.axis_body()
        ct = np.asarray(dd.throat_body_m)
        perp = np.array([-n[2], 0.0, n[0]])
        mouth = ct + dd.depth_m() * n
        poly = [list((ct + dd.throat_radius_m * perp)[[0, 2]]), list((mouth + dd.mouth_radius_m * perp)[[0, 2]]),
                list((mouth - dd.mouth_radius_m * perp)[[0, 2]]), list((ct - dd.throat_radius_m * perp)[[0, 2]])]
        rec = {"title": f"{title} (seed {seed}, {r['final_state_label']}, {r['abort_reasons_label']})",
               "rows": [{k: (float(v) if not isinstance(v, str) else v) for k, v in row.items()} for row in r["_rows"]],
               "geom": cfg["quadplane"]["geometry"], "funnel": [[float(a), float(b)] for a, b in poly],
               "events": r["_events"]}
        with open(os.path.join(d, fname + ".json"), "w") as f:
            json.dump(rec, f)
        print("clip", fname, seed, r["captured_bool"], r["abort_reasons_label"], flush=True)


def sweep_trial(seed: int, kw: dict) -> dict:
    kw = dict(kw)
    w20_fixed = kw.pop("wind20_fixed_m_s", None)
    if w20_fixed is not None:
        rng = np.random.default_rng(seed + 7_000_000)
        gust = {"amplitude_m_s": float(rng.uniform(0.0, 3.0)), "dir_deg": float(rng.uniform(0, 360)),
                "elev_deg": float(rng.uniform(-45, 45)), "start_s": float(rng.uniform(2.0, 20.0)),
                "duration_s": float(rng.uniform(0.5, 3.0))}
        kw.pop("w20_max_m_s", None)
        if w20_fixed > 10.0:   # the mothership flies its moderate-turbulence speed
            v = 23.0 if w20_fixed <= 15.4 else 25.0
            kw.setdefault("airspeed_m_s", v)
            kw.setdefault("design_over", {"axis_pitch_deg": round(math.degrees(math.atan2(0.5 * 1.225 * 0.05 * v * v, 9.80665)), 1)})
        kw.setdefault("airspeed_m_s", V_DOCK)
        r = trial(seed, wind20_m_s=w20_fixed, random_spec=MC_SPEC, discrete_gust=gust, **kw)
        r["mc_w20_m_s"] = w20_fixed
        return r
    return mc_trial(seed, **kw)


if __name__ == "__main__":
    sys.exit(main())
