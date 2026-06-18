"""Domain-randomized velocity-tracking dynamics for the RL env (plan Stage 2).

The kinematic env executes the commanded velocity instantly — real airframes can't. This layer
sits between the policy's commanded velocity and the realized motion, modeling the dominant
sim-to-real effects for a velocity-setpoint policy (ArduPilot closes the inner loop):

  - first-order velocity LAG (time constant tau): the airframe eases toward the command
  - ACCEL cap: bounded change in velocity per tick (momentum — can't stop on a dime)
  - actuation LATENCY: the command takes a few ticks to take effect (react-late failures)
  - WIND: a steady world-frame disturbance pushing the vehicle off course

Every parameter is randomized per episode (domain randomization) so the policy learns margins
that survive the whole range, not one nominal model. Fully vectorized in torch.
"""
from __future__ import annotations

import math


class DynamicsDR:
    """Per-env DR velocity-tracking dynamics. `rand_fn(*shape, lo, hi)->tensor on dev`."""

    LMAX = 2   # max actuation latency in ticks (0.2 s at 10 Hz) — realistic companion->FC->motor

    def __init__(self, torch, n, dev, rand_fn):
        self.t = torch
        self.n = n
        self.dev = dev
        self._rand = rand_fn
        z = torch.zeros(n, device=dev)
        self.v_real = torch.zeros(n, 3, device=dev)   # realized body velocity
        self.yr_real = z.clone()                      # realized yaw rate
        self.buf = torch.zeros(n, self.LMAX + 1, 4, device=dev)  # command history (oldest..newest)
        # DR params (filled by randomize)
        self.tau = z.clone() + 0.2
        self.yaw_tau = z.clone() + 0.15
        self.a_max = z.clone() + 8.0
        self.yaw_a_max = z.clone() + 8.0
        self.latency = torch.zeros(n, device=dev, dtype=torch.long)
        self.wind = torch.zeros(n, 2, device=dev)
        self.scale = 1.0   # DR curriculum: 0 = ~kinematic, 1 = full domain randomization

    def set_scale(self, s):
        """Set the curriculum strength in [0,1] (call once per training iteration)."""
        self.scale = float(max(0.0, min(1.0, s)))

    def randomize(self, mask):
        """Resample DR params + reset realized state for masked envs, scaled by the curriculum.

        At scale 0 the dynamics are ~kinematic (fast lag, huge accel cap, no latency/wind) so the
        policy first relearns the easy task; as scale rises to 1 the full DR range fades in.
        """
        t = self.t
        n = int(mask.sum().item())
        if n == 0:
            return
        s = self.scale
        idx = mask.nonzero(as_tuple=True)[0]
        # interpolate each param from its "ideal/kinematic" value (s=0) to full DR (s=1)
        self.tau[idx] = 0.03 + s * (self._rand(n, lo=0.08, hi=0.30) - 0.03)   # vel lag (s)
        self.yaw_tau[idx] = 0.03 + s * (self._rand(n, lo=0.05, hi=0.25) - 0.03)
        self.a_max[idx] = 40.0 + s * (self._rand(n, lo=4.0, hi=12.0) - 40.0)  # accel cap (m/s^2)
        self.yaw_a_max[idx] = 40.0 + s * (self._rand(n, lo=5.0, hi=15.0) - 40.0)
        lat_full = self._rand(n, lo=0.0, hi=self.LMAX + 0.999)
        self.latency[idx] = (s * lat_full).floor().long().clamp(0, self.LMAX)
        wmag = s * self._rand(n, lo=0.0, hi=1.2)               # wind speed (m/s)
        wdir = self._rand(n, lo=-math.pi, hi=math.pi)
        self.wind[idx, 0] = wmag * t.cos(wdir)
        self.wind[idx, 1] = wmag * t.sin(wdir)
        self.v_real[idx] = 0.0
        self.yr_real[idx] = 0.0
        self.buf[idx] = 0.0

    def apply(self, cmd, dt):
        """cmd (n,4) body-frame [vx,vy,vz,yaw_rate] -> realized (v_body (n,3), yaw_rate (n,))."""
        t = self.t
        # push command, fetch the latency-delayed one
        self.buf = t.cat([self.buf[:, 1:, :], cmd[:, None, :]], dim=1)  # newest at index LMAX
        gather_idx = (self.LMAX - self.latency).clamp(0, self.LMAX)     # (n,)
        rows = t.arange(self.n, device=self.dev)
        delayed = self.buf[rows, gather_idx]                            # (n,4)

        # first-order lag toward the delayed target, then accel-cap the change
        target_v = delayed[:, :3]
        alpha = (dt / self.tau).clamp(0.0, 1.0)[:, None]
        desired_v = self.v_real + (target_v - self.v_real) * alpha
        dv = desired_v - self.v_real
        dvn = dv.norm(dim=1, keepdim=True).clamp(min=1e-9)
        cap = (self.a_max * dt)[:, None]
        dv = dv * (cap / dvn).clamp(max=1.0)
        self.v_real = self.v_real + dv

        # yaw rate: same lag + cap
        target_yr = delayed[:, 3]
        ay = (dt / self.yaw_tau).clamp(0.0, 1.0)
        desired_yr = self.yr_real + (target_yr - self.yr_real) * ay
        dyr = (desired_yr - self.yr_real).clamp(-self.yaw_a_max * dt, self.yaw_a_max * dt)
        self.yr_real = self.yr_real + dyr

        return self.v_real, self.yr_real
