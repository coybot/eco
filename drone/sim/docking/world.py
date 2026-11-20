"""The docking-scenario world: mothership, quadcopters, scene, wind, one fixed-step loop.

Every body is stepped at ``physics_dt_s``. Wind is sampled from one coherent field for
all bodies (turbulence held for ``wind_hold_steps`` physics steps, i.e. 100 Hz); local
sources (airflow near the mothership, Phase 3) are added per body, and a body never feels
its own source.

A quadcopter on the rail is carried rigidly: its pose is the dock pose every step, but its
sensors, navigation filter, mission logic and motors all run, so when it is released its
filter has converged on the real motion and its motors are already at trim thrust. Rotor
thrust of a carried quad is applied to the mothership at the dock. Its drag is already in
the mothership's payload model.

Truth is used here only for physics, contact and scoring: strikes (quad sphere against the
mothership's parts, in the mothership body frame), building collisions and pad contact.
"""
from __future__ import annotations

import math

import numpy as np

from flight.contact import LandingGear
from flight.frames import GRAVITY_M_S2, quat_from_euler, quat_mul, quat_to_rot
from flight.multirotor import QuadStack, quad_params


class QuadAgent:
    def __init__(self, qid: str, p: dict, env: dict, rng, mothership, dock_index: int,
                 mount_pitch_deg: float, mission, gnss_common=None, thrust_max_scale=1.0,
                 cda_scale=1.0, tau_scale=1.0, mass_kg=None):
        self.id = qid
        self.ms = mothership
        self.dock = dock_index
        # mount: nose along the mothership's nose, pitched nose-DOWN by mount_pitch_deg
        self.mount_q_rel = quat_from_euler(0.0, -math.radians(mount_pitch_deg), 0.0)
        pos, vel, q = self._dock_state()
        m = mass_kg if mass_kg is not None else p["mass_loaded_kg"]
        self.stack = QuadStack(p, env, rng, pos, vel, q, m, gnss_common=gnss_common,
                               thrust_max_scale=thrust_max_scale, cda_scale=cda_scale, tau_scale=tau_scale)
        self.stack.phys.motors.thrust_n[:] = 0.0
        self.stack.fc.motors_off = True
        self.gear = LandingGear(p["landing_gear_feet_m"])
        self.mission = mission
        self.attached = True
        self.landed_feet = 0
        self.alive = True
        self.strikes: list = []
        self.contacts: list = []
        self.min_clear: dict = {}
        self.building_hits = 0
        self.stack.outer = self._outer

    def _dock_state(self):
        b = self.ms.body
        r = self.ms.dock_offsets[self.dock]
        pos = b.pos + b.R @ r
        vel = b.vel + b.R @ np.cross(b.omega, r)
        q = quat_mul(b.quat, self.mount_q_rel)
        return pos, vel, q

    def _outer(self, stack, t):
        msg = self.ms.link.read(t)
        self.mission.step(stack, t, msg.value if msg is not None else None, self.landed_feet)

    def step(self, t, wind_w, scene, dt):
        st = self.stack
        if self.attached:
            pos, vel, q = self._dock_state()
            b = st.phys.body
            b.pos[:] = pos
            b.vel[:] = vel
            b.quat = q
            b.R = quat_to_rot(q)
            b.omega = self.ms.body.omega.copy()
            # specific force of a body riding the rail = the mothership's
            b.specific_force_body = b.R.T @ (self.ms.body.R @ self.ms.body.specific_force_body)
            st.sensors.push(t, b)
            if st.n % st.guidance_every == 0:
                st.nav.step(t)
                if st.nav.kf.initialised:
                    self._outer(st, t)
            cmd = st.fc.run(t, dt)
            thrust = st.phys.motors.step(cmd, dt)
            T = float(thrust.sum())
            if T > 0:
                self.ms.stack.phys.ext_forces.append((b.R @ np.array([0, 0, T]), self.ms.dock_offsets[self.dock]))
            st.n += 1
            return
        # free flight: landing gear on pads / ground
        pad = getattr(self.mission, "pad", None)

        def surface(pw):
            if pad is not None and pad.contains_xy(pw, 0.05) and pw[2] > pad.center_top_m[2] - 0.3:
                return float(pad.center_top_m[2])
            return 0.0
        b = st.phys.body
        F, M, n = self.gear.forces(b.pos, b.vel, b.R, b.omega, surface)
        self.landed_feet = n
        st.phys.ext_force_world_n = F
        st.phys.ext_moment_body_nm = M
        st.step(t, wind_w)

    def check_strikes(self, t, r_m):
        if self.attached:
            return
        b = self.ms.body
        p_rel = b.R.T @ (self.stack.body.pos - b.pos)
        clr = self.ms.geom.clearances(p_rel)
        for k, v in clr.items():
            self.min_clear[k] = min(self.min_clear.get(k, math.inf), v)
            if v < r_m:
                (self.strikes if k in self.ms.geom.STRIKE_PARTS else self.contacts).append((round(t, 3), k, round(v, 3)))


class DockingWorld:
    def __init__(self, cfg: dict, scene, mothership, wind, quads: list, wind_hold_steps: int = 4):
        self.cfg = cfg
        self.env = cfg["environment"]
        self.dt = self.env["physics_dt_s"]
        self.scene = scene
        self.ms = mothership
        self.wind = wind
        self.quads = quads
        self.t = 0.0
        self.n = 0
        self.hold = wind_hold_steps
        self._w = {}
        self.log: list = []
        self.hooks: list = []          # callables(world) run every guidance step (scripts)
        self.r_quad = cfg["small_quadcopter"]["body_radius_m"]

    def _wind(self, key, pos, exclude=None):
        if self.n % self.hold == 0 or key not in self._w:
            self._w[key] = self.wind.ambient(pos, self.t)
        w = self._w[key]
        for s in self.wind.sources:
            if exclude is not None and getattr(s, "owner", None) is exclude:
                continue
            w = w + s(pos, self.t)
        return w

    def step(self):
        t = self.t
        if self.n % 4 == 0:
            for h in self.hooks:
                h(self)
        # quads first: a carried quad's pose must be the dock pose at time t (before the
        # mothership advances), and its rotor thrust must reach the mothership this step
        for q in self.quads:
            if not q.alive:
                continue
            q.step(t, self._wind(q.id, q.stack.body.pos, exclude=q), self.scene, self.dt)
            if self.n % 4 == 0:
                q.check_strikes(t, self.r_quad)
                if not q.attached and self.scene is not None and self.scene.sphere_hits_building(
                        q.stack.body.pos, self.r_quad, ignore_pad=getattr(q.mission, "pad", None)):
                    q.building_hits += 1
        self.ms.step(t, self._wind("ms", self.ms.body.pos, exclude=self.ms))
        self.t += self.dt
        self.n += 1

    def run_until(self, t_end_s, stop=None, log_every_s: float = 0.1, logger=None):
        k = max(1, int(round(log_every_s / self.dt)))
        while self.t < t_end_s - 1e-9:
            self.step()
            if logger is not None and self.n % k == 0:
                self.log.append(logger(self))
            if stop is not None and self.n % 4 == 0 and stop(self):
                return True
        return False
