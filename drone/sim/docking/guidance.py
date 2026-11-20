"""Docking guidance (quad side; sensors, own navigation, relative navigation and the
mothership's broadcast only).

Geometry in the world frame, from the broadcast attitude: n = funnel axis out of the
mouth, T = throat. The guided point is the PROBE TIP, tip = quad CG + L R_q d_probe. Its
axial coordinate s = (tip - T) . n (0 at the throat, H at the mouth) and its lateral
offset e_lat (perpendicular to n).

States
  STATION   hold the tip at s = H + station_m on the axis. Leave for APPROACH once the
            relative navigation is fresh and the tip has been inside ``station_tol_m``
            for ``station_dwell_s``.
  APPROACH  the desired s falls at ``close_speed_m_s``; lateral error must stay inside a
            corridor that narrows toward the mouth: |e_lat| <= corridor_base_m +
            corridor_slope_nd * (s - H).
  FINAL     s < H + commit_m: committed. Keep closing; no corridor abort (the funnel
            guides), but a rim hit, a hard contact the IMU feels, or the tip backing out
            aborts. Level-out designs reduce the allowed tilt here.
  LATCHED   the latch reports engaged (a switch on the dock, relayed over the link): motors
            stop after ``motor_stop_delay_s``.
  ABORT     back out along the axis to s = H + station_m + 0.5 m at ``abort_speed_m_s``,
            then STATION again (next attempt). After ``max_attempts`` failures: GIVE_UP
            (retreat and leave).
Control: a = a_ms (broadcast) + kp (p_des - tip_rel) + kd (v_des - v_tip_rel) + ki int +
drag feed-forward (nominal drag area, airspeed from own velocity minus the broadcast wind).
"""
from __future__ import annotations

import math

import numpy as np

from flight.frames import GRAVITY_M_S2, quat_to_rot


