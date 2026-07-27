#!/usr/bin/env python3
"""The envelope guard has to be watching when nobody is flying the aircraft.

`EnvelopeGuardedSimBackend`'s intervention count is the score for camera-based
obstacle avoidance: a selected take must have zero. That only means anything if
the guard is actually consulted throughout the flight.

It was not. The check lived solely inside `drive()`, and the mission thread only
drives while a leg is executing — between legs it waits 4-8 s on VLM inference
while the aircraft carries on at 14 m/s, which is 55-110 m against a 60 m
lookahead. A run duly scored `envelope_events = 0` with a track straight through
a 50 m wall. Zero was not evidence of good flying; the guard had never been
asked.

So this test does the one thing the old design could not survive: points the
aircraft at the wall and then **issues no commands at all**, exactly as if the
model were thinking. Nothing here calls drive(), goto(), or anything else.

Usage:
    python3 tests/test_envelope_watchdog.py
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))

from depot_client import DepotClient  # noqa: E402
from fw_eval import launch_flightline  # noqa: E402
from guarded_backend import EnvelopeGuardedSimBackend  # noqa: E402

RID = "fw-watchdog"
PORT = 9977

# Long enough to cover the worst realistic inference gap, and to fly well past
# the wall at cruise if nothing intervenes.
QUIET_S = 14.0


def main() -> int:
    fails = []
    proc = launch_flightline(seed=0, port=PORT, gui=False, env="sar")
    try:
        c = DepotClient(port=PORT)
        wall = c.fw_env_state()["wall"]

        # Aimed squarely at the middle of the wall, below its top, close enough
        # that cruising straight ahead for QUIET_S goes through it.
        start_e = wall["east"] - 120.0
        c.fw_spawn(RID, (start_e, 0.0, 35.0), 0.0)
        time.sleep(0.3)

        backend = EnvelopeGuardedSimBackend(c, RID)
        try:
            # Nudge it into motion the way a real leg would, then go silent —
            # this is the whole point of the test.
            backend.drive(14.0, 0.0, 0.0)
            print(f"aircraft  : at east {start_e:.0f}, heading east at the "
                  f"{wall['height']:.0f} m wall (east {wall['east']:.0f}), 35 m alt")
            print(f"silence   : issuing no commands for {QUIET_S:.0f} s, as if "
                  f"the model were thinking")
            time.sleep(QUIET_S)

            pose = backend.get_pose()
            events = backend.envelope_events
        finally:
            backend.close()

        poses = [e["data"] for e in c.fw_events(RID)
                 if e.get("kind") == "pose_trace" and e["data"].get("id") == RID]
        crossed = False
        for i in range(1, len(poses)):
            a, b = poses[i - 1], poses[i]
            if (a["x"] - wall["east"]) * (b["x"] - wall["east"]) < 0:
                frac = (wall["east"] - a["x"]) / ((b["x"] - a["x"]) or 1e-9)
                if abs(a["y"] + frac * (b["y"] - a["y"])) <= wall["half_n"]:
                    crossed = True

        print(f"result    : ended at ({pose[0]:.0f}, {pose[1]:.0f}, {pose[2]:.0f}), "
              f"{events} intervention(s), crossed_wall={crossed}")

        if events == 0:
            fails.append("the guard never fired while the aircraft flew unattended "
                         "at a wall — it is only watching during drive() again")
        if crossed:
            fails.append("the aircraft went through the wall despite the guard")

        c.fw_despawn(RID)
        c.close()
    finally:
        proc.stop()

    print()
    if fails:
        print(f"WATCHDOG FAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("WATCHDOG PASS — the envelope is guarded between commands, not just during them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
