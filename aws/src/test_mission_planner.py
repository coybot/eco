"""Unit tests for the multi-option mission planner in conversations.py — the
model call is stubbed, so no AWS / mosquitto / real LLM is needed. The geometry
underneath is covered separately in test_planning.py.

Run: python3 -m pytest control/test_mission_planner.py -v
"""
import json
from unittest.mock import patch

import conversations
import planning


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


def _fleet(vehicle_type="fixedwing", battery=95):
    # These plans fly ~7 km out; give the fixed-wing the range for it.
    return [planning.Vehicle("alpha", planning.Platform.for_vehicle(
        vehicle_type, limits={"max_range_m": 20000}), HOME, battery_pct=battery)]


def _ctx(**kw):
    return dict({"area": [], "no_fly": [NFZ], "wind": None, "margin_m": 10.0}, **kw)


# A plan that parks its waypoint inside the zone: no detour can fix that.
INSIDE_PLAN = {
    "label": "Straight line",
    "rationale": "flies to a point in the zone",
    "per_drone": [
        {"drone_id": "alpha", "phases": [
            {"type": "arm_and_takeoff", "altitude_m": 30},
            _leg(37.005, -121.995),
            {"type": "return_home"}, {"type": "land"}]},
    ],
}


def test_a_crossing_leg_is_rerouted_round_the_zone():
    with patch.object(conversations.llm, "invoke",
                      return_value=_fake_invoke_result([CROSSING_PLAN, CLEAN_PLAN])):
        plans = conversations.call_mission_planner(
            [], "search the area", _fleet(), _ctx())["plans"]
    straight = next(p for p in plans if p["label"] == "Straight line")
    assert straight["nfz_clear"] and straight["dispatchable"]
    assert any("detour" in a for a in straight["per_drone"][0]["adjustments"])


def test_an_unfixable_crossing_is_blocked_and_never_recommended():
    with patch.object(conversations.llm, "invoke",
                      return_value=_fake_invoke_result([INSIDE_PLAN, CLEAN_PLAN])):
        plans = conversations.call_mission_planner(
            [], "search the area", _fleet(), _ctx())["plans"]
    by_label = {p["label"]: p for p in plans}
    assert by_label["Straight line"]["dispatchable"] is False
    assert by_label["Straight line"]["nfz_violations"] >= 1
    recommended = [p for p in plans if p["recommended"]]
    assert len(recommended) == 1 and recommended[0]["label"] == "North detour"


def test_planner_assigns_ids_and_eta():
    with patch.object(conversations.llm, "invoke",
                      return_value=_fake_invoke_result([CLEAN_PLAN, CROSSING_PLAN])):
        plans = conversations.call_mission_planner([], "search", _fleet(), _ctx())["plans"]
    assert sorted(p["plan_id"] for p in plans) == ["plan-1", "plan-2"]
    assert all(p["est_minutes"] > 0 for p in plans)


def test_a_model_error_still_leaves_the_geometry_built_plans():
    area = [{"lat": 37.02, "lon": -122.02}, {"lat": 37.02, "lon": -122.01},
            {"lat": 37.03, "lon": -122.01}, {"lat": 37.03, "lon": -122.02}]
    with patch.object(conversations.llm, "invoke", side_effect=RuntimeError("boom")):
        result = conversations.call_mission_planner(
            [], "find the pickup truck", _fleet(), _ctx(area=area))
    assert result["ask"] is None and result["plans"]
    assert all(p["source"] == "planner" for p in result["plans"])
    with patch.object(conversations.llm, "invoke", side_effect=RuntimeError("boom")):
        result = conversations.call_mission_planner([], "search", _fleet(), _ctx())
    assert result["plans"] == []


def test_planner_surfaces_clarifying_question():
    ask_result = {"content": [{"type": "tool_use", "name": "propose_plans",
                               "input": {"ask": "What should I look for?"}}]}
    with patch.object(conversations.llm, "invoke", return_value=ask_result):
        result = conversations.call_mission_planner([], "go do something", _fleet(), _ctx())
    assert result["plans"] == []
    assert result["ask"] == "What should I look for?"


def test_extract_tool_input_from_text_fallback():
    # A model that ignored the forced tool call and emitted JSON as text.
    text_result = {"content": [{"type": "text", "text":
        json.dumps({"plans": [CLEAN_PLAN]})}]}
    got = conversations._extract_tool_input(text_result, "propose_plans")
    assert "plans" in got and len(got["plans"]) == 1


# --- _render_mission_context caps/budget ------------------------------------

def _record(mission_id, landmarks=None):
    return {"mission_id": mission_id, "completed_at": "2026-08-18T00:00:00Z",
            "success": True, "summary": "found it", "target_location": None,
            "landmarks": landmarks or [], "photos": []}


def test_render_mission_context_none_when_no_records():
    assert conversations._render_mission_context([]) is None


def test_render_mission_context_includes_landmark_data_and_rules():
    landmarks = [{"label": "car", "east_m": 388.0, "north_m": 13.0, "hits": 4}]
    ctx = conversations._render_mission_context([_record("m1", landmarks)])
    assert "COMPLETED MISSION DATA" in ctx
    assert '"east_m": 388.0' in ctx
    assert "go_to_gps" in ctx  # the prohibition rule is present
    assert "east=<E>, north=<N> (metres, local frame)" in ctx


def test_render_mission_context_drops_oldest_when_over_budget():
    # Two records, each ~210 chars serialized — a budget that fits exactly one
    # must keep the NEWEST (last in the oldest-first input list) and drop the
    # oldest, not the reverse.
    old = _record("old-mission", [{"label": "x", "east_m": 1.0, "north_m": 1.0}])
    new = _record("new-mission", [{"label": "y", "east_m": 2.0, "north_m": 2.0}])
    ctx = conversations._render_mission_context([old, new], max_chars=300)
    assert "new-mission" in ctx
    assert "old-mission" not in ctx


def test_render_mission_context_keeps_everything_under_generous_budget():
    records = [_record(f"m{i}") for i in range(2)]
    ctx = conversations._render_mission_context(records, max_chars=4000)
    assert "m0" in ctx and "m1" in ctx
