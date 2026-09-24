#!/usr/bin/env python3
"""Follow a MOVING target, in the live sim, on every vehicle class that has one.

Three geometries, three different things that can go wrong, one executor
(drone/common/follow.py's FollowExecutor) driving all of them through the Backend seam:

  rover       UNICYCLE_2D         holds a stand-off behind a walking person without
                                  reversing into ground it cannot see
  quadcopter  HOLONOMIC_3D        same, but it can translate while yawed and outruns a walk
  fixedwing   COORDINATED_TURN_3D cannot hover, so "following" has to resolve to a circle
                                  whose centre MOVES — and it has to keep the sensor on
                                  that centre, or it stares down the tangent and loses the
                                  target within a quarter lap

Each also checks the thing vision-only following is most prone to: the lock transferring
to something else that matches the same label. The depot carries a second walker and the
surveil_truck scene a decoy car for exactly that.

Runs headless: telemetry and detections only, no camera.

Usage:
    python3 tests/test_follow_target.py
    (or via pytest — skips automatically if no Godot 4 binary is installed)
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path
from shutil import which

SIM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(SIM_DIR.parents[0] / "common"))
sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))

FLEET_PORT = 9994
FW_PORT = 9992

# The depot walker's pace. The sim rover tops out at 0.5 m/s and the real WAVE ROVER at
# 0.35, so a follow scenario at a normal 0.8-1.3 m/s walk can only ever fail — the ceiling
# is physical, not a tuning problem (see test_follow.py's closed-loop cases).
WALK_MPS = 0.4
WALK_ROUTE = [[0.0, 4.0], [0.0, 9.0]]
DECOY_ROUTE = [[4.0, 2.6], [4.0, 5.2]]


def _godot_available() -> bool:
    return bool(os.environ.get("GODOT_BIN") or which("godot4") or which("godot"))


# --------------------------------------------------------------------- fleet (quad/rover)
def _check_fleet(fails: list) -> None:
    from engine_client import EngineClient
    from backends import FleetSimBackend
    from follow import FollowExecutor
    from vehicle_class import get_class

    c = EngineClient(f"tcp://127.0.0.1:{FLEET_PORT}")

    for did, vtype, standoff, altitude, spawn in (
        ("follow-quad", "quadcopter", 3.0, 1.5, (0.0, 1.0, 1.5)),
        ("follow-rover", "rover", 2.5, None, (0.0, 1.0, 0.0)),
    ):
        # Respawn rather than reposition: FleetManager.spawn() ignores an id it already
        # knows, so a bare spawn leaves the vehicle wherever the roster put it.
        c.despawn(did)
        c.spawn(did, vtype, spawn)
        c.set_yaw(did, math.pi / 2)
        c._call({"op": "inject", "name": "person_walk",
                 "params": {"on": True, "speed": WALK_MPS, "waypoints": WALK_ROUTE}})
        time.sleep(0.5)

        backend = FleetSimBackend(c, did, vtype)
        start = backend.get_pose()
        ex = FollowExecutor(backend, get_class(vtype), "follow the person",
                            standoff=standoff, altitude=altitude)
        card = ex.run(duration_s=12, tick_hz=5)
        ex.stop()
        end = backend.get_pose()

        travelled = (end[1] - start[1]) if (start and end) else 0.0
        print(f"{vtype:11s} in_band={card['in_band_fraction']:.2f} "
              f"range={card['min_range']:.2f}..{card['max_range']:.2f} "
              f"travelled={travelled:.2f}m lost={card['lost_ticks']}")

        if card["samples"] < 30:
            fails.append(f"{vtype}: only {card['samples']} samples")
        if card["in_band_fraction"] < 0.75:
            fails.append(f"{vtype}: held the stand-off only "
                         f"{card['in_band_fraction']:.0%} of the time")
        # Holding a stand-off by standing still while the target walks away would show up
        # as an out-of-band range, but assert the travel directly so this cannot pass on a
        # vehicle that never moved.
        if travelled < 2.0:
            fails.append(f"{vtype}: advanced only {travelled:.2f} m while the person walked")
        if card["unverified_reacquisitions"]:
            fails.append(f"{vtype}: {card['unverified_reacquisitions']} unvouched re-locks")
        if card["lost_ticks"]:
            fails.append(f"{vtype}: lost the target for {card['lost_ticks']} ticks")

    _check_fleet_identity(c, fails)


def _check_fleet_identity(c, fails: list) -> None:
    """A second walker, reported with the same 'person' label, must not steal the lock.

    Nothing in the detection distinguishes the two — which is exactly the situation a
    COCO-class detector leaves a follow in, and the case it has to survive by association
    alone.
    """
    from backends import FleetSimBackend
    from follow import FollowExecutor
    from vehicle_class import get_class

    did = "follow-identity"
    c.despawn(did)
    c.spawn(did, "rover", (0.0, 1.0, 0.0))
    c.set_yaw(did, math.pi / 2)
    c._call({"op": "inject", "name": "person_walk",
             "params": {"on": True, "speed": WALK_MPS, "waypoints": WALK_ROUTE}})
    c._call({"op": "inject", "name": "person_walk",
             "params": {"on": True, "person": "DecoyActor", "speed": WALK_MPS,
                        "waypoints": DECOY_ROUTE}})
    time.sleep(0.5)

    backend = FleetSimBackend(c, did, "rover")
    ex = FollowExecutor(backend, get_class("rover"), "follow the person",
                        seed=(0.0, 4.0), standoff=2.5)
    drift = [0.0]

    def watch(e):
        if e.tracker.track is not None:
            # The two walkers are 4 m apart in x; a transferred lock shows up as the
            # tracked x sliding toward the decoy's lane.
            drift[0] = max(drift[0], abs(e.tracker.track.position[0]))
        return False

    card = ex.run(duration_s=10, tick_hz=5, should_stop=watch)
    ex.stop()
    print(f"identity    max_drift_from_lane={drift[0]:.2f}m "
          f"unverified={card['unverified_reacquisitions']}")
    if drift[0] > 1.5:
        fails.append(f"identity: tracked position drifted {drift[0]:.2f} m toward the decoy")
    if card["unverified_reacquisitions"]:
        fails.append("identity: lock re-attached without the gate vouching for it")


# --------------------------------------------------------------------- fixed-wing
def _check_fixedwing(fails: list) -> None:
    from depot_client import DepotClient
    from backends import SimBackend
    from follow import FollowExecutor
    from vehicle_class import get_class

    c = DepotClient(port=FW_PORT)
    rid = "follow-fw"
    c.fw_despawn(rid)
    truck = c.fw_env_state()["target_enu"]
    # Nose-on from the south so the truck is in the camera cone from the first tick.
    c.fw_spawn(rid, (truck[0], truck[1] - 90.0, 45.0), yaw=math.pi / 2)
    time.sleep(0.8)

    backend = SimBackend(c, rid)
    ex = FollowExecutor(backend, get_class("fixedwing"), "the pickup truck", altitude=45.0)

    angles: list = []

    def watch(e):
        pose = backend.get_pose()
        track = e.tracker.track
        if pose and track:
            angles.append(math.atan2(pose[1] - track.position[1], pose[0] - track.position[0]))
        return False

    card = ex.run(duration_s=45, tick_hz=4, should_stop=watch)
    ex.stop()

    swept = 0.0
    for a, b in zip(angles, angles[1:]):
        d = b - a
        while d > math.pi:
            d -= 2 * math.pi
        while d < -math.pi:
            d += 2 * math.pi
        swept += d
    revolutions = abs(swept) / (2 * math.pi)

    print(f"fixedwing   in_band={card['in_band_fraction']:.2f} "
          f"range={card['min_range']:.1f}..{card['max_range']:.1f} "
          f"revolutions={revolutions:.2f} lost={card['lost_ticks']}")

    if card["samples"] < 100:
        fails.append(f"fixedwing: only {card['samples']} samples")
    if card["in_band_fraction"] < 0.8:
        fails.append(f"fixedwing: stayed in the orbit ring only "
                     f"{card['in_band_fraction']:.0%} of the time")
    # The whole claim: it actually went AROUND a target that was itself moving. A straight
    # pass overhead would score a fine range band and sweep almost no angle.
    if revolutions < 1.5:
        fails.append(f"fixedwing: swept only {revolutions:.2f} revolutions — not an orbit")
    if card["lost_ticks"]:
        fails.append(f"fixedwing: lost the truck for {card['lost_ticks']} ticks")
    # A decoy car sits in the same scene; locking onto it would be a silent wrong answer.
    if card["unverified_reacquisitions"]:
        fails.append("fixedwing: lock re-attached without the gate vouching for it")


def main() -> int:
    from fw_eval import launch_flightline
    sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))

    fails: list = []
    fleet = _launch_fleet()
    try:
        _check_fleet(fails)
    finally:
        fleet.terminate()

    fw = launch_flightline(seed=0, port=FW_PORT, gui=False, env="surveil_truck")
    try:
        _check_fixedwing(fails)
    finally:
        fw.stop()

    if fails:
        print("\nFAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("\nAll checks passed.")
    return 0


def _launch_fleet():
    """Launch Godot with a quad + rover roster in the depot env (the only scene with
    walking person actors). fw_eval's launcher always passes an empty fleet, so this
    cannot reuse it."""
    import subprocess
    import threading

    godot = os.environ.get("GODOT_BIN") or which("godot4") or which("godot")
    project = SIM_DIR / "godot"
    proc = subprocess.Popen(
        [godot, "--path", str(project), "--headless", "--",
         "--fleet=quadcopter:1,rover:1", "--env=depot", "--seed=7",
         f"--ipc-port={FLEET_PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ready = threading.Event()

    def _tee():
        for line in proc.stdout:
            if "IPC ready" in line:
                ready.set()
    threading.Thread(target=_tee, daemon=True).start()
    if not ready.wait(90):
        proc.terminate()
        raise RuntimeError("fleet sim did not report 'IPC ready' within 90s")
    time.sleep(1.0)
    return proc


def test_follow_target_all_classes():
    """pytest entry — skips when no Godot 4 binary is available."""
    import pytest
    if not _godot_available():
        pytest.skip("Godot 4 binary not installed (set GODOT_BIN or install godot4)")
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
