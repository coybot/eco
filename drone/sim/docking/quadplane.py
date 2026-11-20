"""Quadplane mothership (Makeflyeasy Pioneer 3200-class) built on flight.fixedwing.

Mothership = FixedWingStack (6-DOF, stall, pusher, servos, autopilot that reads only its
sensors) + a racetrack path manager + an external rail of docks under the fuselage whose
contents are payloads (mass, inertia, weight moment and drag at the dock position), so
a release changes mass and balance + a radio broadcast of its OWN ESTIMATE of its state
(navigation filter, AHRS, pitot, a wind estimate) for the quadcopters.

Geometry (body FLU, origin at the airframe CG) is a set of primitives used for strike
classification: fuselage, wing, booms, the four lift-rotor discs (stopped in cruise but
still blades), the pusher disc (turning), the tail surfaces, and the rail. All
dimensions are in vehicles.yaml with their source or "estimate".
"""
from __future__ import annotations

import math

import numpy as np

from flight.fixedwing import FixedWingStack, line_course, orbit_course
from flight.frames import quat_from_euler, quat_to_rot
from flight.sensors import RadioLink

from .scene import RouteFollower


# ------------------------------------------------------------------ geometry
def _box_dist(p, c, h):
    d = np.maximum(np.abs(p - c) - h, 0.0)
    return float(np.sqrt(d @ d))


def _disc_dist(p, c, axis, r):
    """Distance from point to a flat disc (centre c, unit normal axis, radius r)."""
    d = p - c
    a = float(d @ axis)
    radial = d - a * axis
    rr = float(np.sqrt(radial @ radial))
    return math.hypot(a, max(0.0, rr - r))


def _seg_dist(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, float((p - a) @ ab) / float(ab @ ab)))
    q = a + t * ab
    return float(np.linalg.norm(p - q))


class QuadplaneGeometry:
    """Strike targets of a Pioneer-3200-like quadplane: one fuselage, a high wing, two lift
    arms through the wing on a 1 m square with four 22 in rotors (stopped in cruise, still
    blades), a pusher at the fuselage tail, a tailplane on the fuselage, and a vertical fin
    that projects DOWN below the fuselage, plus the external rail.

    ``clearances(p)`` -> surface distance from point p (body frame) to each part.
    Hard strikes (the pass criterion): tail (tailplane + ventral fin), lift_prop, pusher.
    """

    STRIKE_PARTS = ("tail", "lift_prop", "pusher")

    def __init__(self, g: dict):
        self.g = g
        f = g["fuselage"]
        self.fus_c = np.array([f["center_x_m"], 0.0, f["center_z_m"]])
        self.fus_h = np.array([f["length_m"] / 2, f["width_m"] / 2, f["height_m"] / 2])
        w = g["wing"]
        self.wing_c = np.array([w["center_x_m"], 0.0, w["center_z_m"]])
        self.wing_h = np.array([w["chord_m"] / 2, w["span_m"] / 2, w["thickness_m"] / 2])
        a = g["lift_arms"]
        self.arms = [(np.array([a["front_x_m"], y, a["z_m"]]), np.array([a["rear_x_m"], y, a["z_m"]]))
                     for y in (a["y_m"], -a["y_m"])]
        self.arm_r = a["radius_m"]
        lr = g["lift_rotors"]
        self.lift = [np.array([x, y, lr["z_m"]]) for x in (lr["front_x_m"], lr["rear_x_m"]) for y in (a["y_m"], -a["y_m"])]
        self.lift_r = lr["diameter_m"] / 2
        pu = g["pusher"]
        self.push_c = np.array([pu["x_m"], 0.0, pu["z_m"]])
        self.push_r = pu["diameter_m"] / 2
        t = g["tailplane"]
        self.tp_c = np.array([t["x_m"], 0.0, t["z_m"]])
        self.tp_h = np.array([t["chord_m"] / 2, t["span_m"] / 2, t["thickness_m"] / 2])
        v = g["ventral_fin"]
        self.vf_c = np.array([v["x_m"], 0.0, (v["top_z_m"] + v["bottom_z_m"]) / 2])
        self.vf_h = np.array([v["chord_m"] / 2, v["thickness_m"] / 2, (v["top_z_m"] - v["bottom_z_m"]) / 2])
        r = g["rail"]
        self.rail_c = np.array([r["center_x_m"], 0.0, r["z_m"]])
        self.rail_h = np.array([r["length_m"] / 2, r["width_m"] / 2, r["height_m"] / 2])

    def clearances(self, p: np.ndarray) -> dict:
        ax = np.array([1.0, 0.0, 0.0])
        up = np.array([0.0, 0.0, 1.0])
        return {
            "fuselage": _box_dist(p, self.fus_c, self.fus_h),
            "wing": _box_dist(p, self.wing_c, self.wing_h),
            "lift_arm": min(_seg_dist(p, a, b) for a, b in self.arms) - self.arm_r,
            "lift_prop": min(_disc_dist(p, c, up, self.lift_r) for c in self.lift),
            "pusher": _disc_dist(p, self.push_c, ax, self.push_r),
            "tail": min(_box_dist(p, self.tp_c, self.tp_h), _box_dist(p, self.vf_c, self.vf_h)),
            "rail": _box_dist(p, self.rail_c, self.rail_h),
        }

    def classify(self, p: np.ndarray, r_m: float, skip=()) -> list:
        return [(k, v) for k, v in self.clearances(p).items() if v < r_m and k not in skip]


