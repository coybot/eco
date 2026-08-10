"""
Unit tests for gcs/local_aws.py's LocalDynamo/LocalIoTData/LocalS3 shims,
exercised against the exact call patterns found in aws/src/{conversations,
handler,drones,groups}.py (see the grep inventory in the GCS implementation
plan) - not a general DynamoDB emulation, just enough to run those modules
unmodified against local storage.

Run: cd eco/gcs && python3 -m pytest tests/test_local_aws.py -v
"""
import sys
import tempfile
from pathlib import Path

import pytest
from boto3.dynamodb.conditions import Key

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from local_aws import LocalDynamo, LocalIoTData, LocalS3  # noqa: E402


@pytest.fixture
def dynamo(tmp_path):
    return LocalDynamo(tmp_path / "dynamo.sqlite3")


# --- drones.py: register_handler / get_item / query by userId ------------------

def test_register_then_get_by_composite_key(dynamo):
    table = dynamo.Table("drone-registry-dev")
    table.put_item(Item={"userId": "u1", "droneId": "d1", "name": "d1", "status": "registered"})

    got = table.get_item(Key={"userId": "u1", "droneId": "d1"})
    assert got["Item"]["name"] == "d1"

    missing = table.get_item(Key={"userId": "u1", "droneId": "nope"})
    assert missing == {}


def test_query_by_userid_string_expression(dynamo):
    table = dynamo.Table("drone-registry-dev")
    table.put_item(Item={"userId": "u1", "droneId": "d1"})
    table.put_item(Item={"userId": "u1", "droneId": "d2"})
    table.put_item(Item={"userId": "u2", "droneId": "d3"})

    resp = table.query(KeyConditionExpression="userId = :uid", ExpressionAttributeValues={":uid": "u1"})
    ids = sorted(it["droneId"] for it in resp["Items"])
    assert ids == ["d1", "d2"]


def test_query_gsi_by_droneid_ignores_index_name(dynamo):
    """drones.py's register_handler queries IndexName='droneId-index' to check
    global droneId uniqueness across users - the shim ignores IndexName and
    filters all items, which is semantically equivalent for this use."""
    table = dynamo.Table("drone-registry-dev")
    table.put_item(Item={"userId": "u1", "droneId": "shared-id"})

    resp = table.query(IndexName="droneId-index", KeyConditionExpression="droneId = :did",
                        ExpressionAttributeValues={":did": "shared-id"})
    assert len(resp["Items"]) == 1
    assert resp["Items"][0]["userId"] == "u1"


def test_delete_item(dynamo):
    table = dynamo.Table("drone-registry-dev")
    table.put_item(Item={"userId": "u1", "droneId": "d1"})
    table.delete_item(Key={"userId": "u1", "droneId": "d1"})
    assert table.get_item(Key={"userId": "u1", "droneId": "d1"}) == {}


# --- drones.py: update_item (rename, battery-config, wifi) ---------------------

def test_update_item_set_with_aliases_returns_all_new(dynamo):
    table = dynamo.Table("drone-registry-dev")
    table.put_item(Item={"userId": "u1", "droneId": "d1", "name": "old"})

    resp = table.update_item(
        Key={"userId": "u1", "droneId": "d1"},
        UpdateExpression="SET #n = :name, #ua = :ua",
        ExpressionAttributeNames={"#n": "name", "#ua": "updatedAt"},
        ExpressionAttributeValues={":name": "new", ":ua": "2026-08-10T00:00:00Z"},
        ReturnValues="ALL_NEW",
    )
    assert resp["Attributes"]["name"] == "new"
    assert resp["Attributes"]["updatedAt"] == "2026-08-10T00:00:00Z"

    got = table.get_item(Key={"userId": "u1", "droneId": "d1"})
    assert got["Item"]["name"] == "new"


def test_update_item_without_return_values(dynamo):
    table = dynamo.Table("drone-registry-dev")
    table.put_item(Item={"userId": "u1", "droneId": "d1"})
    resp = table.update_item(
        Key={"userId": "u1", "droneId": "d1"},
        UpdateExpression="SET #bc = :config",
        ExpressionAttributeNames={"#bc": "batteryConfig"},
        ExpressionAttributeValues={":config": {"cellEmptyVoltage": 3.0}},
    )
    assert resp == {}
    got = table.get_item(Key={"userId": "u1", "droneId": "d1"})
    assert got["Item"]["batteryConfig"]["cellEmptyVoltage"] == 3.0


# --- conversations.py: CONVERSATIONS_TABLE (PK, SK) message history -----------

def test_query_begins_with_and_scan_index_forward(dynamo):
    table = dynamo.Table("drone-chat-conversations-dev")
    table.put_item(Item={"PK": "CONV#d1#c1", "SK": "MSG#0002", "content": "second"})
    table.put_item(Item={"PK": "CONV#d1#c1", "SK": "MSG#0001", "content": "first"})
    table.put_item(Item={"PK": "CONV#d1#c1", "SK": "META", "content": "not a message"})

    resp = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk_prefix)",
        ExpressionAttributeValues={":pk": "CONV#d1#c1", ":sk_prefix": "MSG#"},
        ScanIndexForward=True,
        Limit=20,
    )
    contents = [it["content"] for it in resp["Items"]]
    assert contents == ["first", "second"]


# --- drones.py: LOGS_TABLE (droneId, timestamp) query with optional since -----

