"""The onboard loop's team-allocator hook, and the loop flying a fake ArduPilot vehicle.

The closed-loop tests run the whole on-device stack — OnboardL5Runtime with its
smart layer, StaticGoalAllocator, ArduPilotTelemetry, ArduPilotActuator and a
DepthScanSensor — against tests/fake_ardupilot.py, whose MAVLink frames come from
the spec. A sign error anywhere (NED/ENU, body left/right, yaw-rate direction,
EKF origin vs takeoff point) shows up as a vehicle that never arrives.

Run: cd coybot && .venv/bin/python -m pytest eco/drone/common/tests/test_onboard_allocator.py -v
"""
import types

import numpy as np
import pytest

from eco.drone.common.onboard_l5 import (
    OnboardL5Runtime, Pose, StaticGoalAllocator, TeamLinkPeers, normalise_team_msg,
)
from eco.drone.common.ardupilot_l5 import (
    ArduPilotActuator, ArduPilotTelemetry, DepthFrame, DepthScanSensor,
)
from eco.drone.common.team_link import PeerState
from .fake_ardupilot import FakeArduPilot


class _Tel:
    def __init__(self, pos=(0.0, 0.0, 5.0)):
        self.p = Pose(pos_enu=np.array(pos, np.float32), yaw_rad=0.0, vel_enu=np.zeros(3, np.float32))
    def pose(self):
        return self.p


class _Clear:
    def scan(self):
        return np.full(45, 10.0, np.float32)


class _NoPeers:
    def peers(self):
        return []


class _Capture:
    def __init__(self):
        self.actions = []
    def send(self, action, vclass, dt):
        self.actions.append(np.asarray(action, np.float32))


class FakeLink:
    """team_link.TeamLink's public API, recorded."""
    def __init__(self, inbox=(), peers=None):
        self.inbox, self._peers = list(inbox), dict(peers or {})
        self.sent, self.selves = [], []
    def drain_inbox(self):
        out, self.inbox = self.inbox, []
        return out
    def peers(self):
        return dict(self._peers)
    def update_self(self, pos_enu, yaw_deg, vel=None, confidence=1.0, battery_pct=100.0, rtl=False):
        self.selves.append((pos_enu, yaw_deg, vel, confidence))
    def send_task_claim(self, goal_key):
        self.sent.append(("TASK_CLAIM", goal_key))
    def send_task_done(self, claim_id):
        self.sent.append(("TASK_DONE", claim_id))
    def send_sighting(self, target_id, pos_enu):
        self.sent.append(("SIGHTING", target_id, pos_enu))


# --------------------------------------------------------------------------- allocator
def _snap(pos, lock=1.5):
    return types.SimpleNamespace(pos=list(pos), goal_lock_m=lock)


def test_static_allocator_returns_goals_in_order_then_holds():
    a = StaticGoalAllocator([[5, 0, 5], [5, 5, 5]])
    assert a.step(0.0, _snap([0, 0, 5]), {}, []) == ([5.0, 0.0, 5.0], [])
    assert a.step(0.1, _snap([3.0, 0, 5]), {}, [])[0] == [5.0, 0.0, 5.0], "2.0 m out is not there"
    assert a.step(0.2, _snap([4.6, 0, 5]), {}, [])[0] == [5.0, 5.0, 5.0], "within goal_lock_m: next"
    assert not a.done
    assert a.step(0.3, _snap([5, 5, 5]), {}, []) == (None, [])
    assert a.done and a.step(0.4, _snap([0, 0, 0]), {}, [])[0] is None, "done stays done"


def test_static_allocator_explicit_tolerance():
    a = StaticGoalAllocator([[5, 0, 5]], arrive_tol_m=0.3)
    assert a.step(0.0, _snap([4.6, 0, 5]), {}, [])[0] is not None


def test_without_an_allocator_the_loop_is_unchanged():
    """Same goal, same inputs: the allocator-less loop gives the identical action stream."""
    def run(**kw):
        cap = _Capture()
        rt = OnboardL5Runtime("a", "quad", [10.0, 3.0, 5.0], telemetry=_Tel(), sensor=_Clear(),
                              peers=_NoPeers(), actuator=cap, **kw)
        for _ in range(5):
            rt.step(0.1)
        return np.array(cap.actions)
    plain = run()
    allocated = run(allocator=StaticGoalAllocator([[10.0, 3.0, 5.0]]))
    np.testing.assert_array_equal(plain, allocated)


