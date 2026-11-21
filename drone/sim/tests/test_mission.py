"""Bee35 balcony mission: scenarios, line of sight, and one full mission."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docksim.config import load_cfg  # noqa: E402
from mission.scenarios import SCENARIOS, make, segment_blocked  # noqa: E402
from mission.sim import run_mission  # noqa: E402


def test_scenarios_reproducible_and_sign_at_origin():
    cfg = load_cfg()
    for sid in SCENARIOS:
        a, b = make(sid, 5, cfg), make(sid, 5, cfg)
        assert np.allclose(a.hover_pos, b.hover_pos) and len(a.boxes) == len(b.boxes)
        assert np.allclose(a.anchors.mean(0), 0.0, atol=1e-9)          # sign frame: centroid at the origin
        assert a.slab_under_z - a.railing_top_z > 1.3                     # an entry slot exists


def test_slab_above_shadows_the_sign_from_high_up():
    sc = make("B1", 1, load_cfg())
    A = sc.anchors[2] + np.array([0, 0.01, 0])
    assert not segment_blocked(sc, np.array([0.0, 4.0, sc.entry_z]), A)
    assert segment_blocked(sc, np.array([0.0, 2.0, 30.0]), A)


def test_one_mission_delivers_and_returns():
    r = run_mission(load_cfg(), "B1", 1)
    assert r["delivered_bool"] and r["returned_bool"], r["events_label"]
    assert r["drop_err_m"] < 0.3 and r["handover_err_m"] < 0.5
