"""A flight command waits for the operator's OK when the app asks for that.

Real incident: "take a photo" came back from the planner as "Take off, fly to
1 meter, and take a photo", flew at once, and the copter crashed because the
operator wasn't ready. With confirm_flight the command is built and held; only
an explicit "go" for that same held command sends it to the drone.

Run: cd eco/aws/src && python3 -m pytest test_confirm_flight.py -v
"""
import json
from unittest.mock import patch

import pytest

import conversations
from test_plan_handlers import FakeDynamo, FakeIoT, FakeTable, _event, heartbeat


def _delete_item(self, Key):
    self.items = [it for it in self.items if any(it.get(k) != v for k, v in Key.items())]


PHASES = [{"type": "arm_and_takeoff", "altitude_m": 1}, {"type": "capture_photo"}, {"type": "land"}]
SAID = "Take off, fly to 1 meter, and take a photo."


@pytest.fixture
def stack():
    dynamo, iot = FakeDynamo(), FakeIoT()
    dynamo.Table(conversations.DRONE_TABLE).put_item(Item={"userId": "u1", "droneId": "alpha"})
    dynamo.Table(conversations.STATUS_TABLE).put_item(Item=heartbeat("alpha", 37.0, -122.0))
    agent = {"action": "mission", "message": SAID, "mission": {"phases": PHASES}}
    with patch.object(FakeTable, "delete_item", _delete_item, create=True), \
         patch.object(conversations, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations, "get_user_id", return_value="u1"), \
         patch.object(conversations, "get_drone_capabilities", return_value={"has_vlm": True}), \
         patch.object(conversations, "call_mission_agent", return_value=agent):
        yield dynamo, iot


def commands(iot):
    return [p["payload"] for p in iot.published if p["topic"].endswith("/command")]


def send(body):
    resp = conversations.message_handler(_event("alpha", "c1", body), None)
    assert resp["statusCode"] == 200
    return json.loads(resp["body"])


def test_a_mission_is_held_and_the_prompt_says_what_it_will_do(stack):
    _, iot = stack
    out = send({"message": "take a photo", "confirm_flight": True})
    assert out["status"] == "needs_confirmation"
    assert out["prompt"] == SAID
    assert out["steps"] == ["arm_and_takeoff(altitude_m=1)", "capture_photo", "land"]
    assert commands(iot) == []


def test_ok_flies_exactly_the_held_mission(stack):
    _, iot = stack
    held = send({"message": "take a photo", "confirm_flight": True})
    out = send({"pending_id": held["pending_id"], "decision": "go"})
    assert out["status"] == "sent"
    assert out["immediate_response"]["content"] == {"type": "loading", "text": SAID}
    [cmd] = commands(iot)
    assert cmd["action"] == "mission" and cmd["phases"] == PHASES
    assert cmd["mission_id"] == out["mission_id"]


def test_ok_twice_flies_once(stack):
    _, iot = stack
    held = send({"message": "take a photo", "confirm_flight": True})
    send({"pending_id": held["pending_id"], "decision": "go"})
    again = send({"pending_id": held["pending_id"], "decision": "go"})
    assert again["status"] == "expired"
    assert len(commands(iot)) == 1


def test_no_cancels_and_says_so_in_the_chat(stack):
    _, iot = stack
    held = send({"message": "take a photo", "confirm_flight": True})
    out = send({"pending_id": held["pending_id"], "decision": "cancel"})
    assert out["status"] == "cancelled"
    assert "Nothing was sent to the drone" in out["immediate_response"]["content"]["text"]
    assert commands(iot) == []
    # ...and the planner's history records it as cancelled, not flown.
    saved = [it for it in stack[0].Table(conversations.CONVERSATIONS_TABLE).items
             if str(it.get("SK", "")).startswith("MSG#") and it["sender"] == "drone"]
    assert len(saved) == 1 and SAID in saved[0]["content"] and "sent" not in saved[0]


def test_anything_but_go_cancels(stack):
    _, iot = stack
    held = send({"message": "take a photo", "confirm_flight": True})
    assert send({"pending_id": held["pending_id"]})["status"] == "cancelled"
    assert commands(iot) == []


def test_a_new_message_drops_the_held_command(stack):
    _, iot = stack
    held = send({"message": "take a photo", "confirm_flight": True})
    with patch.object(conversations, "call_mission_agent",
                      return_value={"action": "respond", "message": "Battery is 90%."}):
        send({"message": "what's the battery?", "confirm_flight": True})
    assert send({"pending_id": held["pending_id"], "decision": "go"})["status"] == "expired"
    assert commands(iot) == []


def test_a_stale_confirm_does_not_fly(stack):
    dynamo, iot = stack
    held = send({"message": "take a photo", "confirm_flight": True})
    table = dynamo.Table(conversations.CONVERSATIONS_TABLE)
    item = table.get_item(Key=conversations._pending_flight_key("alpha", "c1"))["Item"]
    item["expiresAt"] = 0
    table.put_item(Item=item)
    assert send({"pending_id": held["pending_id"], "decision": "go"})["status"] == "expired"
    assert commands(iot) == []


