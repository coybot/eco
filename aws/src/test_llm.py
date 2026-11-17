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
                    max_tokens=100, model="us.anthropic.claude-sonnet-4-6")


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


# --- Bedrock Converse path (non-Claude models, e.g. Amazon Nova) ----------------

NOVA = "us.amazon.nova-pro-v1:0"


def _converse_out(content, stop="end_turn"):
    return {"output": {"message": {"role": "assistant", "content": content}},
            "stopReason": stop, "usage": {"inputTokens": 11, "outputTokens": 7}}


def test_claude_ids_still_use_invoke_model():
    for m in ("us.anthropic.claude-opus-5-5", "anthropic.claude-3-haiku-20240307-v1:0",
              "global.anthropic.claude-sonnet-4-6"):
        assert llm._is_anthropic_model(m)
    assert not llm._is_anthropic_model(NOVA)


@patch.object(llm, "_bedrock")
def test_converse_text_round_trip(mock_bedrock_fn):
    client = mock_bedrock_fn.return_value
    client.converse.return_value = _converse_out([{"text": "hello"}])
    out = llm.invoke("sys prompt", [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                     max_tokens=1024, model=NOVA, effort="medium")
    client.invoke_model.assert_not_called()
    kw = client.converse.call_args.kwargs
    assert kw["modelId"] == NOVA
    assert kw["system"] == [{"text": "sys prompt"}]
    assert kw["messages"] == [{"role": "user", "content": [{"text": "hi"}]}]
    assert kw["inferenceConfig"] == {"maxTokens": 1024}
    assert "toolConfig" not in kw
    assert out["content"] == [{"type": "text", "text": "hello"}]
    assert out["stop_reason"] == "end_turn"
    assert out["usage"] == {"input_tokens": 11, "output_tokens": 7}


@patch.object(llm, "_bedrock")
def test_converse_caps_max_tokens_below_model_limit(mock_bedrock_fn):
    client = mock_bedrock_fn.return_value
    client.converse.return_value = _converse_out([{"text": "ok"}])
    # The mission planner asks for 16000; Nova Pro rejects anything >= 10000.
    llm.invoke("s", [{"role": "user", "content": "x"}], max_tokens=16000, model=NOVA)
    assert client.converse.call_args.kwargs["inferenceConfig"]["maxTokens"] == 9999


@patch.object(llm, "_bedrock")
def test_converse_image_block_becomes_bytes(mock_bedrock_fn):
    import base64
    client = mock_bedrock_fn.return_value
    client.converse.return_value = _converse_out([{"text": "a field"}])
    raw = b"\xff\xd8\xff fake jpeg"
    llm.invoke("s", [{"role": "user", "content": [
        {"type": "text", "text": "what is this"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.b64encode(raw).decode()}},
    ]}], max_tokens=200, model=NOVA)
    content = client.converse.call_args.kwargs["messages"][0]["content"]
    assert content[0] == {"text": "what is this"}
    assert content[1] == {"image": {"format": "jpeg", "source": {"bytes": raw}}}


@patch.object(llm, "_bedrock")
def test_converse_forced_tool_maps_both_ways(mock_bedrock_fn):
    client = mock_bedrock_fn.return_value
    client.converse.return_value = _converse_out(
        [{"toolUse": {"toolUseId": "t1", "name": "decide", "input": {"action": "stop"}}}],
        stop="tool_use")
    tool = {"name": "decide", "description": "pick", "input_schema": {
        "type": "object", "properties": {"action": {"type": "string"}}}}
    out = llm.invoke("s", [{"role": "user", "content": "go"}], max_tokens=200, model=NOVA,
                     tools=[tool], tool_choice={"type": "tool", "name": "decide"})
    tc = client.converse.call_args.kwargs["toolConfig"]
    assert tc["tools"] == [{"toolSpec": {"name": "decide", "description": "pick",
                                         "inputSchema": {"json": tool["input_schema"]}}}]
    assert tc["toolChoice"] == {"tool": {"name": "decide"}}
    assert out["content"] == [{"type": "tool_use", "id": "t1", "name": "decide",
                               "input": {"action": "stop"}}]
    assert out["stop_reason"] == "tool_use"


