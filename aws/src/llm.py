"""
LLM provider abstraction, shared by conversations.py, handler.py, rover.py, and
groups.py.

Cloud (Lambda) default: Bedrock, calling Claude via the Anthropic messages wire
format. Behavior is byte-identical to the inline `bedrock.invoke_model(...)`
calls this replaces — same body shape, same key order, same client.

Claude API mode: set LLM_PROVIDER=anthropic (and ANTHROPIC_API_KEY) to call
Claude through the official `anthropic` SDK instead of Bedrock; Bedrock-style
model ids are mapped to Claude API ids.

GCS (local ground control station) mode: set LLM_PROVIDER=openai to route
through an OpenAI-compatible /v1/chat/completions endpoint (vLLM, Ollama, ...)
instead. No AWS credentials needed. Stdlib-only (urllib) so the Lambda
bundle gains no new dependency when this branch is unused.

Call sites pass Anthropic-messages-shaped input (system str, messages list,
optional tools/tool_choice) and always get back an Anthropic-messages-shaped
response dict, regardless of provider:
    {"content": [{"type": "text", "text": ...} | {"type": "tool_use",
                  "name": ..., "input": {...}}, ...]}
"""
import json
import os
import re
import urllib.error
import urllib.request

ANTHROPIC_VERSION = "bedrock-2023-05-31"


class LLMError(Exception):
    """Raised when the configured provider fails to produce a response.

    Call sites already wrap their single bedrock.invoke_model() call in a
    try/except Exception — this is a plain Exception subclass so that pattern
    keeps working unchanged after the refactor to llm.invoke().
    """


def _provider_name() -> str:
    return os.environ.get("LLM_PROVIDER", "bedrock").strip().lower()


def invoke(system, messages, *, max_tokens, tools=None, tool_choice=None, model=None, effort=None):
    """Invoke the configured LLM provider.

    `model` is the caller's BEDROCK_MODEL_ID (or BEDROCK_CODE_MODEL_ID) — used
    verbatim by the bedrock provider, ignored by the openai provider (which
    resolves its own model from LLM_MODEL / endpoint auto-discovery, since a
    local server's model name has no relationship to a Bedrock model id).
    """
    if _provider_name() == "openai":
        return _invoke_openai(system, messages, max_tokens=max_tokens,
                               tools=tools, tool_choice=tool_choice)
    if _provider_name() == "anthropic":
        return _invoke_anthropic(system, messages, max_tokens=max_tokens,
                                 tools=tools, tool_choice=tool_choice, model=model, effort=effort)
    return _invoke_bedrock(system, messages, max_tokens=max_tokens,
                            tools=tools, tool_choice=tool_choice, model=model, effort=effort)


# --- Bedrock provider ---------------------------------------------------------

_bedrock_client = None


def _bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3
        region = (os.environ.get("AWS_REGION")
                  or os.environ.get("AWS_DEFAULT_REGION") or "us-east-2")
        _bedrock_client = boto3.client("bedrock-runtime", region_name=region)
    return _bedrock_client


def _is_anthropic_model(model) -> bool:
    """Claude ids ("anthropic.claude-...", "us.anthropic...", "global.anthropic...",
    or an inference-profile ARN naming one) speak the Anthropic messages wire
    format through invoke_model. Everything else (Amazon Nova, ...) goes through
    the Converse API."""
    return "anthropic" in str(model or "")


# Output-token ceilings Bedrock enforces per model family (it rejects a larger
# maxTokens outright instead of truncating). Matched by substring of the id.
_CONVERSE_MAX_OUTPUT = {
    "nova-premier": 32000,
    "nova-2": 65000,
    "nova-pro": 10000,
    "nova-lite": 10000,
    "nova-micro": 10000,
}


def _converse_max_tokens(model: str, requested: int) -> int:
    for key, ceiling in _CONVERSE_MAX_OUTPUT.items():
        if key in model:
            # Bedrock wants strictly below the limit ("lower than 10000").
            return min(requested, ceiling - 1)
    return requested


