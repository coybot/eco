"""One Bee35 balcony mission in the sign frame M: depart point by the dock -> gate -> balcony ->
box set down and released -> back out -> recovery start point by the dock.

Vehicle: translational rigid body with the thrust vector following its command through a
first-order lag (attitude loop, 0.06 s) and the motors (0.04 s); drag against the local
urban wind (mean, shear, turbulence, gusts, facade up/downdrafts); tilt and thrust limits.
Collision shape: a sphere around the drone (capsule radius) plus one at the hanging box.

Sensors (spec section 6 models): UWB ranges to the three sign beacons (exact in M) and to the
three mothership anchors (placed in M from the broadcast pose, with its errors); NLOS
(+0.10-0.30 m) when the line crosses a wall, slab, solid railing or divider; drone barometer
(drift) and, as a variant, a barometer in the sign; downward range and flow (valid below 8 m;
no reading on glass); IMU.

Navigation: 7-state Kalman filter (position, velocity, barometer bias) in M. The mothership
anchors carry it near the mothership, the sign beacons near the wall, the down range over the
balcony floor (whose height relative to the sign is install data).
"""
from __future__ import annotations

import math
import time

import numpy as np

from delivery.urban_wind import UrbanWind
from docksim.config import G_M_S2, RHO_KG_M3, load_cfg
from docksim.drone import RelNav
from docksim.motion import MothershipMotion, rot_y
from docksim.sizing import power_w

from .scenarios import SCENARIOS, aabb_clearance, make, ray_down, segment_blocked


class _WindShim:
    """Adapts a balcony scenario to UrbanWind (z up, facade at y = 0 facing +y)."""

    def __init__(self, sc, gust_scale=1.0):
        self.wind_mean_m_s = sc.wind_mean
        self.info = dict(ground_z_m=sc.ground_z)
        self.roof_z_max_m = sc.roof_z
        self.gust_scale_nd = gust_scale
        self.facades = [((0.0, 0.0, sc.ground_z), (0.0, 1.0, 0.0), sc.roof_z - sc.ground_z)]
        self.street_width_m = 40.0


def dock_to_M(R_m, p_m, r_D):
    """Frame D (x fwd, y up, z stbd) offset -> M (z up). The mothership world frame here is M with
    its y and z swapped: D's 'up' (y) is M's z."""
    v = R_m @ r_D                                        # in the mothership's y-up world
    return p_m + np.array([v[0], -v[2], v[1]])


