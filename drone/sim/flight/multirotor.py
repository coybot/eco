"""Multirotor: rigid-body physics, sensor suite, own navigation, cascaded flight controller.

Physics (Multirotor): RigidBody + MotorBank (lag, saturation) + mixer wrench + body drag
against the local air velocity (wind included) + optional external force/moment (contact,
tether). ``thrust_scale`` multiplies rotor thrust for external aerodynamic effects (ground
or ceiling effect) that a caller models.

Controller (MultirotorFC), reading only sensors:
  acceleration demand (world) -> thrust vector, limited in tilt and magnitude with
  vertical priority -> desired attitude (thrust axis + yaw) -> quaternion attitude P ->
  body-rate PID on the gyro -> mixer -> motor commands.
  ``set_attitude`` bypasses the thrust-vector stage (used by manoeuvres that command an
  attitude directly, such as levelling out).

Navigation (MultirotorNav): IMU specific force rotated by the AHRS attitude drives a
DelayedKF; GNSS position and velocity update it at their delivered (late) timestamps.
"""
from __future__ import annotations

import math

import numpy as np

from .actuators import MotorBank, MultirotorMixer
from .aero import body_drag_body
from .estimation import DelayedKF
from .frames import GRAVITY_M_S2, quat_conj, quat_mul, quat_to_rotvec, quat_to_rot, rot_to_quat
from .rigid_body import RigidBody
from .sensors import AttitudeSensor, GnssSensor, SensorChannel, channel_from_cfg


def quad_params(cfg: dict) -> dict:
    """Derived quantities from a unit-suffixed config block."""
    p = dict(cfg)
    m_ref = p.get("thrust_to_weight_reference_mass_kg", p["mass_empty_kg"])
    p["thrust_max_total_n"] = p["thrust_to_weight_nd"] * m_ref * GRAVITY_M_S2
    p["thrust_max_per_motor_n"] = p["thrust_max_total_n"] / 4.0
    return p


class Multirotor:
    def __init__(self, p: dict, mass_kg: float, pos_m, vel_m_s, quat, rho_kg_m3: float,
                 thrust_max_scale: float = 1.0, cda_scale: float = 1.0, tau_scale: float = 1.0):
        a = p["arm_length_m"] / math.sqrt(2.0)
        pos = [(a, -a), (-a, a), (a, a), (-a, -a)]            # X layout: FR, RL, FL, RR (FLU)
        spins = [1, 1, -1, -1]
        self.tmax = p["thrust_max_per_motor_n"] * thrust_max_scale
        k_yaw = p.get("yaw_torque_per_thrust_m", 0.016)
        self.motors = MotorBank(4, self.tmax, p["motor_tau_up_s"] * tau_scale,
                                p["motor_tau_down_s"] * tau_scale, yaw_torque_per_thrust_m=k_yaw,
                                initial_thrust_n=np.full(4, 0.0))
        self.mixer = MultirotorMixer(pos, spins, k_yaw, self.tmax)
        cda = p["drag_area_m2"]
        cda = np.asarray(cda if np.ndim(cda) else [cda, cda, cda], dtype=float) * cda_scale
        self.cda_m2 = cda
        I = np.asarray(p["inertia_kg_m2"], dtype=float) * (mass_kg / p["mass_empty_kg"])
        self.body = RigidBody(mass_kg, I, pos_m, vel_m_s, quat)
        self.rho = rho_kg_m3
        self.thrust_scale = 1.0
        self.ext_force_world_n = np.zeros(3)
        self.ext_moment_body_nm = np.zeros(3)
        self.last_thrust_total_n = 0.0
        self.last_drag_world_n = np.zeros(3)
        self.rot_damping_nm_s2 = p.get("rot_damping_nm_s2", 2e-5)
        self.v_zero_axial = p.get("prop_zero_thrust_axial_speed_m_s", 1e9)
        self.inflow_factor = 1.0
        self.p = p

    def set_mass(self, mass_kg: float) -> None:
        I = np.asarray(self.p["inertia_kg_m2"], dtype=float) * (mass_kg / self.p["mass_empty_kg"])
        self.body.set_mass(mass_kg, I)

    def hover_thrust_per_motor_n(self) -> float:
        return self.body.mass_kg * GRAVITY_M_S2 / 4.0

    def step(self, motor_cmd_n: np.ndarray, wind_world_m_s: np.ndarray, dt_s: float) -> None:
        b = self.body
        R = b.R
        v_air_b = R.T @ (b.vel - wind_world_m_s)
        # Propeller inflow: air flowing axially through the discs (the body moving along
        # its thrust axis relative to the air, e.g. ~V sin(tilt) in fast forward flight)
        # cuts thrust at a given throttle, roughly linearly to zero at v_zero (a prop's
        # T(J) curve near the operating point). The controller does not know this factor;
        # it sees less acceleration and its loops add throttle, up to saturation.
        self.inflow_factor = min(1.05, max(0.2, 1.0 - float(v_air_b[2]) / self.v_zero_axial))
        thrust = self.motors.step(motor_cmd_n, dt_s) * self.thrust_scale * self.inflow_factor
        w = self.mixer.wrench(thrust)               # Fz, Mx, My, Mz (body)
        F_b = body_drag_body(v_air_b, self.cda_m2, self.rho)
        drag_w = R @ F_b
        F_b[2] += w[0]
        self.last_thrust_total_n = float(w[0])
        self.last_drag_world_n = drag_w
        F_w = R @ F_b + self.ext_force_world_n
        # rotational aerodynamic damping (props and guards), quadratic in body rate
        om = b.omega
        M_damp = -self.rot_damping_nm_s2 * np.abs(om) * om
        b.step(F_w, np.array([w[1], w[2], w[3]]) + self.ext_moment_body_nm + M_damp, dt_s)


