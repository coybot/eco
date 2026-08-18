"""Handler-level round trip for the storyboard planner routes, with in-memory
fakes for DynamoDB / IoT / the model — no mosquitto, no AWS, no network. This
is the portable complement to gcs/tests/test_local_stack.py (which needs a real
broker): it proves plan_handler stores ranked options and plan_select_handler
dispatches the chosen plan's phases to each aircraft over the command topic.

Run: python3 -m pytest control/test_plan_handlers.py -v
"""
import json
from unittest.mock import patch

from control import conversations


# --- in-memory fakes ---------------------------------------------------------

class FakeTable:
    def __init__(self):
        self.items = []

    @staticmethod
    def _key_attrs(item):
        if "PK" in item:
            return ("PK", "SK")
        if "userId" in item:
            return ("userId", "droneId")
        return ("droneId",)

    def put_item(self, Item):
        attrs = self._key_attrs(Item)
        self.items = [it for it in self.items
                      if any(it.get(a) != Item.get(a) for a in attrs)]
        self.items.append(dict(Item))

    def get_item(self, Key):
        for it in self.items:
            if all(it.get(k) == v for k, v in Key.items()):
                return {"Item": dict(it)}
        return {}

    def query(self, **kwargs):
        vals = kwargs.get("ExpressionAttributeValues", {})
        pk = vals.get(":pk")
        prefix = vals.get(":sk_prefix", "")
        rows = [it for it in self.items
                if it.get("PK") == pk and str(it.get("SK", "")).startswith(prefix)]
        rows.sort(key=lambda it: it.get("SK", ""))
        return {"Items": rows}


class FakeDynamo:
    def __init__(self):
        self.tables = {}

    def Table(self, name):
        return self.tables.setdefault(name, FakeTable())


class FakeIoT:
    def __init__(self):
        self.published = []

    def publish(self, topic, qos, payload):
        self.published.append({"topic": topic, "payload": json.loads(payload)})


NFZ = [
    {"lat": 37.000, "lon": -122.000}, {"lat": 37.000, "lon": -121.990},
    {"lat": 37.010, "lon": -121.990}, {"lat": 37.010, "lon": -122.000},
]

CLEAN = {"label": "North detour", "rationale": "around the zone", "recommended": False,
         "per_drone": [{"drone_id": "alpha", "phases": [
             {"type": "arm_and_takeoff", "altitude_m": 30},
             {"type": "go_to_gps", "lat": 37.050, "lon": -121.980, "alt_m": 40},
             {"type": "return_home"}, {"type": "land"}]}]}
CROSSING = {"label": "Straight", "rationale": "through the zone", "recommended": True,
            "per_drone": [{"drone_id": "alpha", "phases": [
                {"type": "arm_and_takeoff", "altitude_m": 30},
                {"type": "go_to_gps", "lat": 37.005, "lon": -121.985, "alt_m": 40},
                {"type": "return_home"}, {"type": "land"}]}]}


def _event(drone_id, conv_id, body):
    return {"pathParameters": {"droneId": drone_id, "conversationId": conv_id},
            "body": json.dumps(body)}


def _seed(dynamo):
    # user u1 owns alpha; alpha has a last-known GPS position (home).
    dynamo.Table(conversations.DRONE_TABLE).put_item(
        Item={"userId": "u1", "droneId": "alpha"})
    dynamo.Table(conversations.STATUS_TABLE).put_item(
        Item={"droneId": "alpha",
              "position": {"latitude": 37.005, "longitude": -122.030}})


def test_plan_then_select_dispatches_recommended_plan():
    dynamo, iot = FakeDynamo(), FakeIoT()
    _seed(dynamo)
    with patch.object(conversations, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations, "get_user_id", return_value="u1"), \
         patch.object(conversations.llm, "invoke", return_value={"content": [
             {"type": "tool_use", "name": "propose_plans",
              "input": {"plans": [CROSSING, CLEAN]}}]}):

        # Step 4: request plans.
        resp = conversations.plan_handler(
            _event("alpha", "c1", {"message": "find the pickup truck",
                                   "no_fly_zones": [NFZ], "drone_ids": ["alpha"]}),
            None)
        assert resp["statusCode"] == 200
        out = json.loads(resp["body"])
        assert len(out["plans"]) == 2
        # The NFZ-clear plan is recommended despite the model picking the other.
        rec_id = out["recommended_plan_id"]
        rec = next(p for p in out["plans"] if p["plan_id"] == rec_id)
        assert rec["label"] == "North detour" and rec["nfz_clear"]

        # Step 5: "go" with the recommended plan.
        sel = conversations.plan_select_handler(
            _event("alpha", "c1", {"plan_id": rec_id}), None)
        assert sel["statusCode"] == 200
        assert json.loads(sel["body"])["dispatched"] == ["alpha"]

    # A mission command hit the command topic with the clean plan's phases.
    cmds = [p for p in iot.published
            if p["topic"] == "drone/alpha/chat/c1/command"]
    assert len(cmds) == 1
    assert cmds[0]["payload"]["action"] == "mission"
    gps = [ph for ph in cmds[0]["payload"]["phases"] if ph.get("type") == "go_to_gps"]
    assert gps and gps[0]["lat"] == 37.050  # the north-detour waypoint


