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
