"""Phase 1 exit checks for the flight package.

    python3 drone/sim/flight/checks_phase1.py [--out DIR] [--workers N]

Each check simulates something with a known answer and compares. Measurements in these
checks read true state (that is what a check is for); the controllers being exercised
still read only sensors. Writes checks.csv and a JSON summary; exits 1 if any fails.

Exit criteria from the task:
  E1 hover thrust equals weight
  E2 trim tilt matches atan(drag / weight) within 1 deg
  E3 a quadcopter that levels out at speed decelerates at drag / mass
  E4 free fall conserves energy
  E5 batch runs reproduce from their seeds
Supporting checks (not exit criteria, but they back the features claimed):
  S1 turbulence: variance and spectrum vs von Karman
  S2 motor lag time constant and saturation
  S3 sensor delay, rate, noise and dropout statistics
  S4 wing: CLmax/stall angle, lift = weight in trimmed level flight
  (The simulated 1-g stall-speed check, check_stall_speed, runs in Phase 2 on the quadplane:
   the reference Aerosonde coefficients are elevator-limited near alpha 9 deg and never
   reach their stall angle, so it cannot be checked on them.)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys

import numpy as np
import yaml

_SIM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SIM not in sys.path:
    sys.path.insert(0, _SIM)

from flight.actuators import MotorBank                      # noqa: E402
from flight.aero import FixedWingAero                       # noqa: E402
from flight.batch import run_batch, write_csv               # noqa: E402
from flight.fixedwing import FixedWingStack, line_course    # noqa: E402
from flight.frames import GRAVITY_M_S2, quat_from_euler, quat_to_rot, rot_to_quat, tilt_rad  # noqa: E402
from flight.multirotor import Multirotor, QuadStack, quad_params  # noqa: E402
from flight.randomize import sample                         # noqa: E402
from flight.sensors import SensorChannel                    # noqa: E402
from flight.wind import (TurbulenceField, WindField, mil_hdbk_1797_low_altitude,  # noqa: E402
                         von_karman_longitudinal_psd, von_karman_transverse_psd)

CFG_PATH = os.path.join(_SIM, "docking", "vehicles.yaml")


def load_cfg():
    with open(CFG_PATH) as f:
        return yaml.safe_load(f)


# ------------------------------------------------------------------ quad helpers
def _speed_hold_outer(v_target_m_s, alt_m, p, rho, use_ff=True):
    """Velocity hold along x at an altitude, with a drag feed-forward from the
    controller's NOMINAL drag area (it does not know the true one)."""
    state = {"i": np.zeros(3)}
    cda = p["drag_area_m2"]

    def outer(s, t):
        v_sp = np.array([v_target_m_s, -0.5 * s.nav.pos[1], 1.0 * (alt_m - s.nav.pos[2])])
        e = v_sp - s.nav.vel
        state["i"] = np.clip(state["i"] + e * 0.01, -10, 10)
        a = 3.0 * e + 1.0 * state["i"]
        if use_ff:
            v = s.nav.vel
            a = a + 0.5 * rho * cda * np.linalg.norm(v) * v / s.fc.mass_est_kg
        s.fc.set_accel(t, a, 0.0)
    return outer


def _run(stack, T_s, wind=None, every=None):
    """Advance a stack T_s seconds; time continues across calls (sensors are scheduled
    on absolute time, so restarting the clock would freeze them)."""
    dt = stack.dt
    t = getattr(stack, "_t_s", 0.0)
    out = []
    for i in range(int(round(T_s / dt))):
        w = np.zeros(3) if wind is None else wind.velocity(stack.body.pos, t)
        stack.step(t, w)
        t += dt
        if every:
            out.append(every(stack, t))
    stack._t_s = t
    return out


