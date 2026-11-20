"""Hexacopter delivery: scenarios, sensors, mapping/planning, and one full trial."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from delivery import geom as G  # noqa: E402
from delivery import scenarios as S  # noqa: E402
from delivery.mapping import OccupancyGrid  # noqa: E402
from delivery.trial import load_cfg, run_trial  # noqa: E402


def test_scenarios_reproducible_and_labelled():
    for sid in S.SCENARIOS:
        a, b = S.make(sid, 3), S.make(sid, 3)
        assert np.array_equal(a.scene.blo, b.scene.blo) and a.corridor_3m_bool == b.corridor_3m_bool
        assert np.allclose(a.beacon_railing_m, 0.0)            # beacon frame
    assert not any(S.make("S9", k).corridor_3m_bool for k in range(10))


def test_raycast_materials():
    s = G.Scene()
    s.box([5, -5, -5], [5.02, 5, 5], G.GLASS, G.K_GLASS)
    s.box([8, -5, -5], [9, 5, 5], G.CONCRETE, G.K_STRUCTURE)
    s.freeze()
    o = np.zeros((1, 3))
    d = np.array([[1.0, 0, 0]])
    mode = np.ones(G.N_MAT, np.int64)
    mode[G.GLASS] = 0                                          # transparent
    r, m, _, _ = G.raycast(o, d, np.array([20.0]), np.zeros(1), *s.arrays(), mode, np.full(G.N_MAT, 20.0),
                           np.ones(G.N_MAT), 0.0)
    assert m[0] == G.CONCRETE and abs(r[0] - 8.0) < 1e-6
    mode[G.GLASS] = 2                                          # opaque, unreadable
    r, m, _, _ = G.raycast(o, d, np.array([20.0]), np.zeros(1), *s.arrays(), mode, np.full(G.N_MAT, 20.0),
                           np.ones(G.N_MAT), 0.0)
    assert m[0] == G.GLASS and abs(r[0] - 5.0) < 1e-6


def test_grid_margins_and_plan():
    cfg = load_cfg()["delivery"]
    g = OccupancyGrid(np.array([-5.0, -5, -5]), np.array([5.0, 5, 5]), cfg)
    g.mark_occupied(np.array([0.0, 0, 0]), 0)                  # solid: 1 m margin
    assert g.blocked(g.idx([0.6, 0, 0])) and not g.blocked(g.idx([1.4, 0, 0]))
    g.mark_occupied(np.array([0.0, 3.0, 0]), 2)                # wire: 2 m margin
    assert g.blocked(g.idx([0.0, 1.3, 0]))
    p = g.plan(np.array([-4.0, 0, 0]), np.array([4.0, 0, 0]), 0.6, w_h=2.0)
    assert p is not None
    d = np.linalg.norm(p - np.array([0.0, 0, 0]), axis=1)
    assert d.min() > 0.8                                       # kept its margin (to cell resolution)


def test_one_delivery_s1():
    r = run_trial("S1", 2)["row"]
    assert r["delivered_bool"] and r["pickup_bool"] and r["collisions_count"] == 0
