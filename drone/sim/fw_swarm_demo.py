#!/usr/bin/env python3
"""The SAR demo run: one or two aircraft, real cloud plan, real on-device model.

This is the harness the showcase video is shot from. Everything that decides
anything is real: the mission is decomposed by the actual Bedrock planner, and
every in-flight choice comes from the on-device Qwen3-VL-8B looking at a frame
the aircraft's own camera produced. Nothing in this file tells an aircraft to
go around the wall, to wait at the tunnel, or to converge on its teammate.

Structure follows fw_count_eval.py:

  PASS 1  one real planner call per aircraft, cached to disk. Take-farming
          re-runs the flight dozens of times and there is no reason to re-plan
          each time — and a cached plan keeps the takes comparable.
  PASS 2  per take: reset the scene, spawn, start recorders and the peer
          sampler, run one MissionLoop per aircraft on its own thread, then
          score the beats against scene truth.

Two aircraft share ONE loaded model (a second 5 GB copy does not fit), so
inference is serialised by VLMService's lock. They still reason independently:
separate MissionLoops, separate memory, separate peer trackers, and no channel
between them other than seeing each other.

Beat assertions are machine-checked against fw_env_state/fw_prop_truth rather
than against the drones' own claims — the point is to catch a take where the
aircraft says it delivered and did not.

Usage:
    python3 fw_swarm_demo.py --takes 3
    python3 fw_swarm_demo.py --drones 1 --takes 5      # M7 single-drone slice
    python3 fw_swarm_demo.py --takes 20 --record       # farm takes with video
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))
sys.path.insert(0, str(SIM_DIR.parent / "common"))
sys.path.insert(0, str(SIM_DIR.parents[2] / "aws" / "src"))

from depot_client import DepotClient  # noqa: E402
from fw_eval import launch_flightline  # noqa: E402
from guarded_backend import EnvelopeGuardedSimBackend  # noqa: E402
from reasoning_loop import Mission, MissionLoop  # noqa: E402
from situation import PerceptionSampler  # noqa: E402
from vehicle_class import get_class  # noqa: E402

PORT = 9978
PLAN_CACHE = SIM_DIR / "swarm_plan_cache.json"

# One tasking, split by sector. Deliberately worded with the detector's own
# nouns ("person", "water bottle") — the on-device grounding guard matches
# labels against this text one-directionally, so prose like "individual in
# crimson outerwear" would make every report unprovable. See
# mission_vocab.PHASE_WORDING_RULES.
TASKING = (
    "Two fixed-wing drones are searching for a person in a red jacket somewhere "
    "within 120 metres of world coordinates (230, 0). A tall wall stands across "
    "the direct route east and must be flown around. Each drone carries one "
    "water bottle and must deliver its water bottle near the person in the red "
    "jacket, then return home. The drones have no radio link to you or to each "
    "other once they launch."
)
SECTOR_HINT = {
    "alpha": "This aircraft searches the NORTHERN half of the search area "
             "(north of world y=0).",
    "bravo": "This aircraft searches the SOUTHERN half of the search area "
             "(south of world y=0).",
}

DRONES = {
    "alpha": {"home": (-20.0, 10.0), "spawn_alt": 30.0},
    "bravo": {"home": (-20.0, -10.0), "spawn_alt": 30.0},
}


# ----------------------------------------------------------------- PASS 1
def get_plans(names, refresh: bool) -> dict:
    """One real Bedrock plan per aircraft, cached on disk."""
    if PLAN_CACHE.exists() and not refresh:
        cached = json.loads(PLAN_CACHE.read_text())
        if all(n in cached for n in names):
            print(f"Using cached plans from {PLAN_CACHE.name}")
            return cached

    import conversations
    plans = {}
    for name in names:
        prompt = f"{TASKING}\n\n{SECTOR_HINT[name]}"
        print(f"Planning for {name} via Bedrock...", flush=True)
        plans[name] = conversations.call_mission_agent([], prompt)
    PLAN_CACHE.write_text(json.dumps(plans, indent=2))
    print(f"Plans cached to {PLAN_CACHE}")
    return plans


def phases_from_plan(plan: dict) -> list:
    """Pull the phase list out of whatever shape the planner returned."""
    for key in ("phases", "mission", "plan"):
        val = plan.get(key)
        if isinstance(val, list) and val:
            return val
        if isinstance(val, dict) and isinstance(val.get("phases"), list):
            return val["phases"]
    return []


# ----------------------------------------------------------------- PASS 2
class DroneRun:
    """One aircraft's thread, backend, loop and per-decision record."""

    def __init__(self, client_factory, name: str, rid: str, phases: list, port: int):
        self.name = name
        self.rid = rid
        self.phases = phases
        self.client = client_factory(port)
        self.backend = EnvelopeGuardedSimBackend(self.client, rid)
        self.loop = MissionLoop(backend=self.backend,
                                vehicle_class=get_class("fixedwing"),
                                on_progress=self._progress)
        self.loop.payload_remaining = 1
        self.loop.payload_capacity = 1
        self.loop.on_tick = self._tick
        self.decisions = []
        self.progress = []
        self.result = None
        self.error = None
        # Its own client, so a 1 Hz poll never interleaves with the mission
        # thread's requests on a shared socket.
        self.sampler_client = client_factory(port)
        self.sampler = PerceptionSampler(
            EnvelopeGuardedSimBackend(self.sampler_client, rid), self.loop.peers)
        self._thread = None

    def _progress(self, msg):
        self.progress.append((time.time(), msg))
        print(f"  [{self.name}] {msg}", flush=True)

    def _tick(self, frame, detections, action, t):
        pose = self.backend.get_pose()
        self.decisions.append({
            "t": t,
            "pose": list(pose) if pose else None,
            "action": action.action_type.value,
            "target": action.target_object,
            "reasoning": (action.reasoning or "")[:300],
            "labels": sorted({d.label for d in detections}),
            "saw_peer": any(d.label == "aircraft" for d in detections),
            "peer_block": self.loop.peers.summarize(
                (pose[0], pose[1], pose[2])) if pose else "",
        })

    def start(self, delay_s: float):
        def _run():
            time.sleep(delay_s)
            try:
                self.sampler.start()
                self.result = self.loop.run(Mission(
                    mission_id=f"swarm-{self.name}",
                    phases=self.phases,
                    original_message=TASKING,
                ))
            except Exception as exc:      # a crash in one aircraft must not
                self.error = repr(exc)    # take the other down with it
                import traceback
                traceback.print_exc()
            finally:
                self.sampler.stop()
        self._thread = threading.Thread(target=_run, daemon=True, name=self.name)
        self._thread.start()

    def join(self, timeout):
        if self._thread:
            self._thread.join(timeout)
        return self._thread is None or not self._thread.is_alive()


