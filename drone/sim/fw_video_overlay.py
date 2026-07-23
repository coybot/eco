"""Overlay renderer for fw_eval.py's Reacquire scenario recording.

Draws a top-left caption block (leg, position, current detections) and a
top-right minimap (ground-truth prop pins + the remembered memory landmark
pin + aircraft position) onto each captured vantage frame, then encodes to mp4
via ffmpeg — same base pipeline as rover/sim/make_smoke_video.py (capture JPEGs
to a temp dir, ffmpeg -framerate ... -i frame%05d.jpg ...).

Deliberately does NOT attempt to project 3D world points into the vantage
camera's own pixel space (would require reproducing Godot's Camera3D view/
projection math by hand) — the minimap is a separate flat top-down 2D panel,
not a 3D-projected overlay on the vantage footage itself.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FPS = 5
MINIMAP_SIZE = 220
# World bounds covering env_flightline.gd's prop spread (water_tower/silo/barn).
WORLD_X = (-20.0, 360.0)
WORLD_Y = (-110.0, 110.0)


def _load_font(size: int = 16) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", size)
    except Exception:
        return ImageFont.load_default()


def _to_px(wx: float, wy: float) -> tuple[float, float]:
    px = (wx - WORLD_X[0]) / (WORLD_X[1] - WORLD_X[0]) * MINIMAP_SIZE
    py = MINIMAP_SIZE - (wy - WORLD_Y[0]) / (WORLD_Y[1] - WORLD_Y[0]) * MINIMAP_SIZE
    return px, py


def _draw_minimap(tick: dict, ground_truth: dict, landmark: dict | None,
                  wall: dict | None = None) -> Image.Image:
    img = Image.new("RGBA", (MINIMAP_SIZE, MINIMAP_SIZE), (10, 15, 20, 220))
    draw = ImageDraw.Draw(img)
    font = _load_font(11)

    for label, world in ground_truth.items():
        px, py = _to_px(world[0], world[1])
        draw.ellipse([px - 4, py - 4, px + 4, py + 4], outline=(200, 200, 60, 255), width=2)
        draw.text((px + 6, py - 6), label, fill=(200, 200, 60, 255), font=font)

    if wall is not None:
        cx, cy = wall["center"]
        hx, hy = wall["half"]
        x0, y0 = _to_px(cx - hx, cy - hy)
        x1, y1 = _to_px(cx + hx, cy + hy)
        draw.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                       outline=(220, 60, 60, 255), width=2)
        draw.text((min(x0, x1) + 3, min(y0, y1) - 12), "wall", fill=(220, 60, 60, 255), font=font)

    if landmark is not None and tick.get("memory_known"):
        px, py = _to_px(landmark["x"], landmark["y"])
        draw.ellipse([px - 6, py - 6, px + 6, py + 6], outline=(80, 220, 255, 255), width=3)
        draw.text((px + 8, py + 4), "memory", fill=(80, 220, 255, 255), font=font)

    ax, ay = tick["pos"][0], tick["pos"][1]
    px, py = _to_px(ax, ay)
    color = (255, 90, 90, 255) if tick["leg"] == "outbound" else (90, 255, 130, 255)
    draw.polygon([(px, py - 7), (px - 5, py + 5), (px + 5, py + 5)], fill=color)

    return img


def render_overlay_video(recorder, result: dict, out_path: str) -> None:
    frame_dir = Path(tempfile.mkdtemp(prefix="fw_eval_frames_"))
    font = _load_font(16)
    ground_truth = result["ground_truth"]
    landmark = result.get("memory_landmark")
    wall = result.get("wall")
    n = 0
    try:
        for tick in recorder.ticks:
            if tick["jpg"] is None:
                continue
            img = Image.open(io.BytesIO(tick["jpg"])).convert("RGBA")
            draw = ImageDraw.Draw(img)

            cap_lines = [
                f"leg: {tick['leg']}",
                f"pos: ({tick['pos'][0]:.0f}, {tick['pos'][1]:.0f}, {tick['pos'][2]:.0f})",
            ]
            for det in tick["detections"]:
                cap_lines.append(f"  see: {det['label']} ({det['score']:.0%})")
            if not tick["detections"]:
                cap_lines.append("  see: (nothing in range/FOV)")
            pad = 8
            box_h = 22 * len(cap_lines) + pad * 2
            draw.rectangle([0, 0, 340, box_h], fill=(0, 0, 0, 160))
            for i, line in enumerate(cap_lines):
                draw.text((pad, pad + i * 22), line, fill=(255, 255, 255, 255), font=font)

            minimap = _draw_minimap(tick, ground_truth, landmark, wall)
            img.paste(minimap, (img.width - MINIMAP_SIZE - 10, 10), minimap)

            img.convert("RGB").save(frame_dir / f"f{n:05d}.jpg", quality=88)
            n += 1

        if n == 0:
            raise RuntimeError("no frames captured — nothing to render")

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y", "-framerate", str(FPS), "-i", str(frame_dir / "f%05d.jpg"),
            "-vf", "scale=1280:720", "-pix_fmt", "yuv420p", str(out),
        ]
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)