def test_allocator_sets_the_goal_and_none_holds():
    class Hold:
        def step(self, now_s, me, peers, inbox):
            return None, []
    cap = _Capture()
    rt = OnboardL5Runtime("a", "quad", [10.0, 0.0, 5.0], telemetry=_Tel(), sensor=_Clear(),
                          peers=_NoPeers(), actuator=cap, allocator=Hold(), use_smart=False)
    rt.step(0.1)
    assert rt.agent.goal is None
    np.testing.assert_array_equal(cap.actions[-1], np.zeros(4, np.float32))


def test_allocator_sees_me_peers_and_a_normalised_inbox_and_its_messages_go_out():
    seen = {}
    peer = PeerState("q2", [1.0, 2.0, 5.0], 0.0, [0, 0, 0], 0.9, 12.0)

    class Spy:
        def step(self, now_s, me, peers, inbox):
            seen.update(now=now_s, me=me, peers=peers, inbox=inbox)
            return [3.0, 4.0, 5.0], [
                {"type": "TASK_CLAIM", "claim_id": "3,4,5"},
                {"type": "SIGHTING", "target_id": "car", "pos_enu": (1, 1, 0)},
                {"type": "TASK_DONE", "claim_id": "0,0,5"},
                {"type": "BID", "value": 3},
            ]
    link = FakeLink(inbox=[{"type": "TASK_CLAIM", "claim_id": "x", "from": "q2", "t": 11.5},
                           {"type": "SIGHTING", "target_id": "car", "stamp": 9.0, "from": "q2", "t": 11.6}],
                    peers={"q2": peer})
    rt = OnboardL5Runtime("q1", "quad", None, telemetry=_Tel(), sensor=_Clear(), peers=_NoPeers(),
                          actuator=_Capture(), allocator=Spy(), team_link=link, clock=lambda: 42.0)
    rt.step(0.1)
    assert seen["now"] == 42.0 and seen["me"].id == "q1" and seen["peers"] == {"q2": peer}
    assert seen["inbox"][0]["sender"] == "q2" and seen["inbox"][0]["stamp"] == 11.5
    assert seen["inbox"][1]["stamp"] == 9.0, "a payload's own stamp is kept"
    assert seen["inbox"][0]["claim_id"] == "x", "payload fields survive"
    np.testing.assert_allclose(rt.agent.goal, [3.0, 4.0, 5.0])
    assert link.sent == [("TASK_CLAIM", "3,4,5"), ("SIGHTING", "car", [1, 1, 0]), ("TASK_DONE", "0,0,5")]
    assert link.selves and link.selves[-1][0] == [0.0, 0.0, 5.0], "own state published to the team"
    assert link.inbox == [], "the inbox was drained"


def test_a_link_with_send_carries_every_message_unchanged():
    class Link2(FakeLink):
        def send(self, msg):
            self.sent.append(("RAW", dict(msg)))
            return msg["type"] != "TOO_BIG"

    msgs = [{"type": "TASK_CLAIM", "claim_id": "1,2,3", "bid": 0.7},
            {"type": "BID", "value": 1}, {"type": "TOO_BIG", "blob": "x"}]

    class Alloc:
        def step(self, now_s, me, peers, inbox):
            return None, msgs
    link = Link2()
    rt = OnboardL5Runtime("q1", "quad", None, telemetry=_Tel(), sensor=_Clear(), peers=_NoPeers(),
                          actuator=_Capture(), allocator=Alloc(), team_link=link)
    rt.step(0.1)
    assert link.sent == [("RAW", m) for m in msgs], "payload fields the typed senders would drop survive"
    assert rt._dropped_types == {"TOO_BIG"}, "a send that failed is noticed"


def test_allocator_without_a_link_runs_alone():
    rt = OnboardL5Runtime("q1", "quad", None, telemetry=_Tel(), sensor=_Clear(), peers=_NoPeers(),
                          actuator=_Capture(), allocator=StaticGoalAllocator([[9, 0, 5]]))
    rt.step(0.1)
    np.testing.assert_allclose(rt.agent.goal, [9, 0, 5])


def test_normalise_keeps_everything():
    m = {"type": "POSITION", "from": "x", "t": 1.0, "pos_enu": [1, 2, 3]}
    n = normalise_team_msg(m)
    assert n["sender"] == "x" and n["stamp"] == 1.0 and n["pos_enu"] == [1, 2, 3] and "sender" not in m


def test_team_link_peers_prefer_the_class_a_teammate_announces():
    link = FakeLink(peers={
        "r1": PeerState("r1", [1, 2, 0], 0, [0, 0, 0], 1.0, 1.0, vclass_name="rover"),
        "x": PeerState("x", [1, 2, 0], 0, [0, 0, 0], 1.0, 1.0, vclass_name="hovercraft")})
    peers = {p.id: p for p in TeamLinkPeers(link, {"r1": "quadcopter", "x": "rover"}).peers()}
    assert peers["r1"].vclass_name == "rover", "its own word beats the briefing"
    assert peers["x"].vclass_name == "rover", "an unknown class falls back to the briefing"


