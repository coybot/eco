"""Mothership performance: stall speed, minimum safe cruise speed, carrying capacity.

Analytic (from wing loading):
  stall speed           V_s = sqrt(2 m g / (rho S CLmax))
  classic minimum       V_min,1.3 = 1.3 V_s  (the usual 30 % speed margin, i.e. cruise at
                        CL <= CLmax / 1.69)
  gust-margin minimum   lowest V with  CL_cruise(V) + a * k * sigma_w / V <= CLmax
                        (a k-sigma vertical gust raises alpha by k sigma_w / V; sharp-edged
                        gust, no alleviation, so conservative)
  minimum safe cruise   max of the two for the design turbulence.

Simulated: the 6-DOF mothership, autopilot holding airspeed and altitude, flown straight
through seeded von Karman turbulence; a speed is safe if in every seed the peak angle of
attack stays ``alpha_margin_deg`` below the CLmax angle. That number is what the scenario
uses; the analytic ones bracket it.
"""
from __future__ import annotations

import math

import numpy as np

from flight.aero import FixedWingAero
from flight.frames import GRAVITY_M_S2


def stall_speed_m_s(mass_kg: float, wing_area_m2: float, cl_max_nd: float, rho_kg_m3: float) -> float:
    return math.sqrt(2 * mass_kg * GRAVITY_M_S2 / (rho_kg_m3 * wing_area_m2 * cl_max_nd))


def gust_margin_speed_m_s(mass_kg, wing_area_m2, cl_max_nd, cl_alpha_per_rad, sigma_w_m_s, k_sigma_nd,
                          rho_kg_m3) -> float:
    W = mass_kg * GRAVITY_M_S2
    for V in np.arange(5.0, 60.0, 0.01):
        cl = W / (0.5 * rho_kg_m3 * V * V * wing_area_m2)
        if cl + cl_alpha_per_rad * k_sigma_nd * sigma_w_m_s / V <= cl_max_nd:
            return float(V)
    return float("nan")


def carry_table(cfg: dict) -> list:
    """Rows for 0..4 quads on the rail: masses, payload check, speeds at each stage."""
    qp = cfg["quadplane"]
    q = cfg["small_quadcopter"]
    env = cfg["environment"]
    aero = FixedWingAero(qp["aero"])
    clmax, _ = aero.cl_max()
    S, rho = qp["aero"]["wing_area_m2"], env["air_density_kg_m3"]
    sigma_w = qp["design_turbulence_sigma_w_m_s"]
    k = qp["design_gust_k_sigma_nd"]
    rows = []
    for n in range(0, 5):
        rail = qp["rail"]["mass_base_kg"] + qp["rail"]["mass_per_dock_kg"] * n if n else 0.0
        payload = n * q["mass_loaded_kg"] + rail
        m_full = qp["mass_airframe_kg"] + payload
        row = {"quads_count": n, "rail_mass_kg": rail, "payload_mass_kg": payload,
               "payload_limit_kg": qp["max_payload_kg"], "takeoff_mass_kg": m_full,
               "mtow_kg": qp["mtow_kg"],
               "within_limits_bool": payload <= qp["max_payload_kg"] + 1e-9 and m_full <= qp["mtow_kg"] + 1e-9}
        stages = [("full", m_full)]
        m = m_full
        for i in range(n):
            m -= q["mass_loaded_kg"]
            stages.append((f"after_release_{i + 1}", m))
        for name, mm in stages:
            vs = stall_speed_m_s(mm, S, clmax, rho)
            vg = gust_margin_speed_m_s(mm, S, clmax, qp["aero"]["cl_alpha_per_rad"], sigma_w, k, rho)
            row[f"{name}_mass_kg"] = mm
            row[f"{name}_stall_speed_m_s"] = vs
            row[f"{name}_min_cruise_1p3_vs_m_s"] = 1.3 * vs
            row[f"{name}_min_cruise_gust_m_s"] = vg
            row[f"{name}_min_safe_cruise_m_s"] = max(1.3 * vs, vg)
        rows.append(row)
    return rows


