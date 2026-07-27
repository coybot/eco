#!/usr/bin/env python3
"""SimBackend.capture_frame recovers from the frozen forward camera, under a real VLM.

The forward-camera render target freezes permanently once the VLM starts using
the GPU (Bug 11 in papers/fixed_wing_sitl_lessons_learned.md). Every capture
after that is byte-identical stale pixels while detect() keeps reporting the
truth, so the model reasons about a photograph of the past and merely looks
blind. MissionLoop captures continuously in flight, so it hits this on every
mission — the recovery has to live in the backend, not in each harness.

This reproduces the exact conditions (real weights inferring between captures)
and asserts captures keep changing. It deliberately loads the real 8B model
rather than mocking, because a mock cannot reproduce the GPU contention that
causes the freeze in the first place.

Usage:
    python3 tests/test_capture_recovery.py
"""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))
sys.path.insert(0, str(SIM_DIR.parents[0] / "common"))

from backends import SimBackend  # noqa: E402
from depot_client import DepotClient  # noqa: E402
from fw_eval import launch_flightline  # noqa: E402

RID = "fw-cap-test"
PORT = 9982
N = 8


def main() -> int:
    fails = []

    from vlm import VLMService
    print("loading the real VLM (the thing that triggers the freeze)...")
    svc = VLMService()
    if not svc.is_available():
        print("SKIP: VLM unavailable — this test is meaningless without it.")
        return 0

    proc = launch_flightline(seed=0, port=PORT, gui=True, env="sar")
    try:
        c = DepotClient(port=PORT)
        backend = SimBackend(c, RID)
        # Fly along the wall so the view genuinely changes between captures.
        c.fw_spawn(RID, (-60.0, 0.0, 35.0), 0.0)
        time.sleep(0.3)

        digests = []
        for i in range(N):
            frame = backend.capture_frame()
            if frame is None:
                fails.append(f"capture {i} returned nothing")
                break
            d = hashlib.md5(frame).hexdigest()[:8]
            digests.append(d)
            # Real inference between captures — this is what freezes the camera.
            svc.ask(frame, "Reply with one word: what colour is the ground?",
                    max_tokens=16)
            print(f"  capture {i}: {len(frame):6d} bytes  md5={d}")
            time.sleep(0.2)

        unique = len(set(digests))
        print(f"\n[A] free flight   : {unique}/{len(digests)} distinct, "
              f"{backend.stale_frame_recoveries} recoveries")
        # The aircraft is moving the whole time, so with a live camera nearly
        # every frame differs. Allow a little slack for genuinely similar views.
        if unique < len(digests) - 1:
            fails.append(f"free flight: only {unique}/{len(digests)} captures distinct")

        # --- B: the pattern that reliably freezes ---------------------------
        # Re-staging the aircraft between captures (as a staged-pose harness
        # does) froze the render target every time during M1, whereas plain
        # continuous flight above often does not. Without this phase the test
        # can pass with zero recoveries and prove nothing about the recovery
        # path it exists to cover.
        base = backend.stale_frame_recoveries
        digests_b = []
        for i in range(N):
            c.fw_spawn(RID, (-60.0 + i * 25.0, i * 8.0, 35.0), 0.0)
            c.fw_stop(RID)
            time.sleep(0.2)
            frame = backend.capture_frame()
            if frame is None:
                fails.append(f"restaged capture {i} returned nothing")
                break
            d = hashlib.md5(frame).hexdigest()[:8]
            digests_b.append(d)
            svc.ask(frame, "Reply with one word: what colour is the ground?",
                    max_tokens=16)
            print(f"  restaged {i}: {len(frame):6d} bytes  md5={d}")

        unique_b = len(set(digests_b))
        fired = backend.stale_frame_recoveries - base
        print(f"\n[B] re-staged     : {unique_b}/{len(digests_b)} distinct, "
              f"{fired} recoveries")
        # Every pose here is a different place, so every capture must differ.
        if unique_b < len(digests_b):
            fails.append(f"re-staged: only {unique_b}/{len(digests_b)} captures "
                         "distinct — stale frames are still reaching the caller")

        c.fw_despawn(RID)
        c.close()
    finally:
        proc.stop()

    print()
    if fails:
        print(f"FAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS — captures stay live across real VLM inference.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
