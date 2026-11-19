"""Aerodynamic force models.

body_drag_body -- quadratic drag on a bluff body. The drag area can be one number
    (isotropic: the classic D = rho/2 CdA V^2 along the relative wind) or one per body
    axis (FLU), e.g. a multirotor that shows more area to the flow when it tilts.

FixedWingAero -- lift, drag, side force and moments of a conventional fixed-wing from
    nondimensional coefficients (the linear stability-derivative form of Beard & McLain,
    "Small Unmanned Aircraft", ch. 4), with stall: lift follows the linear curve up to the
    stall angle and blends, through a sigmoid, into flat-plate lift 2 sin^2(a) cos(a); drag
    blends from the parabolic polar into flat-plate drag. Gusts enter through the relative
    wind, so a vertical gust is an angle-of-attack excursion. Coefficient names carry their
    unit (``_per_rad`` for derivatives, ``_nd`` for nondimensional values).

    Internally computed in FRD (the aero literature's frame), returned in FLU.

Not modelled (stated in PHASE_1.md): compressibility, Reynolds effects, post-stall
pitching moment, dynamic stall, propwash over the wing, ground effect.
"""
from __future__ import annotations

import math

import numpy as np

RHO_SEA_LEVEL_KG_M3 = 1.225


def body_drag_body(v_air_body_m_s: np.ndarray, cda_m2, rho_kg_m3: float) -> np.ndarray:
    """Drag force in the body frame. v_air_body = body velocity relative to the air."""
    speed = math.sqrt(float(v_air_body_m_s @ v_air_body_m_s))
    return -0.5 * rho_kg_m3 * speed * np.asarray(cda_m2) * v_air_body_m_s


def stall_blend(alpha_rad: float, alpha_stall_rad: float, sharpness_per_rad: float) -> float:
    """0 in the attached-flow range, 1 deep in stall (Beard & McLain eq. 4.10)."""
    m, a0 = sharpness_per_rad, alpha_stall_rad
    e1 = math.exp(max(-50.0, min(50.0, -m * (alpha_rad - a0))))
    e2 = math.exp(max(-50.0, min(50.0, m * (alpha_rad + a0))))
    return (1 + e1 + e2) / ((1 + e1) * (1 + e2))


class FixedWingAero:
    """Coefficient model. ``p`` is a dict of named, unit-suffixed coefficients."""

    def __init__(self, p: dict):
        self.S = p["wing_area_m2"]
        self.b = p["span_m"]
        self.c = p["chord_m"]
        self.AR = self.b * self.b / self.S
        g = p.get
        self.cl0 = g("cl0_nd", 0.25)
        self.cla = g("cl_alpha_per_rad", 5.0)
        self.clq = g("cl_q_nd", 7.9)
        self.clde = g("cl_de_per_rad", 0.13)
        self.cd0 = g("cd0_nd", 0.04)
        self.e = g("oswald_nd", 0.8)
        self.alpha_stall = math.radians(g("alpha_stall_deg", 14.0))
        self.stall_m = g("stall_sharpness_per_rad", 50.0)
        self.cd_flat = g("cd_flat_plate_nd", 1.98)
        self.cm0 = g("cm0_nd", 0.02)
        self.cma = g("cm_alpha_per_rad", -1.0)
        self.cmq = g("cm_q_nd", -20.0)
        self.cmde = g("cm_de_per_rad", -1.0)
        self.cyb = g("cy_beta_per_rad", -0.9)
        self.cyp = g("cy_p_nd", 0.0)
        self.cyr = g("cy_r_nd", 0.0)
        self.cydr = g("cy_dr_per_rad", 0.17)
        self.clb = g("croll_beta_per_rad", -0.12)
        self.clp = g("croll_p_nd", -0.5)
        self.clr = g("croll_r_nd", 0.15)
        self.clda = g("croll_da_per_rad", 0.17)
        self.cldr = g("croll_dr_per_rad", 0.002)
        self.cnb = g("cyaw_beta_per_rad", 0.07)
        self.cnp = g("cyaw_p_nd", -0.06)
        self.cnr = g("cyaw_r_nd", -0.1)
        self.cnda = g("cyaw_da_per_rad", -0.01)
        self.cndr = g("cyaw_dr_per_rad", -0.07)

    def lift_coeff(self, alpha_rad: float) -> float:
        s = stall_blend(alpha_rad, self.alpha_stall, self.stall_m)
        lin = self.cl0 + self.cla * alpha_rad
        sa = math.sin(alpha_rad)
        flat = 2.0 * math.copysign(1.0, alpha_rad) * sa * sa * math.cos(alpha_rad)
        return (1 - s) * lin + s * flat

    def drag_coeff(self, alpha_rad: float) -> float:
        s = stall_blend(alpha_rad, self.alpha_stall, self.stall_m)
        lin = self.cl0 + self.cla * alpha_rad
        att = self.cd0 + lin * lin / (math.pi * self.e * self.AR)
        sa = math.sin(alpha_rad)
        return (1 - s) * att + s * (self.cd0 + self.cd_flat * sa * sa)

    def cl_max(self) -> tuple[float, float]:
        """(CLmax, alpha at CLmax in rad), found numerically from the blended curve."""
        al = np.radians(np.linspace(0, 30, 3001))
        cl = np.array([self.lift_coeff(a) for a in al])
        i = int(np.argmax(cl))
        return float(cl[i]), float(al[i])

    def forces_moments(self, v_air_body_flu: np.ndarray, omega_body_flu: np.ndarray,
                       aileron_rad: float, elevator_rad: float, rudder_rad: float,
                       rho_kg_m3: float):
        """Return (force_body_flu_n, moment_body_flu_nm, info)."""
        u, v, w = v_air_body_flu[0], -v_air_body_flu[1], -v_air_body_flu[2]   # FRD
        p, q, r = omega_body_flu[0], -omega_body_flu[1], -omega_body_flu[2]
        Va = math.sqrt(u * u + v * v + w * w)
        if Va < 0.5:
            return np.zeros(3), np.zeros(3), {"alpha_rad": 0.0, "beta_rad": 0.0,
                                              "airspeed_m_s": Va, "cl_nd": 0.0}
        alpha = math.atan2(w, u)
        beta = math.asin(max(-1.0, min(1.0, v / Va)))
        qbar_S = 0.5 * rho_kg_m3 * Va * Va * self.S
        ch, bh = self.c / (2 * Va), self.b / (2 * Va)
        CL = self.lift_coeff(alpha) + self.clq * ch * q + self.clde * elevator_rad
        CD = self.drag_coeff(alpha)
        L, D = qbar_S * CL, qbar_S * CD
        ca, sa = math.cos(alpha), math.sin(alpha)
        fx = -D * ca + L * sa
        fz = -D * sa - L * ca
        fy = qbar_S * (self.cyb * beta + self.cyp * bh * p + self.cyr * bh * r + self.cydr * rudder_rad)
        Lr = qbar_S * self.b * (self.clb * beta + self.clp * bh * p + self.clr * bh * r
                                + self.clda * aileron_rad + self.cldr * rudder_rad)
        M = qbar_S * self.c * (self.cm0 + self.cma * alpha + self.cmq * ch * q + self.cmde * elevator_rad)
        N = qbar_S * self.b * (self.cnb * beta + self.cnp * bh * p + self.cnr * bh * r
                               + self.cnda * aileron_rad + self.cndr * rudder_rad)
        F = np.array([fx, -fy, -fz])
        Mo = np.array([Lr, -M, -N])
        return F, Mo, {"alpha_rad": alpha, "beta_rad": beta, "airspeed_m_s": Va, "cl_nd": CL}
