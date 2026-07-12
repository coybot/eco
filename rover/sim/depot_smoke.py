#!/usr/bin/env python3
"""Phase 1 smoke test for the Depot phrover sim (Python-only, no Swift yet).

Launches Godot headless (falling back to --gui if the headless frame looks
blank — see the "Headless macOS frame capture" risk in the design plan),
spawns one phrover, and exercises every new IPC op: drive/state, detect +
occlusion, the occupancy/observed grid, all injects, the event log, and
reset. Prints PASS/FAIL per check and exits non-zero on any failure.

Usage: python3 depot_smoke.py [--gui] [--seed N] [--keep-alive]
"""

from __future__ import annotations

import argparse
import math
import sys
import time

from depot_client import DepotClient
from godot_launcher import launch_depot

RID = "smoke-1"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def drive_for(client: DepotClient, rid: str, v: float, w: float, seconds: float, hz: float = 20.0) -> None:
    steps = int(seconds * hz)
    for _ in range(steps):
        client.drive(rid, v, w)
        time.sleep(1.0 / hz)
    client.stop(rid)


def frame_looks_blank(jpg: bytes | None) -> bool:
    # Heuristic: a near-solid-black frame (headless GPU render failure) JPEG-
    # compresses far smaller than a lit scene with geometry. No PIL dependency.
    return jpg is None or len(jpg) < 4000