@patch.object(llm, "_bedrock")
def test_converse_forced_tool_answered_in_prose_is_recovered(mock_bedrock_fn):
    client = mock_bedrock_fn.return_value
    client.converse.return_value = _converse_out([{"text": '```json\n{"action": "left"}\n```'}])
    out = llm.invoke("s", [{"role": "user", "content": "go"}], max_tokens=200, model=NOVA,
                     tools=[{"name": "decide", "input_schema": {"type": "object"}}],
                     tool_choice={"type": "tool", "name": "decide"})
    assert out["content"] == [{"type": "tool_use", "name": "decide", "input": {"action": "left"}}]


@patch.object(llm, "_bedrock")
def test_converse_failure_raises_llm_error(mock_bedrock_fn):
    mock_bedrock_fn.return_value.converse.side_effect = RuntimeError("Too many tokens per day")
    with pytest.raises(llm.LLMError, match="converse failed"):
        llm.invoke("s", [{"role": "user", "content": "x"}], max_tokens=10, model=NOVA)


def test_model_id_env_empty_string_falls_back_to_default(monkeypatch):
    import importlib
    import handler
    monkeypatch.setenv("BEDROCK_MODEL_ID", "")
    importlib.reload(handler)
    assert handler.BEDROCK_MODEL_ID == "us.anthropic.claude-sonnet-4-6"
    monkeypatch.setenv("BEDROCK_MODEL_ID", NOVA)
    importlib.reload(handler)
    assert handler.BEDROCK_MODEL_ID == NOVA
    monkeypatch.delenv("BEDROCK_MODEL_ID")
    importlib.reload(handler)


# --- Anthropic API path (LLM_PROVIDER=anthropic) --------------------------------

def _anthropic_msg(content, stop="end_turn"):
    msg = MagicMock()
    msg.to_dict.return_value = {"content": content, "stop_reason": stop}
    return msg


def test_anthropic_model_id_mapping():
    m = llm.anthropic_api_model_id
    assert m("us.anthropic.claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert m("global.anthropic.claude-opus-5-5") == "claude-opus-5-5"
    assert m("anthropic.claude-haiku-4-5-20251001-v1:0") == "claude-haiku-4-5-20251001"
    assert m("claude-opus-5-5") == "claude-opus-5-5"


@patch.object(llm, "_anthropic")
def test_anthropic_text_call_and_effort(mock_fn, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    client = mock_fn.return_value
    client.messages.create.return_value = _anthropic_msg(
        [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "plan"}])
    out = llm.invoke("SYS", [{"role": "user", "content": "go"}], max_tokens=16000,
                     model="us.anthropic.claude-opus-5-5", effort="medium")
    kw = client.messages.create.call_args.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["system"] == "SYS" and kw["max_tokens"] == 16000
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "medium"}
    assert out["content"][-1] == {"type": "text", "text": "plan"}


@patch.object(llm, "_anthropic")
def test_anthropic_forced_tool_kept_on_models_that_allow_it(mock_fn, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    client = mock_fn.return_value
    client.messages.create.return_value = _anthropic_msg(
        [{"type": "tool_use", "id": "t", "name": "decide", "input": {"a": 1}}], stop="tool_use")
    llm.invoke("S", [{"role": "user", "content": "x"}], max_tokens=200,
               model="us.anthropic.claude-sonnet-5", tools=[{"name": "decide", "input_schema": {"type": "object"}}],
               tool_choice={"type": "tool", "name": "decide"})
    assert client.messages.create.call_args.kwargs["tool_choice"] == {"type": "tool", "name": "decide"}


@patch.object(llm, "_anthropic")
def test_anthropic_forced_tool_becomes_auto_on_opus_5_5(mock_fn, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    client = mock_fn.return_value
    client.messages.create.return_value = _anthropic_msg([{"type": "text", "text": '{"a": 2}'}])
    out = llm.invoke("S", [{"role": "user", "content": "x"}], max_tokens=200,
                     model="us.anthropic.claude-opus-5-5",
                     tools=[{"name": "decide", "input_schema": {"type": "object"}}],
                     tool_choice={"type": "tool", "name": "decide"})
    kw = client.messages.create.call_args.kwargs
    assert kw["tool_choice"] == {"type": "auto"}
    assert "decide tool" in kw["system"]
    assert out["content"] == [{"type": "tool_use", "name": "decide", "input": {"a": 2}}]


def test_anthropic_rejects_non_claude_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(llm.LLMError, match="needs a Claude model id"):
        llm.invoke("S", [{"role": "user", "content": "x"}], max_tokens=10, model="us.amazon.nova-pro-v1:0")
