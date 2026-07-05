"""phrover (RoverOperator / WAVE ROVER) e2e check.

Two independent, unrelated checks — see scenarios.yaml's comment for why:

  dialog   -- POST /rover/converse, the open-ended small-talk fallback (aws/src/rover.py).
             fast tier hits harness/mock_rover_converse.py; live tier hits the real
             deployed endpoint.
  navigate -- on-device RoverNav planning/driving. Identical in both tiers: it's pure
             Swift, offline, deterministic — `swift test` in eco/rover/nav/RoverNav.
             NavIntegrationTests.testDriveAroundCornerToGoalCollisionFree already asserts
             "plan reaches the goal without violating the costmap" for a doorway-shaped
             gap, matching the scenario's mission text.

App-level phrover coverage (actually driving RoverOperator's UI against a mocked WAVE
ROVER base) is Layer B — see harness/mock_esp32.py + run_phone.sh, not this module.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from .fixtures import load_scenarios
from .mock_rover_converse import MockRoverConverse

_ROVERNAV_DIR = Path(__file__).resolve().parents[2] / "rover" / "nav" / "RoverNav"


def _run_swift_tests() -> tuple[bool, str]:
    if not _ROVERNAV_DIR.exists():
        return False, f"RoverNav package not found at {_ROVERNAV_DIR}"
    proc = subprocess.run(["swift", "test"], cwd=_ROVERNAV_DIR,
                          capture_output=True, text=True, timeout=300)
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


def _run(tier: str) -> dict[str, Any]:
    scenario = load_scenarios()["phrover"]
    dialog_spec = scenario["dialog"]
    nav_spec = scenario["navigate"]

    dialog_resp = (_dialog_fast if tier == "fast" else _dialog_live)(dialog_spec["utterance"])
    dialog_ok = bool(dialog_resp.get("reply"))

    nav_ok, nav_detail = _run_swift_tests()

    ok = dialog_ok and nav_ok
    reason = "ok" if ok else "; ".join(
        s for s in [None if dialog_ok else "dialog check failed",
                    None if nav_ok else f"swift test failed: {nav_detail}"] if s)
    return {"type": "phrover", "tier": tier, "ok": ok, "reason": reason,
            "dialog": {"utterance": dialog_spec["utterance"], "response": dialog_resp},
            "navigate": {"mission": nav_spec["mission"], "swift_test_ok": nav_ok}}


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
