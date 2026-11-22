"""Single launch, recovery and hold trials with the spec's pass criteria (section 12).

    python -m docksim.trial recovery --seed 3 --hover moderate --wind 6 -v
    python -m docksim.trial launch --seed 3 --box -v
"""
from __future__ import annotations

import argparse
import math
import time

import numpy as np

from .config import channel_geometry, effective_cfg, load_cfg
from .drone import Pilot
from .scene import DroneSpec
from .sim import (CAT_D_BODY, CAT_D_BOX, CAT_D_BRACKET, CAT_D_PAD, CAT_FLOOR, CAT_POST, CAT_RIDGE, CAT_STOP,
                  CAT_WALL, DockSim)


def choose_pockets(neighbours: int, rng):
    """Target pocket and the occupied neighbours for a parked-neighbour count of 0, 1 or 2."""
    if neighbours == 0:
        return int(rng.integers(4)), []
    if neighbours == 2:
        p = int(rng.choice([1, 2]))
        return p, [p - 1, p + 1]
    p = int(rng.integers(4))
    side = [q for q in (p - 1, p + 1) if 0 <= q <= 3]
    return p, [int(rng.choice(side))]


def choose_slots(neighbours: int, rng):
    """Channel dock: the drone flies to the slot behind the drones already parked (front first)."""
    k = {0: 0, 1: 1}.get(neighbours, None)
    if k is None:
        k = int(rng.choice([2, 3]))
    return k, list(range(k))


def _pick(cfg, cell, rng):
    if cfg.get("channel_dock", {}).get("enabled", False):
        return choose_slots(cell.get("parked_neighbours", 0), rng)
    return choose_pockets(cell.get("parked_neighbours", 0), rng)


def anchors_for(cfg, cell, rng):
    ma = cfg["mothership_airframe"]
    use = list(ma["anchor_sets"][cell.get("anchor_set", ma["anchor_set_label"])])
    lost = None
    if cell.get("anchor_loss", "none") == "one":
        lost = use[int(rng.integers(len(use)))]
        use.remove(lost)
    return use, lost


def _parked_specs(cfg, occupied):
    px = cfg["dock"]["pocket_x_m"]
    return [DroneSpec(f"n{k}", k, (px[k], 0.0, 0.0)) for k in occupied]


class Monitor:
    """Truth-side bookkeeping of contacts, speeds and strikes for the active drone (index 0)."""

    def __init__(self, sim, with_box):
        self.sim = sim
        self.with_box = with_box
        self.post_speed = None
        self.touch_speed = None
        self.parked_touch_high = False
        self.peak_force = 0.0
        self.strike = False
        self.post_contacts_after = 0
        self.post_force_peak = 0.0
        self.post_contact_s = 0.0
        self.ridge_contacts_after = 0
        self.parked_contacts_after = 0
        self.well_contacts_after = 0
        self.after = False                      # launch: contacts counted only after leaving the seat
        self.stop_speed = None                  # channel: speed at first touch of the drone ahead or the stop
        self.wall_speed = None
        self.wall_contacts = 0
        self.parked_force_peak = 0.0

    def step(self):
        sim = self.sim
        cs = sim.contacts_of(0)
        if cs:
            v, _ = sim.vel(0)
            p, _ = sim.pose(0)
        for own_oth, cat, own_cat, f, pos in cs:
            self.peak_force = max(self.peak_force, f)
            if cat == CAT_POST:
                if self.post_speed is None:
                    self.post_speed = float(np.linalg.norm(v))
                if self.after:
                    self.post_contacts_after += 1
                    self.post_force_peak = max(self.post_force_peak, f)
            elif cat in (CAT_FLOOR, CAT_RIDGE):
                if own_cat == CAT_D_BOX:
                    if self.after:
                        self.well_contacts_after += 1
                else:
                    if self.touch_speed is None:
                        self.touch_speed = float(np.linalg.norm(v))
                    if self.after and cat == CAT_RIDGE:
                        self.ridge_contacts_after += 1
            elif cat == CAT_STOP:
                if self.stop_speed is None:
                    self.stop_speed = float(np.linalg.norm(v))
            elif cat == CAT_WALL:
                self.wall_contacts += 1
                if self.wall_speed is None:
                    self.wall_speed = float(np.linalg.norm(v))
            elif own_oth >= 1:
                if self.stop_speed is None and p[1] < 0.06:
                    self.stop_speed = float(np.linalg.norm(v))
                if self.after:
                    self.parked_force_peak = max(self.parked_force_peak, f)
                if sim.cfg.get("channel_dock", {}).get("enabled", False):
                    # channel: nose-to-tail contact is the design; landing on top of a parked drone is not
                    po, _ = sim.pose(own_oth)
                    if p[1] > 0.045 and abs(p[0] - po[0]) < 0.2:
                        self.parked_touch_high = True
                elif p[1] > sim.cfg["dock_ops"]["pass"]["parked_touch_above_y_m"]:
                    self.parked_touch_high = True
                if self.after:
                    self.parked_contacts_after += 1
        if self.after and any(c[1] == CAT_POST for c in cs):
            self.post_contact_s += sim.dt
        if not self.strike and sim.in_strike_zone(0, self.with_box):
            self.strike = True


