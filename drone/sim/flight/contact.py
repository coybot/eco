"""Contact forces: spring-damper with regularised Coulomb friction.

Generic penalty contact used for landing on a surface (ground, a balcony pad) and, in
docking/, for a probe in a funnel. Penalty contact is standard in multibody simulation: the
stiffness sets how far surfaces may interpenetrate (mm at these loads) and the damping how
much energy an impact loses (expressed as a coefficient of restitution-like damping
ratio). Values are per-contact config numbers.
"""
from __future__ import annotations

import math

import numpy as np


def penalty_force(depth_m: float, normal: np.ndarray, v_rel_m_s: np.ndarray,
                  stiffness_n_m: float, damping_n_s_m: float, friction_nd: float,
                  v_slip_reg_m_s: float = 0.05) -> np.ndarray:
    """Force on the body for penetration ``depth_m`` (>0) along unit ``normal`` (pointing
    out of the surface, toward the body). ``v_rel`` is the body's velocity relative to the
    surface. Normal force never pulls (no adhesion)."""
    if depth_m <= 0.0:
        return np.zeros(3)
    vn = float(v_rel_m_s @ normal)
    fn = stiffness_n_m * depth_m - damping_n_s_m * vn
    if fn <= 0.0:
        return np.zeros(3)
    vt = v_rel_m_s - vn * normal
    st = math.sqrt(float(vt @ vt))
    ft = -friction_nd * fn * vt / max(st, v_slip_reg_m_s)       # regularised Coulomb
    return fn * normal + ft


class LandingGear:
    """Four feet at body offsets; each touching a horizontal surface gets a penalty force.

    ``surface_z_fn(p_world) -> z or None`` returns the height of the surface under a point
    (None = nothing there). Returns (force_world, moment_body, n_feet_in_contact).
    """

    def __init__(self, feet_body_m, stiffness_n_m: float = 1500.0, damping_n_s_m: float = 40.0,
                 friction_nd: float = 0.8):
        self.feet = [np.asarray(f, float) for f in feet_body_m]
        self.k, self.c, self.mu = stiffness_n_m, damping_n_s_m, friction_nd

    def forces(self, pos_m, vel_m_s, R, omega_body, surface_z_fn):
        F = np.zeros(3)
        M = np.zeros(3)
        n = 0
        up = np.array([0.0, 0.0, 1.0])
        for fb in self.feet:
            pw = pos_m + R @ fb
            zs = surface_z_fn(pw)
            if zs is None:
                continue
            depth = zs - pw[2]
            if depth <= 0.0:
                continue
            v_foot = vel_m_s + R @ np.cross(omega_body, fb)
            f = penalty_force(depth, up, v_foot, self.k, self.c, self.mu)
            if f[2] > 0:
                n += 1
            F += f
            M += np.cross(fb, R.T @ f)
        return F, M, n
