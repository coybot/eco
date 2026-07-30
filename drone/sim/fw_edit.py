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


def capture_fps(frames: list) -> float:
    """The rate the frames were ACTUALLY written at, from their mtimes.

    The recorders are asked for 6 fps and deliver 4-5.4 — a render at 6 plays
    every shot 15-50% fast. Nothing about that is visible in a frame count, so
    it went unnoticed until the aircraft started looking hurried.
    """
    if len(frames) < 2:
        return 6.0
    span = frames[-1].stat().st_mtime - frames[0].stat().st_mtime
    return (len(frames) - 1) / span if span > 0.5 else 6.0


def airframe_px(path: Path) -> int:
    """How much of the high-vis orange airframe is in this frame.

    Framing is the difference between a shot of an aircraft and a shot of a
    field. The scene cameras are tripods — placed once, never re-aimed — so
    whether the aircraft is in them at any given second is luck, and picking a
    beat by timestamp alone produced beats with 4 orange pixels in them.

    The red jacket (~200,30,30) is excluded deliberately: it is red-dominant
    too, and counting it would score the shots framed ON THE PERSON as if the
    aircraft were in them. The airframe (219,84,15) is separated by green
    sitting well above blue.
    """
    import numpy as np
    a = np.asarray(Image.open(path).convert("RGB").resize((640, 360)),
                   dtype=np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return int(((r > 130) & ((r - g) > 55) & ((r - b) > 90)
                & ((g - b) > 25)).sum())


def best_window_t(frames: list, seconds: float, speed: float,
                  lo_t=None, hi_t=None, step: int = 4):
    """The wall-clock instant whose surrounding window shows the most aircraft.

    Bounded by lo_t/hi_t so a beat can be BOTH well framed and honest: the
    obstacle beat has to come from the transit even if the aircraft happens to
    read better an hour later on the far side, or the caption is describing
    something the footage is not.
    """
    cand = [(f, f.stat().st_mtime) for f in frames[::step]]
    cand = [(f, t) for f, t in cand
            if (lo_t is None or t >= lo_t) and (hi_t is None or t <= hi_t)]
    if not cand:
        return None
    scores = [airframe_px(f) for f, _ in cand]
    half = max(1, int(seconds * speed * capture_fps(frames) / step / 2))
    best, best_i = -1, 0
    for i in range(len(scores)):
        s = sum(scores[max(0, i - half):i + half + 1])
        if s > best:
            best, best_i = s, i
    return cand[best_i][1]


def window(frames: list, at_t, seconds: float, speed: float,
           lead: float = 0.35) -> tuple:
    """A CONTIGUOUS run of frames around a moment, and the fps to play it at.

    This replaces sampling every Nth frame across the whole recording, which is
    what made the aircraft look like a hummingbird on amphetamines: a 187 s
    wall run squeezed into 12 s of screen time is a 15x time-lapse, so a
    leisurely 41.7 m-radius turn snaps round in a third of a second, and the
    scene cameras — 900 s into 5-7 s — were running at 110-165x. Motion
    interpolation then made it worse rather than better, because consecutive
    displayed frames were seconds apart in reality and there was no motion to
    compensate: the aircraft smeared into an orange blob.

    A cut shows a MOMENT, at something close to the speed it happened. `at_t`
    is wall-clock (frame mtimes are the only clock the recorders keep, and they
    share it with the mission's own decision log); `lead` puts that moment
    35% of the way in, so the shot arrives before the thing it is about.
    """
    fps = capture_fps(frames)
    need = max(2, int(round(seconds * speed * fps)))
    if at_t is None:
        start = max(0, (len(frames) - need) // 2)
    else:
        mt = [f.stat().st_mtime for f in frames]
        idx = min(range(len(mt)), key=lambda i: abs(mt[i] - at_t))
        start = idx - int(need * lead)
    start = max(0, min(start, len(frames) - need))
    return frames[start:start + need], fps * speed


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
    ap.add_argument("--wall-report", default=None,
                    help="fw_wall_eval report, so the obstacle beat can be cut "
                         "at the moment the aircraft rounded the wall instead "
                         "of wherever the recording happened to be")
    ap.add_argument("--wall-east", type=float, default=246.5,
                    help="east coordinate of the wall plane, for that anchor")
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

    # Scene-camera beats, cut in where the story calls for them. Each is a
    # DIFFERENT camera on the same flight, which is the point: an edit needs
    # coverage, and until now every second of this cut came from one chase rig.
    # Anchors: the wall-clock instants the cut is actually about. Decision
    # timestamps and frame mtimes are the same clock (one machine, one run), so
    # "the frames where it released" is a lookup, not a guess about where in a
    # 900 s recording the interesting bit fell.
    alpha_dec = load_decisions(take, "alpha")

    def moment(kind, which=-1, offset=0.0):
        hits = [d for d in alpha_dec
                if d.get("action", {}).get("action_type") == kind]
        return hits[which]["t"] + offset if hits else None

    # The release happens during the run-in that FOLLOWS the decision, not at
    # the instant of it — ballistics put the bottle away several seconds later.
    t_release = moment("drop_payload", -1, offset=5.0)
    t_transit = alpha_dec[0]["t"] + 12.0 if alpha_dec else None
    t_home = moment("mission_complete", -1, offset=-4.0)
    searches = [d["t"] for d in alpha_dec
                if d.get("action", {}).get("action_type") == "search_area"]
    t_search_lo = min(searches) if searches else None
    t_search_hi = max(searches) if searches else None

    def scene_beat(name, folder, eyebrow, title, body, seconds=7.0, card=True,
                   at=None, speed=1.0, between=None):
        d = take / folder
        frames = sorted(d.glob("*.jpg"))
        if not frames:
            return
        if card:
            add_card(f"{name}_card", title, body, 6.0, eyebrow)
        lead = 0.35
        if at == "auto":
            lo, hi = between or (None, None)
            at = best_window_t(frames, seconds, speed, lo, hi)
            lead = 0.5          # the best window is already centred on itself
            print(f"  {name}: auto-anchored", flush=True)
        chunk, fps = window(frames, at, seconds, speed, lead)
        seq = work / f"seq_{name}"
        if seq.exists():
            shutil.rmtree(seq)
        write_seq([caption_frame(f, [eyebrow], "COMMS: DENIED") for f in chunk], seq)
        segments.append(encode(seq, work / f"{name}.mp4", fps,
                               smooth=not args.draft))

    # 2x on the transits, real time on the delivery. A fixed-wing at 14 m/s is
    # unhurried by nature and a straight cruise leg reads fine slightly quick;
    # the release does not, because it is the one moment where the timing is
    # the point.
    scene_beat("03_wide", "wide", "The area",
               "One tasking. Two aircraft. No link after launch.",
               "The search area sits beyond a 50 m wall. Nobody told the "
               "aircraft the wall was there — the tasking describes the "
               "mission, not the terrain.", seconds=5.0, at=t_transit, speed=2.0)
    # Auto-anchored, and captioned for what this camera can actually show. The
    # aircraft's own transit happened 200 m north of this tripod and reads as
    # four orange pixels; claiming this shot IS the avoidance would be writing
    # for the video. It is the obstacle at its real scale, with the aircraft
    # working beyond it — the avoidance claim is carried by the M6 beat next,
    # where it was scored.
    scene_beat("04_wall", "wall_low", "The obstacle · 50 m of it",
               "What the tasking never mentioned",
               "A 50 m wall stands between the launch point and the search "
               "area. It is not in the tasking, not in a map, and not in any "
               "flight plan — the aircraft has to find it and deal with it.",
               seconds=8.0, at="auto", speed=1.0)

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
            # When it got past the wall, from the guard's 10 Hz track. Without
            # this the beat was cut from wherever the decimation landed — in
            # the first showcase that was the aircraft already 40 m east of the
            # wall and heading away south, under a caption claiming it was
            # routing around an obstacle that was not in the frame.
            t_round = None
            if args.wall_report and Path(args.wall_report).exists():
                rep = json.loads(Path(args.wall_report).read_text())
                idx = wc.name.split("_")[-1]
                run = next((r for r in rep.get("detail", [])
                            if str(r.get("run")) == str(int(idx))
                            if idx.isdigit()), None)
                for p in (run or {}).get("track", []):
                    if p["x"] >= args.wall_east and p.get("t"):
                        t_round = p["t"]
                        break
            chunk, wfps = window(wframes, t_round, 12.0, speed=2.0)
            seq = work / "seq_02b_wall"
            if seq.exists():
                shutil.rmtree(seq)
            write_seq([caption_frame(f, ["OBSTACLE — routed around from the camera alone"],
                                     "COMMS: DENIED") for f in chunk], seq)
            segments.append(encode(seq, work / "02b_wall.mp4", wfps,
                                   smooth=not args.draft))

    for drone in ("alpha", "bravo"):
        chase = take / f"chase_{drone}"
        frames = sorted(chase.glob("*.jpg")) if chase.exists() else []
        decisions = load_decisions(take, drone)
        if not frames or not decisions:
            continue
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
        for i in chosen:
            dec = decisions[i]
            act = dec.get("action", {})
            reasoning = (act.get("reasoning") or "").strip()
            if not reasoning:
                continue
            # Anchored on the decision's own timestamp rather than on
            # frames[i * len(frames) // len(decisions)]. Decisions are 5 to 70 s
            # apart — the even spacing that assumed is not remotely true — so
            # the caption quoting the model's reasoning was drifting away from
            # the footage of it acting on that reasoning.
            chunk, cfps = window(frames, dec.get("t"), args.clip_seconds,
                                 speed=1.0)
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
                                   cfps, smooth=not args.draft))

    # The search, before the delivery it leads to. Bounded to the search phase
    # so the shot and the caption agree, then auto-anchored inside it — this is
    # the one scene camera that rides close enough for the aircraft to read as
    # an aircraft rather than a speck.
    scene_beat("09_search", "orbit", "Searching · no link, no operator",
               "Nobody is flying this",
               "Eleven search legs, each one chosen on board from the previous "
               "camera frame. There is no route uplinked and nothing to ask.",
               seconds=7.0, at="auto", speed=1.0,
               between=(t_search_lo, t_search_hi))
    scene_beat("11_hero", "hero", "Delivery",
               "The bottle lands 1.9 m from where it was aimed",
               "Release range is computed from altitude and airspeed. The "
               "model decides whether and at whom; the ballistics decide when "
               "to let go.", seconds=7.0, at=t_release, speed=1.0)
    scene_beat("12_orbit", "orbit", "Return", "", "", seconds=5.0, card=False,
               at=t_home, speed=1.5)

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
