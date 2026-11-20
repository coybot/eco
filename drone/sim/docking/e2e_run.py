"""End-to-end scenario: two quads released over the street, two balcony deliveries,
take-off, climb to hold points under the route, run-up, docking on a later pass.

Order (from the Phase 3 clearance result): release the REAR dock's quad first, then the
FRONT; recover into the FRONT dock first, then the REAR, so no quad ever passes under one
that is still on the rail.

Run-up: a quad holds below the route ahead of where the mothership will pass. When the
mothership (by its broadcast position) is ``d_trig`` behind, the quad accelerates along
the route at ``runup_accel_m_s2`` until it is within ``handover_speed_m_s`` of the
mothership's speed, then the docking guidance takes over from STATION. A quad that runs
out of straight leg (or gives up) climbs back to its hold point and tries the next pass
(at most ``max_passes``).

    run_e2e(seed) -> dict
"""
from __future__ import annotations

import copy
import math

import numpy as np

from flight.frames import GRAVITY_M_S2, quat_from_euler, quat_to_rot
from flight.multirotor import quad_params
from flight.sensors import CommonModeError, VisionTargetSensor

from .delivery_run import build_wind, load_cfg
from .dock import DockContact, DockDesign
from .docking_trial import body_contact, camera_mount
from .guidance import DockingGuidance
from .quad_mission import DeliveryMission, PosVelController, accel_limited_vel
from .quadplane import Mothership
from .relnav import RelNav
from .scene import make_street
from .wake import MothershipFlow
from .world import DockingWorld, QuadAgent


class RunupAndDock:
    """Wraps the delivery mission: after HOLD, run up and dock (sensors/link only)."""

    def __init__(self, qa, design: DockDesign, dock_index: int, cfg, env, ms, rng, vcfg, rel_cfg, gcfg, order_gate):
        self.qa, self.d, self.dock = qa, design, dock_index
        self.ms = ms
        self.state = "DELIVERY"
        self.cam_R = camera_mount(design.probe_dir_body())
        self.vision = VisionTargetSensor(vcfg, rng, self.cam_R)
        self.relnav = RelNav(design.throat_body_m, env["guidance_dt_s"], rel_cfg)
        self.guid = None
        self.gcfg = gcfg
        self.cfg = cfg
        self.contact = DockContact(design)
        self.ahrs_hist = []
        self.passes = 0
        self.max_passes = 3
        self.order_gate = order_gate     # callable: may this quad start its run-up now?
        self.events = []
        self.ctl = PosVelController(cfg["small_quadcopter"]["drag_area_m2"], env["air_density_kg_m3"])
        self.rng = rng

    def quad_R_at(self, tm):
        for tq, R in reversed(self.ahrs_hist):
            if tq <= tm + 1e-9:
                return R
        return None


