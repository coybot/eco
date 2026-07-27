#!/usr/bin/env python3
"""The camera's pixel axes must match the image the model actually looks at.

Every pixel in this system is picked by a VLM reading a rendered frame, so the
frame is the ground truth and everything else has to agree with it: `detect()`
reports pixel positions, `unproject()` turns a picked pixel back into a world
point, and the system prompt tells the model "y measured downward from the top;
the ground is in the lower half".

Those three drifted apart unnoticed. `unproject()` used elevation directly as a
y coordinate, so its vertical axis ran bottom-to-top while the image ran
top-to-bottom — every pick was silently mirrored, and a sensible one low in the
frame ("the open ground just ahead") came back unresolvable. Nothing caught it
because the sim `Detection` drops `ny`, so the only symptom was a model that
looked like it was flailing while it was in fact being answered upside down.

Two checks, both against the live renderer rather than the math:

1. Monotonicity — low in the frame must unproject NEARER than high in the frame,
   and the top of the frame must be sky. This alone is decisive and does not
   depend on identifying any particular object.
2. Agreement — `detect()`'s `ny` for the red-jacket target must match where the
   red pixels actually are in the captured frame.

Usage:
    python3 tests/test_pixel_axis.py
"""
from __future__ import annotations

import io
import math
import sys
import time
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))

from PIL import Image  # noqa: E402

from depot_client import DepotClient  # noqa: E402
from fw_eval import launch_flightline  # noqa: E402

RID = "fw-axis-test"
PORT = 9973

# Where the red jacket sits in the frame is only meaningful within the usable
# depression band (boresight 15 deg down, VFOV half 17.5 deg), so observe from
# 25 m up and ~55 m out — the same geometry the occlusion test settled on.
OBS_ALT, OBS_GROUND = 25.0, 55.0


def _red_centroid(img: Image.Image):
    """Vertical centre of the red jacket, in normalized image coords.

    The jackets are the only saturated colour in the scene, so dominance beats
    guessing at a bounding box.
    """
    w, h = img.size
    total = weighted = 0
    for y in range(h):
        for x in range(0, w, 2):
            r, g, b = img.getpixel((x, y))
            if r > 90 and r > g * 1.8 and r > b * 1.8:
                total += 1
                weighted += y
    if total < 4:
        return None
    return (weighted / total) / h


def main() -> int:
    fails = []
    proc = launch_flightline(seed=0, port=PORT, gui=True, env="sar")
    try:
        c = DepotClient(port=PORT)
        env = c.fw_env_state()
        tx, ty = env["target_enu"][0], env["target_enu"][1]

        c.fw_spawn(RID, (tx, ty - OBS_GROUND, OBS_ALT), math.radians(90.0))
        time.sleep(0.8)
        c.fw_reset_camera(RID)   # the forward viewport freezes once the GPU is busy
        time.sleep(0.5)

        # --- 1. unproject monotonicity ---------------------------------------
        # Nothing here depends on what is in the scene, which is what makes it
        # the check worth trusting.
        st = c.fw_state(RID)
        ax, ay = st["position"][0], st["position"][1]

        def reach(ny: float):
            wp = c.fw_unproject(RID, 0.5, ny)
            if wp is None:
                return None
            return math.hypot(wp[0] - ax, wp[1] - ay)

        low, mid, high = reach(0.95), reach(0.70), reach(0.05)
        print(f"unproject : bottom-of-frame -> {low}, middle -> {mid}, top -> {high}")
        if low is None:
            fails.append("a pick at the bottom of the frame does not resolve — "
                         "that is the nearest ground, it must always resolve")
        elif mid is None:
            fails.append("a pick below centre does not resolve to ground")
        elif not low < mid:
            fails.append(f"vertical axis is inverted: bottom of frame resolved "
                         f"{low:.0f} m out, further than the middle at {mid:.0f} m — "
                         f"lower in the image must mean nearer")
        else:
            print(f"          : {low:.0f} m < {mid:.0f} m — lower in frame is nearer, correct")
        if high is not None:
            fails.append(f"the top of the frame resolved to ground {high:.0f} m out; "
                         f"it points above the horizon and must be sky")

        # --- 2. detect() agrees with the rendered pixels ----------------------
        # Read pose, detections and frame back to back: nothing holds a
        # fixed-wing still, so these drift apart if separated.
        dets = c.fw_detect(RID)
        raw = c.fw_grab_frame(RID)
        img = Image.open(io.BytesIO(raw)).convert("RGB")

        people = [d for d in dets if d["label"] == "person"]
        seen = _red_centroid(img)
        if not people:
            fails.append("no person detected from the staged observation pose")
        elif seen is None:
            fails.append("the red jacket is not visible in the captured frame, so "
                         "detect()'s pixel position cannot be checked against it")
        else:
            # Whichever detection is lowest in the frame is the nearest person;
            # the red target is the one staged directly ahead.
            best = min(people, key=lambda d: abs(d["ny"] - seen))
            err = abs(best["ny"] - seen)
            print(f"detect    : ny={best['ny']:.3f} vs red pixels at {seen:.3f} "
                  f"(error {err:.3f})")
            if err > 0.15:
                fails.append(f"detect() puts the person at ny={best['ny']:.3f} but the "
                             f"rendered frame has them at {seen:.3f} — the reported "
                             f"pixel position disagrees with the image")

        c.fw_despawn(RID)
        c.close()
    finally:
        proc.stop()

    print()
    if fails:
        print(f"PIXEL AXIS FAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PIXEL AXIS PASS — detect() and unproject() agree with the rendered frame.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
