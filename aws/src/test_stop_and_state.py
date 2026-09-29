"""The planner must know whether the drone is flying, and "stop" must stop it.

Real chat log: "Stop" sent just after a mission went out was answered with
"The drone is on the ground" and nothing was sent to the drone. The planner
had no drone state, and its history held only its own words, not the mission.

Run: cd eco/aws/src && python3 -m pytest test_stop_and_state.py -v
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
import conversations as c  # noqa: E402


@pytest.mark.parametrize("msg,then", [
    ("Stop", "hold"), ("stop!", "hold"), ("STOP NOW", "hold"), ("Halt", "hold"), ("hold", "hold"),
    ("Land", "land"), ("land now please", "land"),
    ("Return home", "return_home"), ("come back", "return_home"), ("RTL", "return_home"),
])
def test_plain_stop_land_and_home_skip_the_planner(msg, then):
    assert c.fast_abort(msg) == then


@pytest.mark.parametrize("msg", [
    "Hover here for 30 seconds then land", "And return home", "Take off to 5m and land",
    "stop following me and take a picture", "Land on the red mat", "come back after 60 seconds",
])
def test_anything_more_goes_to_the_planner(msg):
    assert c.fast_abort(msg) is None


def _hb(**kw):
    base = {"lastUpdate": time.time() * 1000, "armed": False}
    base.update(kw)
    return base


def test_the_planner_is_told_the_drone_is_flying_a_mission():
    s = c.describe_drone_state(_hb(armed=True, altitudeAgl=4.8, battery=71,
                                   mission={"running": True, "phase": 2, "of": 5, "step": "nav"}))
    assert "FLYING at 4.8 m" in s and "step 2 of 5" in s and "71%" in s


def test_on_the_ground_and_stale_heartbeats_are_said_plainly():
    assert "on the ground, disarmed" in c.describe_drone_state(_hb())
    assert "no mission running" in c.describe_drone_state(_hb(mission={"running": False}))
    old = _hb(lastUpdate=(time.time() - 300) * 1000, armed=True, altitudeAgl=5)
    assert "unknown" in c.describe_drone_state(old)
    assert "unknown" in c.describe_drone_state(None)


def test_the_planner_is_told_when_the_pilot_has_the_aircraft():
    s = c.describe_drone_state(_hb(armed=True, altitudeAgl=6.0,
                                   pilotControl="the pilot moved the pitch stick"))
    assert "PILOT HAS CONTROL" in s
    assert "PILOT HAS CONTROL" not in c.describe_drone_state(_hb(armed=True, altitudeAgl=6.0))


def test_the_state_reaches_the_planner_prompt():
    assert c.mission_system_prompt(None, "DRONE STATE (live): FLYING").endswith("DRONE STATE (live): FLYING")
    assert '"action": "abort"' in c.MISSION_SYSTEM_PROMPT


def test_history_shows_what_was_sent_to_the_drone(monkeypatch):
    items = [{"SK": "MSG#1", "sender": "user", "content": "Take off to 5m and go forward 5m"},
             {"SK": "MSG#2", "sender": "drone", "content": "Taking off...",
              "sent": c.summarize_phases([{"type": "arm_and_takeoff", "altitude_m": 5},
                                          {"type": "nav", "forward_m": 5}])}]

    class T:
        def query(self, ScanIndexForward=True, Limit=None, **_):
            return {"Items": (items if ScanIndexForward else items[::-1])[:Limit]}
    monkeypatch.setattr(c, "dynamodb", type("D", (), {"Table": lambda self, n: T()})())
    h = c.get_conversation_history("d", "conv")
    assert h[-1]["content"] == ("Taking off...\n[Sent to the drone: mission: "
                                "arm_and_takeoff(altitude_m=5) -> nav(forward_m=5)]")
