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
import copy
import csv
import itertools
import os
import time
import traceback
from multiprocessing import get_context

from .config import load_cfg
from .trial import run_launch, run_recovery

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docking", "results", "dock")

def _merge(dst, src):
    """Deep-merge src into dst (src is copied, never aliased)."""
    src = copy.deepcopy(src)
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v
    return dst


# design variants: config overrides (same keys as docking/vehicles.yaml)
LIP10 = {"dock": {"starboard_lip": {"z_inner_m": 0.107, "z_outer_m": 0.165, "height_m": 0.10}}}
DUAL = {"dock": {"post": {"x_offsets_m": [-0.075, 0.075]}},
        "bee35": {"bracket": {"x_offsets_m": [-0.075, 0.075], "mouth_half_width_m": 0.070},
                  "down_sensor_pos_m": [0.0, 0.0, -0.100]}}
WINDWARD = {"mothership_airframe": {"wind_from_centre_deg": -90.0, "wind_from_nose_max_deg": 20.0}}
SLOT20 = {"bee35": {"bracket": {"slot_width_m": 0.020}}}

# 249 g drone (empty, with bracket) carrying a Jetson Orin Nano on a custom carrier, same 0.21 m
# footprint so it fits the same pockets, posts and brackets. Mass budget and sources: PHASE_3 section 5.7.
LITE = {"bee35": {"mass_kg": 0.239,                   # (E) 249 g minus the 10 g bracket
                  "inertia_kg_m2": [0.0009, 0.0016, 0.0009],   # (E) Bee35 scaled by mass, a little more central
                  "thrust_max_total_n": 12.0,         # (P) four 1404-class motors on 3.5 in props, ~300 gf each
                  "motor_tau_s": 0.03,                # (E) smaller motors spin up faster
                  "drag_area_m2": 0.022},             # (E) prop guards instead of the Bee35's ducts
        "dock": {"magnet": {"force_per_pad_n": 1.35}}}  # [D] scaled with mass: same g margin as the Bee35 dock
LITE_PUSH = _merge(_merge({}, LITE), {"dock_ops": {"recovery": {"push_force_n": 0.25}}})  # push scaled to yaw authority

# a mothership built to the REPORT section 5 spec (only the rows the dock sim can represent)
ROTORS_OUT = {"mothership_airframe": {"rotors": {"pos_m": [[0.50, -0.10, 1.00], [0.50, -0.10, -1.00],
                                                            [-0.50, -0.10, 1.00], [-0.50, -0.10, -1.00]]}}}
#     lift rotors moved outboard: strike zone starts 0.51 m outboard of the dock floor (was 0.06 m)
STEADY = {"mothership_airframe": {"hover_motion_scale": 0.5}}   # half the hover motion: gusty becomes moderate
BCAST50 = {"dock_sensors": {"broadcast": {"rate_hz": 50, "delay_s": [0.01, 0.02]}},
           "bee35": {"controller": {"dock_accel_lpf_s": 0.1}}}  # 50 Hz, 10-20 ms (was 10 Hz, 50-100 ms)
CTL_SPEC = dict(soft_cutoff_s=0.5, bias_cal_frac=0.5, dock_ff=True)  # controller options used with the spec

# channel dock (user's idea, 2026-11-22): one walled lane, wide funnel mouth at the rear, drones nose to tail
CHANNEL = {"channel_dock": {"enabled": True}}
CH_WIDE = _merge(_merge({}, CHANNEL), {"channel_dock": {"mouth_half_width_m": 0.40}})     # 0.80 m mouth
CH_NARROW = _merge(_merge({}, CHANNEL), {"channel_dock": {"mouth_half_width_m": 0.175}})  # 0.35 m mouth
CH_SLIDE = _merge(_merge({}, CHANNEL), {"channel_dock": {"approach_mode_label": "slide"}})
CH_SLIDE_LOOSE = _merge(_merge({}, CH_SLIDE), {"channel_dock": {"inner_half_width_m": 0.115,          # 10 mm rail
                                                                 "upper_extra_clearance_m": 0.005}})  # clearance

POCKET = {"dock_sensors": {"pocket_beacons": {"enabled": True}}}
POCKET_SAME = {"dock_sensors": {"pocket_beacons": {"enabled": True, "sigma_m": 0.05, "bias_max_m": 0.05}}}
STILL = {"mothership_airframe": {"hover_motion_scale": 0.0}}
DRIFT = {"mothership_airframe": {"drift_with_wind": True}}

