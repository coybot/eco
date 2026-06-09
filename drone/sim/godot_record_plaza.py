#!/usr/bin/env python3
"""Record a plaza demo: 3 quads + 2 rovers inspecting scattered chairs.

Connects to the Godot IPC TCP port (9999) and drives the fleet through a
short inspection sweep while capturing per-vehicle camera frames, then encodes
one MP4 per vehicle with ffmpeg.

Scenario: "count the chairs in the plaza". Chairs are scattered around
ENU (x:-9..9, y:4..22). Vehicles spawn north at ENU y=28 facing -Y (south)
toward the cluster.

Usage (on hoopoe): python3 godot_record_plaza.py --out /tmp/plaza_demo
"""
import argparse
import base64
import json
import os
import socket
import subprocess
import time

FPS = 12
FRAME_DT = 1.0 / FPS

QUADS = ["sim-quadcopter-01", "sim-quadcopter-02", "sim-quadcopter-03"]
ROVERS = ["sim-rover-04", "sim-rover-05"]


def call(s, req):
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(131072)
        if not chunk:
            break
        buf += chunk
    return json.loads(buf.split(b"\n")[0])


def grab(s, vid):
    r = call(s, {"op": "grab_frame", "id": vid})
    b64 = r.get("jpg")
    return base64.b64decode(b64) if b64 and len(b64) > 10 else None


def record_phase(s, vids, duration_s, frames):
    t_end = time.time() + duration_s
    while time.time() < t_end:
        t0 = time.time()
        for vid in vids:
            f = grab(s, vid)
            if f:
                frames[vid].append(f)
        time.sleep(max(0.0, FRAME_DT - (time.time() - t0)))


def frames_to_mp4(frames, out_path):
    if not frames:
        print(f"  [!] no frames for {out_path}")
        return
    cmd = [
        "ffmpeg", "-y",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-r", str(FPS), "-i", "pipe:0",
        "-vcodec", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for f in frames:
        proc.stdin.write(f)
    proc.stdin.close()
    proc.wait()
    sz = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    print(f"  saved {out_path} ({len(frames)} frames, {sz//1024} KB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--out", default="/tmp/plaza_demo")
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((args.host, args.port))
    s.settimeout(15)

    present = []
    for vid in QUADS + ROVERS:
        if call(s, {"op": "get_state", "id": vid}).get("ok"):
            present.append(vid)
    quads = [v for v in QUADS if v in present]
    rovers = [v for v in ROVERS if v in present]
    print(f"Fleet present: {present}")

    frames = {v: [] for v in present}

    # Phase 1: hold at spawn, survey the plaza (4s).
    print("Phase 1: survey...")
    record_phase(s, present, 4.0, frames)

    # Phase 2: advance south into the chair cluster (10s).
    # Quads descend to 6m and move to y=16 (over the chairs). Rovers drive to y=14.
    print("Phase 2: approach chairs...")
    for i, qid in enumerate(quads):
        x = (i - (len(quads) - 1) * 0.5) * 7.0
        call(s, {"op": "set_goal", "id": qid, "p": [x, 16.0, 6.0]})
    for i, rid in enumerate(rovers):
        x = (i - (len(rovers) - 1) * 0.5) * 6.0
        call(s, {"op": "set_goal", "id": rid, "p": [x, 14.0, 0.0]})
    record_phase(s, present, 10.0, frames)

    # Phase 3: lateral sweep across the plaza (10s).
    print("Phase 3: lateral sweep...")
    for i, qid in enumerate(quads):
        x = [-8.0, 0.0, 8.0][i % 3]
        call(s, {"op": "set_goal", "id": qid, "p": [x, 10.0, 6.0]})
    for i, rid in enumerate(rovers):
        x = [6.0, -6.0][i % 2]
        call(s, {"op": "set_goal", "id": rid, "p": [x, 8.0, 0.0]})
    record_phase(s, present, 10.0, frames)

    s.close()

    print("Encoding...")
    os.makedirs(args.out, exist_ok=True)
    for vid, flist in frames.items():
        frames_to_mp4(flist, os.path.join(args.out, vid.replace("-", "_") + ".mp4"))
    print(f"Done. Videos in {args.out}")
    os.system(f"ls -lh {args.out}")


if __name__ == "__main__":
    main()
