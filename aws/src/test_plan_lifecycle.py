"""The fleet plan's whole life through the handlers: choosing vehicles from
the operator's fleet, each from its own home, approval, the re-check at "go",
and after launch - status, abort, retask then approve. In-memory DynamoDB /
IoT fakes and a stubbed model; no AWS, no broker.

Run: python3 -m pytest aws/src/test_plan_lifecycle.py -v   (from eco/)
"""
import json
from contextlib import contextmanager
from unittest.mock import patch

import conversations
import planning
from test_plan_handlers import FakeDynamo, FakeIoT, heartbeat

REF = {"lat": 37.0, "lon": -122.0}


def ll(x, y):
    return planning.to_ll((x, y), REF)


AREA = [ll(0, 0), ll(400, 0), ll(400, 300), ll(0, 300)]
NFZ = [ll(150, 100), ll(250, 100), ll(250, 200), ll(150, 200)]
CALM = {"from_deg": 270, "speed_mps": 1.0}
NO_PLANS = {"content": [{"type": "text", "text": "{\"plans\": []}"}]}


def _event(body, method="POST", resource="/drones/{droneId}/conversations/{conversationId}/plan"):
    return {"pathParameters": {"droneId": "west", "conversationId": "c1"},
            "httpMethod": method, "resource": resource, "body": json.dumps(body)}


def _put(dynamo, *items):
    for it in items:
        dynamo.Table(conversations.STATUS_TABLE).put_item(Item=it)


def _own(dynamo, *ids, user="u1"):
    for did in ids:
        dynamo.Table(conversations.DRONE_TABLE).put_item(Item={"userId": user, "droneId": did})


def _fleet(dynamo):
    """Two quadcopters either side of the area, a rover, and one that is flat."""
    _own(dynamo, "west", "east", "rover", "flat")
    w, e, r, f = ll(-30, 150), ll(430, 150), ll(200, -20), ll(-30, 50)
    _put(dynamo,
         heartbeat("west", w["lat"], w["lon"], battery=100),
         heartbeat("east", e["lat"], e["lon"], battery=100),
         heartbeat("rover", r["lat"], r["lon"], battery=90, vehicle_type="rover", vlm=False),
         heartbeat("flat", f["lat"], f["lon"], battery=15))


@contextmanager
def stack(model=NO_PLANS):
    dynamo, iot = FakeDynamo(), FakeIoT()
    with patch.object(conversations, "dynamodb", dynamo), \
         patch.object(conversations, "iot", iot), \
         patch.object(conversations, "get_user_id", return_value="u1"), \
         patch.object(conversations.llm, "invoke", return_value=model):
        yield dynamo, iot


def _plan(body):
    resp = conversations.plan_handler(_event(body), None)
    assert resp["statusCode"] == 200, resp
    return json.loads(resp["body"])


def _select(body):
    resp = conversations.plan_select_handler(_event(body), None)
    return resp["statusCode"], json.loads(resp["body"])


def _commands(iot, did):
    return [p["payload"] for p in iot.published
            if p["topic"].startswith(f"drone/{did}/") and p["topic"].endswith("/command")]


TASK = {"message": "find the pickup truck", "operating_area": AREA, "no_fly_zones": [NFZ],
        "wind": CALM}


def test_with_no_ids_it_chooses_from_the_operators_fleet_and_says_who_it_left_out():
    with stack() as (dynamo, _):
        _fleet(dynamo)
        out = _plan(TASK)
    assert "flat" in out["excluded"]
    assert any("battery" in r for r in out["excluded"]["flat"])
    used = {d["drone_id"] for p in out["plans"] for d in p["per_drone"]}
    assert used and "flat" not in used
    rec = next(p for p in out["plans"] if p["recommended"])
    assert rec["dispatchable"]


