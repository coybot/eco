#!/usr/bin/env python3
"""One-shot: 'where is the person, where is the floor?'

Grabs a single aligned RGB+depth frame from D435i, runs GDINO for a list of
queries, reports each detection's 3D position in body frame (forward/left/up),
plus depth probes at the image center and floor row. Saves annotated JPEG.

Usage:
  ./venv/bin/python where.py
  ./venv/bin/python where.py person floor chair
  ./venv/bin/python where.py --out /tmp/where.jpg person
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.resolve()))
from grounding import GroundingDINO  # noqa: E402


def grab_one():
    """Grab one aligned RGB + depth frame. Returns (rgb, depth_u16, scale, intr)."""
    import pyrealsense2 as rs
    pipe = rs.pipeline()
    cfg = rs.config()
    cfg.enable_stream(rs.stream.color, 640, 480, rs.format.rgb8, 30)
    cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    profile = pipe.start(cfg)
    align = rs.align(rs.stream.color)
    scale = float(profile.get_device().first_depth_sensor().get_depth_scale())
    intr = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
    for _ in range(15):
        pipe.wait_for_frames(5000)
    frames = align.process(pipe.wait_for_frames(5000))
    rgb = np.asanyarray(frames.get_color_frame().get_data())
    depth = np.asanyarray(frames.get_depth_frame().get_data())
    pipe.stop()
    return rgb, depth, scale, intr


def depth_at(d: np.ndarray, scale: float, x: int, y: int, win: int = 5) -> Optional[float]:
    h, w = d.shape
    x0, x1 = max(0, x - win), min(w, x + win + 1)
    y0, y1 = max(0, y - win), min(h, y + win + 1)
    vals = d[y0:y1, x0:x1]
    vals = vals[vals > 0]
    if vals.size == 0:
        return None
    return float(np.median(vals)) * scale


def backproject(x: int, y: int, z_m: float, intr):
    """Pixel + depth -> body frame (forward, left, up), meters."""
    X = (x - intr.ppx) / intr.fx * z_m
    Y = (y - intr.ppy) / intr.fy * z_m
    return (z_m, -X, -Y)


def fmt_body(fwd: float, left: float, up: float) -> str:
    side = "left" if left >= 0 else "right"
    vert = "up" if up >= 0 else "down"
    return f"{fwd:.2f}m forward, {abs(left):.2f}m {side}, {abs(up):.2f}m {vert}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="*", default=["person", "floor"],
                    help="GDINO queries (default: person floor)")
    ap.add_argument("--out", default="/tmp/where.jpg",
                    help="Annotated JPEG output path")
    ap.add_argument("--box-threshold", type=float, default=0.25)
    ap.add_argument("--text-threshold", type=float, default=0.25)
    ap.add_argument("--top-k", type=int, default=3,
                    help="Print top-K detections per label")
    args = ap.parse_args()

    print(f"grabbing frame from D435i...")
    rgb, depth, scale, intr = grab_one()
    h, w = depth.shape
    print(f"frame {rgb.shape} depth {depth.shape}  scale={scale}m/unit  "
          f"fx={intr.fx:.1f} fy={intr.fy:.1f} ppx={intr.ppx:.1f} ppy={intr.ppy:.1f}")

    print(f"loading GroundingDINO (cold start if first run)...")
    gd = GroundingDINO()
    gd._lazy_load()

    dets = gd.detect(rgb, args.queries,
                     box_threshold=args.box_threshold,
                     text_threshold=args.text_threshold)

    # Group detections by which query they match
    labels = sorted({d.label for d in dets} | set(q.lower() for q in args.queries))
    for query in args.queries:
        q = query.lower()
        group = [d for d in dets if q in d.label.lower() or d.label.lower() in q]
        print(f"\n=== {query!r}: {len(group)} detection(s) ===")
        if not group:
            print("  (none)")
            continue
        for d in sorted(group, key=lambda d: -d.score)[:args.top_k]:
            cx, cy = d.center
            x1, y1, x2, y2 = [int(v) for v in d.bbox]
            z = depth_at(depth, scale, cx, cy)
            print(f"  label={d.label!r} score={d.score:.2f}  bbox=({x1},{y1})-({x2},{y2})  center=({cx},{cy})")
            if z is None:
                # Try median depth over the whole bbox (helps for 'floor' which
                # tiles the lower half, center may hit a leg/foot).
                bb = depth[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
                bb = bb[bb > 0]
                if bb.size == 0:
                    print(f"     depth: NO DATA anywhere in bbox")
                    continue
                z = float(np.median(bb)) * scale
                print(f"     depth@center: no data -> using bbox-median={z:.2f}m")
            fwd, left, up = backproject(cx, cy, z, intr)
            print(f"     depth@center: {z:.2f}m  ->  {fmt_body(fwd, left, up)}")

    # Depth probes: standard reference points in the frame
    probes = {
        "center (straight ahead)":    (w // 2, h // 2),
        "floor row (90% down)":       (w // 2, int(h * 0.90)),
        "ceiling row (10% down)":     (w // 2, int(h * 0.10)),
        "1m-ahead floor (est. 80%)":  (w // 2, int(h * 0.80)),
    }
    print(f"\n=== depth probes ===")
    for name, (x, y) in probes.items():
        z = depth_at(depth, scale, x, y)
        if z is None:
            print(f"  {name} @ ({x},{y}): NO DATA")
        else:
            fwd, left, up = backproject(x, y, z, intr)
            print(f"  {name} @ ({x},{y}): depth={z:.2f}m  ->  {fmt_body(fwd, left, up)}")

    # Save annotated image
    try:
        import cv2
        bgr = rgb[:, :, ::-1].copy()
        # Draw probe markers (cyan)
        for name, (x, y) in probes.items():
            cv2.circle(bgr, (x, y), 4, (255, 255, 0), -1)
        # Draw detections (green)
        for d in dets:
            x1, y1, x2, y2 = [int(v) for v in d.bbox]
            cv2.rectangle(bgr, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(bgr, f"{d.label} {d.score:.2f}", (x1, max(20, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.imwrite(args.out, bgr)
        print(f"\nannotated image -> {args.out}")
    except Exception as e:
        print(f"\ncv2 save failed: {e}")


if __name__ == "__main__":
    main()