def run(gui: bool, seed: int) -> DepotClient:
    print(f"--- launching Godot (gui={gui}, seed={seed}) ---")
    proc = launch_depot(seed=seed, gui=gui)
    time.sleep(1.0)  # let the scene finish one physics tick after 'IPC ready'
    client = DepotClient()
    client._proc = proc  # stash for cleanup
    return client


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gui", action="store_true", help="force windowed Godot (skip headless probe)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--keep-alive", action="store_true", help="leave Godot running after the test")
    args = ap.parse_args()

    gui = args.gui
    client = run(gui=gui, seed=args.seed)

    try:
        client.reset(seed=args.seed)
        r = client.spawn(RID, (0.0, 1.0), yaw=math.pi / 2)
        check("spawn phrover", r.get("ok", False))

        st = client.state(RID)
        check("state after spawn", st.get("ok", False) and abs(st["pose"][0]) < 0.01 and abs(st["pose"][1] - 1.0) < 0.01,
              detail=str(st))

        client.add_vantage("overhead", (0.0, 5.0, 22.0), (0.0, 5.0, 0.0))
        time.sleep(0.3)
        jpg = client.grab_vantage("overhead")
        blank = frame_looks_blank(jpg)
        if blank and not gui:
            print("--- headless frame looks blank, falling back to --gui ---")
            client.close()
            client._proc.stop()
            client = run(gui=True, seed=args.seed)
            client.reset(seed=args.seed)
            client.spawn(RID, (0.0, 1.0), yaw=math.pi / 2)
            client.add_vantage("overhead", (0.0, 5.0, 22.0), (0.0, 5.0, 0.0))
            time.sleep(0.5)
            jpg = client.grab_vantage("overhead")
            blank = frame_looks_blank(jpg)
        check("vantage frame non-blank", not blank, detail=f"{len(jpg) if jpg else 0} bytes, gui={gui or blank == False}")
        if jpg:
            out = "/tmp/depot_smoke_overhead.jpg"
            with open(out, "wb") as f:
                f.write(jpg)
            print(f"  wrote {out}")

        # -- grid before moving: hallway mostly free, walls occupied --------
        g = client.grid(RID)
        check("grid fetch", g is not None)
        occ_ones = sum(g["occ"]) if g else 0
        check("occupancy grid has walls", occ_ones > 50, detail=f"{occ_ones} occupied cells")
        obs_ones_before = sum(g["obs"]) if g else 0

        # -- drive north up the hallway toward the person's start position --
        drive_for(client, RID, v=0.35, w=0.0, seconds=3.0)
        st2 = client.state(RID)
        check("moved north", st2["pose"][1] > st.get("pose", [0, 1, 0])[1] + 0.3, detail=str(st2["pose"]))

        g2 = client.grid(RID)
        obs_ones_after = sum(g2["obs"])
        check("observed grid accumulates", obs_ones_after > obs_ones_before, detail=f"{obs_ones_before} -> {obs_ones_after}")

        # -- detection + occlusion: red_toolbox lives in room A, behind the
        # closed-by-default(open) door A; from the hallway centerline it should
        # not be visible (occluded by the wall) until the rover turns/enters. --
        objs = client.detect(RID)
        check("detect returns list", isinstance(objs, list))

        # -- injects: person, battery, blur, doors, ladder, kill --------------
        # Person's patrol sweeps the hallway at y=4 (idle rest spot is off-centerline,
        # inside room B, until active) — drive far enough north to actually reach that
        # crossing point, not just a token nudge forward.
        r = client.inject("person_walk", on=True)
        check("inject person_walk", r.get("ok", False))
        drive_for(client, RID, v=0.3, w=0.0, seconds=8.0)
        events = client.get_events(since=0.0)
        kinds = {e["kind"] for e in events}
        check("person_dist events logged", "person_dist" in kinds, detail=str(sorted(kinds)))
        # The person-safety governor now stops (v=0, no movement) starting at 1.8m (see
        # phrover_manager.gd), well outside the old guard_stop (0.45m forward ray) /
        # near_miss (0.8m) thresholds — driving toward the person no longer reliably gets
        # close enough to trip those anymore, precisely because the stop engages earlier.
        # person_stop is the direct signal that the encounter was detected and handled.
        check("guard_stop, near_miss, or person_stop fired near person",
              "guard_stop" in kinds or "near_miss" in kinds or "person_stop" in kinds,
              detail=str(sorted(kinds)))

        r = client.inject("block_door", door="A")
        check("inject block_door", r.get("ok", False))
        g3 = client.grid(RID)
        # Door A panel spans ENU x[-1.1,-0.9] y[3.5,4.4]; scan the cells that
        # neighborhood covers rather than one exact index (grid/rect alignment
        # can shift which specific cell(s) register).
        blocked = False
        for gx in range(int((-1.3 - g3["origin"][0]) / g3["res"]), int((-0.7 - g3["origin"][0]) / g3["res"]) + 1):
            for gy in range(int((3.3 - g3["origin"][1]) / g3["res"]), int((4.6 - g3["origin"][1]) / g3["res"]) + 1):
                if g3["occ"][gy * g3["w"] + gx]:
                    blocked = True
        check("door A occupancy cell now blocked", blocked)

        battery0 = client.state(RID)["battery"]
        client.inject("battery_drain", rate=200.0)
        drive_for(client, RID, v=0.2, w=0.0, seconds=1.0)
        battery1 = client.state(RID)["battery"]
        check("battery_drain inject accelerates drain", battery1 < battery0 - 1.0, detail=f"{battery0} -> {battery1}")

        r = client.inject("camera_blur", sigma=3.0)
        check("inject camera_blur", r.get("ok", False))

        r = client.inject("tip_ladder")
        check("inject tip_ladder", r.get("ok", False))

        r = client.inject("kill_rover", id=RID)
        check("inject kill_rover", r.get("ok", False))
        st_dead = client.state(RID)
        check("phrover gone after kill_rover", not st_dead.get("ok", True))

        events_all = client.get_events(since=0.0)
        fired = {e["data"].get("name") for e in events_all if e["kind"] == "inject_fired"}
        check("all injects logged as inject_fired", {"person_walk", "block_door", "battery_drain",
                                                      "camera_blur", "tip_ladder", "kill_rover"} <= fired,
              detail=str(fired))

        # -- reset clears state -------------------------------------------------
        client.spawn(RID, (0.0, 1.0), yaw=math.pi / 2)
        r = client.reset(seed=args.seed + 1)
        check("reset ok", r.get("ok", False))
        events_after_reset = client.get_events(since=0.0)
        check("reset clears events", len(events_after_reset) == 0, detail=f"{len(events_after_reset)} left")

    finally:
        if not args.keep_alive:
            client.close()
            client._proc.stop()
        else:
            print("--- --keep-alive set, leaving Godot running ---")

    print("\n=== SUMMARY ===")
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
