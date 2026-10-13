"""Summarise a follow-bench JSON into per-scenario markdown tables (mean over seeds)."""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict

ORDER = ["B", "B+5", "1", "1+2", "1+2+3", "1+2+3+4", "1-5"]


def mean(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))]
    return sum(xs) / len(xs) if xs else float("nan")


def fmt(x, d=2):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{d}f}"


def main(path: str):
    rs = [r for r in json.load(open(path)) if "error" not in r]
    errs = [r for r in json.load(open(path)) if "error" in r]
    by = defaultdict(list)
    for r in rs:
        by[(r["scenario"], r["variant"])].append(r)
    scen = sorted({k[0] for k in by})
    cols = [("aborted", "abort", lambda g: f"{sum(r['aborted'] for r in g)}/{len(g)}"),
            ("full_stops", "stops", lambda g: fmt(mean([r['full_stops'] for r in g]), 1)),
            ("still_frac", "still%", lambda g: fmt(100 * mean([r['still_frac'] for r in g]), 0)),
            ("rms_accel", "acc", lambda g: fmt(mean([r['rms_accel'] for r in g]))),
            ("valid_frac", "track%", lambda g: fmt(100 * mean([r['valid_frac'] for r in g]), 0)),
            ("coast_frac", "coast%", lambda g: fmt(100 * mean([r['coast_frac'] for r in g]), 0)),
            ("lost_frac", "lost%", lambda g: fmt(100 * mean([r['lost_frac'] for r in g]), 0)),
            ("losses", "losses", lambda g: fmt(mean([r['losses'] for r in g]), 1)),
            ("est_err_rms", "err m", lambda g: fmt(mean([r['est_err_rms'] for r in g]))),
            ("range_err_rms", "rng m", lambda g: fmt(mean([r['range_err_rms'] for r in g]))),
            ("wrong_target_s", "wrong s", lambda g: fmt(mean([r['wrong_target_s'] for r in g]), 1)),
            ("reacq", "reacq s", lambda g: fmt(mean([r['reacq_after_occlusion_s'] for r in g if r['reacq_after_occlusion_s'] is not None]), 1)),
            ("min_range", "min r", lambda g: fmt(mean([r['min_range'] for r in g])))]
    out = []
    for sc in scen:
        note = ""
        out.append(f"\n### {sc}\n")
        out.append("| variant | " + " | ".join(c[1] for c in cols) + " |")
        out.append("|---|" + "---|" * len(cols))
        for v in ORDER:
            g = by.get((sc, v))
            if g:
                out.append(f"| {v} | " + " | ".join(c[2](g) for c in cols) + " |")
    if errs:
        out.append(f"\n{len(errs)} runs errored: " + "; ".join(f"{e['variant']}/{e['scenario']}: {e['error']}" for e in errs[:5]))
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
