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
# The search area in env_sar is r=120 around the same centre, so entering it is
# literally what the objective asks for.
GOAL_RADIUS_M = 120.0
START = (-30.0, 0.0, 35.0)

OBJECTIVE = (
    "Fly east to the search area around world coordinates (400, 0). There is a "
    "tall wall across the direct route. Get to the search area without flying "
    "into it."
)
SUCCESS = "The aircraft has reached the search area east of the wall."


def run_once(client, wall, run_idx: int, max_actions: int,
             record_dir=None, client_factory=None, port: int = PORT) -> dict:
    client.fw_spawn(RID, START, 0.0)
    client.fw_reset_camera(RID)
    time.sleep(0.3)

    backend = EnvelopeGuardedSimBackend(client, RID)
    loop = MissionLoop(backend=backend, vehicle_class=get_class("fixedwing"),
                       on_progress=lambda m: print(f"    {m}", flush=True))

    # Optional recording, so a CLEAN transit produces footage.
    #
    # This gate is where unaided wall avoidance actually happens — 3 runs in 5
    # with zero interventions — while the full mission's wall beat keeps failing
    # on envelope events. Without capture here the one place the claim is genuinely
    # demonstrated leaves no film, and the beat has to be cut from a take where
    # the guard helped.
    recorder = None
    if record_dir is not None and client_factory is not None:
        from fw_count_eval import ChaseCamRecorder
        # The ACTUAL port this run is on. Falling back to the module default
        # connected to nothing and the recorder died with ConnectionRefused.
        rec_client = client_factory(port)
        recorder = ChaseCamRecorder(
            rec_client, EnvelopeGuardedSimBackend(rec_client, RID),
            Path(record_dir) / f"run_{run_idx:02d}",
            vantage_name=f"chase_wall_{run_idx}_{int(time.time())}",
            width=1920, height=1080, fps=6.0,
            burn_overlay=False)
        loop.on_progress = lambda m: (print(f"    {m}", flush=True),
                                      recorder.on_progress(m))
        recorder.start()
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

    # Check the flown track, not just decision ticks — a wall crossing between
    # two decisions would otherwise be invisible.
    #
    # Prefer the guard's own 10 Hz samples over the sim's 1 Hz pose_trace. At
    # cruise the 1 Hz trace is 14 m apart, and this test joins consecutive
    # samples with a straight chord: rounding the wall's north end runs close
    # enough to a span ending at +-70 m that a chord can cut the corner and
    # report a crossing that never happened — which is exactly what a run
    # showing `through=True` alongside `envelope=0` was, since the guard sits at
    # the same wall and had nothing to say. Same source for both now.
    poses = backend.track or [
        e["data"] for e in client.fw_events(RID)
        if e.get("kind") == "pose_trace" and e["data"].get("id") == RID]
    crossed = False
    crossed_over = False
    crossing = None
    for i in range(1, len(poses)):
        a, b = poses[i - 1], poses[i]
        if (a["x"] - wall["east"]) * (b["x"] - wall["east"]) < 0:
            frac = (wall["east"] - a["x"]) / ((b["x"] - a["x"]) or 1e-9)
            y_at = a["y"] + frac * (b["y"] - a["y"])
            # Where it got past, whichever way. Worth recording even for a clean
            # pass: "round the north end at n=+96" is the demo's whole claim, and
            # it is the first thing to look at when a crossing verdict surprises.
            crossing = {"n": round(y_at, 1),
                        "alt": round(a["z"] + frac * (b["z"] - a["z"]), 1),
                        "span_half_n": wall["half_n"]}
            if abs(y_at) > wall["half_n"]:
                continue                      # went round an end — the point
            z_at = a["z"] + frac * (b["z"] - a["z"])
            # Crossing the wall's footprint ABOVE its top is flying over it,
            # not through it. Climbing over a 50 m wall with a 120 m ceiling is
            # a perfectly good answer to "get past this", and the model is never
            # told not to. Scoring it as a crash was a bug in the measurement:
            # this check, like the occupancy grid the guard uses, is purely 2D
            # and cannot tell the two apart without consulting altitude.
            if z_at > wall["height"] + 2.0:
                crossed_over = True
            else:
                crossed = True
                break

    if recorder is not None:
        recorder.stop()

    final = backend.get_pose()
    east_of_wall = final is not None and final[0] > wall["east"]
    dist_to_goal = (math.hypot(final[0] - GOAL[0], final[1] - GOAL[1])
                    if final else None)

    # Did it ever actually get to the search area? Closest approach, not final
    # position, because nothing holds a fixed-wing still — an aircraft that
    # flew through the area and out the far side still reached it.
    #
    # This is the whole objective and the gate did not check it. Runs were
    # scored PASS for clearing the wall and then flying 4 km into open country
    # with mission_success false: every stated criterion met, the mission
    # plainly failed. Getting past the wall is only interesting as a means of
    # getting somewhere.
    closest = min((math.hypot(p["x"] - GOAL[0], p["y"] - GOAL[1]) for p in poses),
                  default=None)
    reached_goal = closest is not None and closest <= GOAL_RADIUS_M

    # Stops the envelope watchdog thread. Leaving it running would have it fly
    # a despawned aircraft, and its events would land in the next run's count.
    backend.close()
    client.fw_despawn(RID)

    return {
        "run": run_idx,
        "passed": bool(east_of_wall and reached_goal
                       and backend.envelope_events == 0 and not crossed),
        "reached_goal": reached_goal,
        "crossing": crossing,
        "track_points": len(poses),
        "closest_to_goal_m": round(closest, 1) if closest is not None else None,
        "east_of_wall": east_of_wall,
        "flew_through_wall": crossed,
        "flew_over_wall": crossed_over,
        "envelope_events": backend.envelope_events,
        "legs_refused": backend.legs_refused,
        "dist_to_goal_m": round(dist_to_goal, 1) if dist_to_goal else None,
        "actions": len(decisions),
        "saw_wall_at_least_once": any(d["saw_wall"] for d in decisions),
        "elapsed_s": round(elapsed, 1),
        "mission_success": result.success,
        "decisions": decisions,
        # Kept so the cut can be anchored on a MOMENT rather than on an
        # arbitrary slice of the run. Every track sample carries wall-clock
        # time and the recorded frames carry mtimes, so "the frames where it
        # rounded the wall's north end" is a lookup rather than a guess.
        "track": [{"x": round(p["x"], 1), "y": round(p["y"], 1),
                   "z": round(p["z"], 1), "t": round(p.get("t", 0.0), 2)}
                  for p in poses],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--max-actions", type=int, default=8)
    ap.add_argument("--out", default="wall_report.json")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--record", default=None,
                    help="directory to record 1080p chase footage into")
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
            r = run_once(client, wall, i, args.max_actions,
                         record_dir=args.record, port=args.port,
                         client_factory=(lambda p: DepotClient(port=p))
                         if args.record else None)
            runs.append(r)
            print(f"  => {'PASS' if r['passed'] else 'FAIL'} "
                  f"east={r['east_of_wall']} through={r['flew_through_wall']} over={r['flew_over_wall']} "
                  f"envelope={r['envelope_events']} refused={r['legs_refused']} "
                  f"reached_goal={r['reached_goal']} "
                  f"closest={r['closest_to_goal_m']}m "
                  f"passed_wall_at_n={(r['crossing'] or {}).get('n')}", flush=True)
        client.close()
    finally:
        proc.stop()

    passed = sum(1 for r in runs if r["passed"])
    report = {
        "gate": "M6_camera_wall_avoidance",
        "pass_bar": "all runs reach the search area, east of the wall, zero "
                    "envelope interventions, no track crossing the wall span",
        "runs": len(runs),
        "passed": passed,
        "total_envelope_events": sum(r["envelope_events"] for r in runs),
        "total_legs_refused": sum(r.get("legs_refused", 0) for r in runs),
        "flew_through_wall": sum(1 for r in runs if r["flew_through_wall"]),
        "flew_over_wall": sum(1 for r in runs if r["flew_over_wall"]),
        "saw_wall": sum(1 for r in runs if r["saw_wall_at_least_once"]),
        "reached_goal": sum(1 for r in runs if r["reached_goal"]),
        "detail": runs,
    }
    Path(args.out).write_text(json.dumps(report, indent=2))

    print("\n" + "=" * 70)
    print(f"M6 {'PASS' if passed == len(runs) else 'FAIL'} — {passed}/{len(runs)} runs clean")
    print(f"  reached the search area: {report['reached_goal']}/{len(runs)} (the objective)")
    print(f"  envelope interventions : {report['total_envelope_events']} (must be 0)")
    print(f"  flew through the wall  : {report['flew_through_wall']} (must be 0)")
    print(f"  saw the wall on camera : {report['saw_wall']}/{len(runs)}")
    print(f"\nreport: {args.out}")
    return 0 if passed == len(runs) else 1


if __name__ == "__main__":
    sys.exit(main())
