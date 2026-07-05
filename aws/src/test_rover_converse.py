"""
Unit tests for rover.py's converse_handler — mocks Bedrock so this runs instantly,
free, and offline. Does NOT touch AWS; it is not a substitute for exercising the
deployed API Gateway route (see README note in this file's docstring below for how
to do that safely before/after a real deploy).

Run: cd eco/aws/src && python3 -m pytest test_rover_converse.py -v
"""
import json
from unittest.mock import MagicMock, patch

import rover


def _event(body=None, authorized=True):
    event = {"body": json.dumps(body) if body is not None else "{}"}
    if authorized:
        event["requestContext"] = {"authorizer": {"userId": "test-user-123"}}
    else:
        event["requestContext"] = {"authorizer": {}}
    return event


def _bedrock_response(text):
    payload = {"content": [{"type": "text", "text": text}]}
    mock_body = MagicMock()
    mock_body.read.return_value = json.dumps(payload).encode()
    return {"body": mock_body}


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


@patch.object(rover, "bedrock")
def test_returns_claude_reply_on_success(mock_bedrock):
    mock_bedrock.invoke_model.return_value = _bedrock_response("The lobby is to your left.")
    resp = rover.converse_handler(_event({"utterance": "where's the lobby"}), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"reply": "The lobby is to your left."}
    # Sanity: the bellboy system prompt (not a code-gen prompt) was actually used.
    call_kwargs = mock_bedrock.invoke_model.call_args.kwargs
    sent_body = json.loads(call_kwargs["body"])
    assert "bellboy" in sent_body["system"].lower()
    assert sent_body["messages"][0]["content"][0]["text"] == "where's the lobby"


@patch.object(rover, "bedrock")
def test_bedrock_error_returns_500(mock_bedrock):
    mock_bedrock.invoke_model.side_effect = RuntimeError("throttled")
    resp = rover.converse_handler(_event({"utterance": "hi"}), None)
    assert resp["statusCode"] == 500


@patch.object(rover, "bedrock")
def test_empty_reply_falls_back_to_default_text(mock_bedrock):
    mock_bedrock.invoke_model.return_value = _bedrock_response("")
    resp = rover.converse_handler(_event({"utterance": "hi"}), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["reply"]  # non-empty fallback, not ""
