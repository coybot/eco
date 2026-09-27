"""The mission planner runs Opus 5.5 at medium effort; the others stay put.

Chosen by benchmark (README, "Recommended Models"). What has to reach Bedrock
for that: the Opus model id, adaptive thinking at medium effort, and a token
budget with room for the thinking - 2048 was sized for a plan alone.

Run: cd eco/aws/src && python3 -m pytest test_mission_model.py -v
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
import conversations as c  # noqa: E402
import llm  # noqa: E402


class Bedrock:
    def __init__(self):
        self.calls = []

    def invoke_model(self, modelId, body):
        self.calls.append((modelId, json.loads(body)))
        reply = {"content": [{"type": "thinking", "thinking": "..."},
                             {"type": "text", "text": '{"action": "respond", "message": "ok"}'}]}
        return {"body": io.BytesIO(json.dumps(reply).encode())}


def test_the_mission_planner_asks_for_opus_at_medium_effort(monkeypatch):
    br = Bedrock()
    monkeypatch.setattr(llm, "_bedrock_client", br)
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    resp = c.call_mission_agent([], "Take off to 5m and land")
    model, body = br.calls[-1]
    assert model == "us.anthropic.claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive"} and body["output_config"] == {"effort": "medium"}
    assert body["max_tokens"] >= 16000
    assert resp == {"action": "respond", "message": "ok"}, "thinking blocks are not part of the answer"


def test_the_other_planners_are_unchanged(monkeypatch):
    br = Bedrock()
    monkeypatch.setattr(llm, "_bedrock_client", br)
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    c.call_agent([], "Take off to 5m and land")
    model, body = br.calls[-1]
    assert model == c.BEDROCK_MODEL_ID and "thinking" not in body and "output_config" not in body
