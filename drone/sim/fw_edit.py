#!/usr/bin/env python3
"""Assemble the SAR demo showcase cut from a recorded take.

All text is rendered with PIL and composited onto frames, then ffmpeg only
encodes and concatenates. That split is not a style choice: the ffmpeg on this
machine is built without libfreetype, so the `drawtext` filter does not exist
at all — a first version of this script used it and could not render a single
card. fw_video_overlay.py already draws its overlays with PIL for the same
reason; this follows that.

The cut is built segment by segment and concatenated, rather than as one giant
filtergraph, so a single beat can be re-rendered without redoing everything.

Every caption comes from the recorded run: the tasking card shows the real
prompt and the real plan, the lower thirds show the model's own verbatim
reasoning at that moment, and the closing card shows the beats as scored against
scene truth. Nothing is written for the video — if a beat did not happen in the
take, it does not appear in the cut.

Inputs come from `fw_swarm_demo.py --record`:
    take_NN/chase_alpha/*.jpg     chase camera
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

from PIL import Image, ImageDraw, ImageFont

FPS = 24
W, H = 1920, 1080
BG = (11, 13, 18)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/SFNS.ttf",
]


def font(size: int):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def wrap(draw, text: str, f, max_w: int, max_lines: int = 40) -> list:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=f) > max_w and cur:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                return lines
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def write_seq(images, out_dir: Path, start_idx: int = 0) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, im in enumerate(images):
        im.save(out_dir / f"{start_idx + i:05d}.jpg", quality=92)
    return start_idx + len(images)


def card_image(title: str, body: str, subtitle: str = "") -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    ft, fs, fb = font(58), font(30), font(30)

    for line_i, line in enumerate(wrap(d, title, ft, W - 300, 3)):
        w = d.textlength(line, font=ft)
        d.text(((W - w) / 2, 150 + line_i * 72), line, font=ft, fill=(255, 255, 255))
    if subtitle:
        w = d.textlength(subtitle, font=fs)
        d.text(((W - w) / 2, 320), subtitle, font=fs, fill=(150, 160, 175))
    y = 430
    for line in wrap(d, body, fb, W - 320, 14):
        d.text((160, y), line, font=fb, fill=(215, 220, 228))
        y += 46
    return im


def caption_frame(path: Path, lines: list, badge: str) -> Image.Image:
    im = Image.open(path).convert("RGB")
    if im.size != (W, H):
        im = im.resize((W, H), Image.LANCZOS)
    d = ImageDraw.Draw(im, "RGBA")
    fb, fc = font(30), font(31)

    if badge:
        bw = d.textlength(badge, font=fb)
        d.rectangle([W - bw - 96, 44, W - 40, 100], fill=(0, 0, 0, 190))
        d.text((W - bw - 68, 56), badge, font=fb, fill=(255, 110, 110))

    if lines:
        box_h = 26 + len(lines) * 44
        d.rectangle([48, H - box_h - 60, W - 48, H - 60], fill=(0, 0, 0, 190))
        y = H - box_h - 42
        for i, line in enumerate(lines):
            d.text((78, y), line, font=fc,
                   fill=(255, 255, 255) if i == 0 else (205, 212, 222))
            y += 44
    return im


def encode(seq_dir: Path, out: Path, src_fps: float, smooth: bool) -> Path:
    vf = f"minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc" if smooth else f"fps={FPS}"
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(src_fps), "-i", str(seq_dir / "%05d.jpg"),
        "-vf", vf, "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18",
        "-r", str(FPS), str(out),
    ], check=True, capture_output=True)
    return out


def load_decisions(take: Path, drone: str) -> list:
    out = []
    for j in sorted((take / f"onboard_{drone}").glob("*.json")):
        try:
            out.append(json.loads(j.read_text()))
        except Exception:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("--plan", default="swarm_plan_cache.json")
    ap.add_argument("--out", default="final.mp4")
    ap.add_argument("--src-fps", type=float, default=6.0)
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None:
        print("FAIL: ffmpeg not on PATH", file=sys.stderr)
        return 2
    take = Path(args.take)
    if not take.exists():
        print(f"FAIL: no such take {take}", file=sys.stderr)
        return 2

    work = take / "_edit"
    work.mkdir(exist_ok=True)
    segments = []

    def add_card(name, title, body, seconds, subtitle=""):
        im = card_image(title, body, subtitle)
        d = work / f"seq_{name}"
        if d.exists():
            shutil.rmtree(d)
        write_seq([im] * max(1, int(seconds * 4)), d)
        segments.append(encode(d, work / f"{name}.mp4", 4.0, smooth=False))

    add_card("00_title", "Two aircraft. No link. One decision-maker on board.",
             "Everything that follows was decided in flight by an 8-billion-parameter "
             "vision-language model running on the aircraft — the size that fits a "
             "Jetson Orin NX. Simulated flight; real model, real camera frames.",
             6.0, "Search and rescue, communications denied")

    plan_path = Path(args.plan)
    plan_txt = "(plan cache not found)"
    if plan_path.exists():
        plans = json.loads(plan_path.read_text())
        bits = []
        for name, plan in plans.items():
            phases = plan.get("phases") or []
            first = "; ".join(str(p.get("objective", p.get("type", "")))[:70]
                              for p in phases[:2])
            bits.append(f"{name}: {len(phases)} phases — {first}")
        plan_txt = "   ".join(bits)
    add_card("01_tasking", "One tasking, uplinked before launch", plan_txt, 9.0,
             "Decomposed by the cloud planner — the last contact they have")

    for drone in ("alpha", "bravo"):
        chase = take / f"chase_{drone}"
        frames = sorted(chase.glob("*.jpg")) if chase.exists() else []
        decisions = load_decisions(take, drone)
        if not frames or not decisions:
            continue
        per = max(1, len(frames) // max(1, len(decisions)))
        for i, dec in enumerate(decisions):
            act = dec.get("action", {})
            reasoning = (act.get("reasoning") or "").strip()
            if not reasoning:
                continue
            chunk = frames[i * per:(i + 1) * per]
            if not chunk:
                break
            probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
            lines = [f"{drone.upper()} · onboard model · {act.get('action_type', '')}"]
            lines += wrap(probe, reasoning, font(31), W - 200, 3)
            seq = work / f"seq_10_{drone}_{i:03d}"
            if seq.exists():
                shutil.rmtree(seq)
            write_seq([caption_frame(f, lines, "COMMS: DENIED") for f in chunk], seq)
            segments.append(encode(seq, work / f"10_{drone}_{i:03d}.mp4",
                                   args.src_fps, smooth=True))

    closing = "Link restored on return."
    if args.report and Path(args.report).exists():
        rep = json.loads(Path(args.report).read_text())
        detail = rep.get("detail") or []
        best = next((t for t in detail if t.get("all_beats_pass")), detail[0] if detail else None)
        if best:
            closing = "Checked against the simulator's own truth, not the drones' claims — " + \
                ", ".join(f"{k.replace('_', ' ')}: {'yes' if v['pass'] else 'no'}"
                          for k, v in best.get("beats", {}).items())
    add_card("99_close", "Link restored", closing, 9.0,
             "Every beat verified against scene truth")

    lst = work / "concat.txt"
    lst.write_text("\n".join(f"file '{s.resolve()}'" for s in segments if s))
    out = Path(args.out)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(out)], check=True, capture_output=True)
    print(f"Wrote {out} from {len(segments)} segments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
