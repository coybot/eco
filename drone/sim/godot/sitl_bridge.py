#!/usr/bin/env python3
"""Fly a Godot aircraft from a real autopilot: ArduPilot or PX4 SITL over MAVLink.

The autopilot does the flying (its own flight dynamics, its own controllers,
the mission code under test talking to it over MAVLink, as in
drone/sim/survey_sitl.py). This bridge reads where the autopilot says
the aircraft is and puts the Godot aircraft there (IPC fw_pose), so the world
the camera films, the obstacles it can strike and the scene the GCS shows are
Godot's. Scenes that know where on Earth they are report it in fw_env_state
(origin_latlng); the bridge needs that to place the aircraft.

    # 1. Where SITL's home must be for this scene (prints ArduPilot's --home):
    python3 drone/sim/godot/sitl_bridge.py --godot 127.0.0.1:9978 --print-home 0,0

    # 2. Start the autopilot with that home, e.g. ArduPlane or ArduCopter SITL:
    sim_vehicle.py -v ArduPlane --custom-location=<lat,lon,alt,hdg> --out=udp:127.0.0.1:14560
    #    or PX4: PX4_HOME_LAT=... PX4_HOME_LON=... PX4_HOME_ALT=... make px4_sitl sihsim_airplane

    # 3. Bridge it (the aircraft appears and follows the autopilot):
    python3 drone/sim/godot/sitl_bridge.py --godot 127.0.0.1:9978 --mavlink udpin:0.0.0.0:14560 --id sitl1

Frames: GLOBAL_POSITION_INT lat/lon go to the scene's ENU by the same
equirectangular anchor the scene was baked on (fw_env_state origin_latlng);
height is relative_alt above home plus the scene's ground under home. ATTITUDE
is NED (yaw clockwise from north), turned into the sim's yaw counter-clockwise
from east; roll and pitch keep their sense (right wing down, nose up).
Needs pymavlink (uv pip install pymavlink).
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import time

R_EARTH = 6378137.0


class Godot:
    def __init__(self, host: str, port: int):
        self.s = socket.create_connection((host, port), timeout=10.0)
        self.f = self.s.makefile("rwb")

    def __call__(self, **req) -> dict:
        self.f.write((json.dumps(req) + "\n").encode())
        self.f.flush()
        return json.loads(self.f.readline() or b"{}")


def scene_origin(g: Godot) -> tuple[float, float]:
    env = g(op="fw_env_state").get("env") or {}
    o = env.get("origin_latlng")
    if not o or len(o) < 2:
        sys.exit("the loaded scene has no origin_latlng (fw_env_state reports none)")
    return float(o[0]), float(o[1])


def enu_of(lat: float, lon: float, origin: tuple[float, float]) -> tuple[float, float]:
    lat0, lon0 = origin
    return (math.radians(lon - lon0) * R_EARTH * math.cos(math.radians(lat0)),
            math.radians(lat - lat0) * R_EARTH)


def latlon_of(e: float, n: float, origin: tuple[float, float]) -> tuple[float, float]:
    lat0, lon0 = origin
    return (lat0 + math.degrees(n / R_EARTH), lon0 + math.degrees(e / (R_EARTH * math.cos(math.radians(lat0)))))


def pose_from(gpi, att, origin, home_ground: float) -> dict:
    """fw_pose fields from a GLOBAL_POSITION_INT and an ATTITUDE message."""
    e, n = enu_of(gpi.lat / 1e7, gpi.lon / 1e7, origin)
    u = home_ground + gpi.relative_alt / 1000.0
    yaw_enu = math.atan2(math.sin(math.pi / 2 - att.yaw), math.cos(math.pi / 2 - att.yaw))
    return {"p": [e, n, u], "rpy": [att.roll, att.pitch, yaw_enu],
            "v": [gpi.vy / 100.0, gpi.vx / 100.0, -gpi.vz / 100.0]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--godot", default="127.0.0.1:9978", help="host:port of the sim's IPC")
    ap.add_argument("--mavlink", default="udpin:0.0.0.0:14560", help="pymavlink connection string")
    ap.add_argument("--id", default="sitl1", help="the Godot aircraft to drive")
    ap.add_argument("--airframe", default="fixedwing", choices=["fixedwing", "vtol"])
    ap.add_argument("--home-enu", default=None, help="E,N of SITL's home in the scene (default: from its first fix)")
    ap.add_argument("--print-home", default=None, metavar="E,N",
                    help="print the --custom-location for SITL to start at this scene point, and exit")
    ap.add_argument("--rate-hz", type=float, default=50.0)
    args = ap.parse_args()

    host, port = args.godot.rsplit(":", 1)
    g = Godot(host, int(port))
    origin = scene_origin(g)
    if args.print_home:
        e, n = (float(v) for v in args.print_home.split(","))
        lat, lon = latlon_of(e, n, origin)
        ground = (g(op="fw_ground", points=[[e, n]]).get("ground") or [0.0])[0]
        print(f"{lat:.7f},{lon:.7f},{ground:.1f},0")
        return 0

    from pymavlink import mavutil
    m = mavutil.mavlink_connection(args.mavlink)
    print(f"waiting for the autopilot on {args.mavlink} ...", flush=True)
    m.wait_heartbeat(timeout=60)
    gpi = att = None
    home_ground = None
    spawned = False
    period = 1.0 / args.rate_hz
    last = 0.0
    sent = 0
    while True:
        msg = m.recv_match(type=["GLOBAL_POSITION_INT", "ATTITUDE"], blocking=True, timeout=5)
        if msg is None:
            print("no position from the autopilot for 5 s", flush=True)
            continue
        if msg.get_type() == "GLOBAL_POSITION_INT":
            gpi = msg
        else:
            att = msg
        if gpi is None or att is None:
            continue
        if home_ground is None:
            he, hn = (float(v) for v in args.home_enu.split(",")) if args.home_enu else \
                enu_of(gpi.lat / 1e7, gpi.lon / 1e7, origin)
            home_ground = (g(op="fw_ground", points=[[he, hn]]).get("ground") or [0.0])[0]
        now = time.monotonic()
        if now - last < period:
            continue
        last = now
        pose = pose_from(gpi, att, origin, home_ground)
        if not spawned:
            req = {"op": "fw_spawn", "id": args.id, "p": pose["p"], "yaw": pose["rpy"][2],
                   "airframe": args.airframe, "ceiling": 3000.0}
            g(**req)
            spawned = True
            print(f"{args.id} spawned at ENU {[round(v, 1) for v in pose['p']]}", flush=True)
        r = g(op="fw_pose", id=args.id, **pose)
        if not r.get("ok"):
            print("fw_pose:", r, flush=True)
        sent += 1
        if sent % int(args.rate_hz * 5) == 0:
            st = g(op="fw_state", id=args.id)
            print(f"{args.id} at {[round(v, 1) for v in st.get('position', [])]}, agl {st.get('agl')}, "
                  f"crashed {st.get('crashed')}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
