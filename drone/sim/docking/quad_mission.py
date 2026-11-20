"""Small-quadcopter guidance for the delivery part of the scenario (Phase 2).

State machine, driven only by the quad's own navigation filter, its sensors, and what
the mothership broadcasts over the radio link:

  ATTACHED   on the rail; motors off.
  SPINUP     still latched; motors run at the computed trim thrust for the airspeed the
             mothership reports, holding the rail's attitude. 0.6 s.
  SEPARATE   latch open. Hold the mothership's broadcast velocity while dropping
             ``separate_drop_m`` below the release point, so the quad leaves downward,
             away from the airframe; then start slowing.
  TRANSIT    fly to a point above the street centreline abeam the pad, at pad height +
             ``approach_height_m``, braking with ``decel_m_s2``.
  APPROACH   move sideways to ``standoff_m`` out from the pad edge.
  OVER_PAD   hover ``hover_height_m`` above the pad centre.
  DESCEND    descend at ``descend_speed_m_s`` until the landing gear carries the weight.
  LANDED     motors idle; after ``deliver_s`` the drink is off (mass -> empty).
  TAKEOFF    climb ``takeoff_climb_m`` above the pad.
  DEPART     back out to the street centreline.
  CLIMB      climb to the rendezvous hold point (the docking phases take over there).
  HOLD       hover at the hold point.

The outer loop is a position/velocity controller with acceleration limits and a drag
feed-forward from the nominal drag area and a wind estimate (the mothership's, from its
pitot and GNSS); an integrator removes what the feed-forward gets wrong.
"""
from __future__ import annotations

import math

import numpy as np

from flight.frames import GRAVITY_M_S2, quat_from_euler


def accel_limited_vel(err_m: np.ndarray, v_max_m_s: float, a_max_m_s2: float, k_p_per_s: float) -> np.ndarray:
    """Velocity toward a point: proportional near it, capped by braking distance and v_max."""
    d = float(np.linalg.norm(err_m))
    if d < 1e-6:
        return np.zeros(3)
    v = min(v_max_m_s, math.sqrt(2 * a_max_m_s2 * d), k_p_per_s * d)
    return err_m / d * v


class PosVelController:
    """Shared outer loop: a_cmd = kv (v_sp - v) + ki int + a_ff + drag_ff, limited."""

    def __init__(self, cda_nominal_m2: float, rho_kg_m3: float, kv_per_s: float = 2.5,
                 ki_per_s2: float = 0.6, a_max_m_s2: float = 9.0):
        self.cda, self.rho = cda_nominal_m2, rho_kg_m3
        self.kv, self.ki, self.amax = kv_per_s, ki_per_s2, a_max_m_s2
        self.i = np.zeros(3)

    def drag_ff(self, vel_m_s, wind_m_s, mass_kg) -> np.ndarray:
        va = vel_m_s - wind_m_s
        return 0.5 * self.rho * self.cda * float(np.linalg.norm(va)) * va / mass_kg

    def accel(self, v_sp, vel, wind, mass_kg, dt_s, a_ff=None, freeze_i=False):
        e = v_sp - vel
        if not freeze_i:
            self.i = np.clip(self.i + e * dt_s, -6.0, 6.0)
        a = self.kv * e + self.ki * self.i
        n = float(np.linalg.norm(a))
        if n > self.amax:
            a *= self.amax / n
        if a_ff is not None:
            a = a + a_ff
        return a + self.drag_ff(vel, wind, mass_kg)


