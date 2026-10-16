"""A drone reply handled more than once must still be one message.

The IoT rule invokes response_handler asynchronously and AWS reruns a failed
invocation up to twice; the drone's outbox can also resend a reply after a
reconnect. On 2026-10-16 a crash after the result was stored (an app message
over AWS IoT's 128 KB limit) left one survey result in the chat three times.

Run: cd eco/aws/src && python3 -m pytest test_reply_retry.py -v
"""
import json
from unittest.mock import patch

import pytest

import conversations as C


class ConditionalCheckFailed(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if ConditionExpression == "attribute_not_exists(PK)" and key in self.items:
            raise ConditionalCheckFailed()
        self.items[key] = dict(Item)

    def get_item(self, Key):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": dict(item)} if item else {}

    def messages(self):
        return [i for (pk, sk), i in self.items.items() if sk.startswith("MSG#")]


class FakeDynamo:
    def __init__(self):
        self.table = FakeTable()

    def Table(self, name):
        return self.table


class World:
    """DynamoDB, IoT and the LLM, all in the process."""

    def __init__(self):
        self.dynamo = FakeDynamo()
        self.published = []
        self.llm_calls = 0
        self.publish_fails = 0

    def publish(self, topic, qos, payload):
        if self.publish_fails:
            self.publish_fails -= 1
            raise RuntimeError("Lambda killed mid-reply")
        self.published.append(json.loads(payload))

    def agent(self, *a, **k):
        self.llm_calls += 1
        return {"message": f"summary #{self.llm_calls}"}

    def deliver(self, payload):
        with patch.object(C, "dynamodb", self.dynamo), \
             patch.object(C.iot, "publish", self.publish), \
             patch.object(C, "publish_to_app", self._publish_to_app), \
             patch.object(C, "get_conversation_history", lambda *a, **k: []), \
             patch.object(C, "call_agent", self.agent), \
             patch.object(C, "generate_presigned_url", lambda u: u):
            C.response_handler(json.loads(json.dumps(payload)), None)

    def _publish_to_app(self, *a, **k):
        # publish_to_app swallows IoT errors; a crash here stands in for the
        # Lambda dying between storing the message and telling the app.
        self.publish("app", 1, json.dumps({"args": [str(x) for x in a]}))


PHOTO = "https://b.s3.amazonaws.com/drones/d1/conversations/c1/20261016_1_a.jpg"


def _reply(ts="2026-10-16T17:02:00+00:00", **extra):
    p = {"droneId": "d1", "conversation_id": "c1", "mission_id": "m-1",
         "original_message": "scan the field",
         "result": {"success": True, "phases_completed": 4, "total_phases": 4},
         "image_urls": [PHOTO], "timestamp": ts}
    p.update(extra)
    return p


def test_a_reply_delivered_three_times_is_one_message_one_notice_one_summary():
    w = World()
    for _ in range(3):
        w.deliver(_reply())
    assert len(w.dynamo.table.messages()) == 1
    assert len(w.published) == 1
    assert w.llm_calls == 1


def test_a_crash_after_storing_then_a_retry_is_still_one_message():
    """The 2026-10-16 case: stored, then died before telling the app; AWS retried."""
    w = World()
    w.publish_fails = 1
    with pytest.raises(RuntimeError):
        w.deliver(_reply())
    assert len(w.dynamo.table.messages()) == 1 and w.published == []
    w.deliver(_reply())                       # AWS's retry
    w.deliver(_reply())                       # and its second one
    msgs = w.dynamo.table.messages()
    assert len(msgs) == 1, "the retry overwrote the same slot"
    assert len(w.published) == 1, "the app heard about it once, from the retry"
    assert msgs[0]["content"] == "summary #2"


def test_a_retry_does_not_duplicate_the_mission_record():
    w = World()
    w.publish_fails = 1
    with pytest.raises(RuntimeError):
        w.deliver(_reply())
    w.deliver(_reply())
    record = w.dynamo.table.items[("CONV#d1#c1", "MISSIONS")]
    assert len(json.loads(record["missions"])) == 1


def test_two_real_replies_are_two_messages():
    w = World()
    w.deliver(_reply(ts="2026-10-16T17:02:00+00:00"))
    w.deliver(_reply(ts="2026-10-16T17:09:00+00:00", mission_id="m-2"))
    assert len(w.dynamo.table.messages()) == 2
    assert len(w.published) == 2


def test_the_message_keeps_the_time_it_was_first_seen():
    """A retry minutes later must not move the reply after later messages."""
    w = World()
    w.publish_fails = 1
    with pytest.raises(RuntimeError):
        w.deliver(_reply())
    first = w.dynamo.table.messages()[0]["SK"]
    w.deliver(_reply())
    assert [m["SK"] for m in w.dynamo.table.messages()] == [first]


def test_a_reply_is_not_lost_when_its_record_cannot_be_written():
    w = World()

    def broken(Item, ConditionExpression=None):
        if Item["PK"].startswith("REPLY#"):
            raise RuntimeError("throttled")
        FakeTable.put_item(w.dynamo.table, Item)
    w.dynamo.table.put_item = broken
    w.deliver(_reply())
    assert len(w.dynamo.table.messages()) == 1 and len(w.published) == 1


def test_reply_records_stay_out_of_the_drones_conversation_index():
    w = World()
    w.deliver(_reply())
    rec = [i for (pk, _), i in w.dynamo.table.items.items() if pk.startswith("REPLY#")]
    assert rec and all("droneId" not in i for i in rec)
    assert rec[0]["status"] == "done"


def test_the_ground_station_storage_also_dedupes(tmp_path):
    """GCS mode runs these handlers on backends.local's LocalDynamo."""
    from backends.local import LocalDynamo
    w = World()
    w.dynamo = LocalDynamo(tmp_path / "dynamo.sqlite3")
    for _ in range(3):
        w.deliver(_reply())
    msgs = w.dynamo.Table("drone-conversations-dev").query(
        KeyConditionExpression="PK = :pk", ExpressionAttributeValues={":pk": "CONV#d1#c1"})["Items"]
    assert len([m for m in msgs if m["SK"].startswith("MSG#")]) == 1
    assert len(w.published) == 1