class DockingGuidance:
    def __init__(self, design, cfg: dict, cda_nominal_m2: float, rho_kg_m3: float):
        g = cfg.get
        self.d = design
        self.c = cfg
        self.H = design.depth_m()
        self.n_body = design.axis_body()
        self.state = "STATION"
        self.t_state = 0.0
        self.attempt = 1
        self.attempts_used = 0
        self.max_attempts = g("max_attempts", 3)
        self.events: list = []
        self.abort_reasons: list = []
        self.cda, self.rho = cda_nominal_m2, rho_kg_m3
        self.kp, self.kd, self.ki = g("kp_per_s2", 6.0), g("kd_per_s", 5.0), g("ki_per_s3", 2.0)
        self.i = np.zeros(3)
        self.s_des = self.H + g("station_m", 1.5)
        self.in_tol_since = None
        self.acc_hist: list = []
        self.latched = False
        self.contact_flag = False
        self.closing_ok = True
        self.dob = np.zeros(3)                 # disturbance observer estimate (world accel)
        self.a_cmd_prev = None
        self.err_f = None

    def _go(self, s, t, reason=None):
        self.events.append((round(t, 3), s if reason is None else f"{s}:{reason}"))
        if s == "ABORT":
            self.abort_reasons.append(reason)
        self.state, self.t_state = s, t

    def step(self, quad, relnav, msg, t, dt, latch_engaged: bool, gimbal_theta=None):
        """quad = QuadStack; msg = latest broadcast dict (or None). Sets fc demands."""
        g = self.c
        fc, nav = quad.fc, quad.nav
        d = self.d
        if self.state == "LATCHED":
            if t - self.t_state > g.get("motor_stop_delay_s", 0.3):
                fc.motors_off = True
            return
        if latch_engaged and self.state in ("APPROACH", "FINAL"):
            self._go("LATCHED", t)
            self.latched = True
            return
        if msg is None or not relnav.kf.initialised:
            fc.set_accel(t, np.zeros(3), 0.0)
            return
        Rms = quat_to_rot(msg["quat"])
        n = Rms @ self.n_body
        yaw = math.atan2(Rms[1, 0], Rms[0, 0])
        qm = quad.sensors.ahrs.read(t)
        Rq = quat_to_rot(qm.value) if qm is not None else np.eye(3)
        pdir = d.probe_dir_body()
        if gimbal_theta is not None:                # measured gimbal deflection (encoders)
            a_ = float(np.linalg.norm(gimbal_theta))
            if a_ > 1e-9:
                k_ = gimbal_theta / a_
                pdir = pdir * math.cos(a_) + np.cross(k_, pdir) * math.sin(a_) + k_ * float(k_ @ pdir) * (1 - math.cos(a_))
        tip_off = d.probe_length_m * (Rq @ pdir)
        e = -relnav.r + tip_off                       # tip relative to the throat
        v_tip = -relnav.v                             # ignore the probe's rotation rate
        s = float(e @ n)
        e_lat = e - s * n
        lat = float(np.linalg.norm(e_lat))
        age = relnav.age_s(t)
        st = self.state
        H = self.H
        station_s = H + g.get("station_m", 1.5)
        # IMU contact detection: sudden specific-force change not explained by commands
        am = quad.sensors.acc.read(t)
        if am is not None:
            self.acc_hist.append(am.value.copy())
            self.acc_hist = self.acc_hist[-6:]
            jump = float(np.linalg.norm(self.acc_hist[-1] - np.mean(self.acc_hist[:-1], axis=0))) if len(self.acc_hist) > 3 else 0.0
        else:
            jump = 0.0
        vclose = g.get("close_speed_m_s", 0.5)
        tilt_limit = None
        v_des_s = 0.0
        if st == "STATION":
            self.s_des = station_s
            ev = np.array([s - station_s, lat])
            self.err_f = ev if self.err_f is None else self.err_f + 0.05 * (ev - self.err_f)   # ~0.2 s LPF
            ok = age < g.get("max_nav_age_s", 0.25) and abs(self.err_f[0]) < g.get("station_tol_m", 0.15) \
                and self.err_f[1] < g.get("station_tol_m", 0.15)
            if ok:
                self.in_tol_since = self.in_tol_since if self.in_tol_since is not None else t
                if t - self.in_tol_since >= g.get("station_dwell_s", 1.0) and t - self.t_state > 1.0:
                    self._go("APPROACH", t)
                    self.s_des = s
            else:
                self.in_tol_since = None
        elif st in ("APPROACH", "FINAL"):
            self.s_des = max(-0.05, self.s_des - vclose * dt)
            v_des_s = -vclose
            corridor = g.get("corridor_base_m", 0.6 * d.mouth_radius_m) + g.get("corridor_slope_nd", 0.25) * max(0.0, s - H)
            if st == "APPROACH":
                if s < H + g.get("commit_m", 0.15):
                    self._go("FINAL", t)
                elif lat > corridor:
                    self._go("ABORT", t, "corridor")
                elif age > g.get("max_nav_age_s", 0.25):
                    self._go("ABORT", t, "nav_stale")
                elif t - self.t_state > g.get("approach_timeout_s", 8.0):
                    self._go("ABORT", t, "timeout")
            else:
                r_ok = d.mouth_radius_m - d.tip_radius_m - g.get("mouth_margin_m", 0.02)
                if s > H - 0.01 and lat > r_ok:
                    self._go("ABORT", t, "will_miss_mouth")
                elif s < -g.get("past_throat_m", 0.10):
                    self._go("ABORT", t, "past_throat_unlatched")
                elif jump > g.get("contact_jump_m_s2", 6.0) and s > H - 0.02 and lat > d.mouth_radius_m * 0.8:
                    self._go("ABORT", t, "rim_contact")
                elif s > H + g.get("commit_m", 0.15) + 0.12:
                    self._go("ABORT", t, "backed_out")
                elif t - self.t_state > g.get("final_timeout_s", 2.5):
                    self._go("ABORT", t, "final_timeout")
            if d.level_out and st == "FINAL" or (d.level_out and s < H + d.level_out_dist_m):
                frac = max(0.0, min(1.0, (s - H + 0.0) / d.level_out_dist_m))
                tilt_limit = math.radians(g.get("trim_tilt_estimate_deg", 48.0)) * frac + math.radians(2.0)
        if self.state == "ABORT":
            self.s_des = min(station_s + 0.5, self.s_des + g.get("abort_speed_m_s", 1.0) * dt)
            v_des_s = g.get("abort_speed_m_s", 1.0)
            if self.s_des >= station_s + 0.5 - 1e-6 and t - self.t_state > 1.0:
                self.attempts_used += 1
                if self.attempt >= self.max_attempts:
                    self._go("GIVE_UP", t)
                else:
                    self.attempt += 1
                    self._go("STATION", t)
                    self.in_tol_since = None
        if self.state == "GIVE_UP":
            self.s_des = min(station_s + 6.0, self.s_des + 1.0 * dt)
            v_des_s = 1.0
        p_des = self.s_des * n
        v_des = v_des_s * n
        err = p_des - e
        verr = v_des - v_tip
        committed = self.state == "FINAL"
        if committed:
            # until the tip is in the mouth keep the lateral loop at reduced gain; inside the
            # funnel the cone does the lateral work and only the axial loop remains
            ax_e, ax_v = float(err @ n) * n, float(verr @ n) * n
            kl = 0.5 if s > H else 0.0
            err = ax_e + kl * (err - ax_e)
            verr = ax_v + max(kl, 0.3) * (verr - ax_v)
        else:
            self.i = np.clip(self.i + err * dt, -0.5, 0.5)
        a = msg["accel_m_s2"] + self.kp * err + self.kd * verr + self.ki * self.i
        an = float(np.linalg.norm(a - msg["accel_m_s2"]))
        amax = g.get("accel_max_m_s2", 6.0)
        if an > amax:
            a = msg["accel_m_s2"] + (a - msg["accel_m_s2"]) * amax / an
        va = nav.vel - msg["wind_m_s"]
        a_drag = 0.5 * self.rho * self.cda * float(np.linalg.norm(va)) * va / fc.mass_est_kg
        # disturbance observer: what the IMU says the quad did minus what it was told to do
        # (gust drag changes, thrust and drag-area errors), low-passed, fed back
        if g.get("dob_enabled", True) and self.a_cmd_prev is not None:
            dist = nav.accel_world - self.a_cmd_prev
            k = 1.0 - math.exp(-2 * math.pi * g.get("dob_bandwidth_hz", 1.5) * dt)
            self.dob += k * (np.clip(dist, -8, 8) - self.dob)
        self.a_cmd_prev = a.copy()
        a = a + a_drag - (self.dob if g.get("dob_enabled", True) else 0.0)
        if committed and s < g.get("push_start_m", 0.06):
            # seat the probe: push along the axis with a set force to pass the latch detent
            a = a - n * g.get("push_force_n", 6.0) / fc.mass_est_kg
        if tilt_limit is not None:
            saved = fc.max_tilt
            fc.max_tilt = tilt_limit
            fc.set_accel(t, a, yaw)
            fc.max_tilt = saved
        else:
            fc.set_accel(t, a, yaw)
        self.last = {"s_m": s, "lat_m": lat, "age_s": age}
