"""
Unit tests for rover.py's converse_handler — mocks llm.invoke (see llm.py) so this
runs instantly, free, and offline, regardless of which provider (Bedrock or a local
GCS model) is configured. Does NOT touch AWS or a real LLM endpoint; it is not a
substitute for exercising the deployed API Gateway route (see README note in this
file's docstring below for how to do that safely before/after a real deploy).

Run: python3 -m pytest control/test_rover_converse.py -v
"""
import json
from unittest.mock import patch

from control import rover


def _event(body=None, authorized=True):
    event = {"body": json.dumps(body) if body is not None else "{}"}
    if authorized:
        event["requestContext"] = {"authorizer": {"userId": "test-user-123"}}
    else:
        event["requestContext"] = {"authorizer": {}}
    return event


def _llm_response(text):
    return {"content": [{"type": "text", "text": text}]}


def test_rejects_unauthenticated():
    resp = rover.converse_handler(_event({"utterance": "hi"}, authorized=False), None)
    assert resp["statusCode"] == 401


def test_rejects_missing_utterance():
    resp = rover.converse_handler(_event({}), None)
    assert resp["statusCode"] == 400


def test_rejects_invalid_json():
    event = {"body": "not json", "requestContext": {"authorizer": {"userId": "u1"}}}
    resp = rover.converse_handler(event, None)
    assert resp["statusCode"] == 400


@patch.object(rover.llm, "invoke")
def test_returns_claude_reply_on_success(mock_invoke):
    mock_invoke.return_value = _llm_response("The lobby is to your left.")
    resp = rover.converse_handler(_event({"utterance": "where's the lobby"}), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"reply": "The lobby is to your left."}
    # Sanity: the ramp-agent system prompt (not a code-gen prompt) was actually used.
    system, messages = mock_invoke.call_args.args
    assert "ramp" in system.lower()
    assert messages[0]["content"][0]["text"] == "where's the lobby"


@patch.object(rover.llm, "invoke")
def test_llm_error_returns_500(mock_invoke):
    mock_invoke.side_effect = RuntimeError("throttled")
    resp = rover.converse_handler(_event({"utterance": "hi"}), None)
    assert resp["statusCode"] == 500


@patch.object(rover.llm, "invoke")
def test_empty_reply_falls_back_to_default_text(mock_invoke):
    mock_invoke.return_value = _llm_response("")
    resp = rover.converse_handler(_event({"utterance": "hi"}), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["reply"]  # non-empty fallback, not ""
