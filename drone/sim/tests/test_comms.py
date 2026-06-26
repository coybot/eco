"""Comms fabric tests (Phase 1): range, jamming, LOS, blackout, bandwidth, latency."""
from __future__ import annotations

import numpy as np
import pytest

from team_world import TeamWorld, KinematicWorld, Box
from comms import CommsFabric, MsgType, Transport


def _two(dist=5.0, typ="quad"):
    w = TeamWorld(KinematicWorld(), dt=0.1)
    w.add_agent("a", typ, [0, 0, 2], yaw=0.0)
    w.add_agent("b", typ, [dist, 0, 2], yaw=0.0)
    f = CommsFabric(seed=0)
    w.comms = f
    return w, f


def _pump(w, ticks=3):
    """Advance enough ticks for latency-delayed messages to deliver."""
    for _ in range(ticks):
        w.comms.deliver(w)
        w.t += w.dt


def test_in_range_delivers():
    w, f = _two(dist=5.0)
    f.send(w, "a", MsgType.POSITION, {"p": [0, 0, 2]})
    _pump(w)
    assert any(m.sender == "a" for m in w.agents["b"].inbox)
    assert f.stats.delivered >= 1


def test_out_of_range_drops():
    # rover range is 120m; place beyond it
    w, f = _two(dist=200.0, typ="rover")
    f.send(w, "a", MsgType.HEARTBEAT, {})
    _pump(w)
    assert w.agents["b"].inbox == []
    assert f.stats.dropped_range == 1


def test_blackout_blocks_mesh():
    w, f = _two(dist=5.0)
    f.set_blackout(True)
    f.send(w, "a", MsgType.SIGHTING, {"x": 1})
    _pump(w)
    assert w.agents["b"].inbox == []
    assert f.stats.delivered == 0


def test_jamming_zone_nulls_link():
    w, f = _two(dist=10.0)
    f.add_jamming_zone([5, 0, 2], 3.0)   # straddles the path midpoint
    f.send(w, "a", MsgType.POSITION, {})
    _pump(w)
    assert w.agents["b"].inbox == []
    assert f.stats.dropped_jam == 1


def test_los_occlusion_for_ground_class():
    # rover requires LOS; put a wall between a and b
    w = TeamWorld(KinematicWorld(
        static_obstacles=[Box(np.array([5, 0, 1.0], np.float32),
                              np.array([0.5, 5.0, 3.0], np.float32))]), dt=0.1)
    w.add_agent("a", "rover", [0, 0, 0], yaw=0.0)
    w.add_agent("b", "rover", [10, 0, 0], yaw=0.0)
    f = CommsFabric(seed=0)
    w.comms = f
    f.send(w, "a", MsgType.POSITION, {})
    _pump(w)
    assert w.agents["b"].inbox == []
    assert f.stats.dropped_los == 1


def test_quad_ignores_los_occlusion():
    # quad does NOT require LOS -> same wall does not block
    w = TeamWorld(KinematicWorld(
        static_obstacles=[Box(np.array([5, 0, 1.0], np.float32),
                              np.array([0.5, 5.0, 3.0], np.float32))]), dt=0.1)
    w.add_agent("a", "quad", [0, 0, 2], yaw=0.0)
    w.add_agent("b", "quad", [10, 0, 2], yaw=0.0)
    f = CommsFabric(seed=0)
    w.comms = f
    f.send(w, "a", MsgType.POSITION, {})
    _pump(w)
    assert f.stats.dropped_los == 0


def test_cloud_transport_down_vs_up():
    w, f = _two(dist=5.0)
    f.cloud_up = False
    f.send(w, "a", MsgType.HEARTBEAT, {}, transport=Transport.CLOUD)
    _pump(w)
    assert f.stats.dropped_cloud_down == 1
    f.cloud_up = True
    f.send(w, "a", MsgType.HEARTBEAT, {}, transport=Transport.CLOUD)
    _pump(w)
    assert f.stats.delivered >= 1


def test_bandwidth_cap_drops_excess():
    w, f = _two(dist=3.0)
    cap = int(w.agents["a"].vclass.comms.bandwidth_msgs_per_s)
    for _ in range(cap + 10):
        f.send(w, "a", MsgType.POSITION, {})   # same tick, t not advanced
    assert f.stats.dropped_bandwidth >= 10


def test_latency_delays_delivery():
    w, f = _two(dist=5.0)
    f.send(w, "a", MsgType.POSITION, {})
    f.deliver(w)                      # same instant: base latency not elapsed
    assert w.agents["b"].inbox == []
    w.t += 0.1
    f.deliver(w)
    assert w.agents["b"].inbox != []


def test_reachable_set():
    w, f = _two(dist=5.0)
    assert f.reachable(w, "a") == {"b"}
    f.set_blackout(True)
    assert f.reachable(w, "a") == set()
