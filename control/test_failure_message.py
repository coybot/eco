"""What the operator is told when a drone command fails.

Five real flights produced five identical "There was an issue: Unknown error"
messages, because this text was built from stderr/error only and a mission
result carries neither. The cause was discarded at the last hop before the
person who needed it, and an uncommanded climb to 4-5x the requested altitude
left no evidence at all.

Run: cd control && python3 -m pytest test_failure_message.py -v
"""
import pytest

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