def _invoke_bedrock(system, messages, *, max_tokens, tools, tool_choice, model, effort=None):
    if not _is_anthropic_model(model):
        return _invoke_bedrock_converse(system, messages, max_tokens=max_tokens,
                                        tools=tools, tool_choice=tool_choice, model=model)
    body = {
        "anthropic_version": ANTHROPIC_VERSION,
        "system": system,
        "messages": messages,
    }
    if tools is not None:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    body["max_tokens"] = max_tokens
    if effort:
        # Adaptive thinking at the given effort ("low" | "medium" | "high").
        # Thinking comes back as "thinking" blocks, which every caller already
        # skips by reading only "text" blocks.
        body["thinking"] = {"type": "adaptive"}
        body["output_config"] = {"effort": effort}
    try:
        response = _bedrock().invoke_model(modelId=model, body=json.dumps(body))
        return json.loads(response["body"].read())
    except Exception as e:
        raise LLMError(f"bedrock invoke_model failed: {e}") from e


# --- Anthropic API (LLM_PROVIDER=anthropic) ------------------------------------
#
# Calls Claude directly through the official `anthropic` SDK, with the key in
# ANTHROPIC_API_KEY. Call sites keep passing their Bedrock-style model ids; they
# are mapped to Claude API ids here, so no other config changes.

_anthropic_client = None

# Models that reject a forced tool_choice ("any" / {"type": "tool"}) with a 400.
_NO_FORCED_TOOL_CHOICE = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1",
                          "claude-fable-5-2", "claude-mythos-5-1", "claude-mythos-5-2")


def _anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    return _anthropic_client


def anthropic_api_model_id(model) -> str:
    """"us.anthropic.claude-sonnet-4-6" -> "claude-sonnet-4-6";
    "anthropic.claude-haiku-4-5-20251001-v1:0" -> "claude-haiku-4-5-20251001".
    An id that is already a Claude API id is returned unchanged."""
    m = str(model or "")
    if "anthropic." in m:
        m = m.split("anthropic.", 1)[1]
    return re.sub(r"(-v\d+)?:\d+$", "", m)


