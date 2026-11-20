"""The delivery hexacopter: flight physics, controller, cage contact, rotor failure,
energy, drink on a line, and its own navigation without satellite positioning.

Physics: flight.multirotor.Multirotor with the hexacopter block (six rotors, PNPNPN).
Cage: a 0.48 m x 0.20 m cylinder; penalty contact (spring-damper + friction) against every
scene primitive, stiffness by material (rigid structure, springy wires, soft foliage and
netting). Rotor failure: the motor's thrust goes to zero; after the ESC-telemetry delay the
controller rebuilds its mixer without it (sliding along the 5-rotor null space to find
non-negative thrusts) and stops commanding heading: PNPNPN can still make zero yaw moment
at hover (sizing check: up to 20 N level with zero moments) but not both signs of yaw
moment, so heading drifts slowly and the rate loop damps what spin it can.

Navigation (HexNav): a 7-state Kalman filter (position, velocity, barometer bias) in the
beacon frame.
Prediction from the IMU specific force rotated by the AHRS attitude. Updates:
  radar Doppler velocity (body -> beacon frame with the AHRS attitude),
  differential barometer (height above the beacon),
  ranging radio to the beacon (range + bearing -> a position with an anisotropic
    covariance: sigma_r along the line of sight, r * sigma_bearing across it),
  long-range radio coarse distance (scalar update),
  scan matching against the map (a position fix, on the way back).
Chi-square gating (99 %) rejects reflections that do not fit; rejected counts are kept.
Measurements are fused when they arrive (their 20-40 ms age is not replayed -- at the
0.5-3 m/s used here that is 1-12 cm).
"""
from __future__ import annotations

import math

import numpy as np

from flight.actuators import MultirotorMixer
from flight.frames import GRAVITY_M_S2, quat_from_euler, quat_to_rot
from flight.multirotor import Multirotor, MultirotorFC, quad_params
from flight.sensors import AttitudeSensor, channel_from_cfg

from . import geom as G

# contact parameters per material: stiffness N/m, damping N s/m, friction
CONTACT = {G.CONCRETE: (3000.0, 40.0, 0.5), G.GLASS: (3000.0, 40.0, 0.3), G.METAL: (3000.0, 40.0, 0.4),
           G.FOLIAGE: (40.0, 15.0, 0.8), G.FABRIC: (400.0, 20.0, 0.6), G.NET: (120.0, 25.0, 0.9),
           G.WIRE_METAL: (400.0, 10.0, 0.3), G.WIRE_NONMETAL: (250.0, 10.0, 0.3), G.PERSON: (600.0, 30.0, 0.6),
           G.WOOD: (800.0, 20.0, 0.6)}


class HexSensors:
    def __init__(self, cfg, rng):
        self.acc = channel_from_cfg(cfg["accel"], 3, rng)
        self.gyro = channel_from_cfg(cfg["gyro"], 3, rng)
        self.ahrs = AttitudeSensor(cfg["ahrs"], rng)

    def push(self, t, body):
        self.acc.push(t, body.specific_force_body)
        self.gyro.push(t, body.omega)
        self.ahrs.push(t, body.quat)