def test_select_unknown_plan_id_404s():
    dynamo, iot = FakeDynamo(), FakeIoT()
    _seed(dynamo)
    with patch.object(conversations, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations, "get_user_id", return_value="u1"):
        resp = conversations.plan_select_handler(
            _event("alpha", "c1", {"plan_id": "plan-99"}), None)
    assert resp["statusCode"] == 404


def test_plan_requires_ownership_of_every_fleet_member():
    dynamo, iot = FakeDynamo(), FakeIoT()
    _seed(dynamo)  # u1 owns alpha but NOT bravo
    with patch.object(conversations, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations, "get_user_id", return_value="u1"):
        resp = conversations.plan_handler(
            _event("alpha", "c1", {"message": "go", "drone_ids": ["alpha", "bravo"]}),
            None)
    assert resp["statusCode"] == 403


# --- mission-record persistence + follow-up query context -------------------
# Guards the operator follow-up feature: a completed mission's structured
# result (landmarks, target, photos) must survive response_handler and come
# back out through message_handler's system-prompt context on the NEXT
# message in the same conversation, so "did you also see a car?" and "fly
# back to X" have real data to answer/anchor from — see
# _build_mission_record / _append_mission_record / _render_mission_context.

def _mission_response_payload(drone_id, conv, mission_id, landmarks=None,
                               target_location=None, extra_result=None):
    """Shape of drone/{id}/chat/{conv}/response, matching
    fw_gcs_daemon.build_response_payload's output."""
    result = {
        "success": True, "summary": "Found the pickup truck.",
        "phases_completed": 4, "total_phases": 4,
        "findings": ["memory: pickup truck at (412.0, -38.0, 0.0) score=0.90 hits=3"],
        "photos": [], "landmarks": landmarks or [],
        "duration_seconds": 62.0, "actions_taken": 9, "failure_reason": None,
        "mission_id": mission_id,
    }
    if target_location:
        result["target_location"] = target_location
    if extra_result:
        result.update(extra_result)
    return {
        "droneId": drone_id, "conversation_id": conv, "mission_id": mission_id,
        "result": result, "image_urls": [],
        "timestamp": "2026-08-18T00:00:00+00:00",
    }


LANDMARKS = [
    {"label": "pickup truck", "east_m": 412.0, "north_m": -38.0, "alt_m": 0.0,
     "score": 0.93, "hits": 3, "image_url": None},
    {"label": "car", "east_m": 388.2, "north_m": 12.5, "alt_m": 0.0,
     "score": 0.88, "hits": 6, "image_url": "http://gcs:8080/images/car.jpg"},
]
TARGET = {"label": "pickup truck", "east_m": 412.0, "north_m": -38.0,
         "score": 0.93, "lat": 37.4048, "lon": -122.0951}


def test_response_handler_persists_mission_record():
    dynamo, iot = FakeDynamo(), FakeIoT()
    payload = _mission_response_payload("alpha", "c1", "m1", LANDMARKS, TARGET)
    with patch.object(conversations, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations.llm, "invoke", side_effect=RuntimeError("no model")):
        conversations.response_handler(payload, None)

        records = conversations._load_mission_records("alpha", "c1")
        assert len(records) == 1
        rec = records[0]
        assert rec["mission_id"] == "m1"
        assert rec["success"] is True
        assert rec["target_location"] == TARGET
        assert rec["landmarks"] == LANDMARKS  # includes the non-target "car"
        # The prose summary still gets saved (deterministic fallback, since
        # the model call was made to fail above) — persistence doesn't
        # replace it.
        history = conversations.get_conversation_history("alpha", "c1")
        assert any(m["role"] == "assistant" for m in history)


def test_response_handler_persists_even_when_photos_present():
    # A mission that also captured an explicit operator-requested photo
    # routes through response_handler's `elif image_urls:` branch instead of
    # the `else: is_mission` branch — the record must still be written (see
    # the up-front is_mission_result check), or a mission with photos would
    # silently never get landmark persistence.
    dynamo, iot = FakeDynamo(), FakeIoT()
    payload = _mission_response_payload("alpha", "c1", "m1", LANDMARKS,
                                        extra_result={"photos": ["http://gcs:8080/images/x.jpg"]})
    payload["image_urls"] = ["http://gcs:8080/images/x.jpg"]
    with patch.object(conversations, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations, "generate_presigned_url", side_effect=lambda u: u), \
         patch.object(conversations, "call_agent", return_value={"message": "here it is"}):
        conversations.response_handler(payload, None)

        records = conversations._load_mission_records("alpha", "c1")
        assert len(records) == 1 and records[0]["landmarks"] == LANDMARKS


