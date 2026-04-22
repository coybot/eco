"""Spatial memory: EMA-smoothed 3D landmark store (per the papers, 1.5m merge radius)."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import math


@dataclass
class Landmark:
    label: str
    x: float  # meters, body frame (forward)
    y: float  # meters, body frame (left)
    z: float  # meters (down-positive or range; we treat as forward distance)
    score: float
    hits: int = 1


class SpatialMemory:
    """Per-label EMA store, merge radius 1.5 m."""

    def __init__(self, merge_radius: float = 1.5, alpha: float = 0.4):
        self.merge_radius = merge_radius
        self.alpha = alpha
        self._store: Dict[str, List[Landmark]] = {}

    def update(self, label: str, x: float, y: float, z: float, score: float) -> Landmark:
        bucket = self._store.setdefault(label, [])
        for lm in bucket:
            if math.dist((lm.x, lm.y, lm.z), (x, y, z)) <= self.merge_radius:
                a = self.alpha
                lm.x = (1 - a) * lm.x + a * x
                lm.y = (1 - a) * lm.y + a * y
                lm.z = (1 - a) * lm.z + a * z
                lm.score = max(lm.score, score)
                lm.hits += 1
                return lm
        lm = Landmark(label=label, x=x, y=y, z=z, score=score)
        bucket.append(lm)
        return lm

    def nearest(self, label: str, from_xyz=(0.0, 0.0, 0.0)) -> Optional[Landmark]:
        bucket = self._store.get(label, [])
        if not bucket:
            return None
        return min(bucket, key=lambda lm: math.dist((lm.x, lm.y, lm.z), from_xyz))

    def all(self) -> List[Landmark]:
        return [lm for b in self._store.values() for lm in b]
