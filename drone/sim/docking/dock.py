"""Dock mechanics: capture funnel (cone), probe on the quadcopter, latch.

Geometry, in the mothership body frame (FLU):
  throat centre c_t, unit axis n pointing OUT of the mouth, throat radius r_t, mouth
  radius R_m, cone half-angle beta, depth H = (R_m - r_t) / tan(beta).
  At axial coordinate s (0 at the throat, H at the mouth) the inner wall radius is
  R(s) = r_t + s tan(beta).
Probe: a rod from the quad's CG along a body direction (``probe_dir_body``) of length L,
ending in a ball of radius r_tip. The probe tip is what the funnel guides.

Contact (penalty, flight.contact.penalty_force):
  * tip inside the cone (s in [0, H], radial < R(s)) touching the inner wall: pushed
    toward the axis and out of the mouth (the wall normal), with Coulomb friction, so a
    closing tip slides down to the throat;
  * tip at the throat floor (s < 0): a stiff stop;
  * tip outside the cone but within the shell's reach: pushed outward (a rim/outer-wall
    hit, logged).
  Compliance: the probe mount is a spring in series with the wall (``probe_stiffness_n_m``),
  so a compliant probe lowers contact stiffness and the torque kick on the quad.
Latch: engages when the tip is seated (s < s_latch, radial < r_t) and the axial contact
force exceeds ``latch_force_n`` (the detent a real spring latch needs). A seat force above
``overload_force_n`` is logged as a structural overload (counted as a failed attempt).

Forces act on the quad at the tip (force + moment about its CG) and, equal and opposite,
on the mothership at the contact point.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from flight.contact import penalty_force


@dataclass
class DockDesign:
    label: str = "pitched_under"
    location: str = "under"              # "under" (below the fuselage) or "top"
    throat_body_m: tuple = (0.30, 0.0, -0.39)   # throat centre in the mothership frame (6 cm pylon below the rail)
    axis_pitch_deg: float = 48.0         # funnel axis tilt from straight down (under) / up (top),
                                         # toward the tail: matches a quad pitched nose-down by it
    mouth_radius_m: float = 0.20
    throat_radius_m: float = 0.018
    half_angle_deg: float = 40.0
    probe_length_m: float = 0.42         # quad CG to tip centre
    probe_mount_deg: float = 0.0         # probe direction tilted from body +z toward body +x
    tip_radius_m: float = 0.012
    wall_stiffness_n_m: float = 6000.0
    wall_damping_n_s_m: float = 30.0
    friction_nd: float = 0.25
    probe_stiffness_n_m: float = 0.0     # 0 = rigid probe; else series spring
    gimbal: bool = True                  # probe on a 2-axis gimbal at the quad CG
    gimbal_spring_nm_per_rad: float = 0.15
    gimbal_damping_nms_per_rad: float = 0.03
    gimbal_max_deg: float = 25.0         # travel stop (beyond it the probe is rigid)
    seat_stiffness_n_m: float = 1500.0   # sprung seat / latch carriage (compliance at the throat)
    seat_damping_n_s_m: float = 50.0
    latch_force_n: float = 3.0
    overload_force_n: float = 80.0
    s_latch_m: float = 0.004
    level_out: bool = False              # quad levels out over the last level_out_dist_m
    level_out_dist_m: float = 0.45
    approach: str = "axis"               # "axis" (along the funnel axis) or "vertical"
    extra: dict = field(default_factory=dict)

    def axis_body(self) -> np.ndarray:
        """Unit axis out of the mouth, mothership frame."""
        a = math.radians(self.axis_pitch_deg)
        if self.location == "under":
            return np.array([-math.sin(a), 0.0, -math.cos(a)])   # mouth faces down and aft
        return np.array([math.sin(a), 0.0, math.cos(a)])         # top: faces up and forward

    def depth_m(self) -> float:
        return (self.mouth_radius_m - self.throat_radius_m) / math.tan(math.radians(self.half_angle_deg))

    def mouth_body_m(self) -> np.ndarray:
        return np.asarray(self.throat_body_m, float) + self.depth_m() * self.axis_body()

    def probe_dir_body(self) -> np.ndarray:
        a = math.radians(self.probe_mount_deg)
        d = np.array([math.sin(a), 0.0, math.cos(a)])
        return d if self.location == "under" else -d            # top dock: probe points down

    def docked_cg_body_m(self, quad_R_rel: np.ndarray) -> np.ndarray:
        """Quad CG in the mothership frame when seated, given the quad attitude relative
        to the mothership."""
        return np.asarray(self.throat_body_m, float) - self.probe_length_m * (quad_R_rel @ self.probe_dir_body())

    def mass_kg(self) -> float:
        """Funnel mass estimate: thin carbon cone, 1.2 kg/m^2 of wall, plus latch 0.06 kg."""
        b = math.radians(self.half_angle_deg)
        slant = self.depth_m() / math.cos(b)
        area = math.pi * (self.mouth_radius_m + self.throat_radius_m) * slant
        return 1.2 * area + 0.06


class DockContact:
    """Evaluates funnel/probe contact for one quad and one dock each physics step."""

    def __init__(self, d: DockDesign):
        self.d = d
        self.ct = np.asarray(d.throat_body_m, float)
        self.n = d.axis_body()
        self.tanb = math.tan(math.radians(d.half_angle_deg))
        self.cosb = math.cos(math.radians(d.half_angle_deg))
        self.sinb = math.sin(math.radians(d.half_angle_deg))
        self.H = d.depth_m()
        k_w, k_p = d.wall_stiffness_n_m, d.probe_stiffness_n_m
        self.k = k_w if k_p <= 0 else 1.0 / (1.0 / k_w + 1.0 / k_p)
        self.inside = False
        self.armed = False
        self.theta = np.zeros(3)    # gimbal deflection (rotation vector, body frame)
        self.on_stop_s = 0.0
        self.max_depth_m = 0.02     # deeper penetration is not physical: cap the penalty force
        self.peak_force_n = 0.0
        self.rim_hits = 0
        self.entered = False
        self.latched = False
        self.overload = False
        self.seat_force_n = 0.0
        self.contact_time_s = 0.0

    def tip_state(self, quad_body, ms_body, theta=None):
        d = self.d
        pd = self._probe_dir(theta)
        r_tip_q = d.probe_length_m * pd
        tip_w = quad_body.pos + quad_body.R @ r_tip_q
        v_tip_w = quad_body.vel + quad_body.R @ np.cross(quad_body.omega, r_tip_q)
        Rm = ms_body.R
        p_rel = Rm.T @ (tip_w - ms_body.pos)
        v_ms_at = ms_body.vel + Rm @ np.cross(ms_body.omega, p_rel)
        v_rel = Rm.T @ (v_tip_w - v_ms_at)
        return tip_w, p_rel, v_rel, r_tip_q

    def _probe_dir(self, theta=None):
        d0 = self.d.probe_dir_body()
        th = self.theta if theta is None else theta
        a = float(np.linalg.norm(th))
        if a < 1e-12:
            return d0
        k = th / a
        return d0 * math.cos(a) + np.cross(k, d0) * math.sin(a) + k * float(k @ d0) * (1 - math.cos(a))

    def step(self, quad_body, ms_body, dt_s):
        """Returns (force_world_on_quad, moment_body_on_quad, force_world_on_ms, point_ms_body, s, rho).

        Rigid probe: the tip force acts at the tip, so the quad feels F and r_tip x F.
        Gimballed probe (massless, pivot at the CG): integrate the probe angle on sub-steps,
        c dtheta/dt = tau_perp - k theta (stop at gimbal_max_deg); the quad feels F through the
        pivot (no moment) plus only the gimbal's spring/damper torque."""
        d = self.d
        if not d.gimbal:
            return self._contact(quad_body, ms_body, dt_s)
        n_sub = 8
        h = dt_s / n_sub
        d0 = d.probe_dir_body()
        F_acc = np.zeros(3)
        M_acc = np.zeros(3)
        out = None
        thmax = math.radians(d.gimbal_max_deg)
        for _ in range(n_sub):
            out = self._contact(quad_body, ms_body, h)
            F_w = out[0]
            r_tip = d.probe_length_m * self._probe_dir()
            tau = np.cross(r_tip, quad_body.R.T @ F_w)
            tau_perp = tau - float(tau @ d0) * d0
            th_dot = (tau_perp - d.gimbal_spring_nm_per_rad * self.theta) / d.gimbal_damping_nms_per_rad
            th_new = self.theta + th_dot * h
            a = float(np.linalg.norm(th_new))
            M_joint = d.gimbal_spring_nm_per_rad * self.theta + d.gimbal_damping_nms_per_rad * th_dot
            if a > thmax:                      # on the stop: rigid beyond it
                th_new *= thmax / a
                M_joint = tau_perp
                self.on_stop_s += h
            self.theta = th_new
            F_acc += F_w
            M_acc += M_joint + float(tau @ d0) * d0 * 0.0
        F_w = F_acc / n_sub
        M_q = M_acc / n_sub
        return F_w, M_q, -F_w, out[3], out[4], out[5]

    def _contact(self, quad_body, ms_body, dt_s):
        d = self.d
        tip_w, p, v, r_tip_q = self.tip_state(quad_body, ms_body)
        rel = p - self.ct
        s = float(rel @ self.n)
        radial_v = rel - s * self.n
        rho = float(np.sqrt(radial_v @ radial_v))
        rhat = radial_v / rho if rho > 1e-9 else np.array([0.0, 1.0, 0.0])
        F_ms = np.zeros(3)      # force on the TIP, mothership frame
        rt = d.tip_radius_m
        dmax = self.max_depth_m
        # inside / outside bookkeeping: entering through the mouth plane within the mouth
        if s > self.H and rho < d.mouth_radius_m:
            self.armed = True
        elif s > self.H + 0.05 or rho > d.mouth_radius_m + 0.05:
            self.armed = False
        if self.armed and s <= self.H and rho < d.mouth_radius_m:
            if not self.inside:
                self.entered = True
            self.inside = True
        if s > self.H + rt or rho > d.mouth_radius_m + rt + 0.01:
            self.inside = False
        Rs = d.throat_radius_m + max(0.0, min(s, self.H)) * self.tanb
        if 0.0 <= s <= self.H:
            dist = (Rs - rho) * self.cosb            # > 0 inside the cone surface
            if self.inside:
                depth = rt - dist
                if depth > 0:
                    n_in = -rhat * self.cosb + self.n * self.sinb
                    F_ms += penalty_force(min(depth, dmax), n_in, v, self.k, d.wall_damping_n_s_m, d.friction_nd)
            else:
                depth = rt + dist                    # outside: dist < 0 is the gap to the shell
                if depth > 0 and dist < 0:
                    n_out = rhat * self.cosb - self.n * self.sinb
                    F_ms += penalty_force(min(depth, dmax), n_out, v, self.k, d.wall_damping_n_s_m, d.friction_nd)
                    self.rim_hits += 1
        # rim: a torus of radius 0 (an edge) along the mouth circle
        q = self.H * self.n + rhat * d.mouth_radius_m        # nearest rim point (relative to throat)
        dv = rel - q
        dd = float(np.sqrt(dv @ dv))
        if dd < rt and dd > 1e-9:
            F_ms += penalty_force(min(rt - dd, dmax), dv / dd, v, self.k, d.wall_damping_n_s_m, d.friction_nd)
            if not self.inside:
                self.rim_hits += 1
        # throat stop
        if s < 0.0 and rho < d.throat_radius_m + rt:
            F_ms += penalty_force(min(-s, 0.03), self.n, v, d.seat_stiffness_n_m, d.seat_damping_n_s_m, d.friction_nd)
            if -s > 0.03:                      # sprung seat bottomed out: hard stop behind it
                F_ms += penalty_force(min(-s - 0.03, dmax), self.n, v, self.k * 3, d.wall_damping_n_s_m * 2, 0.0)
        if self.inside and s < 0.0:
            self.depth_flag = max(getattr(self, "depth_flag", 0.0), -s)
        fmag = float(np.linalg.norm(F_ms))
        if fmag > 0:
            self.contact_time_s += dt_s
        self.peak_force_n = max(self.peak_force_n, fmag)
        axial = float(F_ms @ self.n)
        if s < d.s_latch_m and rho < d.throat_radius_m and self.inside:
            self.seat_force_n = max(self.seat_force_n, axial)
            if axial > d.latch_force_n:
                self.latched = True
            if axial > d.overload_force_n:
                self.overload = True
        F_w = ms_body.R @ F_ms
        M_q = np.cross(r_tip_q, quad_body.R.T @ F_w)
        return F_w, M_q, -F_w, p, s, rho