def run_e2e(seed: int, wind20_m_s: float = 7.7, mean_wind_m_s: float | None = None, airspeed_m_s: float = 20.0,
            t_max_s: float = 420.0, wake_gain: float = 1.0) -> dict:
    cfg = load_cfg()
    env = cfg["environment"]
    rng = np.random.default_rng(seed)
    qcfg = quad_params(cfg["small_quadcopter"])
    qp = copy.deepcopy(cfg["quadplane"])
    V = airspeed_m_s
    front = DockDesign(axis_pitch_deg=round(math.degrees(math.atan2(0.5 * 1.225 * 0.05 * V * V, GRAVITY_M_S2)), 1))
    rear = copy.deepcopy(front)
    rear.throat_body_m = (front.throat_body_m[0] - 0.55, 0.0, front.throat_body_m[2])
    designs = [front, rear]
    Rrel = quat_to_rot(quat_from_euler(0.0, -math.radians(front.axis_pitch_deg), 0.0))
    qp["rail"] = dict(qp["rail"], dock_offsets_m=[list(d.docked_cg_body_m(Rrel)) for d in designs])
    scene = make_street(seed=7, cruise_airspeed_m_s=V * qp.get("turn_speed_factor_nd", 1.15),
                        max_bank_deg=qp.get("turn_bank_deg", 25.0))
    route = scene.route
    pads = [scene.pads[i] for i in rng.choice(len(scene.pads), size=2, replace=False)]
    mw = float(rng.uniform(0, 6)) if mean_wind_m_s is None else mean_wind_m_s
    wind = build_wind(cfg, int(rng.integers(1 << 30)), route.altitude_m, wind20_m_s, mw, float(rng.uniform(0, 360)))
    common = CommonModeError(0.01, 60.0, int(rng.integers(1 << 30)))
    start = np.array([route.leg_start_x_m - 100.0, route.leg_y_m, route.altitude_m])
    ms = Mothership(qp, env, rng, route, start, 0.0, V, [True, True], gnss_common=common,
                    quad_mass_kg=qcfg["mass_loaded_kg"], quad_cda_m2=qcfg["drag_area_m2"])
    flow = MothershipFlow(ms, gain=wake_gain)
    wind.add_source(flow)
    gcfg = dict(cfg["docking_guidance"])
    agents = []
    for i, (dock_i, pad) in enumerate(zip((1, 0), pads)):           # rear (1) released first
        hold = np.array([route.leg_start_x_m + 60.0 + 40.0 * i, route.leg_y_m, route.altitude_m - 5.0])
        mis = DeliveryMission(cfg["mission"], pad, scene.half_width_m, hold, qcfg["drag_area_m2"],
                              env["air_density_kg_m3"], qcfg["mass_loaded_kg"], qcfg["mass_empty_kg"])
        qa = QuadAgent(f"q{i}", qcfg, env, rng, ms, dock_i, front.axis_pitch_deg, mis, gnss_common=common)
        agents.append(qa)
    world = DockingWorld(cfg, scene, ms, wind, agents)
    rd = {}
    docked = {0: False, 1: False}

    def gate_front_first(dock_i):
        return dock_i == 0 or docked[0] or any(a.dock == 0 and rd[a.id].state == "FAILED" for a in agents)

    for qa in agents:
        rd[qa.id] = RunupAndDock(qa, designs[0 if qa.dock == 0 else 1], qa.dock, cfg, env, ms, rng,
                                 dict(cfg["vision"]), cfg["relnav"], gcfg, gate_front_first)
    # release script: rear quad, then front 3 s later, on the first street pass
    rel = {"done": 0, "spin": {}}

    def release_script(w):
        t = w.t
        for k, qa in enumerate(agents):
            mis = qa.mission
            if qa.attached and mis.state == "ATTACHED" and ms.follower.leg == 0 and ms.follower.laps == 0 \
                    and ms.stack.nav.pos[0] >= mis.pad.center_top_m[0] - 60.0 and t > 3.0 * k + 1.0 \
                    and (k == 0 or not agents[0].attached):
                mis._go("SPINUP", t)
                q = qa.stack.sensors.ahrs.read(t)
                mis.mount_q = q.value.copy()
                rel["spin"][qa.id] = t
            if mis.state == "SPINUP" and t - rel["spin"][qa.id] >= cfg["mission"].get("spinup_ramp_s", 1.2) + 0.5:
                qa.attached = False
                ms.release(qa.dock, t)
                mis.release_pos = qa.stack.nav.pos.copy()
                msg = ms.link.read(t)
                mis.release_vel = msg.value["vel_m_s"] if msg is not None else qa.stack.nav.vel.copy()
                mis.yaw = math.atan2(mis.release_vel[1], mis.release_vel[0])
                mis._go("SEPARATE", t)
                if mis.delivered is False:
                    pass
    world.hooks.append(release_script)

    # delivered -> mass change, as in delivery_run
    for qa in agents:
        mis = qa.mission
        orig = mis.step

        def make(qa=qa, mis=mis, orig=orig):
            def step(stack, t, msg, feet):
                before = mis.delivered
                r = rd[qa.id]
                if r.state == "DELIVERY":
                    orig(stack, t, msg, feet)
                    if mis.delivered and not before:
                        qa.stack.phys.set_mass(qcfg["mass_empty_kg"])
                        qa.stack.fc.set_mass_estimate(qcfg["mass_empty_kg"])
                    if mis.state == "HOLD":
                        r.state = "HOLD"
                        r.events.append((round(t, 2), "HOLD"))
                else:
                    dock_step(qa, r, stack, t, msg)
            return step
        mis.step = make()

    def dock_step(qa, r, stack, t, msgv):
        # relnav and camera run in every post-delivery state
        new_link = ms.link.read_new(t) if False else r_link_new(r, t)
        qm = stack.sensors.ahrs.read(t)
        if qm is not None:
            r.ahrs_hist.append((qm.t_meas_s, quat_to_rot(qm.value)))
            if len(r.ahrs_hist) > 120:
                del r.ahrs_hist[:20]
        r.relnav.step(t, stack.nav, new_link, r.vision.read_new(t), r.cam_R, r.quad_R_at)
        fc, nav = stack.fc, stack.nav
        dt = env["guidance_dt_s"]
        hold = qa.mission.hold
        if msgv is None:
            return
        ms_x = msgv["pos_m"][0]
        if r.state == "HOLD":
            fc.motors_off = False
            v_sp = accel_limited_vel(hold - nav.pos, 3.0, 2.0, 0.8)
            fc.set_accel(t, r.ctl.accel(v_sp, nav.vel, msgv["wind_m_s"], fc.mass_est_kg, dt), 0.0)
            # trigger: on the east leg, the mothership behind by the run-up distance
            a = gcfg.get("runup_accel_m_s2", 4.0)
            d_trig = V * V / (2 * a) - 2.0       # ends near the station (about 1.3 m behind the CG)
            if ms.follower.leg == 0 and 0 < nav.pos[0] - ms_x < d_trig + 3.0 and nav.pos[0] - ms_x > d_trig - 3.0 \
                    and r.order_gate(r.dock) and r.passes < r.max_passes:
                r.state = "RUNUP"
                r.passes += 1
                r.events.append((round(t, 2), f"RUNUP_pass{r.passes}"))
        elif r.state == "RUNUP":
            a = gcfg.get("runup_accel_m_s2", 4.0)
            v_ms = msgv["vel_m_s"]
            u = v_ms / max(np.linalg.norm(v_ms), 1.0)
            dv = float((v_ms - nav.vel) @ u)
            a_cmd = u * min(a, max(0.0, 2.0 * dv)) + np.array([0, 0, 1.0 * (hold[2] - nav.pos[2]) - 0.8 * nav.vel[2]])
            lat = (nav.pos - hold) - float((nav.pos - hold) @ u) * u
            a_cmd += -1.0 * np.array([lat[0], lat[1], 0.0])
            fc.set_accel(t, a_cmd + r.ctl.drag_ff(nav.vel, msgv["wind_m_s"], fc.mass_est_kg), 0.0)
            if dv < gcfg.get("handover_speed_m_s", 1.5) and r.relnav.kf.initialised:
                r.guid = DockingGuidance(r.d, gcfg, qcfg["drag_area_m2"], env["air_density_kg_m3"])
                r.state = "DOCKING"
                r.events.append((round(t, 2), "HANDOVER"))
        elif r.state == "DOCKING":
            gth = r.contact.theta + r.rng.normal(0.0, math.radians(1.0), 3)
            r.guid.step(stack, r.relnav, msgv, t, dt, r.contact.latched, gth)
            out_of_leg = ms.follower.leg != 0
            if r.guid.state == "LATCHED":
                pass
            elif r.guid.state == "GIVE_UP" or (out_of_leg and r.guid.state in ("STATION", "ABORT")):
                r.events.append((round(t, 2), "MISSED_PASS"))
                r.state = "RETURN" if r.passes < r.max_passes else "FAILED"
        elif r.state == "RETURN":
            v_sp = accel_limited_vel(hold - nav.pos, 10.0, 3.0, 0.8)
            fc.set_accel(t, r.ctl.accel(v_sp, nav.vel, msgv["wind_m_s"], fc.mass_est_kg, dt), 0.0)
            if np.linalg.norm(hold - nav.pos) < 1.0:
                r.state = "HOLD"
        elif r.state == "FAILED":
            v_sp = accel_limited_vel(hold - nav.pos, 5.0, 2.0, 0.8)
            fc.set_accel(t, r.ctl.accel(v_sp, nav.vel, msgv["wind_m_s"], fc.mass_est_kg, dt), 0.0)

    link_q = {qa.id: [] for qa in agents}

    def r_link_new(r, t):
        # every quad needs its own copy of the broadcast stream
        lst = link_q[r.qa.id]
        out = [m for m in lst if m.t_avail_s <= t + 1e-12]
        link_q[r.qa.id] = [m for m in lst if m.t_avail_s > t + 1e-12]
        return out

    # fan the broadcast out to each quad's queue, and run docking contact physics
    orig_send = ms.link.send

    def send(t, payload):
        n0 = ms.link.ch.seq
        orig_send(t, payload)
        if ms.link.ch.seq > n0:
            m = ms.link.ch.queue[-1]
            for k in link_q:
                link_q[k].append(m)
    ms.link.send = send

    orig_qstep = QuadAgent.step

    def qstep(self, t, wind_w, scene_, dt):
        r = rd[self.id]
        if r.state == "DOCKING" and not self.attached:
            b = self.stack.body
            r.vision.push(t, ms.body.pos + ms.body.R @ np.asarray(r.d.throat_body_m), b.pos, b.R)
            Fq, Mq, Fms, p_c, s_tip, rho_tip = r.contact.step(b, ms.body, dt)
            Fb, pb = body_contact(b, ms, r.d, r.contact, qcfg["body_radius_m"])
            self.stack.phys.ext_force_world_n = Fq + Fb
            self.stack.phys.ext_moment_body_nm = Mq
            if np.any(Fms):
                ms.stack.phys.ext_forces.append((Fms, p_c))
            if np.any(Fb):
                ms.stack.phys.ext_forces.append((-Fb, pb))
            self.stack.step(t, wind_w)
            if r.contact.latched and not r.contact.overload:
                self.attached = True
                docked[self.dock] = True
                Rr = ms.body.R.T @ b.R
                self.mount_q_rel = quat_from_euler(0.0, -math.radians(r.d.axis_pitch_deg), 0.0)
                ms.attach(self.dock, qcfg["mass_empty_kg"], qcfg["drag_area_m2"], t)
                r.state = "DOCKED"
                r.events.append((round(t, 2), "DOCKED"))
                self.stack.fc.motors_off = True
            return
        if r.state == "DOCKED":
            self.attached = True
        if r.state in ("HOLD", "RUNUP", "RETURN", "FAILED") and not self.attached:
            r.vision.push(t, ms.body.pos + ms.body.R @ np.asarray(r.d.throat_body_m), self.stack.body.pos, self.stack.body.R)
        orig_qstep(self, t, wind_w, scene_, dt)
    QuadAgent.step = qstep
    try:
        def stop(w):
            return all(docked.values()) or all(rd[a.id].state in ("DOCKED", "FAILED") for a in agents) and \
                any(rd[a.id].state == "FAILED" for a in agents)
        world.run_until(t_max_s, stop=stop, log_every_s=0.5, logger=lambda w: None)
    finally:
        QuadAgent.step = orig_qstep
    out = {"seed_id": seed, "wind20_m_s": wind20_m_s, "mean_wind_m_s": mw, "airspeed_m_s": V,
           "docked_bool": all(docked.values()), "docked_count": int(sum(docked.values())),
           "strikes_count": int(sum(len(a.strikes) for a in agents)),
           "airframe_contacts_count": int(sum(len(a.contacts) for a in agents)),
           "building_hits_count": int(sum(a.building_hits for a in agents)),
           "delivered_count": int(sum(a.mission.delivered for a in agents)),
           "sim_time_s": world.t}
    for k, a in enumerate(agents):
        r = rd[a.id]
        ev = dict((e[1], e[0]) for e in a.mission.events)
        out[f"q{k}_pad_label"] = a.mission.pad.pad_id
        out[f"q{k}_dock_id"] = a.dock
        out[f"q{k}_t_landed_s"] = ev.get("LANDED", -1.0)
        out[f"q{k}_t_hold_s"] = dict((e[1], e[0]) for e in r.events).get("HOLD", -1.0)
        out[f"q{k}_t_docked_s"] = dict((e[1], e[0]) for e in r.events).get("DOCKED", -1.0)
        out[f"q{k}_passes_count"] = r.passes
        out[f"q{k}_attempts_count"] = (r.guid.attempt if r.guid else 0)
        out[f"q{k}_final_state_label"] = r.state
        out[f"q{k}_events_label"] = ";".join(f"{e[0]}:{e[1]}" for e in r.events)
    return out
