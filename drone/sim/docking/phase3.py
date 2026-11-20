"""Phase 3 design comparisons. Each experiment is a seeded batch of docking trials; the
same seeds are used for every arm of a comparison (paired), so arms differ only in the
thing being compared.

    python3 drone/sim/docking/phase3.py --workers 28 [--n 120] [--only E1,E4]

Writes results/phase3/<experiment>.csv and summary.json. DESIGN_LOG.md is written from
these numbers.
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

from flight.batch import run_batch       # noqa: E402
from docking.exp_common import trial      # noqa: E402

LIGHT = 7.7          # W20 15 kt
MODERATE = 15.4      # W20 30 kt
V_DOCK = 19.0        # light-turbulence docking airspeed (Phase 2: min safe 18.4 m/s with 0-1 quads aboard)


def arm(name, **kw):
    return name, kw


EXPERIMENTS = {
    # H1: pitched dock vs levelling out at the last moment
    "E1_dock_pitch": [
        arm("pitched_under", design_name="pitched_under"),
        arm("level_under_levelout", design_name="level_under_levelout"),
        arm("level_under_angled_probe", design_name="level_under_angled_probe"),
    ],
    # H2: under vs top, nominal and with faults during the approach
    "E2_location_faults": [
        arm(f"{loc}_{f}", design_name=f"pitched_{loc}", fault=(None if f == "nominal" else f))
        for loc in ("under", "top") for f in ("nominal", "motor_loss", "brownout")
    ],
    "E3_probe_compliance": [
        arm("gimbal", design_name="pitched_under"),
        arm("rigid", design_name="pitched_under", design_over={"gimbal": False}),
    ],
    # Would a soft bushing do as well as a gimbal? The joint is a spring-damper with a travel
    # stop either way; a "bushing" is the same joint, stiffer, with less travel.
    "E3b_probe_joint_sweep": [
        arm(f"k_{k:g}_nm_rad_travel_{tr:g}_deg", design_name="pitched_under",
            design_over={"gimbal_spring_nm_per_rad": float(k), "gimbal_max_deg": float(tr)})
        for k in (0.15, 0.7, 3.0, 20.0) for tr in (25.0, 10.0, 3.0)
    ],
    "E4_mouth_radius": [
        arm(f"mouth_{r:.2f}_m", design_name="pitched_under", design_over={"mouth_radius_m": r})
        for r in (0.10, 0.15, 0.20, 0.25)
    ],
    "E4b_cone_half_angle": [
        arm(f"half_angle_{a:.0f}_deg", design_name="pitched_under", design_over={"half_angle_deg": float(a)})
        for a in (30, 40, 50)
    ],
    "E5_relnav_source": [
        arm(m, design_name="pitched_under", relnav_mode=m) for m in ("fused", "link", "vision")
    ],
    "E6_disturbance_observer": [
        arm("dob_off", design_name="pitched_under"),
        arm("dob_on", design_name="pitched_under", guidance_overrides={"dob_enabled": True}),
    ],
    "E7_mothership_speed": [
        arm(f"airspeed_{v:.0f}_m_s", design_name="pitched_under", airspeed_m_s=float(v),
            design_over={"axis_pitch_deg": round(math.degrees(math.atan2(0.5 * 1.225 * 0.05 * v * v, 9.80665)), 1)})
        for v in (19, 21, 23, 25)
    ],
    "E7b_mothership_stiffness": [
        arm(f"stiffness_{k:.1f}", design_name="pitched_under", ms_stiffness=k) for k in (0.5, 1.0, 2.0)
    ],
    "E8_thrust_margin": [
        arm("tw2p5_at_1p4kg", design_name="pitched_under"),
        arm("tw2p5_at_1p0kg", design_name="pitched_under", quad_overrides={"thrust_to_weight_reference_mass_kg": 1.0}),
        arm("tw2p5_at_1p0kg_23ms", design_name="pitched_under", airspeed_m_s=23.0,
            quad_overrides={"thrust_to_weight_reference_mass_kg": 1.0}, design_over={"axis_pitch_deg": 57.0}),
        arm("tw2p5_at_1p4kg_23ms", design_name="pitched_under", airspeed_m_s=23.0, design_over={"axis_pitch_deg": 57.0}),
        arm("cda0p035_23ms", design_name="pitched_under", airspeed_m_s=23.0, quad_overrides={"drag_area_m2": 0.035},
            design_over={"axis_pitch_deg": 47.0}),
    ],
    "E9_second_quad_clearance": [
        arm("front_dock_rear_occupied", design_name="pitched_under", rear_quad_docked=True),
    ],
    "E10_moderate_turbulence": [
        arm("moderate_23ms", design_name="pitched_under", airspeed_m_s=23.0, wind20_m_s=MODERATE,
            design_over={"axis_pitch_deg": 57.0}),
    ],
}


def summarise(rows):
    n = len(rows)
    cap = [r for r in rows if r["captured_bool"]]
    first = [r for r in cap if r["attempts_count"] == 1]
    f = lambda k: [r[k] for r in rows]
    return {
        "trials_count": n,
        "capture_frac": len(cap) / n,
        "capture_first_attempt_frac": len(first) / n,
        "strikes_count": int(sum(r["strikes_count"] > 0 for r in rows)),
        "strike_parts_label": "+".join(sorted({r["strike_part_label"] for r in rows if r["strikes_count"]})) or "none",
        "airframe_contact_trials_count": int(sum(r["airframe_contacts_count"] > 0 for r in rows)),
        "overload_count": int(sum(r["overload_bool"] for r in rows)),
        "peak_contact_force_p95_n": float(np.percentile(f("peak_contact_force_n"), 95)),
        "seat_force_max_n": float(max(f("seat_force_n"))),
        "time_to_capture_median_s": float(np.median([r["time_to_capture_s"] for r in cap])) if cap else -1.0,
        "min_clear_tail_m": float(min(f("min_clear_tail_m"))),
        "min_clear_pusher_m": float(min(f("min_clear_pusher_m"))),
        "min_clear_lift_prop_m": float(min(f("min_clear_lift_prop_m"))),
        "min_clear_other_quad_m": float(min(f("min_clear_other_quad_m"))),
        "relnav_err_final_p95_m": float(np.percentile(f("relnav_err_final_max_m"), 95)),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(_SIM, "docking", "results", "phase3"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    sel = [e for e in EXPERIMENTS if not args.only or e.split("_")[0] in args.only.split(",")]
    spath = os.path.join(args.out, "summary.json")
    summary = json.load(open(spath)) if os.path.exists(spath) else {}
    seeds = list(range(1000, 1000 + args.n))
    for exp in sel:
        summary[exp] = {}
        all_rows = []
        for name, kw in EXPERIMENTS[exp]:
            common = {"airspeed_m_s": V_DOCK, "wind20_m_s": LIGHT}
            common.update(kw)
            rows = run_batch(trial, seeds, workers=args.workers, common=common)
            for r in rows:
                r["arm_label"] = name
                r["row_id"] = len(all_rows)
                all_rows.append(r)
            s = summarise(rows)
            summary[exp][name] = s
            print(f"{exp:28s} {name:28s} capture {s['capture_frac']:.3f} first {s['capture_first_attempt_frac']:.3f} "
                  f"strikes {s['strikes_count']} contacts {s['airframe_contact_trials_count']} "
                  f"F95 {s['peak_contact_force_p95_n']:.0f} N tail {s['min_clear_tail_m']:.3f} m", flush=True)
        from flight.batch import write_csv
        write_csv(all_rows, os.path.join(args.out, f"{exp}.csv"))
        with open(spath, "w") as f:
            json.dump(summary, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
