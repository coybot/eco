"""Bee35 and dock sizing: hover power, flight time, mission energy, carry table on the
Pioneer 3200, cruise drag of the loaded dock, magnet hold margins, rotor clearance and
UWB geometry. Writes docking/results/dock/sizing.json and prints a summary.

    python -m docksim.sizing
"""
from __future__ import annotations

import itertools
import json
import math
import os

import numpy as np

from .config import G_M_S2, RHO_KG_M3, load_cfg

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docking", "results", "dock")


def disk_area_m2(b):
    return 4 * math.pi * (b["rotor_diameter_m"] / 2) ** 2


def power_w(b, mass_kg, speed_m_s=0.0):
    """Electrical power in level flight at speed_m_s (momentum theory + parasite drag)."""
    A = disk_area_m2(b)
    W = mass_kg * G_M_S2
    D = 0.5 * RHO_KG_M3 * b["drag_area_m2"] * speed_m_s ** 2
    T = math.hypot(W, D)
    vh = math.sqrt(T / (2 * RHO_KG_M3 * A))
    # Glauert induced velocity with the disk tilted by the drag angle
    alpha = math.atan2(D, W)
    vi = vh
    for _ in range(60):
        vi = vh ** 2 / math.sqrt((speed_m_s * math.cos(alpha)) ** 2 + (speed_m_s * math.sin(alpha) + vi) ** 2)
    P_ind = T * (vi + speed_m_s * math.sin(alpha))
    return P_ind / b["hover_efficiency_nd"]


def mission_energy(b, cfg, slant_m=80.0, speed_m_s=6.0, approach_s=35.0, attempts=3, attempt_s=14.0,
                   recovery_s=20.0, launch_s=8.0):
    m_e = b["mass_kg"] + b["bracket_mass_kg"]
    m_l = m_e + b["box"]["mass_kg"]
    legs = [
        ("launch and climb-away, loaded", m_l, 0.0, launch_s),
        ("transit out, loaded", m_l, speed_m_s, slant_m / speed_m_s),
        ("balcony approach and set-down, loaded", m_l, 0.0, approach_s),
        ("transit back, empty", m_e, speed_m_s, slant_m / speed_m_s),
        ("recovery, nominal", m_e, 0.0, recovery_s),
        (f"{attempts - 1} extra docking attempts (abort, climb, retry)", m_e, 0.0, (attempts - 1) * attempt_s),
    ]
    rows, tot = [], 0.0
    for name, m, v, t in legs:
        p = power_w(b, m, v)
        e = p * t
        tot += e
        rows.append(dict(leg=name, mass_kg=round(m, 3), speed_m_s=v, time_s=round(t, 1), power_w=round(p, 1),
                         energy_wh=round(e / 3600, 2)))
    usable = b["battery_wh"] * b["battery_usable_frac"]
    return dict(legs=rows, total_wh=round(tot / 3600, 2), usable_wh=round(usable, 2),
                reserve_frac=round(1 - tot / 3600 / usable, 3), slant_m=slant_m, speed_m_s=speed_m_s)


def max_slant_m(b, cfg, speed_m_s=6.0, reserve_frac=0.2):
    lo, hi = 0.0, 2000.0
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        r = mission_energy(b, cfg, slant_m=mid, speed_m_s=speed_m_s)
        lo, hi = (mid, hi) if r["reserve_frac"] > reserve_frac else (lo, mid)
    return lo


def carry_table(cfg):
    b, d = cfg["bee35"], cfg["dock"]
    qp = cfg["quadplane"]
    rows = [("dock, posts, fairing, beacons (E 0.6-0.8)", d["mass_kg"]),
            ("4 x Bee35 incl. bracket", 4 * (b["mass_kg"] + b["bracket_mass_kg"])),
            ("4 x coffee box 0.290", 4 * b["box"]["mass_kg"])]
    tot = sum(m for _, m in rows)
    tot_hi = tot - d["mass_kg"] + 0.8
    return dict(rows=[dict(item=n, mass_kg=round(m, 3)) for n, m in rows], total_kg=round(tot, 3),
                total_with_dock_0p8_kg=round(tot_hi, 3), max_payload_kg=qp["max_payload_kg"],
                margin_kg=round(qp["max_payload_kg"] - tot, 3), margin_with_dock_0p8_kg=round(qp["max_payload_kg"] - tot_hi, 3))


def cruise_drag(cfg, airspeed_m_s=None):
    """Extra drag of the loaded dock in cruise vs the clean airframe (parasite only)."""
    qp = cfg["quadplane"]
    b = cfg["bee35"]
    V = airspeed_m_s or qp["cruise_airspeed_m_s"]
    q = 0.5 * RHO_KG_M3 * V ** 2
    a = qp["aero"]
    S = a["wing_area_m2"]
    m = qp["mtow_kg"]
    CL = m * G_M_S2 / (q * S)
    AR = a["span_m"] ** 2 / S
    CDi = CL ** 2 / (math.pi * AR * a["oswald_nd"])
    D_clean = q * S * (a["cd0_nd"] + CDi)
    cases = {}
    for name, cda in [("drones fully exposed (4 x 0.03) + dock 0.010", 4 * b["drag_area_m2"] + 0.010),
                      ("rear three half-shielded (0.03 + 3 x 0.015) + dock 0.010", 0.03 + 3 * 0.015 + 0.010),
                      ("fully faired enclosure, 0.025", 0.025)]:
        D = q * cda
        cases[name] = dict(extra_cda_m2=round(cda, 3), extra_drag_n=round(D, 1),
                           drag_increase_frac=round(D / D_clean, 2))
    return dict(airspeed_m_s=V, clean_drag_n=round(D_clean, 1), cases=cases)