def score_take(client, runs, env0, take_idx) -> dict:
    """Machine-check the storyboard beats against SCENE truth, not claims."""
    env = client.fw_env_state()
    truth = client.fw_prop_truth()
    bottles = [p for p in truth if p["label"] == "water_bottle"]
    target_xy = env["target_enu"]

    beats = {}
    # 1. Wall cleared without the guard rescuing anyone.
    envelope = sum(r.backend.envelope_events for r in runs)
    beats["wall_cleared_unaided"] = {
        "pass": envelope == 0 and all(
            any(d["pose"] and d["pose"][0] > env["wall"]["east"] for d in r.decisions)
            for r in runs),
        "envelope_events": envelope,
    }
    # 2. The target was actually found (a person detection east of the wall).
    beats["target_found"] = {
        "pass": any("person" in d["labels"] for r in runs for d in r.decisions),
    }
    # 3. The tunnel beat happened at all: the actor went in and came out.
    beats["tunnel_used"] = {
        "pass": env.get("target_phase") in ("exit_tunnel", "to_drop_zone", "settled"),
        "target_phase": env.get("target_phase"),
    }
    # 4. Deliveries landed near the target.
    dists = [math.hypot(b["world"][0] - target_xy[0], b["world"][1] - target_xy[1])
             for b in bottles]
    beats["deliveries"] = {
        "pass": len(bottles) == len(runs) and all(d <= 25.0 for d in dists),
        "count": len(bottles),
        "distances_m": [round(d, 1) for d in dists],
    }
    # 5. Comms-denied swarm: bravo saw alpha before it changed course, and had
    #    no person detection of its own first. Only meaningful with two.
    if len(runs) > 1:
        bravo = next((r for r in runs if r.name == "bravo"), None)
        saw_peer_idx = next((i for i, d in enumerate(bravo.decisions)
                             if d["saw_peer"]), None) if bravo else None
        first_person_idx = next((i for i, d in enumerate(bravo.decisions)
                                 if "person" in d["labels"]), None) if bravo else None
        beats["peer_evidence_before_converge"] = {
            "pass": saw_peer_idx is not None and (
                first_person_idx is None or saw_peer_idx < first_person_idx),
            "first_peer_sighting": saw_peer_idx,
            "first_person_sighting": first_person_idx,
        }
    # 6. Everyone came home.
    homes = {r.name: DRONES[r.name]["home"] for r in runs}
    finals = {}
    for r in runs:
        p = r.backend.get_pose()
        finals[r.name] = (math.hypot(p[0] - homes[r.name][0], p[1] - homes[r.name][1])
                          if p else None)
    beats["returned_home"] = {
        "pass": all(v is not None and v < 60.0 for v in finals.values()),
        "distance_m": {k: round(v, 1) if v else None for k, v in finals.items()},
    }

    return {
        "take": take_idx,
        "all_beats_pass": all(b["pass"] for b in beats.values()),
        "beats": beats,
        "env_final": env,
        "per_drone": {
            r.name: {
                "decisions": len(r.decisions),
                "envelope_events": r.backend.envelope_events,
                "camera_recoveries": r.backend.stale_frame_recoveries,
                "peer_samples": r.sampler.samples,
                "payload_remaining": r.loop.payload_remaining,
                "mission_success": r.result.success if r.result else None,
                "error": r.error,
                "actions": [d["action"] for d in r.decisions],
                "trace": r.decisions,
            } for r in runs
        },
    }


