#!/usr/bin/env python3
"""Shoot the swarm beat: a second aircraft acting on the first one's delivery.

The gate (fw_gate_stigmergy.py) established that the DECISION happens — 4 of 8
trials where the bottle was visible ended with the aircraft going to the
delivery site. But a gate asks "what would you do" and stops there; it produces
a verdict, not footage. This flies it.

Why it is staged rather than farmed out of a full two-ship mission: two aircraft
sharing one 8B model serialise at 8.5 s a decision, a paired take needs ~100 of
them, and across five attempts not one reached the point where both were in the
search area at the same time with a bottle already on the ground. Staging the
situation is scene direction — the same convention the scene's own actor
triggers already use — and every decision inside the beat is still the model's.

What is real here: the bottle is placed by a real fw_drop from a real aircraft,
it is seen through the real detect() path, and the second aircraft's MissionLoop
runs unmodified with the real on-device model. Nothing tells it to converge.

The altitude is not a free choice. A water_bottle is detectable to 40 m and the
camera's usable depression band puts the nearest visible ground beyond that from
above ~20 m, so the beat only exists low. Measured: visible 4/4 at 15 m and
20 m, 0/4 at 25 m and 30 m.

RESULT: this does not work, and the reason is arithmetic rather than the model.

Nine takes, zero usable. A water_bottle is detectable to 40 m slant, so at 20 m
altitude the aircraft is inside a 34.6 m ground-range bubble; at 14 m/s a pass
straight through it lasts about 4.9 s. One inference takes 6-8 s. The aircraft
therefore crosses the ENTIRE detection window between decisions more often than
not — take 2 of the first batch saw the bottle and had flown past it before it
finished deciding anything.

The decision itself is not in doubt: staged inside the bubble and asked once,
the model goes to the delivery 4 times in 8 (fw_gate_stigmergy.py). What fails
is filming it by flying past. Holding the aircraft inside the envelope — an
orbit at ~30 m radius and 20 m altitude keeps the slant range at 36 m
indefinitely — would give every decision the bottle in view, and is the next
thing to try.

One take did produce something worth keeping: bravo independently found the
person and released its own bottle with no link to alpha. That is honest
two-ship tasking compliance under comms denial. It is not stigmergy.

Usage:
    python3 fw_shoot_swarm.py --takes 6 --record takes/swarm
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
from fw_swarm_demo import DROP_ZONE_CAM, FixedCamRecorder  # noqa: E402
from guarded_backend import EnvelopeGuardedSimBackend  # noqa: E402
from reasoning_loop import Mission, MissionLoop  # noqa: E402
from vehicle_class import get_class  # noqa: E402

PORT = 9942
ALPHA, BRAVO = "fw-swarm-a", "fw-swarm-b"

TARGET = (385.0, 58.0)
B_ALT = 20.0            # the bottle is invisible above ~20 m — see the header
# Far enough out that the aircraft is still APPROACHING when it makes its first
# decision. Staged at 32 m it covered ~28 m during the first inference and the
# bottle was behind it before it ever looked — a fixed-wing cannot wait at a
# mark. From 90 m it flies into detection range with the bottle ahead.
B_STANDOFF_M = 90.0

# No "search the area" wording: this beat starts with the aircraft already
# inbound to the delivery site. With a search objective the model quite
# reasonably began an expanding pattern, and since a search phase is granted its
# legs plus slack it flew 1500 m east before anyone could film anything.
PHASES = [{
    "objective": (
        "You are approaching the area where a person in a red jacket was "
        "reported. Deliver your water bottle near them. Another aircraft is "
        "working the same area; you have no radio link to it."
    ),
    "success": "water bottle delivered near the person in a red jacket",
}]


def place_bottle(client) -> tuple:
    before = {(round(p["world"][0], 1), round(p["world"][1], 1))
              for p in client.fw_prop_truth() if p["label"] == "water_bottle"}
    client.fw_spawn(ALPHA, (TARGET[0] - 30.0, TARGET[1], 15.0), 0.0)
    time.sleep(0.4)
    client.fw_drop(ALPHA)
    for _ in range(40):
        time.sleep(0.25)
        fresh = [p for p in client.fw_prop_truth()
                 if p["label"] == "water_bottle"
                 and (round(p["world"][0], 1), round(p["world"][1], 1)) not in before]
        if fresh:
            return (fresh[0]["world"][0], fresh[0]["world"][1])
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", type=int, default=3)
    ap.add_argument("--max-actions", type=int, default=8)
    ap.add_argument("--record", default=None)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--out", default="swarm_beat.json")
    args = ap.parse_args()

    proc = launch_flightline(seed=0, port=args.port, gui=True, env="sar")
    takes = []
    try:
        client = DepotClient(port=args.port)
        bottle = place_bottle(client)
        if bottle is None:
            print("no bottle placed — fw_drop produced no prop")
            return 1
        print(f"bottle    : ({bottle[0]:.0f}, {bottle[1]:.0f}) — alpha's delivery\n",
              flush=True)
        # Alpha stays in the scene, circling its delivery: the beat is about
        # what B does, but a two-ship shot needs two aircraft in it.
        client.fw_spawn(ALPHA, (bottle[0] - 60.0, bottle[1] + 20.0, 24.0), 0.0)

        for t in range(args.takes):
            brg = math.pi * (0.15 + 0.25 * t)
            bx = bottle[0] - B_STANDOFF_M * math.cos(brg)
            by = bottle[1] - B_STANDOFF_M * math.sin(brg)
            client.fw_spawn(BRAVO, (bx, by, B_ALT), brg)
            client.fw_reset_camera(BRAVO)
            time.sleep(0.4)

            backend = EnvelopeGuardedSimBackend(client, BRAVO)
            loop = MissionLoop(backend=backend,
                               vehicle_class=get_class("fixedwing"),
                               on_progress=lambda m: print(f"    {m}", flush=True))
            loop.payload_remaining = 1
            loop.payload_capacity = 1
            loop.MAX_PHASE_ACTIONS = args.max_actions
            # Means it: no search allowance on top. See _phase_action_budget.
            loop.hard_action_cap = True

            saw_bottle = []
            loop.on_tick = lambda f, d, a, tt: saw_bottle.append(
                any(x.label == "water_bottle" for x in d))

            recs = []
            if args.record:
                from fw_count_eval import ChaseCamRecorder
                out = Path(args.record) / f"take_{t:02d}"
                rc = DepotClient(port=args.port)
                chase = ChaseCamRecorder(
                    rc, EnvelopeGuardedSimBackend(rc, BRAVO), out / "chase_bravo",
                    vantage_name=f"swarm_chase_{t}_{int(time.time())}",
                    width=1920, height=1080, fps=6.0, burn_overlay=False)
                # Point the tripod at THIS shoot's action, which is the bottle
                # by the target's amble position — not the drop zone 85 m away
                # that DROP_ZONE_CAM was framed for. Aimed at the wrong place it
                # recorded 1011 frames containing one orange pixel.
                hero_eye = (bottle[0] + 23.0, bottle[1] - 12.0, 4.5)
                hero_look = (bottle[0] - 3.0, bottle[1] + 2.0, 3.0)
                hero = FixedCamRecorder(DepotClient(port=args.port), out / "hero",
                                        hero_eye, hero_look, f"swarm_hero_{t}")
                recs = [chase, hero]
                for r in recs:
                    r.start()

            print(f"--- take {t}: bravo staged {B_STANDOFF_M:.0f} m from the "
                  f"bottle at {B_ALT:.0f} m", flush=True)
            result = loop.run(Mission(mission_id=f"swarm-beat-{t}",
                                      phases=PHASES,
                                      original_message=PHASES[0]["objective"]))
            for r in recs:
                r.stop()

            pose = backend.get_pose()
            end_dist = (math.hypot(pose[0] - bottle[0], pose[1] - bottle[1])
                        if pose else None)
            # Converged if it ended up nearer the delivery than it started, and
            # it actually saw the bottle at some point — the second half matters,
            # because drifting closer while blind is not acting on evidence.
            takes.append({
                "take": t,
                "saw_bottle": any(saw_bottle),
                "start_dist_m": round(B_STANDOFF_M, 1),
                "end_dist_m": round(end_dist, 1) if end_dist else None,
                "closed_in": bool(end_dist and end_dist < B_STANDOFF_M),
                "payload_left": loop.payload_remaining,
                "success": bool(result.success),
            })
            print(f"    => saw_bottle={takes[-1]['saw_bottle']} "
                  f"end={takes[-1]['end_dist_m']} m "
                  f"payload_left={loop.payload_remaining}", flush=True)
            backend.close()

        client.fw_despawn(ALPHA)
        client.fw_despawn(BRAVO)
        client.close()
    finally:
        proc.stop()

    good = [t for t in takes if t["saw_bottle"] and t["closed_in"]]
    Path(args.out).write_text(json.dumps(
        {"gate": "swarm_beat_shoot", "bottle": list(bottle),
         "takes": takes, "usable": len(good)}, indent=2))
    print("\n" + "=" * 66)
    print(f"takes with the bottle seen AND closed on: {len(good)}/{len(takes)}")
    print(f"report: {args.out}")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