def test_each_vehicle_flies_from_its_own_home():
    with stack() as (dynamo, _):
        _fleet(dynamo)
        out = _plan(dict(TASK, drone_ids=["west", "east"],
                         homes={"east": ll(430, 280)}))
    split = next(p for p in out["plans"] if p["source"] == "planner" and len(p["per_drone"]) == 2)
    by = {d["drone_id"]: d for d in split["per_drone"]}
    # Sectors face their own homes...
    cx = {k: planning.centroid(planning.poly_to_xy(d["sector"], REF))[0] for k, d in by.items()}
    assert cx["west"] < cx["east"]
    # ...and east's first leg starts from the home the request gave it.
    first = next(p for p in by["east"]["phases"] if p.get("type") == "go_to_gps")
    x, y = planning.to_xy(first, REF)
    assert y > 150


def test_a_vehicle_with_no_vision_model_is_only_given_a_sweep():
    with stack() as (dynamo, _):
        _fleet(dynamo)
        out = _plan(dict(TASK, drone_ids=["rover"]))
    for p in out["plans"]:
        for d in p["per_drone"]:
            assert not any(ph.get("objective") for ph in d["phases"])


def test_go_needs_the_warnings_acknowledged_and_then_carries_the_geofence():
    with stack() as (dynamo, iot):
        _fleet(dynamo)
        out = _plan(dict(TASK, drone_ids=["west", "east"]))
        rec = next(p for p in out["plans"] if p["recommended"])
        code, body = _select({"plan_id": rec["plan_id"]})
        assert code == 409 and body["status"] == "needs_approval"
        assert any("ASSUMED" in w["detail"] for w in body["warnings"])
        code, body = _select({"plan_id": rec["plan_id"], "acknowledge_warnings": True})
        assert code == 200
    for d in rec["per_drone"]:
        cmd = _commands(iot, d["drone_id"])[-1]
        assert cmd["action"] == "mission"
        assert cmd["geofence"]["keep_in"] and cmd["geofence"]["no_fly"] == [NFZ]


def test_a_blocked_plan_is_refused():
    with stack() as (dynamo, _):
        _fleet(dynamo)
        out = _plan(dict(TASK, drone_ids=["west", "east"],
                         message="photograph the whole area"))
        blocked = [p for p in out["plans"] if not p["dispatchable"]]
        if not blocked:   # every option fit: make one that cannot
            out["plans"][0]["dispatchable"] = False
            conversations._store_plan_options("west", "c1", out["plans"],
                                              conversations._plan_context(TASK))
            blocked = [out["plans"][0]]
        code, body = _select({"plan_id": blocked[0]["plan_id"], "acknowledge_warnings": True})
    assert code == 409 and body["status"] == "blocked"


def test_go_rechecks_against_the_vehicles_now():
    with stack() as (dynamo, iot):
        _fleet(dynamo)
        out = _plan(dict(TASK, drone_ids=["west"]))
        rec = next(p for p in out["plans"] if p["recommended"])
        # Between planning and "go" the pilot takes the aircraft.
        w = ll(-30, 150)
        _put(dynamo, heartbeat("west", w["lat"], w["lon"], battery=100, pilotControl="RC"))
        code, body = _select({"plan_id": rec["plan_id"], "acknowledge_warnings": True})
    assert code == 409 and body["reason"] == "changed since it was planned"
    assert any(b["kind"] == "pilot" for b in body["blocking"])
    assert not _commands(iot, "west")


def _launch(dynamo, ids=("west", "east")):
    out = _plan(dict(TASK, drone_ids=list(ids)))
    rec = next(p for p in out["plans"] if p["recommended"])
    code, body = _select({"plan_id": rec["plan_id"], "acknowledge_warnings": True})
    assert code == 200
    return rec, body


def test_status_reports_each_vehicle_in_the_running_plan():
    with stack() as (dynamo, _):
        _fleet(dynamo)
        rec, sent = _launch(dynamo)
        resp = conversations.plan_control_handler(_event({}, method="GET"), None)
    assert resp["statusCode"] == 200
    out = json.loads(resp["body"])
    assert out["plan_id"] == rec["plan_id"]
    assert set(out["vehicles"]) == set(sent["dispatched"])
    assert all(v["online"] for v in out["vehicles"].values())


