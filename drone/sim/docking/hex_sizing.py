"""Hexacopter sizing check (addendum section 1), by calculation.

    python3 drone/sim/docking/hex_sizing.py            # prints the tables, writes results/sizing/

1. Hover power and endurance, loaded and empty: momentum theory with a figure of merit,
   a cage blockage loss and motor/ESC efficiency.
2. Forward-flight power: momentum theory with the free-stream component through the disc.
3. One rotor failed: for both six-rotor spin patterns, the largest total thrust that can be
   held with zero roll, pitch and yaw moment (a linear programme), and whether small
   moments of either sign remain available at the loaded hover point (controllability).
4. Mission energy with three docking attempts.
5. Payload: three hexacopters, three drinks and the rail against the 5 kg payload.
6. Drag and thrust margin at the mothership's minimum safe cruise speed, including the
   propeller inflow loss at the tilt needed.
All inputs are estimates and live in vehicles.yaml (hexacopter block) or below.
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
from scipy.optimize import linprog

_SIM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SIM not in sys.path:
    sys.path.insert(0, _SIM)

G = 9.80665
RHO = 1.225


def hover_power_w(thrust_n, disc_area_m2, fm, eta_elec, cage_loss_frac):
    ideal = thrust_n ** 1.5 / math.sqrt(2 * RHO * disc_area_m2)
    return ideal / fm / eta_elec / (1 - cage_loss_frac)


def forward_power_w(thrust_n, disc_area_m2, v_axial_m_s, v_inplane_m_s, fm, eta_elec, cage_loss_frac):
    """Momentum theory: v_i = T / (2 rho A |V + v_i|) solved by iteration."""
    vi = math.sqrt(thrust_n / (2 * RHO * disc_area_m2))
    for _ in range(100):
        vt = math.hypot(v_inplane_m_s, v_axial_m_s + vi)
        vi_new = thrust_n / (2 * RHO * disc_area_m2 * max(vt, 1e-6))
        vi = 0.5 * vi + 0.5 * vi_new
    ideal = thrust_n * (vi + v_axial_m_s)
    return max(ideal, 0.0) / fm / eta_elec / (1 - cage_loss_frac)


def hex_layout(pattern: str, arm_m: float):
    """Rotor positions (FLU) at 60 deg spacing starting at the front-right, and spins."""
    ang = [math.radians(30 + 60 * i) for i in range(6)]
    pos = [(arm_m * math.cos(a), -arm_m * math.sin(a)) for a in ang]
    spins = [1 if c == "P" else -1 for c in pattern]
    return pos, spins


def rotor_out(pattern: str, failed: int, tmax_n: float, k_yaw_m: float, arm_m: float, hover_n: float):
    pos, spins = hex_layout(pattern, arm_m)
    idx = [i for i in range(6) if i != failed]
    A = np.array([[1.0] * 5,
                  [pos[i][1] for i in idx],                  # roll  Mx = y T
                  [-pos[i][0] for i in idx],                 # pitch My = -x T
                  [-spins[i] * k_yaw_m for i in idx]])       # yaw
    # max total thrust with zero moments
    res = linprog(-np.ones(5), A_eq=A[1:], b_eq=np.zeros(3), bounds=[(0, tmax_n)] * 5, method="highs")
    tmax_total = -res.fun if res.success else 0.0
    # controllability at the hover point: can every small moment +/-eps be made?
    eps = 0.02 * hover_n * arm_m
    ok = True
    worst = math.inf
    for ax in (1, 2, 3):
        for sgn in (1, -1):
            b = np.array([hover_n, 0.0, 0.0, 0.0])
            b[ax] = sgn * eps * (k_yaw_m / arm_m if ax == 3 else 1.0)
            r = linprog(np.zeros(5), A_eq=A, b_eq=b, bounds=[(0, tmax_n)] * 5, method="highs")
            ok &= bool(r.success)
    return tmax_total, ok


def main():
    import yaml
    cfg = yaml.safe_load(open(os.path.join(_SIM, "docking", "vehicles.yaml")))
    h = cfg["hexacopter"]
    qp = cfg["quadplane"]
    out = {}
    A = 6 * math.pi * (h["prop_diameter_m"] / 2) ** 2
    fm, eta, cage = h["figure_of_merit_nd"], h["motor_esc_efficiency_nd"], h["cage_loss_frac"]
    E_use = h["battery_wh"] * h["battery_usable_frac"]
    P_av = h["avionics_power_w"]
    rows = []
    for label, m in (("loaded", h["mass_loaded_kg"]), ("empty", h["mass_empty_kg"])):
        P = hover_power_w(m * G, A, fm, eta, cage) + P_av
        rows.append({"case_label": label, "mass_kg": m, "hover_power_w": P, "hover_endurance_s": E_use * 3600 / P,
                     "thrust_to_weight_nd": h["thrust_max_total_n"] / (m * G)})
    out["hover"] = rows

    # one rotor failed, both spin patterns, worst failed rotor
    ro = []
    for pattern in ("PNPNPN", "PPNNPN"):    # (rotor cant does not change the result: checked)
        worst_t, all_ok = math.inf, True
        for f in range(6):
            t, ok = rotor_out(pattern, f, h["thrust_max_total_n"] / 6, h["yaw_torque_per_thrust_m"], h["arm_length_m"],
                              h["mass_loaded_kg"] * G)
            worst_t = min(worst_t, t)
            all_ok &= ok
        ro.append({"pattern_label": pattern, "max_level_thrust_one_out_n": worst_t,
                   "loaded_weight_n": h["mass_loaded_kg"] * G, "margin_frac": worst_t / (h["mass_loaded_kg"] * G) - 1,
                   "full_attitude_and_yaw_control_bool": all_ok})
    out["rotor_out"] = ro

    # payload
    n = 3
    for per_dock in (qp["rail"]["mass_per_dock_kg"], h["rail_mass_per_dock_lean_kg"]):
        rail = qp["rail"]["mass_base_kg"] + per_dock * n
        tot = n * h["mass_loaded_kg"] + rail
        out.setdefault("payload", []).append({"hexes_count": n, "rail_per_dock_kg": per_dock, "rail_kg": rail,
                                              "payload_total_kg": tot, "limit_kg": qp["max_payload_kg"],
                                              "fits_bool": tot <= qp["max_payload_kg"] + 1e-9,
                                              "max_hex_loaded_mass_kg": (qp["max_payload_kg"] - rail) / n})

    # thrust margin at docking speeds (empty hexacopter, it docks after delivering)
    fw = []
    m = h["mass_empty_kg"]
    for cda in h["drag_area_cases_m2"]:
        for V in (16.0, 18.0, 18.5, 20.0, 22.0):
            # trim tilt with per-axis drag: frontal cda, plan view cda * plan_ratio
            th = math.atan2(0.5 * RHO * cda * V * V, m * G)
            for _ in range(50):
                cda_eff = cda * math.cos(th) ** 2 + cda * h["plan_drag_ratio_nd"] * math.sin(th) ** 2
                D = 0.5 * RHO * cda_eff * V * V
                th = math.atan2(D, m * G)
            T = math.hypot(D, m * G)
            v_ax = V * math.sin(th)
            infl = max(0.2, 1 - v_ax / h["prop_zero_thrust_axial_speed_m_s"])
            T_av = h["thrust_max_total_n"] * infl
            P = forward_power_w(T, A, v_ax, V * math.cos(th), fm, eta, cage) + P_av
            fw.append({"drag_area_frontal_m2": cda, "airspeed_m_s": V, "tilt_deg": math.degrees(th),
                       "thrust_required_n": T, "thrust_available_n": T_av, "thrust_margin_frac": T_av / T - 1,
                       "power_w": P, "feasible_bool": T_av >= 1.15 * T and math.degrees(th) <= h["max_tilt_deg"]})
    out["forward"] = fw

    # mission energy (one sortie): phases and durations are estimates, in vehicles.yaml
    ms = h["mission_energy"]
    Ph_l = rows[0]["hover_power_w"]
    Ph_e = rows[1]["hover_power_w"]
    Vd = ms["docking_airspeed_m_s"]
    cda = h["drag_area_m2"]
    fdock = [r for r in fw if r["drag_area_frontal_m2"] == cda and abs(r["airspeed_m_s"] - Vd) < 1e-6]
    P_dock = fdock[0]["power_w"] if fdock else float("nan")
    E = {
        "release_and_descent_j": ms["descent_s"] * 0.85 * Ph_l,
        "inbound_navigation_j": ms["inbound_s"] * 1.05 * Ph_l,
        "delivery_j": ms["deliver_landing_s"] * 0.05 * Ph_l + ms["deliver_hover_s"] * Ph_l,
        "outbound_navigation_j": ms["outbound_s"] * 1.05 * Ph_e,
        "climb_j": ms["climb_m"] * m * G / 0.4 + ms["climb_m"] / 2.0 * Ph_e,
        "wait_at_pickup_j": ms["wait_s"] * Ph_e,
        "docking_attempts_j": ms["attempts_count"] * ms["attempt_s"] * P_dock + (ms["attempts_count"] - 1) * ms["reposition_s"] * P_dock,
    }
    E["total_j"] = sum(E.values())
    E["battery_usable_j"] = E_use * 3600
    E["reserve_frac"] = 1 - E["total_j"] / E["battery_usable_j"]
    out["mission_energy"] = E
    os.makedirs(os.path.join(_SIM, "docking", "results", "sizing"), exist_ok=True)
    with open(os.path.join(_SIM, "docking", "results", "sizing", "hex_sizing.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
