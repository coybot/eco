"""One docking trial: the mothership on a straight docking leg, one quadcopter starting
near its station point, docking guidance, funnel contact, latch -- scored on truth.

    run_docking(seed, design=DockDesign(...), airspeed_m_s=19.0, wind20_m_s=7.7, ...)

Randomisation (when ``randomize=True``; Phase 4 widens it): quad mass, drag area, thrust,
motor lag, sensor delays, start offset, mean wind and its direction; turbulence seed.
Fault injection (``fault``): "motor_loss" (one motor to 0 for the rest of the run) or
"brownout" (all motors to 35 % for 0.4 s), triggered at a random point of the approach.

Strikes (the pass criterion): the quad's guard sphere, or its probe, touching the tail
(tailplane, ventral fin), a lift rotor or the pusher. Airframe contacts (fuselage, wing,
rail, funnel exterior, another docked quad) are counted separately.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from flight.frames import GRAVITY_M_S2, quat_from_euler, quat_mul, quat_to_rot, rot_to_quat
from flight.multirotor import QuadStack, quad_params
from flight.randomize import sample
from flight.sensors import CommonModeError, VisionTargetSensor
from flight.wind import TurbulenceField, WindField, mil_hdbk_1797_low_altitude

from .delivery_run import load_cfg
from .dock import DockContact, DockDesign
from .guidance import DockingGuidance
from .quadplane import Mothership
from .relnav import RelNav
from .wake import MothershipFlow


def camera_mount(probe_dir_body: np.ndarray) -> np.ndarray:
    """Camera frame (z = optical axis) aligned with the probe; x roughly body forward."""
    z = probe_dir_body / np.linalg.norm(probe_dir_body)
    x0 = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 0.0, 1.0])
    x = x0 - (x0 @ z) * z
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.column_stack([x, y, z])


def _box_push(p, c, h):
    """(depth, outward normal) of point p against a box surface (depth > 0 means the
    sphere centre is within r of the surface, filled in by the caller)."""
    d = np.abs(p - c) - h
    if np.all(d < 0):                         # centre inside the box: push out the nearest face
        i = int(np.argmax(d))
        nrm = np.zeros(3)
        nrm[i] = math.copysign(1.0, p[i] - c[i])
        return float(-d[i]), nrm, True
    q = np.maximum(d, 0.0)
    dist = float(np.linalg.norm(q))
    nrm = q * np.sign(p - c) / max(dist, 1e-9)
    return dist, nrm, False


def body_contact(qbody, ms, design, contact, r_q, k=3000.0, c=20.0, mu=0.3):
    """Penalty force on the quad's guard sphere from the mothership's fuselage, wing, rail
    and funnel exterior. Returns (force_world_on_quad, contact point in mothership frame)."""
    from flight.contact import penalty_force
    b = ms.body
    p = b.R.T @ (qbody.pos - b.pos)
    v = b.R.T @ (qbody.vel - (b.vel + b.R @ np.cross(b.omega, p)))
    g = ms.geom
    F = np.zeros(3)
    for cc, hh in ((g.fus_c, g.fus_h), (g.wing_c, g.wing_h), (g.rail_c, g.rail_h)):
        dist, nrm, inside = _box_push(p, cc, hh)
        depth = (r_q + dist) if inside else (r_q - dist)
        if depth > 0:
            F += penalty_force(min(depth, 0.05), nrm, v, k, c, mu)
    rel = p - contact.ct
    sc = float(rel @ contact.n)
    radial = rel - sc * contact.n
    rc = float(np.linalg.norm(radial))
    if -0.02 <= sc <= contact.H and rc > 1e-6:
        dist = (rc - (design.throat_radius_m + max(0.0, sc) * contact.tanb)) * contact.cosb
        if 0 < dist < r_q:
            nrm = (radial / rc) * contact.cosb - contact.n * contact.sinb
            F += penalty_force(min(r_q - dist, 0.05), nrm, v, k, c, mu)
    return b.R @ F, p


DEFAULT_RANDOM = {
    "quad_mass_scale_nd": {"dist": "uniform", "low": 0.95, "high": 1.05},
    "quad_cda_scale_nd": {"dist": "uniform", "low": 0.8, "high": 1.2},
    "thrust_scale_nd": {"dist": "uniform", "low": 0.9, "high": 1.05},
    "motor_tau_scale_nd": {"dist": "uniform", "low": 0.8, "high": 1.3},
    "vision_delay_s": {"dist": "uniform", "low": 0.03, "high": 0.07},
    "link_latency_s": {"dist": "uniform", "low": 0.03, "high": 0.08},
    "mean_wind_m_s": {"dist": "uniform", "low": 0.0, "high": 6.0},
    "wind_dir_deg": {"dist": "uniform", "low": 0.0, "high": 360.0},
    "start_offset_m": {"dist": "uniform", "low": 0.0, "high": 0.6},
    "start_vel_offset_m_s": {"dist": "uniform", "low": 0.0, "high": 0.6},
    "ms_mass_scale_nd": {"dist": "uniform", "low": 0.97, "high": 1.03},
    "ms_cd0_scale_nd": {"dist": "uniform", "low": 0.9, "high": 1.1},
}


def run_docking(seed: int, design: DockDesign | None = None, airspeed_m_s: float = 19.0,
                wind20_m_s: float = 7.7, wake_gain: float = 1.0, relnav_mode: str = "fused",
                randomize: bool = True, random_spec: dict | None = None, fault: str | None = None,
                guidance_overrides: dict | None = None, t_max_s: float = 70.0, rear_quad_docked: bool = False,
                sensor_delay_scale: float = 1.0, ms_stiffness: float = 1.0, quad_overrides: dict | None = None,
                record: bool = False, ms_docks_loaded=(False, False)) -> dict:
    cfg = load_cfg()
    env = cfg["environment"]
    dt = env["physics_dt_s"]
    design = design or DockDesign()
    rng = np.random.default_rng(seed)
    rs = sample(random_spec or DEFAULT_RANDOM, rng) if randomize else \
        {k: (1.0 if k.endswith("scale_nd") else 0.0) for k in DEFAULT_RANDOM}
    if not randomize:
        rs.update(vision_delay_s=0.04, link_latency_s=0.04, wind_dir_deg=0.0)
    qcfg = copy.deepcopy(cfg["small_quadcopter"])
    if quad_overrides:
        qcfg.update(quad_overrides)
    qp = quad_params(qcfg)
    msc = copy.deepcopy(cfg["quadplane"])
    msc["link"] = dict(msc["link"], latency_s=rs["link_latency_s"] * sensor_delay_scale)
    gcfg = dict(cfg.get("docking_guidance", {}))
    if guidance_overrides:
        gcfg.update(guidance_overrides)
    # ---- wind
    alt = 50.0
    d = math.radians(rs["wind_dir_deg"])
    turb = TurbulenceField(seed=int(rng.integers(1 << 30)), **mil_hdbk_1797_low_altitude(alt, wind20_m_s)) \
        if wind20_m_s > 0 else None
    wind = WindField([rs["mean_wind_m_s"] * math.cos(d), rs["mean_wind_m_s"] * math.sin(d), 0.0], turb)
    common = CommonModeError(0.01, 60.0, int(rng.integers(1 << 30)))
    # ---- mothership
    ms = Mothership(msc, env, rng, None, np.array([0.0, 0.0, alt]), 0.0, airspeed_m_s, list(ms_docks_loaded),
                    gnss_common=common, mass_scale=rs["ms_mass_scale_nd"], cd0_scale=rs["ms_cd0_scale_nd"],
                    quad_mass_kg=qcfg["mass_empty_kg"], quad_cda_m2=qcfg["drag_area_m2"])
    ms.course_override = (np.array([0.0, 0.0]), 0.0)
    ms.stack.ap.airspeed_cmd = airspeed_m_s
    ms.stack.ap.alt_cmd = alt
    ms.stack.ap.stiffness = ms_stiffness
    flow = MothershipFlow(ms, gain=wake_gain)
    wind.add_source(flow)
    # ---- quad at its station point (truth, plus a random offset)
    contact = DockContact(design)
    m_q = qcfg["mass_empty_kg"] * rs["quad_mass_scale_nd"]
    V = airspeed_m_s
    D = 0.5 * env["air_density_kg_m3"] * qcfg["drag_area_m2"] * rs["quad_cda_scale_nd"] * V * V
    tilt = math.atan2(D, m_q * GRAVITY_M_S2)
    q0 = quat_from_euler(0.0, -tilt, 0.0)
    R0 = quat_to_rot(q0)
    tip_station = np.asarray(design.throat_body_m) + (design.depth_m() + gcfg.get("station_m", 1.5)) * design.axis_body()
    cg_station = tip_station - design.probe_length_m * (R0 @ design.probe_dir_body())
    off = rng.normal(size=3)
    off *= rs["start_offset_m"] / max(np.linalg.norm(off), 1e-9)
    voff = rng.normal(size=3)
    voff *= rs["start_vel_offset_m_s"] / max(np.linalg.norm(voff), 1e-9)
    pos0 = ms.body.pos + ms.body.R @ cg_station + off
    vel0 = ms.body.vel + voff
    quad = QuadStack(qp, env, rng, pos0, vel0, q0, m_q, gnss_common=common,
                     thrust_max_scale=rs["thrust_scale_nd"], cda_scale=rs["quad_cda_scale_nd"],
                     tau_scale=rs["motor_tau_scale_nd"], mass_est_kg=qcfg["mass_empty_kg"])
    quad.phys.motors.thrust_n[:] = math.hypot(D, m_q * GRAVITY_M_S2) / 4.0
    cam_R = camera_mount(design.probe_dir_body())
    vcfg = dict(cfg["vision"], delay_s=rs["vision_delay_s"] * sensor_delay_scale)
    vision = VisionTargetSensor(vcfg, rng, cam_R)
    relnav = RelNav(design.throat_body_m, env["guidance_dt_s"], cfg.get("relnav", {}), mode=relnav_mode)
    guid = DockingGuidance(design, gcfg, qcfg["drag_area_m2"], env["air_density_kg_m3"])
    ahrs_hist: list = []

    def quad_R_at(tm):
        for tq, Rq in reversed(ahrs_hist):
            if tq <= tm + 1e-9:
                return Rq
        return None

    state = {"latched_t": None, "fault_armed": fault is not None, "fault_t": None, "fault_s": float(rng.uniform(0.3, 1.0))}

    def outer(s, t):
        new_link = ms.link.read_new(t)
        msg = ms.link.read(t)
        qm = s.sensors.ahrs.read(t)
        if qm is not None:
            ahrs_hist.append((qm.t_meas_s, quat_to_rot(qm.value)))
            if len(ahrs_hist) > 120:
                del ahrs_hist[:20]
        relnav.step(t, s.nav, new_link, vision.read_new(t), cam_R, quad_R_at)
        gth = None
        if design.gimbal:                           # gimbal encoders: 1 deg noise per axis
            gth = contact.theta + rng.normal(0.0, math.radians(1.0), 3) * (np.abs(contact.theta) > -1)
        guid.step(s, relnav, msg.value if msg is not None else None, t, env["guidance_dt_s"], contact.latched, gth)
    quad.outer = outer

    # ---- loop
    out = {"strikes": [], "contacts": [], "min_clear": {}, "relnav_err_final_max_m": 0.0}
    rows = []
    t = 0.0
    n = 0
    w_ms = w_q = np.zeros(3)
    rotor_r = qcfg["prop_diameter_m"] / 2
    r_q = qcfg["body_radius_m"]
    geom = ms.geom
    rear_cg = None
    if rear_quad_docked:
        rear_cg = np.array(msc["rail"]["dock_offsets_m"][1])
    alt_dev = 0.0
    entry_lat = None
    while t < t_max_s:
        if n % 4 == 0:
            w_ms = wind.ambient(ms.body.pos, t)
            w_q_amb = wind.ambient(quad.body.pos, t)
        w_q = w_q_amb + flow(quad.body.pos, t)
        # fault injection during the approach
        if state["fault_armed"] and guid.state in ("APPROACH", "FINAL") and guid.last["s_m"] < \
                design.depth_m() + gcfg.get("station_m", 1.5) * state["fault_s"]:
            state["fault_armed"] = False
            state["fault_t"] = t
            if fault == "motor_loss":
                quad.phys.motors.healthy_scale[int(rng.integers(4))] = 0.0
            elif fault == "brownout":
                quad.phys.motors.healthy_scale[:] = 0.35
        if fault == "brownout" and state["fault_t"] is not None and t - state["fault_t"] > 0.4:
            quad.phys.motors.healthy_scale[:] = 1.0
        if state["latched_t"] is None:
            vision.push(t, ms.body.pos + ms.body.R @ np.asarray(design.throat_body_m), quad.body.pos, quad.body.R)
            Fq, Mq, Fms, p_c, s_tip, rho_tip = contact.step(quad.body, ms.body, dt)
            # ceiling effect: rotor plane to the nearest surface above it
            clr = geom.clearances(ms.body.R.T @ (quad.body.pos - ms.body.pos))
            gap = min(clr["fuselage"], clr["rail"], clr["wing"]) - r_q * 0.0
            quad.phys.thrust_scale = flow.ceiling_multiplier(gap, rotor_r)
            # the quad's guards against the fuselage, wing, rail and funnel exterior (penalty)
            Fb, pb = body_contact(quad.body, ms, design, contact, r_q)
            quad.phys.ext_force_world_n = Fq + Fb
            quad.phys.ext_moment_body_nm = Mq
            if np.any(Fms):
                ms.stack.phys.ext_forces.append((Fms, p_c))
            if np.any(Fb):
                ms.stack.phys.ext_forces.append((-Fb, pb))
            quad.step(t, w_q)
            if contact.entered and entry_lat is None:
                entry_lat = rho_tip
            if contact.latched:
                state["latched_t"] = t
                Rrel = ms.body.R.T @ quad.body.R
                ms.attach(0, m_q, qcfg["drag_area_m2"], t)
                ms.stack.phys.payloads["dock0"] = (m_q, design.docked_cg_body_m(Rrel), qcfg["drag_area_m2"])
                ms.stack.phys._update_mass()
        else:
            # carried rigidly once latched
            quad.phys.body.pos = ms.body.pos + ms.body.R @ ms.stack.phys.payloads["dock0"][1]
            quad.phys.body.vel = ms.body.vel.copy()
        ms.step(t, w_ms)
        t += dt
        n += 1
        if n % 4 == 0 and state["latched_t"] is None:
            b = ms.body
            p_rel = b.R.T @ (quad.body.pos - b.pos)
            clr = geom.clearances(p_rel)
            tip_rel = b.R.T @ (quad.body.pos + quad.body.R @ (design.probe_length_m * design.probe_dir_body()) - b.pos)
            mid_rel = 0.5 * (p_rel + tip_rel)
            clr_tip = geom.clearances(tip_rel)
            clr_mid = geom.clearances(mid_rel)
            for k in clr:
                c_body = clr[k] - r_q
                c_probe = min(clr_tip[k], clr_mid[k]) - 0.01
                cmin = min(c_body, c_probe)
                out["min_clear"][k] = min(out["min_clear"].get(k, math.inf), cmin)
                if cmin < 0:
                    (out["strikes"] if k in geom.STRIKE_PARTS else out["contacts"]).append((round(t, 3), k))
            if rear_cg is not None:
                dq = float(np.linalg.norm(p_rel - rear_cg)) - 2 * r_q
                out["min_clear"]["other_quad"] = min(out["min_clear"].get("other_quad", math.inf), dq)
                if dq < 0:
                    out["contacts"].append((round(t, 3), "other_quad"))
            # quad body vs funnel exterior (cone volume)
            rel = p_rel - np.asarray(design.throat_body_m)
            sc = float(rel @ contact.n)
            rc = float(np.linalg.norm(rel - sc * contact.n))
            if -0.05 <= sc <= contact.H + 0.02:
                dist = (rc - (design.throat_radius_m + max(0.0, sc) * contact.tanb)) * contact.cosb
                out["min_clear"]["funnel_body"] = min(out["min_clear"].get("funnel_body", math.inf), dist - r_q)
                if dist < r_q:
                    out["contacts"].append((round(t, 3), "funnel_body"))
            alt_dev = max(alt_dev, abs(b.pos[2] - alt))
            if guid.state == "FINAL":
                tr_r = (b.pos + b.R @ np.asarray(design.throat_body_m)) - quad.body.pos
                out["relnav_err_final_max_m"] = max(out["relnav_err_final_max_m"], float(np.linalg.norm(relnav.r - tr_r)))
            if record and n % 8 == 0:
                rows.append({"t_s": t, "x_rel_m": p_rel[0], "y_rel_m": p_rel[1], "z_rel_m": p_rel[2],
                             "tip_x_rel_m": tip_rel[0], "tip_y_rel_m": tip_rel[1], "tip_z_rel_m": tip_rel[2],
                             "pitch_rel_deg": math.degrees(math.atan2(-(b.R.T @ quad.body.R)[2, 0], (b.R.T @ quad.body.R)[2, 2])),
                             "state_label": guid.state})
        if out["strikes"]:
            break
        if state["latched_t"] is not None and t - state["latched_t"] > 0.5:
            break
        if guid.state == "GIVE_UP" and t - guid.t_state > 3.0:
            break
    captured = state["latched_t"] is not None and not contact.overload
    res = {
        "seed_id": seed, "design_label": design.label, "airspeed_m_s": airspeed_m_s, "wind20_m_s": wind20_m_s,
        "wake_gain_nd": wake_gain, "relnav_mode_label": relnav_mode, "fault_label": fault or "none",
        "captured_bool": captured, "attempts_count": guid.attempt if captured else guid.attempts_used,
        "time_to_capture_s": state["latched_t"] if captured else -1.0,
        "strikes_count": len(out["strikes"]), "strike_part_label": out["strikes"][0][1] if out["strikes"] else "none",
        "airframe_contacts_count": len(out["contacts"]),
        "contact_parts_label": "+".join(sorted({c[1] for c in out["contacts"]})) or "none",
        "rim_hits_count": contact.rim_hits, "peak_contact_force_n": contact.peak_force_n,
        "seat_force_n": contact.seat_force_n, "overload_bool": contact.overload,
        "mouth_entry_radius_m": entry_lat if entry_lat is not None else -1.0,
        "aborts_count": len(guid.abort_reasons), "abort_reasons_label": "+".join(r for r in guid.abort_reasons) or "none",
        "relnav_err_final_max_m": out["relnav_err_final_max_m"], "ms_alt_dev_max_m": alt_dev,
        "final_state_label": guid.state, "sim_time_s": t,
    }
    for k in ("tail", "lift_prop", "pusher", "fuselage", "wing", "rail", "funnel_body", "other_quad"):
        res[f"min_clear_{k}_m"] = out["min_clear"].get(k, 99.0)
    for k, v in rs.items():
        res[f"rand_{k}"] = v
    if record:
        res["_rows"] = rows
        res["_events"] = guid.events
    return res
