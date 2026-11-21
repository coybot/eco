"""Bee35 relative navigation, flight control and the launch / recovery sequences (spec 7, 9, 10).

RelNav: 6-state Kalman filter of the drone origin's position and velocity relative to the dock,
in a level frame aligned with the estimated dock heading (taken as frame D; the dock's own
tilt is not observable to the drone and is left as model error). Prediction uses the IMU
acceleration minus the dock acceleration differentiated from the broadcast velocity (the
spec's "broadcast velocity as feed-forward"). Updates: UWB ranges, the downward range against
the known dock shape, optical flow, and a position fix when the post seats in the slot.

FlightControl: PID position loop -> world acceleration -> thrust vector with tilt and thrust
limits -> attitude PD on the AHRS attitude and gyro -> X mixer.

Pilot: the spec's sequences as state machines, reading only sensors.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from .config import G_M_S2
from .motion import rot_y
from .sensors import UWB, Attitude, Broadcast, DownRange, Flow


class RelNav:
    def __init__(self, sigma_a):
        self.x = np.zeros(6)
        self.P = np.eye(6)
        self.sa = sigma_a
        self.n_rej = 0
        self.n_acc = 0

    def init(self, r, sig_r, sig_v=0.2):
        self.x[:3] = r
        self.x[3:] = 0.0
        self.P = np.diag([sig_r ** 2] * 3 + [sig_v ** 2] * 3)

    def predict(self, a, dt):
        x, P = self.x, self.P
        x[:3] += x[3:] * dt + 0.5 * a * dt * dt
        x[3:] += a * dt
        F = np.eye(6)
        F[:3, 3:] = np.eye(3) * dt
        q = self.sa ** 2
        Q = np.zeros((6, 6))
        Q[:3, :3] = np.eye(3) * q * dt ** 4 / 4
        Q[:3, 3:] = Q[3:, :3] = np.eye(3) * q * dt ** 3 / 2
        Q[3:, 3:] = np.eye(3) * q * dt * dt
        self.P = F @ P @ F.T + Q

    def _upd(self, H, y, Rm, gate=9.0):
        H = np.atleast_2d(H)
        y = np.atleast_1d(y)
        S = H @ self.P @ H.T + np.atleast_2d(Rm)
        Si = np.linalg.inv(S)
        if float(y @ Si @ y) > gate * len(y):
            self.n_rej += 1
            return False
        K = self.P @ H.T @ Si
        self.x += K @ y
        I_KH = np.eye(6) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ np.atleast_2d(Rm) @ K.T
        self.n_acc += 1
        return True

    def range(self, A, z, tag_off, sigma):
        p = self.x[:3] + tag_off
        dv = p - A
        h = float(np.linalg.norm(dv))
        H = np.zeros(6)
        H[:3] = dv / h
        return self._upd(H, z - h, sigma ** 2)

    def height(self, z, h_pred, sigma, gate=9.0):
        H = np.zeros(6)
        H[1] = 1.0
        return self._upd(H, z - h_pred, sigma ** 2, gate)

    def flow(self, z2, R_LB, sigma):
        H = np.zeros((2, 6))
        H[:, 3:] = R_LB.T[[0, 2], :]
        return self._upd(H, z2 - H[:, 3:] @ self.x[3:], np.eye(2) * sigma ** 2)

    def fix(self, axis, val, sigma):
        H = np.zeros(6)
        H[axis] = 1.0
        return self._upd(H, val - self.x[axis], sigma ** 2, gate=1e9)

    @property
    def r(self):
        return self.x[:3]

    @property
    def v(self):
        return self.x[3:]


class FlightControl:
    def __init__(self, cfg, mass_kg, inertia):
        b = cfg["bee35"]
        c = b["controller"]
        self.c = c
        self.m = mass_kg
        self.I = np.asarray(inertia, float)
        self.kp, self.kv, self.ki = c["kp_pos_per_s2"], c["kv_per_s"], c["ki_pos_per_s3"]
        self.imax = c["i_max_m_s2"]
        self.amax = c["a_max_m_s2"]
        self.tilt = math.radians(c["max_tilt_deg"])
        self.kR = np.array(c["att_kp_per_s2"])
        self.kW = np.array(c["att_kd_per_s"])
        self.tmax = b["thrust_max_total_n"] * c["thrust_usable_frac"]
        a, k = b["rotor_offset_m"], b["yaw_torque_per_thrust_m"]
        xs = np.array([a, a, -a, -a])
        zs = np.array([a, -a, a, -a])
        spin = np.array([1.0, -1.0, -1.0, 1.0])
        M = np.vstack([np.ones(4), -zs, k * spin, xs])
        self.Minv = np.linalg.inv(M)
        self.tmax_motor = b["thrust_max_total_n"] / 4
        self.integ = np.zeros(3)
        self.F_des_W = np.array([0.0, mass_kg * G_M_S2, 0.0])
        self.R_des = np.eye(3)
        self.collective_override = None
        self.yaw_frac = 1.0

    def accel_cmd(self, r_ref, v_ref, r, v, dt, freeze_axes=(), a_ff=None):
        e = r_ref - r
        a = self.kp * e + self.kv * (v_ref - v)
        for ax in range(3):
            if ax not in freeze_axes:
                self.integ[ax] = np.clip(self.integ[ax] + self.ki * e[ax] * dt, -self.imax, self.imax)
        a = a + self.integ
        if a_ff is not None:
            a = a + a_ff
        n = np.linalg.norm(a)
        return a * (self.amax / n) if n > self.amax else a

    def set_accel(self, a_L, psi_dock):
        """World thrust vector and desired attitude for a level-frame acceleration."""
        Rw = rot_y(psi_dock)
        a_W = Rw @ a_L
        F = self.m * (a_W + np.array([0.0, G_M_S2, 0.0]))
        Fy = max(F[1], 0.2 * self.m * G_M_S2)
        Fh = np.array([F[0], F[2]])
        hmax = Fy * math.tan(self.tilt)
        hn = float(np.hypot(*Fh))
        if hn > hmax:
            Fh *= hmax / hn
            hn = hmax
        if Fy > self.tmax:
            Fy, Fh = self.tmax, Fh * 0
        elif Fy * Fy + hn * hn > self.tmax ** 2:
            Fh *= math.sqrt(self.tmax ** 2 - Fy * Fy) / max(hn, 1e-9)
        Fv = np.array([Fh[0], Fy, Fh[1]])
        self.F_des_W = Fv
        b2 = Fv / np.linalg.norm(Fv)
        xc = Rw[:, 0]
        b3 = np.cross(xc, b2)
        b3 /= np.linalg.norm(b3)
        b1 = np.cross(b2, b3)
        self.R_des = np.column_stack([b1, b2, b3])

    def set_level(self, psi_dock, collective):
        self.R_des = rot_y(psi_dock)
        self.collective_override = collective

    def motors(self, R_WB, omega_B):
        Re = self.R_des
        E = 0.5 * (Re.T @ R_WB - R_WB.T @ Re)
        eR = np.array([E[2, 1], E[0, 2], E[1, 0]])
        tau = self.I * (-self.kR * eR - self.kW * omega_B)
        if self.collective_override is not None:
            T = self.collective_override
        else:
            T = float(max(self.F_des_W @ R_WB[:, 1], 0.0))
        # yaw has the lowest priority: mix thrust + roll + pitch first, then add as much yaw as fits
        base = self.Minv @ np.array([T, tau[0], 0.0, tau[2]])
        yaw = self.Minv @ np.array([0.0, 0.0, tau[1], 0.0])
        lo, hi = 0.0, self.tmax_motor
        if base.min() < lo or base.max() > hi:              # roll/pitch first: shift collective to fit
            shift = (lo - base.min()) if base.min() < lo else (hi - base.max())
            base = base + shift
            base = np.clip(base, lo, hi)
        k = 1.0
        for b_, y_ in zip(base, yaw):
            if y_ > 1e-12:
                k = min(k, (hi - b_) / y_)
            elif y_ < -1e-12:
                k = min(k, (lo - b_) / y_)
        self.yaw_frac = max(k, 0.0)
        return np.clip(base + self.yaw_frac * yaw, lo, hi)


class Pilot:
    """Runs the drone: sensors -> RelNav -> sequence state machine -> FlightControl -> motors."""

    def __init__(self, sim, i, cfg, rng, mode, pocket, anchors_use, noise_scale=1.0, opts=None):
        self.sim, self.i, self.cfg, self.rng = sim, i, cfg, rng
        self.opts = opts or {}
        b = cfg["bee35"]
        ma = cfg["mothership_airframe"]
        self.mode = mode
        self.pocket = pocket
        self.px = cfg["dock"]["pocket_x_m"][pocket]
        self.uwb = UWB(cfg, ma["anchors_m"], anchors_use, rng, noise_scale)
        self.rng_s = DownRange(cfg, rng)
        self.flow_s = Flow(cfg, rng)
        self.bc = Broadcast(cfg, rng)
        self.att = Attitude(cfg, rng)
        bid = sim.body_id[i]
        m = float(sim.model.body_mass[bid])
        self.fc = FlightControl(cfg, m, sim.model.body_inertia[bid])
        self.nav = RelNav(b["controller"]["accel_process_sigma_m_s2"])
        self.tag_B = np.array(b["tag_pos_m"])
        self.down_B = np.array(b["down_sensor_pos_m"])
        self.uwb_sigma_eff = math.sqrt(self.uwb.sigma ** 2 + (cfg["dock_sensors"]["uwb"]["bias_max_m"] * noise_scale) ** 2 / 3)
        rs = cfg["dock_sensors"]["range"]
        self.rng_sigma = rs["sigma_per_acc_nd"] * rs["acc_near_m"]
        self.flow_sigma = cfg["dock_sensors"]["flow"]["sigma_m_s"]
        self.rec = cfg["dock_ops"]["recovery"]
        self.lau = cfg["dock_ops"]["launch"]
        self.psi = 0.0
        self.a_dock = np.zeros(3)
        self._bc_prev = None
        self.acc_sum = np.zeros(3)
        self.acc_n = 0
        self.k = 0
        self.nav_every = 5
        self.dt_nav = self.nav_every * sim.dt
        self.state = "INIT"
        self.t_state = 0.0
        self.aborts = 0
        self.events = []
        self.last_range = math.nan
        self.range_low_count = 0
        self.motors_off = False
        self.t_motors_off = None
        self.r_ref = None
        self.v_ref = np.zeros(3)
        self.freeze = ()
        self.push = False
        self.push_iz = 0.0
        self.settle_timer = 0.0
        self.travel0 = 0.0
        self.stop_timer = 0.0
        self.catch_seen = False
        self.liftoff_t = None
        self.R_LB = np.eye(3)
        self.R_WB = np.eye(3)
        self.pend_uwb = []
        self.pend_rng = None
        self.pend_flow = None
        self.h_rej = 0
        self.h_resets = 0
        self.diag_hook = None
        self.comply_x = self.opts.get("comply_x", True)
        self.stop_window_s = self.opts.get("stop_window_s", 0.3)
        self.flow_lpf_s = self.opts.get("flow_lpf_s", 0.25)
        self.v_flow = np.zeros(3)
        self.flow_age = 1e9
        self.stop_t = 0.0
        self.z_hist = deque()
        self.xz_hist = deque()
        self.kp_comply = self.opts.get("kp_comply", 2.5)
        self.kv_comply = self.opts.get("kv_comply", 6.0)
        self.ix_hold = 0.0
        self.leash_m = self.opts.get("leash_m", 0.01)
        # x stays stiff until the bracket mouth can reach the post (mouth tip at -0.165 meets the post at
        # drone z ~ +0.06, i.e. ~0.12 m of travel from the hold z): limits gust drift before contact
        self.comply_after_m = self.opts.get("comply_after_m", 0.10)
        self.complying = False
        self.abrupt_catch = self.opts.get("abrupt_catch", False)   # tested: rejects slow real catches
        self.search_x = self.opts.get("search_x", True)
        self.x_offset = 0.0
        self.n_search = 0
        self.moving_t = -1e9
        self.yaw_gate_deg = self.opts.get("yaw_gate_deg", 4.0)
        self.yaw_ok_t = 0.0
        self.descending = False

    # ------------------------------------------------------------ helpers
    def _goto(self, s, t):
        self.state = s
        self.t_state = t
        self.events.append((round(t, 3), s))
        if self.diag_hook is not None:
            self.diag_hook(self, s)

    def _map_height(self, r_est):
        """Expected downward range from the estimated sensor position, against the known dock."""
        s = r_est + self.R_LB @ self.down_B + self.R_LB[:, 1] * 0.01
        r, _ = self.sim.ray(s, -self.R_LB[:, 1], self.sim.body_id[self.i])
        return (r - 0.01) if r >= 0 else math.nan

    def _map_height_assoc(self, z, d=0.03):
        """Map range nearest to the measurement among a 3x3 grid within +-d of the estimate: the
        surface below is taken to be whichever nearby dock surface explains the reading best."""
        best, bh = math.inf, math.nan
        for dx in (-d, 0.0, d):
            for dz in (-d, 0.0, d):
                h = self._map_height(self.nav.r + np.array([dx, 0.0, dz]))
                if math.isfinite(h) and abs(h - z) < best:
                    best, bh = abs(h - z), h
        return bh

    def _map_flat(self, hp, d=0.03, tol=0.01):
        """True if the dock below the estimate is flat within tol over +-d in x and z (so a few cm of
        horizontal error cannot turn a well, ridge or lip edge into a height error)."""
        for dx, dz in ((d, 0), (-d, 0), (0, d), (0, -d)):
            h = self._map_height(self.nav.r + np.array([dx, 0.0, dz]))
            if not (math.isfinite(h) and abs(h - hp) < tol):
                return False
        return True

    def _sense_and_estimate(self, t):
        sim, i = self.sim, self.i
        bc = self.bc.step(sim, t)
        if bc is not None and (self._bc_prev is None or bc[3] != self._bc_prev[3]):
            if self._bc_prev is not None:
                dtb = bc[3] - self._bc_prev[3]
                a_w = (bc[1] - self._bc_prev[1]) / max(dtb, 1e-3)
                lp = self.cfg["bee35"]["controller"]["dock_accel_lpf_s"]
                al = 1 - math.exp(-dtb / lp)
                self.a_dock += al * (rot_y(-bc[2]) @ a_w - self.a_dock)
            self._bc_prev = bc
            self.psi = bc[2]
        self.R_WB = self.att.R_W_B(sim, i)
        self.R_LB = rot_y(-self.psi) @ self.R_WB
        self.acc_sum += self.att.accel_rel(sim, i, self.a_dock)
        self.acc_n += 1
        # ranges and flow arrive at their own rates
        self.pend_uwb += self.uwb.sample(sim, i, t)
        true_r = self.rng_s.true_range(sim, i)
        z_rng = self.rng_s.sample(sim, i, t)
        z_flow = self.flow_s.sample(sim, i, t, true_r)
        if z_flow is not None:
            self.pend_flow = z_flow
            vL = self.R_LB[:, [0, 2]] @ z_flow
            a = 1 - math.exp(-self.flow_s.period / self.flow_lpf_s)
            self.v_flow += a * (vL - self.v_flow)
            self.flow_age = 0.0
        else:
            self.flow_age += sim.dt
        if z_rng is not None:
            self.pend_rng = z_rng
            self.last_range = z_rng
            if math.isfinite(z_rng) and z_rng <= self.rec["cutoff_range_m"] + 1e-9:
                self.range_low_count += 1
            else:
                self.range_low_count = 0
        self.k += 1
        if self.k % self.nav_every:
            return False
        a = self.acc_sum / max(self.acc_n, 1)
        self.acc_sum[:] = 0
        self.acc_n = 0
        if self.state != "INIT":
            self.nav.predict(a, self.dt_nav)
        tag_off = self.R_LB @ self.tag_B
        for k, z in self.pend_uwb:
            self.nav.range(self.uwb.A[k], z, tag_off, self.uwb_sigma_eff)
        self.pend_uwb = []
        z_rng, z_flow = self.pend_rng, self.pend_flow
        self.pend_rng = self.pend_flow = None
        if z_rng is not None and math.isfinite(z_rng) and z_rng > self.rec["cutoff_range_m"] + 0.005:
            hp = self._map_height_assoc(z_rng)
            if math.isfinite(hp):
                sig = math.hypot(self.rng_sigma * (1.0 if z_rng < 2.0 else z_rng / 2.0), 0.01)
                if self.nav.height(z_rng, hp, sig, gate=25.0):
                    self.h_rej = 0
                else:
                    # spec 7: over the dock, height comes from the down range. Persistent, moderate
                    # disagreement means the filter's height has drifted (UWB is weak vertically).
                    self.h_rej += 1
                    if self.h_rej >= 3 and abs(z_rng - hp) < 0.08:
                        self.nav.P[1, 1] += (z_rng - hp) ** 2
                        self.nav.height(z_rng, hp, sig, gate=1e9)
                        self.h_rej = 0
                        self.h_resets += 1
        if z_flow is not None:
            self.nav.flow(z_flow, self.R_LB, self.flow_sigma)
        return True

    # ------------------------------------------------------------ main entry, every control tick
    def step(self, t):
        nav_tick = self._sense_and_estimate(t)
        if nav_tick:
            if self.mode == "recovery":
                self._recovery(t)
            elif self.mode == "launch":
                self._launch(t)
            elif self.mode == "hold":
                self._hold(t)
            if not self.motors_off and self.r_ref is not None and self.fc.collective_override is None:
                a = self.fc.accel_cmd(self.r_ref, self.v_ref, self.nav.r, self.nav.v, self.dt_nav,
                                      freeze_axes=self.freeze)
                comply_now = self.state == "APPROACH" and self.comply_x and \
                    (self.travel0 - self.nav.r[2]) > self.comply_after_m
                if comply_now:
                    if not self.complying:                # switch: hold the wind integrator from here on
                        self.complying = True
                        self.ix_hold = float(self.fc.integ[0])
                        self.freeze = (0,)
                    # compliant along x: the fences must be able to slide the drone onto the post (a free drone
                    # pushed with 0.5 N seats from +-0.09 m; a stiff x loop on a few-cm-wrong estimate stalls it).
                    # Weak spring to the estimate, strong damping on the flow velocity, wind integrator held.
                    vx = self.v_flow[0] if self.flow_age < 0.05 else self.nav.v[0]
                    a[0] = self.ix_hold + self.kp_comply * (self.r_ref[0] - self.nav.r[0]) - self.kv_comply * vx
                if self.push:
                    # spec step 10: a sideways push against the post, on top of holding the post position with
                    # the wind integrator running (a fixed push alone loses the post in a port-side gust)
                    a[2] -= self.rec["push_force_n"] / self.fc.m
                self.fc.set_accel(a, self.psi)
        if self.motors_off:
            self.sim.set_motors(self.i, np.zeros(4))
        else:
            self.sim.set_motors(self.i, self.fc.motors(self.R_WB, self.att.rates_B(self.sim, self.i)))

    # ------------------------------------------------------------ hold at a point (checks)
    def _hold(self, t):
        if self.state == "INIT":
            p, _ = self.sim.pose(self.i)
            self.nav.init(p + self.rng.normal(0, 0.1, 3), 0.15)
            self.r_ref = self.target.copy()
            self._goto("HOLD", t)

    # ------------------------------------------------------------ recovery (spec 10)
    def _recovery(self, t):
        rec = self.rec
        s = self.state
        xo = self.px + self.x_offset
        hold = np.array([xo, rec["hold_y_m"], rec["hold_z_m"]])
        if s == "INIT":
            p, _ = self.sim.pose(self.i)
            self.nav.init(p + self.rng.normal(0, 0.1, 3), 0.15)
            self.r_ref = self.nav.r.copy()
            self._goto("SETTLE", t)
        elif s == "SETTLE":
            self.r_ref = self.r_ref
            if t - self.t_state > 1.0:
                self._goto("GOTO_HOLD", t)
        elif s == "GOTO_HOLD":
            self._ramp_to(hold, 1.0)
            if np.linalg.norm(self.nav.r - hold) < 0.08 and np.linalg.norm(self.nav.v) < 0.15 \
                    and np.allclose(self.r_ref, hold):
                self._goto("ALIGN", t)
        elif s == "ALIGN":
            self.r_ref = hold.copy()
            if t - self.t_state > 1.0:
                self._goto("DESCEND", t)
        elif s == "DESCEND":
            tgt = np.array([xo, rec["approach_y_m"], rec["hold_z_m"]])
            self.freeze = (1,)                       # keep the hover integrator; do not wind it up on the ramp
            self._ramp_to(tgt, rec["descent_speed_m_s"])
            if self._abort_check(t):
                return
            if np.allclose(self.r_ref, tgt) and abs(self.nav.r[1] - tgt[1]) < 0.03:
                self.freeze = ()
                self.settle_timer = 0.0
                self._goto("HOLD_LOW", t)
        elif s == "HOLD_LOW":
            if self._abort_check(t):
                return
            ok = abs(self.nav.r[1] - rec["approach_y_m"]) < 0.02 and np.linalg.norm(self.nav.v) < 0.06
            self.settle_timer = self.settle_timer + self.dt_nav if ok else 0.0
            if self.settle_timer >= 0.5 or (t - self.t_state > 4.0 and abs(self.nav.r[1] - rec["approach_y_m"]) < 0.05):
                self.travel0 = self.nav.r[2]
                self.stop_timer = 0.0
                self.stop_t = 0.0
                self.complying = False
                self.freeze = ()
                self._goto("APPROACH", t)
        elif s == "APPROACH":
            sp = rec["approach_speed_m_s"]
            self.r_ref = np.array([xo, rec["approach_y_m"], max(self.r_ref[2] - sp * self.dt_nav, self.nav.r[2] - self.leash_m)])
            self.v_ref = np.array([0.0, 0.0, -sp])
            travel = self.travel0 - self.nav.r[2]
            lo, hi = rec["catch_travel_m"]
            h_ok = math.isfinite(self.last_range) and abs(self.last_range - rec["approach_y_m"]) < rec["catch_height_tol_m"]
            # spec step 9: stopped when the sideways speed falls below stop_speed. Measured with the optical
            # flow (velocity relative to the dock, low-passed), not the UWB-fused estimate, whose anchor biases
            # keep moving it while the drone is physically blocked.
            flow_ok = self.flow_age < 0.05
            slow = flow_ok and abs(self.v_flow[2]) < rec["stop_speed_m_s"]
            if flow_ok and -self.v_flow[2] > 0.6 * rec["approach_speed_m_s"]:
                self.moving_t = t                        # last time the drone was moving at approach speed
            self.stop_t = self.stop_t + self.dt_nav if slow else 0.0
            # a post stop is abrupt: moving at approach speed within the last 0.6 s before stopping. A gust
            # that stalls the drone slows it gradually and is not taken for the post.
            abrupt = (t - self.stop_t) - self.moving_t < 0.6
            stopped = self.stop_t >= self.stop_window_s and (abrupt or not self.abrupt_catch)
            if t - self.t_state > (hi / rec["approach_speed_m_s"]) + 3.0:
                self._start_abort(t, "no_catch_timeout")
                return
            if lo < travel < hi and h_ok and stopped:
                # post seated in the slot: the slot fixes x and z
                self.nav.fix(0, self.px, 0.003)
                self.nav.fix(2, 0.0, 0.003)
                self.push = True
                self.push_iz = self.fc.integ[2]
                # x stiff again with the slot as the reference: a net x force at the post would yaw the drone
                # about the post (the gust torque exceeds a small quad's yaw authority)
                self.fc.integ[0] = self.ix_hold
                self.freeze = ()
                self.v_ref = np.zeros(3)
                self._goto("PUSH_DESCEND", t)
                return
            if self._abort_check(t, axes=(2,) if self.comply_x else (0, 2)):
                return
            if travel > rec["max_travel_m"]:
                self._start_abort(t, "no_catch")
        elif s == "PUSH_DESCEND":
            fs = rec["final_descent_speed_m_s"]
            # the ridges only accept the drone within a few degrees of the dock heading, and a post in a slot
            # does not resist yaw: hold height at the post until the heading has recovered from fence contact
            yerr = self._yaw_err_deg()
            self.yaw_ok_t = self.yaw_ok_t + self.dt_nav if abs(yerr) < self.yaw_gate_deg else 0.0
            if self.yaw_ok_t >= 0.3 or self.descending:
                self.descending = True
                self.r_ref = np.array([self.px, max(self.r_ref[1] - fs * self.dt_nav, self.nav.r[1] - 0.03), 0.0])
                self.v_ref = np.array([0.0, -fs, 0.0])
            else:
                self.v_ref = np.zeros(3)
            if self.range_low_count >= rec["cutoff_samples"]:
                self.motors_off = True
                self.t_motors_off = t
                self._goto("MOTORS_OFF", t)
        elif s == "ABORT_OUT":
            tgt = np.array([self.nav.r[0], max(self.nav.r[1], rec["approach_y_m"]), rec["hold_z_m"]])
            self._ramp_to(tgt, 0.2)
            if abs(self.nav.r[2] - tgt[2]) < 0.03:
                self._goto("ABORT_UP", t)
        elif s == "ABORT_UP":
            self.freeze = (1,)
            self._ramp_to(hold, 0.6)
            if np.linalg.norm(self.nav.r - hold) < 0.1:
                self.freeze = ()
                if self.aborts >= rec["max_aborts"]:
                    self._goto("FALLBACK", t)
                else:
                    self._goto("ALIGN", t)
        elif s in ("MOTORS_OFF", "FALLBACK"):
            pass

    def _yaw_err_deg(self):
        """Heading error relative to the dock as the drone sees it (AHRS yaw minus broadcast heading)."""
        R = rot_y(-self.psi) @ self.R_WB
        return math.degrees(math.atan2(-R[2, 0], R[0, 0]))

    def _ramp_to(self, tgt, speed, decel=0.6):
        d = tgt - self.r_ref
        n = float(np.linalg.norm(d))
        speed = min(speed, math.sqrt(2 * decel * n) + 0.02)       # slow the reference into the target
        step = speed * self.dt_nav
        if n <= step:
            self.r_ref = tgt.copy()
            self.v_ref = np.zeros(3)
        else:
            self.r_ref = self.r_ref + d / n * step
            self.v_ref = d / n * speed

    def _abort_check(self, t, axes=(0, 2)):
        rec = self.rec
        if self.nav.r[1] < rec["abort_below_y_m"]:
            e = self.r_ref - self.nav.r
            if math.sqrt(sum(e[a] ** 2 for a in axes)) > rec["abort_err_m"]:
                self._start_abort(t, "pos_err")
                return True
        return False

    def _start_abort(self, t, why):
        self.complying = False
        self.descending = False
        if why in ("no_catch", "no_catch_timeout") and self.search_x:
            # a constant UWB bias makes a plain retry miss the same way: search +-0.08 m along x (the
            # bracket mouth is 0.20 m wide), in the order +, -
            self.x_offset = (0.08, -0.08, 0.0)[min(self.n_search, 2)]
            self.n_search += 1
        self.yaw_ok_t = 0.0
        self.aborts += 1
        self.push = False
        self.freeze = ()
        self.events.append((round(t, 3), f"abort:{why}"))
        self._goto("ABORT_OUT", t)

    # ------------------------------------------------------------ launch (spec 9)
    def _launch(self, t):
        lau = self.lau
        s = self.state
        side = self.opts.get("launch_sidestep", False)
        if s == "INIT":
            # seated: position known from the pocket
            self.nav.init(np.array([self.px, 0.0, 0.0]), 0.003, 0.01)
            self.collective = 0.5
            self.fc.set_level(self.psi, self.collective)
            self.r_ref = None
            self._goto("IDLE", t)
        elif s == "IDLE":
            self.fc.set_level(self.psi, self.collective)
            self.lift_count = 0
            for ax in range(6):
                self.nav.fix(ax, (self.px, 0.0, 0.0, 0.0, 0.0, 0.0)[ax], 0.003)
            if t - self.t_state > 0.5:
                self._goto("RAMP", t)
        elif s == "RAMP":
            self.collective += lau["thrust_ramp_n_s"] * self.dt_nav
            self.fc.set_level(self.psi, self.collective)
            r = self.last_range
            self.lift_count = self.lift_count + 1 if (math.isfinite(r) and r >= 0.05) else 0
            if self.lift_count < 3:
                for ax in range(6):                  # still seated: the pocket fixes position and velocity
                    self.nav.fix(ax, (self.px, 0.0, 0.0, 0.0, 0.0, 0.0)[ax], 0.003)
            else:
                self.liftoff_t = t
                self.fc.collective_override = None
                self.fc.integ[:] = 0.0
                self.r_ref = np.array([self.px, max(self.nav.r[1], 0.0), 0.0])
                self._goto("SIDESTEP_UP" if side else "CLIMB", t)
        elif s == "SIDESTEP_UP":
            self._ramp_to(np.array([self.px, lau["sidestep_y_m"], 0.0]), 0.5)
            if self.nav.r[1] > lau["sidestep_y_m"] - 0.01:
                self._goto("SIDESTEP_OUT", t)
        elif s == "SIDESTEP_OUT":
            self._ramp_to(np.array([self.px, lau["sidestep_y_m"], lau["sidestep_z_m"]]), 0.3)
            if self.nav.r[2] > lau["sidestep_z_m"] - 0.01:
                self._goto("CLIMB", t)
        elif s == "CLIMB":
            tgt = np.array([self.r_ref[0], lau["depart_y_m"], self.r_ref[2]])
            self._ramp_to(tgt, max(lau["climb_speed_m_s"], 1.2))
            if self.nav.r[1] > lau["depart_y_m"] - 0.1:
                self._goto("DEPART", t)
        elif s == "DEPART":
            tgt = np.array([self.px, lau["depart_y_m"], lau["depart_z_m"]])
            self._ramp_to(tgt, 1.0)
            if np.linalg.norm(self.nav.r - tgt) < 0.2:
                self._goto("DONE", t)