def _invoke_anthropic(system, messages, *, max_tokens, tools, tool_choice, model, effort=None):
    api_model = anthropic_api_model_id(model)
    if not api_model.startswith("claude"):
        raise LLMError(f"LLM_PROVIDER=anthropic needs a Claude model id, got {model!r}")
    kwargs = {"model": api_model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        if (tool_choice.get("type") in ("tool", "any")
                and api_model.startswith(_NO_FORCED_TOOL_CHOICE)):
            # These models 400 on a forced choice. Ask for the tool in auto
            # mode; the JSON-in-text fallback below still recovers a prose answer.
            name = tool_choice.get("name")
            kwargs["tool_choice"] = {"type": "auto"}
            if name:
                note = f"Respond by calling the {name} tool."
                kwargs["system"] = f"{system}\n\n{note}" if isinstance(system, str) and system else note
        else:
            kwargs["tool_choice"] = tool_choice
    if effort:
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": effort}
    try:
        out = _anthropic().messages.create(**kwargs).to_dict()
    except Exception as e:
        raise LLMError(f"anthropic messages.create failed: {e}") from e

    content = out.get("content") or []
    forced = tool_choice and tool_choice.get("type") == "tool"
    if forced and not any(b.get("type") == "tool_use" for b in content):
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        parsed = _try_parse_tool_json(text) if text else None
        if parsed is not None:
            out["content"] = [{"type": "tool_use", "name": tool_choice.get("name"), "input": parsed}]
    return out


# --- Bedrock Converse (non-Claude models, e.g. Amazon Nova) ---------------------

_CONVERSE_IMAGE_FORMATS = {"image/jpeg": "jpeg", "image/jpg": "jpeg", "image/png": "png",
                           "image/gif": "gif", "image/webp": "webp"}


def _anthropic_content_to_converse(content):
    """Anthropic content (str or block list) -> Converse content blocks.

    Handles the block types call sites send: text, base64 image, and (for
    completeness) tool_use / tool_result. Unknown blocks are skipped rather
    than mistranslated, as in the OpenAI mapping."""
    import base64

    if isinstance(content, str):
        return [{"text": content}]
    blocks = []
    for block in content or []:
        btype = block.get("type")
        if btype == "text":
            if block.get("text"):
                blocks.append({"text": block["text"]})
        elif btype == "image":
            source = block.get("source", {})
            if source.get("type") != "base64":
                continue
            fmt = _CONVERSE_IMAGE_FORMATS.get(source.get("media_type", "image/jpeg"), "jpeg")
            blocks.append({"image": {"format": fmt,
                                     "source": {"bytes": base64.b64decode(source.get("data", ""))}}})
        elif btype == "tool_use":
            blocks.append({"toolUse": {"toolUseId": block.get("id") or block.get("name", "tool"),
                                       "name": block.get("name"),
                                       "input": block.get("input") or {}}})
        elif btype == "tool_result":
            inner = block.get("content")
            inner_blocks = ([{"text": inner}] if isinstance(inner, str)
                            else [b for b in _anthropic_content_to_converse(inner) if "text" in b])
            result = {"toolUseId": block.get("tool_use_id"), "content": inner_blocks or [{"text": ""}]}
            if block.get("is_error"):
                result["status"] = "error"
            blocks.append({"toolResult": result})
    return blocks


def _anthropic_tools_to_converse(tools, tool_choice):
    if not tools:
        return None
    config = {"tools": [
        {"toolSpec": {"name": t["name"],
                      "description": t.get("description") or t["name"],
                      "inputSchema": {"json": t.get("input_schema")
                                      or {"type": "object", "properties": {}}}}}
        for t in tools
    ]}
    if tool_choice:
        ttype = tool_choice.get("type")
        if ttype == "tool" and tool_choice.get("name"):
            config["toolChoice"] = {"tool": {"name": tool_choice["name"]}}
        elif ttype == "any":
            config["toolChoice"] = {"any": {}}
        else:
            config["toolChoice"] = {"auto": {}}
    return config


_CONVERSE_STOP_REASONS = {"end_turn": "end_turn", "tool_use": "tool_use",
                          "max_tokens": "max_tokens", "stop_sequence": "stop_sequence"}


def _invoke_bedrock_converse(system, messages, *, max_tokens, tools, tool_choice, model):
    """Call a non-Claude Bedrock model through Converse and return the same
    Anthropic-messages-shaped dict the Claude path returns. `effort` (adaptive
    thinking) is a Claude feature and is not forwarded."""
    if isinstance(system, list):  # Anthropic system blocks
        system_text = "\n\n".join(b.get("text", "") for b in system if isinstance(b, dict))
    else:
        system_text = system or ""
    kwargs = {
        "modelId": model,
        "messages": [{"role": m.get("role", "user"),
                      "content": _anthropic_content_to_converse(m.get("content"))}
                     for m in messages],
        "inferenceConfig": {"maxTokens": _converse_max_tokens(str(model), max_tokens)},
    }
    if system_text:
        kwargs["system"] = [{"text": system_text}]
    tool_config = _anthropic_tools_to_converse(tools, tool_choice)
    if tool_config:
        kwargs["toolConfig"] = tool_config
    try:
        out = _bedrock().converse(**kwargs)
    except Exception as e:
        raise LLMError(f"bedrock converse failed: {e}") from e

    content_blocks = []
    for block in (out.get("output", {}).get("message", {}).get("content") or []):
        if "text" in block:
            content_blocks.append({"type": "text", "text": block["text"]})
        elif "toolUse" in block:
            tu = block["toolUse"]
            content_blocks.append({"type": "tool_use", "id": tu.get("toolUseId"),
                                   "name": tu.get("name"), "input": tu.get("input") or {}})
        # reasoningContent and other blocks are skipped: callers read text/tool_use only.

    # Same fallback as the OpenAI path: a forced tool came back as prose JSON.
    tool_uses = [b for b in content_blocks if b["type"] == "tool_use"]
    if tool_choice and tool_choice.get("type") == "tool" and not tool_uses:
        text = "".join(b["text"] for b in content_blocks if b["type"] == "text")
        parsed = _try_parse_tool_json(text) if text else None
        if parsed is not None:
            content_blocks = [{"type": "tool_use", "name": tool_choice.get("name"), "input": parsed}]

    usage = out.get("usage") or {}
    return {
        "content": content_blocks,
        "stop_reason": _CONVERSE_STOP_REASONS.get(out.get("stopReason"), out.get("stopReason")),
        "usage": {"input_tokens": usage.get("inputTokens"),
                  "output_tokens": usage.get("outputTokens")},
    }


# --- OpenAI-compatible provider (vLLM / Ollama / etc.) ------------------------

def _openai_base_url() -> str:
    return os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")


def _openai_timeout() -> float:
    return float(os.environ.get("LLM_TIMEOUT_S", "60"))


_discovered_model_cache = {}


def _openai_model(base_url: str):
    env_model = os.environ.get("LLM_MODEL")
    if env_model:
        return env_model
    if base_url in _discovered_model_cache:
        return _discovered_model_cache[base_url]
    model = None
    try:
        req = urllib.request.Request(base_url + "/models")
        api_key = os.environ.get("LLM_API_KEY")
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=_openai_timeout()) as r:
            data = json.load(r)
        models = data.get("data") or []
        if models:
            model = models[0]["id"]
    except Exception:
        model = None
    _discovered_model_cache[base_url] = model
    return model


