"""Hexacopter sensors from the addendum: two radars, ten multizone infrared rangefinders,
a 3-antenna ranging radio and a long-range radio to the beacon, a differential
barometer, and contact detection. IMU and AHRS come from flight.sensors.

Every sensor samples truth at its own rate, adds its errors, and delivers the result
``delay_s`` later with the time it was taken. The navigation and mapping code reads only
these outputs. All numbers are in vehicles.yaml (hexacopter.sensors); the radar values
are the user's estimates and are UNVALIDATED.

Radar model. Each radar covers +/-60 deg in azimuth and elevation around its boresight in
15 deg cells (8 x 8 beams). Each beam is sampled by 2 x 2 rays; the beam reports only the
nearest detectable return in its cell (two things closer than 15 deg are not separated:
that is the angular resolution), range rounded to 0.1 m, direction = the return's true
direction + 3 deg (1 sigma) noise (single-target angle accuracy, an estimate; reporting
the cell centre instead put returns up to 7.5 deg off and filled the air in front of every
balcony with phantom obstacles).
Detection ranges per material: structure 20 m, glass 8 m, foliage 8 m with a 35 % chance
per beam per scan ("weakly"). Metal wire >= 3 mm: 10 m within 20 deg of perpendicular to
the line of sight, tapering linearly to 0 at 60 deg off perpendicular (the user said "less
at shallow angles"; the taper is an estimate). Non-metal lines and netting: never.
A return also carries a class guess (solid / tree / wire) with confusion
(foliage reported as solid 20 %, wire as solid 10 %, estimates).
Own velocity from Doppler: available when structure lies within 30 m in the field of view;
noise 0.2 m/s per axis (1 sigma) plus a small bias.

Infrared model. Ring of 8 (every 45 deg, horizontal), one up, one down. 8 x 8 zones over
45 deg; one ray per zone. Range 4 m in shade, 1.5 m when the hexacopter is in sun. Glass:
the zone reads nothing (invalid -- indistinguishable from "out of range" to a naive
mapper, which is why the mapper never clears radar obstacles on a no-reading zone).
Objects thinner than 5 cm (wires, netting, twigs) are not seen.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from . import geom as G

S_SOLID, S_TREE, S_WIRE = 0, 1, 2


class _Delay:
    def __init__(self, delay_s):
        self.d = delay_s
        self.q = deque()

    def put(self, t, item):
        self.q.append((t + self.d, t, item))

    def get(self, t):
        out = []
        while self.q and self.q[0][0] <= t + 1e-9:
            _, tm, item = self.q.popleft()
            out.append((tm, item))
        return out


def _mount(pitch_deg):
    p = math.radians(pitch_deg)
    xs = np.array([math.cos(p), 0.0, math.sin(p)])
    ys = np.array([0.0, 1.0, 0.0])
    zs = np.cross(xs, ys)
    return np.column_stack([xs, ys, zs])


def _dir(az, el):
    return np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])


class Radar:
    def __init__(self, cfg, pitch_deg, rng, scene, wire_scale=1.0):
        self.c = cfg
        self.rng = rng
        self.Rm = _mount(pitch_deg)
        self.period = 1.0 / cfg["rate_hz"]
        self.next_t = rng.uniform(0, self.period)
        self.out = _Delay(cfg["delay_s"])
        hf = math.radians(cfg["half_fov_deg"])
        res = math.radians(cfg["angular_resolution_deg"])
        nb = int(round(2 * hf / res))
        self.nb = nb
        centers = -hf + res * (np.arange(nb) + 0.5)
        ns = cfg["subrays_per_beam_count"]
        sub = (np.arange(ns) + 0.5) / ns - 0.5
        dirs, beam = [], []
        self.beam_dir_s = np.zeros((nb * nb, 3))
        for i, az in enumerate(centers):
            for j, el in enumerate(centers):
                self.beam_dir_s[i * nb + j] = _dir(az, el)
                for a in sub:
                    for e in sub:
                        dirs.append(_dir(az + a * res, el + e * res))
                        beam.append(i * nb + j)
        self.ray_dir_s = np.array(dirs)
        self.ray_beam = np.array(beam)
        self.hf, self.res, self.centers = hf, res, centers
        m = np.zeros(G.N_MAT, np.int64)
        mr = np.zeros(G.N_MAT)
        pd = np.ones(G.N_MAT)
        for mat, key, pkey in ((G.CONCRETE, "range_solid_m", None), (G.METAL, "range_solid_m", None),
                               (G.GLASS, "range_glass_m", None), (G.FOLIAGE, "range_foliage_m", "p_detect_foliage_nd"),
                               (G.WOOD, "range_foliage_m", "p_detect_foliage_nd"),
                               (G.PERSON, "range_person_m", "p_detect_person_nd"),
                               (G.FABRIC, "range_fabric_m", "p_detect_fabric_nd")):
            m[mat] = 1
            mr[mat] = cfg[key]
            if pkey:
                pd[mat] = cfg[pkey]
        self.mode, self.maxr, self.pdet = m, mr, pd
        # structure-only table for the Doppler velocity availability check
        self.vmode = np.zeros(G.N_MAT, np.int64)
        self.vmode[[G.CONCRETE, G.METAL, G.GLASS]] = 1
        self.vmaxr = np.full(G.N_MAT, cfg["velocity_structure_range_m"])
        self.vel_bias = rng.normal(0, cfg["velocity_bias_std_m_s"], 3)
        self.wire_scale = wire_scale * cfg.get("wire_range_scale_nd", 1.0)
        # wire sample points (static wires; metal and thick enough only)
        s = scene
        pts, dirs_w, caps = [], [], []
        for k in range(len(s.c0)):
            if s.cmat[k] != G.WIRE_METAL or 2 * s.crad[k] < cfg["wire_min_diameter_m"]:
                continue
            u = s.c1[k] - s.c0[k]
            L = np.linalg.norm(u)
            n = max(2, int(L / 0.25) + 1)
            for f in np.linspace(0, 1, n):
                pts.append(s.c0[k] + f * u)
                dirs_w.append(u / L)
                caps.append(k)
        self.wpts = np.array(pts).reshape(-1, 3)
        self.wdir = np.array(dirs_w).reshape(-1, 3)
        self.wcap = np.array(caps, np.int64)

    def due(self, t):
        return t + 1e-9 >= self.next_t

    def scan(self, t, pos, R, vel_body, scene):
        self.next_t += self.period
        c = self.c
        Rs = R @ self.Rm                         # sensor -> world
        dirs = self.ray_dir_s @ Rs.T
        n = len(dirs)
        orig = np.repeat(pos[None, :], n, 0)
        u_beam = self.rng.random(self.nb * self.nb)
        u = u_beam[self.ray_beam]
        rng_, mat, _, _ = G.raycast(orig, dirs, np.full(n, c["range_solid_m"]), u, *scene.arrays(),
                                    self.mode, self.maxr, self.pdet, 0.0)
        nbeam = self.nb * self.nb
        br = np.full(nbeam, 1e30)
        bm = np.full(nbeam, -1)
        bdir = self.beam_dir_s.copy()           # direction of the reported return (sensor frame)
        for i in range(n):                      # nearest return per beam
            b = self.ray_beam[i]
            if rng_[i] < br[b]:
                br[b] = rng_[i]
                bm[b] = mat[i]
                bdir[b] = self.ray_dir_s[i]
        cls = np.full(nbeam, S_SOLID)
        tree = (bm == G.FOLIAGE) | (bm == G.WOOD)
        cls[tree] = np.where(self.rng.random(tree.sum()) < 0.8, S_TREE, S_SOLID)
        # metal wires
        if len(self.wpts):
            act = scene.cact[self.wcap]
            v = self.wpts[act] - pos
            if len(v):
                r = np.linalg.norm(v, axis=1)
                vs = v @ Rs                       # sensor frame
                az = np.arctan2(vs[:, 1], vs[:, 0])
                el = np.arcsin(np.clip(vs[:, 2] / np.maximum(r, 1e-9), -1, 1))
                cosang = np.abs(np.sum(v * self.wdir[act], 1)) / np.maximum(r, 1e-9)
                psi = np.degrees(np.arcsin(np.clip(cosang, 0, 1)))      # 0 = perpendicular
                full, zero = c["wire_full_range_within_deg"], c["wire_zero_range_at_deg"]
                rmax = c["wire_range_m"] * self.wire_scale * np.clip((zero - psi) / (zero - full), 0.0, 1.0)
                ok = (np.abs(az) < self.hf) & (np.abs(el) < self.hf) & (r <= rmax) & (vs[:, 0] > 0)
                if ok.any():
                    ia = np.clip(((az[ok] + self.hf) / self.res).astype(int), 0, self.nb - 1)
                    ie = np.clip(((el[ok] + self.hf) / self.res).astype(int), 0, self.nb - 1)
                    bb = ia * self.nb + ie
                    for b, rr, vv in zip(bb, r[ok], vs[ok]):
                        if rr < br[b]:
                            br[b] = rr
                            bdir[b] = vv / rr
                            bm[b] = G.WIRE_METAL
                            cls[b] = S_WIRE if self.rng.random() < 0.9 else S_SOLID
        hit = br < 1e29
        rq = np.where(hit, np.round((br + self.rng.normal(0, c["range_noise_std_m"], nbeam))
                                    / c["range_resolution_m"]) * c["range_resolution_m"], 0.0)
        # Doppler own velocity: structure within 30 m in the field of view (beam centres)
        bd = self.beam_dir_s @ Rs.T
        vr, _, _, _ = G.raycast(np.repeat(pos[None, :], nbeam, 0), bd, np.full(nbeam, c["velocity_structure_range_m"]),
                                np.zeros(nbeam), *scene.arrays(), self.vmode, self.vmaxr, np.ones(G.N_MAT), 0.0)
        vel = None
        if (vr < 1e29).any():
            vel = vel_body + self.vel_bias + self.rng.normal(0, c["velocity_noise_std_m_s"], 3)
        # angle of the reported return: true direction + noise (accuracy, not the 15 deg resolution)
        sa = math.radians(c["angle_noise_std_deg"])
        bdir = bdir + self.rng.normal(0, sa, bdir.shape)
        bdir /= np.linalg.norm(bdir, axis=1)[:, None]
        bdir = np.where(hit[:, None], bdir, self.beam_dir_s)
        self.out.put(t, dict(beam_dir_body=bdir @ self.Rm.T, hit=hit, range_m=rq, cls=cls,
                             vel_body=vel, true_mat=bm))

    def read(self, t):
        return self.out.get(t)


class IRArray:
    def __init__(self, cfg, rng, sun_bool, sun_dir):
        self.c = cfg
        self.rng = rng
        self.period = 1.0 / cfg["rate_hz"]
        self.next_t = rng.uniform(0, self.period)
        self.out = _Delay(cfg["delay_s"])
        nz = cfg["zones_count"]
        f = math.radians(cfg["fov_deg"])
        ang = -f / 2 + f * (np.arange(nz) + 0.5) / nz
        local = np.array([_dir(a, e) for a in ang for e in ang])       # boresight +x
        mounts = []
        for k in range(cfg["ring_count"]):
            y = 2 * math.pi * k / cfg["ring_count"]
            mounts.append(np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1.0]]))
        mounts.append(_mount(90.0))
        mounts.append(_mount(-90.0))
        self.dir_body = np.vstack([local @ M.T for M in mounts])
        self.sensor_of_ray = np.repeat(np.arange(len(mounts)), len(local))
        self.mode = np.zeros(G.N_MAT, np.int64)
        self.mode[[G.CONCRETE, G.METAL, G.FABRIC, G.PERSON, G.FOLIAGE, G.WOOD]] = 1
        self.mode[G.GLASS] = 2
        self.sun = sun_bool
        self.sun_dir = np.asarray(sun_dir, float)
        self.shade_mode = np.zeros(G.N_MAT, np.int64)
        self.shade_mode[[G.CONCRETE, G.METAL, G.FABRIC]] = 1
        self.in_sun = False

    def due(self, t):
        return t + 1e-9 >= self.next_t

    def scan(self, t, pos, R, scene):
        self.next_t += self.period
        c = self.c
        if self.sun:
            r, _, _, _ = G.raycast(pos[None, :], self.sun_dir[None, :], np.array([200.0]), np.zeros(1),
                                   *scene.arrays(), self.shade_mode, np.full(G.N_MAT, 200.0), np.ones(G.N_MAT), 0.0)
            self.in_sun = bool(r[0] > 1e29)
        else:
            self.in_sun = False
        rmax = c["range_sun_m"] if self.in_sun else c["range_shade_m"]
        dirs = self.dir_body @ R.T
        n = len(dirs)
        rr, mat, _, _ = G.raycast(np.repeat(pos[None, :], n, 0), dirs, np.full(n, rmax), np.zeros(n),
                                  *scene.arrays(), self.mode, np.full(G.N_MAT, rmax), np.ones(G.N_MAT),
                                  c["min_object_diameter_m"] / 2)
        valid = (rr < 1e29) & (mat != G.GLASS)
        invalid = (rr < 1e29) & (mat == G.GLASS)
        noise = np.maximum(c["noise_std_min_m"], c["noise_std_frac"] * np.where(valid, rr, 0))
        rng_m = np.where(valid, rr + self.rng.normal(0, 1, n) * noise, 0.0)
        self.out.put(t, dict(dir_body=self.dir_body, valid=valid, invalid=invalid, range_m=rng_m, max_range_m=rmax))

    def read(self, t):
        return self.out.get(t)


class RangingRadio:
    """3-antenna ranging to the beacon: range + bearing (azimuth, elevation) in the body frame.
    Errors: half of the variance white, half a slowly varying (5 s) term, so averaging
    many readings does not beat the error down as fast as white noise would."""

    def __init__(self, cfg, rng, beacon_m):
        self.c = cfg
        self.rng = rng
        self.beacon = np.asarray(beacon_m, float)
        self.period = 1.0 / cfg["rate_hz"]
        self.next_t = rng.uniform(0, self.period)
        self.out = _Delay(cfg["delay_s"])
        self.sr = rng.uniform(cfg["range_err_std_min_m"], cfg["range_err_std_max_m"])
        self.sb = math.radians(rng.uniform(cfg["bearing_err_std_min_deg"], cfg["bearing_err_std_max_deg"]))
        self.block = np.zeros(G.N_MAT, np.bool_)
        self.block[[G.CONCRETE, G.METAL]] = True
        self.n_los = self.n_nlos = self.n_false = 0
        self.enabled = True
        # half of each error is slowly varying (multipath changes with geometry, ~5 s), half white
        self.tau = 5.0
        self.cr = 0.0
        self.caz = 0.0
        self.cel = 0.0
        self.last_t = 0.0

    def due(self, t):
        return t + 1e-9 >= self.next_t

    def sample(self, t, pos, R, scene, near_facade_m):
        self.next_t += self.period
        if not self.enabled:
            return
        c = self.c
        d = self.beacon - pos
        r = float(np.linalg.norm(d))
        if r > c["max_range_m"]:
            return
        los = not G.segment_blocked(pos, self.beacon, scene.blo, scene.bhi, scene.bmat, scene.bact, self.block)
        false = False
        if los:
            self.n_los += 1
            p = c["p_reflection_near_facade_nd"] if near_facade_m < 5.0 else c["p_reflection_nd"]
            false = self.rng.random() < p
        else:
            self.n_nlos += 1
            if self.rng.random() >= c["p_nlos_reflection_nd"]:
                return
            false = True
        db = R.T @ d / max(r, 1e-6)
        az = math.atan2(db[1], db[0])
        el = math.asin(max(-1.0, min(1.0, db[2])))
        if false:
            self.n_false += 1
            r += self.rng.uniform(*c["reflection_extra_range_m"])
            e = math.radians(c["reflection_bearing_err_deg"])
            az += self.rng.uniform(-e, e)
            el += self.rng.uniform(-e, e) * 0.5
        else:
            dt = max(t - self.last_t, 1e-3)
            self.last_t = t
            a = math.exp(-dt / self.tau)
            k = math.sqrt(1 - a * a)
            h = math.sqrt(0.5)
            self.cr = a * self.cr + k * self.rng.normal(0, self.sr * h)
            self.caz = a * self.caz + k * self.rng.normal(0, self.sb * h)
            self.cel = a * self.cel + k * self.rng.normal(0, self.sb * h)
            r += self.cr + self.rng.normal(0, self.sr * h)
            az += self.caz + self.rng.normal(0, self.sb * h)
            el += self.cel + self.rng.normal(0, self.sb * h)
        self.out.put(t, dict(range_m=r, az_rad=az, el_rad=el, sigma_r_m=self.sr, sigma_b_rad=self.sb,
                             truth_false=false))

    def read(self, t):
        return self.out.get(t)


class LongRangeRadio:
    def __init__(self, cfg, rng, beacon_m):
        self.c = cfg
        self.rng = rng
        self.beacon = np.asarray(beacon_m, float)
        self.period = 1.0 / cfg["rate_hz"]
        self.next_t = rng.uniform(0, self.period)
        self.out = _Delay(cfg["delay_s"])

    def due(self, t):
        return t + 1e-9 >= self.next_t

    def sample(self, t, pos):
        self.next_t += self.period
        r = float(np.linalg.norm(self.beacon - pos))
        s = self.c["range_err_std_m"] + self.c["range_err_frac"] * r
        self.out.put(t, dict(range_m=max(0.0, r + self.rng.normal(0, s)), sigma_m=s))

    def read(self, t):
        return self.out.get(t)


class BaroDiff:
    """Hex altitude minus the beacon's, from the two barometers (beacon frame z)."""

    def __init__(self, cfg, rng):
        self.c = cfg
        self.rng = rng
        self.period = 1.0 / cfg["rate_hz"]
        self.next_t = rng.uniform(0, self.period)
        self.out = _Delay(cfg["delay_s"])
        self.offset = rng.normal(0, cfg["offset_std_m"])
        self.last_t = 0.0

    def due(self, t):
        return t + 1e-9 >= self.next_t

    def sample(self, t, z_true, wind_speed_m_s):
        self.next_t += self.period
        c = self.c
        dt = t - self.last_t
        self.last_t = t
        self.offset += self.rng.normal(0, c["drift_rw_m_per_sqrt_s"] * math.sqrt(max(dt, 0.0)))
        z = z_true + self.offset + c["gust_coeff_m_per_m2_s2"] * wind_speed_m_s ** 2 + self.rng.normal(0, c["noise_std_m"])
        self.out.put(t, z)

    def read(self, t):
        return self.out.get(t)


class ContactDetector:
    def __init__(self, cfg, rng):
        self.c = cfg
        self.rng = rng
        self.out = _Delay(cfg["delay_s"])
        self.in_contact = False
        self.n_false = 0

    def step(self, t, force_world_n, dt):
        c = self.c
        F = float(np.linalg.norm(force_world_n))
        touching = F > c["force_threshold_n"]
        if touching and not self.in_contact and self.rng.random() < c["p_detect_nd"]:
            n = force_world_n / F
            e = math.radians(c["direction_err_deg"])
            n = n + self.rng.normal(0, e, 3)
            self.out.put(t, dict(dir_world=n / np.linalg.norm(n), false_bool=False))
        self.in_contact = touching
        if self.rng.random() < c["false_rate_per_s"] * dt:
            self.n_false += 1
            v = self.rng.normal(size=3)
            self.out.put(t, dict(dir_world=v / np.linalg.norm(v), false_bool=True))

    def read(self, t):
        return self.out.get(t)
