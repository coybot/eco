"""Fast tests of the docking scenario code (docking/). The full studies are
docking/phase2.py, phase3.py and phase4.py; these are quick versions."""
import math

import numpy as np
import pytest

from docking.delivery_run import load_cfg
from docking.dock import DockContact, DockDesign
from docking.performance import carry_table, stall_speed_m_s
from docking.quadplane import QuadplaneGeometry
from docking.scene import make_street


@pytest.fixture(scope="module")
def cfg():
    return load_cfg()


def test_street_scene_has_pads_and_route():
    sc = make_street(seed=7)
    assert len(sc.buildings) == 12 and len(sc.pads) >= 12
    assert sc.route.altitude_m > sc.max_roof_m() + 10
    for p in sc.pads:                                  # pads stick out into the street
        assert abs(p.center_top_m[1]) < sc.half_width_m


def test_carry_table_respects_payload(cfg):
    rows = carry_table(cfg)
    ok = [r["quads_count"] for r in rows if r["within_limits_bool"]]
    assert max(ok) == 2                                # 2 quads + rail fit 5 kg; 3 do not
    full = rows[2]
    assert full["full_stall_speed_m_s"] > full["after_release_2_stall_speed_m_s"]


def test_stall_speed_matches_published_when_empty(cfg):
    qp = cfg["quadplane"]
    from flight.aero import FixedWingAero
    clmax, _ = FixedWingAero(qp["aero"]).cl_max()
    v = stall_speed_m_s(qp["mass_airframe_kg"], 1.0, clmax, 1.225)
    assert abs(v - qp["published_stall_speed_m_s"]) < 0.5


def test_geometry_classifies_parts(cfg):
    g = QuadplaneGeometry(cfg["quadplane"]["geometry"])
    c = g.clearances(np.array([-1.02, 0.0, 0.0]))      # pusher hub
    assert c["pusher"] < 1e-9
    c = g.clearances(np.array([-0.86, 0.0, -0.45]))    # inside the ventral fin
    assert c["tail"] < 1e-9
    c = g.clearances(np.array([0.0, 0.0, -2.0]))       # 2 m below: clear of everything
    assert min(c.values()) > 1.5


def test_funnel_guides_tip_toward_axis():
    d = DockDesign(gimbal=False)
    dc = DockContact(d)

    class B:  # minimal body stand-in
        def __init__(self, pos, R):
            self.pos, self.vel, self.omega, self.R = np.asarray(pos, float), np.zeros(3), np.zeros(3), R
    ms = B([0, 0, 0], np.eye(3))
    # place the tip inside the cone, against the wall on +y, half way down
    s = d.depth_m() / 2
    Rs = d.throat_radius_m + s * math.tan(math.radians(d.half_angle_deg))
    tip = np.asarray(d.throat_body_m) + s * d.axis_body() + np.array([0, Rs - 0.005, 0])
    q = B(tip - d.probe_length_m * d.probe_dir_body(), np.eye(3))
    dc.inside = True
    F, M, Fms, p, s_, rho = dc.step(q, ms, 0.0025)
    assert F[1] < 0                                    # pushed back toward the axis
    assert float(F @ d.axis_body()) > 0                # and out along the axis (wall normal)


def test_short_delivery_run_releases_cleanly():
    from docking.delivery_run import run_delivery
    r = run_delivery(3, airspeed_m_s=20.0, t_max_s=30.0)
    assert r["t_release_s"] > 0 and r["strikes_count"] == 0


def test_docking_trial_captures_in_calm_air():
    from docking.docking_trial import run_docking
    r = run_docking(1, DockDesign(), randomize=False, wind20_m_s=0.0, t_max_s=15.0)
    assert r["captured_bool"] and r["strikes_count"] == 0