class QuadSensors:
    def __init__(self, cfg: dict, rng: np.random.Generator, gnss_common=None):
        self.acc = channel_from_cfg(cfg["accel"], 3, rng)
        self.gyro = channel_from_cfg(cfg["gyro"], 3, rng)
        self.ahrs = AttitudeSensor(cfg["ahrs"], rng)
        self.gnss = GnssSensor(cfg["gnss"], rng, gnss_common)
        self.baro = channel_from_cfg(cfg["baro"], 1, rng)

    def push(self, t_s: float, body: RigidBody) -> None:
        self.acc.push(t_s, body.specific_force_body)
        self.gyro.push(t_s, body.omega)
        self.ahrs.push(t_s, body.quat)
        self.gnss.push(t_s, body.pos, body.vel)
        self.baro.push(t_s, body.pos[2:3])


class MultirotorNav:
    """Own position/velocity in the world frame from IMU + AHRS + GNSS."""

    def __init__(self, sensors: QuadSensors, dt_s: float, cfg: dict):
        self.s = sensors
        self.kf = DelayedKF(dt_s, cfg.get("accel_noise_std_m_s2", 1.0), cfg.get("history_s", 0.8))
        self.pos_var = cfg.get("gnss_pos_var_m2", 0.02 ** 2)
        self.vel_var = cfg.get("gnss_vel_var_m2_s2", 0.05 ** 2)
        self.accel_world = np.zeros(3)
        self.R = np.eye(3)
        self.n_reinit = 0

    def step(self, t_s: float) -> None:
        a = self.s.acc.read(t_s)
        q = self.s.ahrs.read(t_s)
        if q is not None:
            self.R = quat_to_rot(q.value)
        f = a.value if a is not None else np.array([0, 0, GRAVITY_M_S2])
        self.accel_world = self.R @ f - np.array([0, 0, GRAVITY_M_S2])
        if not self.kf.initialised:
            gp = self.s.gnss.pos.read(t_s)
            gv = self.s.gnss.vel.read(t_s)
            if gp is not None and gv is not None:
                # the fix is late: extrapolate it to now with its own velocity (at 20 m/s a
                # 0.1 s GNSS delay is 2 m; resetting to the stale fix would bias the filter)
                age = t_s - gp.t_meas_s
                self.kf.reset(t_s, gp.value + gv.value * age, gv.value, 4 * self.pos_var, 4 * self.vel_var)
            return
        self.kf.predict(self.accel_world)
        newp = self.s.gnss.pos.read_new(t_s)
        newv = self.s.gnss.vel.read_new(t_s)
        for m in newp:
            var = self.pos_var if not self.s.gnss.is_float(m.t_meas_s) else self.s.gnss.float_var
            self.kf.update(m.t_meas_s, pos=m.value, pos_var=np.full(3, var))
        for m in newv:
            self.kf.update(m.t_meas_s, vel=m.value, vel_var=np.full(3, self.vel_var))
        if self.kf.needs_reinit() and newp and newv:     # gate kept rejecting: start over
            age = t_s - newp[-1].t_meas_s
            self.kf.reset(t_s, newp[-1].value + newv[-1].value * age, newv[-1].value,
                          4 * self.pos_var, 4 * self.vel_var)
            self.n_reinit += 1

    @property
    def pos(self):
        return self.kf.xp

    @property
    def vel(self):
        return self.kf.xv