def _anthropic_messages_to_openai(system, messages):
    openai_messages = [{"role": "system", "content": system}]
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")
        if isinstance(content, str):
            openai_messages.append({"role": role, "content": content})
            continue
        parts = []
        for block in content or []:
            btype = block.get("type")
            if btype == "text":
                parts.append({"type": "text", "text": block.get("text", "")})
            elif btype == "image":
                source = block.get("source", {})
                media_type = source.get("media_type", "image/jpeg")
                data = source.get("data", "")
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                })
            # tool_use/tool_result blocks aren't produced as *input* by any
            # current call site (all calls are single-turn) - anything else
            # is skipped rather than silently mistranslated.
        if len(parts) == 1 and parts[0]["type"] == "text":
            openai_messages.append({"role": role, "content": parts[0]["text"]})
        else:
            openai_messages.append({"role": role, "content": parts})
    return openai_messages


def _anthropic_tools_to_openai(tools):
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


def _anthropic_tool_choice_to_openai(tool_choice):
    if not tool_choice:
        return None
    ttype = tool_choice.get("type")
    if ttype == "tool" and tool_choice.get("name"):
        return {"type": "function", "function": {"name": tool_choice["name"]}}
    if ttype == "any":
        return "required"
    return "auto"


def _try_parse_tool_json(text: str):
    """Best-effort JSON-object extraction from free text, for backends that
    ignore a forced tool_choice and just describe the call in prose/markdown."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def _invoke_openai(system, messages, *, max_tokens, tools, tool_choice):
    base_url = _openai_base_url()
    model = _openai_model(base_url)
    if not model:
        raise LLMError(
            f"no model available at {base_url}/models (set LLM_MODEL to override "
            "auto-discovery, or confirm the local server is running)"
        )

    body = {
        "model": model,
        "messages": _anthropic_messages_to_openai(system, messages),
        "max_tokens": max_tokens,
    }
    openai_tools = _anthropic_tools_to_openai(tools)
    if openai_tools:
        body["tools"] = openai_tools
    openai_tool_choice = _anthropic_tool_choice_to_openai(tool_choice)
    if openai_tool_choice:
        body["tool_choice"] = openai_tool_choice

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        base_url + "/chat/completions", data=data,
        headers={"Content-Type": "application/json"},
    )
    api_key = os.environ.get("LLM_API_KEY")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=_openai_timeout()) as r:
            out = json.load(r)
    except urllib.error.URLError as e:
        raise LLMError(f"request to {base_url}/chat/completions failed: {e}") from e
    except Exception as e:
        raise LLMError(str(e)) from e

    try:
        choice = out["choices"][0]["message"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"unexpected response shape from {base_url}: {out}") from e

    content_blocks = []
    text = choice.get("content")
    if text:
        content_blocks.append({"type": "text", "text": text})

    tool_calls = choice.get("tool_calls") or []
    for call in tool_calls:
        fn = call.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        content_blocks.append({"type": "tool_use", "name": fn.get("name"), "input": args})

    # Fallback for weak/absent native tool-calling: a forced tool_choice came
    # back with no tool_calls at all - try to parse the text as the tool's
    # JSON input directly (keeps /rover/act usable on e.g. Ollama).
    if tool_choice and not tool_calls and text:
        parsed = _try_parse_tool_json(text)
        if parsed is not None:
            tool_name = tool_choice.get("name") if isinstance(tool_choice, dict) else None
            content_blocks = [{"type": "tool_use", "name": tool_name, "input": parsed}]

    return {"content": content_blocks}
