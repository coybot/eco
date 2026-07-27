#!/usr/bin/env python3
"""M3 gate: the SAR demo scene is built to spec and its two staged beats are real.

Three things have to be true before any brain work is built on this scene, and
each is checked against the live sim rather than read off the source:

1. Geometry matches the plan (wall, tunnel, search area, drop-zone clearance)
   and the occupancy grid actually contains the wall.
2. The target really bolts for the tunnel when an aircraft comes in close and
   low — the trigger is proximity-based, not a timer, so it stays in step with
   however long the model takes to think.
3. The tunnel really hides him. This is the one that matters most: the whole
   "he disappeared, climb and wait for him to re-emerge" beat is worthless if
   the drone can still see through the roof. Checked by putting an aircraft
   directly overhead and confirming detect() loses the target while it can
   still see an unroofed bystander from the same altitude — which rules out
   "detect() saw nothing because the camera/geometry is broken".

Runs headless: telemetry and detections only, no camera.

Usage:
    python3 tests/test_env_sar.py
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))
sys.path.insert(0, str(SIM_DIR.parents[0] / "common"))

from depot_client import DepotClient  # noqa: E402
from fw_eval import launch_flightline  # noqa: E402

RID = "fw-sar-test"
PORT = 9985


def main() -> int:
    fails = []
    proc = launch_flightline(seed=0, port=PORT, gui=False, env="sar")
    try:
        c = DepotClient(port=PORT)
        env = c.fw_env_state()

        # --- 1. geometry to spec -------------------------------------------
        print(f"env       : {env.get('env')}  target_phase={env.get('target_phase')}")
        wall, tun, drop = env["wall"], env["tunnel"], env["drop_zone"]
        print(f"wall      : e={wall['east']} h={wall['height']} span_n=+-{wall['half_n']}")
        print(f"tunnel    : e[{tun['w_end']},{tun['e_end']}] n={tun['n']} "
              f"h={tun['height']}")
        if wall["height"] <= 35.0:
            fails.append(f"wall {wall['height']} m does not block the 35 m transit alt")

        # Drop zone must be well clear of structure so a released bottle
        # settles cleanly (plan D4).
        clear = math.hypot(drop[0] - tun["e_end"], drop[1] - tun["n"])
        print(f"drop zone : {drop} — {clear:.1f} m from tunnel mouth (need >=25)")
        if clear < 25.0:
            fails.append(f"drop zone only {clear:.1f} m from the tunnel")

        # Three people, all reporting the same label: the sim must not leak
        # which one is the target.
        people = [p for p in c.fw_prop_truth() if p["label"] == "person"]
        print(f"props     : {len(people)} people, labels={sorted({p['label'] for p in people})}")
        if len(people) != 3:
            fails.append(f"expected 3 people in the scene, found {len(people)}")

        # The wall must exist in the occupancy grid, not just as a mesh.
        c.fw_spawn(RID, (0.0, 0.0, 35.0), 0.0)
        time.sleep(0.2)
        grid = c.fw_grid(RID)
        import base64
        occ = base64.b64decode(grid["occ"])
        res, origin, gw = grid["res"], grid["origin"], grid["w"]

        def blocked(x, y):
            gx = int((x - origin[0]) / res)
            gy = int((y - origin[1]) / res)
            return occ[gy * gw + gx] == 1

        on_wall = blocked(wall["east"], 0.0)
        off_wall = blocked(wall["east"], wall["half_n"] + 25.0)
        print(f"occupancy : wall cell={on_wall}, corridor cell={off_wall}")
        if not on_wall:
            fails.append("wall is not present in the occupancy grid")
        if off_wall:
            fails.append("the end corridor is blocked in the occupancy grid")

        # --- 2. proximity trigger -------------------------------------------
        tgt = c.fw_env_state()["target_enu"]
        # High pass first: close but ABOVE the altitude trigger must NOT spook him.
        c.fw_spawn(RID, (tgt[0] - 20.0, tgt[1], 40.0), 0.0)
        time.sleep(0.6)
        phase_high = c.fw_env_state()["target_phase"]
        print(f"high pass : 40 m overhead -> phase={phase_high}")
        if phase_high != "amble":
            fails.append(f"target bolted from a high pass (phase={phase_high})")

        # Low pass: close AND low must trigger the run.
        tgt = c.fw_env_state()["target_enu"]
        c.fw_spawn(RID, (tgt[0] - 15.0, tgt[1], 18.0), 0.0)
        time.sleep(0.6)
        st = c.fw_env_state()
        print(f"low pass  : 18 m / ~15 m out -> phase={st['target_phase']} "
              f"triggered_by={st['triggered_by']!r}")
        if st["target_phase"] == "amble":
            fails.append("target did not bolt for a close, low aircraft")
        if st["triggered_by"] != RID:
            fails.append(f"trigger not attributed to the aircraft ({st['triggered_by']!r})")

        # --- 3. the tunnel actually occludes ---------------------------------
        # Put him inside directly, rather than flying a whole approach.
        c.fw_inject("sar_config", force_phase="in_tunnel", dwell_s=600.0,
                    target_enu=[(tun["w_end"] + tun["e_end"]) / 2.0, tun["n"]])
        time.sleep(0.3)
        st = c.fw_env_state()
        mid_e = (tun["w_end"] + tun["e_end"]) / 2.0
        print(f"tunnel    : target at {st['target_enu']} phase={st['target_phase']}")

        # Observation geometry, used identically for the test and its control.
        # It has to satisfy two constraints at once or the test proves nothing:
        # the subject must be inside the camera's 35 deg vertical FOV (boresight
        # is 15 deg down, so depression must stay under ~32.5 deg) AND inside
        # the 80 m detect range. A first attempt at 30 m ground / 25 m alt was
        # a 38.8 deg depression — below the frame entirely, so detect() would
        # have reported nothing whether or not the tunnel had a roof.
        OBS_GROUND, OBS_ALT = 55.0, 25.0   # depression ~24 deg, slant ~60 m

        def look_from_south(east: float, north: float) -> list:
            """Park the aircraft OBS_GROUND south of a point, nose north."""
            c.fw_spawn(RID, (east, north - OBS_GROUND, OBS_ALT), math.radians(90.0))
            time.sleep(0.4)
            return [d["label"] for d in c.fw_detect(RID)]

        seen_roofed = look_from_south(mid_e, tun["n"])
        print(f"occlusion : looking into the tunnel -> detect={seen_roofed}")
        if "person" in seen_roofed:
            fails.append("target is still detectable inside the tunnel")

        # Control: same altitude and range, but at an UNROOFED person. If this
        # also sees nothing then the previous result proved nothing about the roof.
        gray = [p for p in c.fw_prop_truth()
                if abs(p["world"][0] - 205.0) < 1 and abs(p["world"][1] - 45.0) < 1]
        if not gray:
            fails.append("gray bystander not found for the occlusion control")
        else:
            gx, gy = gray[0]["world"][0], gray[0]["world"][1]
            seen_open = look_from_south(gx, gy)
            print(f"control   : same geometry, open ground -> detect={seen_open}")
            if "person" not in seen_open:
                fails.append("control failed: detect() sees no one in the open either, "
                             "so the tunnel result is not evidence of occlusion")

        c.fw_despawn(RID)
        c.close()
    finally:
        proc.stop()

    print()
    if fails:
        print(f"M3 FAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("M3 PASS — scene to spec, proximity trigger real, tunnel genuinely occludes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