class MultirotorFC:
    """Cascaded controller. Gains are per-config; defaults suit a ~1 kg quad."""

    def __init__(self, p: dict, sensors: QuadSensors, mass_est_kg: float, tmax_per_motor_n: float, mixer: MultirotorMixer):
        g = p.get("controller", {})
        self.s = sensors
        self.mass_est_kg = mass_est_kg
        self.mixer = mixer
        self.tmax_total = 4 * tmax_per_motor_n
        self.thrust_margin = g.get("thrust_usable_fraction_nd", 0.92)
        self.max_tilt = math.radians(p.get("max_tilt_deg", 60.0))
        self.k_att = np.array(g.get("att_p_per_s", [8.0, 8.0, 3.0]))
        self.rate_max = np.radians(np.array(g.get("rate_max_deg_s", [300.0, 300.0, 120.0])))
        self.kp_rate = np.array(g.get("rate_p_per_s", [22.0, 22.0, 8.0]))
        self.ki_rate = np.array(g.get("rate_i_per_s2", [40.0, 40.0, 10.0]))
        self.kd_rate = np.array(g.get("rate_d_s", [0.02, 0.02, 0.0]))
        self.dgyro_lpf_hz = g.get("rate_d_lpf_hz", 40.0)
        self.dgyro_f = np.zeros(3)
        self.I = np.asarray(p["inertia_kg_m2"], dtype=float) * (mass_est_kg / p["mass_empty_kg"])
        self.p = p
        self.q_des = np.array([1.0, 0, 0, 0])
        self.collective_n = 0.0
        self.rate_int = np.zeros(3)
        self.last_gyro = np.zeros(3)
        self.thrust_vec_des = np.zeros(3)
        self.tilt_limited = False
        self.thrust_limited = False
        self.motors_off = False

    def set_mass_estimate(self, mass_kg: float) -> None:
        self.mass_est_kg = mass_kg
        self.I = np.asarray(self.p["inertia_kg_m2"], dtype=float) * (mass_kg / self.p["mass_empty_kg"])

    def _R_est(self, t_s):
        q = self.s.ahrs.read(t_s)
        return (quat_to_rot(q.value), q.value) if q is not None else (np.eye(3), np.array([1.0, 0, 0, 0]))

    def set_accel(self, t_s: float, accel_world_m_s2: np.ndarray, yaw_rad: float) -> None:
        """Thrust vector for a desired world acceleration (gravity compensated here)."""
        m = self.mass_est_kg
        F = m * (np.asarray(accel_world_m_s2, float) + np.array([0, 0, GRAVITY_M_S2]))
        Fz = max(F[2], 0.1 * m * GRAVITY_M_S2)
        Fh = F[:2].copy()
        tmax = self.thrust_margin * self.tmax_total
        self.tilt_limited = self.thrust_limited = False
        hmax = Fz * math.tan(self.max_tilt)
        hn = math.hypot(Fh[0], Fh[1])
        if hn > hmax:
            Fh *= hmax / hn
            hn = hmax
            self.tilt_limited = True
        if Fz > tmax:
            Fz, Fh, hn = tmax, Fh * 0.0, 0.0
            self.thrust_limited = True
        elif Fz * Fz + hn * hn > tmax * tmax:           # vertical priority
            hn_new = math.sqrt(tmax * tmax - Fz * Fz)
            Fh *= hn_new / max(hn, 1e-9)
            self.thrust_limited = True
        Fv = np.array([Fh[0], Fh[1], Fz])
        self.thrust_vec_des = Fv
        z = Fv / np.linalg.norm(Fv)
        xc = np.array([math.cos(yaw_rad), math.sin(yaw_rad), 0.0])
        y = np.cross(z, xc)
        y /= np.linalg.norm(y)
        x = np.cross(y, z)
        self.q_des = rot_to_quat(np.column_stack([x, y, z]))
        R, _ = self._R_est(t_s)
        self.collective_n = float(max(0.0, Fv @ R[:, 2]))

    def set_attitude(self, q_des: np.ndarray, collective_n: float) -> None:
        self.q_des = np.asarray(q_des, float)
        self.collective_n = float(collective_n)

    def run(self, t_s: float, dt_s: float) -> np.ndarray:
        if self.motors_off:
            self.rate_int[:] = 0.0
            return np.zeros(4)
        _, q = self._R_est(t_s)
        qe = quat_mul(quat_conj(q), self.q_des)
        e = quat_to_rotvec(qe)
        rate_sp = np.clip(self.k_att * e, -self.rate_max, self.rate_max)
        gm = self.s.gyro.read(t_s)
        gyro = gm.value if gm is not None else np.zeros(3)
        err = rate_sp - gyro
        self.rate_int = np.clip(self.rate_int + err * dt_s, -0.5, 0.5)
        dgyro = (gyro - self.last_gyro) / dt_s
        self.last_gyro = gyro
        a = 1.0 - math.exp(-2 * math.pi * self.dgyro_lpf_hz * dt_s)
        self.dgyro_f += a * (dgyro - self.dgyro_f)
        alpha = self.kp_rate * err + self.ki_rate * self.rate_int - self.kd_rate * self.dgyro_f
        M = self.I * alpha
        return self.mixer.mix(self.collective_n, M)


