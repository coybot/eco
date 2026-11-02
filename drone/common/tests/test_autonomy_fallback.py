"""Cloud-loss fallback: the pre-briefed mission, the onboard run, and the daemon wiring.

The daemon tests pin the two promises that matter: with the option off nothing
about the daemon changes, and with it on the onboard loop takes over only when a
mission is flying, the vehicle is armed in GUIDED, and the control plane has
really gone quiet — and gives the vehicle back on the next operator command.

Run: cd coybot && .venv/bin/python -m pytest eco/drone/common/tests/test_autonomy_fallback.py -v
"""
import json
import sys
import threading
import time
import types
from concurrent.futures import Future
from enum import IntEnum
from pathlib import Path

import numpy as np
import pytest

from eco.drone.common import autonomy_fallback as af
from eco.drone.common.ardupilot_l5 import ArduPilotActuator
from .fake_ardupilot import FakeArduPilot

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MISSION = {
    "region": {"min": [-30, -30], "max": [30, 30], "max_alt_m": 12},
    "roster": [{"id": "drone-test", "vclass": "quadcopter"}, {"id": "rover-1", "vclass": "rover"}],
    "tasks": {"drone-test": [[8, 4, 5], [0, 8, 6]], "rover-1": [[5, 0, 0]]},
    "home": [0, 0, 5],
    "sensor": {"type": "none"},
}


# --------------------------------------------------------------------------- mission file
def test_mission_goals_per_member_with_home_last():
    m = af.PrebriefedMission.from_dict(MISSION)
    assert m.goals_for("drone-test") == [[8, 4, 5], [0, 8, 6], [0, 0, 5]]
    assert m.goals_for("rover-1", aerial=False) == [[5, 0, 0], [0, 0, 0]], "ground goals on the ground"
    assert m.roster_vclass() == {"drone-test": "quadcopter", "rover-1": "rover"}


def test_search_area_is_split_into_lanes_across_the_roster():
    d = dict(MISSION, tasks=None, search_area={"min": [0, 0], "max": [10, 20], "spacing_m": 5, "alt_m": 4},
             return_home=False)
    m = af.PrebriefedMission.from_dict(d)
    mine, theirs = m.goals_for("drone-test"), m.goals_for("rover-1", aerial=False)
    assert [g[1] for g in mine] == [0, 0, 10, 10, 20, 20] and mine[0] == [0, 0, 4]
    assert [g[1] for g in theirs] == [5, 5, 15, 15]


@pytest.mark.parametrize("change,msg", [
    ({"region": None}, "region"),
    ({"tasks": None}, "neither"),
    ({"tasks": [[50, 0, 5]]}, "outside"),
    ({"tasks": [[5, 0, 20]]}, "outside"),
    ({"on_complete": "loiter"}, "on_complete"),
    ({"sensor": {"type": "lidar"}}, "sensor"),
])
def test_bad_missions_are_refused(change, msg):
    d = dict(MISSION, **change)
    with pytest.raises(af.MissionError, match=msg):
        af.PrebriefedMission.from_dict(d).goals_for("drone-test")


def test_a_vehicle_not_on_the_roster_gets_no_mission():
    with pytest.raises(af.MissionError, match="roster"):
        af.PrebriefedMission.from_dict(MISSION).goals_for("someone-else")


def test_config_defaults_off_and_floors_the_silence():
    assert af.FallbackConfig.from_dict(None).enabled is False
    assert af.FallbackConfig.from_dict({"enabled": True, "cloud_silence_s": 2}).cloud_silence_s == af.MIN_SILENCE_S
    c = af.FallbackConfig.from_dict({"mission_file": "m.yaml"}, base_dir="/opt/drone")
    assert c.mission_file == "/opt/drone/m.yaml"


# --------------------------------------------------------------------------- the run
TEAM_LINK = {"key_file": "certs/team.key", "port": 5760}


class _Patched:
    """build_run against the fake vehicle, with team_link swapped for a recorder."""

    def __init__(self, monkeypatch):
        import eco.drone.common.team_link as tl
        self.links = []

        class Link:
            @classmethod
            def from_config(cls, drone_id, cfg, base_dir=None):
                s = cls()
                s.drone_id, s.cfg, s.base_dir = drone_id, cfg, base_dir
                return s
            def __init__(s):
                s.started = s.stopped = False
                self.links.append(s)
            def start(s): s.started = True
            def stop(s): s.stopped = True
            def drain_inbox(s): return []
            def peers(s): return {}
            def update_self(s, *a, **k): pass
            def send(s, msg): return True
        monkeypatch.setattr(tl, "TeamLink", Link)


