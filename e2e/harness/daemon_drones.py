"""quadcopter / rover / fixed-wing: the daemon-drone e2e check.

Both tiers send the exact same scenario mission the phone app would send and assert on
the exact same response shape (``success`` + image presence) — only the transport underneath
differs:

  fast tier  -- MockCloud (harness/mock_cloud.py): a local HTTP server standing in for the
               cloud round trip, so this runs in CI with no AWS/sim/network.
  live tier  -- the real cloud + IoT MQTT, via the same eco/sim/mobile/app_client.py the
               phone app itself mirrors. Talks to a drone already brought up in Coybot Sim
               (eco/drone/sim/launch_fleet_mac.sh) or on real hardware — the harness doesn't
               care which, exactly like a real phone user wouldn't.
"""
from __future__ import annotations

import sys
import urllib.request
import json
from pathlib import Path
from typing import Any

from .fixtures import drone_id, load_scenarios
from .mock_cloud import MockCloud

_ECO_DIR = Path(__file__).resolve().parents[2]
if str(_ECO_DIR) not in sys.path:
    sys.path.insert(0, str(_ECO_DIR))


def _check_expect(expect: dict, success: bool, image_urls: list[str],
                  video_urls: list[str] | None = None) -> tuple[bool, str]:
    if expect.get("response_ok") and not success:
        return False, "expected a successful response, got failure"
    if expect.get("has_image") and not image_urls:
        return False, "expected an image in the response, got none"
    if expect.get("has_video") and not (video_urls or []):
        return False, "expected a recorded video in the response, got none"
    return True, "ok"


def _scenarios_for(vehicle_type: str) -> list[tuple[str, dict]]:
    """The primary mission, plus the optional media mission when the type
    defines one. Two runs rather than one combined mission so the original
    photo path keeps its own coverage — a media regression should not be able
    to hide behind it, or vice versa."""
    scenario = load_scenarios()[vehicle_type]
    runs = [("mission", scenario)]
    if scenario.get("media"):
        runs.append(("media", scenario["media"]))
    return runs


def _combine(vehicle_type: str, tier: str, runs: list[dict]) -> dict[str, Any]:
    """Fold per-scenario results into the single-dict shape run_e2e expects,
    keeping each scenario's own outcome visible in the scorecard."""
    ok = all(r["ok"] for r in runs)
    failed = [f"{r['name']}: {r['reason']}" for r in runs if not r["ok"]]
    return {
        "type": vehicle_type, "tier": tier, "ok": ok,
        "reason": "; ".join(failed) if failed else "ok",
        "mission": runs[0]["mission"],
        "scenarios": runs,
        "response": runs[0].get("response"),
    }


def run_fast(vehicle_type: str) -> dict[str, Any]:
    did = drone_id(vehicle_type)
    runs = []
    with MockCloud() as cloud:
        for name, scenario in _scenarios_for(vehicle_type):
            body = json.dumps({"drone_id": did, "command": scenario["mission"]}).encode()
            req = urllib.request.Request(cloud.base_url + "/command", data=body,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = json.load(r)
            ok, reason = _check_expect(scenario["expect"], resp.get("success", False),
                                       resp.get("image_urls", []),
                                       resp.get("video_urls", []))
            runs.append({"name": name, "ok": ok, "reason": reason,
                         "mission": scenario["mission"], "response": resp})
    return _combine(vehicle_type, "fast", runs)


def run_live(vehicle_type: str, online_timeout_s: float = 240.0,
            mission_timeout_s: float = 240.0) -> dict[str, Any]:
    """Assumes a sim (or real) drone with this id is already Online — see
    eco/drone/sim/launch_fleet_mac.sh / eco/e2e/README.md for how to bring one up."""
    from sim.mobile.app_client import AppClient  # noqa: E402 (path set up above)

    scenarios = _scenarios_for(vehicle_type)
    did = drone_id(vehicle_type)
    client = AppClient(drone_ids=[did])
    client.connect()
    try:
        online = client.wait_online(timeout=online_timeout_s)
        if did not in online:
            return {"type": vehicle_type, "tier": "live", "ok": False,
                    "reason": f"{did} never came online",
                    "mission": scenarios[0][1]["mission"]}
        runs = []
        for name, scenario in scenarios:
            responses = client.send_mission(scenario["mission"], timeout=mission_timeout_s)
            resp = responses[0] if responses else {}
            ok, reason = _check_expect(scenario["expect"], bool(resp.get("success")),
                                       resp.get("image_urls") or [],
                                       resp.get("video_urls") or [])
            runs.append({"name": name, "ok": ok, "reason": reason,
                         "mission": scenario["mission"], "response": resp})
        return _combine(vehicle_type, "live", runs)
    finally:
        client.close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("type", choices=["quadcopter", "rover", "fixedwing"])
    ap.add_argument("--tier", choices=["fast", "live"], default="fast")
    args = ap.parse_args()
    result = run_fast(args.type) if args.tier == "fast" else run_live(args.type)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)