def test_logs_query_with_since_and_scan_index_forward_false(dynamo):
    table = dynamo.Table("drone-logs-dev")
    table.put_item(Item={"droneId": "d1", "timestamp": "100_a", "level": "INFO"})
    table.put_item(Item={"droneId": "d1", "timestamp": "200_b", "level": "INFO"})
    table.put_item(Item={"droneId": "d1", "timestamp": "300_c", "level": "WARN"})

    resp = table.query(
        KeyConditionExpression="droneId = :did AND #ts > :since",
        ExpressionAttributeValues={":did": "d1", ":since": "100_a"},
        ExpressionAttributeNames={"#ts": "timestamp"},
        ScanIndexForward=False,
        Limit=10,
    )
    timestamps = [it["timestamp"] for it in resp["Items"]]
    assert timestamps == ["300_c", "200_b"]  # newest first


def test_logs_query_with_boto3_key_object(dynamo):
    """conversations.py's status-fallback path uses
    Key('droneId').eq(drone_id) directly instead of a string expression."""
    table = dynamo.Table("drone-logs-dev")
    table.put_item(Item={"droneId": "d1", "timestamp": "100_a"})
    table.put_item(Item={"droneId": "d1", "timestamp": "200_b"})

    resp = table.query(KeyConditionExpression=Key("droneId").eq("d1"), ScanIndexForward=False, Limit=1)
    assert len(resp["Items"]) == 1
    assert resp["Items"][0]["timestamp"] == "200_b"


# --- handler.py / conversations.py: scan with FilterExpression -----------------

def test_scan_with_filter_expression(dynamo):
    table = dynamo.Table("drone-registry-dev")
    table.put_item(Item={"userId": "u1", "droneId": "d1", "vehicleType": "rover"})
    table.put_item(Item={"userId": "u2", "droneId": "d2", "vehicleType": "quadcopter"})

    resp = table.scan(FilterExpression="droneId = :d", ExpressionAttributeValues={":d": "d1"})
    assert len(resp["Items"]) == 1
    assert resp["Items"][0]["vehicleType"] == "rover"


# --- groups.py: Key(...) via imported helper ------------------------------------

def test_groups_query_with_key_helper(dynamo):
    table = dynamo.Table("drone-groups-dev")
    table.put_item(Item={"userId": "u1", "groupId": "g1", "name": "Fleet A"})
    table.put_item(Item={"userId": "u2", "groupId": "g2", "name": "Fleet B"})

    resp = table.query(KeyConditionExpression=Key("userId").eq("u1"))
    assert [it["groupId"] for it in resp["Items"]] == ["g1"]


# --- put_item re-puts update the same row, not a duplicate ---------------------

def test_reput_same_key_updates_in_place(dynamo):
    table = dynamo.Table("drone-status-dev")
    table.put_item(Item={"droneId": "d1", "battery": 80})
    table.put_item(Item={"droneId": "d1", "battery": 42})

    resp = table.scan()
    assert len(resp["Items"]) == 1
    assert resp["Items"][0]["battery"] == 42


# --- Decimal round-trip (drones.py's _to_dynamodb_types) ------------------------

def test_decimal_values_stored_as_native_numbers(dynamo):
    from decimal import Decimal
    table = dynamo.Table("drone-registry-dev")
    table.put_item(Item={"userId": "u1", "droneId": "d1", "batteryConfig": {"cellEmptyVoltage": Decimal("3.3")}})
    got = table.get_item(Key={"userId": "u1", "droneId": "d1"})
    assert got["Item"]["batteryConfig"]["cellEmptyVoltage"] == 3.3
    assert isinstance(got["Item"]["batteryConfig"]["cellEmptyVoltage"], float)


# --- persistence across LocalDynamo instances (same sqlite file) ---------------

def test_persists_across_instances(tmp_path):
    db_path = tmp_path / "dynamo.sqlite3"
    LocalDynamo(db_path).Table("t").put_item(Item={"userId": "u1", "droneId": "d1", "v": 1})
    reopened = LocalDynamo(db_path).Table("t").get_item(Key={"userId": "u1", "droneId": "d1"})
    assert reopened["Item"]["v"] == 1


# --- LocalIoTData ----------------------------------------------------------------

class _FakeMqttClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))


def test_iot_publish_forwards_to_mqtt_client(tmp_path):
    mqtt = _FakeMqttClient()
    iot = LocalIoTData(mqtt, tmp_path / "shadows")
    iot.publish(topic="drone/d1/command", payload='{"a":1}', qos=1)
    assert mqtt.published == [("drone/d1/command", b'{"a":1}', 1)]


def test_iot_update_thing_shadow_merges_desired_state(tmp_path):
    mqtt = _FakeMqttClient()
    iot = LocalIoTData(mqtt, tmp_path / "shadows")
    iot.update_thing_shadow(thingName="d1", payload=json_dumps_bytes({"state": {"desired": {"a": 1}}}))
    iot.update_thing_shadow(thingName="d1", payload=json_dumps_bytes({"state": {"desired": {"b": 2}}}))

    shadow_file = tmp_path / "shadows" / "d1.json"
    import json
    saved = json.loads(shadow_file.read_text())
    assert saved["state"]["desired"] == {"a": 1, "b": 2}


def json_dumps_bytes(obj):
    import json
    return json.dumps(obj).encode("utf-8")


# --- LocalS3 -----------------------------------------------------------------------

def test_s3_presigned_url_points_at_local_images_route(tmp_path):
    s3 = LocalS3("http://127.0.0.1:8080", tmp_path / "images", sign_fn=lambda key, exp: "TOKEN")
    url = s3.generate_presigned_url("put_object", Params={"Bucket": "b", "Key": "drones/d1/x.jpg"}, ExpiresIn=300)
    assert url == "http://127.0.0.1:8080/images/drones/d1/x.jpg?token=TOKEN"


def test_s3_public_image_url(tmp_path):
    s3 = LocalS3("http://127.0.0.1:8080/", tmp_path / "images", sign_fn=lambda k, e: "T")
    assert s3.public_image_url("k1") == "http://127.0.0.1:8080/images/k1"
