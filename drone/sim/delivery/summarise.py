"""Tables for the delivery report from docking/results/delivery/*.csv.

    python -m delivery.summarise            # writes summary.json and summary.md next to the CSVs
"""
from __future__ import annotations

import csv
import glob
import json
import math
import os
from collections import Counter, defaultdict

from .report import OUT
from .scenarios import SCENARIOS

KINDS = ["structure", "railing", "glass", "eave", "awning", "wire_metal", "wire_nonmetal", "tree", "person",
         "netting", "ground"]


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _b(x):
    return str(x).strip().lower() in ("true", "1")


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def load(exp):
    p = os.path.join(OUT, f"{exp}.csv")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return list(csv.DictReader(f))


def summarise_rows(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["scenario_label"]].append(r)
    out = {}
    for sid in SCENARIOS:
        rs = [r for r in by.get(sid, []) if r.get("error_label", "none") == "none"]
        n = len(rs)
        if n == 0:
            continue
        lab = [r for r in rs if _b(r["corridor_3m_bool"])]
        nlab = [r for r in rs if not _b(r["corridor_3m_bool"])]
        d = sum(_b(r["delivered_bool"]) for r in rs)
        dl = sum(_b(r["delivered_bool"]) for r in lab)
        pk = sum(_b(r["pickup_bool"]) for r in rs)
        both = sum(_b(r["delivered_bool"]) and _b(r["pickup_bool"]) for r in rs)
        coll = {k: sum(int(float(r.get(f"coll_{k}_count", 0) or 0)) for r in rs) for k in KINDS}
        trials_with_coll = sum(int(float(r["collisions_count"])) > 0 for r in rs)
        limits = Counter(r["sensor_limit_label"] for r in rs if r["sensor_limit_label"])
        reasons = Counter(r["give_up_reason_label"] for r in rs if _b(r["gave_up_bool"]))
        out[sid] = dict(
            trials_count=n, corridor_true_count=len(lab), errors_count=len(by.get(sid, [])) - n,
            delivered_count=d, delivered_frac=d / n, delivered_ci95=wilson(d, n),
            delivered_given_corridor_frac=(dl / len(lab)) if lab else None,
            pickup_count=pk, pickup_frac=pk / n, pickup_ci95=wilson(pk, n),
            delivered_and_pickup_frac=both / n,
            correct_give_up_count=sum(_b(r["correct_give_up_bool"]) for r in rs),
            wrong_give_up_count=sum(_b(r["wrong_give_up_bool"]) for r in rs),
            no_corridor_but_tried_to_deliver_count=sum(_b(r["delivered_bool"]) for r in nlab),
            give_up_reasons=dict(reasons),
            fallback=dict(Counter(r["fallback_label"] for r in rs)),
            results=dict(Counter(r["result_label"] for r in rs)),
            crashes_count=sum(_b(r["crashed_bool"]) for r in rs),
            trials_with_collision_count=trials_with_coll, collisions_by_kind=coll,
            cage_contacts_total_count=sum(int(float(r["cage_contacts_count"])) for r in rs),
            cage_contacts_trials_count=sum(int(float(r["cage_contacts_count"])) > 0 for r in rs),
            time_to_delivery_median_s=med([_f(r["time_to_delivery_s"]) for r in rs]),
            time_to_delivery_p90_s=pct([_f(r["time_to_delivery_s"]) for r in rs], 0.9),
            time_total_median_s=med([_f(r["time_total_s"]) for r in rs]),
            energy_total_median_j=med([_f(r["energy_total_j"]) for r in rs]),
            energy_total_p90_j=pct([_f(r["energy_total_j"]) for r in rs], 0.9),
            release_err_median_m=med([_f(r["release_nav_err_m"]) for r in rs]),
            delivery_err_median_m=med([_f(r["delivery_err_m"]) for r in rs]),
            delivery_err_p90_m=pct([_f(r["delivery_err_m"]) for r in rs], 0.9),
            pickup_err_median_m=med([_f(r["pickup_err_m"]) for r in rs]),
            pickup_err_p90_m=pct([_f(r["pickup_err_m"]) for r in rs], 0.9),
            nav_err_max_median_m=med([_f(r["nav_err_max_m"]) for r in rs]),
            sensor_limits=dict(limits.most_common()),
        )
    return out


def fmt(x, nd=2):
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def main():
    res = {}
    for p in sorted(glob.glob(os.path.join(OUT, "*.csv"))):
        exp = os.path.basename(p)[:-4]
        rows = load(exp)
        if rows:
            res[exp] = summarise_rows(rows)
    with open(os.path.join(OUT, "summary.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    lines = []
    for exp, s in res.items():
        lines.append(f"\n## {exp}\n")
        lines.append("| scen | n | corr | deliv | deliv/corr | pickup | right GU | wrong GU | coll trials | cage | t_del med s | E med kJ | d_err med m | p_err med/p90 m |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for sid, v in s.items():
            lines.append(f"| {sid} | {v['trials_count']} | {v['corridor_true_count']} | {v['delivered_frac']:.2f} | "
                         f"{fmt(v['delivered_given_corridor_frac'])} | {v['pickup_frac']:.2f} | {v['correct_give_up_count']} | "
                         f"{v['wrong_give_up_count']} | {v['trials_with_collision_count']} | {v['cage_contacts_trials_count']} | "
                         f"{fmt(v['time_to_delivery_median_s'], 0)} | {fmt((v['energy_total_median_j'] or 0) / 1e3, 1)} | "
                         f"{fmt(v['delivery_err_median_m'])} | {fmt(v['pickup_err_median_m'])}/{fmt(v['pickup_err_p90_m'])} |")
    with open(os.path.join(OUT, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