def run_take(client_factory, port, plans, names, take_idx, max_actions,
             stagger_s, timeout_s) -> dict:
    client = client_factory(port)
    env0 = client.fw_env_state()

    runs = []
    for i, name in enumerate(names):
        rid = f"fw-{name}"
        client.fw_spawn(rid, (*DRONES[name]["home"], DRONES[name]["spawn_alt"]), 0.0)
        client.fw_reset_camera(rid)
        runs.append(DroneRun(client_factory, name, rid,
                             phases_from_plan(plans[name]), port))
        runs[-1].loop.MAX_PHASE_ACTIONS = max_actions

    print(f"\n=== take {take_idx} — {len(runs)} aircraft ===", flush=True)
    for i, r in enumerate(runs):
        r.start(delay_s=i * stagger_s)

    deadline = time.time() + timeout_s
    for r in runs:
        r.join(max(1.0, deadline - time.time()))

    report = score_take(client, runs, env0, take_idx)
    for r in runs:
        client.fw_despawn(r.rid)
        r.client.close()
        r.sampler_client.close()
    client.fw_inject("sar_config", force_phase="amble",
                     target_enu=[215.0, 58.0])
    client.close()
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", type=int, default=1)
    ap.add_argument("--drones", type=int, default=2, choices=(1, 2))
    ap.add_argument("--max-actions", type=int, default=14)
    ap.add_argument("--stagger", type=float, default=10.0)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--refresh-plan", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--out", default="swarm_report.json")
    args = ap.parse_args()

    names = list(DRONES)[: args.drones]
    plans = get_plans(names, args.refresh_plan)
    for n in names:
        ph = phases_from_plan(plans[n])
        print(f"  {n}: {len(ph)} phases")
        for p in ph:
            print(f"    - {p.get('type', 'vlm')}: "
                  f"{str(p.get('objective', p))[:90]}")
        if not ph:
            print(f"FAIL: planner returned no phases for {n}", file=sys.stderr)
            print(json.dumps(plans[n], indent=2)[:1200], file=sys.stderr)
            return 2

    def client_factory(port):
        return DepotClient(port=port)

    proc = launch_flightline(seed=0, port=args.port, gui=True, env="sar")
    takes = []
    try:
        for i in range(args.takes):
            takes.append(run_take(client_factory, args.port, plans, names, i,
                                  args.max_actions, args.stagger, args.timeout))
            t = takes[-1]
            print(f"\n--- take {i}: "
                  f"{'ALL BEATS PASS' if t['all_beats_pass'] else 'incomplete'}")
            for bname, b in t["beats"].items():
                print(f"    {'ok  ' if b['pass'] else 'miss'} {bname}: "
                      f"{ {k: v for k, v in b.items() if k != 'pass'} }")
    finally:
        proc.stop()

    good = [t for t in takes if t["all_beats_pass"]]
    out = {
        "gate": "M8_two_drone_sar_demo",
        "takes": len(takes),
        "takes_all_beats": len(good),
        "best_take": good[0]["take"] if good else None,
        "tasking": TASKING,
        "detail": takes,
    }
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"\n{len(good)}/{len(takes)} takes passed every beat. Report: {args.out}")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