# ------------------------------------------------------------------ mothership
class Mothership:
    def __init__(self, cfg: dict, env: dict, rng: np.random.Generator, route, pos_m, heading_rad: float,
                 airspeed_m_s: float, docks_loaded: list, gnss_common=None, mass_scale: float = 1.0,
                 cd0_scale: float = 1.0, quad_mass_kg: float = 1.4, quad_cda_m2: float = 0.05):
        self.cfg = cfg
        p = dict(cfg)
        p["cruise_airspeed_m_s"] = airspeed_m_s
        vel = np.array([math.cos(heading_rad), math.sin(heading_rad), 0.0]) * airspeed_m_s
        self.stack = FixedWingStack(p, env, rng, pos_m, vel, quat_from_euler(0.0, math.radians(3.0), heading_rad),
                                    gnss_common=gnss_common, mass_scale=mass_scale, cd0_scale=cd0_scale)
        self.stack.ap.alt_cmd = float(pos_m[2])
        self.geom = QuadplaneGeometry(cfg["geometry"])
        rail = cfg["rail"]
        self.dock_offsets = [np.array(d, float) for d in rail["dock_offsets_m"]]
        self.stack.phys.add_payload("rail", rail["mass_kg"], [cfg["geometry"]["rail"]["center_x_m"], 0, cfg["geometry"]["rail"]["z_m"]],
                                    rail["drag_area_m2"])
        for i, loaded in enumerate(docks_loaded):
            if loaded:
                self.stack.phys.add_payload(f"dock{i}", quad_mass_kg, self.dock_offsets[i], quad_cda_m2)
        self.route = route
        self.follower = RouteFollower(route) if route is not None else None
        self.link = RadioLink(cfg["link"], rng)
        self.wind_est = np.zeros(3)
        self.stack.outer = self._outer
        self.stack.ap.mass_est_kg = self.stack.phys.body.mass_kg
        self._trim(airspeed_m_s, heading_rad)
        self.course_override = None       # (origin_xy, course) to fly a fixed line instead
        # speed schedule: the straight legs at the commanded (docking) airspeed, turns faster
        # so the load factor of the bank does not eat the stall margin
        self.v_straight = airspeed_m_s
        self.v_turn = airspeed_m_s * cfg.get("turn_speed_factor_nd", 1.12)
        self.events = []

    def _trim(self, V: float, heading_rad: float) -> None:
        """Start in trimmed level flight: alpha for lift = weight, elevator for zero pitching
        moment, throttle for thrust = drag, and the autopilot integrators preset to match."""
        ph = self.stack.phys
        a = ph.aero
        rho = ph.rho
        m = ph.body.mass_kg
        qS = 0.5 * rho * V * V * a.S
        CL = m * 9.80665 / qS
        alpha = (CL - a.cl0) / a.cla
        de = -(a.cm0 + a.cma * alpha) / a.cmde
        CD = a.drag_coeff(alpha)
        D = qS * CD + sum(0.5 * rho * V * V * cda for _, _, cda in ph.payloads.values())
        thr = min(1.0, D / max(ph.prop.max_thrust_n(V), 1e-6))
        ph.body.quat = quat_from_euler(0.0, alpha, heading_rad)
        ph.body.R = quat_to_rot(ph.body.quat)
        ph.ele.angle = de
        ph.prop.throttle = thr
        ap = self.stack.ap
        g = ap.g
        ap.i_th = -de / g["pitch_i_per_s"]
        ap.i_h = (alpha - g["pitch_trim_deg"] * math.pi / 180) / g["alt_i_rad_per_m_s"]
        ap.i_v = (thr - g["throttle_trim_nd"]) / g["speed_i_per_m"]
        ap.cmd = (0.0, de, 0.0, thr)

    @property
    def body(self):
        return self.stack.body

    def mass_kg(self) -> float:
        return self.stack.body.mass_kg

    def _outer(self, s, t):
        ap = s.ap
        if self.course_override is not None:
            o, c = self.course_override
            ap.course_cmd, _ = line_course(s.nav.pos, o, c, math.radians(60), 0.03)
            ap.bank_ff = 0.0
        elif self.follower is not None:
            seg = self.follower.update(s.nav.pos)
            if seg[0] == "line":
                ap.course_cmd, _ = line_course(s.nav.pos, seg[1], seg[2], math.radians(60), 0.03)
                ap.bank_ff = 0.0
                ap.airspeed_cmd = self.v_straight
            else:
                ap.airspeed_cmd = self.v_turn
                ap.course_cmd, _ = orbit_course(s.nav.pos, seg[1], seg[2], seg[3], 2.0)
                V = max(ap.airspeed_meas, 10.0)
                ap.bank_ff = -seg[3] * math.atan(V * V / (9.80665 * seg[2]))
        # wind estimate: GNSS velocity minus air velocity along the AHRS nose (pitot), LPF
        q = s.sensors.ahrs.read(t)
        if q is not None:
            R = quat_to_rot(q.value)
            w = s.nav.vel - R[:, 0] * ap.airspeed_meas
            self.wind_est += 0.01 * (w - self.wind_est)    # ~1 s time constant at 100 Hz
            if self.link.due(t):
                self.link.send(t, {"t_meas_s": t, "pos_m": s.nav.pos.copy(), "vel_m_s": s.nav.vel.copy(),
                                   "quat": q.value.copy(), "airspeed_m_s": ap.airspeed_meas,
                                   "wind_m_s": self.wind_est.copy(), "accel_m_s2": s.nav.accel_world.copy()})

    def release(self, dock_index: int, t: float) -> None:
        self.stack.phys.remove_payload(f"dock{dock_index}")
        self.stack.ap.mass_est_kg = self.stack.phys.body.mass_kg     # the autopilot knows what it dropped
        self.events.append((round(t, 3), f"release_dock{dock_index}"))

    def attach(self, dock_index: int, mass_kg: float, cda_m2: float, t: float) -> None:
        self.stack.phys.add_payload(f"dock{dock_index}", mass_kg, self.dock_offsets[dock_index], cda_m2)
        self.stack.ap.mass_est_kg = self.stack.phys.body.mass_kg
        self.events.append((round(t, 3), f"attach_dock{dock_index}"))

    def dock_world(self, dock_index: int) -> np.ndarray:
        b = self.stack.body
        return b.pos + b.R @ self.dock_offsets[dock_index]

    def step(self, t, wind_world):
        self.stack.step(t, wind_world)
