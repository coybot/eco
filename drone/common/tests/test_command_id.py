"""Dedup identity for inbound commands.

The failure this guards against is silent in the worst way: a dropped command
looks like nothing happened, and the app sits at IN PROGRESS with no error. So
the bias is strict - anything that differs at all gets its own id - and the only
thing allowed to collapse two payloads is being byte-identical.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from command_id import compute_command_id  # noqa: E402


def test_explicit_message_id_wins():
    assert compute_command_id({"message_id": "abc", "code": "takeoff()"}) == "abc"


def test_explicit_id_beats_a_differing_body():
    """The publisher owns identity when it supplies one."""
    a = compute_command_id({"message_id": "same", "code": "takeoff()"})
    b = compute_command_id({"message_id": "same", "code": "land()"})
    assert a == b


def test_redelivery_of_the_same_payload_is_still_one_command():
    """MQTT QoS 1 replays identical bytes; that must still dedup."""
    payload = {"action": "mission", "mission_id": "m-1", "phases": [{"goal": "scan"}],
               "conversation_id": "c-1"}
    assert compute_command_id(payload) == compute_command_id(dict(payload))


def test_key_order_does_not_change_the_id():
    a = {"action": "mission", "mission_id": "m-1", "conversation_id": "c-1"}
    b = {"conversation_id": "c-1", "action": "mission", "mission_id": "m-1"}
    assert compute_command_id(a) == compute_command_id(b)


def test_two_missions_in_one_conversation_are_distinct():
    """The regression. Both lack `code`, so the old fallback hashed
    md5("c-1:") for each and dropped the second as a duplicate."""
    first = {"action": "mission", "mission_id": "m-1", "phases": [{"goal": "scan north"}],
             "conversation_id": "c-1", "original_message": "scan north"}
    second = {"action": "mission", "mission_id": "m-2", "phases": [{"goal": "scan south"}],
              "conversation_id": "c-1", "original_message": "scan south"}
    assert compute_command_id(first) != compute_command_id(second)


def test_two_goals_in_one_conversation_are_distinct():
    """action=set_goal carries no code either, and collided the same way."""
    first = {"action": "set_goal", "goal": "patrol the fence", "conversation_id": "c-1"}
    second = {"action": "set_goal", "goal": "return to base", "conversation_id": "c-1"}
    assert compute_command_id(first) != compute_command_id(second)


def test_mission_and_goal_do_not_collide():
    a = {"action": "mission", "conversation_id": "c-1"}
    b = {"action": "set_goal", "conversation_id": "c-1"}
    assert compute_command_id(a) != compute_command_id(b)


def test_same_code_in_different_conversations_is_distinct():
    a = {"action": "execute", "code": "takeoff()", "conversation_id": "c-1"}
    b = {"action": "execute", "code": "takeoff()", "conversation_id": "c-2"}
    assert compute_command_id(a) != compute_command_id(b)


def test_phases_are_part_of_the_identity():
    """Two missions can share an id upstream but differ in what they fly."""
    a = {"action": "mission", "mission_id": "m-1", "phases": [{"goal": "north"}]}
    b = {"action": "mission", "mission_id": "m-1", "phases": [{"goal": "south"}]}
    assert compute_command_id(a) != compute_command_id(b)


def test_unserialisable_values_do_not_raise():
    """A bad payload must not take down the command handler."""
    class Weird:
        pass
    assert compute_command_id({"action": "execute", "thing": Weird()})


def test_empty_payload_is_handled():
    assert compute_command_id({})
