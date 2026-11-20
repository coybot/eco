"""Fixed-wing 6-DOF airframe, sensors and autopilot (reads sensors only).

Physics (FixedWing): RigidBody + FixedWingAero (coefficients with stall) + pusher
Propeller + rate-limited, lagged control-surface servos. Payloads can be attached at a
body offset (mass, inertia by the parallel-axis theorem, the payload's weight moment
about the airframe CG, and its drag) and released mid-flight, so a release changes both
mass and balance and the autopilot has to re-trim. External contact forces can be applied
at body points.

Autopilot (FixedWingAutopilot), successive loop closure after Beard & McLain ch. 6:
  airspeed -> throttle (PI + feed-forward)
  altitude -> pitch command (PID on altitude, climb-rate damping) -> elevator (PD + I)
  path -> course command (straight-line and orbit vector fields, ch. 10) -> bank -> aileron
  rudder drives the lateral specific force to zero (turn coordination)
Its inputs are the pitot airspeed, the AHRS attitude, the gyro and the inertial/GNSS
navigation filter. Gains are config numbers.

Lift rotors of a quadplane are not modelled as actuators: the scenario never leaves
wing-borne cruise. They are present as mass, inertia, drag (in CD0) and geometry.
"""
from __future__ import annotations

import math

import numpy as np

from .actuators import Propeller
from .aero import FixedWingAero
from .frames import GRAVITY_M_S2, euler_from_rot, quat_to_rot, wrap_pi
from .multirotor import MultirotorNav
from .rigid_body import RigidBody
from .sensors import AttitudeSensor, GnssSensor, channel_from_cfg

InertialGnssNav = MultirotorNav    # the filter is vehicle-agnostic


class Servo:
    def __init__(self, limit_rad: float, tau_s: float, rate_rad_s: float):
        self.limit, self.tau, self.rate = limit_rad, tau_s, rate_rad_s
        self.angle = 0.0

    def step(self, cmd_rad: float, dt_s: float) -> float:
        cmd = max(-self.limit, min(self.limit, cmd_rad))
        d = (1.0 - math.exp(-dt_s / self.tau)) * (cmd - self.angle)
        d = max(-self.rate * dt_s, min(self.rate * dt_s, d))
        self.angle += d
        return self.angle


class FixedWing:
    def __init__(self, p: dict, pos_m, vel_m_s, quat, rho_kg_m3: float,
                 mass_scale: float = 1.0, cd0_scale: float = 1.0):
        self.p = p
        a = dict(p["aero"])
        a["cd0_nd"] = a.get("cd0_nd", 0.04) * cd0_scale
        self.aero = FixedWingAero(a)
        self.m0 = p["mass_airframe_kg"] * mass_scale
        self.I0 = np.asarray(p["inertia_kg_m2"], dtype=float) * mass_scale
        self.body = RigidBody(self.m0, self.I0, pos_m, vel_m_s, quat)
        pr = p["pusher"]
        self.prop = Propeller(pr["thrust_static_max_n"], pr["v_zero_thrust_m_s"], pr["tau_s"])
        sv = p["servos"]
        lim, tau, rate = math.radians(sv["limit_deg"]), sv["tau_s"], math.radians(sv["rate_deg_s"])
        self.ail, self.ele, self.rud = Servo(lim, tau, rate), Servo(lim, tau, rate), Servo(lim, tau, rate)
        self.rho = rho_kg_m3
        self.payloads: dict = {}
        self.ext_forces: list = []          # [(force_world_n, point_body_m)] for this step only
        self.last = {}

    # ---- payloads -------------------------------------------------------------------
    def add_payload(self, name: str, mass_kg: float, offset_body_m, cda_m2: float = 0.0) -> None:
        self.payloads[name] = (float(mass_kg), np.asarray(offset_body_m, float), float(cda_m2))
        self._update_mass()

    def remove_payload(self, name: str) -> None:
        self.payloads.pop(name, None)
        self._update_mass()

    def _update_mass(self) -> None:
        m = self.m0
        I = self.I0.copy()
        for mp, r, _ in self.payloads.values():
            m += mp
            I += mp * np.array([r[1] ** 2 + r[2] ** 2, r[0] ** 2 + r[2] ** 2, r[0] ** 2 + r[1] ** 2])
        self.body.set_mass(m, I)

    # ---- step -----------------------------------------------------------------------
    def step(self, ail_cmd_rad: float, ele_cmd_rad: float, rud_cmd_rad: float, throttle_cmd: float,
             wind_world_m_s: np.ndarray, dt_s: float, wind_rate_body_rad_s=None) -> None:
        b = self.body
        R = b.R
        v_air_b = R.T @ (b.vel - wind_world_m_s)
        da, de, dr = self.ail.step(ail_cmd_rad, dt_s), self.ele.step(ele_cmd_rad, dt_s), self.rud.step(rud_cmd_rad, dt_s)
        om_air = b.omega if wind_rate_body_rad_s is None else b.omega - wind_rate_body_rad_s
        F_b, M_b, info = self.aero.forces_moments(v_air_b, om_air, da, de, dr, self.rho)
        T = self.prop.step(throttle_cmd, max(0.0, v_air_b[0]), dt_s)
        F_b = F_b + np.array([T, 0.0, 0.0])
        g_b = R.T @ np.array([0.0, 0.0, -GRAVITY_M_S2])
        for mp, r, cda in self.payloads.values():   # payload weight moment and drag
            M_b = M_b + np.cross(r, mp * g_b)
            if cda > 0:
                sp = math.sqrt(float(v_air_b @ v_air_b))
                fd = -0.5 * self.rho * cda * sp * v_air_b
                F_b = F_b + fd
                M_b = M_b + np.cross(r, fd)
        F_w = R @ F_b
        for fw, rb in self.ext_forces:
            F_w = F_w + fw
            M_b = M_b + np.cross(rb, R.T @ fw)
        self.ext_forces = []
        b.step(F_w, M_b, dt_s)
        info.update(thrust_n=T, aileron_rad=da, elevator_rad=de, rudder_rad=dr)
        self.last = info


