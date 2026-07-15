#!/usr/bin/env python3
"""Phase 3: run the Depot capstone against the REAL cloud brain (billed Bedrock calls),
capturing overhead frames throughout for capstone_full.mp4.

Launches the Godot Depot sim, starts eco/e2e/harness/live_rover_act_bridge.py (which
serves the real production /rover/act handler locally, faking only the API Gateway
authorizer), then runs CloudBrainCapstoneTests on an iOS Simulator against both — while a
background thread polls the SAME already-running Godot instance (a second, independent
IPC client connection — Godot's server supports many concurrent clients, same as the real
fleet daemons; this is NOT a second Godot process, which is what caused a port conflict
during Phase-2 debugging) for overhead vantage frames.

Always produces capstone_full.mp4 from whatever frames were captured, even if the test
is interrupted (Ctrl-C or killed) partway — video isn't contingent on the mission
reaching .done. Also always writes events.jsonl from Godot's own event log.

This makes real, billed Bedrock calls (Claude Sonnet, via AWS_PROFILE=astral) — meant to
be run sparingly, not iterated on the way the free scripted-brain tests were.

Usage: python3 run_live_capstone.py [--seed N] [--device "iPhone 17"] [--fps 2]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from depot_client import DepotClient
from godot_launcher import launch_depot

SDK_DIR = Path(__file__).resolve().parents[3] / "sdk"
ECO_DIR = Path(__file__).resolve().parents[2]
VIDEOS_DIR = Path(__file__).resolve().parents[2] / "videos_phrover"
BRIDGE_READY_TIMEOUT = 30


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
    # explicitly overrides it — see run_live_beats.py's launch_bridge() for why this
    # matters (an entire round of recordings silently used the wrong default model).
    env = {**os.environ, "AWS_PROFILE": "astral",
           "BEDROCK_MODEL_ID": os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-opus-4-8")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "e2e.harness.live_rover_act_bridge"],
        cwd=ECO_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    url_holder = {}
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
    """Polls an overhead vantage on a background thread until stopped."""

    def __init__(self, frame_dir: Path, fps: float):
        self.frame_dir = frame_dir
        self.fps = fps
        self._stop = threading.Event()
        self._client = DepotClient()  # independent connection, safe alongside the Swift test's own
        self._client.add_vantage("overhead", (0.0, -4.0, 18.0), (0.0, 5.0, 0.0))
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.frame_count = 0

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        i = 0
        while not self._stop.is_set():
            jpg = self._client.grab_vantage("overhead")
            if jpg:
                (self.frame_dir / f"f{i:06d}.jpg").write_bytes(jpg)
                i += 1
                self.frame_count = i
            time.sleep(1.0 / self.fps)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._client.close()


def make_video(frame_dir: Path, fps: float, out_path: Path) -> bool:
    if not any(frame_dir.glob("f*.jpg")):
        print("no frames captured — skipping video encode")
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps), "-i", str(frame_dir / "f%06d.jpg"),
        "-vf", "scale=1280:720", "-pix_fmt", "yuv420p", str(out_path),
    ]
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"wrote {out_path}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--device", default="iPhone 17")
    ap.add_argument("--fps", type=float, default=2.0)
    args = ap.parse_args()

    print(f"--- launching Godot Depot (seed={args.seed}, port={args.port}) ---")
    # --gui (not headless): headless Godot returns empty/erroring vantage textures on
    # this Mac (confirmed in Phase 1 and again here) — every grab_vantage call would
    # silently fail and capstone_full.mp4 would have zero frames.
    godot = launch_depot(seed=args.seed, port=args.port, gui=True)

    print("--- launching live_rover_act_bridge (real Bedrock, billed) ---")
    bridge = launch_bridge()
    print(f"--- bridge ready at {bridge.url} ---")

    import tempfile
    frame_dir = Path(tempfile.mkdtemp(prefix="live_capstone_frames_"))
    print(f"--- capturing overhead frames to {frame_dir} at {args.fps} fps ---")
    grabber = FrameGrabber(frame_dir, args.fps)
    grabber.start()

    returncode = 1
    try:
        cmd = [
            "xcodebuild", "test",
            "-scheme", "astral-sdk-Package",
            "-destination", f"platform=iOS Simulator,name={args.device}",
            "-only-testing:PhroverSimTests/CloudBrainCapstoneTests",
        ]
        env_overrides = {
            "TEST_RUNNER_GODOT_HOST": "127.0.0.1",
            "TEST_RUNNER_GODOT_PORT": str(args.port),
            "TEST_RUNNER_LIVE_ROVER_ACT_URL": bridge.url,
            "TEST_RUNNER_DEPOT_SEED": str(args.seed),
        }
        print("running:", " ".join(cmd), "with", env_overrides)
        result = subprocess.run(cmd, cwd=SDK_DIR, env={**os.environ, **env_overrides})
        returncode = result.returncode
    finally:
        grabber.stop()
        print(f"--- captured {grabber.frame_count} frames ---")
        make_video(frame_dir, args.fps, VIDEOS_DIR / "capstone_full.mp4")
        import shutil
        shutil.rmtree(frame_dir, ignore_errors=True)
        bridge.stop()
        godot.stop()

    return returncode


if __name__ == "__main__":
    sys.exit(main())
