#!/usr/bin/env python3
"""Fly one showcase sortie of the tilt-rotor VTOL (fw_spawn airframe="vtol") in the SAR
scene, paced entirely by SIM time so it works under Godot's --write-movie (where the
engine runs faster or slower than the wall clock).

Sortie: landed on home pad A -> vertical take-off -> full pirouette in the hover ->
transition -> wing-borne climb around the north end of the 50 m wall -> back-transition
over the search area -> descend -> hover-taxi to beside the drop zone -> vertical landing.

Typical use is recording a video:

    godot --path godot --write-movie /tmp/vtol.avi --fixed-fps 30 --resolution 1280x720 \\
          --quit-after 3600 -- --fleet= --env=sar --ipc-port=9981 --chase=auto &
    python3 vtol_demo_sortie.py --port 9981

It prints the sim time of each phase, and the sim time the sortie ended at (for trimming).
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))
from depot_client import DepotClient  # noqa: E402

RID = "cruza"
HOME = (-20.0, 10.0)            # env_sar HOME_A
TAKEOFF_ALT = 25.0
CRUISE_ALT = 40.0
CRUISE_SPEED = 18.0
WAYPOINTS = [(250.0, 115.0), (370.0, 45.0)]   # round the north end of the wall (n<=70)
HOVER_AT = (400.0, 30.0)        # over the search area
SURVEY_ALT = 12.0
LAND_AT = (452.0, 30.0)         # beside env_sar's drop zone (462, 18)
DECEL = 2.0                     # m/s^2, fixedwing_manager VTOL_DECEL


def wrap(a: float) -> float:
    return math.remainder(a, math.tau)


class Pilot:
    def __init__(self, c: DepotClient):
        self.c = c
        self.s = c.fw_state(RID)

    def tick(self, airspeed: float, yaw_rate: float, climb: float) -> dict:
        self.c.fw_drive(RID, airspeed, yaw_rate, climb)
        self.s = self.c.fw_state(RID)
        return self.s

    @property
    def t(self) -> float:
        return float(self.s["sim_time"])

    @property
    def pos(self):
        return self.s["position"]

    def heading_to(self, xy) -> float:
        return math.atan2(xy[1] - self.pos[1], xy[0] - self.pos[0])

    def dist_to(self, xy) -> float:
        return math.hypot(xy[0] - self.pos[0], xy[1] - self.pos[1])

    def steer(self, xy, gain: float = 1.5) -> float:
        return max(-1.2, min(1.2, gain * wrap(self.heading_to(xy) - self.s["yaw"])))

    def alt_hold(self, z: float, lo: float = -3.0, hi: float = 4.0) -> float:
        return max(lo, min(hi, 0.6 * (z - self.pos[2])))

    def hold(self, seconds: float, airspeed=0.0, yaw_rate=0.0, climb=0.0) -> None:
        t_end = self.t + seconds
        while self.t < t_end:
            self.tick(airspeed, yaw_rate, climb)


def phase(p: Pilot, name: str) -> None:
    s = p.s
    print(f"[t={p.t:6.1f}s] {name:<28} mode={s['flight_mode']:<10} "
          f"pos=({s['position'][0]:6.1f},{s['position'][1]:6.1f},{s['position'][2]:5.1f}) "
          f"v={s['airspeed']:4.1f} tilt={math.degrees(s['tilt']):3.0f}", flush=True)


def fly(c: DepotClient) -> float:
    # The env registers with FixedWingManager a moment after IPC comes up, and
    # fw_spawn is a silent no-op until it has, so retry until the aircraft exists.
    for _ in range(200):
        c.fw_spawn(RID, (HOME[0], HOME[1], 0.0), 0.0, airframe="vtol")
        if c.fw_state(RID).get("ok"):
            break
        time.sleep(0.1)
    p = Pilot(c)
    phase(p, "on the pad")
    p.hold(3.0)

    phase(p, "vertical take-off")
    while p.pos[2] < TAKEOFF_ALT:
        p.tick(0.0, 0.0, 2.5)
    p.hold(1.0)

    phase(p, "pirouette")
    turned, last = 0.0, p.s["yaw"]
    while turned < math.tau:
        p.tick(0.0, 1.2, p.alt_hold(TAKEOFF_ALT))
        turned += abs(wrap(p.s["yaw"] - last))
        last = p.s["yaw"]

    phase(p, "turn to course")
    while abs(wrap(p.heading_to(WAYPOINTS[0]) - p.s["yaw"])) > math.radians(3):
        p.tick(0.0, p.steer(WAYPOINTS[0], 2.0), p.alt_hold(TAKEOFF_ALT))
    p.hold(1.0, climb=0.0)

    phase(p, "transition")
    for i, wp in enumerate(WAYPOINTS):
        while p.dist_to(wp) > 35.0:
            p.tick(CRUISE_SPEED, p.steer(wp), p.alt_hold(CRUISE_ALT))
            if i == 0 and p.s["flight_mode"] == "cruise" and not getattr(p, "_said", False):
                p._said = True
                phase(p, "wing-borne")
        phase(p, f"waypoint {i + 1}")

    # Back-transition early enough to stop over HOVER_AT: v^2 / 2a, plus a margin.
    while p.dist_to(HOVER_AT) > p.s["airspeed"] ** 2 / (2 * DECEL) + 8.0:
        p.tick(CRUISE_SPEED, p.steer(HOVER_AT), p.alt_hold(CRUISE_ALT))
    phase(p, "back-transition")
    c.fw_stop(RID)
    while p.s["flight_mode"] != "hover":
        p.tick(0.0, p.steer(HOVER_AT, 0.8), p.alt_hold(CRUISE_ALT))
    phase(p, "hover")
    p.hold(1.5, climb=0.0)

    phase(p, "descend, turning to land")
    while p.pos[2] > SURVEY_ALT + 1.0:
        p.tick(0.0, p.steer(LAND_AT, 0.6), -1.5)
    while abs(wrap(p.heading_to(LAND_AT) - p.s["yaw"])) > math.radians(3):
        p.tick(0.0, p.steer(LAND_AT, 1.0), p.alt_hold(SURVEY_ALT))

    phase(p, "hover-taxi")
    while p.dist_to(LAND_AT) > 9.5:        # 6 m/s at 2 m/s^2 stops in 9 m
        p.tick(6.0, p.steer(LAND_AT), p.alt_hold(SURVEY_ALT))
    while p.s["airspeed"] > 0.05:
        p.tick(0.0, 0.0, p.alt_hold(SURVEY_ALT))
    p.hold(1.0)

    phase(p, "vertical landing")
    while not p.s["landed"]:
        p.tick(0.0, 0.0, -1.5)
    phase(p, "landed")
    p.hold(3.0)
    phase(p, "end")
    return p.t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=9981)
    args = ap.parse_args()
    for _ in range(120):                       # wait for Godot's IPC to come up
        try:
            c = DepotClient(port=args.port)
            c.fw_state(RID)
            break
        except OSError:
            time.sleep(0.5)
    else:
        print("could not reach the sim", file=sys.stderr)
        return 1
    t_end = fly(c)
    if c.fw_state(RID).get("crashed"):
        print("CRASHED", file=sys.stderr)
        return 1
    print(f"SORTIE_END_SIM_TIME={t_end:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