def _run(veh, mission=MISSION, team_link=TEAM_LINK, **cfg):
    m = af.PrebriefedMission.from_dict(mission)
    run = af.build_run(veh, af.FallbackConfig(enabled=True, **cfg), m, "drone-test", "quadcopter",
                       team_link_cfg=team_link, base_dir="/opt/drone-api")
    run.clock, run.sleep = veh.clock, veh.advance
    return run


def test_fallback_run_flies_the_briefed_goals_then_rtl(monkeypatch):
    p = _Patched(monkeypatch)
    veh = FakeArduPilot("quad", home_ned=(3.0, -2.0, 0.0), pos_ned=(3.0, -2.0, -5.0), heading=0.7)
    run = _run(veh)
    assert run.run() == "complete"
    assert veh.modes == ["RTL"]
    assert np.linalg.norm(np.array(veh.enu()) - [0, 0, 5]) < 1.5, "came back over home first"
    assert p.links[0].started and p.links[0].stopped, "the team link is closed after the run"
    assert p.links[0].cfg == TEAM_LINK and p.links[0].base_dir == "/opt/drone-api"
    assert run.status.current_phase == len(run.status.phases) - 1


def test_fallback_run_rtl_when_it_leaves_the_briefed_region(monkeypatch):
    _Patched(monkeypatch)
    veh = FakeArduPilot("quad", home_ned=(0, 0, 0), pos_ned=(0, 0, -5.0))
    run = _run(veh)
    veh.ned = [40.0, 0.0, -5.0]        # blown 40 m north, outside the 30 m box
    assert run.run() == "rtl" and veh.modes == ["RTL"]


def test_fallback_run_lands_if_rtl_is_refused(monkeypatch):
    _Patched(monkeypatch)
    veh = FakeArduPilot("quad", home_ned=(0, 0, 0), pos_ned=(0, 0, -5.0))
    run = _run(veh)
    veh.ned = [40.0, 0.0, -5.0]
    asked = []
    veh._set_mode_confirmed = lambda mode, timeout=8: asked.append(mode) or mode != "RTL"
    assert run.run() == "land" and asked == ["RTL", "LAND"]


def test_fallback_run_rtl_when_localisation_is_lost(monkeypatch):
    _Patched(monkeypatch)
    veh = FakeArduPilot("quad", home_ned=(0, 0, 0), pos_ned=(0, 0, -5.0))
    run = _run(veh)
    veh.ekf_flags = 1 | 2        # EKF lost horizontal position
    assert run.run() == "rtl"


def test_fallback_run_rtl_when_telemetry_stops(monkeypatch):
    _Patched(monkeypatch)
    veh = FakeArduPilot("quad", home_ned=(0, 0, 0), pos_ned=(0, 0, -5.0))
    run = _run(veh)
    veh.stale.add("LOCAL_POSITION_NED")
    assert run.run() == "rtl"


def test_fallback_run_rtl_after_max_duration(monkeypatch):
    _Patched(monkeypatch)
    veh = FakeArduPilot("quad", home_ned=(0, 0, 0), pos_ned=(0, 0, -5.0))
    run = _run(veh, max_duration_s=1.0)
    assert run.run() == "rtl"


def test_fallback_run_yields_without_changing_mode_when_someone_else_takes_over(monkeypatch):
    _Patched(monkeypatch)
    veh = FakeArduPilot("quad", home_ned=(0, 0, 0), pos_ned=(0, 0, -5.0))
    run = _run(veh)
    veh.custom_mode = 5          # pilot flipped to LOITER (or the battery guard to RTL)
    assert run.run() == "yielded" and veh.modes == [], "no fight over the aircraft"


def test_fallback_run_hands_back_on_abort(monkeypatch):
    _Patched(monkeypatch)
    veh = FakeArduPilot("quad", home_ned=(0, 0, 0), pos_ned=(0, 0, -5.0))
    run = _run(veh)
    run.abort()
    assert run.run() == "handed_back" and veh.modes == []
    sp = veh.setpoints[-1]
    assert (sp["vx"], sp["vy"], sp["vz"]) == (0.0, 0.0, 0.0), "left holding, zero velocity"


