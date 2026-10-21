"""Synthetic downward camera for SITL: renders a crop field seen from the aircraft's
real position and heading (read from the flight controller via drone_sdk._photo_fix).

cloud_sim_drone.py --prepare installs this as camera.py in the sim drone's directory, in
place of the RealSense driver, so the rest of the capture path (drone_sdk.capture_photo,
EXIF, upload to S3, the album in the app) is the aircraft's own.

The only fake in the capture path. Frames are a 1 m ground grid over green crop rows with
four yellow-brown 'diseased' patches at fixed ground positions, so a set of survey photos
visibly tiles into one field and a patch can be tracked from image to image.
"""
import math
import types

import cv2
import numpy as np

HOME = (-35.363261, 149.165230)   # ArduPilot SITL home (CMAC)
M_PER_DEG = 111319.5
RES = 0.02                        # metres per field-map pixel
N0, N1, E0, E1 = -10.0, 30.0, -12.0, 17.0   # field extent (north, east), metres
PATCHES = [(3.2, 1.4, 0.9), (6.8, 3.6, 0.7), (8.9, 0.9, 0.6), (1.2, 4.1, 0.5)]  # north, east, radius

_field = None


def _build():
    rng = np.random.RandomState(7)
    h, w = int((N1 - N0) / RES), int((E1 - E0) / RES)
    img = np.zeros((h, w, 3), np.float32)
    ee = (np.arange(w) * RES + E0)[None, :]
    nn = (N1 - np.arange(h) * RES)[:, None]
    rows = 0.5 + 0.5 * np.cos(2 * math.pi * ee / 0.75)          # crop rows run north-south
    soil = np.array([60, 80, 105], np.float32)                   # BGR brown
    leaf = np.array([40, 140, 60], np.float32)                   # BGR green
    mix = (rows > 0.35).astype(np.float32)[..., None] * np.ones((h, 1, 1), np.float32)
    img = soil * (1 - mix) + leaf * mix
    img += rng.normal(0, 9, img.shape)
    for (pn, pe, r) in PATCHES:                                   # diseased patches
        d = np.hypot(nn - pn, ee - pe)
        m = np.clip((r - d) / 0.12, 0, 1)[..., None]
        sick = np.array([40, 170, 200], np.float32) + rng.normal(0, 14, (h, w, 3))   # BGR yellow
        img = img * (1 - m) + sick * m
    img = np.clip(img, 0, 255).astype(np.uint8)
    # 1 m ground grid with metre labels at 2 m intersections
    for n in range(int(N0), int(N1) + 1):
        y = int((N1 - n) / RES)
        img[max(y - 1, 0):y + 1, :] = (img[max(y - 1, 0):y + 1, :] * 0.5 + 255 * 0.5).astype(np.uint8)
    for e in range(int(E0), int(E1) + 1):
        x = int((e - E0) / RES)
        img[:, max(x - 1, 0):x + 1] = (img[:, max(x - 1, 0):x + 1] * 0.5 + 255 * 0.5).astype(np.uint8)
    for n in range(int(N0), int(N1) + 1, 2):
        for e in range(int(E0), int(E1) + 1, 2):
            cv2.putText(img, f"{n},{e}", (int((e - E0) / RES) + 4, int((N1 - n) / RES) - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return img


class SyntheticCamera:
    CAMERA_TYPE = 'synthetic-sitl'
    W, H = 1280, 960
    HFOV_M_PER_M = 1.0            # ground width per metre of height (~53 deg HFOV)

    def get_frame(self, timeout_ms=0):
        global _field
        import drone_sdk
        if _field is None:
            _field = _build()
        fix = drone_sdk._photo_fix(max_age=2.0)
        if fix is None:
            return None
        north = (fix['lat'] - HOME[0]) * M_PER_DEG
        east = (fix['lon'] - HOME[1]) * M_PER_DEG * math.cos(math.radians(HOME[0]))
        hdg = math.radians(fix['heading_deg'] or 0.0)
        alt = max(fix['alt_above_home_m'], 1.0)
        mpp = (alt * self.HFOV_M_PER_M) / self.W
        u, v = np.meshgrid(np.arange(self.W), np.arange(self.H))
        right = (u - self.W / 2) * mpp
        fwd = (self.H / 2 - v) * mpp
        east_g = east + right * math.cos(hdg) + fwd * math.sin(hdg)
        north_g = north - right * math.sin(hdg) + fwd * math.cos(hdg)
        mx = ((east_g - E0) / RES).astype(np.float32)
        my = ((N1 - north_g) / RES).astype(np.float32)
        bgr = cv2.remap(_field, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(30, 30, 30))
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return types.SimpleNamespace(rgb=rgb)

    def release(self):
        pass

    def stop(self):
        pass

    def start(self):
        pass


def get_camera(*a, **k):
    return SyntheticCamera()


def list_available_cameras():
    return [{'name': 'synthetic-sitl', 'type': 'synthetic'}]


def last_camera_error():
    return None
