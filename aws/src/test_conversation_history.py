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