def test_build_refuses_without_a_takeoff_point(monkeypatch):
    p = _Patched(monkeypatch)
    veh = FakeArduPilot("quad")
    veh.flight_home = lambda: None
    with pytest.raises(RuntimeError, match="takeoff point"):
        _run(veh)
    assert p.links == [], "nothing left open"


@pytest.mark.parametrize("team_link,why", [
    (None, "no team_link section"),
    ({"port": 5760}, "key_file"),
    ({"key_file": "certs/team.key", "insecure_allow_unsigned": True}, "insecure"),
])
def test_build_refuses_an_unsigned_team_link(monkeypatch, team_link, why):
    p = _Patched(monkeypatch)
    veh = FakeArduPilot("quad", home_ned=(0, 0, 0), pos_ned=(0, 0, -5.0))
    with pytest.raises(RuntimeError, match=why):
        _run(veh, team_link=team_link)
    assert p.links == [] and veh.setpoints == []


def test_the_real_team_link_accepts_build_runs_config(tmp_path):
    """build_run's TeamLink.from_config call, against the real team_link (not started:
    no socket). A relative key_file resolves against the install dir, and it signs."""
    from eco.drone.common.team_link import TeamLink, TeamLinkConfigError
    (tmp_path / "certs").mkdir()
    key = tmp_path / "certs" / "team.key"
    key.write_text("1 " + "ab" * 32 + "\n")
    key.chmod(0o600)
    link = TeamLink.from_config("drone-test", TEAM_LINK, base_dir=str(tmp_path))
    assert link.signed
    with pytest.raises(TeamLinkConfigError):
        TeamLink.from_config("drone-test", {"port": 5760}, base_dir=str(tmp_path))


def test_shipped_examples_parse():
    import yaml
    here = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((here / "config.yaml.example").read_text())
    assert cfg["autonomy_fallback"]["enabled"] is False, "the example ships it OFF"
    assert af.team_link_problem(cfg["team_link"]) is None
    m = af.PrebriefedMission.load(here / "fallback_mission.yaml.example")
    assert m.goals_for("drone-REPLACE-ME")[-1] == [0.0, 0.0, 5.0]


def test_low_confidence_and_blindness_slow_the_vehicle_like_the_sim_runtime():
    class Inner:
        last = None
        def send(self, a, vc, dt): Inner.last = np.asarray(a)
    tel = types.SimpleNamespace(last_pose=types.SimpleNamespace(loc_confidence=1.0, sensor_ok=True))
    sa = af.ScaledActuator(Inner(), tel)
    sa.send([2.0, 0.0, 1.0, 0.5], None, 0.1)
    np.testing.assert_allclose(Inner.last, [2.0, 0.0, 1.0, 0.5])
    tel.last_pose.loc_confidence = 0.5
    sa.send([2.0, 0.0, 1.0, 0.5], None, 0.1)
    np.testing.assert_allclose(Inner.last, [1.0, 0.0, 0.5, 0.5]), "yaw rate untouched"
    tel.last_pose.sensor_ok = False
    sa.send([1.0, 0.4], None, 0.1)
    np.testing.assert_allclose(Inner.last, [1.0 * 0.5 * af.BLIND_SCALE, 0.4])


