"""The mission's geofence, enforced on board at goto()/drive(): no-fly zones
refused, moves out of the search area shortened at its edge, the transit in
from a home outside the area allowed, and the fence gone when the mission ends.

Run: cd eco && python3 -m pytest drone/common/tests/test_geofence.py -v
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geofence as gf  # noqa: E402
import reasoning_loop as rl  # noqa: E402
from vehicle_class import get_class  # noqa: E402

DATUM = (37.0, -122.0)


def ll(east, north):
    lat = DATUM[0] + math.degrees(north / gf.EARTH_RADIUS_M)
    lon = DATUM[1] + math.degrees(east / (gf.EARTH_RADIUS_M * math.cos(math.radians(DATUM[0]))))
    return {"lat": lat, "lon": lon}


AREA = [ll(0, 0), ll(400, 0), ll(400, 300), ll(0, 300)]
NFZ = [ll(150, 100), ll(250, 100), ll(250, 200), ll(150, 200)]
FENCE = {"keep_in": AREA, "no_fly": [NFZ]}


@pytest.fixture
def fence():
    return gf.Geofence.from_latlon(FENCE, DATUM)


def test_lat_lon_lands_in_the_local_frame(fence):
    xs = [round(p[0]) for p in fence.keep_in]
    ys = [round(p[1]) for p in fence.keep_in]
    assert xs == [0, 400, 400, 0] and ys == [0, 0, 300, 300]


def test_a_move_into_or_through_a_no_fly_zone_is_refused(fence):
    ok, _, why = fence.check_move((50, 150), (200, 150))
    assert not ok and "inside no-fly zone" in why
    ok, _, why = fence.check_move((50, 150), (350, 150))
    assert not ok and "crosses no-fly zone" in why


def test_a_move_out_of_the_area_stops_just_inside_it(fence):
    ok, to, why = fence.check_move((350, 250), (600, 250))
    assert ok and "shortened" in why
    assert 395 < to[0] < 400 and abs(to[1] - 250) < 1e-6


def test_the_transit_in_from_outside_is_only_held_to_the_zones(fence):
    ok, to, _ = fence.check_move((-100, 250), (50, 250))
    assert ok and to == (50, 250)
    ok, _, _ = fence.check_move((-100, 150), (300, 150))
    assert not ok


def test_a_move_entirely_inside_is_untouched(fence):
    assert fence.check_move((50, 50), (350, 50)) == (True, (350, 50), "")


class Backend:
    def __init__(self, pos=(50.0, 250.0)):
        self.pos = [pos[0], pos[1], 20.0]
        self.gotos = []
        self.drives = []

    def get_pose(self):
        return (self.pos[0], self.pos[1], self.pos[2], 0.0)

    def goto(self, north_m, east_m, alt_m, **kw):
        self.gotos.append((east_m, north_m))
        self.pos = [east_m, north_m, alt_m]
        return True

    def drive(self, *a, **k):
        self.drives.append(a)

    def get_battery(self):
        return {"remaining": 100.0}

    def takeoff(self, alt):
        self.pos[2] = alt
        return True

    def land(self, heading_deg=None):
        self.pos[2] = 0.0
        return True

    def rtl(self, alt_m=None):
        self.pos = [0.0, 0.0, self.pos[2]]
        return True

    def abort(self):
        pass

    def clear_abort(self):
        pass

    def configure_safety(self, **kw):
        return True

    def log_event(self, *a, **k):
        pass


def test_the_wrapper_refuses_shortens_and_passes_through(fence):
    inner = Backend()
    said = []
    b = gf.GeofencedBackend(inner, fence, said.append)
    assert b.goto(150, 200, 20) is False                 # into the zone
    assert inner.gotos == []
    assert b.goto(250, 700, 20) is True                  # out of the area
    assert inner.gotos[-1][0] < 400
    assert b.get_battery() == {"remaining": 100.0}        # everything else passes through
    assert any("refused" in m for m in said) and any("shortened" in m for m in said)


def test_drive_outside_an_entered_area_heads_back_in(fence):
    inner = Backend((50, 250))
    b = gf.GeofencedBackend(inner, fence, lambda m: None)
    b.drive(5.0, 0.0)
    assert inner.drives                                   # inside: driven normally
    inner.pos = [450.0, 250.0, 20.0]                      # drifted out
    b.drive(5.0, 0.0)
    assert len(inner.drives) == 1
    back = inner.gotos[-1]
    assert fence.in_area(back)


@pytest.fixture
def no_sdk(monkeypatch):
    monkeypatch.setattr(rl, "DRONE_SDK_AVAILABLE", False)
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)


def test_a_mission_fence_is_active_for_that_mission_only(no_sdk):
    inner = Backend((50, 250))
    loop = rl.MissionLoop(backend=inner, conversation_id="c", geo_origin=DATUM,
                          vehicle_class=get_class("quadcopter"))
    seen = []
    loop._exec_hold = lambda phase: (seen.append(loop.backend), {"success": True, "actions": 0})[1]
    m = rl.Mission(mission_id="m", phases=[{"type": "hold", "seconds": 0}], geofence=FENCE)
    loop.run(m)
    assert seen and isinstance(seen[0], gf.GeofencedBackend)
    assert loop.backend is inner


def test_a_go_to_gps_into_a_zone_is_refused_in_flight(no_sdk):
    inner = Backend((50, 150))
    loop = rl.MissionLoop(backend=inner, conversation_id="c", geo_origin=DATUM,
                          vehicle_class=get_class("quadcopter"))
    loop._home_lat, loop._home_lon = DATUM
    loop.backend = gf.GeofencedBackend(inner, gf.Geofence.from_latlon(FENCE, DATUM),
                                       lambda m: None)
    target = ll(200, 150)
    r = loop._exec_go_to_gps({"type": "go_to_gps", "lat": target["lat"], "lon": target["lon"],
                              "alt_m": 20})
    assert r.get("failed")
    assert inner.gotos == []


def test_from_dict_carries_the_fence():
    m = rl.Mission.from_dict({"mission_id": "m", "phases": [], "geofence": FENCE})
    assert m.geofence == FENCE
