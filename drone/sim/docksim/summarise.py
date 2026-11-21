"""Dock test-matrix tables from docking/results/dock/<exp>_{launch,recovery}.csv.

    python -m docksim.summarise          # writes summary.json and summary.md next to the CSVs
"""
from __future__ import annotations

import csv
import glob
import json
import os
from collections import Counter, defaultdict

from delivery.summarise import wilson

from .matrix import OUT

VARS = [("hover", ["calm", "moderate", "gusty"]), ("wind_m_s", ["0", "3", "6", "9"]),
        ("noise_scale", ["1.0", "2.0"]), ("anchor_loss_label", ["none", "one"]),
        ("parked_neighbours", ["0", "1", "2"]), ("launch_box_cell_bool", ["False", "True"])]


def _b(x):
    return str(x).strip().lower() in ("true", "1")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _load(path):
    with open(path) as f:
        return [r for r in csv.DictReader(f) if r.get("error_label", "none") == "none"]


def rate(rows, key="passed_bool"):
    n = len(rows)
    k = sum(_b(r[key]) for r in rows)
    lo, hi = wilson(k, n) if n else (None, None)
    return dict(n=n, pass_frac=k / n if n else None, ci95=(lo, hi))


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def summarise(exp):
    out = {}
    for kind in ("recovery", "launch"):
        p = os.path.join(OUT, f"{exp}_{kind}.csv")
        if not os.path.exists(p):
            continue
        rows = _load(p)
        d = dict(overall=rate(rows), fail_reasons=dict(Counter(r["fail_reason_label"] for r in rows
                                                               if not _b(r["passed_bool"])).most_common()))
        d["by_variable"] = {v: {val: rate([r for r in rows if r[v] == val]) for val in vals} for v, vals in VARS}
        tgt = [r for r in rows if r["hover"] == "moderate" and r["wind_m_s"] == "6"]
        d["target_moderate_6"] = rate(tgt)
        d["target_moderate_6_nominal"] = rate([r for r in tgt if r["noise_scale"] == "1.0" and r["anchor_loss_label"] == "none"])
        by_cell = defaultdict(list)
        for r in rows:
            by_cell[(r["hover"], r["wind_m_s"], r["noise_scale"], r["anchor_loss_label"], r["parked_neighbours"],
                     r["launch_box_cell_bool"])].append(r)
        cells = {"|".join(k): rate(v) for k, v in by_cell.items()}
        d["cells_meeting_95"] = sum(1 for v in cells.values() if v["pass_frac"] is not None and v["pass_frac"] >= 0.95)
        d["cells"] = len(cells)
        d["cells_detail"] = cells
        if kind == "recovery":
            ok = [r for r in rows if _b(r["passed_bool"])]
            d["time_s_median_p90_passed"] = (pct([_f(r["time_s"]) for r in ok], 0.5), pct([_f(r["time_s"]) for r in ok], 0.9))
            d["aborts_mean"] = sum(int(float(r["aborts_count"])) for r in rows) / max(len(rows), 1)
            d["peak_contact_force_n_median_p95"] = (pct([_f(r["peak_contact_force_n"]) for r in rows], 0.5),
                                                    pct([_f(r["peak_contact_force_n"]) for r in rows], 0.95))
            d["final_pos_err_m_median_passed"] = pct([_f(r["final_pos_err_m"]) for r in ok], 0.5)
            d["post_contact_speed_m_s_p95"] = pct([_f(r["post_contact_speed_m_s"]) for r in rows], 0.95)
            d["anchor_lost"] = {a: rate([r for r in rows if r["anchor_lost"] == a]) for a in ("A1", "A2", "A3", "A4")
                                if any(r["anchor_lost"] == a for r in rows)}
        else:
            d["post_contact_s_median_p95"] = (pct([_f(r.get("post_contact_s")) for r in rows], 0.5),
                                              pct([_f(r.get("post_contact_s")) for r in rows], 0.95))
            d["post_force_peak_n_median_p95"] = (pct([_f(r.get("post_force_peak_n")) for r in rows], 0.5),
                                                 pct([_f(r.get("post_force_peak_n")) for r in rows], 0.95))
            # the spec's launch criteria without the guide-post clause (the slot rides the post by design)
            nopost = [r for r in rows if not any(f in r["fail_all_label"].split() for f in
                                                 ("prop_strike", "never_left_magnets", "slow_clear", "touched_ridge",
                                                  "touched_parked"))]
            d["pass_frac_excluding_post_contact"] = len(nopost) / max(len(rows), 1)
            d["thrust_at_free_n_median"] = pct([_f(r["thrust_at_free_n"]) for r in rows], 0.5)
            d["time_free_to_clear_s_p95"] = pct([_f(r["time_free_to_clear_s"]) for r in rows], 0.95)
        out[kind] = d
    return out


def fmt_rate(v):
    if not v or v["n"] == 0:
        return "-"
    return f"{v['pass_frac']:.3f} ({v['ci95'][0]:.3f}-{v['ci95'][1]:.3f}, n={v['n']})"


def main():
    exps = sorted({os.path.basename(p).rsplit("_", 1)[0] for p in glob.glob(os.path.join(OUT, "*_recovery.csv"))})
    res = {e: summarise(e) for e in exps}
    with open(os.path.join(OUT, "summary.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    L = []
    for e, s in res.items():
        L.append(f"\n## {e}\n")
        for kind in ("recovery", "launch"):
            if kind not in s:
                continue
            d = s[kind]
            L.append(f"**{kind}**: overall {fmt_rate(d['overall'])}; target cell (moderate, 6 m/s) "
                     f"{fmt_rate(d['target_moderate_6'])}; nominal sensors in the target cell "
                     f"{fmt_rate(d['target_moderate_6_nominal'])}; cells at >= 0.95: {d['cells_meeting_95']}/{d['cells']}\n")
            L.append("| variable | value | pass fraction (95 % CI, n) |\n|---|---|---|")
            for v, vals in VARS:
                for val in vals:
                    L.append(f"| {v} | {val} | {fmt_rate(d['by_variable'][v][val])} |")
            L.append(f"\nFail reasons: {d['fail_reasons']}\n")
            extra = {k: v for k, v in d.items() if k not in ("overall", "fail_reasons", "by_variable", "cells_detail",
                                                              "target_moderate_6", "target_moderate_6_nominal")}
            L.append("Other: " + json.dumps(extra, default=str) + "\n")
    with open(os.path.join(OUT, "summary.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
