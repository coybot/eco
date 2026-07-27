#!/usr/bin/env python3
"""Can a correct route trip the envelope guard?

The M6 gate scores a take by its envelope-intervention count and demands zero,
on the reasoning that an intervention means the model failed to avoid the wall
itself. That inference only holds if the guard fires ONLY on genuinely unsafe
headings. It fires whenever structure sits within 60 m along the current
heading — and an aircraft flying a perfectly good diagonal toward the wall's
north end is pointed past the end, not at the wall, yet may still have wall
cells inside that ray for part of the leg.

If so, the count penalises the right answer, "zero interventions" is an
unreasonable bar rather than a strict one, and every M6 result so far has been
scored against a metric that cannot distinguish good flying from bad.

So: fly the ideal route in software — no model, no aircraft, just the guard's
own `_blocked_ahead` evaluated along a route that provably never crosses the
wall — and count what it reports. A correct route must score zero.

Usage:
    python3 tests/test_guard_fairness.py
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

RID = "fw-fair"
PORT = 9976

START = (-30.0, 0.0)
GOAL = (400.0, 0.0)
STEP_M = 1.4          # 14 m/s at the guard's own 10 Hz
ALT_M = 35.0


def walk(guard, waypoints):
    """Count guard trips along a straight-leg route, and where they happen."""
    trips, first = 0, None
    pos = waypoints[0]
    for tgt in waypoints[1:]:
        dist = math.hypot(tgt[0] - pos[0], tgt[1] - pos[1])
        steps = max(1, int(dist / STEP_M))
        for i in range(steps + 1):
            f = i / steps
            x = pos[0] + (tgt[0] - pos[0]) * f
            y = pos[1] + (tgt[1] - pos[1]) * f
            yaw = math.atan2(tgt[1] - y, tgt[0] - x) if i < steps else \
                math.atan2(tgt[1] - pos[1], tgt[0] - pos[0])
            if guard._blocked_ahead(x, y, yaw, ALT_M) is not None:
                trips += 1
                if first is None:
                    first = (round(x), round(y))
        pos = tgt
    return trips, first


def crosses_wall(waypoints, wall) -> bool:
    """Ground truth for the route itself, independent of the guard."""
    for a, b in zip(waypoints, waypoints[1:]):
        if (a[0] - wall["east"]) * (b[0] - wall["east"]) < 0:
            f = (wall["east"] - a[0]) / ((b[0] - a[0]) or 1e-9)
            if abs(a[1] + f * (b[1] - a[1])) <= wall["half_n"]:
                return True
    return False


def main() -> int:
    fails = []
    proc = launch_flightline(seed=0, port=PORT, gui=False, env="sar")
    try:
        c = DepotClient(port=PORT)
        wall = c.fw_env_state()["wall"]
        c.fw_spawn(RID, (START[0], START[1], ALT_M), 0.0)
        time.sleep(0.3)
        guard = EnvelopeGuardedSimBackend(c, RID)
        guard.close()          # only the geometry is wanted, not the watchdog

        margin = wall["half_n"]
        routes = {
            # What the model actually chose, twice over, in its own words:
            # round the north end, then run in to the goal.
            "model's own route (via 186,130)":
                [START, (186.0, 130.0), GOAL],
            # The tightest sane detour: clear the end by 15 m.
            "tight but legal (clears end by 15 m)":
                [START, (wall["east"] - 40.0, margin + 15.0),
                 (wall["east"] + 40.0, margin + 15.0), GOAL],
            # A generous detour nobody could call unsafe.
            "wide detour (clears end by 60 m)":
                [START, (wall["east"] - 40.0, margin + 60.0),
                 (wall["east"] + 40.0, margin + 60.0), GOAL],
            # Control: this one SHOULD trip, or the check is measuring nothing.
            "straight through the wall (control)":
                [START, GOAL],
        }

        print(f"wall east={wall['east']}, span +-{wall['half_n']} m, "
              f"guard lookahead {guard.LOOKAHEAD_M:.0f} m\n")
        results = {}
        for name, wps in routes.items():
            trips, first = walk(guard, wps)
            unsafe = crosses_wall(wps, wall)
            results[name] = trips
            print(f"{name:38s} crosses_wall={str(unsafe):5s} trips={trips:4d}"
                  + (f"  first at {first}" if first else ""))

        c.fw_despawn(RID)
        c.close()
    finally:
        proc.stop()

    print()
    control = results.pop("straight through the wall (control)")
    if control == 0:
        fails.append("the control route flies straight through the wall and the "
                     "guard said nothing — this test proves nothing as written")
    for name, trips in results.items():
        if trips:
            fails.append(f"{name} never crosses the wall, yet the guard trips "
                         f"{trips} times — the intervention count penalises "
                         f"correct flying, so 'zero interventions' is not a fair bar")

    if fails:
        print(f"GUARD FAIRNESS FAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("GUARD FAIRNESS PASS — safe routes score zero; only unsafe headings trip.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
