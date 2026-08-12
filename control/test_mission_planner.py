"""Unit tests for the multi-option mission planner in conversations.py — the
model call is stubbed, so no AWS / mosquitto / real LLM is needed. The geometry
underneath is covered separately in test_planning.py.

Run: python3 -m pytest control/test_mission_planner.py -v
"""
import json
from unittest.mock import patch

from control import conversations


# A no-fly zone: a small square. Two candidate plans — one routes north of it
# (clear), one routes straight through it (should be flagged + demoted).
NFZ = [
    {"lat": 37.000, "lon": -122.000},
    {"lat": 37.000, "lon": -121.990},
    {"lat": 37.010, "lon": -121.990},
    {"lat": 37.010, "lon": -122.000},
]
HOME = {"lat": 37.005, "lon": -122.030}  # west of the zone


def _leg(lat, lon):
    return {"type": "go_to_gps", "lat": lat, "lon": lon, "alt_m": 40}


CLEAN_PLAN = {
    "label": "North detour",
    "rationale": "routes north around the no-fly zone",
    "recommended": False,
    "per_drone": [
        {"drone_id": "alpha", "phases": [
            {"type": "arm_and_takeoff", "altitude_m": 30},
            _leg(37.050, -121.980),  # well north of the zone
            {"type": "return_home"}, {"type": "land"}]},
    ],
}

CROSSING_PLAN = {
    "label": "Straight line",
    "rationale": "shortest, flies directly across",
    "recommended": True,   # model wrongly recommends the illegal one
    "per_drone": [
        {"drone_id": "alpha", "phases": [
            {"type": "arm_and_takeoff", "altitude_m": 30},
            _leg(37.005, -121.985),  # east side, straight line crosses the NFZ
            {"type": "return_home"}, {"type": "land"}]},
    ],
}


def _fake_invoke_result(plans):
    return {"content": [{"type": "tool_use", "name": "propose_plans",
                         "input": {"plans": plans}}]}


def test_planner_flags_and_demotes_nfz_crossing_plan():
    with patch.object(conversations.llm, "invoke",
                      return_value=_fake_invoke_result([CROSSING_PLAN, CLEAN_PLAN])):
        plans = conversations.call_mission_planner(
            [], "search the area", operating_area=[], no_fly_zones=[NFZ],
            drone_ids=["alpha"], home=HOME)["plans"]

    assert len(plans) == 2
    by_label = {p["label"]: p for p in plans}
    assert by_label["Straight line"]["nfz_clear"] is False
    assert by_label["Straight line"]["nfz_violations"] >= 1
    assert by_label["North detour"]["nfz_clear"] is True

    # Exactly one recommendation, and it must be the NFZ-clear plan even though
    # the model recommended the crossing one.
    recommended = [p for p in plans if p["recommended"]]
    assert len(recommended) == 1
    assert recommended[0]["label"] == "North detour"


def test_planner_assigns_ids_and_eta():
    with patch.object(conversations.llm, "invoke",
                      return_value=_fake_invoke_result([CLEAN_PLAN, CROSSING_PLAN])):
        plans = conversations.call_mission_planner(
            [], "search", operating_area=[], no_fly_zones=[NFZ],
            drone_ids=["alpha"], home=HOME)["plans"]
    assert [p["plan_id"] for p in plans] == ["plan-1", "plan-2"]
    assert all(p["est_minutes"] > 0 for p in plans)


def test_extract_tool_input_from_text_fallback():
    # A model that ignored the forced tool call and emitted JSON as text.
    text_result = {"content": [{"type": "text", "text":
        json.dumps({"plans": [CLEAN_PLAN]})}]}
    got = conversations._extract_tool_input(text_result, "propose_plans")
    assert "plans" in got and len(got["plans"]) == 1


def test_planner_returns_empty_on_model_error():
    with patch.object(conversations.llm, "invoke", side_effect=RuntimeError("boom")):
        result = conversations.call_mission_planner(
            [], "search", operating_area=[], no_fly_zones=[NFZ],
            drone_ids=["alpha"], home=HOME)
    assert result["plans"] == [] and result["ask"] is None


def test_planner_surfaces_clarifying_question():
    # Model asks instead of planning (vague tasking).
    ask_result = {"content": [{"type": "tool_use", "name": "propose_plans",
                               "input": {"ask": "What should I look for?"}}]}
    with patch.object(conversations.llm, "invoke", return_value=ask_result):
        result = conversations.call_mission_planner(
            [], "go do something", operating_area=[], no_fly_zones=[],
            drone_ids=["alpha"], home=HOME)
    assert result["plans"] == []
    assert result["ask"] == "What should I look for?"
