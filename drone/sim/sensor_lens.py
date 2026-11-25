"""The Godot sim's camera lens, image <-> pinhole (drone/sim/godot/scripts/sensor_fx.gd).

With the sim's camera model on (fw_set_sensor {"realism": "on"}), a frame is
the pinhole image moved by radial distortion and a little jitter, and the frame's
reply carries a "lens" record. A client doing its own pinhole geometry from K
must first take a pixel back to the pinhole image with to_pinhole(). Without a lens record both functions are the
identity. Same maths as SensorFx.to_image()/to_pinhole(); keep them in step.
"""
from __future__ import annotations

import math


def _fill(k1: float, k2: float) -> float:
    return 1.0 / (1.0 + k1 + k2)


def to_image(nx: float, ny: float, lens: dict | None) -> tuple[float, float]:
    """Pinhole image point (0..1, top-left origin) -> where it lands in the frame."""
    if not lens:
        return nx, ny
    a = float(lens["aspect"])
    d = 0.5 * math.sqrt(a * a + 1.0)
    qx, qy = (nx - 0.5) * a, ny - 0.5
    c, s = math.cos(float(lens["roll"])), math.sin(float(lens["roll"]))
    qx, qy = c * qx - s * qy + float(lens["jx"]), s * qx + c * qy + float(lens["jy"])
    ux, uy = qx / d, qy / d
    r2 = ux * ux + uy * uy
    k1, k2 = float(lens["k1"]), float(lens["k2"])
    m = (1.0 + k1 * r2 + k2 * r2 * r2) * _fill(k1, k2) * d
    return ux * m / a + 0.5, uy * m + 0.5


def to_pinhole(nx: float, ny: float, lens: dict | None) -> tuple[float, float]:
    """A point in the frame -> the pinhole image point it shows."""
    if not lens:
        return nx, ny
    a = float(lens["aspect"])
    d = 0.5 * math.sqrt(a * a + 1.0)
    k1, k2 = float(lens["k1"]), float(lens["k2"])
    xd, yd = (nx - 0.5) * a / d, (ny - 0.5) / d
    ux, uy = xd, yd
    for _ in range(8):
        r2 = ux * ux + uy * uy
        k = (1.0 + k1 * r2 + k2 * r2 * r2) * _fill(k1, k2)
        ux, uy = xd / k, yd / k
    tx, ty = ux * d - float(lens["jx"]), uy * d - float(lens["jy"])
    c, s = math.cos(-float(lens["roll"])), math.sin(-float(lens["roll"]))
    qx, qy = c * tx - s * ty, s * tx + c * ty
    return qx / a + 0.5, qy + 0.5


def pixel_to_pinhole(u: float, v: float, width: float, height: float, lens: dict | None) -> tuple[float, float]:
    """to_pinhole() in pixels."""
    nx, ny = to_pinhole(u / width, v / height, lens)
    return nx * width, ny * height
