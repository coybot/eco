"""DynamicWorld -- a TeamWorld backend that flies vehicle classes on rigid-body dynamics.

    world = TeamWorld(backend=DynamicWorld(static_obstacles=boxes, wind=WindField(...)))

Drop-in for ``team_world.KinematicWorld``: same ``obstacles()`` / ``integrate(agent,
action, dt)`` interface, same action contracts, so existing controllers and scenarios run
unchanged. For classes that have a dynamics model here the action becomes a SETPOINT for a
simulated autopilot instead of an instantaneous velocity:

  quadcopter  ([vx, vy, vz, yaw_rate] body)  -> velocity loop -> MultirotorFC -> motors,
              6-DOF rigid body with motor lag, drag, wind. Config block
              ``quadcopter_existing_dynamics`` (ESTIMATED: the kinematic class has no mass).
  fixedwing   ([v, -, vz, yaw_rate])         -> airspeed / climb / course demands ->
              FixedWingAutopilot -> surfaces, 6-DOF with stall. Config ``fixedwing_reference``.

Every other class (rover, crazyflie, vtol, ...) falls through to the kinematic integrator.
The autopilots read only their simulated sensors. ``agent.pos/vel/yaw`` are written back
from the rigid body each tick, so the rest of TeamWorld (sensing, collisions, comms) sees
the dynamic state. The default backend stays KinematicWorld: nothing changes unless a
caller opts in.
"""
from __future__ import annotations

import math
import os

import numpy as np
import yaml

try:
    from team_world import KinematicWorld                    # flat sim/ on path
except ImportError:  # pragma: no cover
    from ..team_world import KinematicWorld                  # type: ignore

from .fixedwing import FixedWingStack
from .frames import GRAVITY_M_S2, euler_from_rot, quat_from_euler, wrap_pi
from .multirotor import QuadStack, quad_params
from .wind import WindField

_CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docking", "vehicles.yaml")


class DynamicWorld(KinematicWorld):
    DYNAMIC_CLASSES = {"quadcopter": "quadcopter_existing_dynamics", "fixedwing": "fixedwing_reference"}

    def __init__(self, static_obstacles=None, wind: WindField | None = None, seed: int = 0,
                 cfg_path: str = _CFG):
        super().__init__(static_obstacles)
        with open(cfg_path) as f:
            self.cfg = yaml.safe_load(f)
        self.env = self.cfg["environment"]
        self.wind_field = wind or WindField()
        self.seed = seed
        self.t = 0.0
        self._stacks: dict = {}
        self._cmd: dict = {}

    # -- stack creation ------------------------------------------------------------
    def _make(self, agent):
        name = agent.vclass.name
        block = self.DYNAMIC_CLASSES[name]
        rng = np.random.default_rng([self.seed, abs(hash(agent.id)) % (1 << 31)])
        q = quat_from_euler(0.0, 0.0, float(agent.yaw))
        if name == "quadcopter":
            p = quad_params(self.cfg[block])
            st = QuadStack(p, self.env, rng, np.asarray(agent.pos, float), np.zeros(3), q, p["mass_empty_kg"])
            st.outer = self._quad_outer(agent.id)
        else:
            p = dict(self.cfg[block])
            v0 = max(agent.vclass.min_speed_mps, p["cruise_airspeed_m_s"])
            vel = np.array([math.cos(agent.yaw), math.sin(agent.yaw), 0.0]) * v0
            st = FixedWingStack(p, self.env, rng, np.asarray(agent.pos, float), vel, q)
            st.ap.alt_cmd = float(agent.pos[2])
            st.outer = self._fw_outer(agent.id)
        self._stacks[agent.id] = st
        return st

    def _quad_outer(self, aid):
        state = {"i": np.zeros(3), "yaw_sp": None}

        def outer(s, t):
            vx, vy, vz, wz = self._cmd[aid]
            R = s.nav.R
            yaw = math.atan2(R[1, 0], R[0, 0])
            if state["yaw_sp"] is None:
                state["yaw_sp"] = yaw
            state["yaw_sp"] = wrap_pi(state["yaw_sp"] + wz * self.env["guidance_dt_s"])
            c, sn = math.cos(yaw), math.sin(yaw)
            v_sp = np.array([c * vx - sn * vy, sn * vx + c * vy, vz])
            e = v_sp - s.nav.vel
            state["i"] = np.clip(state["i"] + e * self.env["guidance_dt_s"], -3, 3)
            a = 3.0 * e + 0.8 * state["i"]
            n = np.linalg.norm(a)
            if n > 6.0:
                a *= 6.0 / n
            s.fc.set_accel(t, a, state["yaw_sp"])
        return outer

    def _fw_outer(self, aid):
        state = {"course": None, "alt": None}

        def outer(s, t):
            v, _, vz, wz = self._cmd[aid]
            dtg = self.env["guidance_dt_s"]
            if state["course"] is None:
                state["course"] = math.atan2(s.nav.vel[1], s.nav.vel[0])
                state["alt"] = float(s.nav.pos[2])
            state["course"] = wrap_pi(state["course"] + wz * dtg)
            state["alt"] += vz * dtg
            state["alt"] += 0.2 * (float(s.nav.pos[2]) - state["alt"]) * dtg   # do not wind up
            s.ap.course_cmd = state["course"]
            s.ap.alt_cmd = state["alt"]
            s.ap.airspeed_cmd = float(np.clip(v, s.p.get("min_airspeed_cmd_m_s", 18.0), s.p["cruise_airspeed_m_s"]))
        return outer

    # -- backend interface ----------------------------------------------------------
    def integrate(self, agent, action, dt):
        if agent.vclass.name not in self.DYNAMIC_CLASSES:
            return super().integrate(agent, action, dt)
        st = self._stacks.get(agent.id) or self._make(agent)
        a = np.asarray(action, dtype=float)
        if len(a) < 4:
            a = np.concatenate([a, np.zeros(4 - len(a))])
        self._cmd[agent.id] = a
        n = max(1, int(round(dt / st.dt)))
        t = getattr(st, "_t_s", 0.0)
        for _ in range(n):
            w = self.wind_field.velocity(st.body.pos, t) + self.wind
            st.step(t, w)
            t += st.dt
        st._t_s = t
        b = st.body
        if b.pos[2] < 0.0:                       # ground: stop, do not tunnel
            b.pos[2] = 0.0
            b.vel[:] = 0.0
        agent.pos = b.pos.astype(np.float32)
        agent.vel = b.vel.astype(np.float32)
        agent.yaw = float(euler_from_rot(b.R)[2])