def test_ok_for_a_drone_that_went_offline_does_not_fly(stack):
    _, iot = stack
    held = send({"message": "take a photo", "confirm_flight": True})
    with patch.object(conversations, "is_drone_online", return_value=False):
        out = send({"pending_id": held["pending_id"], "decision": "go"})
    assert out["status"] == "error"
    assert commands(iot) == []


def test_execute_is_held_too_and_keeps_its_message_id(stack):
    _, iot = stack
    with patch.object(conversations, "get_drone_capabilities", return_value={"has_vlm": False}), \
         patch.object(conversations, "call_agent",
                      return_value={"action": "execute", "message": "Taking off to 1 m.",
                                    "code": "takeoff(1)"}):
        held = send({"message": "take off to 1 meter", "confirm_flight": True})
    assert held["status"] == "needs_confirmation"
    assert commands(iot) == []
    out = send({"pending_id": held["pending_id"], "decision": "go"})
    [cmd] = commands(iot)
    assert cmd["code"] == "takeoff(1)" and cmd["message_id"] == out["message_id"]


def test_without_confirm_flight_it_flies_at_once_as_before(stack):
    _, iot = stack
    out = send({"message": "take a photo"})
    assert out["status"] == "sent"
    [cmd] = commands(iot)
    assert cmd["phases"] == PHASES and cmd["mission_id"] == out["mission_id"]


def test_stop_is_never_held(stack):
    _, iot = stack
    out = send({"message": "stop", "confirm_flight": True})
    assert out["status"] == "sent"
    assert commands(iot)[0]["action"] == "abort"


# --- group (fleet) chat --------------------------------------------------------

import groups  # noqa: E402

ORDERS = {"alpha": "Take off to 1 m and take a photo.", "bravo": "Hold position."}


@pytest.fixture
def fleet():
    dynamo, iot = FakeDynamo(), FakeIoT()
    dynamo.Table(groups.GROUPS_TABLE).put_item(
        Item={"userId": "u1", "groupId": "grp-1", "members": ["alpha", "bravo"]})
    split = {"content": [{"type": "text", "text": json.dumps(ORDERS)}]}
    with patch.object(FakeTable, "delete_item", _delete_item, create=True), \
         patch.object(conversations, "dynamodb", dynamo), \
         patch.object(groups, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations, "get_user_id", return_value="u1"), \
         patch.object(groups.llm, "invoke", return_value=split), \
         patch.object(conversations, "generate_code", side_effect=lambda order, *a, **k: f"# {order}"):
        yield dynamo, iot


def send_group(body):
    resp = groups.message_handler(
        {"pathParameters": {"groupId": "grp-1", "conversationId": "c1"},
         "body": json.dumps(body)}, None)
    assert resp["statusCode"] == 200
    return json.loads(resp["body"])


def test_fleet_orders_are_held_and_listed_per_drone(fleet):
    _, iot = fleet
    out = send_group({"message": "everyone take a photo", "confirm_flight": True})
    assert out["status"] == "needs_confirmation"
    assert out["prompt"] == "Send these orders to 2 drones."
    assert out["steps"] == [f"{d}: {o}" for d, o in ORDERS.items()]
    assert commands(iot) == []


def test_fleet_ok_sends_every_held_order_once(fleet):
    _, iot = fleet
    held = send_group({"message": "everyone take a photo", "confirm_flight": True})
    out = send_group({"pending_id": held["pending_id"], "decision": "go"})
    assert out["status"] == "sent" and out["orders"] == ORDERS
    sent = {p["topic"].split("/")[1]: p["payload"]["code"]
            for p in iot.published if p["topic"].endswith("/command")}
    assert sent == {d: f"# {o}" for d, o in ORDERS.items()}
    assert send_group({"pending_id": held["pending_id"], "decision": "go"})["status"] == "expired"
    assert len(commands(iot)) == 2


def test_fleet_no_cancels_everything(fleet):
    _, iot = fleet
    held = send_group({"message": "everyone take a photo", "confirm_flight": True})
    out = send_group({"pending_id": held["pending_id"], "decision": "cancel"})
    assert out["status"] == "cancelled"
    assert "Nothing was sent to the fleet" in out["immediate_response"]["content"]["text"]
    assert commands(iot) == []


def test_fleet_new_message_drops_held_orders(fleet):
    _, iot = fleet
    held = send_group({"message": "everyone take a photo", "confirm_flight": True})
    send_group({"message": "everyone land", "confirm_flight": True})
    assert send_group({"pending_id": held["pending_id"], "decision": "go"})["status"] == "expired"
    assert commands(iot) == []


def test_fleet_without_confirm_flight_dispatches_at_once(fleet):
    _, iot = fleet
    out = send_group({"message": "everyone take a photo"})
    assert out["orders"] == ORDERS
    assert len(commands(iot)) == 2
