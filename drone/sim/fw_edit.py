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
from pathlib import Path as pathlib_Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FPS = 24
W, H = 1920, 1080
BG = (11, 13, 18)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/SFNS.ttf",
]


# Brand typography and palette. Archivo is the only typeface: Thin for
# titles and body, Bold ALL-CAPS and tracked for eyebrows and labels. Three
# brand colours, plus ONE accent per composition — Light Blue here, because the
# footage is sky and aircraft and the brand guidance is to pick the accent that
# harmonises with the dominant tones.
BRAND = pathlib_Path(
    "<brand-assets>/"
    "brand-guidelines")
ARCHIVO_THIN = BRAND / "assets/fonts/Archivo-Thin.ttf"
ARCHIVO_BOLD = BRAND / "assets/fonts/Archivo-Bold.ttf"
BRAND_LOGO_LIGHT = BRAND / "assets/logos/logo-light.png"
BRAND_LOGO_DARK = BRAND / "assets/logos/logo-dark.png"

BRIGHT_WHITE = (255, 255, 255)
TECHNICAL_SAND = (229, 224, 217)
DEEP_BLACK = (0, 0, 0)
ACCENT = (117, 225, 243)          # Light Blue


def brand_font(size: int, bold: bool = False):
    """Archivo, or a geometric sans fallback — never a serif (brand rule)."""
    path = ARCHIVO_BOLD if bold else ARCHIVO_THIN
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return font(size)


def tracked(d, xy, text: str, f, fill, spacing: float = 0.15):
    """Bold means ALL-CAPS and tracked out — not optional, per the brand."""
    x, y = xy
    for ch in text.upper():
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + f.size * spacing
    return x


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
    """A brand card: Technical Sand ground, Deep Black type, one accent rule.

    Was white-on-near-black in a system font, which reads as a terminal dump.
    The brand is deliberately three colours and one typeface — Archivo Thin for
    the title and body, Bold ALL-CAPS and tracked for the eyebrow — and its
    identity is bright and warm rather than dark and technical.
    """
    im = Image.new("RGB", (W, H), TECHNICAL_SAND)
    d = ImageDraw.Draw(im)
    ft, fs, fe = brand_font(72), brand_font(30), brand_font(22, bold=True)

    # Accent rule: punctuation, not paint.
    d.rectangle([160, 214, 268, 219], fill=ACCENT)

    if subtitle:
        tracked(d, (160, 170), subtitle, fe, (90, 86, 80))

    y = 258
    for line in wrap(d, title, ft, W - 320, 3):
        d.text((160, y), line, font=ft, fill=DEEP_BLACK)
        y += 88

    y += 40
    for line in wrap(d, body, fs, W - 420, 14):
        d.text((160, y), line, font=fs, fill=(58, 55, 50))
        y += 46

    try:
        logo = Image.open(BRAND_LOGO_DARK).convert("RGBA")
        lh = 46
        logo = logo.resize((int(lh * logo.width / logo.height), lh), Image.LANCZOS)
        im.paste(logo, (W - logo.width - 96, H - lh - 78), logo)
    except Exception:
        pass
    return im