# ------------------------------------------------------------------ simulated checks
def _ms_straight(cfg, seed, airspeed_m_s, n_quads, wind20_m_s, T_s=60.0, clmax_case="baseline"):
    """Fly the mothership straight and level for T_s in turbulence; return peak alpha etc."""
    import copy
    from flight.wind import TurbulenceField, WindField, mil_hdbk_1797_low_altitude
    from .quadplane import Mothership
    c = copy.deepcopy(cfg)
    qp = c["quadplane"]
    if clmax_case == "conservative":
        qp["aero"]["alpha_stall_deg"] = 15.0
    env = c["environment"]
    rng = np.random.default_rng(seed)
    alt = 50.0
    ms = Mothership(qp, env, rng, None, np.array([0.0, 0.0, alt]), 0.0, airspeed_m_s,
                    [i < n_quads for i in range(2)] if n_quads else [False, False],
                    quad_mass_kg=c["small_quadcopter"]["mass_loaded_kg"], quad_cda_m2=c["small_quadcopter"]["drag_area_m2"])
    ms.course_override = (np.array([0.0, 0.0]), 0.0)
    ms.stack.ap.airspeed_cmd = airspeed_m_s
    ms.stack.ap.alt_cmd = alt
    wind = WindField([0, 0, 0], TurbulenceField(seed=int(rng.integers(1 << 30)),
                                                **mil_hdbk_1797_low_altitude(alt, wind20_m_s)) if wind20_m_s > 0 else None)
    aero = ms.stack.phys.aero
    clmax, a_clmax = aero.cl_max()
    dt = env["physics_dt_s"]
    t = 0.0
    amax, vmin, hdev, sat = -9.0, 99.0, 0.0, 0
    w = np.zeros(3)
    for i in range(int(T_s / dt)):
        if i % 4 == 0:
            w = wind.ambient(ms.body.pos, t)
        ms.step(t, w)
        t += dt
        if t > 5.0:
            la = ms.stack.phys.last
            amax = max(amax, la["alpha_rad"])
            vmin = min(vmin, la["airspeed_m_s"])
            hdev = max(hdev, abs(ms.body.pos[2] - alt))
            sat += ms.stack.ap.cmd[3] >= 0.999
    return {"alpha_max_deg": math.degrees(amax), "alpha_clmax_deg": math.degrees(a_clmax),
            "airspeed_min_m_s": vmin, "alt_dev_max_m": hdev, "throttle_sat_frac": sat * dt / (T_s - 5.0),
            "mass_kg": ms.mass_kg()}


def sim_min_safe_trial(seed, airspeed_m_s=20.0, n_quads=2, wind20_m_s=7.7, clmax_case="baseline", T_s=60.0):
    from .delivery_run import load_cfg
    cfg = load_cfg()
    r = _ms_straight(cfg, seed, airspeed_m_s, n_quads, wind20_m_s, T_s, clmax_case)
    r.update({"airspeed_cmd_m_s": airspeed_m_s, "quads_count": n_quads, "wind20_m_s": wind20_m_s,
              "clmax_case_label": clmax_case, "row_id": int(round(airspeed_m_s * 100)) * 10 + n_quads})
    return r


def sim_stall_speed(cfg, n_quads=2, clmax_case="baseline", seed=3):
    """Calm air, decelerate 0.25 m/s per s holding altitude; airspeed when alpha reaches
    the CLmax angle, vs the analytic stall speed for the same mass."""
    import copy
    from .quadplane import Mothership
    c = copy.deepcopy(cfg)
    qp = c["quadplane"]
    if clmax_case == "conservative":
        qp["aero"]["alpha_stall_deg"] = 15.0
    qp["autopilot"]["max_pitch_deg"] = 30.0
    qp["autopilot"] = {k: v for k, v in qp["autopilot"].items()
                       if k not in ("alpha_protect_deg", "underspeed_margin_m_s")}   # a physics check: protection off
    env = c["environment"]
    ms = Mothership(qp, env, np.random.default_rng(seed), None, np.array([0.0, 0.0, 300.0]), 0.0, 22.0,
                    [i < n_quads for i in range(2)], quad_mass_kg=c["small_quadcopter"]["mass_loaded_kg"],
                    quad_cda_m2=c["small_quadcopter"]["drag_area_m2"])
    ms.course_override = (np.array([0.0, 0.0]), 0.0)
    ms.stack.ap.alt_cmd = 300.0
    aero = ms.stack.phys.aero
    clmax, a_clmax = aero.cl_max()
    dt = env["physics_dt_s"]
    t = 0.0
    while t < 80.0:
        ms.stack.ap.airspeed_cmd = ms.v_straight = max(8.0, 22.0 - 0.25 * max(0.0, t - 5.0))
        ms.step(t, np.zeros(3))
        t += dt
        la = ms.stack.phys.last
        if la["alpha_rad"] >= a_clmax:
            v = la["airspeed_m_s"]
            break
    else:
        v = float("nan")
    vth = stall_speed_m_s(ms.mass_kg(), aero.S, clmax, c["environment"]["air_density_kg_m3"])
    return {"stall_speed_sim_m_s": v, "stall_speed_analytic_m_s": vth, "mass_kg": ms.mass_kg(),
            "clmax_nd": clmax, "elevator_at_stall_deg": math.degrees(ms.stack.phys.last["elevator_rad"])}
