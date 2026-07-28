#!/usr/bin/env python3
"""Gate: will a drone converge on a teammate that is behaving like it found something?

This is the swarm claim, isolated. Beats 8-9 of the storyboard rest entirely on
one decision: drone B, searching its own sector with no radio, sees drone A
descend and circle low, and concludes on that evidence alone that A has found
something worth going to.

That decision has never once been observed. Across every two-drone take,
`first_peer_sighting` was None — but the reason was opportunity, not capability:
bravo could not complete its transit, so the two aircraft were never in the
search area at the same time. Waiting for a full mission to produce the
situation costs ~20 minutes a try and usually never reaches it.

So stage the situation directly, the same way the M1 red-jacket gate staged
subjects instead of flying to find them, and the same way the scene's own actor
triggers work: placement is scene direction, the decision stays the model's.
Nothing here tells B what to conclude or where to go — it is given the real
PEER OBSERVATIONS block built by the real PeerTracker from real detections, and
asked what to do next.

What counts as converging: any action that moves B toward A's loiter point, or
that names A's position. What counts as a fail: B carrying on with its own
search pattern as though nothing had happened. Both are judged from the action,
not from the prose.

Usage:
    python3 fw_gate_peer.py [--trials 20] [--out peer_gate.json]
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

PORT = 9953
A, B = "fw-peer-a", "fw-peer-b"

# A is over the target, low and circling — the behaviour the doctrine paragraph
# in the system prompt describes as worth noticing. B is mid-search in its own
# sector, far enough away that A is a speck but well inside the 300 m peer range.
TARGET = (385.0, 58.0)
A_ALT = 22.0
A_RADIUS = 55.0
B_ALT = 35.0
# Absolute positions in bravo's actual sector — the southern half of the
# 120 m-radius area around (400, 0) — NOT offsets from the target.
#
# Offsets put three of four start positions at x < 250, which is west of the
# wall, with the wall therefore directly between the two aircraft. M4 already
# established that peer detection is correctly blocked by structure, so the
# gate was faithfully re-measuring that instead of the decision it exists to
# test, and reporting "no sighting" as though the peer channel were broken.
# Third time this session that staging geometry has quietly tested the wrong
# thing, hence the assertion in main().
B_STARTS = [(330.0, -60.0), (360.0, -80.0), (300.0, -40.0), (380.0, -100.0)]

PHASE = {
    "objective": (
        "Search the SOUTHERN half of the area within 120 metres of east=400, "
        "north=0 for a person in a red jacket. Fly an expanding search pattern. "
        "Deliver your water bottle near the person in the red jacket once found."
    ),
    "success": "water bottle delivered near the person in a red jacket",
}


# A closing to deliver descends from search altitude to the run-in altitude. The
# rate has to be a real one: PeerTracker only calls it "descending" past
# 0.35 m/s, and only calls it "staying over one spot" once it has more than 20 s
# of history.
A_START_ALT = 34.0
SAMPLE_POINTS = 28          # two laps' worth
LAP_TIME_S = 2 * math.pi * A_RADIUS / 14.0   # a 55 m orbit at cruise, ~25 s


def build_peer_history(loop, client, points: int = SAMPLE_POINTS):
    """Fly A round its low orbit, sampling from B's camera, until B has a trend.

    Positions and detections are real — A is flown to each point and B looks at
    it through the real detect() path, so the PEER OBSERVATIONS block is the one
    a flight would produce. Only the CLOCK is compressed: sightings are stamped
    with the time the orbit would actually have taken (a 55 m lap at cruise is
    ~25 s) rather than the wall-clock of staging them.

    That matters, and getting it wrong made the first run of this gate
    meaningless. PeerTracker needs >5 s of history to report "descending" and
    >20 s to report "staying over one spot" — the two cues the doctrine
    paragraph actually names. Staged in real time the whole orbit took 4 s, so
    the block said only "flying low", and the gate was asking whether the model
    converges on a strictly weaker signal than the beat describes.
    """
    seen = 0
    t0 = time.monotonic()
    for i in range(points):
        frac = i / max(points - 1, 1)
        ang = 2 * math.pi * (i / (points / 2.0))
        ax = TARGET[0] + A_RADIUS * math.cos(ang)
        ay = TARGET[1] + A_RADIUS * math.sin(ang)
        alt = A_START_ALT + (A_ALT - A_START_ALT) * frac   # ~0.5 m/s descent
        client.fw_spawn(A, (ax, ay, alt), ang + math.pi / 2)
        time.sleep(0.2)
        stamp = t0 + frac * (2 * LAP_TIME_S)
        for d in client.fw_detect(B):
            if d.get("label") == "aircraft" and d.get("world"):
                w = d["world"]
                loop.peers.observe(d.get("peer_id", A), w[0], w[1], w[2], t=stamp)
                seen += 1
    return seen


def classify(action, a_pos) -> tuple:
    """Did this action move toward the teammate? Judged on the action, not prose."""
    kind = action.action_type.value
    if kind == "navigate_to_world" and action.world_x is not None:
        d = math.hypot(action.world_x - a_pos[0], action.world_y - a_pos[1])
        return ("converge" if d < 120.0 else "elsewhere", f"world ({action.world_x:.0f},"
                f" {action.world_y:.0f}) is {d:.0f} m from the teammate")
    if kind == "orbit_point" and action.world_x is not None:
        d = math.hypot(action.world_x - a_pos[0], action.world_y - a_pos[1])
        return ("converge" if d < 120.0 else "elsewhere", f"orbit {d:.0f} m from teammate")
    tgt = (action.target_object or "").lower()
    if kind == "return_to_landmark" and "teammate" in tgt:
        return ("converge", "return_to_landmark teammate_last_seen")
    # Flying at the teammate by name is converging. This was scored as "other"
    # at first, which would have undercounted the very behaviour being measured
    # — the model reaches for navigate_to_object because the teammate is a
    # labelled thing in CURRENT DETECTIONS, which is a perfectly reasonable way
    # to express "go and look at that".
    if kind == "navigate_to_object" and any(w in tgt for w in ("aircraft", "teammate", "drone")):
        return ("converge", f"navigate_to_object '{action.target_object}'")
    if kind in ("search_area", "navigate_to_point"):
        return ("own_search", f"carried on with {kind}")
    return ("other", kind)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--out", default="peer_gate.json")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    proc = launch_flightline(seed=0, port=args.port, gui=True, env="sar")
    rows = []
    try:
        client = DepotClient(port=args.port)
        backend_b = EnvelopeGuardedSimBackend(client, B)

        # Refuse to run a rig that cannot answer the question. Both aircraft
        # must be on the same side of the wall, or the gate measures occlusion
        # (already proven in M4) and calls it a peer-channel failure.
        wall_e = client.fw_env_state()["wall"]["east"]
        bad = [p for p in B_STARTS if (p[0] - wall_e) * (TARGET[0] - wall_e) <= 0]
        if bad:
            print(f"staging error: {bad} are on the far side of the wall at "
                  f"east={wall_e:.0f} from the target — the wall would occlude "
                  f"the teammate and the result would say nothing about the "
                  f"decision this gate exists to test.")
            return 1
        print(f"staging   : wall at east={wall_e:.0f}; all {len(B_STARTS)} start "
              f"positions and the target are east of it\n")

        for t in range(args.trials):
            bx, by = B_STARTS[t % len(B_STARTS)]
            client.fw_spawn(B, (bx, by, B_ALT), math.atan2(TARGET[1] - by, TARGET[0] - bx))
            client.fw_reset_camera(B)
            time.sleep(0.3)

            loop = MissionLoop(backend=backend_b, vehicle_class=get_class("fixedwing"))
            loop.payload_remaining = 1
            loop.payload_capacity = 1

            sightings = build_peer_history(loop, client)
            a_state = client.fw_state(A)
            a_pos = (a_state["position"][0], a_state["position"][1])
            peer_block = loop.peers.summarize(
                (bx, by, B_ALT)) if sightings else ""

            if not sightings:
                rows.append({"trial": t, "sightings": 0, "verdict": "no_sighting",
                             "note": "B never saw A — nothing to decide on"})
                print(f"trial {t:2d}: NO SIGHTING (peer channel gave B nothing)", flush=True)
                continue

            # The block must actually carry the cues the beat is about, or this
            # trial asks a weaker question than the one that matters. Caught
            # live: staged in real time the whole orbit took 4 s, PeerTracker
            # needs >5 s for "descending" and >20 s for "staying over one spot",
            # so the model was shown "flying low" alone and scored for not
            # converging on it.
            cues = [c for c in ("descending", "staying over one spot")
                    if c not in peer_block]
            if cues:
                rows.append({"trial": t, "sightings": sightings,
                             "verdict": "weak_cues", "missing": cues,
                             "peer_block": peer_block})
                print(f"trial {t:2d}: WEAK CUES — block lacks {cues}; not a fair "
                      f"test of the decision", flush=True)
                continue

            # Exactly the call MissionLoop makes, so the model sees the prompt a
            # real flight would have built — same blocks, same detections, same
            # PeerTracker output. A gate that assembled its own prompt would be
            # evidence about a prompt nobody flies.
            frame = backend_b.capture_frame()
            dets = backend_b.detect()
            action = loop._get_vlm().decide(
                image=frame,
                mission_phase=PHASE,
                drone_state=loop._get_drone_state(),
                history=[],
                detections=dets,
                memory=loop.memory.all(),
                extra_context=loop._situation_blocks(loop.peers.peers(), dets),
            )
            verdict, why = classify(action, a_pos)
            rows.append({
                "trial": t, "sightings": sightings, "verdict": verdict,
                "action": action.action_type.value, "why": why,
                "reasoning": (action.reasoning or "")[:220],
                "peer_block": peer_block,
            })
            print(f"trial {t:2d}: {verdict:10s} {action.action_type.value:20s} {why}",
                  flush=True)

        client.fw_despawn(A)
        client.fw_despawn(B)
        backend_b.close()
        client.close()
    finally:
        proc.stop()

    decided = [r for r in rows if r["verdict"] not in ("no_sighting", "weak_cues")]
    converged = [r for r in decided if r["verdict"] == "converge"]
    report = {
        "gate": "swarm_peer_convergence",
        "weak_cue_trials": sum(1 for r in rows if r["verdict"] == "weak_cues"),
        "pass_bar": "at least half of the trials in which B actually saw A end in "
                    "an action that moves B toward A",
        "trials": len(rows),
        "trials_with_a_sighting": len(decided),
        "converged": len(converged),
        "detail": rows,
    }
    Path(args.out).write_text(json.dumps(report, indent=2))

    print("\n" + "=" * 70)
    if not decided:
        print("PEER GATE INCONCLUSIVE — B never saw A in any trial. That is a "
              "SENSING problem, not a decision one; fix the channel first.")
        return 1
    rate = len(converged) / len(decided)
    print(f"saw the teammate     : {len(decided)}/{len(rows)} trials")
    print(f"chose to converge    : {len(converged)}/{len(decided)} ({100 * rate:.0f}%)")
    print(f"\nreport: {args.out}")
    if rate >= 0.5:
        print("PEER GATE PASS — the swarm beat is reachable from evidence alone.")
        return 0
    print("PEER GATE FAIL — B sees the teammate and carries on regardless. The "
          "swarm beat cannot be filmed honestly until this changes.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
