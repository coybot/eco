#!/usr/bin/env python3
"""Unit tests for the M5 brain additions: label matching, drop gates, peer handling.

No sim and no model — these cover the deterministic logic wrapped around the
model's decisions, which is where a silent mistake does the most damage:

* label matching, because the grounding guard compares one-directionally and
  the cloud planner's phase wording has to satisfy it. If "person" stops
  matching "person in the red jacket", every legitimate delivery report gets
  rejected and the mission never completes;
* the delivery gates, because a payload cannot be recovered and a drop on the
  wrong person is the worst outcome this mission has;
* peer handling, because a teammate must be usable as a navigation fix without
  ever becoming evidence that the objective was achieved.

Usage:
    python3 tests/test_brain_v2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from backends import Detection  # noqa: E402
from spatial_memory import SpatialMemory, labels_match, normalize_label  # noqa: E402
from vlm import ActionType, VLMAction  # noqa: E402


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    return 0 if cond else 1


class FakeBackend:
    """Minimal Backend stand-in recording what the loop asked the vehicle to do."""

    def __init__(self, pose=(250.0, 40.0, 15.0, 0.0), release_ok=True):
        self._pose = pose
        self.release_ok = release_ok
        self.released = 0
        self.gotos = []
        self.events = []
        self.sensor_aimed_at = "unset"

    def get_pose(self):
        return self._pose

    def goto(self, north_m, east_m, alt_m, timeout_s=60.0, tol_m=5.0):
        self.gotos.append((north_m, east_m, alt_m))
        return True

    def log_event(self, kind, data):
        self.events.append((kind, data))

    def aim_sensor(self, center):
        self.sensor_aimed_at = center

    def drop_payload(self):
        if not self.release_ok:
            return None
        self.released += 1
        return {"release_enu": [self._pose[0], self._pose[1], self._pose[2]]}


def make_loop(backend, payload=1, detections=()):
    from reasoning_loop import MissionLoop
    from vehicle_class import get_class
    loop = MissionLoop(backend=backend, vehicle_class=get_class("fixedwing"))
    loop.payload_remaining = payload
    loop.payload_capacity = 1
    loop._last_detections = list(detections)
    return loop


def main() -> int:
    bad = 0

    # --- label matching (the grounding guard's contract) --------------------
    print("label matching — phase wording must satisfy the guard")
    bad += check('"person" matches "person in the red jacket"',
                 labels_match("person", "person in the red jacket"))
    bad += check('"water_bottle" matches "water bottle"',
                 labels_match("water_bottle", "water bottle"))
    bad += check('"person" does not match "water bottle"',
                 not labels_match("person", "water bottle"))
    # The guard tests `label in objective_blob`, so this is the real check the
    # planner's phase text has to pass.
    blob = "deliver a water bottle to the person in the red jacket".lower()
    bad += check("objective blob contains the person label",
                 normalize_label("person") in blob)
    bad += check("objective blob contains the bottle label",
                 normalize_label("water_bottle").replace("_", " ") in blob
                 or normalize_label("water_bottle") in blob.replace(" ", "_"))

    # --- delivery gates ------------------------------------------------------
    print("\ndelivery gates")
    target = Detection(label="person", score=0.9, world_xyz=(255.0, 40.0, 0.9))

    # Nothing visible this frame -> must not release, however sure the model is.
    b = FakeBackend()
    loop = make_loop(b, detections=[])
    loop._exec_drop_payload(VLMAction(action_type=ActionType.DROP_PAYLOAD,
                                      target_object="person"), b)
    bad += check("refuses to drop with the target not in view", b.released == 0)

    # Visible and close -> releases.
    b = FakeBackend()
    loop = make_loop(b, detections=[target])
    loop._exec_drop_payload(VLMAction(action_type=ActionType.DROP_PAYLOAD,
                                      target_object="person"), b)
    bad += check("releases on a confirmed, close target", b.released == 1)
    bad += check("decrements the payload count", loop.payload_remaining == 0)

    # Visible but far -> closes in first rather than releasing early.
    far = Detection(label="person", score=0.9, world_xyz=(600.0, 40.0, 0.9))
    b = FakeBackend()
    loop = make_loop(b, detections=[far])
    loop._exec_drop_payload(VLMAction(action_type=ActionType.DROP_PAYLOAD,
                                      target_object="person"), b)
    bad += check("does not release from out of range", b.released == 0)
    bad += check("closes in on the target instead", len(b.gotos) == 1, str(b.gotos))

    # Empty -> refuses, and does not go negative.
    b = FakeBackend()
    loop = make_loop(b, payload=0, detections=[target])
    loop._exec_drop_payload(VLMAction(action_type=ActionType.DROP_PAYLOAD,
                                      target_object="person"), b)
    bad += check("refuses when no payload remains", b.released == 0)
    bad += check("payload count never goes negative", loop.payload_remaining == 0)

    # A bystander detection must not authorise a drop aimed at a bottle.
    b = FakeBackend()
    loop = make_loop(b, detections=[Detection(label="car", score=0.9,
                                              world_xyz=(255.0, 40.0, 0.9))])
    loop._exec_drop_payload(VLMAction(action_type=ActionType.DROP_PAYLOAD,
                                      target_object="person"), b)
    bad += check("a non-matching detection does not authorise release", b.released == 0)

    # --- orbit ----------------------------------------------------------------
    print("\norbit")
    b = FakeBackend(pose=(200.0, 0.0, 30.0, 0.0))
    loop = make_loop(b)
    c = loop._orbit_center(VLMAction(action_type=ActionType.ORBIT_POINT,
                                     world_x=259.0, world_y=38.0), b.get_pose())
    bad += check("uses the world point when given", c == (259.0, 38.0), str(c))

    loop.memory.update("person", 259.0, 38.0, 0.9, 0.9)
    c = loop._orbit_center(VLMAction(action_type=ActionType.ORBIT_POINT,
                                     target_object="person"), b.get_pose())
    bad += check("falls back to remembered position", c == (259.0, 38.0), str(c))

    c = loop._orbit_center(VLMAction(action_type=ActionType.ORBIT_POINT,
                                     target_object="unicorn"), b.get_pose())
    bad += check("no point and no memory -> None", c is None, str(c))

    # --- peers ----------------------------------------------------------------
    print("\npeer handling")
    m = SpatialMemory(merge_radius=12.0)
    for i in range(6):
        m.pin("teammate", 200.0 + i * 30.0, 40.0, 25.0)
    bad += check("a teammate's track stays one landmark, not six",
                 m.count("teammate") == 1, str(m.count("teammate")))

    from reasoning_loop import MissionLoop
    bad += check("teammate labels cannot ground a success claim",
                 {"aircraft", "teammate"} <= MissionLoop.NON_GROUNDING_LABELS)

    # --- which phases the delivery gate applies to -------------------------
    # The gate exists because a delivery phase completed by repeating the
    # PREVIOUS phase's finding ("person in red jacket located and confirmed").
    # That claim is properly grounded, so the grounding check passed it and the
    # aircraft went home with the bottle aboard. What matters here is that the
    # gate fires on phases defined by an ACT and stays out of the way otherwise.
    print("\ndelivery gate scope")
    from reasoning_loop import _phase_wants_delivery
    real_delivery = {
        "objective": "Close on the person in the red jacket and release the "
                     "carried water bottle as close as possible.",
        "success": "water bottle delivered near person in red jacket"}
    bad += check("fires on a real delivery phase", _phase_wants_delivery(real_delivery))
    bad += check("ignores a search phase",
                 not _phase_wants_delivery(
                     {"objective": "Search the northern half for a person in a "
                                   "red jacket.", "success": "person located"}))
    bad += check("ignores return home",
                 not _phase_wants_delivery({"type": "return_home"}))
    bad += check("ignores a phase that only mentions the bottle",
                 not _phase_wants_delivery(
                     {"objective": "Note where the water bottle came to rest.",
                      "success": "position recorded"}))
    # A real plan produced "After delivering the water bottle, note the
    # position..." — a REPORTING phase that trips the wording test. Harmless
    # only because the payload is already gone by then, which is worth knowing
    # rather than rediscovering: the gate is a payload-state check, so it can
    # never block a phase that follows an actual delivery.
    bad += check("a post-delivery reporting phase matches the wording",
                 _phase_wants_delivery(
                     {"objective": "After delivering the water bottle, note the "
                                   "position of the person.",
                      "success": "position reported"}),
                 "documented: harmless because payload_remaining is 0 by then")

    print()
    if bad:
        print(f"FAIL — {bad} check(s) failed")
        return 1
    print("PASS — label matching, delivery gates, orbit targeting and peer handling.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
