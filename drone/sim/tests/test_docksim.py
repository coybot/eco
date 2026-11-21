"""Passive four-pocket dock (MuJoCo): geometry, magnets, motion statistics, launch and recovery."""
import os
import sys

import mujoco
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docksim.config import G_M_S2, load_cfg  # noqa: E402
from docksim.motion import MothershipMotion  # noqa: E402
from docksim.scene import DroneSpec, build  # noqa: E402
from docksim import checks  # noqa: E402
from docksim.trial import run_launch, run_recovery  # noqa: E402


def test_seated_drone_is_stable_and_post_in_slot():
    cfg = load_cfg()
    px = cfg["dock"]["pocket_x_m"][1]
    m, _ = build(cfg, [DroneSpec("a", 1, (px, 0.0, 0.0))])
    d = mujoco.MjData(m)
    for _ in range(2000):
        mujoco.mj_step(m, d)
    p = d.xpos[m.body("a").id]
    assert abs(p[0] - px) < 1e-3 and abs(p[1]) < 1e-3 and abs(p[2]) < 2e-3


def test_fences_funnel_a_compliant_drone_into_the_slot():
    """A drone free along x, pushed with 0.5 N towards the post, seats from up to +-0.09 m off."""
    cfg = load_cfg()
    px = cfg["dock"]["pocket_x_m"][1]
    for dx in (-0.09, 0.06):
        m, _ = build(cfg, [DroneSpec("a", 1, (px + dx, 0.17, 0.10))])
        m.opt.gravity[:] = 0
        d = mujoco.MjData(m)
        bid = m.body("a").id
        dof = m.body_dofadr[bid]
        for _ in range(4000):
            R = d.xmat[bid].reshape(3, 3)
            e = 0.5 * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
            d.xfrc_applied[bid, :3] = [0, -50 * (d.xpos[bid][1] - 0.17) - 5 * d.qvel[dof + 1], -0.5]
            d.xfrc_applied[bid, 3:] = -0.5 * e - 0.02 * (R @ d.qvel[dof + 3:dof + 6])
            mujoco.mj_step(m, d)
        p = d.xpos[bid]
        assert abs(p[0] - px) < 0.003 and abs(p[2]) < 0.002


def test_magnets_hold_to_minus_two_g_empty():
    cfg = load_cfg()
    _, lift_a = checks._parked(cfg, False, [0, 2.0 * G_M_S2, 0], None, T=0.6)    # -2.0 g load factor
    _, lift_b = checks._parked(cfg, False, [0, 2.7 * G_M_S2, 0], None, T=0.6)    # -2.7 g
    assert lift_a < 0.002 < lift_b


def test_hover_motion_rms_matches_table():
    cfg = load_cfg()
    hm = cfg["mothership_airframe"]["hover_motion"]
    mm = MothershipMotion(hm["moderate"], hm, np.random.default_rng(4))
    P = []
    for i in range(60000):
        mm.update(0.01)
        if i % 5 == 0:
            P.append(mm.pos_W.copy())
    rms = np.array(P).std(0)
    assert 0.35 < rms[0] < 0.65 and 0.13 < rms[1] < 0.27


def test_launch_leaves_magnets_near_spec_thrust():
    r = run_launch(load_cfg(), dict(hover="calm", wind=0.0, launch_box=True), 1)
    assert r["thrust_at_free_n"] is not None and 18.0 < r["thrust_at_free_n"] < 26.0
    assert r["time_free_to_clear_s"] is not None and r["time_free_to_clear_s"] < 3.0


def test_calm_recovery_seats_the_drone():
    r = run_recovery(load_cfg(), dict(hover="calm", wind=0.0), 2)
    assert r["passed_bool"], r["fail_all_label"]
    assert r["final_pos_err_m"] < 0.005