def test_mission_records_trim_to_last_3():
    dynamo, iot = FakeDynamo(), FakeIoT()
    with patch.object(conversations, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations.llm, "invoke", side_effect=RuntimeError("no model")):
        for i in range(5):
            payload = _mission_response_payload("alpha", "c1", f"m{i}")
            conversations.response_handler(payload, None)

        records = conversations._load_mission_records("alpha", "c1")
        assert len(records) == 3
        # Newest last, oldest two (m0, m1) dropped.
        assert [r["mission_id"] for r in records] == ["m2", "m3", "m4"]


def test_mission_records_are_scoped_per_drone_pk():
    dynamo, iot = FakeDynamo(), FakeIoT()
    with patch.object(conversations, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations.llm, "invoke", side_effect=RuntimeError("no model")):
        conversations.response_handler(
            _mission_response_payload("alpha", "c1", "m-alpha", LANDMARKS), None)
        conversations.response_handler(
            _mission_response_payload("bravo", "c1", "m-bravo", []), None)

        alpha_records = conversations._load_mission_records("alpha", "c1")
        bravo_records = conversations._load_mission_records("bravo", "c1")
        assert len(alpha_records) == 1 and alpha_records[0]["landmarks"] == LANDMARKS
        assert len(bravo_records) == 1 and bravo_records[0]["landmarks"] == []


def _agent_text_result(action_dict):
    """An Anthropic-messages-shaped model response whose text is the JSON
    call_mission_agent expects to parse (see conversations.py:1024-1030)."""
    import json as _json
    return {"content": [{"type": "text", "text": _json.dumps(action_dict)}]}


def test_message_handler_injects_mission_context_for_vlm_drone():
    dynamo, iot = FakeDynamo(), FakeIoT()
    _seed(dynamo)
    dynamo.Table(conversations.STATUS_TABLE).put_item(
        Item={"droneId": "alpha", "capabilities": {"vlm_available": True}})
    # Seed a completed mission's structured record directly (bypassing
    # response_handler — this test is only about message_handler's read side).
    with patch.object(conversations, "dynamodb", dynamo):
        conversations._append_mission_record(
            "alpha", "c1",
            conversations._build_mission_record(
                _mission_response_payload("alpha", "c1", "m1", LANDMARKS, TARGET),
                _mission_response_payload("alpha", "c1", "m1", LANDMARKS, TARGET)["result"]))

    captured = {}

    def fake_invoke(system, messages, **kwargs):
        captured["system"] = system
        return _agent_text_result({"action": "respond", "message": "Yes, a car too."})

    with patch.object(conversations, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations, "get_user_id", return_value="u1"), \
         patch.object(conversations.llm, "invoke", side_effect=fake_invoke):
        resp = conversations.message_handler(
            _event("alpha", "c1", {"message": "did you also see a car?"}), None)

    assert resp["statusCode"] == 200
    assert "system" in captured
    assert "COMPLETED MISSION DATA" in captured["system"]
    assert '"east_m": 388.2' in captured["system"]  # the car's landmark data
    assert "go_to_gps" in captured["system"]


def test_message_handler_omits_context_when_no_prior_missions():
    dynamo, iot = FakeDynamo(), FakeIoT()
    _seed(dynamo)
    dynamo.Table(conversations.STATUS_TABLE).put_item(
        Item={"droneId": "alpha", "capabilities": {"vlm_available": True}})

    captured = {}

    def fake_invoke(system, messages, **kwargs):
        captured["system"] = system
        return _agent_text_result({"action": "respond", "message": "ok"})

    with patch.object(conversations, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations, "get_user_id", return_value="u1"), \
         patch.object(conversations.llm, "invoke", side_effect=fake_invoke):
        conversations.message_handler(
            _event("alpha", "c1", {"message": "hello"}), None)

    assert "COMPLETED MISSION DATA" not in captured["system"]


def test_summarize_mission_excludes_landmarks_from_prompt():
    result = {
        "success": True, "summary": "ok", "phases_completed": 4, "total_phases": 4,
        "landmarks": LANDMARKS, "photos": [],
    }
    captured = {}

    def fake_invoke(system, messages, **kwargs):
        captured["prompt"] = messages[0]["content"][0]["text"]
        return {"content": [{"type": "text", "text": "Summary text."}]}

    with patch.object(conversations.llm, "invoke", side_effect=fake_invoke):
        conversations.summarize_mission([], "alpha", result)

    assert "image_url" not in captured["prompt"]  # landmarks array excluded
    assert "Distinct objects mapped: 2" in captured["prompt"]
