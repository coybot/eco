"""Pinhole camera model for the follow bench. Units: metres, radians, normalized image
coordinates u (0 = left, 1 = right) and v (0 = bottom, 1 = top), matching Vision's origin.

ASSUMPTION (stated in the report): the phone is portrait, mounted `height` m above the
floor, tilted up by `pitch_deg`. The default 0.30 m / 15 deg is the mount at which today's
body check (needs shoulders AND hips in frame) works at the 1.5 m stand-off, which is what
the "kind of works" description implies. `sweep` varies it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

Pose = tuple[float, float, float]  # x, y (ENU m), yaw (rad CCW from east)


@dataclass(frozen=True)
class Camera:
    height: float = 0.30
    pitch_deg: float = 15.0
    hfov_deg: float = 50.0   # across the short side (portrait)
    vfov_deg: float = 63.0   # along the long side (portrait)

    @property
    def th(self) -> float:
        return math.tan(math.radians(self.hfov_deg) / 2)

    @property
    def tv(self) -> float:
        return math.tan(math.radians(self.vfov_deg) / 2)

    def project(self, pose: Pose, x: float, y: float, z: float):
        """-> (u, v, z_depth) or None when behind the image plane."""
        px, py, yaw = pose
        rx, ry = x - px, y - py
        f = rx * math.cos(yaw) + ry * math.sin(yaw)
        l = -rx * math.sin(yaw) + ry * math.cos(yaw)
        dz = z - self.height
        p = math.radians(self.pitch_deg)
        fwd = f * math.cos(p) + dz * math.sin(p)
        up = -f * math.sin(p) + dz * math.cos(p)
        if fwd <= 0.05:
            return None
        return (0.5 + 0.5 * (-l / fwd) / self.th, 0.5 + 0.5 * (up / fwd) / self.tv, fwd)

    def ray(self, u: float, v: float):
        """Rover-frame (forward, left, up) of the ray through (u, v), camera z-depth = 1."""
        a = (u - 0.5) * 2 * self.th
        b = (v - 0.5) * 2 * self.tv
        p = math.radians(self.pitch_deg)
        return math.cos(p) - b * math.sin(p), -a, math.sin(p) + b * math.cos(p)

    def to_world(self, pose: Pose, u: float, v: float, z_depth: float):
        """World (x, y) of the point at pixel (u, v) whose camera z-depth is z_depth."""
        f, l, _ = self.ray(u, v)
        f, l = f * z_depth, l * z_depth
        px, py, yaw = pose
        return (px + f * math.cos(yaw) - l * math.sin(yaw), py + f * math.sin(yaw) + l * math.cos(yaw))

    def bearing(self, u: float, v: float) -> float:
        """Rover-frame bearing (rad, +left) of the ray through (u, v)."""
        f, l, _ = self.ray(u, v)
        return math.atan2(l, f)

    @staticmethod
    def in_frame(u: float, v: float) -> bool:
        return 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0
