#!/usr/bin/env python3
"""M4 gate: the sensing extensions the demo's three claims rest on.

Each check maps to a claim that would otherwise be asserted rather than shown:

* per-class detect range — the wall is sensable from 200 m (so avoiding it is a
  camera decision with time to react) while a person still is not (so the
  aircraft must descend for a close identification pass);
* wall markers occlude correctly — the far face is genuinely hidden, so "the
  route beyond the wall is blocked" is true rather than stipulated;
* sensor yaw offset — an aircraft flying a circle can see the point it is
  circling. Without this the tunnel-watch beat is impossible, because both the
  camera and detect() otherwise follow the airframe's nose and an orbiting
  aircraft stares at the tangent forever;
* peer sensing — one aircraft can see another at range with the right world
  position, which is the only channel the comms-denied swarm claim has;
* ballistic drop and stigmergy — a released bottle falls, comes to rest, and is
  then detectable BY THE OTHER AIRCRAFT, which is how drone B reads drone A's
  delivery off the environment with no radio link.

Runs headless: detections and telemetry only.

Usage:
    python3 tests/test_fw_sensing.py
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))

from depot_client import DepotClient  # noqa: E402
from fw_eval import launch_flightline  # noqa: E402

A, B = "fw-sense-a", "fw-sense-b"
PORT = 9983

# Camera boresight sits 15 deg below horizontal with a 35 deg vertical FOV, so a
# subject is only in frame while its depression angle stays within ~32.5 deg.
# Staging that ignores this produces tests that "pass" for the wrong reason —
# it already happened once in M3.
LOOK_DOWN, VFOV_HALF = 15.0, 17.5


def depression(alt: float, ground: float, target_h: float = 0.9) -> float:
    return math.degrees(math.atan2(alt - target_h, ground))


def framed(alt: float, ground: float, target_h: float = 0.9) -> bool:
    return abs(depression(alt, ground, target_h) - LOOK_DOWN) <= VFOV_HALF - 2.0


def labels(c, rid):
    return [d["label"] for d in c.fw_detect(rid)]


def main() -> int:
    fails = []
    proc = launch_flightline(seed=0, port=PORT, gui=False, env="sar")
    try:
        c = DepotClient(port=PORT)
        env = c.fw_env_state()
        wall_e = env["wall"]["east"]
        tun = env["tunnel"]

        # --- 1. per-class range ---------------------------------------------
        # 200 m west of the wall, at transit altitude, nose east.
        c.fw_spawn(A, (wall_e - 200.0, 0.0, 35.0), 0.0)
        time.sleep(0.3)
        seen = labels(c, A)
        print(f"range     : 200 m west of the wall -> {sorted(set(seen))}")
        if "wall" not in seen:
            fails.append("wall not detected at 200 m (per-class range not applied)")
        if "person" in seen:
            fails.append("a person was detected at 200 m — the 80 m people limit is "
                         "what forces the close identification pass")

        # --- 2. marker occlusion --------------------------------------------
        # Looking east from the west side, only the west-face markers should
        # register; the east-face row must be hidden behind the wall itself.
        wall_hits = [d for d in c.fw_detect(A) if d["label"] == "wall"]
        east_side = [d for d in wall_hits if d["world"][0] > wall_e]
        print(f"occlusion : {len(wall_hits)} wall markers seen, "
              f"{len(east_side)} of them on the far face")
        if east_side:
            fails.append(f"{len(east_side)} far-face wall markers visible through the wall")
        if not wall_hits:
            fails.append("no wall markers detected at all")

        # --- 3. sensor yaw offset -------------------------------------------
        # Fly a tangent past the tunnel, as an orbit does: the aircraft's nose
        # points 90 deg away from the thing it is meant to be watching.
        mid_e = (tun["w_end"] + tun["e_end"]) / 2.0
        ground = 55.0
        assert framed(25.0, ground), "staging geometry is itself out of frame"
        c.fw_inject("sar_config", force_phase="in_tunnel", dwell_s=600.0,
                    target_enu=[mid_e, tun["n"] + 60.0])   # out in the open, not hidden
        time.sleep(0.2)
        # Aircraft south of the target, nose EAST (target is due north).
        c.fw_spawn(A, (mid_e, tun["n"] + 60.0 - ground, 25.0), 0.0)
        c.fw_set_sensor(A, offset=0.0)
        time.sleep(0.3)
        nose_only = labels(c, A)
        print(f"sensor    : nose 90 deg off target -> {sorted(set(nose_only))}")
        if "person" in nose_only:
            fails.append("target visible with the sensor pointed 90 deg away — "
                         "the offset is not being applied")

        c.fw_spawn(A, (mid_e, tun["n"] + 60.0 - ground, 25.0), 0.0)
        c.fw_set_sensor(A, at=(mid_e, tun["n"] + 60.0))
        time.sleep(0.3)
        aimed = labels(c, A)
        st = c.fw_state(A)
        print(f"sensor    : aimed at the target (offset={st['sensor_yaw_offset']:.2f} rad) "
              f"-> {sorted(set(aimed))}")
        if "person" not in aimed:
            fails.append("target NOT visible even with the sensor aimed at it — "
                         "an orbiting aircraft could never watch its orbit centre")

        # --- 4. peer sensing --------------------------------------------------
        # Both aircraft east of the wall, 250 m apart along north, A's nose on B.
        # (Staging them either side of the wall instead detects nothing — the
        # wall correctly occludes the peer, which is checked separately below.)
        c.fw_set_sensor(A, offset=0.0)
        c.fw_spawn(A, (150.0, -125.0, 35.0), math.radians(90.0))
        c.fw_spawn(B, (150.0, 125.0, 35.0), math.radians(-90.0))
        time.sleep(0.3)
        peers = [d for d in c.fw_detect(A) if d["label"] == "aircraft"]
        print(f"peer      : A sees {len(peers)} aircraft at 250 m")
        if not peers:
            fails.append("peer aircraft not detected at 250 m — the comms-denied "
                         "swarm cue has no channel")
        else:
            p = peers[0]
            # Compare against where B ACTUALLY is, not where it was spawned:
            # nothing can hold a fixed-wing still (stop() is a 12 m/s loiter),
            # so it has already flown several metres by capture time.
            b_now = c.fw_state(B)["position"]
            err = math.hypot(p["world"][0] - b_now[0], p["world"][1] - b_now[1])
            print(f"peer      : reported at {[round(v, 1) for v in p['world']]} "
                  f"(err {err:.1f} m), peer_id={p.get('peer_id')}, "
                  f"alt={p.get('peer_alt')}")
            if err > 2.0:
                fails.append(f"peer world position off by {err:.1f} m")
            if p.get("peer_id") != B:
                fails.append(f"peer id wrong: {p.get('peer_id')!r}")

        # Peers obey occlusion too: across the 50 m wall, A must NOT see B.
        # Both are at 35 m and the wall tops out at 50 m, so the sightline is
        # genuinely blocked — this is what stops the swarm cue from working
        # through terrain.
        #
        # Read the wall position from the scene rather than hard-coding it. This
        # spawned B at a literal 250, which was comfortably past the wall when it
        # stood at east=100 and became the wall's own mid-plane when the wall
        # moved east — so the "sightline" ended inside the wall instead of
        # crossing it, and the check silently stopped testing occlusion at all.
        wall_e = c.fw_env_state()["wall"]["east"]
        c.fw_spawn(A, (wall_e - 100.0, 0.0, 35.0), 0.0)
        c.fw_spawn(B, (wall_e + 100.0, 0.0, 35.0), math.pi)
        time.sleep(0.3)
        through_wall = [d for d in c.fw_detect(A) if d["label"] == "aircraft"]
        print(f"peer      : across the 50 m wall -> {len(through_wall)} aircraft "
              f"(expect 0)")
        if through_wall:
            fails.append("peer aircraft visible straight through the wall")

        # --- 5. ballistic drop, then stigmergy --------------------------------
        drop = env["drop_zone"]
        c.fw_spawn(A, (drop[0] - 40.0, drop[1], 15.0), 0.0)
        time.sleep(0.3)
        before = c.fw_state(A)["payload_remaining"]
        rel = c.fw_drop(A)
        print(f"drop      : released={rel.get('ok')} payload {before} -> "
              f"{c.fw_state(A)['payload_remaining']}")
        if not rel.get("ok"):
            fails.append("payload release refused")

        # Wait for it to land and be promoted to a sensable prop.
        rest = None
        for _ in range(40):
            time.sleep(0.25)
            ev = [e for e in c.fw_events(A) if e.get("kind") == "payload_landed"]
            if ev:
                rest = ev[-1]["data"]["rest_enu"]
                break
        print(f"drop      : rest position {rest}")
        if rest is None:
            fails.append("payload never came to rest / never became a prop")
        else:
            bottles = [p for p in c.fw_prop_truth() if p["label"] == "water_bottle"]
            if not bottles:
                fails.append("landed payload is not in prop truth")

            # The other aircraft must be able to see it. Bottles sense to 40 m,
            # so pick a stand-off that is inside that AND inside the FOV band.
            g = 28.0
            assert framed(12.0, g, 0.2), "bottle observation geometry out of frame"
            c.fw_spawn(B, (rest[0] - g, rest[1], 12.0), 0.0)
            c.fw_set_sensor(B, offset=0.0)
            time.sleep(0.3)
            seen_b = labels(c, B)
            print(f"stigmergy : the OTHER aircraft sees -> {sorted(set(seen_b))}")
            if "water_bottle" not in seen_b:
                fails.append("drone B cannot see the bottle drone A dropped — "
                             "no stigmergy channel")

            # And it must NOT be visible from far away (per-class range holds).
            c.fw_spawn(B, (rest[0] - 120.0, rest[1], 35.0), 0.0)
            time.sleep(0.3)
            if "water_bottle" in labels(c, B):
                fails.append("bottle detectable from 120 m — 40 m class range not applied")

        c.fw_despawn(A)
        c.fw_despawn(B)
        c.close()
    finally:
        proc.stop()

    print()
    if fails:
        print(f"M4 FAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("M4 PASS — class ranges, occlusion, gimbal, peer sensing and stigmergy all real.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
