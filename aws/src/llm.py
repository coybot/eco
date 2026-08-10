"""
LLM provider abstraction, shared by conversations.py, handler.py, rover.py, and
groups.py.

Cloud (Lambda) default: Bedrock, calling Claude via the Anthropic messages wire
format. Behavior is byte-identical to the inline `bedrock.invoke_model(...)`
calls this replaces — same body shape, same key order, same client.

GCS (local ground control station) mode: set LLM_PROVIDER=openai to route
through an OpenAI-compatible /v1/chat/completions endpoint (vLLM, Ollama, ...)
instead. No AWS credentials needed. Stdlib-only (urllib, like
drone/sim/ishmael/nlp.py's vLLM client) so the Lambda bundle gains no new
dependency when this branch is unused.

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


def invoke(system, messages, *, max_tokens, tools=None, tool_choice=None, model=None):
    """Invoke the configured LLM provider.

    `model` is the caller's BEDROCK_MODEL_ID (or BEDROCK_CODE_MODEL_ID) — used
    verbatim by the bedrock provider, ignored by the openai provider (which
    resolves its own model from LLM_MODEL / endpoint auto-discovery, since a
    local server's model name has no relationship to a Bedrock model id).
    """
    if _provider_name() == "openai":
        return _invoke_openai(system, messages, max_tokens=max_tokens,
                               tools=tools, tool_choice=tool_choice)
    return _invoke_bedrock(system, messages, max_tokens=max_tokens,
                            tools=tools, tool_choice=tool_choice, model=model)


# --- Bedrock provider ---------------------------------------------------------

_bedrock_client = None


def _bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3
        region = (os.environ.get("AWS_REGION")
                  or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2")
        _bedrock_client = boto3.client("bedrock-runtime", region_name=region)
    return _bedrock_client


def _invoke_bedrock(system, messages, *, max_tokens, tools, tool_choice, model):
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
    try:
        response = _bedrock().invoke_model(modelId=model, body=json.dumps(body))
        return json.loads(response["body"].read())
    except Exception as e:
        raise LLMError(f"bedrock invoke_model failed: {e}") from e


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
