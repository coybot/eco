#!/usr/bin/env python3
"""Gate: does a drone act on a delivery its teammate already made?

The swarm claim's second route. The first — drone B inferring from drone A's
BEHAVIOUR that A has found something — was measured and does not happen: 32
trials across two taskings, 32 sightings of a teammate descending and circling
low over one spot, zero convergences. The model reads that as a teammate
searching, and searching elsewhere is a perfectly sensible response to it.

This tests the other evidence the storyboard already calls for: the water bottle
A dropped, lying on the ground. That is not an inference about intent, it is an
object in CURRENT DETECTIONS — and M4 already proved the channel exists, with a
bottle released by one aircraft detected by the other. It is also a truer piece
of swarm behaviour than the first version: stigmergy, coordination through marks
left in the environment, with no link between the aircraft at all.

Geometry matters here and nearly sank it unnoticed. A water_bottle is detectable
to 40 m, and the camera's usable depression band is -20 to +50 degrees, so at
the 35 m search altitude the two DO NOT OVERLAP: the nearest ground the camera
can see is already beyond the bottle's range. The channel only exists at 30 m
and below, which is why altitude is a variable here rather than an assumption.

Nothing tells B what to conclude. The bottle is placed by a real fw_drop, seen
through the real detect() path, and the decision is the model's.

Usage:
    python3 fw_gate_stigmergy.py [--trials 15] [--out stig_gate.json]
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
from reasoning_loop import MissionLoop  # noqa: E402
from vehicle_class import get_class  # noqa: E402

PORT = 9952
DROPPER, B = "fw-stig-a", "fw-stig-b"

TARGET = (385.0, 58.0)
# Altitudes to try. 35 m is included deliberately as the documented negative:
# at search altitude the bottle cannot be seen at all, and a beat that quietly
# depends on the aircraft having descended should say so.
B_ALTS = [15.0, 20.0, 25.0, 30.0]
GROUND_RANGE_M = 26.0

# How close a commanded point has to be to count as staying on the delivery.
# B is staged GROUND_RANGE_M away, so this has to be tight: inherited from the
# peer gate at 120 m (where B started 150 m out) it scored a pick resolving
# 101 m away as converging, which is flying past the thing, not to it.
CONVERGE_RADIUS_M = 40.0

PHASE = {
    "objective": (
        "Find the person in a red jacket within 120 metres of east=400, north=0 "
        "and deliver your water bottle near them. To avoid both aircraft covering "
        "the same ground, start your search in the SOUTHERN half (south of "
        "north=0) — that is a division of effort, not a boundary; go wherever the "
        "mission requires."
    ),
    "success": "water bottle delivered near the person in a red jacket",
}


def place_bottle(client) -> tuple:
    """Drop a real bottle near the target and return where it came to rest."""
    before = {(round(p["world"][0], 1), round(p["world"][1], 1))
              for p in client.fw_prop_truth() if p["label"] == "water_bottle"}
    # Run in low and level at the target, the way a delivery actually arrives.
    client.fw_spawn(DROPPER, (TARGET[0] - 30.0, TARGET[1], 15.0), 0.0)
    time.sleep(0.4)
    client.fw_drop(DROPPER)
    for _ in range(40):
        time.sleep(0.25)
        now = [p for p in client.fw_prop_truth() if p["label"] == "water_bottle"]
        fresh = [p for p in now
                 if (round(p["world"][0], 1), round(p["world"][1], 1)) not in before]
        if fresh:
            client.fw_despawn(DROPPER)
            return (fresh[0]["world"][0], fresh[0]["world"][1])
    client.fw_despawn(DROPPER)
    return None


def classify(action, backend, bottle) -> tuple:
    """Did this action keep the aircraft on the delivery, or walk away from it?"""
    kind = action.action_type.value
    tgt = (action.target_object or "").lower()

    if kind in ("navigate_to_world", "orbit_point") and action.world_x is not None:
        d = math.hypot(action.world_x - bottle[0], action.world_y - bottle[1])
        return ("converge" if d < CONVERGE_RADIUS_M else "elsewhere",
                f"{kind} {d:.0f} m from the bottle")
    if kind in ("navigate_to_object", "return_to_landmark"):
        if any(w in tgt for w in ("bottle", "person", "jacket", "water")):
            return ("converge", f"{kind} '{action.target_object}'")
        return ("other", f"{kind} '{action.target_object}'")
    if kind == "drop_payload":
        return ("converge", "moved to deliver here")
    if kind == "navigate_to_point" and action.point_x is not None:
        # Unproject the pick so a pixel aimed at the bottle counts. Judging this
        # on the action name alone would score "fly at that thing on the ground"
        # as walking away.
        w = backend.unproject(min(max(action.point_x / 640.0, 0.0), 1.0),
                              min(max(action.point_y / 480.0, 0.0), 1.0))
        if w is None:
            return ("other", "pixel did not resolve")
        d = math.hypot(w[0] - bottle[0], w[1] - bottle[1])
        return ("converge" if d < CONVERGE_RADIUS_M else "elsewhere",
                f"pixel resolves {d:.0f} m from the bottle")
    if kind == "search_area":
        return ("own_search", "carried on with the search pattern")
    return ("other", kind)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=16)
    ap.add_argument("--out", default="stig_gate.json")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    proc = launch_flightline(seed=0, port=args.port, gui=True, env="sar")
    rows = []
    try:
        client = DepotClient(port=args.port)
        bottle = place_bottle(client)
        if bottle is None:
            print("could not place a bottle — fw_drop never produced a prop")
            return 1
        print(f"bottle    : rests at ({bottle[0]:.0f}, {bottle[1]:.0f})\n")

        backend_b = EnvelopeGuardedSimBackend(client, B)
        for t in range(args.trials):
            alt = B_ALTS[t % len(B_ALTS)]
            # Approach from a bearing that puts the bottle dead ahead and below.
            brg = 2 * math.pi * (t / max(args.trials, 1))
            bx = bottle[0] - GROUND_RANGE_M * math.cos(brg)
            by = bottle[1] - GROUND_RANGE_M * math.sin(brg)
            client.fw_spawn(B, (bx, by, alt), brg)
            client.fw_reset_camera(B)
            time.sleep(0.5)

            dets = backend_b.detect()
            saw = [d for d in dets if d.label == "water_bottle"]
            if not saw:
                rows.append({"trial": t, "alt_m": alt, "verdict": "not_visible",
                             "labels": sorted({d.label for d in dets})})
                print(f"trial {t:2d} @{alt:.0f} m: BOTTLE NOT VISIBLE "
                      f"(saw {sorted({d.label for d in dets})})", flush=True)
                continue

            loop = MissionLoop(backend=backend_b, vehicle_class=get_class("fixedwing"))
            loop.payload_remaining = 1
            loop.payload_capacity = 1
            action = loop._get_vlm().decide(
                image=backend_b.capture_frame(),
                mission_phase=PHASE,
                drone_state=loop._get_drone_state(),
                history=[],
                detections=dets,
                memory=loop.memory.all(),
                extra_context=loop._situation_blocks([], dets),
            )
            verdict, why = classify(action, backend_b, bottle)
            rows.append({"trial": t, "alt_m": alt, "verdict": verdict,
                         "action": action.action_type.value, "why": why,
                         "reasoning": (action.reasoning or "")[:220]})
            print(f"trial {t:2d} @{alt:.0f} m: {verdict:10s} "
                  f"{action.action_type.value:20s} {why}", flush=True)

        client.fw_despawn(B)
        backend_b.close()
        client.close()
    finally:
        proc.stop()

    visible = [r for r in rows if r["verdict"] != "not_visible"]
    converged = [r for r in visible if r["verdict"] == "converge"]
    by_alt = {}
    for r in rows:
        a = by_alt.setdefault(r["alt_m"], {"seen": 0, "total": 0})
        a["total"] += 1
        if r["verdict"] != "not_visible":
            a["seen"] += 1

    report = {
        "gate": "swarm_stigmergy",
        "pass_bar": "at least half of the trials in which the bottle was visible "
                    "end in an action that keeps the aircraft on the delivery",
        "bottle": list(bottle),
        "trials": len(rows),
        "bottle_visible": len(visible),
        "converged": len(converged),
        "visibility_by_alt": by_alt,
        "detail": rows,
    }
    Path(args.out).write_text(json.dumps(report, indent=2))

    print("\n" + "=" * 70)
    for alt in sorted(by_alt):
        v = by_alt[alt]
        print(f"  bottle visible at {alt:.0f} m: {v['seen']}/{v['total']}")
    if not visible:
        print("\nSTIGMERGY GATE INCONCLUSIVE — the bottle was never visible. That is "
              "sensing, not decision: check altitude against the 40 m range and "
              "the -20..+50 deg depression band.")
        return 1
    rate = len(converged) / len(visible)
    print(f"\nsaw the bottle       : {len(visible)}/{len(rows)} trials")
    print(f"stayed on it         : {len(converged)}/{len(visible)} ({100 * rate:.0f}%)")
    print(f"\nreport: {args.out}")
    if rate >= 0.5:
        print("STIGMERGY GATE PASS — the swarm beat can rest on the dropped bottle.")
        return 0
    print("STIGMERGY GATE FAIL — the bottle is seen and ignored. Neither route to "
          "the swarm beat works; the video should claim two capabilities, not three.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
