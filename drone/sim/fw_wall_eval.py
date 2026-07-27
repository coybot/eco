#!/usr/bin/env python3
"""M6: does the model route around the wall from its own camera?

The demo claims camera-based obstacle avoidance with no path planner. That
claim is only worth making if the aircraft is genuinely deciding — so this runs
the real MissionLoop with the real on-device model and the real forward camera,
gives it a destination on the far side of a 50 m wall, and scores whether it
gets there without the envelope guard ever having to save it.

What counts as a pass, and why:

* the aircraft ends up east of the wall (it actually got through);
* ZERO envelope_protection events. The guard turning the aircraft away means
  the MODEL failed and deterministic code rescued it — a take with
  interventions is not evidence of avoidance, so it is scored as a failure
  rather than quietly cleaned up;
* it never crosses the wall's span. Aircraft have no collision body here, so
  "didn't hit it" is not observable; flying THROUGH the wall looks identical to
  flying past it unless the track is checked explicitly.

There is no scripted detour anywhere in this file. The route is whatever the
model chooses from the OBSTACLES block and what it sees.

Usage:
    python3 fw_wall_eval.py [--runs 10] [--out wall_report.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))
sys.path.insert(0, str(SIM_DIR.parent / "common"))

from depot_client import DepotClient  # noqa: E402
from fw_eval import launch_flightline  # noqa: E402
from guarded_backend import EnvelopeGuardedSimBackend  # noqa: E402
from reasoning_loop import Mission, MissionLoop  # noqa: E402
from vehicle_class import get_class  # noqa: E402

RID = "fw-wall"
PORT = 9979

GOAL = (400.0, 0.0)
START = (-30.0, 0.0, 35.0)

OBJECTIVE = (
    "Fly east to the search area around world coordinates (400, 0). There is a "
    "tall wall across the direct route. Get to the search area without flying "
    "into it."
)
SUCCESS = "The aircraft has reached the search area east of the wall."


def run_once(client, wall, run_idx: int, max_actions: int) -> dict:
    client.fw_spawn(RID, START, 0.0)
    client.fw_reset_camera(RID)
    time.sleep(0.3)

    backend = EnvelopeGuardedSimBackend(client, RID)
    loop = MissionLoop(backend=backend, vehicle_class=get_class("fixedwing"),
                       on_progress=lambda m: print(f"    {m}", flush=True))
    loop.MAX_PHASE_ACTIONS = max_actions

    decisions = []

    def on_tick(frame, detections, action, t):
        decisions.append({
            "action": action.action_type.value,
            "reasoning": (action.reasoning or "")[:200],
            "saw_wall": any(d.label == "wall" for d in detections),
        })

    loop.on_tick = on_tick

    t0 = time.time()
    result = loop.run(Mission(
        mission_id=f"wall-{run_idx}",
        phases=[{"objective": OBJECTIVE, "success": SUCCESS}],
        original_message=OBJECTIVE,
    ))
    elapsed = time.time() - t0

    # Check the flown track from the sim's own pose trace, not just decision
    # ticks — a wall crossing between two decisions would otherwise be invisible.
    poses = [e["data"] for e in client.fw_events(RID)
             if e.get("kind") == "pose_trace" and e["data"].get("id") == RID]
    crossed = False
    for i in range(1, len(poses)):
        a, b = poses[i - 1], poses[i]
        if (a["x"] - wall["east"]) * (b["x"] - wall["east"]) < 0:
            frac = (wall["east"] - a["x"]) / ((b["x"] - a["x"]) or 1e-9)
            y_at = a["y"] + frac * (b["y"] - a["y"])
            if abs(y_at) <= wall["half_n"]:
                crossed = True
                break

    final = backend.get_pose()
    east_of_wall = final is not None and final[0] > wall["east"]
    dist_to_goal = (math.hypot(final[0] - GOAL[0], final[1] - GOAL[1])
                    if final else None)

    client.fw_despawn(RID)

    return {
        "run": run_idx,
        "passed": bool(east_of_wall and backend.envelope_events == 0 and not crossed),
        "east_of_wall": east_of_wall,
        "flew_through_wall": crossed,
        "envelope_events": backend.envelope_events,
        "legs_refused": backend.legs_refused,
        "dist_to_goal_m": round(dist_to_goal, 1) if dist_to_goal else None,
        "actions": len(decisions),
        "saw_wall_at_least_once": any(d["saw_wall"] for d in decisions),
        "elapsed_s": round(elapsed, 1),
        "mission_success": result.success,
        "decisions": decisions,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--max-actions", type=int, default=8)
    ap.add_argument("--out", default="wall_report.json")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    proc = launch_flightline(seed=0, port=args.port, gui=True, env="sar")
    runs = []
    try:
        client = DepotClient(port=args.port)
        wall = client.fw_env_state()["wall"]
        print(f"wall: east={wall['east']} height={wall['height']} "
              f"span=+-{wall['half_n']}\n" + "=" * 70, flush=True)
        for i in range(args.runs):
            print(f"\n--- run {i + 1}/{args.runs} ---", flush=True)
            r = run_once(client, wall, i, args.max_actions)
            runs.append(r)
            print(f"  => {'PASS' if r['passed'] else 'FAIL'} "
                  f"east={r['east_of_wall']} through_wall={r['flew_through_wall']} "
                  f"envelope={r['envelope_events']} refused={r['legs_refused']} "
                  f"dist_to_goal={r['dist_to_goal_m']}m", flush=True)
        client.close()
    finally:
        proc.stop()

    passed = sum(1 for r in runs if r["passed"])
    report = {
        "gate": "M6_camera_wall_avoidance",
        "pass_bar": "all runs east of the wall, zero envelope interventions, "
                    "no track crossing the wall span",
        "runs": len(runs),
        "passed": passed,
        "total_envelope_events": sum(r["envelope_events"] for r in runs),
        "total_legs_refused": sum(r.get("legs_refused", 0) for r in runs),
        "flew_through_wall": sum(1 for r in runs if r["flew_through_wall"]),
        "saw_wall": sum(1 for r in runs if r["saw_wall_at_least_once"]),
        "detail": runs,
    }
    Path(args.out).write_text(json.dumps(report, indent=2))

    print("\n" + "=" * 70)
    print(f"M6 {'PASS' if passed == len(runs) else 'FAIL'} — {passed}/{len(runs)} runs clean")
    print(f"  envelope interventions : {report['total_envelope_events']} (must be 0)")
    print(f"  flew through the wall  : {report['flew_through_wall']} (must be 0)")
    print(f"  saw the wall on camera : {report['saw_wall']}/{len(runs)}")
    print(f"\nreport: {args.out}")
    return 0 if passed == len(runs) else 1


if __name__ == "__main__":
    sys.exit(main())
