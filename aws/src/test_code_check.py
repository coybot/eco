"""Code the planner writes must only call functions the aircraft has
(code_check), and the cloud must not send code that cannot run (checked_code)."""
import importlib.util
from pathlib import Path

import code_check
import conversations as C


def test_invented_call_is_caught():
    # The 2026-11-17 e2e failure: Sonnet wrote go_up(2); the drone raised NameError.
    assert code_check.unknown_calls("go_up(2)\ncapture_photo()\nland()") == ["go_up"]
    assert code_check.unknown_calls("take_photo()") == ["take_photo"]


def test_real_sdk_builtins_and_own_names_pass():
    code = (
        "import math\n"
        "def hop(h):\n"
        "    return takeoff(h)\n"
        "ok = hop(2)\n"
        "if not ok:\n"
        "    print(battery_status())\n"
        "for i in range(int(math.ceil(1.5))):\n"
        "    sleep(1)\n"
        "url = capture_photo(upload=True)\n"
        "fn = lambda: land()\n"
        "fn()\n"
    )
    assert code_check.unknown_calls(code) == []


def test_rover_calls_pass():
    assert code_check.unknown_calls("arm()\ndrive(0.5, 2)\nturn(90)\nstop()") == []


def test_syntax_error_is_reported():
    (bad,) = code_check.unknown_calls("takeoff(2\nland()")
    assert bad.startswith("<syntax error")


def test_sdk_name_list_matches_the_drone_sources():
    gen = Path(__file__).resolve().parents[1] / "scripts" / "gen_drone_sdk_names.py"
    spec = importlib.util.spec_from_file_location("gen_drone_sdk_names", gen)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    committed = (Path(__file__).parent / "drone_sdk_names.py").read_text()
    assert mod.render() == committed, "run: python3 aws/scripts/gen_drone_sdk_names.py"


def test_checked_code_asks_again_once_and_uses_the_fix(monkeypatch):
    asked = []
    monkeypatch.setattr(C, "publish_log", lambda *a, **k: None)
    monkeypatch.setattr(C, "generate_code", lambda instr, *a, **k: asked.append(instr) or "takeoff(2)\nland()")
    code, err = C.checked_code("go_up(2)", "go up 2 m", "conv", "drone-x")
    assert err is None and code == "takeoff(2)\nland()"
    assert len(asked) == 1 and "go_up" in asked[0] and asked[0].startswith("go up 2 m")


def test_checked_code_refuses_to_send_code_that_still_cannot_run(monkeypatch):
    monkeypatch.setattr(C, "publish_log", lambda *a, **k: None)
    monkeypatch.setattr(C, "generate_code", lambda *a, **k: "go_up(2)")
    code, err = C.checked_code("go_up(2)", "go up 2 m", "conv", "drone-x")
    assert code is None and "go_up" in err and "Nothing was sent" in err


def test_checked_code_leaves_good_code_alone(monkeypatch):
    monkeypatch.setattr(C, "generate_code", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no retry")))
    assert C.checked_code("takeoff(2)", "x", "conv", "drone-x") == ("takeoff(2)", None)
