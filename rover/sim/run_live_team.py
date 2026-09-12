#!/usr/bin/env python3
"""Phase 4, capability #9: 3-rover team allocation + survivor recovery against REAL
CloudBrain instances (billed Bedrock calls, 3x concurrent).

Same launch pattern as run_live_capstone.py (Godot Depot + live_rover_act_bridge.py +
xcodebuild), but targets TeamCloudBrainTests and captures overhead frames throughout for
a team survivor-recovery video.

Usage: python3 run_live_team.py [--seed N] [--device "iPhone 17"] [--fps 2]
"""

from __future__ import annotations

import argparse
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
    env = {**os.environ, "AWS_PROFILE": "coybot",
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
    def __init__(self, frame_dir: Path, fps: float):
        self.frame_dir = frame_dir
        self.fps = fps
        self._stop = threading.Event()
        self._client = DepotClient()
        self._client.add_vantage("overhead", (0.0, -4.0, 20.0), (0.0, 5.0, 0.0))
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
    godot = launch_depot(seed=args.seed, port=args.port, gui=True)  # --gui: headless returns empty frames

    print("--- launching live_rover_act_bridge (real Bedrock, billed, 3x concurrent) ---")
    bridge = launch_bridge()
    print(f"--- bridge ready at {bridge.url} ---")

    frame_dir = Path(tempfile.mkdtemp(prefix="live_team_frames_"))
    print(f"--- capturing overhead frames to {frame_dir} at {args.fps} fps ---")
    grabber = FrameGrabber(frame_dir, args.fps)
    grabber.start()

    returncode = 1
    try:
        cmd = [
            "xcodebuild", "test",
            "-scheme", "coybot-sdk-Package",
            "-destination", f"platform=iOS Simulator,name={args.device}",
            "-only-testing:PhroverSimTests/TeamCloudBrainTests",
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
        make_video(frame_dir, args.fps, VIDEOS_DIR / "team_survivor.mp4")
        shutil.rmtree(frame_dir, ignore_errors=True)
        bridge.stop()
        godot.stop()

    return returncode


if __name__ == "__main__":
    sys.exit(main())
