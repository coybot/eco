#!/usr/bin/env python3
"""Scene gate for the pickup-truck surveillance environment (Part 2).

Checked against the LIVE sim, not read off the source:
  1. env_state reports the surveil_truck scene with a pickup-truck target and
     exposes the truck's ENU (so localization can be scored against truth).
  2. The scene contains exactly one "pickup truck" and one decoy "car" — so a
     "find the pickup truck" objective can't be satisfied by any vehicle.
  3. The truck is detectable from within its per-label range (Part 1C), and its
     position actually changes over time (it patrols).
  4. The transit wall is tall enough to block the transit altitude, same as SAR.

Runs headless: telemetry + detections only, no camera.

Usage:
    python3 tests/test_env_surveil_truck.py
    (or via pytest — skips automatically if no Godot 4 binary is installed)
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path
from shutil import which

SIM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))
sys.path.insert(0, str(SIM_DIR.parents[0] / "common"))

RID = "fw-truck-test"
PORT = 9987


def _godot_available() -> bool:
    return bool(os.environ.get("GODOT_BIN") or which("godot4") or which("godot"))


def main() -> int:
    from depot_client import DepotClient
    from fw_eval import launch_flightline

    fails = []
    proc = launch_flightline(seed=0, port=PORT, gui=False, env="surveil_truck")
    try:
        c = DepotClient(port=PORT)
        env = c.fw_env_state()
        print(f"env       : {env.get('env')}  target_kind={env.get('target_kind')}")
        if env.get("env") != "surveil_truck":
            fails.append(f"env is {env.get('env')!r}, expected 'surveil_truck'")
        if env.get("target_kind") != "pickup truck":
            fails.append(f"target_kind is {env.get('target_kind')!r}")

        target0 = env.get("target_enu")
        if not (isinstance(target0, list) and len(target0) == 2):
            fails.append(f"target_enu missing/malformed: {target0!r}")

        # --- vehicle labels: exactly one truck, one car --------------------
        truth = c.fw_prop_truth()
        labels = sorted(p["label"] for p in truth)
        trucks = [p for p in truth if p["label"] == "pickup truck"]
        cars = [p for p in truth if p["label"] == "car"]
        print(f"vehicles  : trucks={len(trucks)} cars={len(cars)} all_labels={labels}")
        if len(trucks) != 1:
            fails.append(f"expected 1 pickup truck, found {len(trucks)}")
        if len(cars) != 1:
            fails.append(f"expected 1 decoy car, found {len(cars)}")

        # --- wall blocks transit altitude (same requirement as SAR) --------
        wall = env["wall"]
        print(f"wall      : e={wall['east']} h={wall['height']}")
        if wall["height"] <= 35.0:
            fails.append(f"wall {wall['height']} m does not block 35 m transit")

        # --- truck detectable from within range, and it moves --------------
        # Put an aircraft near the truck, above it, and confirm detect() sees a
        # "pickup truck". Range for the vehicle labels is 150 m (Part 1C).
        tx, ty = target0
        c.fw_spawn(RID, (tx - 60.0, ty, 35.0), 0.0)
        c.aim_sensor_at(RID, [tx, ty, 0.0]) if hasattr(c, "aim_sensor_at") else None
        time.sleep(0.3)
        seen = {d["label"] for d in c.fw_detect(RID)}
        print(f"detect    : {sorted(seen)}")
        if "pickup truck" not in seen:
            fails.append(f"pickup truck not detected within range; saw {sorted(seen)}")

        # It patrols: position changes over ~1.5 s.
        env_a = c.fw_env_state()["target_enu"]
        time.sleep(1.5)
        env_b = c.fw_env_state()["target_enu"]
        moved = math.hypot(env_b[0] - env_a[0], env_b[1] - env_a[1])
        print(f"movement  : truck moved {moved:.2f} m in 1.5 s")
        if moved < 0.5:
            fails.append(f"truck did not move (only {moved:.2f} m) — patrol broken")

    finally:
        proc.stop()

    if fails:
        print("\nFAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("\nAll checks passed.")
    return 0


def test_surveil_truck_scene():
    """pytest entry — skips when no Godot 4 binary is available."""
    import pytest
    if not _godot_available():
        pytest.skip("Godot 4 binary not installed (set GODOT_BIN or install godot4)")
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
