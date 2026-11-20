"""Wind between buildings: mean, shelter, turbulence, gusts, facade up/downdrafts, street
canyon recirculation. A parametric model, NOT a CFD result -- shapes from the
urban-canopy literature (Oke 1988 street-canyon regimes; windward-facade downwash below
the stagnation point at ~0.7 H and upwash above it), magnitudes are estimates.

Components at a point p (beacon frame):
  mean      U_ref at 20 m above ground, power-law shear (exponent 0.25), reduced to
            ``shelter_nd`` (0.5) below the roofline inside the street.
  turbulence  von Karman random-Fourier field (flight.wind.TurbulenceField), intensity
            0.25 of the local mean + 0.3 m/s, L_h 20 m, L_v 10 m.
  gusts     1-cosine pulses, Poisson in time, 2-6 s long, 1-3 m/s x gust_scale.
  facades   for each facade facing the wind (U . n < 0): within ~3 m of it, vertical flow
            w = 0.5 |U.n| (upward above 0.7 H, downward below), and the normal component
            blocked; on the lee side the mean drops to 0.3.
  canyon    below the roofline between two facing facades, the cross-street component
            reverses near the ground (a recirculating vortex), linear in height.
"""
from __future__ import annotations

import math

import numpy as np

from flight.wind import DiscreteGust, TurbulenceField


class UrbanWind:
    def __init__(self, sc, seed: int, t_end_s: float = 900.0, shelter_nd: float = 0.5,
                 intensity_nd: float = 0.25):
        rng = np.random.default_rng([seed, 4242])
        self.sc = sc
        self.U = np.asarray(sc.wind_mean_m_s, float)
        self.ground_z = sc.info["ground_z_m"]
        self.roof_z = sc.roof_z_max_m
        self.shelter = shelter_nd
        Um = float(np.linalg.norm(self.U))
        sig = intensity_nd * Um + 0.3
        self.turb = TurbulenceField(sig, sig, 0.7 * sig, 20.0, 10.0, seed=int(rng.integers(1 << 30)),
                                    n_modes=400, wavelength_min_m=1.0)
        self.gusts = []
        t = float(rng.exponential(60.0))
        while t < t_end_s:
            d = rng.normal(size=2)
            d /= np.linalg.norm(d)
            A = rng.uniform(1.0, 3.0) * sc.gust_scale_nd
            self.gusts.append(DiscreteGust(t, rng.uniform(2.0, 6.0), [A * d[0], A * d[1], 0.0]))
            t += float(rng.exponential(60.0 / max(sc.gust_scale_nd, 1.0)))
        self.facades = [(np.asarray(p, float), np.asarray(n, float), float(h)) for p, n, h in sc.facades]
        self.street_w = sc.street_width_m

    def mean_at(self, p):
        h = max(1.0, p[2] - self.ground_z)
        m = self.U * (h / 20.0) ** 0.25
        if p[2] < self.roof_z:
            m = m * self.shelter
        return m

    def velocity(self, p, t_s):
        p = np.asarray(p, float)
        m = self.mean_at(p)
        w = m + self.turb.velocity(p - self.U * t_s) * (0.6 if p[2] < self.roof_z else 1.0)
        for g in self.gusts:
            if g.t0 <= t_s <= g.t0 + g.T:
                w = w + g.velocity(t_s) * (0.7 if p[2] < self.roof_z else 1.0)
        for (fp, n, H) in self.facades:
            d = float((p - fp) @ n)                 # distance in front of the facade
            if d < 0.0 or d > 8.0:
                continue
            zrel = p[2] - self.ground_z
            if zrel > H + 3.0:
                continue
            un = float(m @ n)
            if un < 0:                              # windward
                f = math.exp(-d / 3.0)
                stag = 0.7 * H
                sgn = 1.0 if zrel > stag else -1.0
                w = w + np.array([0.0, 0.0, sgn * 0.5 * (-un) * f]) - n * un * f   # vertical, normal blocked
            else:                                   # leeward: sheltered
                f = math.exp(-d / 4.0)
                w = w - m * 0.7 * f
        # canyon recirculation (cross-street component reverses near the ground)
        if p[2] < self.roof_z and self.street_w < 30.0:
            zr = (p[2] - self.ground_z) / max(self.roof_z - self.ground_z, 1.0)
            cross = np.array([0.0, m[1], 0.0])
            w = w - cross * (2.0 * (1.0 - zr)) * 0.8
        return w
