"""Phase 2 checks for the hexacopter (addendum section 1) and its sensors.

    python -m delivery.checks_phase2        # prints a table, writes docking/results/phase2_hex/checks.json

H1 hover thrust equals weight (loaded and empty), within 1 %.
H2 hover power from the simulated rotor thrusts equals the sizing calculation, within 3 %.
H3 one rotor failed, for each of the six rotors, loaded, in hover with a 3 m/s wind:
   holds height within 0.5 m and stays level (tilt < 20 deg) for 20 s; heading spins.
H4 one rotor failed while flying at 3 m/s: same limits.
S1 radar sees a 3 mm metal wire to 10 m when perpendicular, less at shallow angles, never
   a non-metal line; sees glass to 8 m, not at 9 m.
S2 infrared: no reading on glass; 4 m range in shade, 1.5 m in sun; no 3 mm wire.
S3 ranging radio: blocked by concrete; works in line of sight.
L  corridor-label fractions per scenario (100 seeds each).
The H checks use true state in the outer loop (the point is controllability, not navigation).
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

_SIM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SIM not in sys.path:
    sys.path.insert(0, _SIM)

from flight.frames import GRAVITY_M_S2, euler_from_rot, tilt_rad  # noqa: E402

from . import geom as G  # noqa: E402
from . import scenarios as SC  # noqa: E402
from .hex import HexVehicle  # noqa: E402
from .sensors_hex import IRArray, Radar, RangingRadio  # noqa: E402
from .trial import load_cfg  # noqa: E402

OUT = os.path.join(_SIM, "docking", "results", "phase2_hex")


def _empty_scene():
    s = G.Scene()
    s.box([-500, -500, -1.0], [500, 500, 0.0], G.CONCRETE, G.K_GROUND)
    return s.freeze()


def fly(mass_kg, wind=np.zeros(3), v_cmd=np.zeros(3), fail=None, T=25.0, seed=0):
    cfg = load_cfg()
    h, env = cfg["hexacopter"], cfg["environment"]
    rng = np.random.default_rng(seed)
    hx = HexVehicle(h, env, rng, np.array([0.0, 0.0, 20.0]), 0.0, mass_kg)
    scene = _empty_scene()
    dt, gdt = env["physics_dt_s"], env["guidance_dt_s"]
    n = int(round(gdt / dt))
    t, vi = 0.0, np.zeros(3)
    p_ref = np.array([0.0, 0.0, 20.0])
    log = []
    while t < T:
        b = hx.body
        if fail is not None and hx.failed_rotor is None and t >= fail:
            hx.fail_rotor(t, fail_idx)
        p_ref = p_ref + v_cmd * gdt
        ev = v_cmd + 1.0 * (p_ref - b.pos) - b.vel
        vi = np.clip(vi + ev * gdt, -3, 3)
        a = 2.2 * ev + 0.4 * vi
        an = np.linalg.norm(a)
        if an > 6.0:
            a *= 6.0 / an
        yaw = euler_from_rot(b.R)[2] if hx.yaw_free else 0.0
        hx.fc.set_accel(t, a, yaw)
        for _ in range(n):
            hx.step(t, wind, scene)
            t += dt
        T_tot = float(np.sum(hx.phys.motors.thrust_n * hx.phys.motors.healthy_scale))
        log.append((t, *b.pos, math.degrees(tilt_rad(b.R)), b.omega[2], T_tot,
                    int(np.sum(hx.phys.motors.thrust_n >= hx.phys.tmax * 0.999)), hx.energy_j))
    return np.array(log), hx


fail_idx = 0


def main():
    global fail_idx
    cfg = load_cfg()
    h = cfg["hexacopter"]
    res = {}
    rows = []
    # H1/H2
    for label, m in (("loaded", h["mass_loaded_kg"]), ("empty", h["mass_empty_kg"])):
        lg, hx = fly(m, T=10.0)
        tail = lg[lg[:, 0] > 6.0]
        T_mean = float(tail[:, 6].mean())
        W = m * GRAVITY_M_S2
        P_sim = float((tail[-1, 8] - tail[0, 8]) / (tail[-1, 0] - tail[0, 0]))
        from docking.hex_sizing import hover_power_w
        A = 6 * math.pi * (h["prop_diameter_m"] / 2) ** 2
        # per-rotor momentum theory, as the sim (equal thrust per rotor)
        P_calc = hover_power_w(W, A, h["figure_of_merit_nd"], h["motor_esc_efficiency_nd"], h["cage_loss_frac"]) + h["avionics_power_w"]
        rows.append(dict(check_label=f"H1 hover thrust = weight ({label})", value=T_mean / W, target="1.00 +/- 0.01",
                         pass_bool=abs(T_mean / W - 1) < 0.01))
        rows.append(dict(check_label=f"H2 hover power sim vs calc ({label})", value=P_sim / P_calc,
                         target="1.00 +/- 0.03", pass_bool=abs(P_sim / P_calc - 1) < 0.03,
                         detail=dict(power_sim_w=P_sim, power_calc_w=P_calc,
                                     endurance_s=h["battery_wh"] * h["battery_usable_frac"] * 3600 / P_sim)))
    # H3/H4
    for case, wind, vcmd in (("H3 hover, 3 m/s wind", np.array([3.0, 0, 0]), np.zeros(3)),
                             ("H4 3 m/s forward", np.zeros(3), np.array([3.0, 0, 0]))):
        worst = dict(dz=0.0, tilt=0.0, yaw_rate=0.0, sat=0, drift=0.0)
        per = []
        for i in range(6):
            fail_idx = i
            lg, hx = fly(h["mass_loaded_kg"], wind=wind, v_cmd=vcmd, fail=3.0, T=25.0)
            after = lg[lg[:, 0] > 3.0]
            dz = float(np.max(np.abs(after[:, 3] - 20.0)))
            tilt = float(np.max(after[after[:, 0] > 3.5, 4]))
            yr = float(np.max(np.abs(after[:, 5])))
            ref_x = 3.0 * after[:, 0] if vcmd[0] else 0.0
            drift = float(np.max(np.hypot(after[:, 1] - ref_x, after[:, 2])))
            sat = int(after[:, 7].max())
            per.append(dict(rotor_index=i, height_dev_max_m=dz, tilt_max_deg=tilt, yaw_rate_max_rad_s=yr,
                            horizontal_dev_max_m=drift, motors_saturated_max_count=sat))
            worst = dict(dz=max(worst["dz"], dz), tilt=max(worst["tilt"], tilt), yaw_rate=max(worst["yaw_rate"], yr),
                         sat=max(worst["sat"], sat), drift=max(worst["drift"], drift))
        n_ok = sum(1 for q in per if q["height_dev_max_m"] < 0.5 and q["tilt_max_deg"] < 20.0)
        rows.append(dict(check_label=f"{case}, one rotor out: rotors held level", value=float(n_ok),
                         target="6 of 6 (height dev < 0.5 m, tilt < 20 deg)", pass_bool=n_ok == 6,
                         detail=dict(spin_pattern_label=h["spin_pattern_label"], per_rotor=per)))
    # sensors
    rng = np.random.default_rng(1)
    scfg = h["sensors"]
    det = {}
    def wire_scan(p0, p1, metal):
        s_ = G.Scene()
        s_.capsule(p0, p1, 0.0015, G.WIRE_METAL if metal else G.WIRE_NONMETAL,
                   G.K_WIRE_METAL if metal else G.K_WIRE_NONMETAL)
        s_.box([-500, -500, -60], [500, 500, -59], G.CONCRETE, G.K_GROUND)
        s_.freeze()
        rd = Radar(scfg["radar"], 0.0, rng, s_)
        rd.scan(0.0, np.zeros(3), np.eye(3), np.zeros(3), s_)
        return bool((rd.read(1.0)[0][1]["true_mat"] == G.WIRE_METAL).any())
    for metal in (True, False):
        nm = "metal" if metal else "nonmetal"
        for d in (5.0, 9.5, 10.5):          # across the line of sight (a specular point exists)
            det[f"{nm}_across_{d}m"] = wire_scan([d, -6, 0], [d, 6, 0], metal)
        for side in (0.5, 1.5, 3.0):        # along the line of sight, 3-15 m ahead, offset sideways
            det[f"{nm}_along_offset_{side}m"] = wire_scan([3.0, side, 0], [15.0, side, 0], metal)
    s1 = (det["metal_across_9.5m"] and not det["metal_across_10.5m"] and det["metal_across_5.0m"]
          and not det["metal_along_offset_0.5m"] and not any(v for k, v in det.items() if k.startswith("nonmetal")))
    rows.append(dict(check_label="S1 radar wire detection by angle and material", value=None,
                     target="metal across: to 10 m; metal along the approach: not seen; non-metal: never",
                     pass_bool=s1, detail=det))
    gl = {}
    for rr in (7.5, 8.5):
        s = G.Scene()
        s.box([rr, -5, -5], [rr + 0.02, 5, 5], G.GLASS, G.K_GLASS)
        s.box([-500, -500, -60], [500, 500, -59], G.CONCRETE, G.K_GROUND)
        s.freeze()
        rd = Radar(scfg["radar"], 0.0, rng, s)
        rd.scan(0.0, np.zeros(3), np.eye(3), np.zeros(3), s)
        m = rd.read(1.0)[0][1]
        gl[f"radar_glass_{rr}m"] = bool((m["true_mat"] == G.GLASS).any())
    ir_res = {}
    for sun in (False, True):
        for mat, rr in ((G.GLASS, 1.0), (G.CONCRETE, 1.4), (G.CONCRETE, 3.5), (G.WIRE_METAL, 0.8)):
            s = G.Scene()
            if mat == G.WIRE_METAL:
                s.capsule([rr, -3, -3], [rr, 3, 3], 0.0015, mat, G.K_WIRE_METAL)
            else:
                s.box([rr, -5, -5], [rr + 0.05, 5, 5], mat, G.K_GLASS if mat == G.GLASS else G.K_STRUCTURE)
            s.freeze()
            ir = IRArray(scfg["ir"], rng, sun, np.array([0, 0, 1.0]))
            ir.scan(0.0, np.zeros(3), np.eye(3), s)
            m = ir.read(1.0)[0][1]
            fwd = ir.sensor_of_ray == 0
            ir_res[f"{'sun' if sun else 'shade'}_{G.MAT_NAMES[mat]}_{rr}m"] = dict(
                valid=int(m["valid"][fwd].sum()), invalid=int(m["invalid"][fwd].sum()))
    s2 = (ir_res["shade_glass_1.0m"]["valid"] == 0 and ir_res["shade_concrete_3.5m"]["valid"] > 0
          and ir_res["sun_concrete_3.5m"]["valid"] == 0 and ir_res["sun_concrete_1.4m"]["valid"] > 0
          and ir_res["shade_wire_metal_0.8m"]["valid"] == 0 and gl["radar_glass_7.5m"] and not gl["radar_glass_8.5m"])
    rows.append(dict(check_label="S2 infrared (glass, sun, thin wire) and radar glass range", value=None,
                     target="no glass reading; 4 m shade, 1.5 m sun; no wire; radar glass 8 m", pass_bool=s2,
                     detail=dict(ir=ir_res, radar=gl)))
    s = G.Scene()
    s.box([5, -5, -5], [5.3, 5, 5], G.CONCRETE, G.K_STRUCTURE)
    s.freeze()
    rr_ = RangingRadio(scfg["ranging_radio"], rng, np.array([10.0, 0, 0]))
    for k in range(200):
        rr_.sample(k * 0.1, np.zeros(3), np.eye(3), s, 99.0)
    n_out = len(rr_.read(1e9))
    rr2 = RangingRadio(scfg["ranging_radio"], rng, np.array([10.0, 20.0, 0]))
    for k in range(200):
        rr2.sample(k * 0.1, np.zeros(3), np.eye(3), s, 99.0)
    n_los = len(rr2.read(1e9))
    rows.append(dict(check_label="S3 ranging radio blocked by concrete", value=n_out / 200,
                     target="blocked: only reflections (~30 %); line of sight: ~all", pass_bool=n_out < 90 and n_los > 190,
                     detail=dict(blocked_readings_frac=n_out / 200, los_readings_frac=n_los / 200)))
    # corridor labels
    lab = {}
    for sid in SC.SCENARIOS:
        lab[sid] = float(np.mean([SC.make(sid, k).corridor_3m_bool for k in range(100)]))
    rows.append(dict(check_label="L corridor-3m label fraction per scenario (100 seeds)", value=None,
                     target="S9 = 0", pass_bool=lab["S9"] == 0.0, detail=lab))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "checks.json"), "w") as f:
        json.dump(rows, f, indent=1, default=float)
    for r in rows:
        v = "" if r["value"] is None else f"{r['value']:.3f}"
        print(f"{'PASS' if r['pass_bool'] else 'FAIL'}  {r['check_label']:58s} {v:>8s}  ({r['target']})")
    print(json.dumps({r["check_label"]: r.get("detail") for r in rows if r.get("detail")}, indent=1, default=float)[:4000])


if __name__ == "__main__":
    main()