class Nav:
    """x = [p(3), v(3), baro_bias, s(3), g(3)]. The mothership places its anchors in M with an error
    o = s + g: s is its constant survey error of the sign, g its broadcast pose error (Gauss-Markov,
    sigma 0.5 m, tau 5 s). Both are states: treating them as white range noise makes the filter
    overconfident (60 correlated ranges a second), a random walk lets the poorly observed vertical
    part drift to the mirror solution near the near-coplanar mothership anchors."""

    N = 13

    def __init__(self, p0, sig_s, sig_g, tau_g, sig_bias, sa=1.0):
        self.x = np.zeros(self.N)
        self.x[:3] = p0
        Ss = np.diag(np.asarray(sig_s, float) ** 2)
        Sg = np.eye(3) * sig_g ** 2
        P = np.zeros((self.N, self.N))
        P[:3, :3] = Ss + Sg + np.eye(3) * 0.05 ** 2     # start position known in the mothership's frame...
        P[7:10, 7:10] = Ss
        P[10:, 10:] = Sg
        P[:3, 7:10] = P[7:10, :3] = Ss                  # ... so its error is the frame error
        P[:3, 10:] = P[10:, :3] = Sg
        P[3:6, 3:6] = np.eye(3) * 0.1 ** 2
        P[6, 6] = sig_bias ** 2
        self.P = P
        self.sa = sa
        self.sig_g, self.tau_g = sig_g, tau_g
        self.Ss, self.Sg = Ss, Sg
        self.n_rej = 0
        self.rej_run = {"sign": 0, "mship": 0}
        self.n_reopen = 0

    @property
    def o(self):
        return self.x[7:10] + self.x[10:13]

    def predict(self, a, dt):
        self.x[:3] += self.x[3:6] * dt + 0.5 * a * dt * dt
        self.x[3:6] += a * dt
        ag = math.exp(-dt / self.tau_g)
        self.x[10:] *= ag
        F = np.eye(self.N)
        F[:3, 3:6] = np.eye(3) * dt
        F[10:, 10:] = np.eye(3) * ag
        Q = np.zeros((self.N, self.N))
        q = self.sa ** 2
        Q[:3, :3] = np.eye(3) * q * dt ** 4 / 4
        Q[:3, 3:6] = Q[3:6, :3] = np.eye(3) * q * dt ** 3 / 2
        Q[3:6, 3:6] = np.eye(3) * q * dt * dt
        Q[6, 6] = (0.02 ** 2) * dt
        Q[10:, 10:] = np.eye(3) * self.sig_g ** 2 * (1 - ag * ag)
        self.P = F @ self.P @ F.T + Q

    def upd(self, H, y, R, gate=9.0):
        H = np.atleast_2d(H)
        y = np.atleast_1d(y)
        S = H @ self.P @ H.T + np.atleast_2d(R)
        Si = np.linalg.inv(S)
        if float(y @ Si @ y) > gate * len(y):
            self.n_rej += 1
            return False
        K = self.P @ H.T @ Si
        self.x += K @ y
        I_KH = np.eye(self.N) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ np.atleast_2d(R) @ K.T
        return True

    def rng(self, A, z, sig, gate=9.0, frame=False):
        """Range to an anchor at A (M). frame=True: A is a mothership anchor as the mothership places
        it; the true anchor is A - o."""
        Ac = A - self.o if frame else A
        d = self.x[:3] - Ac
        h = float(np.linalg.norm(d))
        H = np.zeros(self.N)
        H[:3] = d / h
        if frame:
            H[7:10] = d / h
            H[10:] = d / h
        ok = self.upd(H, z - h, sig ** 2, gate)
        src = "mship" if frame else "sign"
        self.rej_run[src] = 0 if ok else self.rej_run[src] + 1
        if self.rej_run[src] >= 60:                      # 1 s of continuous rejection: the filter has locked
            self.reopen()                                # onto a wrong frame offset; re-open and re-acquire
        return ok

    def reopen(self):
        """Inflate the frame-offset and position covariance back to the prior (correlations dropped)."""
        self.P[7:10, :] = 0.0
        self.P[:, 7:10] = 0.0
        self.P[10:, :] = 0.0
        self.P[:, 10:] = 0.0
        self.P[7:10, 7:10] = self.Ss
        self.P[10:, 10:] = self.Sg
        self.P[:3, :3] += self.Ss + self.Sg
        self.rej_run = {"sign": 0, "mship": 0}
        self.n_reopen += 1

    def zfix(self, z, sig, with_bias=False, gate=16.0):
        H = np.zeros(self.N)
        H[2] = 1.0
        if with_bias:
            H[6] = 1.0
        return self.upd(H, z - (self.x[2] + (self.x[6] if with_bias else 0.0)), sig ** 2, gate)

    def vel(self, v2, sig):
        H = np.zeros((2, self.N))
        H[0, 3] = H[1, 4] = 1.0
        return self.upd(H, v2 - self.x[3:5], np.eye(2) * sig ** 2)