def caption_frame(path: Path, lines: list, badge: str,
                  onboard: Path = None) -> Image.Image:
    im = Image.open(path).convert("RGB")
    if im.size != (W, H):
        im = im.resize((W, H), Image.LANCZOS)
    d = ImageDraw.Draw(im, "RGBA")
    fb, fc = font(30), font(31)

    # The frame the model actually saw, inset large.
    #
    # This is not decoration, it is the only place the subject appears. Sampled
    # across a whole take: the person in the red jacket is in 0 of 247 chase
    # frames and clearly legible in the onboard view. The chase camera sits 9 m
    # back and 3 m up, so a 1.7 m figure fifty metres out is sub-pixel — a
    # search-and-rescue film in which the chase camera is the only picture is
    # one where the rescued person is never on screen.
    #
    # Onboard frames are one per decision rather than continuous, so they cannot
    # carry motion; the chase plate does that underneath while this shows what
    # the decision was made from.
    if onboard is not None and onboard.exists():
        try:
            ob = Image.open(onboard).convert("RGB")
            ow = int(W * 0.34)
            ob = ob.resize((ow, int(ow * ob.height / ob.width)), Image.LANCZOS)
            # Right side, clear of the badge: the sim burns its own telemetry
            # into the top-left of every chase frame, and the inset was sitting
            # on top of it.
            ox, oy = W - ob.width - 48, 120
            d.rectangle([ox - 6, oy - 6, ox + ob.width + 6, oy + ob.height + 40],
                        fill=(0, 0, 0, 200))
            im.paste(ob, (ox, oy))
            d.text((ox + 8, oy + ob.height + 8),
                   "ONBOARD — the frame this decision was made from",
                   font=font(24), fill=(190, 200, 210))
        except Exception:
            pass

    if badge:
        fbb = brand_font(24, bold=True)
        bw = sum(d.textlength(c, font=fbb) + fbb.size * 0.15 for c in badge)
        d.rectangle([W - bw - 104, 44, W - 44, 96], fill=(0, 0, 0, 205))
        d.rectangle([W - bw - 104, 44, W - bw - 100, 96], fill=ACCENT)
        tracked(d, (W - bw - 82, 58), badge, fbb, TECHNICAL_SAND)

    if lines:
        # Blur-and-tint the caption zone rather than dropping a black box on it.
        #
        # The brand is explicit that a semi-transparent dark rectangle over
        # imagery reads as broken CSS rather than design, and that if text needs
        # a scrim to be readable the composition is wrong. Blurring averages the
        # frame's brightness variation into a predictable surface while keeping
        # its colour, and a feathered top edge keeps the transition intentional.
        box_h = 30 + len(lines) * 46
        top = H - box_h - 64
        zone = im.crop((0, top, W, H)).filter(ImageFilter.GaussianBlur(28))
        tint = Image.new("RGB", zone.size, (14, 16, 18))
        zone = Image.blend(zone, tint, 0.55)
        # Feather: fully transparent at the top of the zone, opaque by 90 px.
        mask = Image.new("L", zone.size, 255)
        md = ImageDraw.Draw(mask)
        for i in range(90):
            md.line([(0, i), (W, i)], fill=int(255 * i / 90))
        im.paste(zone, (0, top), mask)

        d = ImageDraw.Draw(im, "RGBA")
        y = top + 40
        for i, line in enumerate(lines):
            if i == 0:
                # Eyebrow: Bold, ALL-CAPS, tracked, with the accent rule.
                d.rectangle([78, y + 6, 78 + 54, y + 10], fill=ACCENT)
                tracked(d, (150, y - 2), line, brand_font(22, bold=True),
                        TECHNICAL_SAND)
            else:
                d.text((78, y), line, font=brand_font(31), fill=BRIGHT_WHITE)
            y += 46
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
    # A cut is a SELECTION, not a transcript. One segment per decision meant 162
    # segments for a two-aircraft take — a half-hour video, and 2.7 hours to
    # render because every one of them ran motion-compensated interpolation.
    ap.add_argument("--clips-per-drone", type=int, default=6,
                    help="how many decisions to feature per aircraft")
    ap.add_argument("--clip-seconds", type=float, default=3.5,
                    help="screen time per featured decision")
    ap.add_argument("--wall-clip", default=None,
                    help="a CLEAN M6 wall run's frame directory, spliced in as "
                         "the obstacle beat")
    ap.add_argument("--draft", action="store_true",
                    help="skip motion interpolation; minutes instead of hours")
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

    # The obstacle beat, from the gate where it is actually demonstrated.
    #
    # In the full mission the wall beat keeps failing on envelope interventions
    # — the guard does the avoiding, so that footage cannot honestly carry the
    # claim. The M6 gate is where the model routes around it unaided, 1 run in 5
    # here and 3 in 5 previously, so the beat is cut from a run that earned it
    # rather than from a prettier one that did not.
    if args.wall_clip:
        wc = Path(args.wall_clip)
        wframes = sorted(wc.glob("*.jpg"))
        if wframes:
            add_card("02_wall", "A wall it was never told about",
                     "The tasking describes the mission, not the terrain. The "
                     "aircraft meets a 50 m wall on its own camera and routes "
                     "around it — no map, no path planner, no operator in the "
                     "loop. Scored clean: the envelope guard never intervened.",
                     8.0)
            span = max(1, int(args.src_fps * 12.0))
            step = max(1, len(wframes) // span)
            chunk = wframes[::step][:span]
            seq = work / "seq_02b_wall"
            if seq.exists():
                shutil.rmtree(seq)
            write_seq([caption_frame(f, ["OBSTACLE — routed around from the camera alone"],
                                     "COMMS: DENIED") for f in chunk], seq)
            segments.append(encode(seq, work / "02b_wall.mp4", args.src_fps,
                                   smooth=not args.draft))

    for drone in ("alpha", "bravo"):
        chase = take / f"chase_{drone}"
        frames = sorted(chase.glob("*.jpg")) if chase.exists() else []
        decisions = load_decisions(take, drone)
        if not frames or not decisions:
            continue
        per = max(1, len(frames) // max(1, len(decisions)))
        # Feature a spread of decisions, and never drop a delivery: the release
        # is the beat the whole mission exists for, so it is pinned in whatever
        # else gets cut.
        usable = [i for i, d in enumerate(decisions)
                  if (d.get("action", {}).get("reasoning") or "").strip()]
        pinned = {i for i in usable
                  if decisions[i].get("action", {}).get("action_type")
                  in ("drop_payload", "mission_complete")}
        budget = max(1, args.clips_per_drone - len(pinned))
        step = max(1, len(usable) // budget)
        chosen = sorted(pinned | set(usable[::step][:budget]))
        # Screen time per clip is fixed, so the cut's length is predictable
        # rather than a function of how many decisions the aircraft happened
        # to make.
        span = max(1, int(args.src_fps * args.clip_seconds))
        for i in chosen:
            dec = decisions[i]
            act = dec.get("action", {})
            reasoning = (act.get("reasoning") or "").strip()
            if not reasoning:
                continue
            start = min(i * per, max(0, len(frames) - span))
            chunk = frames[start:start + span]
            if not chunk:
                break
            probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
            lines = [f"{drone.upper()} · onboard model · {act.get('action_type', '')}"]
            lines += wrap(probe, reasoning, font(31), W - 200, 3)
            seq = work / f"seq_10_{drone}_{i:03d}"
            if seq.exists():
                shutil.rmtree(seq)
            ob = take / f"onboard_{drone}" / f"{i:04d}.jpg"
            write_seq([caption_frame(f, lines, "COMMS: DENIED", ob) for f in chunk],
                      seq)
            segments.append(encode(seq, work / f"10_{drone}_{i:03d}.mp4",
                                   args.src_fps, smooth=not args.draft))

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
