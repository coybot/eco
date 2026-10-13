"""Scenarios for the follow bench, all in the open yard south of the Depot building
(x in -6..6 m, y in -8..0 m). Person walking pace 0.3-0.5 m/s (rover cap 0.4 m/s).

Occlusion scenarios use an ORBIT geometry: the target walks an arc around a near-stationary
rover so the occluder (a pole / a low crate placed ~1 m in front of the rover) is crossed
deterministically whatever the controller does. Rover and person start in view of each other.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

ROVER = (0.0, -7.0)


@dataclass
class Scenario:
    name: str
    T: float
    rover: tuple[float, float, float]
    persons: list[dict]                       # {name, waypoints, speed, [start_speed]}
    obstacles: list[dict] = field(default_factory=list)   # {rect:[cx,cy,w,d], h}
    events: list[tuple[float, dict]] = field(default_factory=list)  # (t, person_walk params)
    note: str = ""


def _arc(radius: float, a0: float, a1: float, step_deg: float = 8.0, centre=ROVER):
    n = max(2, int(abs(a1 - a0) / step_deg) + 1)
    out = []
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)       # bearing from north, + = east
        out.append([round(centre[0] + radius * math.sin(a), 3), round(centre[1] + radius * math.cos(a), 3)])
    return out


def _len(wps) -> float:
    return sum(math.dist(a, b) for a, b in zip(wps, wps[1:]))


def _yaw_to(rover, target):
    return math.atan2(target[1] - rover[1], target[0] - rover[0])


def s1_walk() -> Scenario:
    wps = [[-4.0, -6.0], [3.0, -6.0], [3.0, -3.0], [-3.0, -3.0]]
    return Scenario("S1_walk", 46.0, (-6.5, -6.0, 0.0),
                    [dict(name="PersonActor", waypoints=wps, speed=0.3)],
                    events=[(18.0, dict(speed=0.0)), (21.0, dict(speed=0.3))],
                    note="straight legs, two corners, a 3 s pause: smoothness")


def s2_pole() -> Scenario:
    arc = _arc(1.9, -50, 50)
    end = _len(arc) / 0.5
    return Scenario("S2_pole", end + 2.5, (*ROVER, _yaw_to(ROVER, arc[0])),
                    [dict(name="PersonActor", waypoints=arc, speed=0.5)],
                    obstacles=[dict(rect=[ROVER[0], ROVER[1] + 1.0, 0.3, 0.3], h=2.0)],
                    events=[(end - 0.1, dict(speed=0.0))],
                    note="target orbits behind a 0.3 m pole 1 m ahead: ~1-2 s fully hidden")


def s3_close() -> Scenario:
    start = [ROVER[0], ROVER[1] + 1.6]
    wps = [start, [ROVER[0], ROVER[1] + 0.75], [ROVER[0], ROVER[1] + 4.0]]
    return Scenario("S3_close", 20.0, (*ROVER, math.pi / 2),
                    [dict(name="PersonActor", waypoints=wps, speed=0.3)],
                    events=[(2.6, dict(speed=0.0)), (8.0, dict(speed=0.3))],
                    note="target walks in to ~0.9 m, stands 5 s (legs only), then leaves")


def s4_crate() -> Scenario:
    arc = _arc(1.9, -50, 50)
    end = _len(arc) / 0.4
    return Scenario("S4_crate", end + 2.5, (*ROVER, _yaw_to(ROVER, arc[0])),
                    [dict(name="PersonActor", waypoints=arc, speed=0.4)],
                    obstacles=[dict(rect=[ROVER[0], ROVER[1] + 1.15, 0.8, 0.4], h=1.1)],
                    events=[(end - 0.1, dict(speed=0.0))],
                    note="target orbits behind a 1.1 m crate: hips hidden, shoulders visible")


def s5_decoy() -> Scenario:
    arc = _arc(1.9, -50, 50)
    decoy = _arc(0.95, 45, -45)
    end, dend = _len(arc) / 0.5, 1.8 + _len(decoy) / 0.5
    return Scenario("S5_decoy", end + 2.5, (*ROVER, _yaw_to(ROVER, arc[0])),
                    [dict(name="PersonActor", waypoints=arc, speed=0.5),
                     dict(name="DecoyActor", waypoints=decoy, speed=0.0)],
                    obstacles=[],
                    events=[(1.8, dict(person="DecoyActor", speed=0.5)), (end - 0.1, dict(speed=0.0)),
                            (dend - 0.1, dict(person="DecoyActor", speed=0.0))],
                    note="a second person crosses between rover and target, in the opposite direction")


def s2_control() -> Scenario:
    sc = s2_pole()
    sc.name, sc.obstacles, sc.note = "S2c_orbit", [], "S2 geometry with no pole: isolates the orbit from the occlusion"
    return sc


def s4_control() -> Scenario:
    sc = s4_crate()
    sc.name, sc.obstacles, sc.note = "S4c_orbit", [], "S4 geometry with no crate"
    return sc


def s6_cross() -> Scenario:
    arc = _arc(1.9, -50, 50)
    decoy = _arc(2.3, 50, -50)
    end = _len(arc) / 0.5
    dend = _len(decoy) / 0.6
    return Scenario("S6_cross", end + 2.5, (*ROVER, _yaw_to(ROVER, arc[0])),
                    [dict(name="PersonActor", waypoints=arc, speed=0.5),
                     dict(name="DecoyActor", waypoints=decoy, speed=0.6)],
                    events=[(end - 0.1, dict(speed=0.0)), (dend - 0.1, dict(person="DecoyActor", speed=0.0))],
                    note="a second, fully visible person crosses 0.4 m behind the target at the same moment")


def s7_overtake() -> Scenario:
    arc = _arc(1.9, -50, 50)
    decoy = _arc(2.25, -50, 50)
    stop_at = math.radians(40) * 1.9 / 0.5            # target halts at bearing ~ -10 deg
    dend = _len(decoy) / 0.5
    return Scenario("S7_overtake", dend + 1.5, (*ROVER, _yaw_to(ROVER, arc[0])),
                    [dict(name="PersonActor", waypoints=arc, speed=0.5),
                     dict(name="DecoyActor", waypoints=decoy, speed=0.5)],
                    events=[(stop_at, dict(speed=0.0)), (dend - 0.1, dict(person="DecoyActor", speed=0.0))],
                    note="target stops; a second person walking the same way overtakes 0.35 m behind it")


ALL = {f().name: f for f in (s1_walk, s2_pole, s2_control, s3_close, s4_crate, s4_control, s5_decoy, s6_cross, s7_overtake)}