def run_mission(cfg, sid, seed, opts=None, verbose=False):
    opts = opts or {}
    bm, b, ds, ma = cfg["balcony_mission"], cfg["bee35"], cfg["dock_sensors"], cfg["mothership_airframe"]
    sc = make(sid, seed, cfg, sign_side=opts.get("sign_side_m"))
    rng = np.random.default_rng([seed, SCENARIOS.index(sid) + 901, int(opts.get("variant_id", 0))])
    wind = UrbanWind(_WindShim(sc), seed + 7919 * (SCENARIOS.index(sid) + 1), t_end_s=400.0)
    noise = opts.get("uwb_noise_scale", 1.0)
    sign_baro = opts.get("sign_baro", bm["sign"]["barometer"])
    self_switch_m = opts.get("rel_switch_m", 8.0)
    # mothership: hover motion about its mean position, nose into the wind
    mm = MothershipMotion(ma["hover_motion"][sc.hover_level], ma["hover_motion"], rng)
    wdir = sc.wind_mean / max(np.linalg.norm(sc.wind_mean), 1e-6)
    yaw0 = math.atan2(wdir[1], wdir[0]) + math.pi              # heading of the nose in M's x-y plane
    pocket = int(rng.integers(4))
    px = cfg["dock"]["pocket_x_m"][pocket]
    rec_start_D = np.array([px, 3.0, 3.0])
    depart_D = np.array([px, cfg["dock_ops"]["launch"]["depart_y_m"], cfg["dock_ops"]["launch"]["depart_z_m"]])

    def mship():
        """(R from D to the y-up world rotated by the mean heading, position in M)."""
        Rh = rot_y(yaw0)                    # about y-up: D's nose -> (cos, ., -sin) -> M (cos yaw0, sin yaw0, 0)
        pos = sc.hover_pos + np.array([mm.pos_W[0], -mm.pos_W[2], mm.pos_W[1]])
        return Rh @ mm.Rm, pos

    # broadcast errors (GM, delay) and the survey error of the sign in the mothership's map
    bc = ds["broadcast"]
    surv = rng.normal(0, 1, 3) * np.array(bm["sign"]["survey_sigma_m"])
    e_bp = rng.normal(0, bc["pos_sigma_m"], 3)
    e_bh = rng.normal(0, math.radians(bc["heading_sigma_deg"]))
    tau_bc = bc["error_tau_s"]
    uwb = ds["uwb"]
    bias_s = rng.uniform(-uwb["bias_max_m"], uwb["bias_max_m"], 3) * noise
    bias_m = rng.uniform(-uwb["bias_max_m"], uwb["bias_max_m"], 3) * noise
    m_anchor_D = np.array([ma["anchors_m"][k] for k in ma["anchor_sets"]["triangle"]])

    # vehicle state
    m_box = b["box"]["mass_kg"]
    m_e = b["mass_kg"] + b["bracket_mass_kg"]
    mass = m_e + m_box
    Rm, pm = mship()
    p = dock_to_M(Rm, pm, depart_D)
    v = np.zeros(3)
    thrust_vec = np.array([0.0, 0.0, mass * G_M_S2])
    tmax = b["thrust_max_total_n"] * b["controller"]["thrust_usable_frac"]
    tilt_max = math.radians(bm["transit_max_tilt_deg"])
    a_max = bm["transit_max_accel_m_s2"]
    # navigation: the drone knows its D position; the mothership converts to M with its own estimate
    # of the sign position (survey error) and its broadcast pose error

    # barometer: without a sign barometer the drone's pressure height maps to M through the surveyed sign
    # height (vertical survey error + sensor bias); with one, only the two sensors' relative bias remains
    sig_bias = ds["baro"]["sigma_m"] if sign_baro else math.hypot(bm["sign"]["survey_sigma_m"][2], 0.9)
    baro_bias = float(rng.normal(0, sig_bias))
    nav = Nav(p + surv + e_bp, bm["sign"]["survey_sigma_m"], bc["pos_sigma_m"], tau_bc, sig_bias)
    kp, kv, ki = 4.0, 4.5, 0.8
    integ = np.zeros(3)
    dt, dt_nav = 0.005, 0.02
    nsub = int(round(dt_nav / dt))
    t = 0.0
    energy = 0.0
    state = "TRANSIT"
    t_state = 0.0
    events = [(0.0, state)]
    r_ref = nav.x[:3].copy()
    collision = None
    collision_speed = 0.0
    drop_pos = None
    drop_err = None
    gate = np.array([0.0, bm["gate_out_m"], sc.entry_z])
    over_drop = np.array([sc.drop[0], sc.drop[1], sc.entry_z])
    t_balcony0 = None
    gate_err = None
    handover_err = None
    released = False
    gave_up = None
    min_clear = math.inf
    min_clear_kind = ""
    t_uwb = 0.0
    t_rng = 0.0
    nlos = 0
    nuwb = 0
    last_range = math.nan
    usable_j = b["battery_wh"] * b["battery_usable_frac"] * 3600
    rad = bm["drone_capsule_radius_m"]
    box_off = np.array([0.0, 0.0, -b["box"]["rim_drop_m"] - b["box"]["size_m"][1] / 2])
    wall_t0 = time.time()

    def goto(s):
        nonlocal state, t_state
        state, t_state = s, t
        events.append((round(t, 2), s))

    def ramp(tgt, speed):
        nonlocal r_ref
        d = tgt - r_ref
        n = float(np.linalg.norm(d))
        speed = min(speed, math.sqrt(2 * 0.8 * n) + 0.02)
        st = speed * dt_nav
        r_ref = tgt.copy() if n <= st else r_ref + d / n * st
        return d / n * speed if n > st else np.zeros(3)

    vref = np.zeros(3)
    rel = None                                   # dock-frame relative filter, started near the mothership
    t_rel0 = 0.0
    hold_t = 0.0
    v_prev_M = vel_M(mm)
    a_m_M = np.zeros(3)
    while t < opts.get("t_max_s", 400.0):
        # ---------------- navigation and guidance at 50 Hz
        Rm, pm = mship()
        # broadcast pose as the drone receives it (errors random-walk with tau)
        a_bc = math.exp(-dt_nav / tau_bc)
        e_bp = a_bc * e_bp + math.sqrt(1 - a_bc * a_bc) * rng.normal(0, bc["pos_sigma_m"], 3)
        e_bh = a_bc * e_bh + math.sqrt(1 - a_bc * a_bc) * rng.normal(0, math.radians(bc["heading_sigma_deg"]))
        a_meas = (thrust_vec + _drag(v, wind.velocity(p, t), b)) / mass - np.array([0, 0, G_M_S2]) \
            + rng.normal(0, 0.15, 3)
        nav.predict(a_meas, dt_nav)
        if t >= t_uwb:
            t_uwb += 1.0 / uwb["rate_hz"]
            # sign beacons: exact in M
            for k, A in enumerate(sc.anchors):
                if rng.random() < uwb["dropout_p"]:
                    continue
                r = float(np.linalg.norm(p - A))
                if r > 100.0:
                    continue
                z = r + bias_s[k] + rng.normal(0, uwb["sigma_m"] * noise)
                nuwb += 1
                if segment_blocked(sc, p, A + np.array([0, 0.01, 0])):
                    z += rng.uniform(*uwb["blocked_bias_m"])
                    nlos += 1
                nav.rng(A, z, math.hypot(uwb["sigma_m"] * noise, uwb["bias_max_m"] * noise / math.sqrt(3)))
            # mothership anchors: true position from the true pose; assumed position from the broadcast
            Rb = rot_y(e_bh) @ Rm
            pb = pm + e_bp + surv
            for k, AD in enumerate(m_anchor_D):
                if rng.random() < uwb["dropout_p"]:
                    continue
                At = dock_to_M(Rm, pm, AD)
                Ab = dock_to_M(Rb, pb, AD)
                r = float(np.linalg.norm(p - At))
                if r > 100.0:
                    continue
                z = r + bias_m[k] + rng.normal(0, uwb["sigma_m"] * noise)
                nav.rng(Ab, z, math.hypot(uwb["sigma_m"] * noise, uwb["bias_max_m"] * noise / math.sqrt(3)), frame=True)
        if t >= t_rng:
            t_rng += 0.02
            rr, kind = ray_down(sc, p + np.array([0.085, 0.0, 0.0]))
            if math.isfinite(rr):
                rr += rng.normal(0, 0.02 if rr < 2 else 0.01 * rr)
                last_range = max(rr, 0.02)
                # height fix only over the known target floor (install data: floor relative to the sign)
                xe = nav.x[:3]
                over_floor = abs(xe[0] - sc.drop[0]) < sc.half_width - 0.2 and 0.1 < xe[1] < sc.railing_y - 0.15
                if over_floor:
                    nav.zfix(sc.floor_z + last_range, 0.04)
                # flow, relative to the surface (static)
                if 0.08 < rr < 8.0:
                    nav.vel(v[:2] + rng.normal(0, ds["flow"]["sigma_m_s"], 2), ds["flow"]["sigma_m_s"])
            else:
                last_range = math.nan
            # barometer (bias state; its prior depends on whether the sign carries a barometer)
            baro_bias += rng.normal(0, 0.02 * math.sqrt(0.02))
            zb = p[2] + baro_bias + rng.normal(0, ds["baro"]["sigma_m"])
            nav.zfix(zb, ds["baro"]["sigma_m"] * 2.0, with_bias=True)

        # dock-frame relative navigation for the last leg (the dock sim RelNav): within rel_switch_m of the
        # mothership the drone flies to the recovery start point in D, as the dock sim assumes
        Rfull = Rm
        vm = vel_M(mm)
        a_m_M = (vm - v_prev_M) / dt_nav
        v_prev_M = vm
        if state == "RETURN" and rel is None and np.linalg.norm(nav.x[:3] - (pm + e_bp + surv - nav.o)) < self_switch_m:
            rel = RelNav(1.0)
            r0 = Rfull.T @ M_to_yup(nav.x[:3] - (pm + e_bp + surv - nav.o))
            rel.init(r0, 2.0, 1.0)
            t_rel0 = t
            hold_t = 0.0
            events.append((round(t, 2), "REL_NAV"))
        if rel is not None:
            a_rel_D = Rfull.T @ M_to_yup(a_meas - a_m_M) + rng.normal(0, 0.3, 3)
            rel.predict(a_rel_D, dt_nav)
            r_true_D = Rfull.T @ M_to_yup(p - pm)
            for k, AD in enumerate(m_anchor_D):
                if rng.random() < uwb["dropout_p"] or (int(t / dt_nav) % 2):
                    continue
                z = float(np.linalg.norm(r_true_D - AD)) + bias_m[k] + rng.normal(0, uwb["sigma_m"] * noise)
                rel.range(AD, z, np.zeros(3), math.hypot(uwb["sigma_m"] * noise, uwb["bias_max_m"] * noise / math.sqrt(3)))
            if int(t / dt_nav) % 10 == 0:            # barometer vs the broadcast altitude: weak height
                rel.fix(1, r_true_D[1] + rng.normal(0, 0.7), 0.7)
        # ---------------- mission state machine
        pe = nav.x[:3]
        if verbose and verbose > 1 and int(t / dt_nav) % 100 == 0:
            print(f"t {t:6.1f} {state:8s} true {np.round(p,2)} est {np.round(pe,2)} o_est {np.round(nav.o,2)} o_true {np.round(e_bp+surv,2)} dist_m {np.linalg.norm(p-pm):.1f} rej {nav.n_rej}")
        if state == "TRANSIT":
            pre = gate + np.array([0.0, 6.0, 0.0])              # descend outside, then approach level
            vref = ramp(pre, bm["cruise_speed_m_s"] if np.linalg.norm(pe - pre) > 12 else bm["near_speed_m_s"])
            if np.linalg.norm(pe - pre) < 0.5:
                goto("APPROACH")
        elif state == "APPROACH":
            vref = ramp(gate, bm["near_speed_m_s"])
            if np.linalg.norm(pe - gate) < 0.25 and np.linalg.norm(nav.x[3:6]) < 0.3:
                gate_err = float(np.linalg.norm(p - gate))
                t_balcony0 = t
                goto("ENTER")
        elif state == "ENTER":
            vref = ramp(over_drop, bm["inside_speed_m_s"])
            if np.linalg.norm(pe - over_drop) < 0.08 and np.linalg.norm(nav.x[3:6]) < 0.15:
                goto("SET_DOWN")
        elif state == "SET_DOWN":
            tgt = np.array([sc.drop[0], sc.drop[1], sc.floor_z + bm["setdown_range_m"] - 0.05])
            vref = ramp(tgt, 0.25)
            if math.isfinite(last_range) and last_range <= bm["setdown_range_m"] + 0.01:
                drop_pos = p + box_off
                drop_err = float(np.linalg.norm((drop_pos - sc.drop)[:2]))
                mass = m_e
                released = True
                goto("RELEASE")
        elif state == "RELEASE":
            tgt = np.array([sc.drop[0], sc.drop[1] - bm["release_slide_m"], r_ref[2]])
            vref = ramp(tgt, 0.2)
            if np.linalg.norm(pe[:2] - tgt[:2]) < 0.03:
                goto("CLIMB")
        elif state == "CLIMB":
            vref = ramp(np.array([pe[0], pe[1], sc.entry_z]), 0.4)
            if abs(pe[2] - sc.entry_z) < 0.05:
                goto("EXIT")
        elif state == "EXIT":
            vref = ramp(gate, bm["inside_speed_m_s"])
            if np.linalg.norm(pe - gate) < 0.3:
                goto("RETURN")
        elif state == "RETURN":
            if rel is None:
                tgt = dock_to_M(rot_y(e_bh) @ Rm, pm + e_bp + surv, rec_start_D) - nav.o
                vref = ramp(tgt, bm["cruise_speed_m_s"] if np.linalg.norm(pe - tgt) > 8 else bm["near_speed_m_s"])
            else:
                # guidance in D, converted to an M-frame reference about the drone's own estimate, with the
                # mothership's (broadcast) velocity as feed-forward
                eD = rec_start_D - rel.r
                w = Rfull @ eD
                eM = np.array([w[0], -w[2], w[1]])
                d = float(np.linalg.norm(eM))
                step = min(d, 1.5)
                r_ref = pe + (eM / d * step if d > 1e-6 else 0.0)
                vref = vm + (eM / d * min(bm["near_speed_m_s"] * 2, math.sqrt(1.6 * d)) if d > 0.05 else 0.0)
                hold_t = hold_t + dt_nav if d < bm["handover_tol_m"] else 0.0
                if verbose and verbose > 2 and int(t / dt_nav) % 50 == 0:
                    rt = Rfull.T @ M_to_yup(p - pm)
                    print(f"REL t {t:6.1f} relr {np.round(rel.r,2)} true {np.round(rt,2)} tgt {rec_start_D} d {d:.2f} rej {rel.n_rej}/{rel.n_acc}")
                if hold_t >= 1.0:
                    handover_err = float(np.linalg.norm(Rfull.T @ M_to_yup(p - pm) - rec_start_D))
                    goto("HANDOVER")
                    break
                if t - t_rel0 > 60.0:
                    gave_up = "handover_timeout"
                    goto("NO_HANDOVER")
                    break
        # give-up rules
        if state in ("ENTER", "SET_DOWN", "RELEASE") and t_balcony0 is not None \
                and t - t_balcony0 > bm["balcony_time_limit_s"] and not released:
            gave_up = "balcony_time"
            goto("CLIMB")
        p_ret = power_w(b, mass, bm["cruise_speed_m_s"])
        if energy + p_ret * (np.linalg.norm(p - pm) / bm["cruise_speed_m_s"] + 40) > (1 - bm["energy_reserve_frac"]) * usable_j \
                and state not in ("RETURN", "EXIT", "CLIMB"):
            gave_up = gave_up or "energy"
            goto("CLIMB")

        # position control -> thrust vector command
        e = r_ref - pe
        integ = np.clip(integ + ki * e * dt_nav, -6.0, 6.0)          # must hold the drag of an 11 m/s wind
        a_cmd = kp * e + kv * (vref - nav.x[3:6]) + integ
        n = np.linalg.norm(a_cmd)
        if n > a_max:
            a_cmd *= a_max / n
        F = mass * (a_cmd + np.array([0, 0, G_M_S2]))
        F[2] = max(F[2], 0.2 * mass * G_M_S2)
        h = math.hypot(F[0], F[1])
        hmax = F[2] * math.tan(tilt_max)
        if h > hmax:
            F[:2] *= hmax / h
        if np.linalg.norm(F) > tmax:
            F *= tmax / np.linalg.norm(F)
        # ---------------- vehicle dynamics at 200 Hz
        for _ in range(nsub):
            thrust_vec += (F - thrust_vec) * (1 - math.exp(-dt / 0.07))
            wv = wind.velocity(p, t)
            acc = (thrust_vec + _drag(v, wv, b)) / mass - np.array([0, 0, G_M_S2])
            v = v + acc * dt
            p = p + v * dt
            mm.update(dt)
            airspeed = float(np.linalg.norm(v - wv))
            energy += (power_w(b, mass, airspeed) + mass * G_M_S2 * max(v[2], 0.0) / 0.5) * dt
            t += dt
        # collisions: drone body box (footprint half-width incl. a yaw margin) and the hanging box
        hw = b["size_m"][0] / 2 + 0.015
        dist, bx = aabb_clearance(sc, p + np.array([-hw, -hw, 0.0]), p + np.array([hw, hw, b["size_m"][1]]))
        if not released:
            bxs = b["box"]["size_m"]
            on_floor = state in ("SET_DOWN", "RELEASE")
            d2, b2 = aabb_clearance(sc, p + np.array([-bxs[0] / 2, -bxs[2] / 2, -b["box"]["rim_drop_m"] - bxs[1]]),
                                    p + np.array([bxs[0] / 2, bxs[2] / 2, -b["box"]["rim_drop_m"]]),
                                    skip=(lambda q: q.kind == "slab") if on_floor else None)
            if d2 < dist:
                dist, bx = d2, b2
        if bx is not None and dist < min_clear and bx.kind != "ground":
            min_clear, min_clear_kind = dist, bx.kind
        if dist < 0:
            collision = bx.kind
            collision_speed = float(np.linalg.norm(v))
            goto("COLLISION")
            break
        if energy > usable_j:
            gave_up = "battery_empty"
            goto("BATTERY")
            break

    delivered = bool(released and drop_err is not None and drop_err <= bm["drop_tolerance_m"])
    returned = state == "HANDOVER"
    row = dict(scenario_label=sid, seed=seed, sign_side_m=sc.info["sign_side_m"], sign_baro_bool=bool(sign_baro),
               uwb_noise_scale=noise, level=sc.info["level"], depth_m=round(sc.info["depth_m"], 2),
               width_m=round(sc.info["width_m"], 2), wind_m_s=round(sc.info["wind_m_s"], 2),
               standoff_m=round(sc.info["standoff_m"], 1), hover_level=sc.hover_level,
               delivered_bool=delivered, released_bool=released, returned_bool=returned,
               mission_ok_bool=delivered and returned, collision_label=collision or "",
               collision_speed_m_s=round(collision_speed, 2), gave_up_label=gave_up or "",
               final_state_label=state, gate_err_m=_r(gate_err), drop_err_m=_r(drop_err),
               handover_err_m=_r(handover_err), min_clearance_m=_r(min_clear), min_clearance_kind_label=min_clear_kind,
               time_s=round(t, 1), energy_wh=round(energy / 3600, 2),
               battery_used_frac=round(energy / usable_j, 3), nlos_frac=round(nlos / max(nuwb, 1), 3),
               nav_rejected_count=nav.n_rej, nav_reopen_count=nav.n_reopen, wall_s=round(time.time() - wall_t0, 2),
               events_label=" ".join(f"{a}:{s}" for a, s in events))
    if verbose:
        print(row)
    return row


def M_to_yup(v):
    return np.array([v[0], v[2], -v[1]])


def vel_M(mm):
    return np.array([mm.vel_W[0], -mm.vel_W[2], mm.vel_W[1]])


def _drag(v, wv, b):
    vr = wv - v
    return 0.5 * RHO_KG_M3 * b["drag_area_m2"] * np.linalg.norm(vr) * vr


def _r(x, nd=3):
    return None if x is None else round(float(x), nd)


if __name__ == "__main__":
    import sys
    run_mission(load_cfg(), sys.argv[1] if len(sys.argv) > 1 else "B1", int(sys.argv[2]) if len(sys.argv) > 2 else 1,
                verbose=True)
