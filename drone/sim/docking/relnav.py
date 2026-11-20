"""Relative navigation: where is the dock's throat relative to my CG, and how fast is it
moving relative to me? (World frame, quad side, sensors only.)

State: r = p_throat - p_quad, v = d r / dt, per axis, in a DelayedKF. Prediction input:
the mothership's broadcast acceleration minus the quad's own IMU acceleration (both
world frame). Measurements, each fused at the time it was TAKEN:

  link   The mothership broadcasts its own navigation estimate (position, velocity,
         attitude) stamped with its time. The quad looks up its OWN navigation estimate at
         that same time (DelayedKF.state_at) and differences them, after moving the
         mothership position to the throat with the broadcast attitude. With both GNSS on
         one RTK correction stream the common error cancels; what is left is two receivers'
         noise, two attitude errors times the lever arm, and the link latency, which the
         timestamp removes up to clock alignment.
  vision A camera on the quad looking along the probe sees a fiducial at the throat and
         measures its position in the camera frame; rotated to world with the quad's AHRS
         at image time. Valid only within its field of view and range; noise grows with
         range squared; frames drop out, sometimes in bursts.

``mode``: "link" (RTK + link only), "vision" (camera only, link used just to initialise
and for the acceleration feed-forward), or "fused".
"""
from __future__ import annotations

import math

import numpy as np

from flight.estimation import DelayedKF
from flight.frames import quat_to_rot


class RelNav:
    def __init__(self, throat_body_m, dt_s: float, cfg: dict, mode: str = "fused"):
        self.throat = np.asarray(throat_body_m, float)
        self.kf = DelayedKF(dt_s, cfg.get("rel_accel_noise_std_m_s2", 1.5), cfg.get("history_s", 0.8),
                            gate_sigma=cfg.get("gate_sigma", 6.0))
        self.mode = mode
        self.link_pos_var = cfg.get("link_pos_std_m", 0.03) ** 2
        self.link_vel_var = cfg.get("link_vel_std_m_s", 0.05) ** 2
        self.att_err_rad = math.radians(cfg.get("attitude_err_std_deg", 0.6))
        self.last_link_s = -1e9
        self.last_vision_s = -1e9
        self.n_vision = 0
        self.n_link = 0
        self.a_ms = np.zeros(3)
        self.ms_R = np.eye(3)
        self.ms_vel = np.zeros(3)

    def _throat_world(self, msg):
        R = quat_to_rot(msg["quat"])
        return msg["pos_m"] + R @ self.throat, R

    def step(self, t_s, own_nav, link_new: list, vision_new: list, cam_R_body: np.ndarray, quad_R_at):
        kf = self.kf
        for m in link_new:
            self.a_ms = m.value["accel_m_s2"]
            self.ms_vel = m.value["vel_m_s"]
            self.ms_R = quat_to_rot(m.value["quat"])
        if not kf.initialised:
            if link_new:
                m = link_new[-1].value
                own = own_nav.kf.state_at(m["t_meas_s"])
                if own is not None:
                    pT, _ = self._throat_world(m)
                    age = t_s - m["t_meas_s"]
                    r = pT - own[0] + (m["vel_m_s"] - own[1]) * age
                    kf.reset(t_s, r, m["vel_m_s"] - own[1], 0.05 ** 2, 0.1 ** 2)
                    self.last_link_s = m["t_meas_s"]
            return
        kf.predict(self.a_ms - own_nav.accel_world)
        if self.mode in ("link", "fused"):
            for m in link_new:
                v = m.value
                own = own_nav.kf.state_at(v["t_meas_s"])
                if own is None:
                    continue
                pT, R = self._throat_world(v)
                lever = float(np.linalg.norm(self.throat))
                # the difference inherits the quad's OWN position uncertainty (large in a
                # GNSS outage, when the camera should carry the solution)
                own_var = own_nav.kf.pos_std() ** 2
                var = self.link_pos_var + (self.att_err_rad * lever) ** 2 + own_var
                own_vvar = own_nav.kf.vel_std() ** 2
                if kf.update(v["t_meas_s"], pos=pT - own[0], pos_var=var,
                             vel=v["vel_m_s"] - own[1], vel_var=self.link_vel_var + own_vvar):
                    self.last_link_s = v["t_meas_s"]
                    self.n_link += 1
        if self.mode in ("vision", "fused"):
            for m in vision_new:
                Rq = quad_R_at(m.t_meas_s)
                if Rq is None:
                    continue
                c = m.value
                rng_m = float(np.linalg.norm(c))
                sd = 0.005 + 0.002 * rng_m * rng_m + self.att_err_rad * rng_m
                z = Rq @ (cam_R_body @ c)
                if kf.update(m.t_meas_s, pos=z, pos_var=np.full(3, sd * sd)):
                    self.last_vision_s = m.t_meas_s
                    self.n_vision += 1

        if kf.needs_reinit() and link_new:             # diverged: restart from the link
            kf.initialised = False
            self.n_reinit = getattr(self, "n_reinit", 0) + 1

    def age_s(self, t_s: float) -> float:
        last = {"link": self.last_link_s, "vision": self.last_vision_s}.get(self.mode, max(self.last_link_s, self.last_vision_s))
        return t_s - last

    @property
    def r(self):
        return self.kf.xp

    @property
    def v(self):
        return self.kf.xv
