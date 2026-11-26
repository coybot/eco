#!/usr/bin/env python3
"""Bake what the Istanbul scene's moving world (scripts/istanbul_life.gd)
needs from OpenStreetMap beyond roads.json: which streets cars may not use,
where the traffic lights are and where the trams stop.

Reads the same BBBike Istanbul extract fetch_istanbul.py caches
(assets/istanbul/_cache/Istanbul.osm.pbf; run that tool first). Source:
(c) OpenStreetMap contributors, ODbL 1.0.

Output: drone/sim/godot/assets/istanbul/life.json

  frame        as roads.json (ENU metres around Hagia Sophia, no rotation)
  closed       [[e, n, e, n, ...], flag] per OSM way closed to general
               traffic: flag 1 = no motor vehicles, 2 = buses and taxis only.
               Points are rounded exactly as roads.json's nodes, so a road
               edge is matched by its two end points.
  signals      [e, n] of every highway=traffic_signals node
  tram_stops   [e, n, name] of every tram stop (railway=tram_stop, or a
               public_transport=stop_position with tram=yes)

    uv run --no-project --with osmium python drone/sim/tools/fetch_istanbul_life.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

DATUM_LAT = 41.0086          # Hagia Sophia (fetch_istanbul.py)
DATUM_LON = 28.9802
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))
#: fetch_istanbul.BOUNDS grown a little: e0, n0, e1, n1.
BOUNDS = (-6200.0, -3400.0, 5200.0, 5000.0)

ASSETS = Path(__file__).resolve().parents[1] / "godot" / "assets" / "istanbul"
NO = ("no", "private", "agricultural", "forestry", "discouraged")
ROAD = ("motorway", "trunk", "primary", "secondary", "tertiary", "residential", "unclassified",
        "living_street", "road", "service", "track", "motorway_link", "trunk_link", "primary_link",
        "secondary_link", "tertiary_link")


def project(lon: float, lat: float) -> tuple[float, float]:
    return ((lon - DATUM_LON) * M_PER_DEG_LON, (lat - DATUM_LAT) * M_PER_DEG_LAT)


def inside(e: float, n: float) -> bool:
    return BOUNDS[0] <= e <= BOUNDS[2] and BOUNDS[1] <= n <= BOUNDS[3]


def closed_flag(t: dict) -> int:
    """1: no motor vehicles at all; 2: buses/taxis only; 0: open."""
    mv = t.get("motor_vehicle") or t.get("motorcar") or t.get("vehicle") or t.get("access")
    if mv not in NO:
        return 0
    if t.get("psv") in ("yes", "designated") or t.get("bus") in ("yes", "designated"):
        return 2
    return 1


def main() -> int:
    import osmium

    pbf = ASSETS / "_cache" / "Istanbul.osm.pbf"
    if not pbf.exists():
        print(f"missing {pbf}: run fetch_istanbul.py first", file=sys.stderr)
        return 1

    class H(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.closed, self.signals, self.stops = [], [], []

        def node(self, n):
            if not len(n.tags) or not n.location.valid():
                return
            t = dict(n.tags)
            e, nn = project(n.location.lon, n.location.lat)
            if not inside(e, nn):
                return
            if t.get("highway") == "traffic_signals" or t.get("crossing") == "traffic_signals":
                self.signals.append([round(e, 1), round(nn, 1)])
            if t.get("railway") == "tram_stop" or (t.get("public_transport") == "stop_position"
                                                    and t.get("tram") == "yes"):
                self.stops.append([round(e, 1), round(nn, 1), t.get("name", "")])

        def way(self, w):
            t = dict(w.tags)
            if t.get("highway") not in ROAD:
                return
            f = closed_flag(t)
            if f == 0:
                return
            try:
                pts = [project(nd.location.lon, nd.location.lat) for nd in w.nodes]
            except osmium.InvalidLocationError:
                return
            if not any(inside(e, n) for e, n in pts):
                return
            flat = []
            for e, n in pts:
                flat += [round(e, 1), round(n, 1)]
            self.closed.append([flat, f])

    h = H()
    h.apply_file(str(pbf), locations=True, idx="flex_mem")
    out = {"frame": "ENU metres, datum Hagia Sophia 41.0086 N 28.9802 E, no rotation",
           "source": "OpenStreetMap (ODbL 1.0): access tags, traffic signals, tram stops",
           "closed": h.closed, "signals": h.signals, "tram_stops": h.stops}
    (ASSETS / "life.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"life.json: {len(h.closed)} closed ways, {len(h.signals)} signals, {len(h.stops)} tram stops",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
