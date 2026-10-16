"""The planner must see the latest messages of a long conversation.

Measured in the real chat log: a ~90-message conversation, then "Take off 3
meters and follow me" -> "What should the drone do if it loses sight of you?"
-> "Keep searching for 30s then return home" came back as "Could you clarify
what you'd like me to search for?". The history query took the FIRST 20.

Run: cd eco/aws/src && python3 -m pytest test_conversation_history.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
import conversations as c  # noqa: E402


class Table:
    """Answers query() the way DynamoDB does: sorted by SK, Limit applied
    after ordering in the requested direction."""
    def __init__(self, items):
        self.items = sorted(items, key=lambda i: i["SK"])

    def query(self, ScanIndexForward=True, Limit=None, **_):
        items = self.items if ScanIndexForward else list(reversed(self.items))
        return {"Items": items[:Limit]}


def test_a_long_conversation_ends_with_its_latest_exchange(monkeypatch):
    noise = []
    for i in range(45):
        noise += [{"SK": f"MSG#2026-09-24T17:{i:02d}:00#a", "sender": "user", "content": "Take off to 1 meter and land"},
                  {"SK": f"MSG#2026-09-24T17:{i:02d}:30#b", "sender": "drone", "content": "Done!"}]
    latest = [{"SK": "MSG#2026-09-24T18:52:00#c", "sender": "user", "content": "Take off 3 meters and follow me"},
              {"SK": "MSG#2026-09-24T18:52:10#d", "sender": "drone",
               "content": "What should the drone do if it loses sight of you?"},
              {"SK": "MSG#2026-09-24T18:53:00#e", "sender": "user", "content": "Keep searching for 30s then return home"}]
    monkeypatch.setattr(c, "dynamodb", type("D", (), {"Table": lambda self, n: Table(noise + latest)})())
    history = c.get_conversation_history("d", "conv")
    assert len(history) == 20
    assert [m["content"] for m in history[-3:]] == [m["content"] for m in latest]
    assert [m["role"] for m in history[-3:]] == ["user", "assistant", "user"]


# --- the history the apps load (get_handler) -------------------------------- #

def _get(monkeypatch, items):
    monkeypatch.setattr(c, "get_user_id", lambda e: "u1")
    monkeypatch.setattr(c, "verify_ownership", lambda u, d: True)
    monkeypatch.setattr(c, "generate_presigned_url", lambda u: u + "?signed")
    monkeypatch.setattr(c, "dynamodb", type("D", (), {"Table": lambda self, n: Table(items)})())
    import json
    resp = c.get_handler({"pathParameters": {"droneId": "d", "conversationId": "conv"}}, None)
    return json.loads(resp["body"])["messages"]


def _msg(sk, sender, ctype, text, **extra):
    return {"SK": f"MSG#2026-10-15T21:{sk}#x", "messageId": sk, "sender": sender,
            "contentType": ctype, "content": text, "timestamp": sk, **extra}


PLAN = "Surveying the 10 x 5 m area ahead and to the right: 50 photos, one per 1 m, each tagged with GPS and time"


def test_reopening_a_finished_survey_still_shows_the_plan(monkeypatch):
    """Seen 2026-10-15: reopened, the chat went straight from the request to the album -
    the planner's "50 photos, one per 1 m" confirmation had been dropped from history."""
    out = _get(monkeypatch, [
        _msg("44:19", "user", "text", "scan the rectangle..."),
        _msg("44:22", "drone", "loading", PLAN, sent="mission: survey_rect(...)"),
        _msg("47:22", "drone", "media", "Scan complete! 50 photos",
             media=[{"url": "https://b.s3.amazonaws.com/p1.jpg", "kind": "photo"}]),
    ])
    assert [(m["content"]["type"], m["content"]["text"][:8]) for m in out] == [
        ("text", "scan the"), ("text", "Surveyin"), ("media", "Scan com")]
    assert out[2]["content"]["media"][0]["url"].endswith("?signed")


def test_a_plan_still_in_progress_is_left_to_the_live_view(monkeypatch):
    """Not answered yet: an Android client draws a stored "loading" as a spinner."""
    out = _get(monkeypatch, [_msg("44:19", "user", "text", "scan..."),
                             _msg("44:22", "drone", "loading", PLAN)])
    assert [m["content"]["type"] for m in out] == ["text"]


def test_generic_placeholders_stay_out_of_history(monkeypatch):
    out = _get(monkeypatch, [_msg("01:00", "user", "text", "take a photo"),
                             _msg("01:01", "drone", "loading", "Working on it..."),
                             _msg("01:09", "drone", "text", "Done!")])
    assert [m["content"]["text"] for m in out] == ["take a photo", "Done!"]
