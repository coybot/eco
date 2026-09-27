"""Any mission phase can carry a time limit, and the mission goes on after it.

Observed on the quadcopter: "take off to 3m, follow me then return home after
60 seconds" ended in "Phase action limit reached": no phase could say how
long, so the follow ran until its action budget did, and the mission failed
instead of coming home. "Keep searching for 30s then return home" had the same
hole. time_limit_s ends a phase where it is and carries on to the next one.

Run: cd eco && python3 -m pytest drone/common/tests/test_phase_time_limits.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mission_vocab  # noqa: E402
import reasoning_loop as rl  # noqa: E402
from vlm import ActionType, VLMAction  # noqa: E402
from vehicle_class import get_class  # noqa: E402

DECISION_S = 5.0   # how long each simulated VLM decision takes


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(rl, "DRONE_SDK_AVAILABLE", False)
    monkeypatch.setattr(rl.time, "time", c.time)
    monkeypatch.setattr(rl.time, "sleep", c.sleep)
    return c


class Backend:
    def __init__(self):
        self.homes = 0
        self.gotos = []

    def capture_frame(self):
        return np.zeros((8, 8, 3), dtype=np.uint8)

    def get_pose(self):
        return (0.0, 0.0, 3.0, 0.0)

    def get_battery(self):
        return {"remaining": 100.0}

    def detect(self):
        return []

    def loiter(self, center, radius):
        pass

    def goto(self, north_m, east_m, alt_m):
        self.gotos.append((north_m, east_m))
        return True

    def rtl(self, alt_m=None):
        self.homes += 1
        return True

    def configure_safety(self, **kw):
        return True

    def log_event(self, *a, **k):
        pass

    def unproject(self, *a):
        return None


class StillLooking:
    """A VLM that never finishes: every decision asks for help and holds."""
    def __init__(self, clock):
        self.clock, self.calls = clock, 0

    def is_available(self):
        return True

    def decide(self, *a, **k):
        self.calls += 1
        self.clock.t += DECISION_S
        return VLMAction(action_type=ActionType.ASK_CLOUD, message="still looking")


def _run(clock, phases, max_phase_actions=3):
    backend = Backend()
    vlm = StillLooking(clock)
    loop = rl.MissionLoop(backend=backend, conversation_id="c-1", vehicle_class=get_class("quadcopter"))
    loop.MAX_PHASE_ACTIONS = max_phase_actions
    loop._get_vlm = lambda: vlm
    loop._get_nav = lambda: None
    loop._get_perception = lambda: None
    loop._battery_gov = None
    result = loop.run(rl.Mission(mission_id="m", phases=phases, conversation_id="c-1",
                                 original_message="follow me then return home after 60 seconds"))
    return result, backend, vlm


FOLLOW = {"objective": "Follow the person", "success": "Stayed with the person"}


def test_without_a_limit_an_endless_phase_fails_the_mission(clock):
    """The behaviour the operator saw: the budget, not the operator, ends it."""
    result, backend, vlm = _run(clock, [dict(FOLLOW), {"type": "return_home"}])
    assert not result.success and "action limit" in (result.failure_reason or "")
    assert backend.homes == 0, "never came home"


def test_the_time_limit_ends_the_phase_and_the_mission_comes_home(clock):
    t0 = clock.t
    result, backend, vlm = _run(clock, [dict(FOLLOW, time_limit_s=60), {"type": "return_home"}])
    assert result.success, result.failure_reason
    assert backend.homes == 1
    assert vlm.calls == 60 / DECISION_S, "ran for its 60 s, not for its 3-action budget"
    assert 60 <= clock.t - t0 < 60 + 2 * DECISION_S
    assert any("60 s time limit" in f for f in result.findings)


def test_a_survey_stops_between_photos_and_says_how_far_it_got(clock):
    backend = Backend()
    loop = rl.MissionLoop(backend=backend, conversation_id="c-1", vehicle_class=get_class("quadcopter"))
    loop._home_yaw_rad = 0.0
    loop._media_warnings, loop._photos = [], []
    loop._take_photo = lambda: (setattr(clock, "t", clock.t + 3.0), ("https://x/1.jpg", ""))[1]
    loop._start_phase_clock({"time_limit_s": 30})
    out = loop._exec_survey_rect({"type": "survey_rect", "forward_m": 5, "right_m": 5, "spacing_m": 1})
    assert out["time_limit_reached"] and out["success"]
    assert 1 <= len(backend.gotos) < 25
    assert loop._media_warnings == [f"the survey stopped at its time limit after {len(backend.gotos)} of 25 photos"]


def test_hold_waits_its_seconds(clock):
    loop = rl.MissionLoop(backend=Backend(), conversation_id="c-1", vehicle_class=get_class("quadcopter"))
    t0 = clock.t
    assert loop._exec_hold({"type": "hold", "seconds": 30}) == {"success": True, "actions": 1}
    assert clock.t - t0 == pytest.approx(30)


def test_the_planner_is_told_about_both():
    assert "hold" in mission_vocab.PHASE_SCHEMAS
    assert "time_limit_s" in mission_vocab.render_phase_prompt_section()
    prompt = (Path(__file__).resolve().parents[3] / "aws" / "src" / "conversations.py").read_text()
    assert '"time_limit_s": 60' in prompt and '"type": "hold"' in prompt