def check_hover(cfg):
    p = quad_params(cfg["small_quadcopter"])
    env = cfg["environment"]
    res = []
    for mass in (p["mass_empty_kg"], p["mass_loaded_kg"]):
        st = QuadStack(p, env, np.random.default_rng(11), [0, 0, 20], [0, 0, 0], [1, 0, 0, 0], mass)
        st.outer = _speed_hold_outer(0.0, 20.0, p, env["air_density_kg_m3"])
        log = _run(st, 30.0, every=lambda s, t: s.phys.last_thrust_total_n)
        T = float(np.mean(log[-int(10 / st.dt):]))
        W = mass * GRAVITY_M_S2
        err = (T - W) / W
        res.append(dict(check_label="E1_hover_thrust_equals_weight", case_label=f"mass_{mass:.1f}_kg",
                        measured_nd=T, expected_nd=W, error_frac=err, tolerance_frac=0.01,
                        pass_bool=abs(err) < 0.01, note_label="mean total thrust N over last 10 s vs m g"))
    return res


def check_trim_tilt(cfg):
    p = quad_params(cfg["small_quadcopter"])
    env = cfg["environment"]
    rho = env["air_density_kg_m3"]
    res = []
    for V in (5.0, 10.0, 15.0, 20.0):
        st = QuadStack(p, env, np.random.default_rng(12), [0, 0, 30], [V, 0, 0],
                       quat_from_euler(0, -math.atan2(0.5 * rho * 0.05 * V * V, GRAVITY_M_S2), 0), 1.0)
        st.outer = _speed_hold_outer(V, 30.0, p, rho)
        log = _run(st, 25.0, every=lambda s, t: (math.degrees(tilt_rad(s.body.R)), float(np.linalg.norm(s.body.vel))))
        L = np.array(log[-int(8 / st.dt):])
        tilt, spd = L[:, 0].mean(), L[:, 1].mean()
        D = 0.5 * rho * p["drag_area_m2"] * spd * spd
        exp = math.degrees(math.atan2(D, 1.0 * GRAVITY_M_S2))
        res.append(dict(check_label="E2_trim_tilt_atan_drag_over_weight", case_label=f"speed_{V:.0f}_m_s",
                        measured_nd=tilt, expected_nd=exp, error_frac=tilt - exp, tolerance_frac=1.0,
                        pass_bool=abs(tilt - exp) < 1.0,
                        note_label=f"tilt deg at measured speed {spd:.2f} m/s (error column in deg)"))
    return res


def check_level_out_decel(cfg):
    """Fly at V0, then command level attitude with collective = weight; once level,
    compare the measured deceleration with D(v)/m at every instant, and the speed
    history with the closed form v(t) = v1 / (1 + k v1 t), k = rho CdA / (2 m)."""
    p = quad_params(cfg["small_quadcopter"])
    # Ideal AHRS for this check: a 0.5 deg attitude bias tilts the thrust (= weight) by
    # 0.5 deg, a horizontal force of 0.9 % of weight, which is 2 % of the drag at 12 m/s.
    # The criterion is about the physics, so the attitude sensor is made perfect here.
    p["sensors"] = dict(p["sensors"])
    p["sensors"]["ahrs"] = dict(p["sensors"]["ahrs"], noise_std_deg=0.0, bias_std_deg=0.0, bias_rw_deg_per_sqrt_s=0.0)
    env = cfg["environment"]
    rho = env["air_density_kg_m3"]
    res = []
    for V0 in (12.0, 18.0):
        m = 1.0
        st = QuadStack(p, env, np.random.default_rng(13), [0, 0, 40], [V0, 0, 0],
                       quat_from_euler(0, -math.atan2(0.5 * rho * 0.05 * V0 * V0, GRAVITY_M_S2), 0), m)
        st.outer = _speed_hold_outer(V0, 40.0, p, rho)
        _run(st, 15.0)
        st.outer = lambda s, t: s.fc.set_attitude(np.array([1.0, 0, 0, 0]), m * GRAVITY_M_S2)
        log = _run(st, 4.0, every=lambda s, t: (t, tilt_rad(s.body.R), s.body.vel[0], s.body.accel_world[0],
                                                 s.phys.last_thrust_total_n))
        L = np.array(log)
        level = np.where(L[:, 1] < math.radians(0.05))[0]
        i0 = int(level[0]) + int(0.5 / st.dt)     # 0.5 s after reaching level, attitude settled
        seg = L[i0:i0 + int(2.0 / st.dt)]
        v = seg[:, 2]
        a_meas = -seg[:, 3]
        a_exp = 0.5 * rho * p["drag_area_m2"] * v * v / m
        # least-squares ratio of measured deceleration to D/m over the window
        ratio_err = float(abs((a_meas @ a_exp) / (a_exp @ a_exp) - 1.0))
        k = rho * p["drag_area_m2"] / (2 * m)
        tt = seg[:, 0] - seg[0, 0]
        v_cf = v[0] / (1 + k * v[0] * tt)
        cf_err = float(np.max(np.abs(v - v_cf)))
        res.append(dict(check_label="E3_level_out_decel_equals_drag_over_mass", case_label=f"speed_{V0:.0f}_m_s",
                        measured_nd=float(a_meas[0]), expected_nd=float(a_exp[0]), error_frac=ratio_err,
                        tolerance_frac=0.02, pass_bool=ratio_err < 0.02,
                        note_label=f"|fit(a vs D/m)-1| over 2 s once level; speed vs closed form max err {cf_err:.4f} m/s"))
        res.append(dict(check_label="E3b_level_out_speed_closed_form", case_label=f"speed_{V0:.0f}_m_s",
                        measured_nd=float(v[-1]), expected_nd=float(v_cf[-1]), error_frac=cf_err / v[0],
                        tolerance_frac=0.01, pass_bool=cf_err / v[0] < 0.01,
                        note_label="v(t)=v1/(1+k v1 t), worst error / v1"))
    return res


