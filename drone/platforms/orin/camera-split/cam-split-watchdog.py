#!/usr/bin/env python3
"""Restart cam-split when its camera feed freezes.

cam-split's ffmpeg copies the D435I colour stream (/dev/d435i-color) onto the
v4l2loopback nodes the aircraft reads (/dev/eco-stream, /dev/eco-color,
/dev/eco-watch). When the camera stops delivering, ffmpeg does not exit: it
keeps writing duplicates of its last frame (or nothing), so systemd's
Restart=always never fires. Seen on 2026-09-26: the feed froze ~47 min after
boot and every photo after that failed with "started but delivered no frame"
until cam-split was restarted.

A live sensor never produces byte-identical consecutive frames, so no frames
or identical frames over ~2 s means the feed is frozen.

It reads /dev/eco-watch and nothing else, on purpose. Opened and closed every
30 s on a node the drone is also reading, it made the drone's OpenCV reader
stop receiving frames: on the quadcopter, 7 to 13 of 30 reads came back empty
whenever the two overlapped, and about one photo in five failed with "the
camera produced no image". A node of its own removed the overlap (30 of 30).
"""
import hashlib, os, subprocess, sys, time
import cv2

DEVICE = "/dev/eco-watch"
STAMP = "/run/cam-split-watchdog.last"
MIN_GAP_S = 120

cap = cv2.VideoCapture(DEVICE, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000) if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC") else None
digests = []
for _ in range(4):
    ok, frame = cap.read()
    digests.append(hashlib.md5(frame.tobytes()).hexdigest() if ok and frame is not None else None)
    time.sleep(0.6)
cap.release()

live = [d for d in digests if d]
if len(set(live)) >= 2:
    sys.exit(0)
why = "no frames" if not live else "identical frames"
try:
    if time.time() - os.path.getmtime(STAMP) < MIN_GAP_S:
        print(f"feed frozen ({why}); restarted recently, waiting")
        sys.exit(0)
except OSError:
    pass
open(STAMP, "w").close()
print(f"feed frozen ({why}) on {DEVICE}: restarting cam-split")
subprocess.run(["systemctl", "restart", "cam-split"], check=False)
