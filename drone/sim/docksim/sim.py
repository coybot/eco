"""Dock-frame physics: MuJoCo contacts plus magnets, rotors, drag, wind, rotor inflow and the
fictitious forces of the hovering mothership.

The simulation runs in frame D. The mothership's motion enters as
  gravity_D = R^T (g_W - a_O)                          (all free bodies, via opt.gravity)
  F_fict    = -m [alpha x r + omega x (omega x r) + 2 omega x v]
  tau_fict  = -I_W alpha
with omega, alpha the mothership's angular velocity/acceleration in D. This is exact for the
translational part and keeps contacts between drone and dock in one rigid frame (no moving
kinematic bodies). Air velocity in D: R^T (w_W - v_O) - omega x r, plus rotor inflow.

One ``tick`` advances one control period (physics_hz / control_hz MuJoCo steps); forces are
held over the tick.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

from .config import G_M_S2, RHO_KG_M3
from .motion import MothershipMotion, Wind, cross
from .scene import DroneSpec, build

CAT_FLOOR, CAT_RIDGE, CAT_POST, CAT_FRAME, CAT_WALL, CAT_STOP = 1, 2, 3, 4, 5, 6
CAT_D_BODY, CAT_D_PAD, CAT_D_BRACKET, CAT_D_BOX = 10, 11, 12, 13
GROUP_UWB = np.array([0, 1, 1, 0, 0, 0], np.uint8)        # airframe + drone bodies block UWB
GROUP_ALL = np.array([1, 1, 1, 1, 1, 0], np.uint8)


def _cat_of(name: str, drone_names):
    for i, dn in enumerate(drone_names):
        if name.startswith(dn + "_"):
            part = name[len(dn) + 1:]
            if part.startswith("pad"):
                return i, CAT_D_PAD
            if part.startswith("br_"):
                return i, CAT_D_BRACKET
            if part in ("box", "rim"):
                return i, CAT_D_BOX
            return i, CAT_D_BODY
    if name.startswith("floor"):
        return -1, CAT_FLOOR
    if name.startswith(("ridge", "wedge", "lip")):
        return -1, CAT_RIDGE
    if name.startswith("post"):
        return -1, CAT_POST
    if name.startswith(("wall", "funnel")):
        return -1, CAT_WALL
    if name.startswith("stop"):
        return -1, CAT_STOP
    return -1, CAT_FRAME


class DockSim:
    def __init__(self, cfg, drones: list[DroneSpec], hover_level: str, wind_mean_m_s: float, rng,
                 hover_scale=1.0, motion_override=None):
        self.cfg = cfg
        b, d, ma = cfg["bee35"], cfg["dock"], cfg["mothership_airframe"]
        self.model, self.xml = build(cfg, drones)
        self.data = mujoco.MjData(self.model)
        m = self.model
        self.specs = drones
        self.names = [ds.name for ds in drones]
        self.n_sub = int(round(d["physics_hz"] / d["control_hz"]))
        self.dt = self.n_sub / d["physics_hz"]
        self.t = 0.0
        self.body_id = [m.body(n).id for n in self.names]
        self.qadr = [m.jnt_qposadr[m.body_jntadr[i]] for i in self.body_id]
        self.vadr = [m.jnt_dofadr[m.body_jntadr[i]] for i in self.body_id]
        self.tag_site = [m.site(f"{n}_tag").id for n in self.names]
        self.down_site = [m.site(f"{n}_down").id for n in self.names]
        self.g_owner = np.full(m.ngeom, -1, int)
        self.g_cat = np.zeros(m.ngeom, int)
        for gi in range(m.ngeom):
            self.g_owner[gi], self.g_cat[gi] = _cat_of(m.geom(gi).name, self.names)
        self.g_name = [m.geom(gi).name for gi in range(m.ngeom)]
        # mothership and environment
        lv = ma["hover_motion"][hover_level]
        self.motion = motion_override or MothershipMotion(lv, ma["hover_motion"], rng, hover_scale)
        self.wind = Wind(wind_mean_m_s, cfg, rng)
        self.g_W = np.array([0.0, -G_M_S2, 0.0])
        # actuators
        self.a_rot = b["rotor_offset_m"]
        yc = b["size_m"][1] / 2
        self.rotor_B = np.array([[self.a_rot, yc, self.a_rot], [self.a_rot, yc, -self.a_rot],
                                 [-self.a_rot, yc, self.a_rot], [-self.a_rot, yc, -self.a_rot]])
        self.spin = np.array([1.0, -1.0, -1.0, 1.0])
        self.k_yaw = b["yaw_torque_per_thrust_m"]
        self.tmax_motor = b["thrust_max_total_n"] / 4
        self.tau_motor = b["motor_tau_s"]
        self.thrust = {i: np.zeros(4) for i in range(len(drones))}
        self.thrust_cmd = {i: np.zeros(4) for i in range(len(drones))}
        self.cda = b["drag_area_m2"]
        # magnets
        mg = d["magnet"]
        o = mg["pad_offset_m"]
        self.pad_B = np.array([[o, 0, o], [o, 0, -o], [-o, 0, o], [-o, 0, -o]], float)
        self.dock_pads = np.array([[px + sx * o, 0.0, sz * o] for px in d["pocket_x_m"]
                                   for sx in (1, -1) for sz in (1, -1)])
        self.mag_f = mg["force_per_pad_n"]
        self.mag_gap = mg["max_gap_m"]
        self.mag_tol = mg["align_tol_m"]
        self.mag_scale = 1.0
        # rotor inflow and strike zones
        ro = ma["rotors"]
        self.rot_pos = np.array(ro["pos_m"], float)
        self.rot_r = ro["radius_m"]
        self.strike_r = ro["strike_radius_m"]
        self.strike_h = ro["strike_half_height_m"]
        self.inflow_v = ro["inflow_speed_m_s"]
        self.inflow_h = ro["inflow_height_m"]
        hx, hz = b["size_m"][0] / 2, b["size_m"][2] / 2
        gx = np.linspace(-hx, hx, 3)
        self.foot_B = np.array([[x, yc, z] for x in gx for z in gx])
        br = b["bracket"]
        pts = [[sx * hx, y, sz * hz] for sx in (1, -1) for sz in (1, -1) for y in (0.0, b["size_m"][1])]
        if not cfg.get("channel_dock", {}).get("enabled", False):
            pts += [[sx * br["mouth_half_width_m"], br["y_min_m"], br["mouth_z_m"]] for sx in (1, -1)]
        self.part_pts_B = np.array(pts)
        if any(ds.box for ds in drones):
            bx = b["box"]
            yb = -bx["rim_drop_m"] - bx["size_m"][1]
            self.box_pts_B = np.array([[sx * bx["size_m"][0] / 2, yb, sz * bx["size_m"][2] / 2]
                                       for sx in (1, -1) for sz in (1, -1)])
        else:
            self.box_pts_B = None
        self.extra_force = {}                                 # drone index -> (force_D, point_D) for tests
        self.gravity_override = None
        self._v_prev = {i: np.zeros(3) for i in range(len(drones))}
        self.acc_rel = {i: np.zeros(3) for i in range(len(drones))}
        mujoco.mj_forward(m, self.data)

    # ---------------------------------------------------------------- truth access
    def pose(self, i):
        d = self.data
        bid = self.body_id[i]
        return d.xpos[bid].copy(), d.xmat[bid].reshape(3, 3).copy()

    def vel(self, i):
        """(linear velocity of the body origin in D, angular velocity in D)."""
        v = np.zeros(6)
        mujoco.mj_objectVelocity(self.model, self.data, mujoco.mjtObj.mjOBJ_BODY, self.body_id[i], v, 0)
        return v[3:].copy(), v[:3].copy()

    def site_pos(self, sid):
        return self.data.site_xpos[sid].copy()

    def ray(self, p, u, exclude_body, group=GROUP_ALL):
        gid = np.zeros(1, np.int32)
        r = mujoco.mj_ray(self.model, self.data, p, u, group, 1, exclude_body, gid)
        return r, int(gid[0])

    def set_motors(self, i, cmd_n):
        self.thrust_cmd[i] = np.clip(cmd_n, 0.0, self.tmax_motor)

    # ---------------------------------------------------------------- forces
    def _air_D(self, p):
        mm = self.motion
        v = mm.Rm.T @ (self.wind.vel_W - mm.vel_W) - cross(mm.omega_D, p)
        return v

    def _inflow_frac(self, i, R, p):
        """Mean downward inflow over the drone footprint (3x3 samples)."""
        pts = p + self.foot_B @ R.T
        tot = 0.0
        for rp in self.rot_pos:
            dy = pts[:, 1] - rp[1]
            dh = np.hypot(pts[:, 0] - rp[0], pts[:, 2] - rp[2])
            msk = (dh < self.rot_r) & (dy >= 0) & (dy <= self.inflow_h)
            if msk.any():
                tot += float(np.sum(self.inflow_v * (1 - dy[msk] / self.inflow_h)))
        return tot / len(pts)

    def _magnets(self, R, p):
        """Total force and torque (about the body origin) of the dock magnets on one drone."""
        pads = p + self.pad_B @ R.T
        F = np.zeros(3)
        Tq = np.zeros(3)
        for pk, rB in zip(pads, self.pad_B @ R.T):
            gap = pk[1]
            if gap > self.mag_gap:
                continue
            dh = np.hypot(self.dock_pads[:, 0] - pk[0], self.dock_pads[:, 2] - pk[2])
            if dh.min() > self.mag_tol:
                continue
            g = max(gap, 0.0)
            f = self.mag_scale * self.mag_f * (1 - g / self.mag_gap) ** 2
            fv = np.array([0.0, -f, 0.0])
            F += fv
            Tq += cross(rB, fv)
        return F, Tq

    def _apply_forces(self):
        d, m = self.data, self.model
        mm = self.motion
        g_D = mm.Rm.T @ (self.g_W - mm.acc_W) if self.gravity_override is None else self.gravity_override
        m.opt.gravity[:] = g_D
        w, al = mm.omega_D, mm.alpha_D
        d.xfrc_applied[:] = 0.0
        for i, bid in enumerate(self.body_id):
            p, R = d.xpos[bid], d.xmat[bid].reshape(3, 3)
            com = d.xipos[bid]
            mass = m.body_mass[bid]
            v, om = self.vel(i)
            v_com = v + cross(om, com - p)
            F = -mass * (cross(al, com) + cross(w, cross(w, com)) + 2 * cross(w, v_com))
            Ri = d.ximat[bid].reshape(3, 3)
            Iw = Ri @ np.diag(m.body_inertia[bid]) @ Ri.T
            T = -Iw @ al
            # drag against the local air (wind, mothership motion, rotor inflow)
            va = self._air_D(com)
            infl = self._inflow_frac(i, R, p)
            if infl:
                va = va + np.array([0.0, -infl, 0.0])
            vr = va - v_com
            F += 0.5 * RHO_KG_M3 * self.cda * np.linalg.norm(vr) * vr
            # rotors (with first-order lag)
            th = self.thrust[i]
            th += (self.thrust_cmd[i] - th) * (1 - math.exp(-self.dt / self.tau_motor))
            if th.any():
                c_B = R.T @ (com - p)
                fB = np.array([0.0, th.sum(), 0.0])
                tB = np.zeros(3)
                for rB, ti in zip(self.rotor_B, th):
                    r = rB - c_B
                    tB += np.array([-r[2] * ti, 0.0, r[0] * ti])
                tB[1] += self.k_yaw * float(self.spin @ th)
                F += R @ fB
                T += R @ tB
            # magnets
            Fm, Tm = self._magnets(R, p)
            if Fm.any():
                F += Fm
                T += Tm - cross(com - p, Fm)          # move torque from the origin to the COM
            if i in self.extra_force:
                fe, pe = self.extra_force[i]
                F += fe
                T += cross(pe - com, fe)
            d.xfrc_applied[bid, :3] = F
            d.xfrc_applied[bid, 3:] = T

    def tick(self):
        self.motion.update(self.dt)
        self.wind.step(self.dt)
        self._apply_forces()
        mujoco.mj_step(self.model, self.data, self.n_sub)
        self.t += self.dt
        for i in range(len(self.body_id)):
            v, _ = self.vel(i)
            self.acc_rel[i] = (v - self._v_prev[i]) / self.dt
            self._v_prev[i] = v

    # ---------------------------------------------------------------- monitoring helpers
    def contacts_of(self, i):
        """List of (other_owner, other_cat, own_cat, normal_force_n, pos) for contacts of drone i."""
        d, m = self.data, self.model
        out = []
        f6 = np.zeros(6)
        for k in range(d.ncon):
            c = d.contact[k]
            o1, o2 = self.g_owner[c.geom1], self.g_owner[c.geom2]
            if o1 == i:
                own, oth = c.geom1, c.geom2
            elif o2 == i:
                own, oth = c.geom2, c.geom1
            else:
                continue
            mujoco.mj_contactForce(m, d, k, f6)
            out.append((int(self.g_owner[oth]), int(self.g_cat[oth]), int(self.g_cat[own]), float(np.linalg.norm(f6[:3])),
                        c.pos.copy()))
        return out

    def in_strike_zone(self, i, with_box):
        p, R = self.pose(i)
        pts = p + self.part_pts_B @ R.T
        if with_box and self.box_pts_B is not None:
            pts = np.vstack([pts, p + self.box_pts_B @ R.T])
        for rp in self.rot_pos:
            dh = np.hypot(pts[:, 0] - rp[0], pts[:, 2] - rp[2])
            dy = np.abs(pts[:, 1] - rp[1])
            if np.any((dh < self.strike_r) & (dy < self.strike_h)):
                return True
        return False

    def magnet_gaps(self, i):
        p, R = self.pose(i)
        return (p + self.pad_B @ R.T)[:, 1]