def check_free_fall(cfg):
    p = quad_params(cfg["small_quadcopter"])
    env = cfg["environment"]
    res = []
    for drag_on in (False, True):
        pp = dict(p)
        if not drag_on:
            pp["drag_area_m2"] = 0.0
        q0 = quat_from_euler(0.2, -0.3, 0.5)
        mr = Multirotor(pp, 1.0, [0, 0, 500], [6.0, -2.0, 3.0], q0, env["air_density_kg_m3"])
        mr.body.omega = np.array([0.5, -0.3, 1.0])
        mr.motors.thrust_n[:] = 0.0
        E0 = mr.body.kinetic_energy_j() + mr.body.potential_energy_j()
        work = 0.0
        dt = env["physics_dt_s"]
        for _ in range(int(8.0 / dt)):
            v_before = mr.body.vel.copy()
            mr.step(np.zeros(4), np.zeros(3), dt)
            work += float(mr.last_drag_world_n @ (0.5 * (v_before + mr.body.vel))) * dt
        E1 = mr.body.kinetic_energy_j() + mr.body.potential_energy_j()
        if not drag_on:
            err = abs(E1 - E0) / E0
            res.append(dict(check_label="E4_free_fall_conserves_energy", case_label="no_drag",
                            measured_nd=E1, expected_nd=E0, error_frac=err, tolerance_frac=1e-9,
                            pass_bool=err < 1e-9, note_label="total energy J after 8 s of free fall with spin"))
        else:
            loss = E0 - E1
            err = abs(loss + work) / abs(work)
            res.append(dict(check_label="E4b_free_fall_energy_budget_with_drag", case_label="drag_on",
                            measured_nd=loss, expected_nd=-work, error_frac=err, tolerance_frac=0.005,
                            pass_bool=err < 0.005, note_label="energy lost J vs integral of drag power"))
    return res


