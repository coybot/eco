"""phrover (PhroverOperator / WAVE ROVER) e2e check.

Three independent, unrelated checks — see scenarios.yaml's comment for why:

  dialog        -- POST /rover/converse, the open-ended small-talk fallback
                   (aws/src/rover.py). fast tier hits harness/mock_rover_converse.py;
                   live tier hits the real deployed endpoint.
  navigate      -- on-device RoverNav planning/driving. Identical in both tiers: it's pure
                   Swift, offline, deterministic — RoverNavTests in the public coybot-sdk
                   repo (sibling of this repo; see ../../../sdk). NavIntegrationTests
                   .testDriveAroundCornerToGoalCollisionFree already asserts "plan reaches
                   the goal without violating the costmap" for a doorway-shaped gap,
                   matching the scenario's mission text. PhroverKitTests runs alongside it
                   in the same xcodebuild invocation — it exercises MissionAgent's decision
                   loop (grounding, look-around, ask/answer, best-effort fallback) against
                   scripted fakes, and the unprojection math against synthetic geometry.
  follow        -- the same POST /rover/act route, asked to follow. Checks the one action
                   that is a mode rather than a trip comes back well-formed (action=follow
                   with a description or an image point to follow); without it nothing can
                   reach FollowController from a spoken command.
  mission_agent -- POST /rover/act, the vision + tool-use mission brain behind
                   MissionAgent's CloudBrain (aws/src/rover.py's act_handler). fast tier
                   hits harness/mock_rover_act.py; live tier hits the real deployed
                   endpoint. Only checks the decision shape is well-formed and reachable —
                   real visual grounding needs a live camera frame, which is a hardware
                   concern, not a fast-CI one.

RoverNav/PhroverKit now live in the same SwiftPM package as PhroverCloud, which imports
ARKit (unavailable on macOS) — so plain `swift test` there tries to build everything and
fails. We run the Swift tests through `xcodebuild test` against an iOS Simulator instead,
which is still hardware-free (no phone, no chassis, no AWS creds).

App-level phrover coverage (actually driving PhroverOperator's UI against a mocked WAVE
ROVER base) is Layer B — see harness/mock_esp32.py + run_phone.sh, not this module.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from .fixtures import load_scenarios
from .mock_rover_act import MockRoverAct
from .mock_rover_converse import MockRoverConverse

_SDK_DIR = Path(__file__).resolve().parents[3] / "sdk"
# Must match DECIDE_TOOL's action enum in aws/src/rover.py, which is the source of truth
# for what this route can return — a valid action missing here fails the check on a
# perfectly good answer (claimRoom was missing until a team mission would have hit it).
_VALID_ACTIONS = {"navigate", "follow", "explore", "lookAround", "ask", "say", "stop",
                  "done", "claimRoom"}


def _first_available_simulator() -> str | None:
    # The first xcrun on a cold CI runner has to resolve the toolchain and can take
    # well over 30s; a timeout here used to escape as an unhandled TimeoutExpired and
    # kill the whole run rather than reporting a failed check.
    try:
        proc = subprocess.run(["xcrun", "simctl", "list", "devices", "available", "-j"],
                              capture_output=True, text=True, timeout=180)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    devices = json.loads(proc.stdout).get("devices", {})
    for runtime, entries in devices.items():
        if "iOS" not in runtime:
            continue
        for entry in entries:
            if entry.get("isAvailable") and "iPhone" in entry.get("name", ""):
                return entry["udid"]
    return None


def _run_swift_tests() -> tuple[bool, str]:
    if not _SDK_DIR.exists():
        return False, f"coybot-sdk package not found at {_SDK_DIR} (expected as a sibling of this repo)"
    udid = _first_available_simulator()
    if not udid:
        return False, "no available iOS Simulator found (xcrun simctl list devices available)"
    # A cold macos-latest runner has to resolve packages and build the whole SDK before
    # the first test runs. Measured on main: 198-311 s for this step across green runs,
    # so the old 300 s ceiling sat inside normal variance and a slow runner tripped it.
    # And the TimeoutExpired escaped uncaught, killing the run with a traceback instead
    # of reporting a failed check — the same shape _first_available_simulator had.
    timeout_s = int(os.environ.get("E2E_SWIFT_TIMEOUT_S", "900"))
    try:
        proc = subprocess.run(
            ["xcodebuild", "test", "-scheme", "coybot-sdk-Package",
             "-destination", f"id={udid}",
             "-only-testing:RoverNavTests", "-only-testing:PhroverKitTests"],
            cwd=_SDK_DIR, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, f"xcodebuild test did not finish within {timeout_s}s (E2E_SWIFT_TIMEOUT_S)"
    ok = proc.returncode == 0
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
    return ok, tail


def _dialog_fast(utterance: str) -> dict[str, Any]:
    with MockRoverConverse() as mock:
        body = json.dumps({"utterance": utterance}).encode()
        req = urllib.request.Request(mock.base_url + "/rover/converse", data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)


def _dialog_live(utterance: str) -> dict[str, Any]:
    import os
    api_base = os.environ.get("ISHMAEL_API_BASE", "").rstrip("/")
    api_token = os.environ.get("ISHMAEL_API_TOKEN", "")
    if not api_base:
        raise RuntimeError("ISHMAEL_API_BASE not set; cannot reach the deployed /rover/converse")
    body = json.dumps({"utterance": utterance}).encode()
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = api_token
    req = urllib.request.Request(api_base + "/rover/converse", data=body,
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _mission_agent_fast(utterance: str) -> dict[str, Any]:
    with MockRoverAct() as mock:
        body = json.dumps({"utterance": utterance}).encode()
        req = urllib.request.Request(mock.base_url + "/rover/act", data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)


def _mission_agent_live(utterance: str) -> dict[str, Any]:
    import os
    api_base = os.environ.get("ISHMAEL_API_BASE", "").rstrip("/")
    api_token = os.environ.get("ISHMAEL_API_TOKEN", "")
    if not api_base:
        raise RuntimeError("ISHMAEL_API_BASE not set; cannot reach the deployed /rover/act")
    body = json.dumps({"utterance": utterance}).encode()
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = api_token
    req = urllib.request.Request(api_base + "/rover/act", data=body,
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _follow_target_ok(response: dict[str, Any]) -> bool:
    """A follow decision has to carry something followable. `visualQuery` needs the
    description in `text`; an `imagePoint` needs both coordinates. A bare action=follow
    would start a lock with nothing to lock onto."""
    if response.get("action") != "follow":
        return False
    kind = response.get("target_kind")
    if kind == "visualQuery":
        return bool(response.get("text"))
    if kind in ("imagePoint", "worldPoint"):
        return response.get("x") is not None and response.get("y") is not None
    return bool(response.get("text"))


def _run(tier: str) -> dict[str, Any]:
    scenario = load_scenarios()["phrover"]
    dialog_spec = scenario["dialog"]
    nav_spec = scenario["navigate"]
    mission_agent_spec = scenario["mission_agent"]
    follow_spec = scenario["follow"]

    dialog_resp = (_dialog_fast if tier == "fast" else _dialog_live)(dialog_spec["utterance"])
    dialog_ok = bool(dialog_resp.get("reply"))

    nav_ok, nav_detail = _run_swift_tests()

    mission_agent_resp = (_mission_agent_fast if tier == "fast" else _mission_agent_live)(
        mission_agent_spec["utterance"])
    mission_agent_ok = mission_agent_resp.get("action") in _VALID_ACTIONS

    follow_resp = (_mission_agent_fast if tier == "fast" else _mission_agent_live)(
        follow_spec["utterance"])
    follow_ok = _follow_target_ok(follow_resp)

    ok = dialog_ok and nav_ok and mission_agent_ok and follow_ok
    reason = "ok" if ok else "; ".join(
        s for s in [None if dialog_ok else "dialog check failed",
                    None if nav_ok else f"swift test failed: {nav_detail}",
                    None if mission_agent_ok else "mission_agent check failed",
                    None if follow_ok else f"follow check failed: {follow_resp}"] if s)
    return {"type": "phrover", "tier": tier, "ok": ok, "reason": reason,
            "dialog": {"utterance": dialog_spec["utterance"], "response": dialog_resp},
            "navigate": {"mission": nav_spec["mission"], "swift_test_ok": nav_ok},
            "mission_agent": {"utterance": mission_agent_spec["utterance"], "response": mission_agent_resp},
            "follow": {"utterance": follow_spec["utterance"], "response": follow_resp}}


def run_fast() -> dict[str, Any]:
    return _run("fast")


def run_live() -> dict[str, Any]:
    return _run("live")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tier", choices=["fast", "live"], default="fast")
    args = ap.parse_args()
    result = run_fast() if args.tier == "fast" else run_live()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)
