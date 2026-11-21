"""Drone-side sensors for the dock sim (spec section 6). Each returns measurements built from
DockSim truth plus the spec's error models; the estimator and controller see only these.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from .motion import cross, rot_y
from .sim import GROUP_UWB


class UWB:
    """One range per anchor per cycle. Gaussian noise, per-anchor bias, dropout, NLOS bias."""

    def __init__(self, cfg, anchors: dict, use: list, rng, noise_scale=1.0, n_in_flight=1):
        u = cfg["dock_sensors"]["uwb"]
        self.names = list(use)
        self.A = np.array([anchors[n] for n in use], float)
        self.sigma = u["sigma_m"] * noise_scale
        self.bias = rng.uniform(-u["bias_max_m"], u["bias_max_m"], len(use)) * noise_scale
        n = len(use)
        self.sig = np.full(n, self.sigma)
        self.bias_max = np.full(n, u["bias_max_m"] * noise_scale)
        self.max_range = np.full(n, math.inf)
        pb = cfg["dock_sensors"].get("pocket_beacons", {})
        if pb.get("enabled", False):                    # one short-range beacon per pocket, at the port rail
            P = [[x, pb["y_m"], pb["z_m"]] for x in cfg["dock"]["pocket_x_m"]]
            k = len(P)
            self.names += [f"PB{j + 1}" for j in range(k)]
            self.A = np.vstack([self.A, np.array(P, float)])
            self.sig = np.concatenate([self.sig, np.full(k, pb["sigma_m"] * noise_scale)])
            self.bias_max = np.concatenate([self.bias_max, np.full(k, pb["bias_max_m"] * noise_scale)])
            self.bias = np.concatenate([self.bias, rng.uniform(-1, 1, k) * pb["bias_max_m"] * noise_scale])
            self.max_range = np.concatenate([self.max_range, np.full(k, pb["max_range_m"])])
        self.p_drop = u["dropout_p"]
        self.nlos = u["blocked_bias_m"]
        self.period = n_in_flight / u["rate_hz"]
        self.rng = rng
        self.t_next = 0.0
        self.n_blocked = 0
        self.n_total = 0
        self.blocked_by = np.zeros(len(self.names), int)

    def sample(self, sim, i, t):
        if t + 1e-9 < self.t_next:
            return []
        self.t_next += self.period
        tag = sim.site_pos(sim.tag_site[i])
        out = []
        for k, a in enumerate(self.A):
            if self.rng.random() < self.p_drop:
                continue
            dv = a - tag
            r = float(np.linalg.norm(dv))
            if r > self.max_range[k]:
                continue
            hit, _ = sim.ray(tag, dv / r, sim.body_id[i], GROUP_UWB)
            z = r + self.bias[k] + self.sig[k] * self.rng.standard_normal()
            self.n_total += 1
            if 0 <= hit < r - 0.02:
                z += self.rng.uniform(*self.nlos)
                self.n_blocked += 1
                self.blocked_by[k] += 1
            out.append((k, z))
        return out


class DownRange:
    """Downward range finder (centre ray plus four rays at half the beam angle; nearest wins)."""

    def __init__(self, cfg, rng):
        r = cfg["dock_sensors"]["range"]
        self.cfg = r
        self.period = 1.0 / r["rate_hz"]
        self.t_next = 0.0
        self.rng = rng
        h = math.radians(r["beam_deg"] / 2)
        self.dirs_B = np.array([[0, -1, 0], [math.sin(h), -math.cos(h), 0], [-math.sin(h), -math.cos(h), 0],
                                [0, -math.cos(h), math.sin(h)], [0, -math.cos(h), -math.sin(h)]])
        self.k_sig = r["sigma_per_acc_nd"]

    def true_range(self, sim, i, lift=0.01):
        p, R = sim.pose(i)
        s = sim.site_pos(sim.down_site[i]) + R[:, 1] * lift
        best = math.inf
        for dB in self.dirs_B:
            u = R @ dB
            r, _ = sim.ray(s, u, sim.body_id[i])
            if r >= 0:
                best = min(best, r - lift)
        return best

    def sample(self, sim, i, t):
        if t + 1e-9 < self.t_next:
            return None
        self.t_next += self.period
        c = self.cfg
        r = self.true_range(sim, i)
        if not math.isfinite(r) or r > c["max_m"]:
            return math.nan
        sig = self.k_sig * (c["acc_near_m"] if r < 2.0 else c["acc_far_frac"] * r)
        return float(np.clip(r + sig * self.rng.standard_normal(), c["min_m"], c["max_m"]))


class Flow:
    """Velocity relative to the surface below, body x/z axes, valid above min_height."""

    def __init__(self, cfg, rng):
        f = cfg["dock_sensors"]["flow"]
        self.sigma = f["sigma_m_s"]
        self.hmin = f["min_height_m"]
        self.period = 1.0 / f["rate_hz"]
        self.t_next = 0.0
        self.rng = rng

    def sample(self, sim, i, t, true_range):
        if t + 1e-9 < self.t_next:
            return None
        self.t_next += self.period
        if not (math.isfinite(true_range) and self.hmin < true_range < 8.0):
            return None
        v, om = sim.vel(i)
        p, R = sim.pose(i)
        s = sim.site_pos(sim.down_site[i])
        vs = v + cross(om, s - p)                      # the surface below is static in D
        vb = R.T @ vs
        return np.array([vb[0], vb[2]]) + self.sigma * self.rng.standard_normal(2)


class Broadcast:
    """Mothership velocity and heading at 10 Hz with delay and Gauss-Markov errors."""

    def __init__(self, cfg, rng):
        b = cfg["dock_sensors"]["broadcast"]
        self.period = 1.0 / b["rate_hz"]
        self.delay = float(rng.uniform(*b["delay_s"]))
        self.vs, self.hs = b["vel_sigma_m_s"], math.radians(b["heading_sigma_deg"])
        self.tau = b["error_tau_s"]
        self.rng = rng
        self.ev = rng.standard_normal(3) * self.vs
        self.eh = rng.standard_normal() * self.hs
        self.q = deque()
        self.t_next = 0.0
        self.latest = None

    def step(self, sim, t):
        if t + 1e-9 >= self.t_next:
            self.t_next += self.period
            a = math.exp(-self.period / self.tau)
            s = math.sqrt(1 - a * a)
            self.ev = a * self.ev + s * self.vs * self.rng.standard_normal(3)
            self.eh = a * self.eh + s * self.hs * self.rng.standard_normal()
            mm = sim.motion
            self.q.append((t + self.delay, mm.vel_W + self.ev, mm.yaw + self.eh, t))
        while self.q and self.q[0][0] <= t:
            self.latest = self.q.popleft()
        return self.latest


class Attitude:
    """AHRS attitude in the world, yaw from the compass (bias + white), tilt noise; gyro rates."""

    def __init__(self, cfg, rng):
        c = cfg["dock_sensors"]["compass"]
        imu = cfg["dock_sensors"]["imu"]
        s = math.radians(c["sigma_deg"])
        self.yaw_bias = rng.standard_normal() * s * math.sqrt(c["bias_frac_nd"])
        self.yaw_white = s * math.sqrt(1 - c["bias_frac_nd"]) * 0.3       # filtered by the gyro in the AHRS
        self.tilt = math.radians(imu["attitude_sigma_deg"])
        self.gyro = imu["gyro_sigma_rad_s"]
        self.acc = imu["accel_sigma_m_s2"]
        self.rng = rng

    def R_W_B(self, sim, i):
        _, R_D_B = sim.pose(i)
        Rw = sim.motion.Rm @ R_D_B
        e = self.rng.standard_normal(3) * np.array([self.tilt, self.yaw_white, self.tilt])
        e[1] += self.yaw_bias
        ex = np.array([[0, -e[2], e[1]], [e[2], 0, -e[0]], [-e[1], e[0], 0]])
        return (np.eye(3) + ex) @ Rw

    def rates_B(self, sim, i):
        _, R_D_B = sim.pose(i)
        _, om = sim.vel(i)
        return R_D_B.T @ (om + sim.motion.omega_D) + self.gyro * self.rng.standard_normal(3)

    def accel_rel(self, sim, i, a_dock_est_D):
        """Relative acceleration as the drone can form it: inertial (IMU) minus its dock estimate."""
        mm = sim.motion
        p, _ = sim.pose(i)
        v, _ = sim.vel(i)
        w, al = mm.omega_D, mm.alpha_D
        a_in = sim.acc_rel[i] + mm.Rm.T @ mm.acc_W + cross(al, p) + cross(w, cross(w, p)) + 2 * cross(w, v)
        return a_in - a_dock_est_D + self.acc * self.rng.standard_normal(3)


def yaw_frame(psi):
    return rot_y(psi)
