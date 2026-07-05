"""phrover (PhroverOperator / WAVE ROVER) e2e check.

Two independent, unrelated checks — see scenarios.yaml's comment for why:

  dialog   -- POST /rover/converse, the open-ended small-talk fallback (aws/src/rover.py).
             fast tier hits harness/mock_rover_converse.py; live tier hits the real
             deployed endpoint.
  navigate -- on-device RoverNav planning/driving. Identical in both tiers: it's pure
             Swift, offline, deterministic — RoverNavTests in the public astral-sdk repo
             (sibling of this repo; see ../../../sdk). NavIntegrationTests
             .testDriveAroundCornerToGoalCollisionFree already asserts "plan reaches the
             goal without violating the costmap" for a doorway-shaped gap, matching the
             scenario's mission text.

RoverNav now lives in the same SwiftPM package as PhroverKit/PhroverCloud, which import
ARKit (unavailable on macOS) — so plain `swift test` there tries to build everything and
fails. We run RoverNavTests through `xcodebuild test` against an iOS Simulator instead,
which is still hardware-free (no phone, no chassis, no AWS creds).

App-level phrover coverage (actually driving PhroverOperator's UI against a mocked WAVE
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

_SDK_DIR = Path(__file__).resolve().parents[3] / "sdk"


def _first_available_simulator() -> str | None:
    proc = subprocess.run(["xcrun", "simctl", "list", "devices", "available", "-j"],
                          capture_output=True, text=True, timeout=30)
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
        return False, f"astral-sdk package not found at {_SDK_DIR} (expected as a sibling of this repo)"
    udid = _first_available_simulator()
    if not udid:
        return False, "no available iOS Simulator found (xcrun simctl list devices available)"
    proc = subprocess.run(
        ["xcodebuild", "test", "-scheme", "astral-sdk-Package",
         "-destination", f"id={udid}", "-only-testing:RoverNavTests"],
        cwd=_SDK_DIR, capture_output=True, text=True, timeout=300)
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
