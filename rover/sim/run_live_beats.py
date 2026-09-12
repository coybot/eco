#!/usr/bin/env python3
"""Phase 4, video-set finalization: run each capability-beat live CloudBrain mission in
LiveCapstoneBeats.swift as its own isolated Godot + bridge + xcodebuild invocation, and
encode that mission's own captured overhead frames directly into its own named clip — no
cutting or timestamp correlation needed, since each mission IS its capability's clip.

Real, billed Bedrock calls (Claude Sonnet, via AWS_PROFILE=coybot) — one short live
mission per beat. See LiveCapstoneBeats.swift's file doc comment for why this replaced an
earlier plan to slice sub-clips out of one long continuous run.

Usage: python3 run_live_beats.py [--device "iPhone 17"] [--fps 2] [--beat NAME ...]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from depot_client import DepotClient
from godot_launcher import launch_depot

SDK_DIR = Path(__file__).resolve().parents[3] / "sdk"
ECO_DIR = Path(__file__).resolve().parents[2]
VIDEOS_DIR = Path(__file__).resolve().parents[2] / "videos_phrover"
BRIDGE_READY_TIMEOUT = 30

# test method -> output clip name
BEATS = {
    "testPersonCrossingLive": "cap1_person_crossing.mp4",
    "testReplansAroundBlockedDoorLive": "cap3_door_block_replan.mp4",
    "testBatteryForcesEarlyReturnLive": "cap4_battery_forced_return.mp4",
    "testGoBackToRememberedObjectLive": "cap2_memory_recall.mp4",
    "testAnomalySweepLive": "cap5_8_10b_anomaly_sweep.mp4",
    "testHardStopBypassesBrainLive": "cap10a_hard_stop.mp4",
    # Dedicated one-clip-per-capability beats (previously #5/#8 only existed bundled into
    # the anomaly-sweep clip above; #6/#7 had no video at all).
    "testGoalDirectedExplorationLive": "cap5_exploration.mp4",
    "testAsksForHelpUnderAmbiguityLive": "cap7_asking_for_help.mp4",
    "testUnpromptedAnomalyReportLive": "cap8_unprompted_report.mp4",
    "testLearningFromExperienceDemoLive": "cap6_learning.mp4",
}


class Bridge:
    def __init__(self, proc: subprocess.Popen, url: str):
        self.proc = proc
        self.url = url

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def launch_bridge() -> Bridge:
    # Default to the model bake-off winner (see RESULTS_video_set.md) unless the caller
    # explicitly overrides it — confirmed the hard way that forgetting to set this env var
    # silently falls back to rover.py's own default (sonnet-5, NOT the winner), so an
    # entire round of "final" recordings got made with the wrong model without anyone
    # noticing until a capability that sonnet-5 handles worse (person-crossing) failed.
    env = {**os.environ, "AWS_PROFILE": "coybot",
           "BEDROCK_MODEL_ID": os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-opus-4-8")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "e2e.harness.live_rover_act_bridge"],
        cwd=ECO_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    url_holder: dict[str, str] = {}
    ready = threading.Event()

    def _tee():
        for line in proc.stdout:
            print(f"[bridge] {line}", end="", flush=True)
            if line.startswith("BRIDGE_URL="):
                url_holder["url"] = line.strip().split("=", 1)[1]
                ready.set()
        ready.set()

    threading.Thread(target=_tee, daemon=True).start()
    if not ready.wait(timeout=BRIDGE_READY_TIMEOUT) or "url" not in url_holder:
        proc.terminate()
        raise RuntimeError("live_rover_act_bridge did not report BRIDGE_URL in time")
    return Bridge(proc, url_holder["url"])


class FrameGrabber:
    # If grab_vantage() returns byte-identical JPEGs this many times in a row, the
    # SubViewport's render target has stalled (confirmed the hard way: 4 of a batch of
    # recorded clips froze on a single early frame for their entire remaining duration —
    # not a mission/simulation freeze, since the same runs' live decision transcripts
    # showed completely normal mission progress the whole time — purely a rendering
    # readback issue, and it's specific to the vantage capture path, not universal, so a
    # fixed engine-level root cause wasn't pinned down). Recreating the vantage (Godot's
    # add_vantage() otherwise no-ops if the name is already registered, so remove first)
    # reliably resumes real rendering.
    # First tuned to 6 (~3s @ fps=2), which was far too sensitive: real Bedrock decision
    # latency is routinely 5-15s+, so a genuinely idle rover waiting on its next decision
    # produces several legitimately-identical frames as a matter of course, not a bug —
    # confirmed live: 6 fired 24-164 times in a single ~1-10min beat, an order of
    # magnitude more than a rare rendering glitch would explain. The actual bug (frames
    # frozen for the entire remainder of a multi-minute clip) needs a threshold that
    # only trips on something that long, not on a single normal thinking-pause.
    STALL_THRESHOLD = 40  # ~20s @ fps=2 — well past any single decision's latency

    def __init__(self, frame_dir: Path, fps: float):
        self.frame_dir = frame_dir
        self.fps = fps
        self._stop = threading.Event()
        self._client = DepotClient()
        self._vantage_pos = (0.0, -4.0, 20.0)
        self._vantage_look = (0.0, 5.0, 0.0)
        self._client.add_vantage("overhead", self._vantage_pos, self._vantage_look)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.frame_count = 0
        self.stall_recoveries = 0
        # Hash -> count of every frame actually written to disk (the real video content,
        # not just the stall-detector's transient repeat streak) — lets diversity_report()
        # catch this class of bug automatically instead of relying on someone watching the
        # clip or manually diffing frames after the fact.
        self._hash_counts: dict[str, int] = {}

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        i = 0
        last_jpg: bytes | None = None
        repeat_count = 0
        while not self._stop.is_set():
            jpg = self._client.grab_vantage("overhead")
            if jpg:
                if jpg == last_jpg:
                    repeat_count += 1
                    if repeat_count >= self.STALL_THRESHOLD:
                        print(f"!!! overhead vantage stalled ({repeat_count} identical frames) — recreating it")
                        self._client.remove_vantage("overhead")
                        self._client.add_vantage("overhead", self._vantage_pos, self._vantage_look)
                        self.stall_recoveries += 1
                        repeat_count = 0
                        last_jpg = None
                        time.sleep(1.0 / self.fps)
                        continue
                else:
                    repeat_count = 0
                last_jpg = jpg
                (self.frame_dir / f"f{i:06d}.jpg").write_bytes(jpg)
                h = hashlib.sha1(jpg).hexdigest()
                self._hash_counts[h] = self._hash_counts.get(h, 0) + 1
                i += 1
                self.frame_count = i
            time.sleep(1.0 / self.fps)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._client.close()

    def diversity_report(self) -> tuple[int, int, float]:
        """(distinct_frames, total_frames, majority_fraction) — majority_fraction is how
        much of the whole recording is the single most-repeated frame. A healthy mission
        recording has this well under 0.3 even with several idle/thinking pauses; a
        genuinely stalled render target (the bug this guards against) pins it near 1.0
        since one frame dominates the entire remaining clip."""
        total = sum(self._hash_counts.values())
        if total == 0:
            return (0, 0, 1.0)
        distinct = len(self._hash_counts)
        majority = max(self._hash_counts.values()) / total
        return (distinct, total, majority)


def make_video(frame_dir: Path, fps: float, out_path: Path) -> bool:
    if not any(frame_dir.glob("f*.jpg")):
        print("no frames captured — skipping video encode")
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps), "-i", str(frame_dir / "f%06d.jpg"),
        "-vf", "scale=1280:720", "-pix_fmt", "yuv420p", str(out_path),
    ]
    subprocess.run(cmd, check=True)
    print(f"wrote {out_path}")
    return True


def run_one_beat(test_name: str, clip_name: str, port: int, device: str, fps: float) -> int:
    print(f"\n=== beat: {test_name} -> {clip_name} ===")
    godot = launch_depot(seed=7, port=port, gui=True)
    bridge = launch_bridge()
    print(f"--- bridge ready at {bridge.url} ---")

    frame_dir = Path(tempfile.mkdtemp(prefix=f"beat_{test_name}_"))
    grabber = FrameGrabber(frame_dir, fps)
    grabber.start()

    returncode = 1
    skipped = False
    try:
        cmd = [
            "xcodebuild", "test",
            "-scheme", "coybot-sdk-Package",
            "-destination", f"platform=iOS Simulator,name={device}",
            f"-only-testing:PhroverSimTests/LiveCapstoneBeats/{test_name}",
        ]
        env_overrides = {
            "TEST_RUNNER_GODOT_HOST": "127.0.0.1",
            "TEST_RUNNER_GODOT_PORT": str(port),
            "TEST_RUNNER_LIVE_ROVER_ACT_URL": bridge.url,
        }
        print("running:", " ".join(cmd), "with", env_overrides)
        # Capture output (instead of inheriting stdout directly) so a skipped test can be
        # detected programmatically — confirmed the hard way that xcodebuild still exits 0
        # when a test skips (e.g. a transient Godot-connection race), which previously let
        # this function fall through to make_video() and silently overwrite a perfectly
        # good prior clip with a near-empty one from a test that never actually ran.
        proc = subprocess.Popen(cmd, cwd=SDK_DIR, env={**os.environ, **env_overrides},
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            print(line, end="", flush=True)
            if f"{test_name}]' skipped" in line or f"{test_name}]' : Test skipped" in line:
                skipped = True
        proc.wait()
        returncode = proc.returncode
    finally:
        grabber.stop()
        print(f"--- captured {grabber.frame_count} frames for {test_name} "
              f"({grabber.stall_recoveries} vantage stall-recoveries) ---")
        if skipped:
            print(f"!!! {test_name} was SKIPPED (not a real run) — leaving {clip_name} untouched, not overwriting with a bogus clip")
            returncode = returncode or 3
        else:
            # Automated frame-diversity gate — catches a frozen/near-static recording
            # without anyone having to watch the clip or manually diff frames after the
            # fact. Confirmed the hard way: 4 clips in one batch were frozen on a single
            # frame for their entire remaining duration despite the mission itself
            # progressing completely normally, and it went unnoticed until someone
            # actually watched the videos. Never again silently ship that.
            distinct, total, majority = grabber.diversity_report()
            print(f"--- frame diversity for {test_name}: {distinct} distinct / {total} total, "
                  f"majority frame = {majority:.1%} of the clip ---")
            if total > 0 and (majority > 0.35 or distinct < 8):
                print(f"!!! {test_name}'s recording looks frozen/near-static (majority frame "
                      f"{majority:.1%}, only {distinct} distinct frames) — NOT a trustworthy "
                      f"clip even though the mission itself may have run fine; treat this as a "
                      f"failure requiring investigation, not a clean pass")
                returncode = returncode or 4
            make_video(frame_dir, fps, VIDEOS_DIR / clip_name)
        shutil.rmtree(frame_dir, ignore_errors=True)
        bridge.stop()
        godot.stop()

    return returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--device", default="iPhone 17")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--beat", action="append", choices=list(BEATS),
                     help="run only this beat (repeatable); default: all beats")
    args = ap.parse_args()

    beats = args.beat or list(BEATS)
    worst = 0
    for test_name in beats:
        rc = run_one_beat(test_name, BEATS[test_name], args.port, args.device, args.fps)
        worst = worst or rc
        print(f"=== {test_name} exited {rc} ===")

    return worst


if __name__ == "__main__":
    sys.exit(main())