def _yaw_deg(R):
    return math.degrees(math.atan2(-R[2, 0], R[0, 0]))


def run_recovery(cfg, cell, seed, verbose=False, opts=None):
    opts = opts or {}
    rng = np.random.default_rng([seed, 11, cell.get("cell_id", 0)])
    cfg = effective_cfg(cfg)
    chan = cfg.get("channel_dock", {}).get("enabled", False)
    pocket, occ = _pick(cfg, cell, rng)
    use, lost = anchors_for(cfg, cell, rng)
    rec = cfg["dock_ops"]["recovery"]
    px = cfg["dock"]["pocket_x_m"][pocket]
    off = rec["start_offset_m"]
    x0 = channel_geometry(cfg)["x_mouth"] + cfg["channel_dock"]["hold_inside_mouth_m"] if chan else px
    start = (x0 + off[0], off[1], off[2])
    specs = [DroneSpec("a", pocket, start, active=True)] + _parked_specs(cfg, occ)
    sim = DockSim(cfg, specs, cell.get("hover", "moderate"), cell.get("wind", 6.0), rng,
                  hover_scale=cfg["mothership_airframe"].get("hover_motion_scale", 1.0))
    if "magnet_scale" in opts:
        sim.mag_scale = opts["magnet_scale"]
    pil = Pilot(sim, 0, cfg, rng, "recovery", pocket, use, cell.get("noise", 1.0), opts)
    hover = sim.model.body_mass[sim.body_id[0]] * 9.80665 / 4
    sim.thrust[0][:] = hover
    mon = Monitor(sim, False)
    diag = dict(appr=[], catch=[], aborts=[])

    def hook(pl, state):
        p_, R_ = sim.pose(0)
        e = p_ - np.array([px, 0.0, 0.0])
        if state == "APPROACH":
            diag["appr"].append((round(e[0], 3), round(p_[1], 3), round(pl.nav.r[0] - p_[0], 3), round(pl.nav.r[1] - p_[1], 3)))
        elif state == "PUSH_DESCEND":
            diag["catch"].append((round(e[0], 3), round(e[2], 3), round(p_[1], 3)))
        elif state == "ABORT_OUT":
            diag["aborts"].append(pl.events[-2][1] if len(pl.events) > 1 else "")
    pil.diag_hook = hook
    t_end = opts.get("t_max_s", 60.0)
    err_samples = []
    wall0 = time.time()
    while sim.t < t_end:
        pil.step(sim.t)
        sim.tick()
        mon.step()
        if pil.state not in ("INIT", "SETTLE"):
            p, _ = sim.pose(0)
            err_samples.append(float(np.linalg.norm(pil.nav.r - p)))
        if pil.state == "FALLBACK":
            break
        if pil.motors_off and sim.t - pil.t_motors_off > 1.5:
            break
    p, R = sim.pose(0)
    ps = cfg["dock_ops"]["pass"]
    t_off = pil.t_motors_off
    final_x = p[0] - px
    final_z = p[2]
    heading = _yaw_deg(R)
    seated = bool(pil.motors_off and abs(p[1]) < 0.003)
    fails = []
    if mon.strike:
        fails.append("prop_strike")
    if mon.parked_touch_high:
        fails.append("touched_parked")
    if mon.post_speed is not None and mon.post_speed > ps["post_contact_speed_m_s"]:
        fails.append("post_speed")
    if chan and mon.stop_speed is not None and mon.stop_speed > cfg["channel_dock"]["stop_contact_speed_m_s"]:
        fails.append("stop_speed")
    if mon.touch_speed is not None and mon.touch_speed > ps["touchdown_speed_m_s"]:
        fails.append("touchdown_speed")
    if pil.state == "FALLBACK":
        fails.append("fallback_after_aborts")
    elif t_off is None:
        fails.append("timeout_not_landed")
    else:
        if t_off > rec["time_limit_s"]:
            fails.append("over_time")
        if not seated:
            fails.append("not_seated")
        if abs(final_x) > ps["final_xz_err_m"] or abs(final_z) > ps["final_xz_err_m"]:
            fails.append("final_position")
        if abs(heading) > ps["final_heading_err_deg"]:
            fails.append("final_heading")
    row = dict(kind="recovery", seed=seed, pocket=f"P{pocket + 1}", neighbours_occupied=" ".join(f"P{k + 1}" for k in occ),
               anchor_lost=lost or "", wind_dir_from_nose_deg=round(math.degrees(sim.wind.dir_from_nose_rad), 1),
               passed_bool=not fails, fail_reason_label=fails[0] if fails else "", fail_all_label=" ".join(fails),
               time_s=round(t_off, 2) if t_off is not None else None, aborts_count=pil.aborts,
               peak_contact_force_n=round(mon.peak_force, 2),
               post_contact_speed_m_s=round(mon.post_speed, 3) if mon.post_speed is not None else None,
               touchdown_speed_m_s=round(mon.touch_speed, 3) if mon.touch_speed is not None else None,
               final_x_err_m=round(final_x, 4), final_z_err_m=round(final_z, 4),
               final_pos_err_m=round(math.hypot(final_x, final_z), 4), final_heading_err_deg=round(heading, 2),
               final_y_m=round(float(p[1]), 4),
               nav_err_median_m=round(float(np.median(err_samples)), 3) if err_samples else None,
               nav_err_p95_m=round(float(np.percentile(err_samples, 95)), 3) if err_samples else None,
               uwb_blocked_frac=round(pil.uwb.n_blocked / max(pil.uwb.n_total, 1), 3),
               nav_rejected_count=pil.nav.n_rej, final_state_label=pil.state, wall_s=round(time.time() - wall0, 2),
               approach_x_err_m=" ".join(str(a[0]) for a in diag["appr"]),
               approach_nav_x_err_m=" ".join(str(a[2]) for a in diag["appr"]),
               approach_nav_y_err_m=" ".join(str(a[3]) for a in diag["appr"]),
               catch_xz_err_m=" ".join(f"{c[0]}/{c[1]}" for c in diag["catch"]),
               abort_reasons_label=" ".join(diag["aborts"]), height_resets_count=pil.h_resets,
               stop_contact_speed_m_s=round(mon.stop_speed, 3) if mon.stop_speed is not None else None,
               wall_contact_speed_m_s=round(mon.wall_speed, 3) if mon.wall_speed is not None else None,
               events_label=" ".join(e[1] for e in pil.events if e[1].startswith("abort")))
    if verbose:
        print(pil.events)
        print("uwb blocked per anchor", dict(zip(pil.uwb.names, pil.uwb.blocked_by.tolist())), "of", pil.uwb.n_total)
        print(row)
    return row


