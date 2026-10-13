"""Smoke test for phrover_person_view / place_obstacle / person_governor.

Launches one headless Depot sim, parks a person 3 m north of the rover in the open yard,
then puts a pillar between them. Expect: every body sample visible with no obstacle; torso
samples hidden (depth == pillar range, not person range) once the pillar is there.

Run from eco/:  python -m rover.sim.follow_bench.smoke_person_view
"""
from __future__ import annotations

import math
import sys

from rover.sim.depot_client import DepotClient
from rover.sim.godot_launcher import launch_depot

PORT = 9421


def main() -> int:
    proc = launch_depot(seed=0, port=PORT, gui=False)
    failures: list[str] = []
    try:
        c = DepotClient(port=PORT)
        c.reset(7)
        c.spawn("r", (0.0, -6.0), math.pi / 2)
        # A stationary person 3 m due north of the rover: two identical waypoints.
        c.inject("person_walk", on=True, speed=0.0, waypoints=[[0.0, -3.0], [0.0, -3.0]])
        c.inject("person_governor", on=False)
        import time; time.sleep(0.5)

        v = c.person_view("r", 0.3)
        persons = v["persons"]
        if len(persons) != 1:
            failures.append(f"expected 1 person, got {len(persons)}")
        else:
            s = persons[0]["samples"]
            hidden = [x for x in s if not x[3]]
            print(f"open yard: {len(s)} samples, {len(hidden)} hidden, pos={persons[0]['pos']}")
            if hidden:
                failures.append(f"open yard should have 0 hidden samples, got {len(hidden)}")

        # Pillar centred at (0,-4.5): 1.2 x 1.2 x 2.0 m, between rover (y=-6) and person (y=-3).
        c.inject("place_obstacle", rect=[0.0, -4.5, 1.2, 1.2], h=2.0)
        import time; time.sleep(0.3)
        v = c.person_view("r", 0.3)
        s = v["persons"][0]["samples"]
        by_joint: dict[str, list] = {}
        for x in s:
            by_joint.setdefault(x[5], []).append(x)
        for joint, rows in by_joint.items():
            vis = sum(1 for r in rows if r[3])
            depths = sorted({round(r[4], 2) for r in rows})
            print(f"  pillar: {joint:9s} visible {vis}/{len(rows)} depths {depths}")
        if any(r[3] for r in s):
            failures.append("pillar spans the whole body width at 2 m tall: expected 0 visible")
        # Blocked depth must be the pillar's near face (~1.4 m from the camera), not the person's 3 m.
        if any(r[4] > 2.0 for r in s if not r[3]):
            failures.append("blocked sample depth should be the occluder range (< 2 m)")

        # Governor off: rover driven straight at the person must not be held by the 1.8 m stop.
        c.inject("clear_obstacles")
        c.drive("r", 0.4, 0.0)
        time.sleep(4.0)
        st = c.state("r")
        print(f"governor off, drove 4 s: pose={st['pose']} person_stop_active={st['person_stop_active']}")
        if st["person_stop_active"]:
            failures.append("person_stop_active should stay false with the governor off")
        c.reset(7)
        print("after reset governor restored; obstacles cleared:",
              len(c.person_view("r", 0.3)["persons"]) == 0)
    finally:
        proc.stop()
    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
