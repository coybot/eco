#!/usr/bin/env python3
"""Returning home has to work from the far side of the wall.

The delivery happens east of a 50 m wall; home is west of it. rtl() flew a
straight line to the origin at whatever altitude the phase asked for — 15 m in
the plans the cloud planner writes — so the leg crossed the wall, the vehicle
correctly refused it, the phase failed, and nothing holds a fixed-wing still.
Measured in the first recorded take: an aircraft that had just delivered its
payload flew straight for fifteen minutes and finished 12.8 km from home.

Real RTL climbs to a safe return altitude before heading back, which is what
ArduPilot's RTL_ALT is for. This checks that it does, from the place the
mission actually ends up.

Usage:
    python3 tests/test_rtb.py
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

RID = "fw-rtb"
PORT = 9951
DROP_ZONE = (462.0, 18.0)     # where a delivery actually leaves the aircraft
HOME_RADIUS_M = 60.0


def main() -> int:
    fails = []
    proc = launch_flightline(seed=0, port=PORT, gui=False, env="sar")
    try:
        c = DepotClient(port=PORT)
        wall = c.fw_env_state()["wall"]

        # Post-delivery pose: east of the wall, low, as the run-in leaves it.
        c.fw_spawn(RID, (DROP_ZONE[0], DROP_ZONE[1], 15.0), 0.0)
        time.sleep(0.4)
        backend = EnvelopeGuardedSimBackend(c, RID)
        try:
            print(f"start     : ({DROP_ZONE[0]:.0f}, {DROP_ZONE[1]:.0f}) at 15 m, "
                  f"east of a {wall['height']:.0f} m wall at east={wall['east']:.0f}")

            # The straight line home from here is exactly the leg that used to be
            # refused — assert that, so a scene change that moves the wall cannot
            # turn this test into a no-op that passes for the wrong reason.
            if not backend.path_blocked((0.0, 0.0), 15.0):
                fails.append("the direct line home at 15 m does NOT cross the wall "
                             "from here, so this test is not exercising the bug")

            ok = backend.rtl(15.0)
            pose = backend.get_pose()
            dist = math.hypot(pose[0], pose[1])
            print(f"rtl(15 m) : returned {ok}, ended at ({pose[0]:.0f}, {pose[1]:.0f}, "
                  f"{pose[2]:.0f}) — {dist:.0f} m from home")

            if not ok:
                fails.append("rtl() refused: the return leg is still being planned "
                             "through the wall instead of over it")
            if dist > HOME_RADIUS_M:
                fails.append(f"ended {dist:.0f} m from home (want within "
                             f"{HOME_RADIUS_M:.0f} m)")
            if pose[2] <= wall["height"]:
                fails.append(f"returned at {pose[2]:.0f} m, at or below the "
                             f"{wall['height']:.0f} m wall — it got home by luck, "
                             f"not by climbing over")
        finally:
            backend.close()
        c.fw_despawn(RID)
        c.close()
    finally:
        proc.stop()

    print()
    if fails:
        print(f"RTB FAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("RTB PASS — climbs over the wall and gets home from the delivery point.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
