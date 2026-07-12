#!/usr/bin/env python3
"""Phase 1 smoke video: proves the Depot sim renders and reacts to injects.

Not the real capstone video (that's Phase 2+, with the Swift MissionAgent driving
and caption overlays from the event log) — this is a scripted drive-and-inject
sequence recorded from the overhead vantage, to visually confirm the Godot side
of the sim before any Swift/brain integration exists.

Usage: python3 make_smoke_video.py [--seed N] [--out PATH]
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from depot_client import DepotClient
from godot_launcher import launch_depot

RID = "video-1"
FPS = 5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "videos_phrover" / "smoke_test.mp4"))
    args = ap.parse_args()

    proc = launch_depot(seed=args.seed, gui=True)
    time.sleep(1.0)
    client = DepotClient()
    client.reset(seed=args.seed)
    client.spawn(RID, (0.0, 1.0), yaw=math.pi / 2)
    client.add_vantage("overhead", (0.0, -4.0, 18.0), (0.0, 5.0, 0.0))
    time.sleep(0.3)

    frame_dir = Path(tempfile.mkdtemp(prefix="depot_smoke_frames_"))
    print(f"capturing frames to {frame_dir}")
    frame_i = 0

    def capture(seconds: float, v: float = 0.0, w: float = 0.0) -> None:
        nonlocal frame_i
        steps = int(seconds * FPS)
        for _ in range(steps):
            client.drive(RID, v, w)
            jpg = client.grab_vantage("overhead")
            if jpg:
                (frame_dir / f"f{frame_i:05d}.jpg").write_bytes(jpg)
                frame_i += 1
            time.sleep(1.0 / FPS)
        client.stop(RID)

    try:
        capture(1.0)                              # establishing shot
        client.inject("person_walk", on=True)
        capture(3.0, v=0.35)                       # drive up the hallway, person visible
        client.inject("block_door", door="A")
        capture(1.0)                               # show the door close
        capture(2.0, v=0.0, w=0.6)                 # turn to look around
        client.inject("tip_ladder")
        client.inject("camera_blur", sigma=2.0)
        capture(2.0, v=0.2)
        client.inject("kill_rover", id=RID)
        capture(1.0)                               # aftermath frame(s)
    finally:
        client.close()
        proc.stop()

    if frame_i == 0:
        print("no frames captured", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-framerate", str(FPS), "-i", str(frame_dir / "f%05d.jpg"),
        "-vf", "scale=1280:720", "-pix_fmt", "yuv420p", str(out_path),
    ]
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    shutil.rmtree(frame_dir, ignore_errors=True)
    print(f"wrote {out_path} ({frame_i} frames @ {FPS}fps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