def test_abort_stops_every_vehicle_in_the_plan_or_just_the_ones_named():
    with stack() as (dynamo, iot):
        _fleet(dynamo)
        _, sent = _launch(dynamo)
        ev = _event({"then": "land", "drone_ids": [sent["dispatched"][0]]},
                    resource="/drones/{droneId}/conversations/{conversationId}/plan/abort")
        one = json.loads(conversations.plan_control_handler(ev, None)["body"])
        ev = _event({}, resource="/drones/{droneId}/conversations/{conversationId}/plan/abort")
        everyone = json.loads(conversations.plan_control_handler(ev, None)["body"])
        bad = conversations.plan_control_handler(
            _event({"then": "explode"},
                   resource="/drones/{droneId}/conversations/{conversationId}/plan/abort"), None)
    assert one["aborted"] == [sent["dispatched"][0]] and one["then"] == "land"
    assert set(everyone["aborted"]) == set(sent["dispatched"])
    assert everyone["then"] == "return_home"
    assert bad["statusCode"] == 400
    aborts = [c for d in sent["dispatched"] for c in _commands(iot, d) if c["action"] == "abort"]
    assert len(aborts) == 1 + len(sent["dispatched"])


def test_abort_works_even_with_no_plan_on_record():
    with stack() as (dynamo, iot):
        _fleet(dynamo)
        ev = _event({}, resource="/drones/{droneId}/conversations/{conversationId}/plan/abort")
        out = json.loads(conversations.plan_control_handler(ev, None)["body"])
    assert out["aborted"] == ["west"]
    assert _commands(iot, "west")[-1]["action"] == "abort"


def test_retask_proposes_from_where_they_are_and_flies_only_once_approved():
    with stack() as (dynamo, iot):
        _fleet(dynamo)
        first, sent = _launch(dynamo)
        before = len(iot.published)
        # Mid-mission: west is airborne over the area, flying the first plan.
        w = ll(120, 250)
        _put(dynamo, heartbeat("west", w["lat"], w["lon"], battery=70, altitudeAgl=20.0,
                               armed=True, mission={"running": True,
                                                    "id": sent["mission_ids"].get("west")}))
        ev = _event({"message": "search the east half again for the truck",
                     "drone_ids": ["west"]},
                    resource="/drones/{droneId}/conversations/{conversationId}/plan/retask")
        resp = conversations.plan_control_handler(ev, None)
        assert resp["statusCode"] == 200
        out = json.loads(resp["body"])
        assert out["status"] == "proposed" and out["retask_of"] == first["plan_id"]
        assert len(iot.published) == before       # nothing flown yet
        rec = next(p for p in out["plans"] if p["recommended"])
        # It is costed from where it is (~180 m from home), not from home.
        here = planning._dist(planning.to_xy(ll(-30, 150), REF), planning.to_xy(w, REF))
        assert rec["per_drone"][0]["costing"]["max_dist_from_home_m"] >= here - 1
        code, body = _select({"plan_id": rec["plan_id"], "acknowledge_warnings": True})
        assert code == 200 and body["dispatched"] == ["west"]
        status = json.loads(conversations.plan_control_handler(
            _event({}, method="GET"), None)["body"])
    # The retask replaced west's mission and left east's plan running.
    assert set(status["vehicles"]) == set(sent["dispatched"])
    assert _commands(iot, "west")[-1]["mission_id"] == body["mission_ids"]["west"]


def test_retask_with_nothing_running_says_to_plan_first():
    with stack() as (dynamo, _):
        _fleet(dynamo)
        ev = _event({"message": "go again"},
                    resource="/drones/{droneId}/conversations/{conversationId}/plan/retask")
        assert conversations.plan_control_handler(ev, None)["statusCode"] == 409


def test_nothing_can_fly_says_why():
    with stack() as (dynamo, _):
        _own(dynamo, "west")
        f = ll(-30, 50)
        _put(dynamo, heartbeat("west", f["lat"], f["lon"], battery=10))
        out = _plan(dict(TASK, drone_ids=["west"]))
    assert out["status"] == "unavailable" and out["excluded"]["west"]
