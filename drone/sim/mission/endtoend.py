"""End-to-end delivery success from the parts: launch (dock matrix) x balcony mission (mission
runs) x recovery (dock matrix), per wind band, and for a full four-drone sortie flown in the
spec's orders.

    python -m mission.endtoend [--dock-exp baseline] [--mission-exp baseline]

The dock matrix gives pass rates per (hover level, mean wind, parked neighbours) at nominal
sensors. A mission row is mapped to the dock cell with its own hover level and the nearest
matrix wind. The four-drone sortie uses the spec's orders:
  launch   P4 (P3 parked: 1 neighbour), P1 (P2: 1), P3 (P2: 1), P2 (none: 0)
  recovery P2 (none: 0), P3 (P2: 1), P1 (P2: 1), P4 (P3: 1)
The parts are treated as independent (they share only the wind), so this is an estimate.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict

from docksim.matrix import OUT as DOCK_OUT

from .report import OUT as MIS_OUT

WINDS = [0, 3, 6, 9]


def _b(x):
    return str(x).strip().lower() in ("true", "1")


def dock_rates(exp, kind, launch_ok_without_post=True):
    p = os.path.join(DOCK_OUT, f"{exp}_{kind}.csv")
    with open(p) as f:
        rows = [r for r in csv.DictReader(f) if r.get("error_label", "none") == "none"
                and r["noise_scale"] == "1.0" and r["anchor_loss_label"] == "none"]
    agg = defaultdict(lambda: [0, 0])
    for r in rows:
        if kind == "launch" and launch_ok_without_post:
            ok = not any(f in r["fail_all_label"].split() for f in
                         ("prop_strike", "never_left_magnets", "slow_clear", "touched_ridge", "touched_parked"))
        else:
            ok = _b(r["passed_bool"])
        k = (r["hover"], int(float(r["wind_m_s"])), int(r["parked_neighbours"]))
        agg[k][0] += ok
        agg[k][1] += 1
    return {k: v[0] / v[1] for k, v in agg.items() if v[1]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dock-exp", default="baseline")
    ap.add_argument("--mission-exp", default="baseline")
    a = ap.parse_args()
    L = dock_rates(a.dock_exp, "launch")
    Ls = dock_rates(a.dock_exp, "launch", launch_ok_without_post=False)
    R = dock_rates(a.dock_exp, "recovery")
    with open(os.path.join(MIS_OUT, f"{a.mission_exp}.csv")) as f:
        mis = [r for r in csv.DictReader(f) if r.get("error_label", "none") == "none"]
    out = {}
    bands = defaultdict(list)
    for r in mis:
        w = min(WINDS, key=lambda x: abs(x - float(r["wind_m_s"])))
        h = r["hover_level"]
        p_one = L.get((h, w, 1), 0) * float(_b(r["mission_ok_bool"])) * R.get((h, w, 1), 0)
        launch = [L.get((h, w, n), 0) for n in (1, 1, 1, 0)]
        rec = [R.get((h, w, n), 0) for n in (0, 1, 1, 1)]
        p_sortie_dock = 1.0
        for x in launch + rec:
            p_sortie_dock *= x
        bands[(h, w)].append((p_one, float(_b(r["mission_ok_bool"])), p_sortie_dock,
                              Ls.get((h, w, 1), 0)))
    lines = ["| hover | wind (nearest matrix) m/s | missions | mission ok | one delivery end to end "
             "(launch x mission x recovery) | four-drone sortie, dock steps only | launch by the spec's "
             "post clause |", "|---|---|---|---|---|---|---|"]
    for k in sorted(bands, key=lambda k: (["calm", "moderate", "gusty"].index(k[0]), k[1])):
        v = bands[k]
        n = len(v)
        row = dict(n=n, mission_ok=sum(x[1] for x in v) / n, one_delivery=sum(x[0] for x in v) / n,
                   sortie_dock=sum(x[2] for x in v) / n, launch_spec=sum(x[3] for x in v) / n)
        out[f"{k[0]}|{k[1]}"] = row
        lines.append(f"| {k[0]} | {k[1]} | {n} | {row['mission_ok']:.3f} | {row['one_delivery']:.3f} | "
                     f"{row['sortie_dock']:.3f} | {row['launch_spec']:.3f} |")
    allv = [x for v in bands.values() for x in v]
    out["ALL"] = dict(n=len(allv), one_delivery=sum(x[0] for x in allv) / len(allv),
                      mission_ok=sum(x[1] for x in allv) / len(allv))
    lines.append(f"\nAll missions: one delivery end to end {out['ALL']['one_delivery']:.3f} "
                 f"(mission alone {out['ALL']['mission_ok']:.3f}).")
    os.makedirs(MIS_OUT, exist_ok=True)
    with open(os.path.join(MIS_OUT, "endtoend.json"), "w") as f:
        json.dump(out, f, indent=1)
    with open(os.path.join(MIS_OUT, "endtoend.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
