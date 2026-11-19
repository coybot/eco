"""Delayed-measurement Kalman filter for position/velocity (own or relative navigation).

Per world axis a 2-state [position, velocity] filter driven by a known acceleration
input (from the IMU, rotated by the AHRS attitude) with white acceleration process noise.
The three axes are filtered independently (diagonal measurement noise), which keeps it
cheap enough to run inside thousands of Monte Carlo trials.

Measurements arrive late (sensor delay, radio latency). Each one carries the time it was
taken; the filter rolls back to the stored state at that time, applies the update, and
replays the stored inputs up to now. Measurements older than the history window are
dropped and counted. Innovations beyond ``gate_sigma`` standard deviations are rejected.
"""
from __future__ import annotations

import bisect
import math
from collections import deque

import numpy as np


class DelayedKF:
    def __init__(self, dt_s: float, accel_noise_std_m_s2: float, history_s: float = 0.8,
                 gate_sigma: float = 6.0):
        self.dt = dt_s
        self.q = accel_noise_std_m_s2 ** 2
        self.maxlen = int(history_s / dt_s) + 2
        self.gate = gate_sigma
        self.t = 0.0
        self.xp = np.zeros(3)
        self.xv = np.zeros(3)
        self.P = np.zeros((3, 2, 2))
        self.hist: deque = deque(maxlen=self.maxlen)   # (t, xp, xv, P, u) at the END of a step
        self.initialised = False
        self.n_late_dropped = 0
        self.n_gated = 0
        self.last_update_s = -1e9

    def reset(self, t_s: float, pos: np.ndarray, vel: np.ndarray, pos_var: float, vel_var: float) -> None:
        self.t = t_s
        self.xp = np.asarray(pos, dtype=float).copy()
        self.xv = np.asarray(vel, dtype=float).copy()
        self.P = np.zeros((3, 2, 2))
        self.P[:, 0, 0] = pos_var
        self.P[:, 1, 1] = vel_var
        self.hist.clear()
        self.hist.append((t_s, self.xp.copy(), self.xv.copy(), self.P.copy(), np.zeros(3)))
        self.initialised = True
        self.last_update_s = t_s

    def _predict_once(self, xp, xv, P, u):
        dt = self.dt
        xp = xp + xv * dt + 0.5 * u * dt * dt
        xv = xv + u * dt
        q = self.q
        p00, p01, p11 = P[:, 0, 0], P[:, 0, 1], P[:, 1, 1]
        n00 = p00 + 2 * dt * p01 + dt * dt * p11 + q * dt ** 4 / 4
        n01 = p01 + dt * p11 + q * dt ** 3 / 2
        n11 = p11 + q * dt * dt
        Pn = np.empty_like(P)
        Pn[:, 0, 0], Pn[:, 0, 1], Pn[:, 1, 0], Pn[:, 1, 1] = n00, n01, n01, n11
        return xp, xv, Pn

    def predict(self, accel_world_m_s2: np.ndarray) -> None:
        u = np.asarray(accel_world_m_s2, dtype=float)
        self.xp, self.xv, self.P = self._predict_once(self.xp, self.xv, self.P, u)
        self.t += self.dt
        self.hist.append((self.t, self.xp.copy(), self.xv.copy(), self.P.copy(), u.copy()))

    @staticmethod
    def _update_axis(xp, xv, P, z, var, which, gate):
        """Scalar-per-axis update; which = 0 (position) or 1 (velocity)."""
        x = xp if which == 0 else xv
        S = P[:, which, which] + var
        innov = z - x
        ok = innov * innov <= gate * gate * S
        K0 = np.where(ok, P[:, 0, which] / S, 0.0)
        K1 = np.where(ok, P[:, 1, which] / S, 0.0)
        xp = xp + K0 * innov
        xv = xv + K1 * innov
        r0 = P[:, which, 0].copy()
        r1 = P[:, which, 1].copy()
        Pn = P.copy()
        Pn[:, 0, 0] -= K0 * r0
        Pn[:, 0, 1] -= K0 * r1
        Pn[:, 1, 0] -= K1 * r0
        Pn[:, 1, 1] -= K1 * r1
        return xp, xv, Pn, int((~ok).sum())

    def update(self, t_meas_s: float, pos=None, pos_var=None, vel=None, vel_var=None) -> bool:
        if not self.initialised:
            return False
        times = [h[0] for h in self.hist]
        if t_meas_s < times[0] - 1e-9:
            self.n_late_dropped += 1
            return False
        k = max(0, bisect.bisect_right(times, t_meas_s + 1e-9) - 1)
        _, xp, xv, P, _ = self.hist[k]
        rejected = 0
        if pos is not None:
            xp, xv, P, r = self._update_axis(xp, xv, P, np.asarray(pos, float), np.asarray(pos_var, float), 0, self.gate)
            rejected += r
        if vel is not None:
            xp, xv, P, r = self._update_axis(xp, xv, P, np.asarray(vel, float), np.asarray(vel_var, float), 1, self.gate)
            rejected += r
        self.n_gated += rejected
        # replay forward, rewriting history
        hist = list(self.hist)
        hist[k] = (hist[k][0], xp.copy(), xv.copy(), P.copy(), hist[k][4])
        for j in range(k + 1, len(hist)):
            xp, xv, P = self._predict_once(xp, xv, P, hist[j][4])
            hist[j] = (hist[j][0], xp.copy(), xv.copy(), P.copy(), hist[j][4])
        self.hist = deque(hist, maxlen=self.maxlen)
        self.xp, self.xv, self.P = xp, xv, P
        self.last_update_s = t_meas_s
        return True

    def pos_std(self) -> np.ndarray:
        return np.sqrt(np.maximum(self.P[:, 0, 0], 0.0))

    def vel_std(self) -> np.ndarray:
        return np.sqrt(np.maximum(self.P[:, 1, 1], 0.0))