class FailSafeMixer:
    """Wraps the full mixer; after a rotor failure is detected, mixes on the others."""

    def __init__(self, pos, spins, k_yaw, tmax):
        self.pos, self.spins, self.k, self.tmax = pos, spins, k_yaw, tmax
        self.full = MultirotorMixer(pos, spins, k_yaw, tmax)
        self.n = len(spins)
        self.sub = None
        self.alive = list(range(self.n))

    def fail(self, i):
        self.alive = [j for j in range(self.n) if j != i]
        self.sub = MultirotorMixer([self.pos[j] for j in self.alive], [self.spins[j] for j in self.alive],
                                   self.k, self.tmax)
        # 5 rotors, 4 demands: one free direction (null space). The minimum-norm solution often
        # needs a negative thrust; sliding along the null space usually finds a feasible one.
        _, _, Vt = np.linalg.svd(self.sub.A)
        self.null = Vt[-1]

    def _slide(self, T0):
        lo, hi = -np.inf, np.inf
        for t0, nv in zip(T0, self.null):
            if abs(nv) < 1e-12:
                if t0 < -1e-9 or t0 > self.tmax + 1e-9:
                    return None
                continue
            a, b = (0.0 - t0) / nv, (self.tmax - t0) / nv
            if a > b:
                a, b = b, a
            lo, hi = max(lo, a), min(hi, b)
        if lo > hi:
            return None
        al = min(max(0.0, lo), hi)
        return T0 + al * self.null

    def mix(self, collective, M):
        if self.sub is None:
            return self.full.mix(collective, M)
        T = np.zeros(self.n)
        A_pinv = self.sub.A_pinv
        # 1) full demand (yaw included); 2) zero yaw moment; 3) the standard yaw-dropping mixer
        for yaw in (M[2], 0.0):
            Ts = self._slide(A_pinv @ np.array([collective, M[0], M[1], yaw]))
            if Ts is not None:
                T[self.alive] = Ts
                return T
        # roll/pitch demand too large to keep zero yaw moment: scale it down (bisection) rather
        # than give up yaw -- losing yaw starts a spin that wrecks roll/pitch control anyway
        lo_s, hi_s, best = 0.0, 1.0, None
        for _ in range(12):
            sm = 0.5 * (lo_s + hi_s)
            Ts = self._slide(A_pinv @ np.array([collective, sm * M[0], sm * M[1], 0.0]))
            if Ts is not None:
                best, lo_s = Ts, sm
            else:
                hi_s = sm
        if best is not None:
            T[self.alive] = best
            return T
        T[self.alive] = self.sub.mix(collective, np.array([M[0], M[1], 0.0]))
        return T

    def wrench(self, T):
        return self.full.wrench(T)


class HexVehicle:
    def __init__(self, hcfg, env, rng, pos, yaw_rad, mass_kg):
        p = quad_params(hcfg)
        self.p = p
        self.dt = env["physics_dt_s"]
        q = quat_from_euler(0.0, 0.0, yaw_rad)
        self.phys = Multirotor(p, mass_kg, pos, np.zeros(3), q, env["air_density_kg_m3"])
        ph = self.phys
        self.mixer = FailSafeMixer([tuple(x) for x in ph.rotor_pos_m], ph.spins, p["yaw_torque_per_thrust_m"],
                                   ph.tmax)
        ph.mixer = self.mixer
        self.sensors = HexSensors(p["sensors"], rng)
        self.fc = MultirotorFC(p, self.sensors, mass_kg, p["thrust_max_per_motor_n"], self.mixer)
        ph.motors.thrust_n[:] = ph.hover_thrust_per_motor_n()
        self.fc.q_des = q.copy()
        self.rad = p["cage_radius_m"]
        self.hh = p["cage_half_height_m"]
        self.rho = env["air_density_kg_m3"]
        self.A_rotor = math.pi * (p["prop_diameter_m"] / 2) ** 2
        self.k_pow = 1.0 / math.sqrt(2 * self.rho * self.A_rotor) / p["figure_of_merit_nd"] / \
            p["motor_esc_efficiency_nd"] / (1 - p["cage_loss_frac"])
        self.energy_j = 0.0
        self.yaw_free = False
        self.failed_rotor = None
        self.fail_t = None
        self.contact_force_w = np.zeros(3)
        self._cd = np.zeros(16)
        self._cn = np.zeros((16, 3))
        self._ct = np.zeros(16, np.int64)
        self._ci = np.zeros(16, np.int64)
        self.touching = []              # list of (typ, idx, depth, vn)
        self.cup = None
        self.rotor_fail_detect_s = p["rotor_fail_detect_delay_s"]

    @property
    def body(self):
        return self.phys.body

    def fail_rotor(self, t, i):
        self.phys.motors.healthy_scale[i] = 0.0
        self.failed_rotor = i
        self.fail_t = t

    def set_mass(self, m):
        self.phys.set_mass(m)
        self.fc.set_mass_estimate(m)

    def contacts(self, scene):
        b = self.body
        k = G.cage_contacts(b.pos, b.R, self.rad, self.hh, scene.blo, scene.bhi, scene.bact, scene.c0, scene.c1,
                            scene.crad, scene.cact, scene.sc, scene.sr, scene.sact, self._cd, self._cn, self._ct, self._ci)
        F = np.zeros(3)
        M = np.zeros(3)
        self.touching = []
        for q in range(k):
            typ, idx = self._ct[q], self._ci[q]
            mat = (scene.bmat, scene.cmat, scene.smat)[typ][idx]
            kk, cc, mu = CONTACT[int(mat)]
            n = self._cn[q]
            depth = min(self._cd[q], 0.05)
            # contact point: cage surface along -n
            nb = b.R.T @ n
            hxy = math.hypot(nb[0], nb[1])
            r_b = np.array([-self.rad * nb[0] / hxy if hxy > 1e-6 else 0.0,
                            -self.rad * nb[1] / hxy if hxy > 1e-6 else 0.0, -self.hh * math.copysign(1.0, nb[2])])
            v = b.vel + b.R @ np.cross(b.omega, r_b)
            vn = float(v @ n)
            fn = kk * depth - cc * vn
            if fn <= 0:
                self.touching.append((int(typ), int(idx), float(depth), vn))
                continue
            vt = v - vn * n
            st = float(np.linalg.norm(vt))
            f = fn * n - mu * fn * vt / max(st, 0.05)
            F += f
            M += np.cross(r_b, b.R.T @ f)
            self.touching.append((int(typ), int(idx), float(depth), vn))
        return F, M

    def step(self, t, wind, scene, extra_force_w=None):
        ph = self.phys
        self.sensors.push(t, ph.body)
        if self.failed_rotor is not None and not self.yaw_free and t - self.fail_t >= self.rotor_fail_detect_s:
            self.mixer.fail(self.failed_rotor)
            self.fc.tmax_total = 5 * ph.tmax
            self.yaw_free = True               # heading no longer commanded; the yaw-rate loop still damps spin
            self.fc.rate_int[:] = 0.0
        F, M = self.contacts(scene)
        self.contact_force_w = F
        ph.ext_force_world_n = F + (extra_force_w if extra_force_w is not None else 0.0)
        ph.ext_moment_body_nm = M
        cmd = self.fc.run(t, self.dt)
        ph.step(cmd, wind, self.dt)
        T = ph.motors.thrust_n * ph.motors.healthy_scale
        self.energy_j += (self.k_pow * float(np.sum(np.maximum(T, 0.0) ** 1.5)) + self.p["avionics_power_w"]) * self.dt


