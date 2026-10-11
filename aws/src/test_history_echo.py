"""The operator's message was shown to the model twice.

The send-message handler saves the operator's message and then loads the
history, so the history already ended with it; the agent call appended it
again. An operator asked the PX4 test aircraft to "Do an Arm test" and the reply began "Your
message came through twice".

Run: python3 -m pytest aws/src/test_history_echo.py -v
"""
from unittest.mock import patch

import conversations


def _capture(fn, history, message, **kw):
    seen = {}

    def fake_invoke(system, messages, **_):
        seen['messages'] = messages
        return {'content': [{'type': 'text', 'text': '{"action": "respond", "message": "ok"}'}]}

    with patch.object(conversations.llm, 'invoke', fake_invoke):
        fn(history, message, **kw)
    return [m for m in seen['messages'] if m['role'] == 'user']


def _texts(user_msgs):
    return [m['content'][0]['text'] for m in user_msgs]


def test_mission_agent_sees_the_message_once():
    history = [{'role': 'assistant', 'content': 'hi'},
               {'role': 'user', 'content': 'Do an Arm test'}]
    assert _texts(_capture(conversations.call_mission_agent, history, 'Do an Arm test')) == ['Do an Arm test']


def test_legacy_agent_sees_the_message_once():
    history = [{'role': 'user', 'content': 'Do an Arm test'}]
    assert _texts(_capture(conversations.call_agent, history, 'Do an Arm test')) == ['Do an Arm test']


def test_earlier_identical_requests_are_kept():
    """Only the trailing copy is the echo: a real repeat earlier in the chat stays."""
    history = [{'role': 'user', 'content': 'hover'}, {'role': 'assistant', 'content': 'refused'},
               {'role': 'user', 'content': 'hover'}]
    assert _texts(_capture(conversations.call_mission_agent, history, 'hover')) == ['hover', 'hover']


def test_a_different_last_message_is_not_dropped():
    history = [{'role': 'user', 'content': 'hover'}]
    assert _texts(_capture(conversations.call_mission_agent, history, 'land')) == ['hover', 'land']
