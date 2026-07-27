#!/usr/bin/env python3
"""Unit tests for the situational context blocks and pinned memory.

Pure logic — no sim, no model. These blocks are what the demo's comms-denied
swarm reasoning is built on, so the geometry behind them has to be right before
the model is asked to reason from it: a mis-signed bearing or an inverted
descent trend would have the model confidently drawing the wrong conclusion
from correct sensor data.

Usage:
    python3 tests/test_situation.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from situation import ObstacleTracker, PeerTracker, payload_block  # noqa: E402
from spatial_memory import SpatialMemory  # noqa: E402


class FakeDet:
    def __init__(self, label, world, top_z=None):
        self.label = label
        self.world_xyz = world
        self.top_z = top_z


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    return 0 if cond else 1


def main() -> int:
    bad = 0
    print("PeerTracker")
    pt = PeerTracker()
    # A teammate north-east of us, descending from 45 m to 18 m while staying
    # over one spot — the exact signature of "it has found something".
    for i in range(10):
        pt.observe("drone-b", 260.0 + math.sin(i) * 20.0, 110.0 + math.cos(i) * 20.0,
                   45.0 - i * 3.0, t=100.0 + i * 4.0)
    s = pt.summarize((150.0, 0.0, 35.0))
    print("    " + s.replace("\n", "\n    "))
    bad += check("names the peer", "drone-b" in s)
    bad += check("bearing is north-east", "NE" in s, s)
    bad += check("spots the descent", "descending" in s)
    bad += check("spots it loitering over one spot", "one spot" in s)
    bad += check("flags low altitude", "flying low" in s)

    # A teammate in level cruise must NOT read as descending or loitering —
    # otherwise every ordinary transit looks like a discovery.
    pt2 = PeerTracker()
    for i in range(10):
        pt2.observe("drone-b", 100.0 + i * 25.0, 0.0, 35.0, t=100.0 + i * 4.0)
    s2 = pt2.summarize((100.0, 0.0, 35.0))
    print("    " + s2.replace("\n", "\n    "))
    bad += check("cruise is not 'descending'", "descending" not in s2)
    bad += check("cruise is not 'one spot'", "one spot" not in s2)
    bad += check("cruise is not 'flying low'", "flying low" not in s2)

    # Sightings older than the window fall out.
    pt3 = PeerTracker(history_s=10.0)
    pt3.observe("drone-b", 0.0, 0.0, 30.0, t=0.0)
    pt3.observe("drone-b", 5.0, 5.0, 30.0, t=100.0)
    bad += check("stale sightings expire", len(pt3._tracks["drone-b"]) == 1)

    print("\nObstacleTracker")
    ot = ObstacleTracker()
    # Exactly what env_sar emits: markers at MID-height (25 m) carrying the real
    # top (50 m). Reading the sensed z as the top would halve the wall and tell
    # the model a 35 m transit clears it.
    wall = [FakeDet("wall", (100.0, y, 25.0), top_z=50.0) for y in range(-70, 71, 20)]
    # Heading due east, straight at it.
    s3 = ot.summarize((-100.0, 0.0, 35.0), 0.0, wall)
    print("    " + s3.replace("\n", "\n    "))
    bad += check("reports structure ahead", "across your course" in s3)
    bad += check("gives both ends", "-70" in s3 and "70" in s3, s3)
    bad += check("uses the real top, not the marker height", "50 m above" in s3, s3)
    bad += check("warns it is not clearable", "not clear it" in s3)

    # Same wall, but flying away from it — must stay silent, or a wall already
    # passed would keep arguing for a turn.
    s4 = ot.summarize((-100.0, 0.0, 35.0), math.pi, wall)
    bad += check("silent when the wall is behind", s4 == "", repr(s4))

    # Above it: still reported, but without the "cannot clear" line.
    s5 = ot.summarize((-100.0, 0.0, 60.0), 0.0, wall)
    bad += check("no false 'cannot clear' when above", "not clear it" not in s5)

    bad += check("ignores non-wall detections",
                 ot.summarize((-100.0, 0.0, 35.0), 0.0,
                              [FakeDet("person", (0.0, 0.0, 1.0))]) == "")

    print("\npayload_block")
    bad += check("carrying reads as ready", "ready to release" in payload_block(1))
    bad += check("empty reads as cannot deliver", "cannot deliver" in payload_block(0))

    print("\nSpatialMemory.pin")
    m = SpatialMemory(merge_radius=12.0)
    m.pin("teammate", 10.0, 10.0, 30.0)
    m.pin("teammate", 200.0, 50.0, 20.0)
    got = m.matching("teammate")
    bad += check("pin keeps exactly one entry", len(got) == 1, f"{len(got)}")
    bad += check("pin keeps the LATEST fix", got[0].x == 200.0, f"x={got[0].x}")
    bad += check("pin counts sightings", got[0].hits == 2, f"hits={got[0].hits}")
    # update() must still accumulate — pin is the exception, not a behaviour change.
    m.update("person", 0.0, 0.0, 1.0, 0.9)
    m.update("person", 90.0, 90.0, 1.0, 0.9)
    bad += check("update still accumulates distinct objects", m.count("person") == 2)

    print()
    if bad:
        print(f"FAIL — {bad} check(s) failed")
        return 1
    print("PASS — situation blocks and pinned memory behave as the prompt assumes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
