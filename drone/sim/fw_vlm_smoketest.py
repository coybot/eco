#!/usr/bin/env python3
"""Real-VLM smoke test for the fixed-wing MissionLoop against the Godot flightline sim.

Unlike fw_eval.py's Reacquire/Sector Sweep/Wall Went Up scenarios (which
deliberately script the flight because no VLM weights were available on this
Mac at the time), this drives the ACTUAL reasoning_loop.MissionLoop — the real
production reasoning layer, with a real backends.SimBackend and a real
vlm.VLMService loaded from downloaded Qwen3-VL-2B weights — end-to-end against
a live Godot instance. Every action taken is genuinely chosen by the model
looking at a real captured frame (backend.capture_frame(), now wired through
fixedwing_manager.gd's forward camera), not a scripted proxy.

Bounded (MAX_PHASE_ACTIONS lowered) because each VLM decision is a real
multi-second inference call plus real (blocking) flight time for any
goto()-style action — this is a smoke test proving the wiring works, not a
full mission run.

Usage:
    python3 fw_vlm_smoketest.py [--objective "..."] [--start x y z] [--max-actions N]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
sys.path.insert(0, str(COMMON_DIR))
ROVER_SIM_DIR = Path(__file__).resolve().parents[2] / "rover" / "sim"
sys.path.insert(0, str(ROVER_SIM_DIR))

from backends import SimBackend  # noqa: E402
from vehicle_class import get_class  # noqa: E402
from reasoning_loop import Mission, MissionLoop  # noqa: E402
from depot_client import DepotClient  # noqa: E402

from fw_eval import launch_flightline, RID  # noqa: E402 — reuse the proven launcher, not a second copy


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--objective", default="Find the water tower and report on it")
    ap.add_argument("--success", default="Water tower located and reported")
    ap.add_argument("--start", type=float, nargs=3, default=[0.0, 0.0, 30.0], metavar=("X", "Y", "Z"))
    ap.add_argument("--max-actions", type=int, default=6)
    ap.add_argument("--port", type=int, default=9989)
    args = ap.parse_args()

    # gui=True unconditionally: fw_grab_frame (like grab_vantage) needs a real
    # rendering driver — headless Godot's dummy driver leaves it blank.
    proc = launch_flightline(seed=0, port=args.port, gui=True)
    try:
        client = DepotClient(port=args.port)
        client.fw_spawn(RID, tuple(args.start), 0.0)
        time.sleep(0.3)

        backend = SimBackend(client, RID)
        vc = get_class("fixedwing")
        loop = MissionLoop(backend=backend, vehicle_class=vc, on_progress=print)
        loop.MAX_PHASE_ACTIONS = args.max_actions

        mission = Mission(
            mission_id="fw_vlm_smoketest_1",
            phases=[{"objective": args.objective, "success": args.success}],
            original_message=args.objective,
        )

        print(f"\n{'='*60}\n  REAL VLM SMOKE TEST — MissionLoop.run() against Godot\n{'='*60}")
        t0 = time.time()
        result = loop.run(mission)
        elapsed = time.time() - t0

        client.fw_despawn(RID)
        client.close()
    finally:
        proc.stop()

    print(f"\nWall time: {elapsed:.1f}s")
    print(f"Success: {result.success}")
    print(f"Summary: {result.summary}")
    print(f"Actions taken: {result.actions_taken}")
    if result.failure_reason:
        print(f"Failure reason: {result.failure_reason}")
    if result.findings:
        print("Findings:")
        for f in result.findings:
            print(f"  - {f}")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
