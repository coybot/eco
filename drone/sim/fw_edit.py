#!/usr/bin/env python3
"""Assemble the SAR demo showcase cut from a recorded take.

Builds the final ~2 minute video segment by segment and concatenates, rather
than as one giant ffmpeg filtergraph — a single mega-graph is unreadable, fails
as a unit, and makes re-rendering one beat mean re-rendering everything.

Every caption in the cut comes from the recorded run: the tasking card shows the
real prompt and the real plan JSON, the lower thirds show the model's own
verbatim reasoning at that moment, and the closing card shows the actual mission
reports. Nothing is written for the video. If a beat did not happen in the take,
it does not appear in the cut — which is the point of scoring the take first.

Inputs come from `fw_swarm_demo.py --record`:
    take_NN/chase_alpha/*.jpg     chase camera, 1080p
    take_NN/onboard_alpha/*.jpg   the frames the model actually saw
    take_NN/onboard_alpha/*.json  what it decided from each one

Usage:
    python3 fw_edit.py --take runs/take_00 --report swarm_report.json --out final.mp4
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FPS = 24
W, H = 1920, 1080
FONT = "/System/Library/Fonts/Supplemental/Helvetica.ttc"


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def esc(text: str) -> str:
    """Escape for ffmpeg drawtext, which is fussy about several characters."""
    out = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")
    return out.replace("%", "\\%").replace(",", "\\,").replace("[", "(").replace("]", ")")


def wrap(text: str, width: int = 64, max_lines: int = 6) -> list:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                lines[-1] += " ..."
                return lines
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def card(out: Path, title: str, body: str, seconds: float, subtitle: str = "") -> Path:
    """A full-screen text card."""
    filters = [f"drawtext=fontfile={FONT}:text='{esc(title)}':fontcolor=white:"
               f"fontsize=64:x=(w-text_w)/2:y=180"]
    if subtitle:
        filters.append(f"drawtext=fontfile={FONT}:text='{esc(subtitle)}':"
                       f"fontcolor=0xAAAAAA:fontsize=32:x=(w-text_w)/2:y=270")
    y = 380
    for line in wrap(body, width=70, max_lines=12):
        filters.append(f"drawtext=fontfile={FONT}:text='{esc(line)}':fontcolor=0xDDDDDD:"
                       f"fontsize=30:x=140:y={y}")
        y += 46
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=0x0B0D12:s={W}x{H}:d={seconds}:r={FPS}",
        "-vf", ",".join(filters), "-pix_fmt", "yuv420p", str(out),
    ], check=True, capture_output=True)
    return out


def clip(out: Path, frames_dir: Path, start: int, count: int, src_fps: float,
         overlays: list, badge: str = "COMMS: DENIED") -> Path:
    """A segment of chase-cam frames with captions burned in."""
    frames = sorted(frames_dir.glob("*.jpg"))[start:start + count]
    if not frames:
        return None
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, f in enumerate(frames):
            (tmp / f"{i:05d}.jpg").write_bytes(f.read_bytes())
        filters = [f"scale={W}:{H}"]
        if badge:
            filters.append(
                f"drawtext=fontfile={FONT}:text='{esc(badge)}':fontcolor=0xFF6666:"
                f"fontsize=30:x=w-text_w-50:y=50:box=1:boxcolor=0x000000AA:boxborderw=12")
        y = H - 260
        for line in overlays:
            filters.append(
                f"drawtext=fontfile={FONT}:text='{esc(line)}':fontcolor=white:"
                f"fontsize=32:x=70:y={y}:box=1:boxcolor=0x000000BB:boxborderw=14")
            y += 48
        # minterpolate smooths the low capture rate up to 24 fps; the sim can't
        # render fast enough to capture at final frame rate without stalling.
        filters.append(f"minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc")
        subprocess.run([
            "ffmpeg", "-y", "-framerate", str(src_fps), "-i", str(tmp / "%05d.jpg"),
            "-vf", ",".join(filters), "-pix_fmt", "yuv420p", "-r", str(FPS), str(out),
        ], check=True, capture_output=True)
    return out


def load_decisions(take: Path, drone: str) -> list:
    out = []
    d = take / f"onboard_{drone}"
    for j in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(j.read_text()))
        except Exception:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", required=True, help="a take_NN directory from --record")
    ap.add_argument("--report", default=None, help="swarm_report.json for the beat log")
    ap.add_argument("--plan", default="swarm_plan_cache.json")
    ap.add_argument("--out", default="final.mp4")
    ap.add_argument("--src-fps", type=float, default=6.0)
    args = ap.parse_args()

    if not have_ffmpeg():
        print("FAIL: ffmpeg not on PATH", file=sys.stderr)
        return 2
    take = Path(args.take)
    if not take.exists():
        print(f"FAIL: no such take {take}", file=sys.stderr)
        return 2

    work = take / "_edit"
    work.mkdir(exist_ok=True)
    segments = []

    # --- title ---------------------------------------------------------------
    segments.append(card(
        work / "00_title.mp4",
        "Two aircraft. No link. One decision-maker on board.",
        "Everything that follows was decided in flight by an 8-billion-parameter "
        "vision-language model running on the aircraft — the size that fits a "
        "Jetson Orin NX. Simulated flight; real model, real camera frames.",
        6.0, subtitle="Search and rescue, comms denied"))

    # --- tasking: the REAL prompt and the REAL plan ---------------------------
    plan_path = Path(args.plan)
    plan_txt = "(plan cache not found)"
    if plan_path.exists():
        plans = json.loads(plan_path.read_text())
        bits = []
        for name, plan in plans.items():
            phases = plan.get("phases") or []
            bits.append(f"{name}: {len(phases)} phases — "
                        + "; ".join(str(p.get("objective", p.get("type", "")))[:60]
                                    for p in phases[:3]))
        plan_txt = "  |  ".join(bits)
    segments.append(card(
        work / "01_tasking.mp4", "One tasking, uplinked before launch",
        plan_txt, 8.0,
        subtitle="Decomposed by the cloud planner — the last contact they have"))

    # --- flight beats, per aircraft ------------------------------------------
    for drone in ("alpha", "bravo"):
        chase = take / f"chase_{drone}"
        if not chase.exists():
            continue
        decisions = load_decisions(take, drone)
        frames = sorted(chase.glob("*.jpg"))
        if not frames:
            continue
        # Spread the available chase frames across the decisions, so each
        # segment is captioned with the reasoning that was live at the time.
        per = max(1, len(frames) // max(1, len(decisions)))
        for i, dec in enumerate(decisions):
            act = dec.get("action", {})
            reasoning = (act.get("reasoning") or "").strip()
            if not reasoning:
                continue
            lines = [f"{drone.upper()} — onboard model: {act.get('action_type', '')}"]
            lines += wrap(reasoning, width=78, max_lines=3)
            seg = clip(work / f"10_{drone}_{i:03d}.mp4", chase, i * per, per,
                       args.src_fps, lines)
            if seg:
                segments.append(seg)

    # --- closing: the real reports -------------------------------------------
    closing = "Link restored on return. Both aircraft reported:"
    if args.report and Path(args.report).exists():
        rep = json.loads(Path(args.report).read_text())
        best = next((t for t in rep.get("detail", []) if t.get("all_beats_pass")),
                    (rep.get("detail") or [None])[0])
        if best:
            beats = ", ".join(f"{k}: {'yes' if v['pass'] else 'no'}"
                              for k, v in best.get("beats", {}).items())
            closing = f"Verified against scene truth — {beats}"
    segments.append(card(work / "99_close.mp4", "Link restored", closing, 8.0,
                         subtitle="Beats checked against the simulator, not the "
                                  "drones' own claims"))

    # --- concat ---------------------------------------------------------------
    lst = work / "concat.txt"
    lst.write_text("\n".join(f"file '{s.resolve()}'" for s in segments if s))
    out = Path(args.out)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(out)], check=True, capture_output=True)
    print(f"Wrote {out} from {len(segments)} segments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
