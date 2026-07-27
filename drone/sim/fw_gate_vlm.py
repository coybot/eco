#!/usr/bin/env python3
"""M1 perception gate: can the on-device VLM read "the guy in the RED jacket"?

This is the first milestone of the two-drone SAR demo plan, run BEFORE any
other demo work, because the whole "big AI on device, not just YOLO" claim
rests on one unproven assumption: that the shipped on-device model
(Qwen3-VL-8B Q4, the weights that fit a Jetson Orin NX 16GB) can identify a
person by a VISUAL ATTRIBUTE from a fixed-wing camera, at the slant ranges the
mission actually flies. A YOLO-class detector says "person"; the demo's
premise is that the big model says "that one, in the red jacket". If it
cannot, the storyboard's identification beat is fiction and the plan branches
to fine-tuning instead of building on sand.

What it does: launches Godot with the `gate` env (drone/sim/godot/scripts/
env_gate.gd — well-separated humanoid subjects, one red-jacketed and two
ordinarily dressed), stages the aircraft in front of each subject at a matrix
of slant ranges, bearings and altitude profiles, captures the REAL forward
camera frame each time, and asks the REAL model a closed question about it.
Nothing here is scripted or stubbed: the frames come from the same
fw_grab_frame path MissionLoop uses in flight, and the answers come from the
same VLMService the drone runs.

Two honest details that shape the rig:

* The sim has no way to hold a fixed-wing still — stop() is a loiter at
  MIN_AIRSPEED (12 m/s), so a staged aircraft is always moving toward its
  subject. Rather than pretend otherwise, each pose is converged by
  re-spawning with a drift correction, and every trial is then scored against
  the range MEASURED from fw_state at capture time, not the range requested.
* Framing and apparent size are separated. "centered" staging puts the subject
  on the camera boresight (the camera looks 15° down), isolating apparent size
  as the variable. "swoop" staging fixes altitude at the plan's 12 m descent
  floor, which is the harder, mission-realistic geometry: a steeper, more
  top-down look with the subject low in the frame. Geometries that would put
  the subject outside the 35° vertical FOV are skipped rather than scored.

Pass bar (from the plan): >=80% correct red-jacket discrimination at slant
ranges <=25 m. Every frame is written to disk alongside the report so the
score is auditable rather than taken on trust.

Usage:
    python3 fw_gate_vlm.py [--out DIR] [--ranges 15 25 40] [--quick]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import time
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
sys.path.insert(0, str(COMMON_DIR))
ROVER_SIM_DIR = Path(__file__).resolve().parents[2] / "rover" / "sim"
sys.path.insert(0, str(ROVER_SIM_DIR))

from depot_client import DepotClient  # noqa: E402
from fw_eval import launch_flightline  # noqa: E402 — reuse the proven launcher

RID = "fw-gate-1"

# Mirrors env_gate.gd's SUBJECT_* constants. Cross-checked against
# fw_prop_truth() at startup instead of being trusted — a silent drift between
# the two would quietly invalidate every score below.
SUBJECTS = {
    "red_jacket":     {"pos": (200.0, 0.0),    "red": True},
    "gray_bystander": {"pos": (200.0, -120.0), "red": False},
    "navy_bystander": {"pos": (200.0, 120.0),  "red": False},
}
PAIR_SUBJECT = {"pos": (60.0, 4.5), "red": True}   # midpoint of pair_red/pair_gray

SUBJECT_MID_HEIGHT = 0.875   # humanoid.gd origin is at mid-height (HEIGHT/2)
LOOK_DOWN_DEG = 15.0         # fixedwing_manager.gd DETECT_LOOK_DOWN / camera boresight
VFOV_HALF_DEG = 17.5         # fixedwing_manager.gd DETECT_VFOV_HALF
FRAMING_MARGIN_DEG = 2.5     # keep the subject this far inside the vertical FOV edge
SWOOP_ALT = 12.0             # plan D1's commanded-altitude floor

PROBE = (
    "This is a frame from a fixed-wing drone's forward camera flying over open "
    "grassland. Look carefully at any people on the ground.\n"
    "Answer with JSON only, no other text:\n"
    '{"people_visible": <integer>, "red_jacket_person": <true or false>, '
    '"note": "<at most 12 words>"}\n'
    'Set "red_jacket_person" to true ONLY if at least one visible person is wearing '
    "a clearly RED jacket or red upper-body clothing. Gray, navy, blue, dark or any "
    "other non-red top is NOT a red jacket. If you see no people at all, set it to false."
)


# --------------------------------------------------------------------------- geometry
def stage_pose(target_xy, slant_m: float, bearing_deg: float, alt_mode: str):
    """Aircraft ENU pose that views `target_xy` at `slant_m`, or None if the
    geometry would push the subject outside the camera's vertical FOV.

    bearing_deg is the compass-style direction FROM the target TO the aircraft,
    so the aircraft always ends up pointing back at the subject.
    """
    tx, ty = target_xy
    if alt_mode == "centered":
        # Subject exactly on the boresight: depression == LOOK_DOWN_DEG.
        ground = slant_m * math.cos(math.radians(LOOK_DOWN_DEG))
        alt = SUBJECT_MID_HEIGHT + slant_m * math.sin(math.radians(LOOK_DOWN_DEG))
    elif alt_mode == "swoop":
        dz = SWOOP_ALT - SUBJECT_MID_HEIGHT
        if slant_m <= dz:
            return None
        ground = math.sqrt(slant_m * slant_m - dz * dz)
        alt = SWOOP_ALT
    else:
        raise ValueError(f"unknown alt_mode {alt_mode}")

    depression = math.degrees(math.atan2(alt - SUBJECT_MID_HEIGHT, ground))
    if abs(depression - LOOK_DOWN_DEG) > (VFOV_HALF_DEG - FRAMING_MARGIN_DEG):
        return None   # subject would fall outside the frame — not a fair probe

    th = math.radians(bearing_deg)
    sx, sy = tx + ground * math.cos(th), ty + ground * math.sin(th)
    yaw = math.atan2(ty - sy, tx - sx)
    return {"pos": (sx, sy, alt), "yaw": yaw, "ground": ground,
            "alt": alt, "depression": depression}


def measured_slant(state: dict, target_xy) -> float:
    px, py, pz = state["position"]
    tx, ty = target_xy
    return math.sqrt((px - tx) ** 2 + (py - ty) ** 2 + (pz - SUBJECT_MID_HEIGHT) ** 2)


def converge_pose(client, target_xy, slant_m, bearing_deg, alt_mode,
                  settle_s: float = 0.20, tol_m: float = 0.6, max_iter: int = 4):
    """Spawn, let the renderer settle, and correct for the drift the aircraft
    accumulates while flying toward the subject at MIN_AIRSPEED. Returns the
    final measured state, or None if the geometry is unusable."""
    pose = stage_pose(target_xy, slant_m, bearing_deg, alt_mode)
    if pose is None:
        return None
    tx, ty = target_xy
    th = math.radians(bearing_deg)
    ground, alt = pose["ground"], pose["alt"]
    state = None
    for _ in range(max_iter):
        sx, sy = tx + ground * math.cos(th), ty + ground * math.sin(th)
        yaw = math.atan2(ty - sy, tx - sx)
        client.fw_spawn(RID, (sx, sy, alt), yaw)
        client.fw_stop(RID)
        # The forward-camera render target freezes permanently once the VLM
        # starts using the GPU (see FixedWingManager.reset_camera). Without
        # this rebuild every capture after the first inference is byte-identical
        # stale pixels, which reads as a blind model rather than a stale camera
        # — it silently invalidated a whole gate run before being caught.
        # Rebuilt here so the settle below doubles as the new viewport's first
        # render.
        client.fw_reset_camera(RID)
        time.sleep(settle_s)
        state = client.fw_state(RID)
        err = measured_slant(state, target_xy) - slant_m
        if abs(err) <= tol_m:
            break
        # The aircraft flies toward the subject during the settle, so it ends
        # up closer than staged (err < 0); back the spawn off by that much.
        ground = max(3.0, ground - err)
    return state


def _extract_json(text: str):
    if not text:
        return None
    m = re.search(r"\{.*?\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def parse_probe(reply: str):
    """(red_jacket_bool_or_None, people_count_or_None, parsed_ok)."""
    data = _extract_json(reply)
    if isinstance(data, dict) and "red_jacket_person" in data:
        red = data.get("red_jacket_person")
        if isinstance(red, str):
            red = red.strip().lower() in ("true", "yes")
        count = data.get("people_visible")
        if not isinstance(count, int):
            try:
                count = int(count)
            except Exception:
                count = None
        return (bool(red), count, True)
    # Fallback: the model answered in prose. Only trust an unambiguous phrasing.
    low = (reply or "").lower()
    if "red_jacket_person" in low or "red jacket" in low:
        if re.search(r"(no|not|isn't|no one|nobody)[^.]{0,40}red", low):
            return (False, None, False)
        if re.search(r"(yes|is|wearing)[^.]{0,40}red", low):
            return (True, None, False)
    return (None, None, False)


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="gate_out", help="directory for frames + report")
    ap.add_argument("--ranges", type=float, nargs="+", default=[15.0, 25.0, 40.0])
    ap.add_argument("--bearings", type=float, nargs="+", default=[0.0, 120.0, 240.0])
    ap.add_argument("--modes", nargs="+", default=["centered", "swoop"])
    ap.add_argument("--port", type=int, default=9991)
    ap.add_argument("--quick", action="store_true",
                    help="one bearing, centered only — smoke-test the rig, not a real gate")
    args = ap.parse_args()

    if args.quick:
        args.bearings = [0.0]
        args.modes = ["centered"]

    out_dir = Path(args.out).resolve()
    (out_dir / "frames").mkdir(parents=True, exist_ok=True)

    # Load the real model first: it is the slow, most likely to fail step, and
    # there is no point launching Godot if the weights are missing.
    from vlm import VLMService
    print("Loading on-device VLM (this takes a moment)...")
    t0 = time.time()
    svc = VLMService()
    if not svc.is_available():
        print("FAIL: VLM not available — cannot run the gate.", file=sys.stderr)
        print(json.dumps(svc.get_info(), indent=2), file=sys.stderr)
        return 2
    print(f"VLM ready in {time.time() - t0:.1f}s: {svc.get_info()['model_path']}")

    # gui=True unconditionally: fw_grab_frame needs a real rendering driver —
    # headless Godot's dummy driver leaves SubViewport textures blank.
    proc = launch_flightline(seed=0, port=args.port, gui=True, env="gate")
    trials = []
    try:
        client = DepotClient(port=args.port)

        # Cross-check the duplicated subject table against the sim's own truth.
        truth = client.fw_prop_truth()
        truth_xy = [(round(p["world"][0], 1), round(p["world"][1], 1)) for p in truth]
        for name, s in SUBJECTS.items():
            key = (round(s["pos"][0], 1), round(s["pos"][1], 1))
            if key not in truth_xy:
                print(f"FAIL: subject {name} at {key} not in sim truth {truth_xy}",
                      file=sys.stderr)
                return 2
        print(f"Subject table matches sim truth ({len(truth)} props).")

        jobs = []
        for name, s in SUBJECTS.items():
            for r in args.ranges:
                for b in args.bearings:
                    for m in args.modes:
                        jobs.append((name, s["pos"], s["red"], r, b, m, False))
        for r in args.ranges:
            for b in args.bearings:
                for m in args.modes:
                    jobs.append(("pair", PAIR_SUBJECT["pos"], True, r, b, m, True))

        print(f"\n{len(jobs)} staged poses queued.\n" + "=" * 72)
        prev_hash = None
        stale = 0
        for i, (name, pos, is_red, rng, brg, mode, is_pair) in enumerate(jobs, 1):
            state = converge_pose(client, pos, rng, brg, mode)
            if state is None:
                continue
            actual = measured_slant(state, pos)
            frame = client.fw_grab_frame(RID)
            if not frame:
                print(f"[{i}/{len(jobs)}] {name} r={rng} b={brg} {mode}: NO FRAME")
                continue

            # Two consecutive byte-identical captures from two different staged
            # poses means the render target froze again. Skip rather than score:
            # a stale frame scores as "model saw nothing", which is
            # indistinguishable from a real miss and quietly destroys the result.
            fhash = hashlib.md5(frame).hexdigest()
            if fhash == prev_hash:
                stale += 1
                print(f"[{i}/{len(jobs)}] STALE FRAME (render target frozen) "
                      f"{name} r={rng} b={brg} {mode} — not scored")
                prev_hash = fhash
                continue
            prev_hash = fhash

            # Independent cross-check: detect() is pure CPU geometry and cannot
            # be fooled by a frozen camera, so it says whether a person really
            # was in the sensor cone for this pose.
            detected = [d["label"] for d in client.fw_detect(RID)]

            tag = f"{name}_r{int(rng)}_b{int(brg)}_{mode}"
            fpath = out_dir / "frames" / f"{tag}.jpg"
            fpath.write_bytes(frame)

            t_probe = time.time()
            reply = svc.ask(frame, PROBE, max_tokens=160)
            latency = time.time() - t_probe
            red, count, parsed = parse_probe(reply)
            correct = (red == is_red) if red is not None else False

            trials.append({
                "subject": name, "is_red_truth": is_red, "is_pair": is_pair,
                "range_nominal_m": rng, "range_measured_m": round(actual, 2),
                "bearing_deg": brg, "alt_mode": mode,
                "alt_m": round(state["position"][2], 2),
                "answer_red": red, "answer_people": count,
                "detect_labels": detected, "in_sensor_cone": bool(detected),
                "parsed_json": parsed, "correct": correct,
                "latency_s": round(latency, 2), "frame": str(fpath.relative_to(out_dir)),
                "reply": (reply or "")[:400],
            })
            mark = "OK " if correct else "MISS"
            # flush: llama.cpp writes to the same stream unbuffered from C, so
            # without this the Python progress lines sit in a block buffer for
            # the whole run and a redirected log shows only inference spew.
            print(f"[{i}/{len(jobs)}] {mark} {name:15s} r={actual:5.1f}m b={brg:3.0f} "
                  f"{mode:8s} det={len(detected)} -> red={red} people={count} "
                  f"({latency:.1f}s)", flush=True)

        client.fw_despawn(RID)
        client.close()
    finally:
        proc.stop()

    # ----------------------------------------------------------------- scoring
    singles = [t for t in trials if not t["is_pair"]]
    close = [t for t in singles if t["range_measured_m"] <= 26.0]
    pairs = [t for t in trials if t["is_pair"]]

    def acc(ts):
        return (sum(1 for t in ts if t["correct"]) / len(ts)) if ts else 0.0

    by_range = {}
    for t in singles:
        by_range.setdefault(int(round(t["range_nominal_m"])), []).append(t)
    by_mode = {}
    for t in singles:
        by_mode.setdefault(t["alt_mode"], []).append(t)

    gate_acc = acc(close)
    passed = len(close) >= 8 and gate_acc >= 0.8

    report = {
        "gate": "M1_red_jacket_discrimination",
        "model": svc.get_info(),
        "pass_bar": ">=80% correct on single-subject trials at measured slant <=26 m",
        "passed": passed,
        "stale_frames_skipped": stale,
        "trials_not_in_sensor_cone": sum(1 for t in trials if not t["in_sensor_cone"]),
        "gate_accuracy_le26m": round(gate_acc, 3),
        "gate_n": len(close),
        "overall_accuracy_singles": round(acc(singles), 3),
        "accuracy_by_nominal_range": {str(k): {"n": len(v), "acc": round(acc(v), 3)}
                                      for k, v in sorted(by_range.items())},
        "accuracy_by_alt_mode": {k: {"n": len(v), "acc": round(acc(v), 3)}
                                 for k, v in by_mode.items()},
        "pair_frames": {
            "n": len(pairs),
            "red_detected": sum(1 for t in pairs if t["answer_red"] is True),
            "counted_two_people": sum(1 for t in pairs if t["answer_people"] == 2),
        },
        "json_parse_rate": round(sum(1 for t in trials if t["parsed_json"]) / len(trials), 3)
        if trials else 0.0,
        "latency_s": {
            "median": round(statistics.median([t["latency_s"] for t in trials]), 2),
            "max": round(max(t["latency_s"] for t in trials), 2),
        } if trials else {},
        "trials": trials,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))

    print("\n" + "=" * 72)
    print(f"GATE {'PASS' if passed else 'FAIL'} — {gate_acc:.0%} correct on {len(close)} "
          f"trials at <=26 m (bar: 80%)")
    print(f"  overall singles : {acc(singles):.0%} of {len(singles)}")
    for k, v in sorted(by_range.items()):
        print(f"  range {k:>3} m     : {acc(v):.0%} of {len(v)}")
    for k, v in by_mode.items():
        print(f"  mode {k:9s}: {acc(v):.0%} of {len(v)}")
    print(f"  pair frames     : red seen {report['pair_frames']['red_detected']}/{len(pairs)}, "
          f"counted 2 people {report['pair_frames']['counted_two_people']}/{len(pairs)}")
    print(f"  JSON parse rate : {report['json_parse_rate']:.0%}")
    print(f"  stale frames    : {stale} skipped (frozen render target)")
    print(f"  not in cone     : {report['trials_not_in_sensor_cone']} "
          f"(detect() saw nothing — geometry, not the model)")
    print(f"\nFrames + report: {out_dir}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