class Cup:
    """The drink on a line below the hexacopter: point mass, inextensible-ish line
    (stiff spring when taut), drag in the wind, contact with the balcony floor/railing."""

    def __init__(self, mass_kg, cda_m2, rho, anchor):
        self.m = mass_kg
        self.cda = cda_m2
        self.rho = rho
        self.pos = np.asarray(anchor, float) - np.array([0, 0, 0.05])
        self.vel = np.zeros(3)
        self.L = 0.05
        self.on_ground = False
        self.released = False
        self.max_swing_m = 0.0
        self.hit_railing = False

    def step(self, anchor, anchor_vel, wind, dt, floor_fn):
        """floor_fn(p) -> height of the surface under p, or None. Returns the force on the hexacopter."""
        if self.released:
            return np.zeros(3)
        d = self.pos - anchor
        dist = float(np.linalg.norm(d))
        T = np.zeros(3)
        if dist > self.L:
            n = d / dist
            vrel = float((self.vel - anchor_vel) @ n)
            ten = 3000.0 * (dist - self.L) + 20.0 * vrel
            if ten > 0:
                T = -ten * n                           # on the cup
        va = self.vel - wind
        drag = -0.5 * self.rho * self.cda * float(np.linalg.norm(va)) * va
        F = T + drag + np.array([0, 0, -self.m * GRAVITY_M_S2])
        self.vel = self.vel + F / self.m * dt
        self.pos = self.pos + self.vel * dt
        fz = floor_fn(self.pos)
        self.on_ground = False
        if fz is not None and self.pos[2] <= fz:
            self.pos[2] = fz
            if self.vel[2] < 0:
                self.vel[2] = 0.0
            self.vel[:2] *= 0.8
            self.on_ground = True
        self.max_swing_m = max(self.max_swing_m, float(np.linalg.norm((self.pos - anchor)[:2])))
        return -T                                       # on the hexacopter