# ------------------------------------------------------------------ reproducibility
def repro_trial(seed: int, cfg_path: str = CFG_PATH, T_s: float = 6.0) -> dict:
    """A small Monte Carlo trial: hover-hold in randomised turbulence, randomised quad."""
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    rng = np.random.default_rng(seed)
    spec = {"mass_kg": {"dist": "uniform", "low": 0.95, "high": 1.45},
            "cda_scale_nd": {"dist": "normal", "mean": 1.0, "std": 0.15, "low": 0.6, "high": 1.4},
            "wind20_m_s": {"dist": "uniform", "low": 0.0, "high": 15.0},
            "mean_wind_m_s": {"dist": "uniform", "low": 0.0, "high": 6.0},
            "wind_dir_deg": {"dist": "uniform", "low": 0.0, "high": 360.0}}
    s = sample(spec, rng)
    p = quad_params(cfg["small_quadcopter"])
    env = cfg["environment"]
    tp = mil_hdbk_1797_low_altitude(40.0, s["wind20_m_s"])
    d = math.radians(s["wind_dir_deg"])
    wind = WindField([s["mean_wind_m_s"] * math.cos(d), s["mean_wind_m_s"] * math.sin(d), 0],
                     TurbulenceField(seed=int(rng.integers(1 << 31)), **tp))
    st = QuadStack(p, env, rng, [0, 0, 40], [0, 0, 0], [1, 0, 0, 0], s["mass_kg"], cda_scale=s["cda_scale_nd"])
    st.outer = _speed_hold_outer(0.0, 40.0, p, env["air_density_kg_m3"], use_ff=False)
    log = _run(st, T_s, wind=wind, every=lambda st_, t: np.linalg.norm(st_.body.pos - np.array([0, 0, 40])))
    out = {"mass_kg": s["mass_kg"], "cda_scale_nd": s["cda_scale_nd"], "wind20_m_s": s["wind20_m_s"],
           "pos_err_max_m": float(np.max(log)), "pos_err_final_m": float(log[-1]),
           "final_x_m": float(st.body.pos[0]), "final_z_m": float(st.body.pos[2])}
    return out


def check_reproducibility(out_dir: str, workers: int):
    seeds = list(range(100, 116))
    paths = []
    for tag, w in (("a_w1", 1), ("b_wN", workers), ("c_wN", workers)):
        pth = os.path.join(out_dir, f"repro_{tag}.csv")
        run_batch(repro_trial, seeds, workers=w, out_csv=pth)
        paths.append(pth)
    h = [hashlib.sha256(open(x, "rb").read()).hexdigest() for x in paths]
    other = os.path.join(out_dir, "repro_d_other_seeds.csv")
    run_batch(repro_trial, list(range(200, 216)), workers=workers, out_csv=other)
    h_other = hashlib.sha256(open(other, "rb").read()).hexdigest()
    same = len(set(h)) == 1
    return [dict(check_label="E5_batch_reproduces_from_seeds", case_label=f"16_seeds_1_vs_{workers}_workers_x2",
                 measured_nd=float(len(set(h))), expected_nd=1.0, error_frac=0.0 if same else 1.0,
                 tolerance_frac=0.0, pass_bool=same and h_other != h[0],
                 note_label=f"sha256 {h[0][:12]} identical across 3 runs; other seeds differ: {h_other != h[0]}")]


