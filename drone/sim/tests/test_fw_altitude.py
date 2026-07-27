#!/usr/bin/env python3
"""M2 gate: the fixed-wing altitude command actually flies the aircraft up and down.

Before this milestone `fw_drive` carried only airspeed and yaw rate, so a
fixed-wing flew its entire mission frozen at spawn altitude — `goto()` even
accepted a target altitude and silently discarded it. The SAR demo's
descend-to-identify and climb-to-orbit beats need real vertical control, so
this checks the whole chain (SimBackend -> depot_client -> IPC ->
FixedWingManager) against a live sim rather than trusting the wiring.

Runs headless: nothing here reads the camera, only telemetry.

Usage:
    python3 tests/test_fw_altitude.py
"""
from __future__ import annotations

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

RID = "fw-alt-test"
PORT = 9986


def _alt(c):
    return c.fw_state(RID)["position"][2]


def _fly(c, backend, climb, seconds, airspeed=16.0):
    """Hold a climb command for `seconds`, returning the altitude trace."""
    trace = []
    t_end = time.time() + seconds
    while time.time() < t_end:
        backend.drive(airspeed, 0.0, climb)
        time.sleep(0.1)
        trace.append(_alt(c))
    return trace


def main() -> int:
    failures = []
    proc = launch_flightline(seed=0, port=PORT, gui=False, env="flightline")
    try:
        c = DepotClient(port=PORT)
        backend = SimBackend(c, RID)

        # --- 1. climb -------------------------------------------------------
        c.fw_spawn(RID, (0.0, 0.0, 40.0), 0.0)
        time.sleep(0.2)
        start = _alt(c)
        trace = _fly(c, backend, climb=4.0, seconds=6.0)
        gained = trace[-1] - start
        # 6 s at 4 m/s = 24 m, less the ~1.5 s easing ramp. Assert a real,
        # substantial climb rather than an exact figure, which would just be
        # pinning the easing constant into a test.
        print(f"climb   : {start:.1f} -> {trace[-1]:.1f} m (+{gained:.1f} m in 6 s)")
        if gained < 12.0:
            failures.append(f"climb too weak: +{gained:.1f} m in 6 s at 4 m/s cmd")

        # --- 2. descent -----------------------------------------------------
        top = _alt(c)
        trace = _fly(c, backend, climb=-4.0, seconds=6.0)
        lost = top - trace[-1]
        print(f"descend : {top:.1f} -> {trace[-1]:.1f} m (-{lost:.1f} m in 6 s)")
        if lost < 12.0:
            failures.append(f"descent too weak: -{lost:.1f} m in 6 s at -4 m/s cmd")

        # --- 3. level hold: climb=0 must not drift --------------------------
        # Settle first. Climb rate is eased with a ~1.5 s time constant (so the
        # chase camera doesn't snap), so commanding 0 straight out of the -4 m/s
        # descent above legitimately keeps losing height for a couple of seconds.
        # Measuring during that ramp tests the easing, not the altitude hold.
        _fly(c, backend, climb=0.0, seconds=4.0)
        base = _alt(c)
        trace = _fly(c, backend, climb=0.0, seconds=3.0)
        drift = abs(trace[-1] - base)
        print(f"hold    : drift {drift:.2f} m over 3 s")
        if drift > 1.0:
            failures.append(f"altitude drifts {drift:.2f} m with climb=0")

        # --- 4. envelope floor ----------------------------------------------
        # Command a hard sustained descent from low altitude and confirm the
        # aircraft refuses to go through the floor.
        c.fw_spawn(RID, (0.0, 0.0, 15.0), 0.0)
        time.sleep(0.2)
        trace = _fly(c, backend, climb=-12.0, seconds=8.0)
        lowest = min(trace)
        print(f"floor   : lowest {lowest:.2f} m (floor 2.0 m)")
        if lowest < 2.0 - 1e-6:
            failures.append(f"altitude floor breached: {lowest:.2f} m < 2.0 m")
        events = [e for e in c.fw_events(RID)
                  if e.get("kind") == "envelope_protection"]
        print(f"floor   : {len(events)} envelope_protection events logged")
        if not events:
            failures.append("floor was enforced but no envelope_protection event logged")

        # --- 5. goto() honours its altitude argument -------------------------
        c.fw_spawn(RID, (0.0, 0.0, 30.0), 0.0)
        time.sleep(0.2)
        backend.goto(north_m=0.0, east_m=260.0, alt_m=55.0, timeout_s=40.0, tol_m=12.0)
        reached = _alt(c)
        print(f"goto    : commanded 55 m, reached {reached:.1f} m")
        if abs(reached - 55.0) > 6.0:
            failures.append(f"goto ignored alt: wanted 55 m, got {reached:.1f} m")

        # --- 6. goto() floors an unsafely low commanded altitude -------------
        c.fw_spawn(RID, (0.0, 0.0, 30.0), 0.0)
        time.sleep(0.2)
        backend.goto(north_m=0.0, east_m=260.0, alt_m=1.0, timeout_s=40.0, tol_m=12.0)
        floored = _alt(c)
        print(f"goto    : commanded 1 m, floored to {floored:.1f} m "
              f"(min {SimBackend.MIN_COMMANDED_ALT_M} m)")
        if floored < SimBackend.MIN_COMMANDED_ALT_M - 2.0:
            failures.append(f"goto flew below commanded-altitude floor: {floored:.1f} m")

        c.fw_despawn(RID)
        c.close()
    finally:
        proc.stop()

    print()
    if failures:
        print(f"M2 FAIL — {len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("M2 PASS — altitude command, envelope floor and goto altitude all verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