class HexNav:
    def __init__(self, p0, sigma_p0, rcfg_radar, baro_cfg):
        self.x = np.zeros(7)                           # position, velocity, barometer bias
        self.x[:3] = p0
        self.P = np.diag([sigma_p0 ** 2] * 3 + [0.1 ** 2] * 3 + [baro_cfg["offset_std_m"] ** 2])
        self.q_bias = baro_cfg["drift_rw_m_per_sqrt_s"]
        self.qa = 0.6                                  # m/s^2 process noise (vibration, unmodelled aero)
        self.R_att = np.eye(3)
        self.n_rej = {"ranging": 0, "lora": 0, "match": 0, "vel": 0, "baro": 0}
        self.n_acc = {"ranging": 0, "lora": 0, "match": 0, "vel": 0, "baro": 0}
        self.sv = rcfg_radar["velocity_noise_std_m_s"]
        self.sb = baro_cfg["noise_std_m"]
        self.use = {"ranging": True, "lora": True, "match": True, "vel": True, "baro": True}

    @property
    def pos(self):
        return self.x[:3]

    @property
    def vel(self):
        return self.x[3:6]

    def predict(self, a_world, dt):
        F = np.eye(7)
        F[:3, 3:6] = np.eye(3) * dt
        self.x[:3] += self.x[3:6] * dt + 0.5 * a_world * dt * dt
        self.x[3:6] += a_world * dt
        G_ = np.vstack([np.eye(3) * 0.5 * dt * dt, np.eye(3) * dt, np.zeros((1, 3))])
        self.P = F @ self.P @ F.T + G_ @ G_.T * self.qa ** 2
        self.P[6, 6] += self.q_bias ** 2 * dt

    def _update(self, H, z, R, kind, gate=11.34):
        if not self.use[kind]:
            return False
        y = z - H @ self.x
        S = H @ self.P @ H.T + R
        try:
            Si = np.linalg.inv(S)
        except np.linalg.LinAlgError:
            return False
        d2 = float(y @ Si @ y)
        if d2 > gate:
            self.n_rej[kind] += 1
            return False
        K = self.P @ H.T @ Si
        self.x = self.x + K @ y
        self.P = (np.eye(7) - K @ H) @ self.P
        self.P = 0.5 * (self.P + self.P.T)
        self.n_acc[kind] += 1
        return True

    def vel_update(self, v_world, sig):
        H = np.hstack([np.zeros((3, 3)), np.eye(3), np.zeros((3, 1))])
        return self._update(H, v_world, np.eye(3) * sig ** 2 + np.eye(3) * 0.02 ** 2, "vel", gate=16.3)

    def baro_update(self, z, sig):
        H = np.zeros((1, 7))
        H[0, 2] = 1.0
        H[0, 6] = 1.0                                  # baro reads height + bias
        return self._update(H, np.array([z]), np.array([[sig ** 2]]), "baro", gate=9.0)

    def ranging_update(self, beacon, d_world, r, sr, sb):
        z = beacon - d_world
        u = d_world / max(np.linalg.norm(d_world), 1e-6)
        sx = max(r * sb, 0.05) ** 2 + (r * math.radians(3.0)) ** 2        # bearing + attitude error
        R = sx * (np.eye(3) - np.outer(u, u)) + sr ** 2 * np.outer(u, u)
        H = np.hstack([np.eye(3), np.zeros((3, 4))])
        return self._update(H, z, R, "ranging")

    def range_update(self, beacon, r, s):
        d = self.x[:3] - beacon
        rr = float(np.linalg.norm(d))
        if rr < 1e-3:
            return False
        H = np.zeros((1, 7))
        H[0, :3] = d / rr
        z = np.array([r])
        y = z - rr
        S = (H @ self.P @ H.T).item() + s * s
        if not self.use["lora"]:
            return False
        if y[0] ** 2 / S > 9.0:
            self.n_rej["lora"] += 1
            return False
        K = (self.P @ H.T) / S
        self.x = self.x + (K * y).ravel()
        self.P = (np.eye(7) - K @ H) @ self.P
        self.n_acc["lora"] += 1
        return True

    def z_update(self, z, sig):
        """Height from the down rangefinder over a surface of known height (the balcony floor)."""
        H = np.zeros((1, 7))
        H[0, 2] = 1.0
        return self._update(H, np.array([z]), np.array([[sig ** 2]]), "baro", gate=9.0)

    def pos_update(self, p, sig, kind="match"):
        H = np.hstack([np.eye(3), np.zeros((3, 4))])
        return self._update(H, p, np.eye(3) * sig ** 2, kind)
