"""generate_code must hand the drone the whole program the model wrote.

Benchmarking the planner, a 2 m survey reached the drone as `land()` alone:
the preamble stripper cut at the first line starting with a known SDK call,
and `urls = survey_rect(...)` is not one. Code opening with `if` or `try`
lost its head the same way.

Run: cd eco/aws/src && python3 -m pytest test_generated_code_cleanup.py -v
"""
import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
import conversations as c  # noqa: E402

SURVEY = ("urls = survey_rect(forward_m=10, right_m=6, spacing_m=2, altitude_m=6)\n"
          "print(urls)\nreturn_home()\nland()")
GUARDED = ("arm()\nif not takeoff(5):\n    print('takeoff failed')\nelse:\n"
           "    print(survey_rect(forward_m=5, right_m=5))\n    return_home()\n    land()")


def _generate(monkeypatch, text):
    monkeypatch.setattr(c.llm, "invoke",
                        lambda *a, **k: {"content": [{"type": "text", "text": text}]})
    return c.generate_code("photograph the area", "conv-1", vehicle_type="quadcopter")


@pytest.mark.parametrize("program", [SURVEY, GUARDED, "try:\n    arm()\nexcept Exception:\n    land()"])
def test_the_whole_program_survives(monkeypatch, program):
    assert _generate(monkeypatch, program) == program


def test_prose_before_the_code_is_dropped(monkeypatch):
    out = _generate(monkeypatch, "Here is the code for the survey:\n\n" + SURVEY)
    assert out == SURVEY


def test_a_fenced_block_is_unwrapped(monkeypatch):
    out = _generate(monkeypatch, "Sure.\n```python\n" + GUARDED + "\n```\nThis flies it.")
    assert out == GUARDED
    ast.parse(out)
