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
# parents[1] is the eco submodule root. parents[2] is the PARENT repo, which
# has no aws/src — the planner import then failed at the one moment it is used,
# after the Bedrock call had already been set up.
sys.path.insert(0, str(SIM_DIR.parents[1] / "aws" / "src"))

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
# Describes the MISSION, not the terrain — deliberately.
#
# This used to add "a tall wall stands across the direct route east and must be
# flown around", and the planner did exactly what it was told: it returned typed
# `nav` waypoints at north_m=80 described as "fly north to clear the tall wall".
# Typed nav phases are executed by deterministic code, so the aircraft rounded
# the wall on a cloud-computed route with the on-device model contributing
# nothing. The video's claim that each drone sees the wall and routes around it
# from its own camera would then have been narrated over footage of a
# pre-planned turn, and the whole of M6 would have been irrelevant to the take
# that actually got filmed.
#
# An operator tasking a drone states the objective; they do not brief the
# terrain. Leaving the wall out is what makes the on-device avoidance claim
# true, and it is the aircraft's job to discover it on camera.
TASKING = (
    "Two fixed-wing drones are searching for a person in a red jacket somewhere "
    "within 120 metres of world coordinates east=400, north=0 (metres, local "
    "frame, not GPS). Each drone carries one "
    "water bottle and must deliver its water bottle near the person in the red "
    "jacket, then return home. The drones have no radio link to you or to each "
    "other once they launch."
)
# A sector is how the work is DIVIDED, not a fence.
#
# Worded as a plain assignment ("this aircraft searches the SOUTHERN half"), the
# planner turns it into a phase objective that reads as a boundary, and the
# aircraft obeys it over everything else. Measured directly: shown a teammate
# descending and circling low over one spot — the exact cue the comms-denied
# doctrine names — the model converged 0 times in 16, reasoning "the drone is
# currently in the southern half and needs to search for a person in a red
# jacket". A specific instruction beats general doctrine every time, and the
# specific instruction was one nobody meant literally: an operator dividing a
# search area does not mean "stay there even if your teammate has found them".
#
# This says what the operator actually wants and still leaves the decision open
# — nothing here tells an aircraft to converge, or when.
SECTOR_HINT = {
    "alpha": "To avoid both aircraft covering the same ground, start your search "
             "in the NORTHERN half of the search area (north of world y=0). That "
             "is a division of effort, not a boundary — go wherever the mission "
             "requires.",
    "bravo": "To avoid both aircraft covering the same ground, start your search "
             "in the SOUTHERN half of the search area (south of world y=0). That "
             "is a division of effort, not a boundary — go wherever the mission "
             "requires.",
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


# A fixed camera looking at the drop zone, so the delivery has a shot with the
# PERSON in it.
#
# Sampled across a whole take, the person in the red jacket appears in 0 of 247
# chase frames: the chase rig rides 9 m behind the aircraft, and from there a
# 1.7 m figure fifty metres out is sub-pixel. Every hero shot in the cut is
# therefore of an aircraft over empty ground, in a film about finding someone.
#
# This one does not track the aircraft. It sits off the drop zone at head height
# and looks at it, the way a camera on a tripod would, so whatever flies through
# is framed against the person rather than against grass.
# Distances chosen from the arithmetic, then checked against a rendered frame.
# At the first attempt's 64 m a 1.7 m figure is 20 px — still a speck, just a
# nearer one. 25 m puts the person at ~52 px and the aircraft at ~108 px, which
# is the difference between "someone is there" and "a person in a red jacket".
DROP_ZONE_CAM = {
    "name": "hero_dropzone",
    "eye": (484.0, 6.0, 4.5),      # ~25 m out, head height, east of the zone
    "look": (458.0, 20.0, 3.0),    # the run-in crosses the frame toward camera
}


class FixedCamRecorder:
    """A camera on a tripod. Placed once, never moved, grabbed on a timer.

    ChaseCamRecorder re-aims every tick to keep the aircraft framed, which is
    what makes its footage useless for showing anything ON THE GROUND — the
    subject is never in it. This one is pointed at a place and left there, so
    the aircraft flies through a shot that already contains the person.
    """

    def __init__(self, client, out_dir: Path, eye, look, name: str,
                 width: int = 1920, height: int = 1080, fps: float = 6.0):
        self.client = client
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.eye, self.look = eye, look
        # Unique per instance for the same reason ChaseCamRecorder's is: reusing
        # a vantage name across runs in one Godot session returns stale frames.
        self.name = f"{name}_{int(time.time())}"
        self.width, self.height, self.fps = width, height, fps
        self._stop = threading.Event()
        self._thread = None
        self._i = 0

    def _loop(self):
        self.client.add_vantage(self.name, self.eye, self.look,
                                w=self.width, h=self.height)
        interval = 1.0 / self.fps
        while not self._stop.wait(interval):
            try:
                jpg = self.client.grab_vantage(self.name)
                if jpg:
                    (self.out_dir / f"f{self._i:05d}.jpg").write_bytes(jpg)
                    self._i += 1
            except Exception:
                continue

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        try:
            self.client.remove_vantage(self.name)
        except Exception:
            pass
        return self._i


class OrbitCamRecorder:
    """A camera that circles the aircraft instead of trailing it.

    Every shot so far comes from one of two rigs: a chase locked behind the
    aircraft, or a tripod. Both are static relationships, so cutting between
    them yields two views of the same thing rather than coverage. An orbit
    moves the camera independently of the subject, which is what makes a reveal
    possible — the scene rotates behind the aircraft and the geography reads.
    """

    def __init__(self, client, backend, out_dir: Path, name: str,
                 radius_m: float = 34.0, height_m: float = 12.0,
                 period_s: float = 14.0, width: int = 1920, height: int = 1080,
                 fps: float = 6.0):
        self.client, self.backend = client, backend
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.name = f"{name}_{int(time.time())}"
        self.radius_m, self.height_m, self.period_s = radius_m, height_m, period_s
        self.width, self.height, self.fps = width, height, fps
        self._stop = threading.Event()
        self._thread = None
        self._i = 0

    def _loop(self):
        self.client.add_vantage(self.name, (0.0, -30.0, 20.0), (0.0, 0.0, 5.0),
                                w=self.width, h=self.height)
        interval = 1.0 / self.fps
        t0 = time.monotonic()
        while not self._stop.wait(interval):
            try:
                pose = self.backend.get_pose()
                if pose is None:
                    continue
                x, y, z = pose[0], pose[1], pose[2]
                ang = 2 * math.pi * ((time.monotonic() - t0) / self.period_s)
                cam = (x + self.radius_m * math.cos(ang),
                       y + self.radius_m * math.sin(ang),
                       max(4.0, z + self.height_m))
                self.client.move_vantage(self.name, cam, (x, y, z))
                jpg = self.client.grab_vantage(self.name)
                if jpg:
                    (self.out_dir / f"f{self._i:05d}.jpg").write_bytes(jpg)
                    self._i += 1
            except Exception:
                continue

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        try:
            self.client.remove_vantage(self.name)
        except Exception:
            pass
        return self._i


# Fixed cameras placed around the scene, so the cut has coverage rather than
# one relationship filmed twice. Positions are scene geography, not guesses:
# the wall is at east 250 spanning +-70, the tunnel at east 420-438, the drop
# zone at (462, 18).
SCENE_CAMS = [
    # Establishing: high and back, the whole run-in from home to the wall.
    {"name": "wide", "eye": (60.0, -300.0, 190.0), "look": (300.0, 20.0, 0.0)},
    # Ground level at the wall's south end, looking along the face. The wall is
    # 50 m tall and reads as a slab from the air; from underneath it has scale.
    # Back and off to the south-west, so the wall presents its FACE across the
    # frame and an aircraft rounding the south end crosses between camera and
    # wall. Placed close and edge-on it read as a monolith, not a barrier, and
    # a tree filled a third of the shot.
    # Inside the corridor the scatter deliberately keeps clear (|north| < 95 for
    # x in -60..320), close enough that an aircraft rounding the south end is a
    # recognisable aircraft rather than a speck. Further back the tree line
    # masked the wall's base and the subject was 20 px.
    {"name": "wall_low", "eye": (214.0, -88.0, 5.0), "look": (254.0, -44.0, 26.0)},
    # The drop zone, framed so a person and an aircraft fit in one shot.
    {"name": "hero", "eye": (484.0, 6.0, 4.5), "look": (458.0, 20.0, 3.0)},
]


# ----------------------------------------------------------------- PASS 2
class DroneRun:
    """One aircraft's thread, backend, loop and per-decision record."""

    def __init__(self, client_factory, name: str, rid: str, phases: list, port: int,
                 record_dir: Path = None, take: int = 0):
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

        # Optional recording. The chase camera and the frames the MODEL saw are
        # captured separately and both kept: a chase view is what makes the
        # flight legible on screen, but it is not what the aircraft was looking
        # at, and the demo's whole claim is about the latter.
        self.recorder = None
        self.tick_dir = None
        if record_dir is not None:
            from fw_count_eval import ChaseCamRecorder
            self.rec_client = client_factory(port)
            self.recorder = ChaseCamRecorder(
                self.rec_client,
                EnvelopeGuardedSimBackend(self.rec_client, rid),
                record_dir / f"chase_{name}",
                # Unique per take AND per aircraft: reusing a vantage name
                # across runs returns a frozen frame (a documented Godot
                # render-target reuse bug, hit for real in S2).
                vantage_name=f"chase_{name}_t{take}_{int(time.time())}",
                width=1920, height=1080, burn_overlay=False, fps=6.0,
            )
            self.tick_dir = record_dir / f"onboard_{name}"
            self.tick_dir.mkdir(parents=True, exist_ok=True)

    def _progress(self, msg):
        self.progress.append((time.time(), msg))
        if self.recorder is not None:
            self.recorder.on_progress(msg)   # captions come from the real run
        # Never let a log line end a flight. stdout went away under a long
        # background batch and the resulting OSError unwound through the phase,
        # the mission and all four takes. The record of what happened lives in
        # self.progress and the report either way.
        try:
            print(f"  [{self.name}] {msg}", flush=True)
        except Exception:
            pass

    def _tick(self, frame, detections, action, t):
        pose = self.backend.get_pose()
        if self.tick_dir is not None and frame:
            idx = len(self.decisions)
            (self.tick_dir / f"{idx:04d}.jpg").write_bytes(frame)
            (self.tick_dir / f"{idx:04d}.json").write_text(json.dumps({
                "t": t, "pose": list(pose) if pose else None,
                "action": action.to_dict(),
                "detections": [{"label": d.label, "score": d.score,
                                "world": list(d.world_xyz) if d.world_xyz else None}
                               for d in detections],
            }, indent=2))
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
                if self.recorder is not None:
                    self.recorder.start()
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
                if self.recorder is not None:
                    self.recorder.stop()
        self._thread = threading.Thread(target=_run, daemon=True, name=self.name)
        self._thread.start()

    def join(self, timeout):
        if self._thread:
            self._thread.join(timeout)
        return self._thread is None or not self._thread.is_alive()


def score_take(client, runs, env0, take_idx, bottles_before=()) -> dict:
    """Machine-check the storyboard beats against SCENE truth, not claims."""
    env = client.fw_env_state()
    truth = client.fw_prop_truth()
    # Only bottles dropped in THIS take. Nothing resets the scene between takes,
    # so a delivered bottle stays on the ground and every later take was scored
    # against it: two consecutive takes reported an identical 106.3 m, one of
    # which had not delivered at all and was being judged on its predecessor's
    # bottle.
    before = set(bottles_before)
    bottles = [p for p in truth if p["label"] == "water_bottle"
               and (round(p["world"][0], 1), round(p["world"][1], 1)) not in before]
    target_xy = env["target_enu"]

    # Where each delivery was AIMED, logged by the drop handler at release.
    aims = []
    for r in runs:
        try:
            aims += [e["data"]["target_xy"] for e in client.fw_events(r.rid)
                     if e.get("kind") == "delivery_aim"]
        except Exception:
            pass

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
    # Measure each bottle against the point it was AIMED at, falling back to the
    # target's current position only if no aim was logged. The target walks to a
    # drop zone after the tunnel, so scoring against where he ended up measured
    # his walk rather than the aircraft's aim: a release made 24 m from him
    # scored 106 m because he then covered ninety of them on foot.
    def _delivery_error(b):
        bx, by = b["world"][0], b["world"][1]
        candidates = aims or [target_xy]
        return min(math.hypot(bx - a[0], by - a[1]) for a in candidates)

    dists = [_delivery_error(b) for b in bottles]
    beats["deliveries"] = {
        "pass": len(bottles) == len(runs) and all(d <= 25.0 for d in dists),
        "count": len(bottles),
        "distances_m": [round(d, 1) for d in dists],
        "scored_against": "aim point at release" if aims else "target position now",
        "walked_after_release_m": (
            round(min(math.hypot(a[0] - target_xy[0], a[1] - target_xy[1])
                      for a in aims), 1) if aims else None),
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


def _write_report(out_path, takes) -> None:
    """Persist what has been measured so far. Called after every take."""
    good = [t for t in takes if t["all_beats_pass"]]
    Path(out_path).write_text(json.dumps({
        "gate": "M8_two_drone_sar_demo",
        "takes": len(takes),
        "takes_all_beats": len(good),
        "best_take": good[0]["take"] if good else None,
        "tasking": TASKING,
        "detail": takes,
    }, indent=2))


def run_take(client_factory, port, plans, names, take_idx, max_actions,
             stagger_s, timeout_s, record_dir=None) -> dict:
    client = client_factory(port)
    env0 = client.fw_env_state()
    # Bottles already on the ground before this take starts — see score_take.
    bottles_before = [(round(p["world"][0], 1), round(p["world"][1], 1))
                      for p in client.fw_prop_truth() if p["label"] == "water_bottle"]

    runs = []
    for i, name in enumerate(names):
        rid = f"fw-{name}"
        client.fw_spawn(rid, (*DRONES[name]["home"], DRONES[name]["spawn_alt"]), 0.0)
        client.fw_reset_camera(rid)
        runs.append(DroneRun(client_factory, name, rid,
                             phases_from_plan(plans[name]), port,
                             record_dir=record_dir, take=take_idx))
        runs[-1].loop.MAX_PHASE_ACTIONS = max_actions

    # Full camera coverage: three fixed positions around the scene plus an
    # orbit on the lead aircraft. One chase offset filmed for a whole mission
    # is a screen recording; coverage is what lets an edit cut.
    scene_cams = []
    if record_dir is not None:
        base = Path(record_dir) / f"take_{take_idx:02d}"
        for spec in SCENE_CAMS:
            scene_cams.append(FixedCamRecorder(
                client_factory(port), base / spec["name"],
                spec["eye"], spec["look"], spec["name"]))
        if runs:
            oc = client_factory(port)
            scene_cams.append(OrbitCamRecorder(
                oc, EnvelopeGuardedSimBackend(oc, runs[0].rid),
                base / "orbit", "orbit"))
        for c in scene_cams:
            c.start()

    print(f"\n=== take {take_idx} — {len(runs)} aircraft ===", flush=True)
    for i, r in enumerate(runs):
        r.start(delay_s=i * stagger_s)

    deadline = time.time() + timeout_s
    # A drone that overruns the deadline must be STOPPED before the take is
    # scored, not left flying. join()'s return value was being discarded, so
    # score_take() ran against aircraft that were still moving: one drone was
    # recorded as having made 6 decisions and failed its search when it had
    # actually been cut off mid-run, and its thread carried on printing into a
    # harness whose clients were already closed.
    timed_out = []
    for r in runs:
        if not r.join(max(1.0, deadline - time.time())):
            r.loop.abort()
            timed_out.append(r.name)
    for r in runs:
        if not r.join(30.0):
            print(f"  [{r.name}] did not stop after abort", flush=True)

    for c in scene_cams:
        try:
            print(f"  cam {c.out_dir.name}: {c.stop()} frames", flush=True)
        except Exception:
            pass

    report = score_take(client, runs, env0, take_idx, bottles_before)
    report["timed_out"] = timed_out
    if timed_out:
        # A truncated take cannot be evidence for or against any beat.
        report["all_beats_pass"] = False
        report["truncated"] = True
        print(f"  TAKE TRUNCATED — {', '.join(timed_out)} hit the time limit; "
              f"beat results below are not evidence", flush=True)
    for r in runs:
        client.fw_despawn(r.rid)
        r.client.close()
        r.sampler_client.close()
        if r.recorder is not None:
            r.rec_client.close()
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
    ap.add_argument("--record", default=None,
                    help="directory to record chase cams and onboard frames into")
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
            take_dir = (Path(args.record) / f"take_{i:02d}") if args.record else None
            takes.append(run_take(client_factory, args.port, plans, names, i,
                                  args.max_actions, args.stagger, args.timeout,
                                  record_dir=take_dir))
            t = takes[-1]
            # Write the report after EVERY take, not only when all of them
            # finish. llama.cpp's Metal backend aborts on teardown
            # (GGML_ASSERT rsets->data count == 0, upstream PR 17869), and when
            # it did mid-batch it took the scores of two completed takes with
            # it — the footage was on disk and the numbers describing it were
            # not. A batch's value should not be all-or-nothing.
            _write_report(args.out, takes)
            print(f"\n--- take {i}: "
                  f"{'ALL BEATS PASS' if t['all_beats_pass'] else 'incomplete'}")
            for bname, b in t["beats"].items():
                print(f"    {'ok  ' if b['pass'] else 'miss'} {bname}: "
                      f"{ {k: v for k, v in b.items() if k != 'pass'} }")
    finally:
        proc.stop()

    good = [t for t in takes if t["all_beats_pass"]]
    _write_report(args.out, takes)
    print(f"\n{len(good)}/{len(takes)} takes passed every beat. Report: {args.out}")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
