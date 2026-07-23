"""Spatial memory: EMA-smoothed 3D landmark store (per the papers, 1.5m merge radius)."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import math


def normalize_label(label: str) -> str:
    """Canonical form for matching a free-form VLM-written object name (e.g.
    "water tower") against a snake_case detection/memory label (e.g.
    "water_tower") — confirmed live: the model writes target_object as
    natural-language prose, not the exact detection label string, and an
    exact-match comparison silently fails (NAVIGATE_TO_OBJECT/RETURN_TO_LANDMARK
    report "not found" even when the target is right there in memory) unless
    both sides are normalized the same way first."""
    return label.strip().lower().replace("-", "_").replace(" ", "_")


def labels_match(a: str, b: str) -> bool:
    """Equal after normalization, OR one contains the other. Also confirmed
    live: the model adds disambiguating qualifiers on top of the real label
    ("water tower on the right", "closer water tower") that don't reduce to
    the exact label even normalized — plain equality still misses those.
    Substring containment is deliberately permissive (a handful of known prop
    labels, no risk of a wrong cross-match at this vocabulary size)."""
    na, nb = normalize_label(a), normalize_label(b)
    return na == nb or na in nb or nb in na


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
        key = normalize_label(label)
        bucket = self._store.setdefault(key, [])
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
        candidates = self.matching(label)
        if not candidates:
            return None
        return min(candidates, key=lambda lm: math.dist((lm.x, lm.y, lm.z), from_xyz))

    def matching(self, label: str) -> List[Landmark]:
        """All landmarks whose stored label matches `label` (same permissive
        labels_match semantics as nearest()) — the real distinct-object list a
        caller should count, since update() already merges repeat sightings of
        the same physical object into one Landmark per bucket entry via
        merge_radius. Counting landmarks (not raw per-frame detections) is
        what makes an orbit-and-count mission not just recount the same car
        on every lap of the orbit."""
        return [
            lm for key, bucket in self._store.items() if labels_match(label, key) for lm in bucket
        ]

    def count(self, label: str) -> int:
        """Distinct-object count for `label` — see matching()'s docstring for
        why this is "free" (the dedup already happened in update())."""
        return len(self.matching(label))

    def all(self) -> List[Landmark]:
        return [lm for b in self._store.values() for lm in b]
