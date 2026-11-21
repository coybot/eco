"""Mothership hover motion and wind, both seeded and stepped at a fixed rate.

Hover motion (spec 8.2): each axis is white noise through three cascaded first-order lags
of rate 1/tau. Position, velocity and acceleration are all continuous, and the stationary
RMS equals the table value: for n lags driven by white noise of intensity q^2 the output
variance is q^2 w C_n with C_3 = 3/16.

Wind (spec 8.3): mean wind from within +-wind_from_nose_max_deg of the nose (a hovering
quadplane points into wind), plus first-order Gauss-Markov turbulence per axis with
RMS = turbulence_frac x mean and a time scale drawn per run from wind_time_scale_s.
"""
from __future__ import annotations

import math

import numpy as np


def cross(a, b):
    """3-vector cross product (np.cross is ~10x slower on single vectors)."""
    return np.array([a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]])


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


class Lag3:
    """Vector of independent 3-lag filtered noise channels with given stationary RMS."""

    def __init__(self, rms, tau, rng):
        self.rms = np.asarray(rms, float)
        self.w = 1.0 / np.asarray(tau, float)
        self.q = self.rms * np.sqrt(16.0 / (3.0 * self.w))
        self.y = np.zeros((3, len(self.rms)))
        self.rng = rng
        # start from the stationary distribution: burn in for 12 time constants at a coarse step
        dt = 0.02
        for _ in range(int(12 * max(np.atleast_1d(tau)) / dt)):
            self.step(dt)

    def step(self, dt):
        w, y = self.w, self.y
        noise = self.rng.standard_normal(len(self.rms)) * self.q / math.sqrt(dt)
        d0 = w * (noise - y[0])
        d1 = w * (y[0] - y[1])
        d2 = w * (y[1] - y[2])
        y[0] += d0 * dt
        y[1] += d1 * dt
        y[2] += d2 * dt

    @property
    def pos(self):
        return self.y[2]

    @property
    def vel(self):
        return self.w * (self.y[1] - self.y[2])

    @property
    def acc(self):
        w, y = self.w, self.y
        return w * (w * (y[0] - y[1]) - w * (y[1] - y[2]))


class MothershipMotion:
    """Kinematic pose of frame D in the world (world: +y up, nose along +x at zero yaw)."""

    def __init__(self, level: dict, taus: dict, rng, scale=1.0):
        h, v = level["h_rms_m"] * scale, level["v_rms_m"] * scale
        self.lin = Lag3([h, v, h], [taus["tau_pos_s"]] * 3, rng)
        ang = np.radians([level["tilt_rms_deg"], level["yaw_rms_deg"], level["tilt_rms_deg"]]) * scale
        self.ang = Lag3(ang, [taus["tau_tilt_s"], taus["tau_yaw_s"], taus["tau_tilt_s"]], rng)
        self._prev = None
        self.update(0.0)

    def R(self, ang=None):
        r, yw, p = self.ang.pos if ang is None else ang       # roll about x, yaw about y, pitch about z
        return rot_y(yw) @ rot_z(p) @ rot_x(r)

    def update(self, dt):
        if dt > 0:
            self.lin.step(dt)
            self.ang.step(dt)
        a, da = self.ang.pos, self.ang.vel
        dda = self.ang.acc
        R = self.R()
        h = 1e-4
        Rp = self.R(a + da * h)
        Rpp = self.R(a + 2 * da * h + dda * h * h)
        # body (D-frame) angular velocity and acceleration by finite differences of R
        W1 = R.T @ (Rp - R) / h
        W2 = Rp.T @ (Rpp - Rp) / h
        w1 = np.array([W1[2, 1], W1[0, 2], W1[1, 0]])
        w2 = np.array([W2[2, 1], W2[0, 2], W2[1, 0]])
        self.Rm = R
        self.omega_D = w1
        self.alpha_D = (w2 - w1) / h
        self.pos_W = self.lin.pos.copy()
        self.vel_W = self.lin.vel.copy()
        self.acc_W = self.lin.acc.copy()
        self.yaw = float(a[1])


class Wind:
    def __init__(self, mean_m_s, cfg, rng):
        mc, ma = cfg["dock_matrix"], cfg["mothership_airframe"]
        lim = math.radians(ma["wind_from_nose_max_deg"])
        self.dir_from_nose_rad = float(rng.uniform(-lim, lim))
        # air moves from the nose towards the tail: from +x towards -x (rotated about y)
        self.mean = -mean_m_s * np.array([math.cos(self.dir_from_nose_rad), 0.0, -math.sin(self.dir_from_nose_rad)])
        self.sigma = mc["wind_turbulence_frac"] * mean_m_s
        lo, hi = mc["wind_time_scale_s"]
        self.tau = float(rng.uniform(lo, hi))
        self.rng = rng
        self.turb = rng.standard_normal(3) * self.sigma

    def step(self, dt):
        a = math.exp(-dt / self.tau)
        self.turb = a * self.turb + math.sqrt(1 - a * a) * self.sigma * self.rng.standard_normal(3)

    @property
    def vel_W(self):
        return self.mean + self.turb