def test_team_link_peers_take_their_class_from_the_roster():
    link = FakeLink(peers={"r1": PeerState("r1", [1, 2, 0], 0, [0.5, 0, 0], 0.8, 1.0),
                           "q9": PeerState("q9", [0, 0, 5], 0, [0, 0, 0], 1.0, 1.0)})
    peers = {p.id: p for p in TeamLinkPeers(link, {"r1": "rover"}).peers()}
    assert peers["r1"].vclass_name == "rover" and peers["q9"].vclass_name == "quadcopter"
    np.testing.assert_allclose(peers["r1"].vel_enu, [0.5, 0, 0])
    assert peers["r1"].confidence == pytest.approx(0.8)


# --------------------------------------------------------------------------- closed loop
def _empty_depth(veh):
    """A camera that sees nothing within range: every ray clear, but a fresh frame."""
    z = np.zeros((48, 64), np.uint16)
    return lambda: DepthFrame(z, 0.001, 38.5, 38.5, 32.0, 24.0, veh.t)


def _fly(veh, vclass, goals, ticks, use_smart=True, sensor_kw=None):
    tel = ArduPilotTelemetry(sdk=veh)
    sensor = DepthScanSensor(vclass, _empty_depth(veh), clock=veh.clock, **(sensor_kw or {}))
    tel.sensor_health = sensor.healthy
    act = ArduPilotActuator(vclass, sdk=veh, altitude_fn=tel.altitude_m)
    alloc = StaticGoalAllocator(goals)
    rt = OnboardL5Runtime(veh.sysid and "me", vclass, None, telemetry=tel, sensor=sensor,
                          peers=_NoPeers(), actuator=act, allocator=alloc, use_smart=use_smart)
    track = []
    for _ in range(ticks):
        rt.step(0.1)
        veh.advance(0.1)
        track.append(veh.enu())
        if alloc.done:
            break
    return alloc, np.array(track), act


def test_onboard_loop_flies_a_fake_arducopter_through_its_goals():
    # Took off 3 m north / 2 m west of the EKF origin, hovering 5 m up, nose 40 deg east of north.
    veh = FakeArduPilot("quad", home_ned=(3.0, -2.0, 0.0), pos_ned=(3.0, -2.0, -5.0), heading=0.7)
    goals = [[8.0, 4.0, 5.0], [0.0, 8.0, 6.0], [0.0, 0.0, 5.0]]
    alloc, track, act = _fly(veh, "quadcopter", goals, 1500)
    assert alloc.done, f"did not finish: at {track[-1]} after {len(track)} ticks, goal {alloc.index}"
    for g in goals:
        assert np.linalg.norm(track - np.array(g), axis=1).min() < 1.5, f"never reached {g}"
    assert act.last_refusal is None and len(veh.setpoints) == len(track)


def test_onboard_loop_drives_a_fake_ardurover_to_its_goal():
    # Facing north (ENU yaw +90 deg); goal is behind-right at first, so it must turn the right way.
    veh = FakeArduPilot("rover", home_ned=(0.0, 0.0, 0.0), pos_ned=(0.0, 0.0, 0.0), heading=0.0)
    goals = [[6.0, -3.0, 0.0], [6.0, 4.0, 0.0]]
    alloc, track, _ = _fly(veh, "rover", goals, 1500, sensor_kw={"mount_height_m": 0.3})
    assert alloc.done, f"did not finish: at {track[-1]}"
    assert np.linalg.norm(track[-1, :2] - np.array([6.0, 4.0])) < 1.5


def test_loop_stops_commanding_when_the_vehicle_leaves_guided():
    veh = FakeArduPilot("quad", pos_ned=(3.0, -2.0, -5.0))
    tel = ArduPilotTelemetry(sdk=veh)
    act = ArduPilotActuator("quadcopter", sdk=veh)
    rt = OnboardL5Runtime("me", "quadcopter", None, telemetry=tel,
                          sensor=DepthScanSensor("quadcopter", _empty_depth(veh), clock=veh.clock),
                          peers=_NoPeers(), actuator=act, allocator=StaticGoalAllocator([[20, 0, 5]]))
    rt.step(0.1)
    n = len(veh.setpoints)
    veh.custom_mode = 5    # the pilot switched to LOITER
    rt.step(0.1)
    assert len(veh.setpoints) == n and "GUIDED" in act.last_refusal
