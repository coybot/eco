"""Phase 2 end-to-end run: mothership flies the street route in gusts, releases a quad over
the street, the quad lands on a balcony pad, delivers, takes off and climbs to a hold point.

    run_delivery(seed, pad_id=None, wind20_m_s=15*0.514, ...) -> dict of results

Release timing: the quad is released when the mothership (by its own navigation estimate,
not truth) passes ``release_lead_m`` before the pad's x on the street leg.
"""
from __future__ import annotations

import math
import os

import numpy as np
import yaml

from flight.multirotor import quad_params
from flight.sensors import CommonModeError
from flight.wind import TurbulenceField, WindField, mil_hdbk_1797_low_altitude

from .quad_mission import DeliveryMission
from .quadplane import Mothership
from .scene import make_street
from .world import DockingWorld, QuadAgent

CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vehicles.yaml")


def load_cfg(path: str = CFG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_wind(cfg, seed, altitude_m, wind20_m_s, mean_wind_m_s=0.0, wind_dir_deg=0.0):
    tp = mil_hdbk_1797_low_altitude(altitude_m, wind20_m_s) if wind20_m_s > 0 else None
    d = math.radians(wind_dir_deg)
    mean = [mean_wind_m_s * math.cos(d), mean_wind_m_s * math.sin(d), 0.0]
    return WindField(mean, TurbulenceField(seed=seed, **tp) if tp else None,
                     shear_exponent_nd=0.14, shear_ref_height_m=altitude_m)


def run_delivery(seed: int, pad_id: str | None = None, wind20_m_s: float = 7.7, mean_wind_m_s: float = 3.0,
                 wind_dir_deg: float | None = None, airspeed_m_s: float | None = None, t_max_s: float = 200.0,
                 cfg_path: str = CFG_PATH, release_lead_m: float = 60.0, log: bool = False) -> dict:
    cfg = load_cfg(cfg_path)
    env = cfg["environment"]
    rng = np.random.default_rng(seed)
    qcfg = quad_params(cfg["small_quadcopter"])
    qp = cfg["quadplane"]
    V = airspeed_m_s or qp["min_safe_cruise_airspeed_m_s"]
    scene = make_street(seed=7, cruise_airspeed_m_s=V * qp.get("turn_speed_factor_nd", 1.15),
                        max_bank_deg=qp.get("turn_bank_deg", 25.0))
    route = scene.route
    if pad_id is None:
        pad = scene.pads[int(rng.integers(len(scene.pads)))]
    else:
        pad = scene.pad(pad_id)
    wdir = float(rng.uniform(0, 360)) if wind_dir_deg is None else wind_dir_deg
    wind = build_wind(cfg, int(rng.integers(1 << 30)), route.altitude_m, wind20_m_s, mean_wind_m_s, wdir)
    common = CommonModeError(0.01, 60.0, int(rng.integers(1 << 30)))
    start = np.array([route.leg_start_x_m - 100.0, route.leg_y_m, route.altitude_m])
    ms = Mothership(qp, env, rng, route, start, 0.0, V, [True, True], gnss_common=common,
                    quad_mass_kg=qcfg["mass_loaded_kg"], quad_cda_m2=qcfg["drag_area_m2"])
    ms.stack.ap.airspeed_cmd = V
    ms.stack.ap.alt_cmd = route.altitude_m
    hold = np.array([pad.center_top_m[0] + 40.0, 0.0, route.altitude_m - 6.0])
    mission = DeliveryMission(cfg["mission"], pad, scene.half_width_m, hold, qcfg["drag_area_m2"],
                              env["air_density_kg_m3"], qcfg["mass_loaded_kg"], qcfg["mass_empty_kg"])
    qa = QuadAgent("q0", qcfg, env, rng, ms, 0, qp["rail"]["mount_pitch_deg"], mission, gnss_common=common)
    world = DockingWorld(cfg, scene, ms, wind, [qa])
    state = {"phase": "wait", "t_spin": None, "release": None, "ms_pitch_before": None}

    def script(w):
        t = w.t
        if state["phase"] == "wait":
            # release when the mothership's own estimate passes the release point on leg 0
            if ms.follower.leg == 0 and ms.stack.nav.pos[0] >= pad.center_top_m[0] - release_lead_m:
                mission._go("SPINUP", t)
                q = qa.stack.sensors.ahrs.read(t)
                mission.mount_q = q.value.copy() if q is not None else qa.mount_q_rel
                mission.yaw = 0.0
                state["phase"], state["t_spin"] = "spin", t
        elif state["phase"] == "spin" and t - state["t_spin"] >= cfg["mission"].get("spinup_ramp_s", 1.2) + 0.5:
            qa.attached = False
            ms.release(0, t)
            mission.release_pos = qa.stack.nav.pos.copy()
            msg = ms.link.read(t)
            mission.release_vel = msg.value["vel_m_s"] if msg is not None else qa.stack.nav.vel.copy()
            mission.yaw = math.atan2(mission.release_vel[1], mission.release_vel[0])
            mission._go("SEPARATE", t)
            state["phase"], state["release"] = "free", t
            state["ms_alt_at_release"] = ms.body.pos[2]
    world.hooks.append(script)

    track = {"ms_alt_dev_max_m": 0.0, "ms_airspeed_min_m_s": 99.0, "ms_airspeed_max_m_s": 0.0,
             "ms_alpha_max_deg": -99.0, "ms_bank_max_deg": 0.0, "ms_alt_dev_after_release_max_m": 0.0,
             "ms_pitch_dev_after_release_max_deg": 0.0}
    rows = []

    def logger(w):
        b = ms.body
        la = ms.stack.phys.last
        if w.t > 10.0:
            track["ms_alt_dev_max_m"] = max(track["ms_alt_dev_max_m"], abs(b.pos[2] - route.altitude_m))
            track["ms_airspeed_min_m_s"] = min(track["ms_airspeed_min_m_s"], la["airspeed_m_s"])
            track["ms_airspeed_max_m_s"] = max(track["ms_airspeed_max_m_s"], la["airspeed_m_s"])
            track["ms_alpha_max_deg"] = max(track["ms_alpha_max_deg"], math.degrees(la["alpha_rad"]))
            track["ms_bank_max_deg"] = max(track["ms_bank_max_deg"], abs(math.degrees(math.atan2(b.R[2, 1], b.R[2, 2]))))
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, b.R[2, 0]))))
        if state["phase"] in ("wait", "spin"):
            state.setdefault("pitch_hist", []).append(pitch)
            state["pitch_hist"] = state["pitch_hist"][-10:]          # last 1 s before release
        if state["release"] is not None and w.t - state["release"] < 10.0:
            ref = float(np.mean(state["pitch_hist"]))
            track["ms_alt_dev_after_release_max_m"] = max(track["ms_alt_dev_after_release_max_m"],
                                                           abs(b.pos[2] - route.altitude_m))
            track["ms_pitch_dev_after_release_max_deg"] = max(track["ms_pitch_dev_after_release_max_deg"],
                                                               abs(pitch - ref))
        if log:
            qb = qa.stack.body
            rows.append({"t_s": w.t, "ms_x_m": b.pos[0], "ms_y_m": b.pos[1], "ms_z_m": b.pos[2],
                         "ms_airspeed_m_s": la["airspeed_m_s"], "ms_alpha_deg": math.degrees(la["alpha_rad"]),
                         "q_x_m": qb.pos[0], "q_y_m": qb.pos[1], "q_z_m": qb.pos[2],
                         "q_speed_m_s": float(np.linalg.norm(qb.vel)), "q_state_label": mission.state,
                         "q_mass_kg": qb.mass_kg})
        return None

    def stop(w):
        return mission.state == "HOLD"

    # the drink comes off at delivery
    orig_step = mission.step

    def step_with_mass(stack, t, msg, feet):
        before = mission.delivered
        orig_step(stack, t, msg, feet)
        if mission.delivered and not before:
            qa.stack.phys.set_mass(qcfg["mass_empty_kg"])
            qa.stack.fc.set_mass_estimate(qcfg["mass_empty_kg"])
    mission.step = step_with_mass

    done = world.run_until(t_max_s, stop=stop, log_every_s=0.1, logger=logger)
    ev = dict((e[1], e[0]) for e in mission.events)
    land_err = None
    if "LANDED" in ev:
        land_err = None
    out = {
        "seed_id": seed, "pad_label": pad.pad_id, "pad_height_m": float(pad.center_top_m[2]),
        "wind20_m_s": wind20_m_s, "mean_wind_m_s": mean_wind_m_s, "wind_dir_deg": wdir,
        "airspeed_cmd_m_s": V, "reached_hold_bool": bool(done), "landed_bool": "LANDED" in ev,
        "delivered_bool": "DRINK_DELIVERED" in ev, "took_off_bool": "TAKEOFF" in ev,
        "t_release_s": state["release"] or -1.0, "t_landed_s": ev.get("LANDED", -1.0),
        "t_takeoff_s": ev.get("TAKEOFF", -1.0), "t_hold_s": ev.get("HOLD", -1.0),
        "strikes_count": len(qa.strikes), "airframe_contacts_count": len(qa.contacts),
        "building_hits_count": qa.building_hits,
        "min_clear_tail_m": qa.min_clear.get("tail", math.inf), "min_clear_lift_prop_m": qa.min_clear.get("lift_prop", math.inf),
        "min_clear_pusher_m": qa.min_clear.get("pusher", math.inf),
        "ms_mass_end_kg": ms.mass_kg(),
    }
    out.update(track)
    if log:
        out["_rows"] = rows
        out["_events"] = mission.events + ms.events
    return out
