"""Actuators: motor lag and saturation, the multirotor mixer, and a propeller.

MotorBank -- N motors, each a first-order lag on thrust with separate spin-up and
    spin-down time constants (an ESC without active braking spins down slower than it
    spins up) and hard saturation at [thrust_min_n, thrust_max_n]. The command is clipped
    before the lag, so a saturated motor really delivers no more than its maximum.
    Each motor's reaction torque is yaw_torque_per_thrust_m * thrust about body z.

MultirotorMixer -- maps [collective, roll, pitch, yaw] demands to per-motor thrust for
    any planar layout. When the demand does not fit inside the motor limits it gives up
    yaw first, then shifts collective, then scales roll/pitch, so attitude control keeps
    priority (what PX4/ArduPilot mixers do in spirit).

Propeller -- thrust that falls linearly with airspeed to zero at v_zero_thrust_m_s,
    driven through its own first-order lag. A placeholder for a measured prop map.
"""
from __future__ import annotations

import math

import numpy as np


class MotorBank:
    def __init__(self, n: int, thrust_max_n: float, tau_up_s: float, tau_down_s: float,
                 thrust_min_n: float = 0.0, yaw_torque_per_thrust_m: float = 0.0,
                 initial_thrust_n=None):
        self.n = n
        self.thrust_max_n = float(thrust_max_n)
        self.thrust_min_n = float(thrust_min_n)
        self.tau_up_s = float(tau_up_s)
        self.tau_down_s = float(tau_down_s)
        self.k_yaw = float(yaw_torque_per_thrust_m)
        self.thrust_n = np.zeros(n) if initial_thrust_n is None else \
            np.asarray(initial_thrust_n, dtype=float).copy()
        self.command_n = self.thrust_n.copy()
        self.saturated = np.zeros(n, dtype=bool)
        self.healthy_scale = np.ones(n)   # fault injection: 0 = dead motor

    def step(self, command_n: np.ndarray, dt_s: float) -> np.ndarray:
        cmd = np.clip(command_n, self.thrust_min_n, self.thrust_max_n)
        self.saturated = (command_n > self.thrust_max_n + 1e-9) | (command_n < self.thrust_min_n - 1e-9)
        self.command_n = cmd
        up = cmd > self.thrust_n
        a_up = 1.0 - math.exp(-dt_s / self.tau_up_s)
        a_dn = 1.0 - math.exp(-dt_s / self.tau_down_s)
        alpha = np.where(up, a_up, a_dn)
        self.thrust_n = self.thrust_n + alpha * (cmd - self.thrust_n)
        return self.thrust_n * self.healthy_scale


class MultirotorMixer:
    """Planar multirotor. Motor i at (x_i, y_i) in body FLU, thrust along +z, spin s_i
    (+1 = counter-clockwise seen from above, reaction torque -k*T about +z)."""

    def __init__(self, positions_m, spins, yaw_torque_per_thrust_m: float, thrust_max_n: float):
        P = np.asarray(positions_m, dtype=float)
        s = np.asarray(spins, dtype=float)
        self.n = len(s)
        # rows: collective Fz, roll Mx, pitch My, yaw Mz (FLU). Mx = y*T, My = -x*T.
        self.A = np.vstack([np.ones(self.n), P[:, 1], -P[:, 0], -s * yaw_torque_per_thrust_m])
        self.A_pinv = np.linalg.pinv(self.A)
        self.thrust_max_n = thrust_max_n

    def mix(self, collective_n: float, moment_body_nm: np.ndarray) -> np.ndarray:
        tmax = self.thrust_max_n
        d = np.array([collective_n, moment_body_nm[0], moment_body_nm[1], moment_body_nm[2]])
        T = self.A_pinv @ d
        if T.min() >= 0.0 and T.max() <= tmax:
            return T
        # 1) drop yaw
        d[3] = 0.0
        T_att = self.A_pinv @ np.array([0.0, d[1], d[2], 0.0])
        span = T_att.max() - T_att.min()
        if span > tmax:                       # 3) roll/pitch alone do not fit: scale them
            T_att *= tmax / span
            span = tmax
        # 2) choose collective (per motor) as close to the demand as the limits allow
        c = collective_n / self.n
        lo, hi = -T_att.min(), tmax - T_att.max()
        c = min(max(c, lo), hi)
        T = T_att + c
        # put back as much yaw as fits
        T_yaw = self.A_pinv @ np.array([0.0, 0.0, 0.0, moment_body_nm[2]])
        k = 1.0
        for i in range(self.n):
            if T_yaw[i] > 1e-12:
                k = min(k, (tmax - T[i]) / T_yaw[i])
            elif T_yaw[i] < -1e-12:
                k = min(k, -T[i] / T_yaw[i])
        return np.clip(T + max(0.0, k) * T_yaw, 0.0, tmax)

    def wrench(self, thrust_n: np.ndarray) -> np.ndarray:
        return self.A @ thrust_n


class Propeller:
    def __init__(self, thrust_static_max_n: float, v_zero_thrust_m_s: float, tau_s: float):
        self.T0 = float(thrust_static_max_n)
        self.v0 = float(v_zero_thrust_m_s)
        self.tau_s = float(tau_s)
        self.throttle = 0.0

    def step(self, throttle_cmd: float, airspeed_m_s: float, dt_s: float) -> float:
        cmd = min(1.0, max(0.0, throttle_cmd))
        self.throttle += (1.0 - math.exp(-dt_s / self.tau_s)) * (cmd - self.throttle)
        return self.throttle * self.max_thrust_n(airspeed_m_s)

    def max_thrust_n(self, airspeed_m_s: float) -> float:
        return self.T0 * max(0.0, 1.0 - max(0.0, airspeed_m_s) / self.v0)
