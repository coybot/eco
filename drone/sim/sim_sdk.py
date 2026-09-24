"""Eco sim SDK helpers: GPS <-> local ENU, and the video FrameBus.

The sim world is metric and local (x=east, y=north, z=up) while the cloud talks
GPS, so an arbitrary home is anchored here and converted around.

Consumed by ``sim_control.py``, ``fleet_worker.py`` and
``sim_drone_daemon.py``.
"""

from __future__ import annotations

import math
import threading
from typing import Optional

import numpy as np

# --- GPS <-> local ENU mapping -------------------------------------------------
# The sim world is metric/local (x=east, y=north, z=up). The cloud talks GPS, so
# we anchor an arbitrary home and convert. The prompt's "+0.00001 deg ~= 1.1m"
# convention falls out of this (1 deg lat ~= 111320 m).
HOME_LAT = 37.0
HOME_LON = -122.0
_M_PER_DEG = 111319.9


def latlon_to_xy(lat: float, lon: float) -> tuple[float, float]:
    east = (lon - HOME_LON) * _M_PER_DEG * math.cos(math.radians(HOME_LAT))
    north = (lat - HOME_LAT) * _M_PER_DEG
    return east, north  # x, y


def xy_to_latlon(x: float, y: float) -> tuple[float, float]:
    lat = HOME_LAT + y / _M_PER_DEG
    lon = HOME_LON + x / (_M_PER_DEG * math.cos(math.radians(HOME_LAT)))
    return lat, lon


class FrameBus:
    """Thread-safe holder for the latest RGB frame (H, W, 3) uint8."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None

    def set(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame

    def get(self) -> Optional[np.ndarray]:
        with self._lock:
            return None if self._frame is None else self._frame.copy()