def hold_margins(cfg):
    b, d = cfg["bee35"], cfg["dock"]
    Fm = d["magnet"]["force_per_pad_n"] * d["magnet"]["pads_per_pocket"]
    out = {}
    for lbl, m in [("empty", b["mass_kg"] + b["bracket_mass_kg"]),
                   ("with box", b["mass_kg"] + b["bracket_mass_kg"] + b["box"]["mass_kg"])]:
        W = m * G_M_S2
        out[lbl] = dict(mass_kg=round(m, 3), weight_n=round(W, 2), magnet_n=Fm,
                        liftoff_load_factor_g=round(-Fm / W, 2),
                        release_thrust_n=round(W + Fm, 1),
                        release_thrust_frac_of_max=round((W + Fm) / b["thrust_max_total_n"], 2),
                        excess_accel_at_release_m_s2=round(Fm / m, 1))
    return out


def rotor_clearance(cfg):
    ma = cfg["mothership_airframe"]
    b, d, rec = cfg["bee35"], cfg["dock"], cfg["dock_ops"]["recovery"]
    rot = ma["rotors"]
    half = b["size_m"][0] / 2
    res = []
    for i, px in enumerate(d["pocket_x_m"]):
        best = 1e9
        for rp in rot["pos_m"]:
            # nearest horizontal distance from the rotor axis to the drone footprint during the descent column
            for zc in (rec["hold_z_m"], 0.0):
                dx = max(abs(rp[0] - px) - half, 0.0)
                dz = max(abs(rp[2] - zc) - half, 0.0)
                best = min(best, math.hypot(dx, dz))
        res.append(dict(pocket=f"P{i + 1}", footprint_to_rotor_axis_m=round(best, 3),
                        inside_disk_radius=bool(best < rot["radius_m"]),
                        inside_strike_radius=bool(best < rot["strike_radius_m"])))
    strike_top = rot["pos_m"][0][1] + rot["strike_half_height_m"]
    inflow_top = rot["pos_m"][0][1] + rot["inflow_height_m"]
    return dict(pockets=res, strike_zone_top_y_m=strike_top, drone_underside_min_y_m=0.0,
                vertical_clearance_to_strike_m=round(0.0 - strike_top, 3), inflow_top_y_m=inflow_top,
                approach_height_y_m=rec["approach_y_m"],
                note="The drone never goes below y = 0, so the strike zones (y <= -0.02) are out of reach unless "
                     "it tumbles; P1/P4 descend partly through the inflow of the starboard rotors.")


def uwb_dop(cfg):
    """Position DOP of the anchor sets at the hold and approach points, height taken from the down range."""
    ma, d, rec = cfg["mothership_airframe"], cfg["dock"], cfg["dock_ops"]["recovery"]
    tag = np.array(cfg["bee35"]["tag_pos_m"])
    out = {}
    for set_name, names in ma["anchor_sets"].items():
        for lost in [None] + list(names):
            use = [n for n in names if n != lost]
            A = np.array([ma["anchors_m"][n] for n in use])
            rows = []
            for px in d["pocket_x_m"]:
                for lbl, p in [("hold", [px, rec["hold_y_m"], rec["hold_z_m"]]),
                               ("approach", [px, rec["approach_y_m"], rec["hold_z_m"]]),
                               ("seated", [px, 0.0, 0.0])]:
                    r = np.array(p) + tag
                    H = (r - A) / np.linalg.norm(r - A, axis=1)[:, None]
                    Hy = np.vstack([H, [0, 1, 0]])          # down range: height, weight ~ (0.05/0.02)^2
                    W = np.diag([1.0] * len(use) + [6.0])
                    try:
                        Pcov = np.linalg.inv(Hy.T @ W @ Hy)
                        hdop = float(math.sqrt(Pcov[0, 0] + Pcov[2, 2]))
                        xdop, zdop = float(math.sqrt(Pcov[0, 0])), float(math.sqrt(Pcov[2, 2]))
                    except np.linalg.LinAlgError:
                        hdop = xdop = zdop = float("inf")
                    rows.append(dict(px=px, point=lbl, x_dop=round(xdop, 2), z_dop=round(zdop, 2),
                                     h_dop=round(hdop, 2)))
            key = f"{set_name}" + (f"-minus-{lost}" if lost else "")
            out[key] = dict(anchors=use, worst_h_dop=max(r["h_dop"] for r in rows), rows=rows)
    return out


def main():
    cfg = load_cfg()
    b = cfg["bee35"]
    m_e = b["mass_kg"] + b["bracket_mass_kg"]
    m_l = m_e + b["box"]["mass_kg"]
    usable_j = b["battery_wh"] * b["battery_usable_frac"] * 3600
    hover = {lbl: dict(mass_kg=round(m, 3), thrust_frac_of_max=round(m * G_M_S2 / b["thrust_max_total_n"], 3),
                       hover_power_w=round(power_w(b, m), 1),
                       hover_time_min=round(usable_j / power_w(b, m) / 60, 1),
                       power_at_6_m_s_w=round(power_w(b, m, 6.0), 1))
             for lbl, m in [("empty", m_e), ("loaded", m_l)]}
    res = dict(
        hover=hover,
        mission_80m=mission_energy(b, cfg),
        max_slant_m_at_20pct_reserve=round(max_slant_m(b, cfg), 0),
        carry=carry_table(cfg),
        cruise_drag=cruise_drag(cfg),
        hold=hold_margins(cfg),
        rotor_clearance=rotor_clearance(cfg),
        uwb_dop=uwb_dop(cfg),
    )
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "sizing.json"), "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "uwb_dop"}, indent=1))
    print("UWB worst horizontal DOP (x sigma_range):")
    for k, v in res["uwb_dop"].items():
        print(f"  {k:24s} {v['worst_h_dop']}")


if __name__ == "__main__":
    main()