def run_launch(cfg, cell, seed, verbose=False, opts=None):
    opts = opts or {}
    rng = np.random.default_rng([seed, 12, cell.get("cell_id", 0)])
    cfg = effective_cfg(cfg)
    chan = cfg.get("channel_dock", {}).get("enabled", False)
    pocket, occ = _pick(cfg, cell, rng)
    use, lost = anchors_for(cfg, cell, rng)
    box = bool(cell.get("launch_box", False))
    px = cfg["dock"]["pocket_x_m"][pocket]
    specs = [DroneSpec("a", pocket, (px, 0.0, 0.0), box=box, active=True)] + _parked_specs(cfg, occ)
    sim = DockSim(cfg, specs, cell.get("hover", "moderate"), cell.get("wind", 6.0), rng,
                  hover_scale=cfg["mothership_airframe"].get("hover_motion_scale", 1.0))
    pil = Pilot(sim, 0, cfg, rng, "launch", pocket, use, cell.get("noise", 1.0), opts)
    mon = Monitor(sim, box)
    lau = cfg["dock_ops"]["launch"]
    t_free = None
    thrust_at_free = None
    t_clear = None
    wall0 = time.time()
    # let the parked state settle under the magnets before anything happens
    while sim.t < opts.get("t_max_s", 15.0):
        pil.step(sim.t)
        sim.tick()
        if t_free is None and np.all(sim.magnet_gaps(0) > sim.mag_gap):
            t_free = sim.t
            thrust_at_free = float(sim.thrust[0].sum())
            mon.after = True
        mon.step()
        p, _ = sim.pose(0)
        if t_free is not None and t_clear is None and p[1] >= lau["clear_y_m"]:
            t_clear = sim.t
        if pil.state == "DONE":
            break
    fails = []
    if mon.strike:
        fails.append("prop_strike")
    if t_free is None:
        fails.append("never_left_magnets")
    elif t_clear is None or t_clear - t_free > lau["clear_time_s"]:
        fails.append("slow_clear")
    if mon.post_contacts_after:
        fails.append("touched_post")
    if mon.ridge_contacts_after:
        fails.append("touched_ridge")
    if chan:
        if mon.parked_force_peak > 1.0:        # nose-to-tail contact is by design; pushing the neighbour is not
            fails.append("pushed_parked")
    elif mon.parked_contacts_after:
        fails.append("touched_parked")
    row = dict(kind="launch", seed=seed, pocket=f"P{pocket + 1}", neighbours_occupied=" ".join(f"P{k + 1}" for k in occ),
               anchor_lost=lost or "", launch_box_bool=box, passed_bool=not fails,
               fail_reason_label=fails[0] if fails else "", fail_all_label=" ".join(fails),
               time_to_free_s=round(t_free, 3) if t_free else None,
               thrust_at_free_n=round(thrust_at_free, 2) if thrust_at_free else None,
               time_free_to_clear_s=round(t_clear - t_free, 3) if (t_clear and t_free) else None,
               post_contacts_count=mon.post_contacts_after, post_contact_s=round(mon.post_contact_s, 3),
               post_force_peak_n=round(mon.post_force_peak, 2), ridge_contacts_count=mon.ridge_contacts_after,
               parked_contacts_count=mon.parked_contacts_after, box_well_contacts_count=mon.well_contacts_after,
               wall_contacts_count=mon.wall_contacts, parked_force_peak_n=round(mon.parked_force_peak, 2),
               peak_contact_force_n=round(mon.peak_force, 2), final_state_label=pil.state,
               wall_s=round(time.time() - wall0, 2))
    if verbose:
        print(pil.events)
        print(row)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["recovery", "launch"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--hover", default="moderate")
    ap.add_argument("--wind", type=float, default=6.0)
    ap.add_argument("--noise", type=float, default=1.0)
    ap.add_argument("--anchor-loss", default="none")
    ap.add_argument("--neighbours", type=int, default=0)
    ap.add_argument("--box", action="store_true")
    ap.add_argument("--sidestep", action="store_true")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    cfg = load_cfg()
    cell = dict(hover=a.hover, wind=a.wind, noise=a.noise, anchor_loss=a.anchor_loss, parked_neighbours=a.neighbours,
                launch_box=a.box)
    opts = dict(launch_sidestep=a.sidestep)
    f = run_recovery if a.kind == "recovery" else run_launch
    f(cfg, cell, a.seed, verbose=True, opts=opts)


if __name__ == "__main__":
    main()
