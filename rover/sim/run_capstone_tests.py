#!/usr/bin/env python3
"""Launch the Godot Depot sim, then run PhroverSimTests/CapstoneTests against it on an
iOS Simulator. Phase 2 of the design plan: scripted-brain capstone, no cloud calls.

xcodebuild test only passes environment variables prefixed `TEST_RUNNER_` through to the
simulator test-host process (stripped of the prefix) — see
sdk/swift/Tests/PhroverKitLiveProbes' use of TEST_RUNNER_LIVE_ROVER_ACT_URL for the same
convention. The iOS *Simulator* (unlike real hardware) shares the host Mac's loopback
interface, so a plain 127.0.0.1 connection from the simulated test process reaches Godot
running as a normal macOS process.

Usage: python3 run_capstone_tests.py [--seed N] [--device "iPhone 17"] [--test CapstoneTests/testName]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from godot_launcher import launch_depot

SDK_DIR = Path(__file__).resolve().parents[3] / "sdk"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--device", default="iPhone 17")
    ap.add_argument("--test", default="PhroverSimTests/CapstoneTests",
                     help="xcodebuild -only-testing target, e.g. PhroverSimTests/CapstoneTests/testBatteryForcesEarlyReturn")
    ap.add_argument("--gui", action="store_true", help="show the Godot window (default headless)")
    args = ap.parse_args()

    print(f"--- launching Godot Depot (seed={args.seed}, port={args.port}) ---")
    proc = launch_depot(seed=args.seed, port=args.port, gui=args.gui)

    try:
        cmd = [
            "xcodebuild", "test",
            "-scheme", "presidio-sdk-Package",
            "-destination", f"platform=iOS Simulator,name={args.device}",
            f"-only-testing:{args.test}",
        ]
        env_overrides = {
            "TEST_RUNNER_GODOT_HOST": "127.0.0.1",
            "TEST_RUNNER_GODOT_PORT": str(args.port),
        }
        print("running:", " ".join(cmd), "with", env_overrides)
        import os
        result = subprocess.run(cmd, cwd=SDK_DIR, env={**os.environ, **env_overrides})
        return result.returncode
    finally:
        proc.stop()


if __name__ == "__main__":
    sys.exit(main())