# ------------------------------------------------------------------ supporting checks
def _welch(x, fs, nseg):
    n = len(x) // nseg
    win = np.hanning(n)
    scale = 1.0 / (fs * (win * win).sum())
    P = np.zeros(n // 2 + 1)
    for k in range(nseg * 2 - 1):                      # 50 % overlap
        seg = x[k * n // 2: k * n // 2 + n]
        if len(seg) < n:
            break
        X = np.fft.rfft((seg - seg.mean()) * win)
        P += (np.abs(X) ** 2) * scale
    P /= (nseg * 2 - 1)
    P[1:-1] *= 2
    return np.fft.rfftfreq(n, 1 / fs), P


def check_turbulence(out_dir):
    tp = mil_hdbk_1797_low_altitude(50.0, 15.0 * 0.514444 * 2)    # moderate, 30 kt
    res = []
    rows = []
    V = 20.0
    fs = 20.0
    for seed in (1, 2, 3, 4):
        tf = TurbulenceField(seed=seed, **tp)
        N = int(4000 * fs)
        xs = np.zeros((N, 3))
        for i in range(N):
            xs[i] = tf.velocity(np.array([V * i / fs, 0.0, 50.0]))
        f, Pu = _welch(xs[:, 0], fs, 16)
        _, Pw = _welch(xs[:, 2], fs, 16)
        rows.append((f, Pu, Pw, xs.std(0)))
    f = rows[0][0]
    Pu = np.mean([r[1] for r in rows], axis=0)
    Pw = np.mean([r[2] for r in rows], axis=0)
    std = np.mean([r[3] for r in rows], axis=0)
    om = 2 * math.pi * f / V                                  # rad/m
    # temporal one-sided PSD from spatial: Phi_t(f) = Phi_s(om) * 2 pi / V
    Pu_th = von_karman_longitudinal_psd(om, tp["sigma_u_m_s"], tp["length_horizontal_m"]) * 2 * math.pi / V
    Pw_th = von_karman_transverse_psd(om, tp["sigma_w_m_s"], tp["length_vertical_m"]) * 2 * math.pi / V
    # third-octave band averages (the usual way to compare a measured spectrum)
    edges = 0.02 * 2 ** (np.arange(0, 28) / 3.0)
    edges = edges[edges <= 4.0]
    bu, bw = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (f >= lo) & (f < hi)
        bu.append(Pu[sel].mean() / Pu_th[sel].mean())
        bw.append(Pw[sel].mean() / Pw_th[sel].mean())
    bu, bw = np.array(bu), np.array(bw)
    eu = float(np.exp(np.mean(np.abs(np.log(bu)))))
    ew = float(np.exp(np.mean(np.abs(np.log(bw)))))
    worst = (float(np.exp(np.max(np.abs(np.log(bu))))), float(np.exp(np.max(np.abs(np.log(bw))))))
    for comp, s_meas, s_exp in (("u", std[0], tp["sigma_u_m_s"]), ("w", std[2], tp["sigma_w_m_s"])):
        e = s_meas / s_exp - 1
        res.append(dict(check_label="S1_turbulence_sigma", case_label=f"{comp}_moderate_50m",
                        measured_nd=float(s_meas), expected_nd=float(s_exp), error_frac=float(e), tolerance_frac=0.15,
                        pass_bool=abs(e) < 0.15, note_label="std m/s along a 80 km straight path at 20 m/s, 4 seeds"))
    for comp, e, wst in (("u", eu, worst[0]), ("w", ew, worst[1])):
        res.append(dict(check_label="S1_turbulence_psd_vs_von_karman", case_label=f"{comp}_0p02_to_4_hz",
                        measured_nd=float(e), expected_nd=1.0, error_frac=float(e - 1), tolerance_frac=0.25,
                        pass_bool=e - 1 < 0.25,
                        note_label=f"geometric-mean ratio over third-octave bands (worst band x{wst:.2f}), 4 seeds"))
    np.savetxt(os.path.join(out_dir, "turbulence_psd.csv"),
               np.column_stack([f, Pu, Pu_th, Pw, Pw_th]), delimiter=",",
               header="freq_hz,psd_u_m2_s,psd_u_theory_m2_s,psd_w_m2_s,psd_w_theory_m2_s", comments="")
    return res


def check_motor():
    mb = MotorBank(1, 10.0, 0.03, 0.05)
    dt = 0.0025
    t, T = 0.0, []
    for _ in range(int(0.5 / dt)):
        T.append(mb.step(np.array([5.0]), dt)[0])
        t += dt
    T = np.array(T)
    i63 = int(np.argmax(T >= 5.0 * (1 - math.exp(-1))))
    tau_up = (i63 + 1) * dt
    for _ in range(int(0.5 / dt)):
        Ts = mb.step(np.array([50.0]), dt)[0]
    T2 = []
    for _ in range(int(0.5 / dt)):
        T2.append(mb.step(np.array([0.0]), dt)[0])
    T2 = np.array(T2)
    i37 = int(np.argmax(T2 <= 10.0 * math.exp(-1)))
    tau_dn = (i37 + 1) * dt
    return [dict(check_label="S2_motor_lag_tau_up", case_label="step_0_to_5_n", measured_nd=tau_up, expected_nd=0.03,
                 error_frac=tau_up / 0.03 - 1, tolerance_frac=0.1, pass_bool=abs(tau_up / 0.03 - 1) < 0.1,
                 note_label="time to 63 % s"),
            dict(check_label="S2_motor_lag_tau_down", case_label="step_10_to_0_n", measured_nd=tau_dn, expected_nd=0.05,
                 error_frac=tau_dn / 0.05 - 1, tolerance_frac=0.1, pass_bool=abs(tau_dn / 0.05 - 1) < 0.1,
                 note_label="time to 37 % s"),
            dict(check_label="S2_motor_saturation", case_label="command_50_n_max_10_n", measured_nd=float(Ts),
                 expected_nd=10.0, error_frac=float(Ts) / 10 - 1, tolerance_frac=1e-6,
                 pass_bool=float(Ts) <= 10.0 + 1e-9, note_label="thrust N never exceeds max")]


def check_sensor():
    rng = np.random.default_rng(5)
    ch = SensorChannel(3, 50.0, 0.08, 0.1, rng, dropout_p=0.1, jitter_s=0.0)
    dt = 0.0025
    t = 0.0
    got = []
    for i in range(int(200 / dt)):
        ch.push(t, np.array([math.sin(t), 0.0, 0.0]))
        for m in ch.read_new(t):
            got.append((m.t_meas_s, m.t_avail_s, m.value[0] - math.sin(m.t_meas_s), t))
        t += dt
    G = np.array(got)
    delay = float(np.mean(G[:, 3] - G[:, 0]))
    rate = ch.n_taken / 200.0
    drop = ch.n_dropped / ch.n_taken
    nstd = float(np.std(G[:, 2]))
    res = []
    for lab, m, e, tol in (("S3_sensor_delay_s", delay, 0.08, dt + 1e-9),
                           ("S3_sensor_rate_hz", rate, 50.0, 0.5),
                           ("S3_sensor_dropout_frac", drop, 0.1, 0.01),
                           ("S3_sensor_noise_std", nstd, 0.1, 0.005)):
        res.append(dict(check_label=lab, case_label="50hz_80ms_10pct_drop", measured_nd=m, expected_nd=e,
                        error_frac=m - e, tolerance_frac=tol, pass_bool=abs(m - e) <= tol,
                        note_label="absolute error vs configured (error column in the check's unit)"))
    return res


def check_wing(cfg):
    res = []
    p = cfg["fixedwing_reference"]
    env = cfg["environment"]
    aero = FixedWingAero(p["aero"])
    clmax, a_clmax = aero.cl_max()
    a2 = math.radians(2.0)
    slope = (aero.lift_coeff(a2 + 1e-4) - aero.lift_coeff(a2 - 1e-4)) / 2e-4
    e_s = slope / aero.cla - 1
    res.append(dict(check_label="S4_wing_lift_slope_attached", case_label="fixedwing_reference",
                    measured_nd=slope, expected_nd=aero.cla, error_frac=e_s, tolerance_frac=0.01,
                    pass_bool=abs(e_s) < 0.01, note_label="dCL/dalpha per rad at 2 deg vs the coefficient"))
    drop = aero.lift_coeff(a_clmax + math.radians(8.0)) / clmax
    res.append(dict(check_label="S4_wing_stalls_past_clmax", case_label="fixedwing_reference",
                    measured_nd=drop, expected_nd=0.85, error_frac=drop - 0.85, tolerance_frac=0.0,
                    pass_bool=drop < 0.85,
                    note_label=f"CL 8 deg past CLmax / CLmax (< 0.85 = lift breaks down); CLmax {clmax:.3f} at {math.degrees(a_clmax):.1f} deg"))
    # trimmed level flight: lift = weight
    st = FixedWingStack(p, env, np.random.default_rng(7), [0, 0, 60], [25, 0, 0], quat_from_euler(0, 0.05, 0))
    st.ap.alt_cmd = 60.0
    st.outer = lambda s, t: setattr(s.ap, "course_cmd", line_course(s.nav.pos, [0, 0, 0], 0.0, 1.0, 0.05)[0])
    acc = []
    def rec(s, t):
        a = s.phys.last
        Va = a["airspeed_m_s"]
        L = 0.5 * env["air_density_kg_m3"] * Va * Va * s.phys.aero.S * a["cl_nd"]   # total CL as applied
        pitch = math.asin(max(-1.0, min(1.0, s.body.R[2, 0])))
        return L + a["thrust_n"] * math.sin(pitch)
    log = _run(st, 40.0, every=rec)
    Lm = float(np.mean(log[-int(10 / st.dt):]))
    W = st.body.mass_kg * GRAVITY_M_S2
    e = Lm / W - 1
    res.append(dict(check_label="S4_wing_lift_equals_weight_level_flight", case_label="fixedwing_reference_25_m_s",
                    measured_nd=Lm, expected_nd=W, error_frac=e, tolerance_frac=0.02, pass_bool=abs(e) < 0.02,
                    note_label="vertical lift + thrust component N vs weight, mean of last 10 s"))
    return res


def check_stall_speed(cfg):
    """Slow the reference fixed-wing by 0.25 m/s per s while holding altitude; the speed at
    which it can no longer hold altitude (alpha passes CLmax) vs sqrt(2W / (rho S CLmax))."""
    p = dict(cfg["fixedwing_reference"])
    env = cfg["environment"]
    rho = env["air_density_kg_m3"]
    aero = FixedWingAero(p["aero"])
    clmax, a_clmax = aero.cl_max()
    st = FixedWingStack(p, env, np.random.default_rng(8), [0, 0, 300], [22, 0, 0], quat_from_euler(0, 0.08, 0))
    ap = dict(p["autopilot"])
    st.ap.g["max_pitch_deg"] = 35.0
    st.ap.alt_cmd = 300.0
    st.ap.airspeed_cmd = 22.0
    state = {"t0": None}

    def outer(s, t):
        s.ap.course_cmd = line_course(s.nav.pos, [0, 0, 0], 0.0, 1.0, 0.05)[0]
        s.ap.airspeed_cmd = max(5.0, 22.0 - 0.25 * max(0.0, t - 10.0))
    st.outer = outer
    log = _run(st, 70.0, every=lambda s, t: (t, s.phys.last["alpha_rad"], s.phys.last["airspeed_m_s"], s.body.pos[2]))
    L = np.array(log)
    i = int(np.argmax(L[:, 1] >= a_clmax))
    W = st.body.mass_kg * GRAVITY_M_S2
    v_th = math.sqrt(2 * W / (rho * aero.S * clmax))
    v_meas = float(L[i, 2]) if L[i, 1] >= a_clmax else float("nan")
    e = v_meas / v_th - 1
    return [dict(check_label="S5_stall_speed_1g", case_label="fixedwing_reference", measured_nd=v_meas,
                 expected_nd=v_th, error_frac=e, tolerance_frac=0.05, pass_bool=abs(e) < 0.05,
                 note_label="airspeed m/s when alpha reaches the CLmax angle in a slow deceleration")]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(_SIM, "docking", "results", "phase1"))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    cfg = load_cfg()
    rows = []
    for name, fn in (("hover", lambda: check_hover(cfg)), ("tilt", lambda: check_trim_tilt(cfg)),
                     ("decel", lambda: check_level_out_decel(cfg)), ("free_fall", lambda: check_free_fall(cfg)),
                     ("repro", lambda: check_reproducibility(args.out, args.workers)),
                     ("turbulence", lambda: check_turbulence(args.out)), ("motor", check_motor),
                     ("sensor", check_sensor), ("wing", lambda: check_wing(cfg))):
        r = fn()
        for x in r:
            print(f"[{'PASS' if x['pass_bool'] else 'FAIL'}] {x['check_label']:45s} {x['case_label']:28s} "
                  f"meas {x['measured_nd']:.6g}  exp {x['expected_nd']:.6g}  err {x['error_frac']:.3g}")
        rows.extend(r)
    for i, r in enumerate(rows):
        r["row_id"] = i
    write_csv(rows, os.path.join(args.out, "checks.csv"))
    ok = all(r["pass_bool"] for r in rows)
    print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
