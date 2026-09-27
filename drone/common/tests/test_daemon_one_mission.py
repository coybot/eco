"""One mission flies at a time, and "stop" stops it.

Found reading the daemon after the real chat log: a mission that arrived while
another was flying started a second MissionLoop alongside the first, both
steering the aircraft, and there was no way to stop a mission from chat.

Run: cd eco && python3 -m pytest drone/common/tests/test_daemon_one_mission.py -v
"""
import json
import sys
import threading
import time
import types
from enum import IntEnum
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def daemon(tmp_path_factory):
    # The daemon imports the AWS IoT SDK at module level; the logic under test does not use it.
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
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos):
        self.published.append((topic, json.loads(payload)))


class FakeLoop:
    """Flies until told to stop, like a MissionLoop on a long leg."""
    started = []

    def __init__(self, **kw):
        self._stop = threading.Event()
        self.overlap = None

    def abort(self):
        self._stop.set()

    def run(self, mission):
        FakeLoop.started.append((mission.mission_id, time.time()))
        # Any other loop still flying now would be two missions at once.
        self.overlap = sum(1 for t in threading.enumerate() if getattr(t, "_flying", False))
        threading.current_thread()._flying = True
        self._stop.wait(10)
        threading.current_thread()._flying = False
        return types.SimpleNamespace(success=not self._stop.is_set(), phases_completed=0, total_phases=1,
                                     failure_reason="Stopped on your command", summary="", actions_taken=0,
                                     duration_seconds=0.0, photos=[], videos=[],
                                     to_dict=lambda: {"success": False, "failure_reason": "Stopped on your command"})


@pytest.fixture
def fake_loop(monkeypatch):
    import reasoning_loop
    FakeLoop.started = []
    monkeypatch.setattr(reasoning_loop, "MissionLoop", FakeLoop)
    return FakeLoop


def _send(d, **data):
    data.setdefault("conversation_id", "c1")
    data["nonce"] = time.time()   # the daemon drops exact duplicates
    d.on_chat_command("drone/x/chat/c1/command", json.dumps(data))


def _wait(pred, s=5.0):
    end = time.time() + s
    while time.time() < end and not pred():
        time.sleep(0.02)
    return pred()


def test_a_new_mission_replaces_the_one_flying(daemon, fake_loop, monkeypatch):
    monkeypatch.setattr(daemon, "_mqtt_connection", Conn())
    _send(daemon, action="mission", mission_id="follow", phases=[{"objective": "Follow the person"}])
    assert _wait(lambda: daemon.active_mission_status().get("id") == "follow")
    first = daemon._active["loop"]
    _send(daemon, action="mission", mission_id="land", phases=[{"type": "land"}])
    assert _wait(lambda: daemon.active_mission_status().get("id") == "land")
    assert first._stop.is_set(), "the follow was told to stop"
    assert daemon._active["loop"].overlap == 0, "the landing started only after the follow ended"
    daemon._stop_active_mission()


def test_stop_ends_the_mission_and_holds(daemon, fake_loop, monkeypatch):
    conn = Conn()
    monkeypatch.setattr(daemon, "_mqtt_connection", conn)
    calls = []
    fake_sdk = types.SimpleNamespace(
        is_flying=lambda: True, brake=lambda: calls.append("brake") or True,
        clear_abort=lambda: calls.append("clear"),
        hold_here=lambda brake_first=True: calls.append(f"hold(brake_first={brake_first})") or True,
        _position_relative=lambda: (0, 0, 4.8), land=lambda: calls.append("land"),
        return_home=lambda: calls.append("home") or True, request_abort=lambda: None)
    monkeypatch.setitem(sys.modules, "drone_sdk", fake_sdk)
    _send(daemon, action="mission", mission_id="survey", phases=[{"type": "survey_rect"}])
    assert _wait(lambda: daemon.active_mission_status().get("running"))
    _send(daemon, action="abort", then="hold")
    assert _wait(lambda: any(p.get("result", {}).get("stdout", "").startswith("Stopped the mission")
                             for _, p in conn.published))
    assert calls == ["brake", "clear", "hold(brake_first=False)"], "brake first, hold where it stopped"
    assert not daemon.active_mission_status()["running"]
    reply = [p for _, p in conn.published if "stdout" in p.get("result", {})][-1]["result"]
    assert reply["success"] and "Holding position at 4.8 m" in reply["stdout"]


def test_stop_on_the_ground_does_nothing(daemon, monkeypatch):
    conn = Conn()
    monkeypatch.setattr(daemon, "_mqtt_connection", conn)
    moved = []
    fake_sdk = types.SimpleNamespace(is_flying=lambda: False, clear_abort=lambda: None,
                                     brake=lambda: moved.append("brake"), hold_here=lambda **k: moved.append("hold"),
                                     land=lambda: moved.append("land"), return_home=lambda: moved.append("home"))
    monkeypatch.setitem(sys.modules, "drone_sdk", fake_sdk)
    _send(daemon, action="abort", then="return_home")
    assert _wait(lambda: conn.published)
    assert moved == [] and conn.published[-1][1]["result"]["stdout"] == "The drone is not flying."
