"""Design variants against the baseline on the same cells and seeds (nominal sensors).

    python -m docksim.compare [--max-seed 60]     # writes docking/results/dock/variants.{md,json}

Seeds are deterministic per (trial, cell), so the baseline and spec4 rows for the same cell and
seed are the same trials the variants would have flown: no re-run is needed for them.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
from collections import Counter

from delivery.summarise import wilson

from .matrix import OUT

VARIANTS = [("baseline", "spec as built: 3 anchors, 38 mm lip, 1 post, wind from the nose"),
            ("spec4", "+ dock anchor A4"),
            ("lip10", "starboard lip 0.10 m (was 0.038 m)"),
            ("dual", "two posts and two brackets per pocket"),
            ("windward", "mothership yaws: wind from starboard"),
            ("slot20", "launch slot 20 mm (was 12 mm)"),
            ("still", "mothership hovers with no motion at all"),
            ("drift", "mothership hovers relative to the air (drifts with the mean wind)"),
            ("still_drift", "both"),
            ("all", "all geometry changes + A4"),
            ("all_drift", "all geometry changes + A4 + drifting hover"),
            ("all_drift_nodual", "as all_drift, without the two-post bracket"),
            ("pocket_drift", "3 anchors + a short-range beacon per pocket (3 cm, +-2 cm bias), drifting hover"),
            ("pocket_same_drift", "as pocket_drift, beacons as noisy as A1-A3 (5 cm, +-5 cm)"),
            ("pocket_all_drift_nodual", "pocket beacons + all_drift_nodual"),
            ("pd_soft", "pocket_drift + collective ramped to 0 over 0.5 s at cutoff"),
            ("pd_cal", "pocket_drift + UWB bias calibrated while parked (half the bias left)"),
            ("pd_ff", "pocket_drift + mothership acceleration feedforward"),
            ("pd_dob", "pocket_drift + gust observer (0.1 s), free flight only"),
            ("pd_ctl", "pocket_drift + all four controller options"),
            ("pd_nodob", "pd_ctl without the disturbance observer"),
            ("sp_rotors", "spec mothership: lift rotors 0.51 m clear of the dock (with pd_nodob's options)"),
            ("sp_steady", "spec mothership: half the hover motion"),
            ("sp_bcast", "spec mothership: motion broadcast at 50 Hz, 10-20 ms"),
            ("sp_all", "spec mothership: all three"),
            ("ch_base", "channel dock: one walled lane, 0.50 m funnel mouth at the rear, drones nose to tail"),
            ("ch_ctl", "channel + soft cutoff, parked calibration, dock feedforward"),
            ("ch_drift", "channel + drifting hover"),
            ("ch_drift_ctl", "channel + drift + the three controller options"),
            ("ch_wide_drift", "as ch_drift_ctl, 0.80 m mouth"),
            ("ch_narrow_drift", "as ch_drift_ctl, 0.35 m mouth"),
            ("ch_pb_drift", "as ch_drift_ctl, plus a short-range beacon by each slot"),
            ("lite_base", "249 g drone (Orin Nano), spec dock"),
            ("lite_pd", "249 g drone, pocket beacons + drift"),
            ("lite_pd_push", "as lite_pd, approach push 0.25 N (was 0.5 N)"),
            ("base_ctl", "spec baseline + all four controller options")]
POST_FREE = ("prop_strike", "never_left_magnets", "slow_clear", "touched_ridge", "touched_parked")


def _open(path):
    if os.path.exists(path):
        return open(path)
    if os.path.exists(path + ".gz"):
        return gzip.open(path + ".gz", "rt")
    return None


def load(exp, kind, max_seed, hover=None):
    f = _open(os.path.join(OUT, f"{exp}_{kind}.csv"))
    if f is None:
        return []
    with f:
        return [r for r in csv.DictReader(f) if r.get("error_label", "none") == "none"
                and r["noise_scale"] == "1.0" and r["anchor_loss_label"] == "none" and int(r["seed"]) <= max_seed
                and (hover is None or r["hover"] == hover)]


def frac(rows, ok):
    n = len(rows)
    k = sum(ok(r) for r in rows)
    return (k / n if n else None), wilson(k, n) if n else (None, None), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-seed", type=int, default=60)
    ap.add_argument("--hover", default=None, help="restrict to one hover level, e.g. moderate")
    a = ap.parse_args()
    res = {}
    passed = lambda r: str(r["passed_bool"]).lower() == "true"
    for exp, desc in VARIANTS:
        rec = load(exp, "recovery", a.max_seed, a.hover)
        lau = load(exp, "launch", a.max_seed, a.hover)
        if not rec:
            continue
        d = dict(desc=desc)
        d["recovery"] = frac(rec, passed)
        d["target"] = frac([r for r in rec if r["hover"] == "moderate" and r["wind_m_s"] == "6"], passed)
        for w in ("0", "3", "6", "9"):
            d[f"wind{w}"] = frac([r for r in rec if r["wind_m_s"] == w], passed)
        d["fails"] = dict(Counter(r["fail_reason_label"] for r in rec if not passed(r)).most_common(5))
        d["launch_spec"] = frac(lau, passed)
        d["launch_no_post"] = frac(lau, lambda r: not any(f in r["fail_all_label"].split() for f in POST_FREE))
        d["post_contact_frac"] = frac(lau, lambda r: float(r.get("post_contacts_count") or 0) > 0)
        res[exp] = d
    f2 = lambda t: "-" if t[0] is None else f"{t[0]:.3f}"
    scope = f"{a.hover} hover only (24 cells)" if a.hover else "the 72 cells hover x wind x neighbours x box"
    L = [f"Nominal sensors, {scope}, seeds 1-{a.max_seed} (spec4: 1-50).\n",
         "| variant | change | recovery | target cell (moderate, 6 m/s) | 0 m/s | 3 m/s | 6 m/s | 9 m/s | "
         "launch, spec criteria | launch without the post clause | launches touching a post | n recoveries |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for exp, d in res.items():
        L.append(f"| {exp} | {d['desc']} | **{f2(d['recovery'])}** | **{f2(d['target'])}** | {f2(d['wind0'])} | "
                 f"{f2(d['wind3'])} | {f2(d['wind6'])} | {f2(d['wind9'])} | {f2(d['launch_spec'])} | "
                 f"{f2(d['launch_no_post'])} | {f2(d['post_contact_frac'])} | {d['recovery'][2]} |")
    L.append("\nMain recovery failures:\n")
    for exp, d in res.items():
        L.append(f"- {exp}: {d['fails']}")
    tag = f"_{a.hover}" if a.hover else ""
    with open(os.path.join(OUT, f"variants{tag}.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    with open(os.path.join(OUT, f"variants{tag}.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
