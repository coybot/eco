"""publish_to_drone() must give every command its own identity.

The drone dedups on message_id and only hashes the payload when it is missing.
That fallback keyed on conversation_id + code, so commands with no code -
action=mission and action=set_goal - were indistinguishable inside one
conversation and the second was silently dropped. Stamping an id at the
publisher removes the ambiguity before it reaches the aircraft.

Run: cd eco/aws/src && python3 -m pytest test_publish_to_drone.py -v
"""
import json
from unittest.mock import patch

import conversations


def _published(payloads):
    """Capture what would go to IoT Core instead of publishing it."""
    def fake_publish(topic, qos, payload):
        payloads.append((topic, json.loads(payload)))
    return fake_publish


def test_every_command_gets_a_message_id():
    sent = []
    with patch.object(conversations.iot, "publish", _published(sent)):
        conversations.publish_to_drone("d-1", "c-1", {"action": "mission", "mission_id": "m-1"})
    _, payload = sent[0]
    assert payload["message_id"]


def test_two_code_less_missions_get_different_ids():
    """The regression, at the publisher: same conversation, no code either time."""
    sent = []
    with patch.object(conversations.iot, "publish", _published(sent)):
        conversations.publish_to_drone("d-1", "c-1", {"action": "mission", "mission_id": "m-1"})
        conversations.publish_to_drone("d-1", "c-1", {"action": "mission", "mission_id": "m-2"})
    assert sent[0][1]["message_id"] != sent[1][1]["message_id"]


def test_an_explicit_id_is_left_alone():
    """Callers that already track an id - execute passes the loading message's -
    stay authoritative, so the app and the drone agree on one identity."""
    sent = []
    with patch.object(conversations.iot, "publish", _published(sent)):
        conversations.publish_to_drone(
            "d-1", "c-1", {"action": "execute", "code": "takeoff()", "message_id": "mine"})
    assert sent[0][1]["message_id"] == "mine"


def test_the_caller_s_dict_is_not_mutated():
    """Callers reuse these dicts for logging and responses after publishing."""
    sent = []
    payload = {"action": "set_goal", "goal": "patrol"}
    with patch.object(conversations.iot, "publish", _published(sent)):
        conversations.publish_to_drone("d-1", "c-1", payload)
    assert "message_id" not in payload
    assert sent[0][1]["message_id"]


def test_topic_is_unchanged():
    sent = []
    with patch.object(conversations.iot, "publish", _published(sent)):
        conversations.publish_to_drone("d-1", "c-1", {"action": "execute", "code": "land()"})
    assert sent[0][0] == "drone/d-1/chat/c-1/command"
