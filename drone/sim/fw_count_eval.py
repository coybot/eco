#!/usr/bin/env python3
"""Milestone S2 — the sim-verification gate for the fixed-wing "orbit and
count" capability (perception + memory + reasoning + obstacle avoidance),
per the fixed-wing autonomy plan (no-again-not-for-compiled-quiche.md).

Two passes, run for real (not mocked):

PASS 1 — cloud decomposition breadth (fast, cheap real Bedrock calls).
For every phrase in PHRASES, calls the REAL aws/src/conversations.py
call_mission_agent() (real claude-sonnet-4-6 via Bedrock, the actual
MISSION_SYSTEM_PROMPT built from mission_vocab.py — changes B/C/E's cloud
side) and asserts the response is well-formed for THIS fixed-wing: only
phase types this dispatcher accepts (mission_vocab.PHASE_SCHEMAS), no
fields that would silently no-op, and that the deliberately out-of-envelope
phrase gets declined/asked rather than planned as an impossible mission.

PASS 2 — full real pipeline, deep proof, for a representative subset
(not all 7 phrases — an honest scoping choice: the real 8B VLM is several
seconds per decision, so a full run of all 7 would take 30-40+ minutes).
For each of PHRASES_FOR_SIM, runs the decomposed mission through the REAL
MissionLoop (drone/common/reasoning_loop.py) on a REAL SimBackend against
the LIVE countdemo Godot env (Milestone S1), with the REAL on-device VLM
(Qwen3-VL-8B, real GGUF weights — same model MissionLoop uses by default in
production) doing every perception/reasoning decision. Asserts the reported
count against countdemo's real ground truth (fw_prop_truth()).

Requires: AWS_PROFILE=astral (real Bedrock access), the astral venv (real
llama_cpp + boto3), and gui=True Godot (fw_grab_frame needs a real rendering
driver — headless Godot's dummy driver leaves frames blank, an established
finding from fw_vlm_smoketest.py).

Usage:
    AWS_PROFILE=astral AWS_REGION=us-east-1 \
        /Users/jsaib/.astral-venv/bin/python3 fw_count_eval.py
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
sys.path.insert(0, str(COMMON_DIR))
ROVER_SIM_DIR = Path(__file__).resolve().parents[2] / "rover" / "sim"
sys.path.insert(0, str(ROVER_SIM_DIR))
AWS_SRC_DIR = Path(__file__).resolve().parents[2] / "aws" / "src"
sys.path.insert(0, str(AWS_SRC_DIR))

from backends import SimBackend  # noqa: E402
from vehicle_class import get_class  # noqa: E402
from reasoning_loop import Mission, MissionLoop  # noqa: E402
from depot_client import DepotClient  # noqa: E402
import mission_vocab  # noqa: E402

from fw_eval import find_godot, GodotProcess, GODOT_PROJECT, READY_TIMEOUT, _chase_cam_pose  # noqa: E402

RID = "fw-count-eval-1"
START_POS = (0.0, 0.0, 30.0)  # above the 18m treeline from the very start
CHASE_VANTAGE = "s2_chase_cam"
VIDEO_FPS = 8


class ChaseCamRecorder:
    """Runs a chase-cam capture loop in a background thread while
    MissionLoop.run() blocks the main thread — reuses fw_eval.py's PROVEN
    _chase_cam_pose() (9m back/4m side/3m up: close enough that a real
    Skywalker-X8-scale airframe reads as a recognizable delta-wing shape, not
    a barely-visible dot — the earlier 35m/40m-total-distance attempt was
    checked and rejected for exactly that reason, see _chase_cam_pose's own
    docstring). Captions come from MissionLoop's own on_progress callback —
    the SAME real progress strings already printed to console — so the video
    shows what the mission is actually doing at each moment, not just a
    silent flythrough.
    """

    def __init__(self, client, backend, out_dir: Path, vantage_name: str = CHASE_VANTAGE,
                 width: int = 1280, height: int = 720, fps: float = 3.0,
                 burn_overlay: bool = True):
        self.client = client
        self.backend = backend
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        # Capture resolution and sample rate are parameters so the showcase
        # recorders can ask for 1080p at a higher rate while existing callers
        # keep the proven 720p defaults.
        self.width = width
        self.height = height
        self.sample_hz = fps
        # Burn the caption + pose box into the frame, or leave it clean.
        #
        # For the showcase cut it must be OFF: fw_edit lays down its own typeset
        # captions, and this one sits underneath them as a black debug box
        # reading "pos=(66,-0,35)" — the single most amateur thing in frame.
        # It stays on by default so existing callers, which rely on it as their
        # only caption, are unaffected.
        self.burn_overlay = burn_overlay
        # MUST be unique per recorder instance, not the shared CHASE_VANTAGE
        # default — confirmed live (checked the actual rendered frames, not
        # just the code): running two missions back-to-back in the same
        # Godot session while both reuse the same vantage name produces a
        # real bug where the SECOND mission's grab_vantage() returns a
        # frozen, stale frame instead of a live render (identical background
        # terrain across frames despite wildly different real aircraft
        # positions in the caption text) — a Godot-side vantage/render-target
        # reuse issue, not something worth chasing further when a unique name
        # per recorder sidesteps it entirely.
        self.vantage_name = vantage_name
        self._stop = threading.Event()
        self._thread = None
        self._frame_i = 0
        self._latest_caption = "(mission starting)"
        self._lock = threading.Lock()

    def on_progress(self, message: str) -> None:
        with self._lock:
            self._latest_caption = message
        print(message)  # preserve existing console visibility

    # Consecutive identical grabs before recreating the vantage (see
    # _recover_stuck_vantage's docstring) — 5 at the ~3Hz sample rate below
    # is ~1.6s of genuinely frozen render before acting, long enough that a
    # real momentary duplicate (aircraft briefly motionless, e.g. right after
    # spawn) doesn't falsely trigger a recreate.
    _STUCK_FRAME_THRESHOLD = 5

    def _recover_stuck_vantage(self, cam_pos, look_at):
        """fleet_manager.gd's remove_vantage() docstring already documents
        this exact failure mode and its own fix: 'a SubViewport whose render
        target has silently stopped updating (get_texture().get_image() keeps
        returning the same bytes indefinitely, despite UPDATE_ALWAYS) ...
        confirmed intermittently for long-running headless captures ... a
        fresh SubViewport reliably resumes rendering.' Confirmed live in this
        project too: a ~170s chase-cam recording rendered one real frame
        then repeated it, byte-for-byte, for the entire rest of the mission
        (checked by extracting and viewing frames directly, not just
        inferring from code) — this recorder just never called the existing
        recovery. remove + re-add gets a genuinely fresh SubViewport/camera.
        """
        try:
            self.client.remove_vantage(self.vantage_name)
        except Exception:
            pass
        self.client.add_vantage(self.vantage_name, cam_pos, look_at)

    def _loop(self):
        from PIL import Image, ImageDraw, ImageFont
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
        except Exception:
            font = ImageFont.load_default()
        self.client.add_vantage(self.vantage_name, (0.0, -15.0, 15.0), (0.0, 0.0, 10.0),
                                self.width, self.height)
        interval = 1.0 / self.sample_hz  # ffmpeg re-times on assembly
        last_jpg = None
        stuck_count = 0
        while not self._stop.is_set():
            pose = self.backend.get_pose()
            if pose is not None:
                x, y, z, yaw = pose
                cam_pos, look_at = _chase_cam_pose(x, y, z, yaw)
                self.client.move_vantage(self.vantage_name, cam_pos, look_at)
                jpg = self.client.grab_vantage(self.vantage_name)
                if jpg:
                    if jpg == last_jpg:
                        stuck_count += 1
                        if stuck_count >= self._STUCK_FRAME_THRESHOLD:
                            self._recover_stuck_vantage(cam_pos, look_at)
                            stuck_count = 0
                            last_jpg = None
                            time.sleep(interval)
                            continue
                    else:
                        stuck_count = 0
                    last_jpg = jpg
                    img = Image.open(io.BytesIO(jpg)).convert("RGB")
                    if self.burn_overlay:
                        draw = ImageDraw.Draw(img)
                        with self._lock:
                            caption = self._latest_caption
                        lines = [caption[i:i + 70] for i in range(0, len(caption), 70)][:3]
                        lines.append(f"pos=({x:.0f},{y:.0f},{z:.0f})")
                        box_h = 24 * len(lines) + 12
                        draw.rectangle([0, 0, 760, box_h], fill=(0, 0, 0))
                        for li, line in enumerate(lines):
                            draw.text((8, 6 + li * 24), line, fill=(255, 255, 255), font=font)
                    img.save(self.out_dir / f"f{self._frame_i:05d}.jpg", quality=92)
                    self._frame_i += 1
            time.sleep(interval)

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        try:
            self.client.remove_vantage(self.vantage_name)
        except Exception:
            pass
        return self._frame_i

    def render(self, out_path: Path) -> bool:
        if self._frame_i == 0:
            print("  WARNING: no frames captured, nothing to render")
            return False
        cmd = [
            "ffmpeg", "-y", "-framerate", str(VIDEO_FPS),
            "-i", str(self.out_dir / "f%05d.jpg"),
            "-vf", "scale=1280:720", "-pix_fmt", "yuv420p", str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ffmpeg FAILED: {result.stderr[-500:]}")
            return False
        return True

# --- The command phrases (Milestone S2's "multiple phrasings", not one
# canonical string) --------------------------------------------------------
PHRASES = [
    {"phrase": "circle around and count the people then land", "target": "person", "expect_land": True},
    {"phrase": "how many cars are parked down there? come back and tell me", "target": "car", "expect_land": False},
    {"phrase": "fly over the trees and count the people on the grass", "target": "person", "expect_land": False},
    {"phrase": "survey the field and report what you see", "target": None, "expect_land": False},
    {"phrase": "go count the vehicles by the lot, then land", "target": "car", "expect_land": True},
    {"phrase": "find the people and orbit them", "target": "person", "expect_land": False},
    {"phrase": "land on my hand", "target": None, "expect_land": False, "out_of_envelope": True},
]

# Representative subset actually flown through the full real sim+VLM
# pipeline (Pass 2) — one static/exact-match target (cars) and one
# moving/honest-uncertainty target (people). See module docstring for why
# this is a deliberate scoping choice, not all 7.
PHRASES_FOR_SIM = [PHRASES[1], PHRASES[0]]


def call_real_planner(phrase: str) -> dict:
    """Real Bedrock call via aws/src/conversations.py — the actual cloud
    planner code path a real app command triggers, not a reimplementation."""
    import conversations
    return conversations.call_mission_agent([], phrase)


def check_decomposition(phrase_spec: dict, response: dict) -> list[str]:
    """Returns a list of problems (empty = passed). Checks the response is
    well-formed for what THIS fixed-wing dispatcher actually accepts (change
    B's drift-checked vocabulary) — not that the LLM chose any particular
    exact phase sequence, which would be over-fitting a test to one
    generative model's phrasing of a valid plan."""
    problems = []
    action = response.get("action")

    if phrase_spec.get("out_of_envelope"):
        if action not in ("ask", "respond"):
            problems.append(f"expected ask/respond for an out-of-envelope command, got action={action!r}")
        return problems

    if action not in ("mission", "ask"):
        problems.append(f"unexpected action={action!r}")
        return problems
    if action == "ask":
        # A legitimate command being asked-about isn't necessarily wrong
        # (the mission might genuinely be ambiguous) but is worth flagging
        # for manual review rather than silently treated as a pass.
        problems.append(f"NOTE (not necessarily a failure): planner asked instead of planning: {response.get('message')}")
        return problems

    phases = response.get("mission", {}).get("phases", [])
    if not phases:
        problems.append("mission action with empty phases")
        return problems

    valid_types = set(mission_vocab.PHASE_SCHEMAS.keys())
    for i, phase in enumerate(phases):
        ptype = phase.get("type")
        if ptype is not None and ptype not in valid_types:
            problems.append(f"phase {i} has type={ptype!r}, not in mission_vocab.PHASE_SCHEMAS "
                             f"{sorted(valid_types)} — would silently fail the drift-check "
                             f"assertion or be misdispatched on-device")
        # land/heading_deg, nav|go_to_gps/min_clearance_alt are the only new
        # fields from changes A/E — no schema violation possible on the
        # OTHER fields since _execute_phase reads with .get() defaults, but
        # worth confirming no unrecognized field name typo'd from the prompt.
        if ptype in ("nav", "go_to_gps"):
            unknown = set(phase.keys()) - {"type"} - set(mission_vocab.PHASE_SCHEMAS[ptype].fields.keys())
            if unknown:
                problems.append(f"phase {i} ({ptype}) has fields not in its schema: {unknown}")

    return problems


def run_sim_mission(client: DepotClient, response: dict, phrase_spec: dict,
                     ground_truth: list[dict], video_out_dir: Path) -> dict:
    """Runs the decomposed mission through the REAL MissionLoop against the
    live countdemo sim, with the real VLM, while a background ChaseCamRecorder
    captures a compelling (not a distant dot) video of the actual flight.
    Returns a result dict for the summary table, including the rendered
    video's path (or None if rendering failed/produced nothing)."""
    client.fw_despawn(RID)
    client.fw_spawn(RID, START_POS, 0.0)
    time.sleep(0.3)

    backend = SimBackend(client, RID)
    vc = get_class("fixedwing")

    frame_tmp = Path(tempfile.mkdtemp(prefix="s2_frames_"))
    # Unique per mission (see ChaseCamRecorder.__init__'s docstring for why
    # reusing one vantage name across missions in the same Godot session is
    # a confirmed real bug, not just theoretical caution).
    vantage_name = f"{CHASE_VANTAGE}_{abs(hash(phrase_spec['phrase'])) % 100000}"
    recorder = ChaseCamRecorder(client, backend, frame_tmp, vantage_name=vantage_name)

    loop = MissionLoop(backend=backend, vehicle_class=vc, on_progress=recorder.on_progress)
    loop.MAX_PHASE_ACTIONS = 20  # bounded — real inference is several sec/tick

    phases = response.get("mission", {}).get("phases", [])
    mission = Mission(
        mission_id=f"s2-{abs(hash(phrase_spec['phrase']))}",
        phases=phases,
        original_message=phrase_spec["phrase"],
    )

    recorder.start()
    t0 = time.time()
    try:
        result = loop.run(mission)
    finally:
        n_frames = recorder.stop()
    elapsed = time.time() - t0

    video_out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() else "_" for c in phrase_spec["phrase"])[:60]
    video_path = video_out_dir / f"{safe_name}.mp4"
    video_ok = recorder.render(video_path)
    print(f"  video: {n_frames} frames captured -> "
          f"{video_path if video_ok else '(rendering failed)'}")

    target = phrase_spec.get("target")
    truth_count = None
    reported_count = None
    if target:
        truth_count = sum(1 for p in ground_truth if p["label"] == target)
        for f in (result.findings or []):
            if f.lower().startswith("counted") and target in f.lower():
                # "Counted N <target>..." — pull the leading integer.
                try:
                    reported_count = int(f.split()[1])
                except (IndexError, ValueError):
                    pass

    return {
        "phrase": phrase_spec["phrase"],
        "success": result.success,
        "elapsed_s": round(elapsed, 1),
        "actions": result.actions_taken,
        "summary": result.summary,
        "findings": result.findings,
        "failure_reason": result.failure_reason,
        "target": target,
        "truth_count": truth_count,
        "reported_count": reported_count,
        "video_path": str(video_path) if video_ok else None,
        "n_frames": n_frames,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9994)
    ap.add_argument("--skip-sim", action="store_true",
                     help="Only run Pass 1 (cloud decomposition), skip the real sim+VLM pass")
    args = ap.parse_args()

    print(f"{'='*70}\nPASS 1 — cloud decomposition breadth ({len(PHRASES)} phrases, real Bedrock)\n{'='*70}")
    decomposition_results = []
    all_pass1_ok = True
    for spec in PHRASES:
        print(f"\n--- \"{spec['phrase']}\" ---")
        try:
            response = call_real_planner(spec["phrase"])
        except Exception as e:
            print(f"  ERROR calling planner: {e}")
            decomposition_results.append({"phrase": spec["phrase"], "error": str(e)})
            all_pass1_ok = False
            continue
        print(f"  action={response.get('action')!r}")
        if response.get("action") == "mission":
            phases = response["mission"]["phases"]
            print(f"  {len(phases)} phases: {[p.get('type', 'VLM') for p in phases]}")
        problems = check_decomposition(spec, response)
        hard_problems = [p for p in problems if not p.startswith("NOTE")]
        for p in problems:
            print(f"  {'PROBLEM' if not p.startswith('NOTE') else p[:4]}: {p}")
        if hard_problems:
            all_pass1_ok = False
        decomposition_results.append({"phrase": spec["phrase"], "response": response, "problems": problems})

    print(f"\n{'='*70}\nPASS 1 RESULT: {'ALL PASSED' if all_pass1_ok else 'SOME FAILED'}\n{'='*70}")

    if args.skip_sim:
        return 0 if all_pass1_ok else 1

    print(f"\n{'='*70}\nPASS 2 — full real sim+VLM pipeline ({len(PHRASES_FOR_SIM)} representative phrases)\n{'='*70}")
    video_out_dir = Path(__file__).resolve().parent / "s2_videos"
    godot_bin = find_godot()
    # No --headless: fw_grab_frame/grab_vantage need a real rendering driver
    # (headless Godot's dummy driver leaves frames blank — an established
    # finding from fw_vlm_smoketest.py) — needed both for the VLM's own
    # forward-camera view and this run's chase-cam video capture.
    args_list = [godot_bin, "--path", str(GODOT_PROJECT), "--",
                 "--fleet=", "--env=countdemo", f"--ipc-port={args.port}"]
    proc = subprocess.Popen(args_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ready = threading.Event()
    lines = []
    def _tee():
        for line in proc.stdout:
            lines.append(line)
            print(f"[godot] {line}", end="", flush=True)
            if "IPC ready" in line:
                ready.set()
        ready.set()
    threading.Thread(target=_tee, daemon=True).start()

    sim_results = []
    all_pass2_ok = True
    try:
        if not ready.wait(timeout=READY_TIMEOUT):
            raise RuntimeError("Godot did not become ready")
        time.sleep(0.5)
        client = DepotClient(port=args.port)
        ground_truth = client.fw_prop_truth()
        print(f"Ground truth: {len(ground_truth)} props "
              f"({sum(1 for p in ground_truth if p['label']=='person')} person, "
              f"{sum(1 for p in ground_truth if p['label']=='car')} car)")

        # Warm the VLM once — get_vlm_service() is a module-level singleton
        # (vlm.py), so every MissionLoop._get_vlm() call below reuses this
        # same loaded model instead of reloading ~5GB weights per phrase.
        print("\nLoading real VLM (Qwen3-VL-8B, ~5GB weights)...")
        from vlm import get_vlm_service
        t0 = time.time()
        vlm = get_vlm_service()
        print(f"VLM loaded in {time.time()-t0:.1f}s, available={vlm.is_available()}")

        for spec in PHRASES_FOR_SIM:
            print(f"\n--- SIM: \"{spec['phrase']}\" ---")
            dr = next((d for d in decomposition_results if d["phrase"] == spec["phrase"]), None)
            if dr is None or "response" not in dr or dr["response"].get("action") != "mission":
                print("  SKIPPED (no valid mission decomposition from Pass 1)")
                all_pass2_ok = False
                continue
            try:
                r = run_sim_mission(client, dr["response"], spec, ground_truth, video_out_dir)
            except Exception as e:
                print(f"  ERROR running mission: {e}")
                sim_results.append({"phrase": spec["phrase"], "error": str(e)})
                all_pass2_ok = False
                continue
            print(f"  success={r['success']} actions={r['actions']} elapsed={r['elapsed_s']}s")
            print(f"  summary: {r['summary']}")
            if r["target"]:
                print(f"  target={r['target']!r} truth_count={r['truth_count']} reported_count={r['reported_count']}")
                if r["reported_count"] is None:
                    print("  PROBLEM: no count was reported for a counting mission")
                    all_pass2_ok = False
                elif r["target"] == "car" and r["reported_count"] != r["truth_count"]:
                    print(f"  PROBLEM: car count should be exact — expected {r['truth_count']}, got {r['reported_count']}")
                    all_pass2_ok = False
                elif r["target"] == "person" and abs(r["reported_count"] - r["truth_count"]) > 2:
                    print(f"  PROBLEM: person count too far off truth ({r['truth_count']}) even allowing for movement uncertainty: {r['reported_count']}")
                    all_pass2_ok = False
            sim_results.append(r)

        client.fw_despawn(RID)
        client.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"\n{'='*70}\nPASS 2 RESULT: {'ALL PASSED' if all_pass2_ok else 'SOME FAILED'}\n{'='*70}")
    print(f"\nOVERALL: {'GATE PASSED' if (all_pass1_ok and all_pass2_ok) else 'GATE FAILED'}")

    out = {"pass1": decomposition_results, "pass2": sim_results}
    Path("fw_count_eval_report.json").write_text(json.dumps(out, indent=2, default=str))
    print("\nFull report written to fw_count_eval_report.json")

    return 0 if (all_pass1_ok and all_pass2_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
