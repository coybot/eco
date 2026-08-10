"""
Unit tests for llm.py — the provider abstraction that sits between conversations.py/
handler.py/rover.py/groups.py and either Bedrock (cloud default) or an OpenAI-
compatible endpoint (GCS / local mode, LLM_PROVIDER=openai).

Two things are being protected here:
1. Byte-compat: BedrockProvider must serialize the exact same request body shape
   the inline `bedrock.invoke_model(...)` calls used to build, for every call-site
   shape in use (plain text, with images, with forced tool_choice) — so refactoring
   call sites onto llm.invoke() changes nothing about what hits Bedrock.
2. Correct mapping: OpenAICompatProvider's Anthropic<->OpenAI translation in both
   directions (messages, images, tools, tool_choice, and the JSON-fallback for
   weak/no tool-calling support).

Run: cd eco/aws/src && python3 -m pytest test_llm.py -v
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest

import llm


# --- Bedrock provider: golden body tests --------------------------------------

def _bedrock_response(content_blocks):
    payload = {"content": content_blocks}
    mock_body = MagicMock()
    mock_body.read.return_value = json.dumps(payload).encode()
    return {"body": mock_body}


@patch.object(llm, "_bedrock")
def test_bedrock_plain_text_body_matches_pre_refactor_shape(mock_bedrock_fn):
    """Mirrors conversations.call_agent's pre-refactor invoke_model body exactly:
    anthropic_version, system, messages, max_tokens - no tools key at all."""
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = _bedrock_response([{"type": "text", "text": "ok"}])
    mock_bedrock_fn.return_value = mock_client

    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    llm.invoke("SYSTEM", messages, max_tokens=1024, model="us.anthropic.claude-sonnet-4-6")

    call = mock_client.invoke_model.call_args
    assert call.kwargs["modelId"] == "us.anthropic.claude-sonnet-4-6"
    body = json.loads(call.kwargs["body"])
    assert list(body.keys()) == ["anthropic_version", "system", "messages", "max_tokens"]
    assert body == {
        "anthropic_version": "bedrock-2023-05-31",
        "system": "SYSTEM",
        "messages": messages,
        "max_tokens": 1024,
    }


@patch.object(llm, "_bedrock")
def test_bedrock_tool_use_body_matches_rover_act_shape(mock_bedrock_fn):
    """Mirrors rover.act_handler's pre-refactor body: adds tools + tool_choice
    between messages and max_tokens, in that order."""
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = _bedrock_response(
        [{"type": "tool_use", "name": "decide", "input": {"action": "stop"}}]
    )
    mock_bedrock_fn.return_value = mock_client

    tools = [{"name": "decide", "description": "d", "input_schema": {"type": "object"}}]
    tool_choice = {"type": "tool", "name": "decide"}
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    result = llm.invoke("SYSTEM", messages, max_tokens=700, model="us.anthropic.claude-sonnet-5",
                         tools=tools, tool_choice=tool_choice)

    body = json.loads(mock_client.invoke_model.call_args.kwargs["body"])
    assert list(body.keys()) == [
        "anthropic_version", "system", "messages", "tools", "tool_choice", "max_tokens",
    ]
    assert body["tools"] == tools
    assert body["tool_choice"] == tool_choice
    assert result["content"][0]["name"] == "decide"


@patch.object(llm, "_bedrock")
def test_bedrock_failure_raises_llm_error(mock_bedrock_fn):
    mock_client = MagicMock()
    mock_client.invoke_model.side_effect = RuntimeError("throttled")
    mock_bedrock_fn.return_value = mock_client

    with pytest.raises(llm.LLMError):
        llm.invoke("SYSTEM", [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                    max_tokens=100, model="m")


def test_default_provider_is_bedrock(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert llm._provider_name() == "bedrock"


# --- OpenAI-compatible provider: mapping tests ---------------------------------

class _StubOpenAIHandler(BaseHTTPRequestHandler):
    """Minimal /v1/models + /v1/chat/completions stub, modeled on the pattern in
    e2e/harness/mock_cloud.py - runs a real http.server so llm.py's urllib client
    is exercised end-to-end rather than mocked at the transport layer."""

    captured_request = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            self._reply(200, {"data": [{"id": "stub-model"}]})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _StubOpenAIHandler.captured_request = body
        if self.path == "/v1/chat/completions":
            self._reply(200, _StubOpenAIHandler.next_response)
        else:
            self._reply(404, {"error": "not found"})

    def _reply(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def stub_server(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _StubOpenAIHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    llm._discovered_model_cache.clear()

    yield _StubOpenAIHandler
    server.shutdown()


def test_openai_text_only_round_trip(stub_server):
    stub_server.next_response = {
        "choices": [{"message": {"role": "assistant", "content": "hello there"}}]
    }
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    result = llm.invoke("SYSTEM PROMPT", messages, max_tokens=128)

    sent = stub_server.captured_request
    assert sent["model"] == "stub-model"
    assert sent["messages"][0] == {"role": "system", "content": "SYSTEM PROMPT"}
    assert sent["messages"][1] == {"role": "user", "content": "hi"}
    assert "tools" not in sent
    assert result == {"content": [{"type": "text", "text": "hello there"}]}


def test_openai_image_block_becomes_data_uri(stub_server):
    stub_server.next_response = {"choices": [{"message": {"content": "I see a cat"}}]}
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}},
        ],
    }]
    llm.invoke("SYS", messages, max_tokens=64)

    sent_content = stub_server.captured_request["messages"][1]["content"]
    assert sent_content[0] == {"type": "text", "text": "what is this"}
    assert sent_content[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}


def test_openai_forced_tool_choice_maps_and_parses_tool_calls(stub_server):
    stub_server.next_response = {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{"function": {"name": "decide", "arguments": '{"action": "stop"}'}}],
            }
        }]
    }
    tools = [{"name": "decide", "description": "d", "input_schema": {"type": "object"}}]
    tool_choice = {"type": "tool", "name": "decide"}
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    result = llm.invoke("SYS", messages, max_tokens=64, tools=tools, tool_choice=tool_choice)

    sent = stub_server.captured_request
    assert sent["tools"] == [{
        "type": "function",
        "function": {"name": "decide", "description": "d", "parameters": {"type": "object"}},
    }]
    assert sent["tool_choice"] == {"type": "function", "function": {"name": "decide"}}
    assert result == {"content": [{"type": "tool_use", "name": "decide", "input": {"action": "stop"}}]}


def test_openai_weak_tool_support_falls_back_to_json_in_text(stub_server):
    """Some local backends ignore a forced tool_choice and just describe the call in
    prose/markdown - llm.py must still synthesize a usable tool_use block."""
    stub_server.next_response = {
        "choices": [{"message": {"content": '```json\n{"action": "stop"}\n```'}}]
    }
    tools = [{"name": "decide", "description": "d", "input_schema": {"type": "object"}}]
    tool_choice = {"type": "tool", "name": "decide"}
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    result = llm.invoke("SYS", messages, max_tokens=64, tools=tools, tool_choice=tool_choice)

    assert result == {"content": [{"type": "tool_use", "name": "decide", "input": {"action": "stop"}}]}


def test_openai_llm_model_env_overrides_autodiscovery(stub_server, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "my-pinned-model")
    stub_server.next_response = {"choices": [{"message": {"content": "ok"}}]}
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    llm.invoke("SYS", messages, max_tokens=16)

    assert stub_server.captured_request["model"] == "my-pinned-model"


def test_openai_no_model_available_raises_llm_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1")  # nothing listening
    monkeypatch.delenv("LLM_MODEL", raising=False)
    llm._discovered_model_cache.clear()

    with pytest.raises(llm.LLMError):
        llm.invoke("SYS", [{"role": "user", "content": [{"type": "text", "text": "hi"}]}], max_tokens=16)