EXPS = {
    "baseline": dict(),                                         # user's 3-anchor triangle, spec sequence
    "spec4": dict(cell=dict(anchor_set="spec4")),               # the spec's four anchors (adds A4 at the dock rear)
    "sidestep": dict(opts=dict(launch_sidestep=True)),          # launch: rise past the lip, step off the post, climb
    "stiff_x": dict(opts=dict(comply_x=False)),                 # no x compliance on the approach (spec as written)
    "magnet8": dict(opts=dict(magnet_scale=8 / 12)),
    "magnet16": dict(opts=dict(magnet_scale=16 / 12)),
    "lip10": dict(cfg=LIP10),                                   # starboard lip 0.10 m high, ~60 deg
    "dual": dict(cfg=DUAL),                                     # two posts and two brackets per pocket
    "windward": dict(cfg=WINDWARD),                             # mothership yaws: wind from starboard +-20 deg
    "slot20": dict(cfg=SLOT20),                                 # launch slot 20 mm (5 mm clearance each side)
    "still": dict(cfg=STILL),                                   # hover with no motion at all (upper bound)
    "drift": dict(cfg=DRIFT),                                   # hover relative to the air: drift with the wind
    "still_drift": dict(cfg=_merge(_merge({}, STILL), DRIFT)),
    "all": dict(cfg=_merge(_merge(_merge(_merge({}, LIP10), DUAL), WINDWARD), SLOT20),
                cell=dict(anchor_set="spec4")),                 # every change at once, plus the dock anchor A4
    "all_drift": dict(cfg=_merge(_merge(_merge(_merge(_merge({}, LIP10), DUAL), WINDWARD), SLOT20), DRIFT),
                      cell=dict(anchor_set="spec4")),           # every geometry change + A4 + drifting hover
    "all_drift_nodual": dict(cfg=_merge(_merge(_merge(_merge({}, LIP10), WINDWARD), SLOT20), DRIFT),
                             cell=dict(anchor_set="spec4")),    # the same without the two-post bracket
    "pocket_drift": dict(cfg=_merge(_merge({}, POCKET), DRIFT)),          # triangle + a beacon per pocket, drifting
    "pocket_same_drift": dict(cfg=_merge(_merge({}, POCKET_SAME), DRIFT)),  # same, beacons as noisy as A1-A3
    # navigation and control options (Pilot opts), on top of drift + pocket beacons
    "pd_soft": dict(cfg=_merge(_merge({}, POCKET), DRIFT), opts=dict(soft_cutoff_s=0.5)),
    "pd_cal": dict(cfg=_merge(_merge({}, POCKET), DRIFT), opts=dict(bias_cal_frac=0.5)),
    "pd_ff": dict(cfg=_merge(_merge({}, POCKET), DRIFT), opts=dict(dock_ff=True)),
    "pd_dob": dict(cfg=_merge(_merge({}, POCKET), DRIFT), opts=dict(dob_tau_s=0.1)),
    "pd_ctl": dict(cfg=_merge(_merge({}, POCKET), DRIFT),
                   opts=dict(soft_cutoff_s=0.5, bias_cal_frac=0.5, dock_ff=True, dob_tau_s=0.1)),
    # the same four on the spec's dock without beacons or drift (do they help where navigation is weak?)
    "pd_nodob": dict(cfg=_merge(_merge({}, POCKET), DRIFT),         # pd_ctl without the disturbance observer
                     opts=dict(soft_cutoff_s=0.5, bias_cal_frac=0.5, dock_ff=True)),
    # a 249 g drone in the same dock (section 5.7)
    "lite_base": dict(cfg=LITE),
    "lite_pd": dict(cfg=_merge(_merge(_merge({}, LITE), POCKET), DRIFT)),
    "lite_pd_push": dict(cfg=_merge(_merge(_merge({}, LITE_PUSH), POCKET), DRIFT)),
    # the mothership spec (REPORT section 5), each row alone and together, on drift + beacons + controller
    "sp_rotors": dict(cfg=_merge(_merge(_merge({}, POCKET), DRIFT), ROTORS_OUT), opts=CTL_SPEC),
    "sp_steady": dict(cfg=_merge(_merge(_merge({}, POCKET), DRIFT), STEADY), opts=CTL_SPEC),
    "sp_bcast": dict(cfg=_merge(_merge(_merge({}, POCKET), DRIFT), BCAST50), opts=CTL_SPEC),
    "sp_all": dict(cfg=_merge(_merge(_merge(_merge(_merge({}, POCKET), DRIFT), ROTORS_OUT), STEADY), BCAST50), opts=CTL_SPEC),
    # channel dock variants (PHASE_3 section 5.8). Triangle anchors only: no pocket beacons
    "ch_base": dict(cfg=CHANNEL),
    "ch_drift": dict(cfg=_merge(_merge({}, CHANNEL), DRIFT)),
    "ch_ctl": dict(cfg=CHANNEL, opts=CTL_SPEC),                       # no drift, with the controller options
    "ch_drift_ctl": dict(cfg=_merge(_merge({}, CHANNEL), DRIFT), opts=CTL_SPEC),
    "ch_wide_drift": dict(cfg=_merge(_merge({}, CH_WIDE), DRIFT), opts=CTL_SPEC),
    "ch_narrow_drift": dict(cfg=_merge(_merge({}, CH_NARROW), DRIFT), opts=CTL_SPEC),
    "ch_slide_drift": dict(cfg=_merge(_merge({}, CH_SLIDE), DRIFT), opts=CTL_SPEC),
    "ch_slide_loose_drift": dict(cfg=_merge(_merge({}, CH_SLIDE_LOOSE), DRIFT), opts=CTL_SPEC),
    "ch_pb_drift": dict(cfg=_merge(_merge(_merge({}, CHANNEL), POCKET), DRIFT), opts=CTL_SPEC),  # + a beacon by each slot
    "base_ctl": dict(opts=dict(soft_cutoff_s=0.5, bias_cal_frac=0.5, dock_ff=True, dob_tau_s=0.1)),
    "pocket_all_drift_nodual": dict(cfg=_merge(_merge(_merge(_merge(_merge({}, LIP10), WINDWARD), SLOT20), DRIFT),
                                               POCKET), cell=dict(anchor_set="spec4")),
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
    e = EXPS[exp]
    cfg = _merge(load_cfg(), e.get("cfg", {}))
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