class DeliveryMission:
    def __init__(self, cfg_mission: dict, pad, street_half_width_m: float, hold_point_m: np.ndarray,
                 cda_nominal_m2: float, rho_kg_m3: float, mass_loaded_kg: float, mass_empty_kg: float):
        g = cfg_mission.get
        self.c = cfg_mission
        self.pad = pad
        self.hold = np.asarray(hold_point_m, float)
        self.state = "ATTACHED"
        self.t_state = 0.0
        self.ctl = PosVelController(cda_nominal_m2, rho_kg_m3)
        self.m_loaded, self.m_empty = mass_loaded_kg, mass_empty_kg
        self.events: list = []
        self.release_pos = None
        self.release_vel = None
        self.side = float(np.sign(pad.center_top_m[1]))
        self.mount_q = None
        self.yaw = 0.0
        self.delivered = False

    def _go(self, s, t):
        self.events.append((round(t, 3), s))
        self.state, self.t_state = s, t

    def trim_thrust_n(self, airspeed_m_s: float, mass_kg: float) -> float:
        D = 0.5 * self.ctl.rho * self.ctl.cda * airspeed_m_s ** 2
        return math.hypot(D, mass_kg * GRAVITY_M_S2)

    def step(self, stack, t, link_msg, landed_feet: int):
        """Called at the guidance rate. ``link_msg`` = latest mothership broadcast or None."""
        st = self.state
        nav, fc = stack.nav, stack.fc
        dt = stack.p.get("guidance_dt_s", 0.01)
        wind = link_msg["wind_m_s"] if link_msg is not None else np.zeros(3)
        mass = fc.mass_est_kg
        g = self.c
        if st == "ATTACHED":
            fc.motors_off = True
            return
        if st == "SPINUP":
            fc.motors_off = False
            V = link_msg["airspeed_m_s"] if link_msg is not None else 0.0
            # ramp to trim thrust so the mothership's autopilot can absorb the pitching
            # moment of a thrusting quad hanging below its CG
            ramp = min(1.0, (t - self.t_state) / self.c.get("spinup_ramp_s", 1.2))
            fc.set_attitude(self.mount_q, ramp * self.trim_thrust_n(V, mass))
            return
        if st == "SEPARATE":
            v_ms = link_msg["vel_m_s"] if link_msg is not None else self.release_vel
            dz = nav.pos[2] - (self.release_pos[2] - g["separate_drop_m"])
            v_sp = np.array([v_ms[0], v_ms[1], -g["separate_sink_m_s"] if dz > 0 else 0.0])
            if dz <= 0.2:
                self._go("TRANSIT", t)
            a = self.ctl.accel(v_sp, nav.vel, wind, mass, dt)
            fc.set_accel(t, a, self.yaw)
            return
        p = self.pad.center_top_m
        pad_out = p + self.side * np.array([0.0, -(self.pad.size_m[1] / 2 + g["standoff_m"]), 0.0])
        street_pt = np.array([p[0], 0.0, p[2] + g["approach_height_m"]])
        if st == "TRANSIT":
            tgt = street_pt
            v_sp = accel_limited_vel(tgt - nav.pos, g["transit_speed_m_s"], g["decel_m_s2"], 0.8)
            if np.linalg.norm(tgt - nav.pos) < 1.0 and np.linalg.norm(nav.vel) < 1.0:
                self._go("APPROACH", t)
        elif st == "APPROACH":
            tgt = np.array([pad_out[0], pad_out[1], p[2] + g["approach_height_m"]])
            v_sp = accel_limited_vel(tgt - nav.pos, 2.0, 1.0, 0.8)
            if np.linalg.norm(tgt - nav.pos) < 0.3 and np.linalg.norm(nav.vel) < 0.3:
                self._go("OVER_PAD", t)
        elif st == "OVER_PAD":
            tgt = p + np.array([0.0, 0.0, g["hover_height_m"]])
            v_sp = accel_limited_vel(tgt - nav.pos, 1.0, 0.6, 0.8)
            if np.linalg.norm(tgt - nav.pos) < 0.08 and np.linalg.norm(nav.vel) < 0.15:
                self._go("DESCEND", t)
        elif st == "DESCEND":
            e = p - nav.pos
            v_sp = np.array([0.8 * e[0], 0.8 * e[1], -g["descend_speed_m_s"]])
            if landed_feet >= 3 and t - self.t_state > 0.5:
                self._go("LANDED", t)
        elif st == "LANDED":
            fc.motors_off = True
            if not self.delivered and t - self.t_state >= g["deliver_s"]:
                self.delivered = True
                self.events.append((round(t, 3), "DRINK_DELIVERED"))
            if self.delivered and t - self.t_state >= g["deliver_s"] + 1.0:
                fc.motors_off = False
                self.ctl.i[:] = 0.0
                self._go("TAKEOFF", t)
            return
        elif st == "TAKEOFF":
            tgt = p + np.array([0.0, 0.0, g["takeoff_climb_m"]])
            v_sp = accel_limited_vel(tgt - nav.pos, 1.5, 1.5, 1.0)
            if np.linalg.norm(tgt - nav.pos) < 0.2:
                self._go("DEPART", t)
        elif st == "DEPART":
            tgt = np.array([p[0], 0.0, p[2] + g["takeoff_climb_m"]])
            v_sp = accel_limited_vel(tgt - nav.pos, 3.0, 2.0, 0.8)
            if np.linalg.norm(tgt - nav.pos) < 0.5:
                self._go("CLIMB", t)
        elif st == "CLIMB":
            tgt = self.hold
            v_sp = accel_limited_vel(tgt - nav.pos, g["transit_speed_m_s"], g["decel_m_s2"], 0.8)
            if np.linalg.norm(tgt - nav.pos) < 0.5 and np.linalg.norm(nav.vel) < 0.4:
                self._go("HOLD", t)
        else:  # HOLD
            v_sp = accel_limited_vel(self.hold - nav.pos, 2.0, 1.0, 0.8)
        a = self.ctl.accel(v_sp, nav.vel, wind, mass, dt, freeze_i=(st in ("DESCEND",) and landed_feet > 0))
        fc.set_accel(t, a, self.yaw)