# --------------------------------------------------------------------------- daemon
@pytest.fixture(scope="module")
def daemon():
    class QoS(IntEnum):
        AT_MOST_ONCE = 0
        AT_LEAST_ONCE = 1
    stubs = {
        "awscrt": types.SimpleNamespace(io=types.SimpleNamespace(), mqtt=types.SimpleNamespace(QoS=QoS)),
        "awscrt.io": types.SimpleNamespace(), "awscrt.mqtt": types.SimpleNamespace(QoS=QoS),
        "awsiot": types.SimpleNamespace(mqtt_connection_builder=None),
        "provisioning": types.SimpleNamespace(ProvisioningService=object,
                                              get_or_create_drone_id=lambda *a: "drone-test"),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        import daemon as d
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return d


class Conn:
    """An MQTT connection whose QoS 1 publishes are acknowledged (or not)."""

    def __init__(self, acks=True):
        self.published, self.acks = [], acks

    def publish(self, topic, payload, qos):
        self.published.append((topic, json.loads(payload), int(qos)))
        f = Future()
        if self.acks or int(qos) == 0:
            f.set_result(1)
        return f, 1


class FlyingLoop:
    """A MissionLoop on a long leg: flies until told to stop."""
    instances = []

    def __init__(self, **kw):
        self.stopped = threading.Event()
        FlyingLoop.instances.append(self)

    def abort(self):
        self.stopped.set()

    def run(self, mission):
        self.stopped.wait(20)
        return types.SimpleNamespace(success=False, phases_completed=0, total_phases=1,
                                     failure_reason="stopped", summary="", actions_taken=0,
                                     duration_seconds=0.0, photos=[], videos=[],
                                     to_dict=lambda: {"success": False})


class FakeRun:
    def __init__(self):
        self.cancel = threading.Event()
        self.status = af.FallbackStatus([[1, 2, 3]])
        self.cleanup = None
        self.started = threading.Event()

    def abort(self):
        self.cancel.set()

    def run(self):
        self.started.set()
        self.cancel.wait(20)
        return "handed_back"


class Clock:
    def __init__(self):
        self.t = 1000.0
    def __call__(self):
        return self.t


def _wait(pred, s=5.0):
    end = time.time() + s
    while time.time() < end and not pred():
        time.sleep(0.02)
    return pred()


@pytest.fixture
def world(daemon, monkeypatch, tmp_path):
    import yaml
    import reasoning_loop
    FlyingLoop.instances = []
    monkeypatch.setattr(reasoning_loop, "MissionLoop", FlyingLoop)
    veh = FakeArduPilot("quad", pos_ned=(3.0, -2.0, -5.0))
    monkeypatch.setitem(sys.modules, "drone_sdk", veh)
    conn = Conn()
    monkeypatch.setattr(daemon, "_mqtt_connection", conn)
    monkeypatch.setattr(daemon, "detect_capabilities", lambda: {})
    mission = tmp_path / "fallback.yaml"
    mission.write_text(yaml.safe_dump(MISSION))
    w = types.SimpleNamespace(veh=veh, conn=conn, clock=Clock(), runs=[], mission=str(mission),
                              build_error=None)

    def build(sdk, actuator):
        if w.build_error:
            raise w.build_error
        r = FakeRun()
        w.runs.append(r)
        return r

    def enable(**cfg):
        tl = cfg.pop("team_link", TEAM_LINK)
        fb = daemon._make_autonomy(
            dict({"enabled": True, "cloud_silence_s": 15, "mission_file": w.mission}, **cfg),
            build=build, clock=w.clock, team_link_cfg=tl,
            make_actuator=lambda sdk: ArduPilotActuator("quadcopter", sdk=sdk))
        monkeypatch.setattr(daemon, "_autonomy", fb)
        return fb
    w.enable = enable
    yield w
    if daemon._autonomy is not None:
        daemon._autonomy.handback()
    daemon._stop_active_mission()


def _chat(daemon, **data):
    data.setdefault("conversation_id", "c1")
    data["nonce"] = time.time()
    daemon.on_chat_command("drone/x/chat/c1/command", json.dumps(data))


def _start_mission(daemon):
    _chat(daemon, action="mission", mission_id="survey", phases=[{"type": "survey_rect"}])
    assert _wait(lambda: daemon.active_mission_status().get("id") == "survey")


def test_disabled_changes_nothing(daemon, world):
    assert daemon._make_autonomy({}) is None
    assert daemon._make_autonomy({"enabled": False, "mission_file": world.mission}) is None
    assert daemon._autonomy is None
    daemon.publish_heartbeat()
    assert world.conn.published[-1][2] == 0, "heartbeat stays QoS 0"
    _start_mission(daemon)
    world.clock.t += 3600       # an hour of silence changes nothing: there is nothing to tick
    daemon._note_control_plane()
    daemon._autonomy_handback()
    assert daemon.active_mission_status()["id"] == "survey"
    assert not FlyingLoop.instances[-1].stopped.is_set() and world.veh.setpoints == []


@pytest.mark.parametrize("team_link", [None, {"port": 5760},
                                       {"key_file": "k", "insecure_allow_unsigned": True}])
def test_enabled_without_a_signed_team_link_stays_off(daemon, world, team_link):
    assert world.enable(team_link=team_link) is None
    daemon.publish_heartbeat()
    assert world.conn.published[-1][2] == 0, "off means off: daemon unchanged"


def test_enabled_takes_over_only_after_real_silence(daemon, world):
    fb = world.enable()
    daemon.publish_heartbeat()
    assert world.conn.published[-1][2] == 1, "heartbeat at QoS 1, so its ack proves the link"
    _start_mission(daemon)
    world.clock.t += 14
    assert fb.tick() is False, "14 s is not 15"
    daemon.publish_heartbeat()          # acknowledged by the broker: the control plane is there
    world.clock.t += 14
    assert fb.tick() is False, "the ack reset the silence"
    world.conn.acks = False             # the link dies: heartbeats go unacknowledged
    daemon.publish_heartbeat()
    world.clock.t += 2
    assert fb.tick() is True
    assert _wait(lambda: world.runs and world.runs[0].started.is_set())
    assert FlyingLoop.instances[-1].stopped.is_set(), "the cloud mission was stopped first"
    assert world.veh.setpoints[0]["vx"] == 0.0, "held while the onboard loop was built"
    status = daemon.active_mission_status()
    assert status["running"] and status["id"] == "autonomy-fallback"
    assert fb.tick() is False, "engages once"


def test_operator_command_hands_control_back(daemon, world):
    fb = world.enable()
    _start_mission(daemon)
    world.clock.t += 30
    assert fb.tick()
    assert _wait(lambda: world.runs and world.runs[0].started.is_set())
    _chat(daemon, action="mission", mission_id="new", phases=[{"type": "land"}])
    assert world.runs[0].cancel.is_set(), "the onboard loop stopped before the command ran"
    assert _wait(lambda: daemon.active_mission_status().get("id") == "new")
    assert not fb.engaged


def test_a_stop_from_the_operator_also_ends_it(daemon, world):
    fb = world.enable()
    _start_mission(daemon)
    world.clock.t += 30
    fb.tick()
    assert _wait(lambda: world.runs and world.runs[0].started.is_set())
    world.veh.is_flying = lambda: True
    world.veh.brake = lambda: True
    world.veh.hold_here = lambda brake_first=True: True
    world.veh._position_relative = lambda: (0, 0, 5.0)
    _chat(daemon, action="abort", then="hold")
    assert _wait(lambda: not daemon.active_mission_status()["running"])
    assert world.runs[0].cancel.is_set()


def test_no_mission_flying_means_no_takeover(daemon, world):
    fb = world.enable()
    world.clock.t += 600
    assert fb.tick() is False and world.runs == []


def test_not_armed_or_not_guided_declines_and_leaves_the_mission_alone(daemon, world):
    fb = world.enable()
    _start_mission(daemon)
    world.clock.t += 30
    world.veh.armed = False
    assert fb.tick()
    assert _wait(lambda: not fb.engaged)
    assert world.runs == [] and not FlyingLoop.instances[-1].stopped.is_set()
    assert fb.tick() is False, "backs off before looking again"
    world.clock.t += fb.RETRY_S
    world.veh.armed, world.veh.custom_mode = True, 5   # armed now, but in LOITER
    assert fb.tick()
    assert _wait(lambda: not fb.engaged)
    assert world.runs == [] and not FlyingLoop.instances[-1].stopped.is_set()


def test_a_fallback_that_cannot_start_returns_to_launch(daemon, world):
    fb = world.enable()
    world.build_error = RuntimeError("depth camera not found")
    _start_mission(daemon)
    world.clock.t += 30
    assert fb.tick()
    assert _wait(lambda: not fb.engaged)
    assert FlyingLoop.instances[-1].stopped.is_set()
    assert world.veh.modes == ["RTL"] and fb.last_outcome == "rtl"


def test_a_broken_mission_file_is_reported_at_boot_and_rtls_if_needed(daemon, world, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("tasks: [[1, 2, 3]]\n")            # no region
    fb = world.enable(mission_file=str(bad))
    assert fb.mission is None
    _start_mission(daemon)
    world.clock.t += 30
    fb.tick()
    assert _wait(lambda: not fb.engaged)
    assert world.veh.modes == ["RTL"]


def test_inbound_traffic_counts_as_the_control_plane(daemon, world):
    fb = world.enable()
    _start_mission(daemon)
    world.clock.t += 14
    daemon.on_shadow_rejected("t", b"{}")
    world.clock.t += 14
    assert fb.tick() is False
    daemon.on_connection_resumed(None, 0, True)
    world.clock.t += 14
    assert fb.tick() is False
