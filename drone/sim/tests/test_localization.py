"""Localization fabric tests (Phase 2): GPS healthy / denied-drift / spoof / recover."""
from __future__ import annotations

import numpy as np
import pytest

from team_world import TeamWorld, KinematicWorld, reactive_goto_controller
from localization import LocalizationFabric, LocMode


def _world(typ="quad"):
    w = TeamWorld(KinematicWorld(), dt=0.1)
    w.add_agent("a", typ, [0, 0, 2 if typ == "quad" else 0], yaw=0.0, goal=[20, 0, 2])
    f = LocalizationFabric(seed=1)
    w.localization = f
    return w, f


def test_gps_ok_high_confidence_small_error():
    w, f = _world()
    for _ in range(20):
        f.update(w)
    assert f.confidence(w.agents["a"]) > 0.95
    assert f.error(w.agents["a"]) < 0.3   # only sensor noise


def test_denied_drift_grows_and_confidence_decays():
    w, f = _world("quad")
    f.gps_loss("a")
    errs, confs = [], []
    for _ in range(200):
        f.update(w)
        errs.append(f.error(w.agents["a"]))
        confs.append(f.confidence(w.agents["a"]))
    # drift accumulates (random walk) and confidence falls below GPS-healthy level
    assert errs[-1] > errs[0]
    assert confs[-1] < 0.9
    assert confs[-1] < confs[0]


def test_rover_drift_is_planar():
    w, f = _world("rover")
    f.gps_loss("a")
    for _ in range(200):
        f.update(w)
    bias = f._state("a").bias
    assert abs(bias[2]) < 1e-9        # ground class never drifts in z


def test_rover_drifts_slower_than_quad():
    # rover drift 0.05 m/s vs quad 0.20 m/s -> rover accumulates less
    wq, fq = _world("quad"); fq.gps_loss("a")
    wr, fr = _world("rover"); fr.gps_loss("a")
    for _ in range(300):
        fq.update(wq); fr.update(wr)
    assert fr.error(wr.agents["a"]) < fq.error(wq.agents["a"])


def test_spoof_is_confidently_wrong():
    w, f = _world("quad")
    f.gps_spoof("a", vel=[0.3, 0.0, 0.0])
    for _ in range(100):  # 10 s
        f.update(w)
    # large error but confidence stays high -> the dangerous case
    assert f.error(w.agents["a"]) > 2.0
    assert f.confidence(w.agents["a"]) >= 0.9


def test_restore_recovers_confidence():
    w, f = _world("quad")
    f.gps_loss("a")
    for _ in range(200):
        f.update(w)
    assert f.confidence(w.agents["a"]) < 0.9
    f.restore("a")
    for _ in range(50):
        f.update(w)
    assert f.confidence(w.agents["a"]) > 0.95


def test_belief_drives_navigation_drift():
    """Under denial, the agent's believed goal vector diverges from truth."""
    w, f = _world("quad")
    a = w.agents["a"]
    f.gps_loss("a")
    for _ in range(150):
        f.update(w)
    believed = f.believed_pos(a)
    assert np.linalg.norm(believed - a.pos) > 0.5
