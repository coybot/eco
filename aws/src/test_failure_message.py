"""What the operator is told when a drone command fails.

Five real flights produced five identical "There was an issue: Unknown error"
messages, because this text was built from stderr/error only and a mission
result carries neither. The cause was discarded at the last hop before the
person who needed it, and an uncommanded climb to 4-5x the requested altitude
left no evidence at all.

Run: cd eco/aws/src && python3 -m pytest test_failure_message.py -v
"""
import pytest

import conversations

from conversations import failure_message


def test_mission_failure_reports_its_reason():
    """The regression: this result shape used to render "Unknown error"."""
    result = {
        "success": False,
        "failure_reason": "Failed at phase 1",
        "summary": "Failed at phase 1",
        "phases_completed": 0,
        "total_phases": 2,
    }
    msg = failure_message(result)
    assert "Unknown error" not in msg
    assert "Failed at phase 1" in msg
    assert "0/2 phases completed" in msg


def test_falls_back_to_summary_when_there_is_no_reason():
    assert "Mission timeout" in failure_message(
        {"summary": "Mission timeout", "total_phases": 3, "phases_completed": 1})


def test_code_execution_still_reports_stderr_first():
    """The original path must not regress: stderr stays the most specific signal."""
    result = {"stderr": "NameError: takeoff is not defined", "error": "generic"}
    assert failure_message(result) == "NameError: takeoff is not defined"


def test_error_beats_failure_reason():
    assert failure_message({"error": "boom", "failure_reason": "later"}) == "boom"


def test_phase_count_is_appended_only_when_known():
    assert failure_message({"error": "boom"}) == "boom"
    assert "phases completed" not in failure_message({"error": "boom", "total_phases": 0})


def test_blank_reason_does_not_produce_an_empty_message():
    """An empty stderr is worse than saying nothing useful - it reads as success."""
    assert failure_message({"stderr": "   ", "error": ""}) == "Unknown error"


def test_missing_phases_completed_defaults_to_zero():
    assert "0/2 phases completed" in failure_message({"error": "boom", "total_phases": 2})


def test_empty_result_still_says_something():
    assert failure_message({}) == "Unknown error"


# --- response_handler branch order -------------------------------------------
# failure_message() being correct is not enough: the handler has to reach it.
# The image branches fire on image_urls alone, and a failed mission still
# carries result.photos, so a mission that failed after taking a picture was
# described to the operator as a photo with no mention of the failure.

def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(conversations, "save_message",
                        lambda *a, **k: sent.append(("save",) + a))
    monkeypatch.setattr(conversations, "publish_to_app",
                        lambda *a, **k: sent.append(("publish",) + a))
    return sent


def test_failed_mission_with_photos_still_reports_the_failure(monkeypatch):
    sent = _capture(monkeypatch)
    conversations.response_handler({
        "droneId": "d-1",
        "conversation_id": "c-1",
        "original_message": "Take off to 1 meter and land",
        "image_urls": ["s3://bucket/photo.jpg"],
        "result": {
            "success": False,
            "failure_reason": "takeoff did not report reaching altitude",
            "phases_completed": 0,
            "total_phases": 2,
        },
    }, None)
    texts = " ".join(str(x) for x in sent)
    assert "takeoff did not report reaching altitude" in texts
    assert "0/2 phases completed" in texts


def test_failed_mission_without_photos_still_reports(monkeypatch):
    sent = _capture(monkeypatch)
    conversations.response_handler({
        "droneId": "d-1", "conversation_id": "c-1",
        "result": {"success": False, "failure_reason": "GUIDED mode refused",
                   "phases_completed": 0, "total_phases": 2},
    }, None)
    assert "GUIDED mode refused" in " ".join(str(x) for x in sent)


def test_successful_result_is_not_hijacked_by_the_failure_branch(monkeypatch):
    """A success must still take its normal path rather than the failure branch."""
    sent = _capture(monkeypatch)
    monkeypatch.setattr(conversations, "get_conversation_history", lambda *a, **k: [])
    monkeypatch.setattr(conversations, "call_agent",
                        lambda *a, **k: {"action": "respond", "message": "Landed."})
    conversations.response_handler({
        "droneId": "d-1", "conversation_id": "c-1",
        "result": {"success": True, "stdout": "Landed."},
    }, None)
    joined = " ".join(str(x) for x in sent)
    assert "There was an issue" not in joined
    assert sent, "a successful result should still tell the operator something"