def velocity_to_accel(v_err_m_s: np.ndarray, kp_per_s: float, accel_ff_m_s2=None, a_max_m_s2: float = 8.0):
    a = kp_per_s * np.asarray(v_err_m_s, float)
    if accel_ff_m_s2 is not None:
        a = a + accel_ff_m_s2
    n = np.linalg.norm(a)
    return a * (a_max_m_s2 / n) if n > a_max_m_s2 else a


class QuadStack:
    """Physics + sensors + navigation + flight controller for one multirotor.

    ``step(t, wind)`` advances one physics step; every ``guidance_every`` steps it first
    runs the navigation filter and the caller's ``outer(stack, t)`` callback, which sets
    the controller demand (fc.set_accel / fc.set_attitude). The callback sees only
    ``stack.nav``, ``stack.sensors`` and anything the caller passes it -- never ``body``.
    """

    def __init__(self, p: dict, env: dict, rng: np.random.Generator, pos_m, vel_m_s, quat,
                 mass_kg: float, gnss_common=None, thrust_max_scale: float = 1.0,
                 cda_scale: float = 1.0, tau_scale: float = 1.0, mass_est_kg: float | None = None):
        self.p = p
        self.dt = env["physics_dt_s"]
        self.guidance_every = int(round(env["guidance_dt_s"] / self.dt))
        self.phys = Multirotor(p, mass_kg, pos_m, vel_m_s, quat, env["air_density_kg_m3"],
                               thrust_max_scale, cda_scale, tau_scale)
        self.sensors = QuadSensors(p["sensors"], rng, gnss_common)
        self.nav = MultirotorNav(self.sensors, env["guidance_dt_s"], p.get("nav", {}))
        self.fc = MultirotorFC(p, self.sensors, mass_est_kg or mass_kg,
                               p["thrust_max_per_motor_n"], self.phys.mixer)
        # start the motors at hover so a mid-air spawn does not drop
        self.phys.motors.thrust_n[:] = self.phys.hover_thrust_per_motor_n()
        self.fc.q_des = np.asarray(quat, float).copy()
        self.n = 0
        self.outer = None

    @property
    def body(self):
        return self.phys.body

    def step(self, t_s: float, wind_world_m_s: np.ndarray) -> None:
        self.sensors.push(t_s, self.phys.body)
        if self.n % self.guidance_every == 0:
            self.nav.step(t_s)
            if not self.nav.kf.initialised:      # no fix yet: hold level, hover thrust
                self.fc.set_attitude(self.fc.q_des, self.fc.mass_est_kg * GRAVITY_M_S2)
            elif self.outer is not None:
                self.outer(self, t_s)
        cmd = self.fc.run(t_s, self.dt)
        self.phys.step(cmd, wind_world_m_s, self.dt)
        self.n += 1
