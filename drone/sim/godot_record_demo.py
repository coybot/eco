#!/usr/bin/env python3
"""
Record a demo video of the Godot fleet.

Connects directly to the Godot IPC TCP port (9999 on hoopoe).
Sends fleet on a patrol around the building facade, captures frames
at 15 fps, then encodes MP4s with ffmpeg.

Usage (run on hoopoe or via SSH tunnel):
    python3 godot_record_demo.py [--host 127.0.0.1] [--port 9999] [--out /tmp/demo]
"""
import argparse
import base64
import json
import os
import socket
import subprocess
import time


FPS = 15
FRAME_DT = 1.0 / FPS


def call(s: socket.socket, req: dict) -> dict:
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(131072)
        if not chunk:
            break
        buf += chunk
    return json.loads(buf.split(b"\n")[0])


def grab_frame(s, drone_id):
    r = call(s, {"op": "grab_frame", "id": drone_id})
    b64 = r.get("jpg")
    return base64.b64decode(b64) if b64 else None


def grab_vantage(s, name):
    r = call(s, {"op": "grab_vantage", "name": name})
    b64 = r.get("jpg")
    return base64.b64decode(b64) if b64 else None


def frames_to_mp4(frames, out_path, fps=FPS):
    if not frames:
        print(f"  [!] no frames for {out_path}, skipping")
        return
    cmd = [
        "ffmpeg", "-y",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-r", str(fps), "-i", "pipe:0",
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


def record_phase(s, vids, vantage_name, duration_s, frames):
    t_end = time.time() + duration_s
    while time.time() < t_end:
        t0 = time.time()
        for vid in vids:
            f = grab_frame(s, vid)
            if f:
                frames[vid].append(f)
        fv = grab_vantage(s, vantage_name)
        if fv:
            frames[vantage_name].append(fv)
        elapsed = time.time() - t0
        time.sleep(max(0.0, FRAME_DT - elapsed))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--out", default="/tmp/presidio_demo")
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((args.host, args.port))
    s.settimeout(10)

    # Discover which vehicle IDs are present
    quads = ["sim-quadcopter-01", "sim-quadcopter-02", "sim-quadcopter-03"]
    rovers = ["sim-rover-04", "sim-rover-05"]
    all_v = []
    for vid in quads + rovers:
        r = call(s, {"op": "get_state", "id": vid})
        if r.get("ok"):
            all_v.append(vid)
            print(f"  found {vid} at {r['position']}")
        else:
            print(f"  (skip {vid}: not in fleet)")

    vantage = "overhead"
    print(f"Setting up vantage '{vantage}'...")
    call(s, {"op": "auto_overhead", "name": vantage})
    time.sleep(2)

    frames: dict = {v: [] for v in all_v}
    frames[vantage] = []

    # --- Phase 1: Spread across facade at distance ---
    print("Phase 1: spread across facade...")
    active_quads = [v for v in quads if v in all_v]
    active_rovers = [v for v in rovers if v in all_v]
    n_q = len(active_quads)
    for i, qid in enumerate(active_quads):
        x = (i - (n_q - 1) * 0.5) * 30.0
        call(s, {"op": "set_goal", "id": qid, "p": [x, 200.0, 45.0]})
    for i, rid in enumerate(active_rovers):
        x = (i - (len(active_rovers) - 1) * 0.5) * 25.0
        call(s, {"op": "set_goal", "id": rid, "p": [x, 200.0, 3.0]})
    record_phase(s, all_v, vantage, 6.0, frames)

    # --- Phase 2: Approach building ---
    print("Phase 2: approach lobby...")
    for i, qid in enumerate(active_quads):
        x = (i - (n_q - 1) * 0.5) * 25.0
        call(s, {"op": "set_goal", "id": qid, "p": [x, 130.0, 50.0]})
    for i, rid in enumerate(active_rovers):
        x = (i - (len(active_rovers) - 1) * 0.5) * 20.0
        call(s, {"op": "set_goal", "id": rid, "p": [x, 130.0, 3.0]})
    record_phase(s, all_v, vantage, 8.0, frames)

    # --- Phase 3: Fan out sweep ---
    print("Phase 3: facade sweep...")
    sweep_x = [-50.0, 0.0, 50.0]
    for i, qid in enumerate(active_quads):
        call(s, {"op": "set_goal", "id": qid, "p": [sweep_x[i % 3], 125.0, 60.0]})
    record_phase(s, all_v, vantage, 7.0, frames)

    s.close()

    print("Encoding videos...")
    os.makedirs(args.out, exist_ok=True)
    for vid, flist in frames.items():
        out = os.path.join(args.out, vid.replace("-", "_") + ".mp4")
        frames_to_mp4(flist, out, FPS)

    print(f"\nDone. Videos in {args.out}:")
    os.system(f"ls -lh {args.out}")


if __name__ == "__main__":
    main()
