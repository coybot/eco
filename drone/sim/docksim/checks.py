"""Passive dock checks with fixed inputs (spec 7 and 12).

  H1  magnet hold test: a parked drone (motors off) under vertical load factor -0.5..+1.5 g,
      0.3 g sideways (x and z), a steady 3 N rearward force, and the combinations; with and
      without the box. Also the lift-off load factor found by sweep.
  H2  parked drone through 20 s of gusty hover motion in 9 m/s wind.
  P1  position hold in calm air (spec 7: 0.10 m or better, 95 % of the time) at the hold
      point (y = 2.0) and at the approach height (y = 0.17), 12 seeds each, plus heading error.

    python -m docksim.checks          # writes docking/results/dock/checks.json
"""
from __future__ import annotations

import json
import math
import os
from multiprocessing import get_context

import numpy as np

from .config import G_M_S2, load_cfg
from .drone import Pilot
from .matrix import OUT
from .scene import DroneSpec
from .sim import DockSim


def _parked(cfg, box, gravity, force=None, T=2.0, hover=None, wind=0.0, seed=1):
    rng = np.random.default_rng(seed)
    px = cfg["dock"]["pocket_x_m"][1]
    sim = DockSim(cfg, [DroneSpec("a", 1, (px, 0.0, 0.0), box=box)], hover or "calm", wind, rng,
                  hover_scale=1.0 if hover else 0.0)
    if hover is None:
        sim.wind.sigma = 0.0
        sim.wind.turb[:] = 0.0
    p0, _ = sim.pose(0)
    if gravity is not None:
        sim.gravity_override = np.asarray(gravity, float)
    if force is not None:
        sim.extra_force[0] = (np.asarray(force, float), sim.data.xipos[sim.body_id[0]].copy())
    shift = 0.0
    lift = 0.0
    while sim.t < T:
        if force is not None:
            sim.extra_force[0] = (np.asarray(force, float), sim.data.xipos[sim.body_id[0]].copy())
        sim.tick()
        p, _ = sim.pose(0)
        shift = max(shift, float(np.linalg.norm(p - p0)))
        lift = max(lift, float(p[1]))
    return shift, lift


def hold_test(cfg):
    ht = cfg["dock_ops"]["hold_test"]
    g = G_M_S2
    cases = []
    for box in (False, True):
        for n in (ht["vertical_g"][0], 0.0, 1.0, ht["vertical_g"][1]):
            cases.append((box, f"vertical load factor {n:+.1f} g", [0, -n * g, 0], None))
        s = ht["sideways_g"] * g
        for ax, lbl in ((0, "+x"), (0, "-x"), (2, "+z"), (2, "-z")):
            gv = [0.0, -g, 0.0]
            gv[ax] = s if lbl[0] == "+" else -s
            cases.append((box, f"sideways {ht['sideways_g']} g {lbl}", gv, None))
            gv2 = list(gv)
            gv2[1] = -ht["vertical_g"][0] * g
            cases.append((box, f"sideways {ht['sideways_g']} g {lbl} at {ht['vertical_g'][0]:+.1f} g", gv2, None))
        cases.append((box, f"steady {ht['rear_force_n']} N rearward", [0, -g, 0], [-ht["rear_force_n"], 0, 0]))
        cases.append((box, f"steady {ht['rear_force_n']} N rearward at {ht['vertical_g'][0]:+.1f} g",
                      [0, -ht["vertical_g"][0] * g, 0], [-ht["rear_force_n"], 0, 0]))
    rows = []
    for box, lbl, gv, f in cases:
        shift, lift = _parked(cfg, box, gv, f)
        rows.append(dict(box=box, case=lbl, max_shift_m=round(shift, 5), max_lift_m=round(lift, 5),
                         passed=bool(shift < ht["max_shift_m"])))
    # lift-off load factor by sweep
    lo = {}
    for box in (False, True):
        n_off = None
        for n in np.arange(-0.5, -4.01, -0.1):
            shift, lift = _parked(cfg, box, [0, -n * g, 0], None, T=1.0)
            if lift > 0.002:
                n_off = round(float(n), 2)
                break
        lo["with box" if box else "empty"] = n_off
    return rows, lo


def _hold_one(a):
    level, y, seed = a
    cfg = load_cfg()
    rng = np.random.default_rng([seed, 31])
    px, z = cfg["dock"]["pocket_x_m"][1], cfg["dock_ops"]["recovery"]["hold_z_m"]
    sim = DockSim(cfg, [DroneSpec("a", 1, (px, y, z), active=True)], level, 0.0, rng)
    pil = Pilot(sim, 0, cfg, rng, "hold", 1, cfg["mothership_airframe"]["anchor_sets"]["triangle"])
    pil.target = np.array([px, y, z])
    sim.thrust[0][:] = sim.model.body_mass[sim.body_id[0]] * G_M_S2 / 4
    E, H = [], []
    while sim.t < 25.0:
        pil.step(sim.t)
        sim.tick()
        if sim.t > 5.0 and pil.k % 10 == 0:
            p, R = sim.pose(0)
            E.append(math.hypot(p[0] - px, p[2] - z))
            H.append(abs(math.degrees(math.atan2(-R[2, 0], R[0, 0]))))
    return float(np.percentile(E, 95)), float(np.percentile(H, 95))


def hover_hold(workers=8):
    out = {}
    with get_context("spawn").Pool(workers) as pool:
        for level in ("calm", "moderate"):
            for y in (2.0, 0.17):
                res = pool.map(_hold_one, [(level, y, s) for s in range(12)])
                e = [r[0] for r in res]
                h = [r[1] for r in res]
                out[f"{level} y={y}"] = dict(p95_horiz_err_m_median=round(float(np.median(e)), 3),
                                             p95_horiz_err_m_worst=round(float(np.max(e)), 3),
                                             seeds_meeting_0p10=int(sum(x <= 0.10 for x in e)), seeds=len(e),
                                             p95_heading_err_deg_median=round(float(np.median(h)), 2))
    return out


def main():
    cfg = load_cfg()
    rows, liftoff = hold_test(cfg)
    gusty = {}
    for box in (False, True):
        shift, lift = _parked(cfg, box, None, None, T=20.0, hover="gusty", wind=9.0, seed=5)
        gusty["with box" if box else "empty"] = dict(max_shift_m=round(shift, 5), max_lift_m=round(lift, 5))
    hh = hover_hold()
    res = dict(H1_hold_test=rows, H1_liftoff_load_factor_g=liftoff, H2_gusty_parked=gusty, P1_position_hold=hh)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "checks.json"), "w") as f:
        json.dump(res, f, indent=1)
    for r in rows:
        print(f"  H1 {'box  ' if r['box'] else 'empty'} {r['case']:45s} shift {r['max_shift_m'] * 1e3:6.2f} mm  "
              f"{'PASS' if r['passed'] else 'FAIL'}")
    print("  H1 lift-off load factor:", liftoff)
    print("  H2 gusty 9 m/s parked:", gusty)
    for k, v in hh.items():
        print(f"  P1 {k:16s} {v}")


if __name__ == "__main__":
    main()