class FWSensors:
    def __init__(self, cfg: dict, rng: np.random.Generator, gnss_common=None):
        self.acc = channel_from_cfg(cfg["accel"], 3, rng)
        self.gyro = channel_from_cfg(cfg["gyro"], 3, rng)
        self.ahrs = AttitudeSensor(cfg["ahrs"], rng)
        self.gnss = GnssSensor(cfg["gnss"], rng, gnss_common)
        self.baro = channel_from_cfg(cfg["baro"], 1, rng)
        self.airspeed = channel_from_cfg(cfg["airspeed"], 1, rng)

    def push(self, t_s: float, body: RigidBody, wind_world_m_s: np.ndarray) -> None:
        self.acc.push(t_s, body.specific_force_body)
        self.gyro.push(t_s, body.omega)
        self.ahrs.push(t_s, body.quat)
        self.gnss.push(t_s, body.pos, body.vel)
        self.baro.push(t_s, body.pos[2:3])
        if self.airspeed.due(t_s):
            u = float((body.R.T @ (body.vel - wind_world_m_s))[0])
            self.airspeed.push(t_s, [u])


def line_course(pos_m, origin_m, course_rad, chi_inf_rad, k_path_per_m):
    """Vector-field course command for a straight line (ENU course, ccw from east)."""
    e = -math.sin(course_rad) * (pos_m[0] - origin_m[0]) + math.cos(course_rad) * (pos_m[1] - origin_m[1])
    return course_rad - chi_inf_rad * (2 / math.pi) * math.atan(k_path_per_m * e), e


def orbit_course(pos_m, center_m, radius_m, direction, k_orbit_nd):
    """direction +1 = counter-clockwise seen from above."""
    dx, dy = pos_m[0] - center_m[0], pos_m[1] - center_m[1]
    d = math.hypot(dx, dy)
    ang = math.atan2(dy, dx)
    return ang + direction * (math.pi / 2 + math.atan(k_orbit_nd * (d - radius_m) / radius_m)), d - radius_m


