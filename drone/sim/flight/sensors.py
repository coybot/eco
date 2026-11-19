"""Sensor models: update rate, delay (with jitter), noise, bias drift, dropouts.

Controllers in this package never read true state. They read the latest *delivered*
measurement of a sensor, which is:
  * sampled at the sensor's own rate (not the physics rate),
  * corrupted by white noise plus a bias that starts random and random-walks,
  * delivered ``delay_s`` (+ uniform jitter) after it was taken, in order,
  * lost with probability ``dropout_p`` per sample, and lost entirely during bursts that
    start as a Poisson process (``burst_rate_hz``) and last ``burst_len_s``.
Each measurement carries the time it was TAKEN, so estimators can fuse it at the right
moment (see estimation.py); its age at use is the honest latency.

SensorChannel is the generic block. The classes below build the usual suite from it:
IMU (specific force + rate), AHRS attitude output, GNSS position/velocity (optionally
with a common-mode error shared between vehicles, like two RTK receivers on one
correction stream), barometer, pitot airspeed, a camera that measures the position of
a fiducial in its own frame (field of view, range limits, range-dependent noise), and a
radio link that carries arbitrary payloads with latency and packet loss.

The AHRS sensor stands in for the autopilot's attitude estimator output. That is an
abstraction: its errors are modelled statistically rather than by running an EKF on the
IMU. Stated in PHASE_1.md.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from .frames import quat_from_rotvec, quat_mul


@dataclass
class Measurement:
    t_meas_s: float        # when it was taken
    t_avail_s: float       # when it reached the consumer
    value: object
    seq: int


def channel_from_cfg(cfg: dict, dim: int, rng: np.random.Generator) -> "SensorChannel":
    g = cfg.get
    return SensorChannel(dim=dim, rate_hz=g("rate_hz", 100.0), delay_s=g("delay_s", 0.0),
                         noise_std=g("noise_std", 0.0), rng=rng,
                         bias_std=g("bias_std", 0.0),
                         bias_rw_std_per_sqrt_s=g("bias_rw_std_per_sqrt_s", 0.0),
                         dropout_p=g("dropout_p", 0.0), burst_rate_hz=g("burst_rate_hz", 0.0),
                         burst_len_s=g("burst_len_s", 0.0), jitter_s=g("jitter_s", 0.0))


class SensorChannel:
    def __init__(self, dim: int, rate_hz: float, delay_s: float, noise_std, rng: np.random.Generator,
                 bias_std=0.0, bias_rw_std_per_sqrt_s=0.0, dropout_p: float = 0.0,
                 burst_rate_hz: float = 0.0, burst_len_s: float = 0.0, jitter_s: float = 0.0):
        self.dim = dim
        self.period_s = 1.0 / rate_hz
        self.delay_s = delay_s
        self.jitter_s = jitter_s
        self.noise_std = np.broadcast_to(np.asarray(noise_std, dtype=float), (dim,)).copy() if dim else None
        self.rng = rng
        bstd = np.broadcast_to(np.asarray(bias_std, dtype=float), (dim,)) if dim else None
        self.bias = rng.normal(0.0, 1.0, dim) * bstd if dim else None
        self.bias_rw = np.broadcast_to(np.asarray(bias_rw_std_per_sqrt_s, dtype=float), (dim,)).copy() if dim else None
        self.dropout_p = dropout_p
        self.burst_rate_hz = burst_rate_hz
        self.burst_len_s = burst_len_s
        self.burst_until_s = -1.0
        self.next_sample_s = 0.0
        self.queue: deque = deque()
        self.latest: Measurement | None = None
        self.seq = 0
        self.last_avail_s = -1e9
        self.n_taken = 0
        self.n_dropped = 0

    def due(self, t_s: float) -> bool:
        return t_s + 1e-12 >= self.next_sample_s

    def push(self, t_s: float, truth) -> None:
        """Offer the true value at time t_s; a measurement is taken if one is due."""
        if not self.due(t_s):
            return
        self.next_sample_s += self.period_s
        if self.next_sample_s <= t_s:                 # caller skipped time: resync
            self.next_sample_s = t_s + self.period_s
        self.n_taken += 1
        if self.dim:
            if np.any(self.bias_rw > 0):
                self.bias = self.bias + self.rng.normal(0.0, 1.0, self.dim) * self.bias_rw * math.sqrt(self.period_s)
            value = np.asarray(truth, dtype=float) + self.bias + self.rng.normal(0.0, 1.0, self.dim) * self.noise_std
        else:
            value = truth
        # dropouts: Poisson bursts, then independent losses
        if self.burst_rate_hz > 0 and self.rng.random() < self.burst_rate_hz * self.period_s:
            self.burst_until_s = t_s + self.burst_len_s
        if t_s < self.burst_until_s or (self.dropout_p > 0 and self.rng.random() < self.dropout_p):
            self.n_dropped += 1
            return
        jitter = self.rng.uniform(0.0, self.jitter_s) if self.jitter_s > 0 else 0.0
        t_av = max(t_s + self.delay_s + jitter, self.last_avail_s)
        self.last_avail_s = t_av
        self.seq += 1
        self.queue.append(Measurement(t_s, t_av, value, self.seq))

    def read(self, t_s: float) -> Measurement | None:
        """Latest measurement delivered by time t_s (may be stale; check its age)."""
        q = self.queue
        while q and q[0].t_avail_s <= t_s + 1e-12:
            self.latest = q.popleft()
        return self.latest

    def read_new(self, t_s: float) -> list:
        """All measurements delivered since the last call, oldest first."""
        out = []
        q = self.queue
        while q and q[0].t_avail_s <= t_s + 1e-12:
            self.latest = q.popleft()
            out.append(self.latest)
        return out


class CommonModeError:
    """A random-walk error shared by several receivers (e.g. RTK on one correction stream).

    Generated on a fixed knot grid from its own seed and interpolated, so every consumer
    sees the same value at the same time regardless of who asks first.
    """

    def __init__(self, std_m: float, correlation_time_s: float, seed: int, knot_s: float = 1.0):
        self.std = std_m
        self.tau = correlation_time_s
        self.knot = knot_s
        self.rng = np.random.default_rng(seed)
        self.vals = [self.rng.normal(0.0, std_m, 3)]
        self.a = math.exp(-knot_s / correlation_time_s)

    def value(self, t_s: float) -> np.ndarray:
        if self.std <= 0:
            return np.zeros(3)
        i = int(t_s // self.knot)
        while len(self.vals) <= i + 1:   # first-order Gauss-Markov on the knot grid
            self.vals.append(self.a * self.vals[-1] + math.sqrt(1 - self.a ** 2) * self.rng.normal(0.0, self.std, 3))
        f = t_s / self.knot - i
        return (1 - f) * self.vals[i] + f * self.vals[i + 1]


class AttitudeSensor:
    """AHRS output: true attitude rotated by a small error (bias + noise), delayed."""

    def __init__(self, cfg: dict, rng: np.random.Generator):
        c = dict(cfg)
        c["noise_std"] = math.radians(c.pop("noise_std_deg", 0.3))
        c["bias_std"] = math.radians(c.pop("bias_std_deg", 0.5))
        c["bias_rw_std_per_sqrt_s"] = math.radians(c.pop("bias_rw_deg_per_sqrt_s", 0.02))
        self.ch = channel_from_cfg(c, 3, rng)
        self._q = None

    def push(self, t_s: float, quat: np.ndarray) -> None:
        if self.ch.due(t_s):
            self._q = quat.copy()
            # store the true quaternion alongside a zero error vector; error added below
            self.ch.push(t_s, np.zeros(3))
            if self.ch.queue and self.ch.queue[-1].t_meas_s == t_s:
                m = self.ch.queue[-1]
                m.value = quat_mul(self._q, quat_from_rotvec(m.value))

    def read(self, t_s: float):
        return self.ch.read(t_s)


class GnssSensor:
    """Position + velocity. Position error = own bias/noise + optional common-mode term."""

    def __init__(self, cfg: dict, rng: np.random.Generator, common: CommonModeError | None = None):
        g = cfg.get
        self.pos = SensorChannel(3, g("rate_hz", 10.0), g("delay_s", 0.1),
                                 [g("pos_noise_std_m", 0.02)] * 2 + [g("pos_noise_std_vertical_m", 0.03)], rng,
                                 bias_std=g("pos_bias_std_m", 0.0),
                                 bias_rw_std_per_sqrt_s=g("pos_bias_rw_m_per_sqrt_s", 0.0),
                                 dropout_p=g("dropout_p", 0.0), burst_rate_hz=g("burst_rate_hz", 0.0),
                                 burst_len_s=g("burst_len_s", 0.0))
        self.vel = SensorChannel(3, g("rate_hz", 10.0), g("delay_s", 0.1), g("vel_noise_std_m_s", 0.03), rng,
                                 dropout_p=0.0)
        self.common = common

    def push(self, t_s: float, pos_m: np.ndarray, vel_m_s: np.ndarray) -> None:
        if self.pos.due(t_s):
            dropped_before = self.pos.n_dropped
            p = pos_m + (self.common.value(t_s) if self.common is not None else 0.0)
            self.pos.push(t_s, p)
            if self.pos.n_dropped == dropped_before:     # velocity comes in the same fix
                self.vel.next_sample_s = t_s
                self.vel.push(t_s, vel_m_s)
            else:
                self.vel.next_sample_s = self.pos.next_sample_s


class VisionTargetSensor:
    """Camera measuring a fiducial's position in the camera frame.

    Camera frame = body frame rotated by ``mount_R`` (camera optical axis = camera +z).
    Only valid when the target is inside the cone of half-angle ``fov_half_deg`` and
    between ``range_min_m`` and ``range_max_m``. Noise std per axis =
    noise_floor_m + noise_per_m2 * range^2 (pixel noise grows with range squared).
    """

    def __init__(self, cfg: dict, rng: np.random.Generator, mount_R: np.ndarray):
        g = cfg.get
        self.ch = SensorChannel(3, g("rate_hz", 30.0), g("delay_s", 0.04), 0.0, rng,
                                dropout_p=g("dropout_p", 0.02), burst_rate_hz=g("burst_rate_hz", 0.05),
                                burst_len_s=g("burst_len_s", 0.5), jitter_s=g("jitter_s", 0.01))
        self.fov_cos = math.cos(math.radians(g("fov_half_deg", 40.0)))
        self.rmin = g("range_min_m", 0.3)
        self.rmax = g("range_max_m", 6.0)
        self.floor = g("noise_floor_m", 0.005)
        self.k2 = g("noise_per_m2", 0.002)
        self.mount_R = mount_R
        self.rng = rng

    def push(self, t_s: float, target_world_m: np.ndarray, cam_world_m: np.ndarray, R_body: np.ndarray) -> None:
        if not self.ch.due(t_s):
            return
        rel_c = self.mount_R.T @ (R_body.T @ (target_world_m - cam_world_m))
        rng_m = float(np.linalg.norm(rel_c))
        visible = self.rmin <= rng_m <= self.rmax and rel_c[2] >= self.fov_cos * rng_m
        if not visible:
            self.ch.next_sample_s += self.ch.period_s
            return
        sd = self.floor + self.k2 * rng_m * rng_m
        self.ch.push(t_s, rel_c + self.rng.normal(0.0, sd, 3))

    def read_new(self, t_s: float):
        return self.ch.read_new(t_s)


class RadioLink:
    """Payload link: rate, latency + jitter, packet loss, loss bursts. Payload is any object."""

    def __init__(self, cfg: dict, rng: np.random.Generator):
        g = cfg.get
        self.ch = SensorChannel(0, g("rate_hz", 20.0), g("latency_s", 0.05), 0.0, rng,
                                dropout_p=g("loss_p", 0.05), burst_rate_hz=g("burst_rate_hz", 0.02),
                                burst_len_s=g("burst_len_s", 0.5), jitter_s=g("jitter_s", 0.02))

    def due(self, t_s: float) -> bool:
        return self.ch.due(t_s)

    def send(self, t_s: float, payload) -> None:
        self.ch.push(t_s, payload)

    def read(self, t_s: float):
        return self.ch.read(t_s)

    def read_new(self, t_s: float):
        return self.ch.read_new(t_s)