class FixedWingAutopilot:
    def __init__(self, p: dict, sensors: FWSensors, nav: MultirotorNav):
        self.g = dict(p["autopilot"])
        self.s, self.nav = sensors, nav
        self.airspeed_cmd = p["cruise_airspeed_m_s"]
        self.alt_cmd = 50.0
        self.course_cmd = 0.0
        self.bank_ff = 0.0
        self.max_bank = math.radians(self.g["max_bank_deg"])
        self.i_h = self.i_v = self.i_th = self.i_phi = 0.0
        self.throttle = self.g.get("throttle_trim_nd", 0.5)
        self.cmd = (0.0, 0.0, 0.0, self.throttle)
        self.pitch_cmd = self.roll_cmd = 0.0
        self.airspeed_meas = self.airspeed_cmd
        self.stiffness = 1.0      # scales the altitude/airspeed loop gains (docking "soft" mode)
        a = p["aero"]
        self.S, self.cl0, self.cla = a["wing_area_m2"], a.get("cl0_nd", 0.25), a.get("cl_alpha_per_rad", 5.0)
        self.rho = 1.225
        self.mass_est_kg = p["mass_airframe_kg"]   # owners update it when payloads change

    def run(self, t_s: float, dt_s: float):
        g = self.g
        q = self.s.ahrs.read(t_s)
        R = quat_to_rot(q.value) if q is not None else np.eye(3)
        roll, pitch, yaw = euler_from_rot(R)
        gm = self.s.gyro.read(t_s)
        p, q_flu, r_flu = gm.value if gm is not None else (0.0, 0.0, 0.0)
        q_up = -q_flu
        am = self.s.airspeed.read(t_s)
        V = float(am.value[0]) if am is not None else self.airspeed_cmd
        self.airspeed_meas = V
        Vc = max(V, 5.0)
        # longitudinal
        k = self.stiffness
        e_h = self.alt_cmd - self.nav.pos[2]
        climb = self.nav.vel[2]
        lim_i = g["alt_i_limit_m_s"]
        self.i_h = max(-lim_i, min(lim_i, self.i_h + e_h * dt_s))
        th_c = g["pitch_trim_deg"] * math.pi / 180 + k * (g["alt_p_rad_per_m"] * e_h + g["alt_i_rad_per_m_s"] * self.i_h) \
            - k * g["alt_d_rad_per_m_s"] * climb
        lim = math.radians(g["max_pitch_deg"])
        th_c = max(-lim, min(lim, th_c))
        if "alpha_protect_deg" in g and V > 5.0:
            # stall protection from an AERODYNAMIC alpha estimate: lift coefficient implied
            # by the measured normal load factor (accelerometer) and dynamic pressure (pitot),
            # through the nominal lift curve. Responds to gust alpha; blind to steady
            # vertical air motion (a GNSS flight-path estimate is not: it reads a downdraft
            # as alpha and dives with it).
            acc = self.s.acc.read(t_s)
            if acc is not None:
                qS = 0.5 * self.rho * V * V * self.S
                cl_est = self.mass_est_kg * float(acc.value[2]) / qS
                a_est = (cl_est - self.cl0) / self.cla
                self.alpha_est = 0.8 * getattr(self, "alpha_est", a_est) + 0.2 * a_est
                excess = self.alpha_est - math.radians(g["alpha_protect_deg"])
                if excess > 0:
                    th_c = min(th_c, pitch - g.get("alpha_protect_gain_nd", 1.5) * excess)
        self.pitch_cmd = th_c
        e_th = th_c - pitch
        self.i_th = max(-0.3, min(0.3, self.i_th + e_th * dt_s))
        ele = -(g["pitch_p_nd"] * e_th + g["pitch_i_per_s"] * self.i_th - g["pitch_d_s"] * q_up)
        e_v = self.airspeed_cmd - V
        self.i_v = max(-15.0, min(15.0, self.i_v + e_v * dt_s))
        thr = g["throttle_trim_nd"] + k * (g["speed_p_per_m_s"] * e_v + g["speed_i_per_m"] * self.i_v)
        if "underspeed_margin_m_s" in g and e_v > g["underspeed_margin_m_s"]:
            thr = 1.0                                   # underspeed: full power
        # lateral
        vel = self.nav.vel
        chi = math.atan2(vel[1], vel[0])
        phi_c = -g["course_p_rad_per_rad"] * wrap_pi(self.course_cmd - chi) + self.bank_ff
        phi_c = max(-self.max_bank, min(self.max_bank, phi_c))
        self.roll_cmd = phi_c
        e_phi = phi_c - roll
        self.i_phi = max(-0.2, min(0.2, self.i_phi + e_phi * dt_s))
        ail = g["roll_p_nd"] * e_phi + g["roll_i_per_s"] * self.i_phi - g["roll_d_s"] * p
        acc = self.s.acc.read(t_s)
        fy = acc.value[1] if acc is not None else 0.0
        rud = -g["yaw_ay_rad_per_m_s2"] * fy
        self.cmd = (ail, ele, rud, min(1.0, max(0.0, thr)))
        return self.cmd


class FixedWingStack:
    def __init__(self, p: dict, env: dict, rng: np.random.Generator, pos_m, vel_m_s, quat,
                 gnss_common=None, mass_scale: float = 1.0, cd0_scale: float = 1.0):
        self.p = p
        self.dt = env["physics_dt_s"]
        self.guidance_every = int(round(env["guidance_dt_s"] / self.dt))
        self.phys = FixedWing(p, pos_m, vel_m_s, quat, env["air_density_kg_m3"], mass_scale, cd0_scale)
        self.sensors = FWSensors(p["sensors"], rng, gnss_common)
        self.nav = InertialGnssNav(self.sensors, env["guidance_dt_s"], p.get("nav", {}))
        self.ap = FixedWingAutopilot(p, self.sensors, self.nav)
        self.n = 0
        self.outer = None      # callback(stack, t): sets ap.course_cmd / alt_cmd / airspeed_cmd

    @property
    def body(self):
        return self.phys.body

    def step(self, t_s: float, wind_world_m_s: np.ndarray) -> None:
        self.sensors.push(t_s, self.phys.body, wind_world_m_s)
        if self.n % self.guidance_every == 0:
            self.nav.step(t_s)
            if self.nav.kf.initialised and self.outer is not None:
                self.outer(self, t_s)
        a, e, r, thr = self.ap.run(t_s, self.dt) if self.nav.kf.initialised else self.ap.cmd
        self.phys.step(a, e, r, thr, wind_world_m_s, self.dt)
        self.n += 1
