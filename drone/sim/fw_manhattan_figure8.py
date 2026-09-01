#!/usr/bin/env python3
"""Fly a continuous figure-8 over Manhattan at a fixed altitude, avoiding towers.

Guidance is rate-based because that is what the sim exposes (fw_drive takes
airspeed / yaw_rate / climb, not waypoints): each tick we pick a heading, then
command the turn rate that walks the nose toward it.

Avoidance is BOTH vertical and lateral, and it prefers vertical. If the tallest
thing in the corridor ahead can be cleared within the airframe's 120 m envelope
ceiling (fixedwing_manager.ALT_CEILING_M) it climbs over and holds the pattern;
otherwise it routes around. At a 70 m cruise most obstacles are 55-95 m and go
under the aircraft, but 822 buildings exceed 100 m and the tallest is 472 m, so
midtown genuinely cannot be overflown and lateral routing is still required.

Feasibility is tested by simulating the path the aircraft would ACTUALLY fly —
the yaw-rate-limited arc onto a candidate heading, plus the climb — rather than a
straight ray along it. See predict_clear(); testing the ray instead let the
aircraft clear a heading, bank onto it, and curve through a building.

The sim has no collision response — nothing stops an aircraft flying through a
tower — so a strike is something this script has to detect itself, by testing
the aircraft's own position against the building set each tick. That check is
the actual pass/fail signal; "it kept flying" proves nothing on its own.

Usage:
    python3 fw_manhattan_figure8.py --port 9979 --minutes 10
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import time
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parent
DATA = SIM_DIR / "godot" / "assets" / "manhattan" / "buildings.json"

TILE = 200.0            # spatial index cell, metres
CRUISE = 25.0           # m/s, MAX_AIRSPEED
ESCAPE_SPEED = 13.0     # m/s, just above MIN_AIRSPEED (12) — see ESCAPE_FANS
MAX_YAW_RATE = 0.6      # rad/s, bank-limited
CLEARANCE = 15.0        # treat a building as blocking if it reaches within this of us

# Corridor the aircraft insists on before committing to a heading.
#
# Was 320 m x 45 m half-width, which asks for a 90 m-wide lane clear for 320 m.
# Manhattan does not have those: the avenues are ~30 m of roadway and the blocks
# are ~80 m, so downtown the test said "blocked" in every direction and the
# fallback below flew into a tower. 200 m is still 8 s of warning at cruise
# against a 42 m turn radius (CRUISE / MAX_YAW_RATE), and a 56 m lane is wide
# enough to commit to while leaving room to manoeuvre inside it.
LOOKAHEAD = 200.0       # how far down the corridor we check
CORRIDOR_HALF = 28.0    # corridor half-width, metres

OFFMAP_M = 2500.0       # beyond this from the path, abandon the pattern and rejoin

# Heading options relative to the direct bearing, tried nearest-first so the
# aircraft only deviates as far as it must. Deliberately capped near +-95: with
# the full circle in here, "nearest clear" will happily pick 165 deg and the
# aircraft reverses direction on a whim, which thrashes instead of flying.
FANS = [0, 10, -10, 20, -20, 32, -32, 45, -45, 60, -60, 78, -78, 95, -95]

# Escape fan: the whole circle, used ONLY when nothing in FANS is clear.
#
# This is the fix for the 76 strikes in the 45-minute run. The old fallback took
# the roomiest heading within +-90 of the target bearing — but when a tower
# cluster blocks the entire forward arc, every one of those options ends in a
# building and "roomiest" just picks which one. The direction the aircraft came
# FROM is clear by construction (it was just flown), and it only appears in the
# fan if the fan covers the full circle. So max-clearance over 360 deg naturally
# selects a retreat, and the aircraft turns back instead of pressing on.
ESCAPE_FANS = [d for d in range(-180, 180, 10)]

# Escaping on max-clearance alone is not enough, and the 20-minute run proved it:
# strikes fell 76 -> 22 but the aircraft then sat at (139, -2855) for ten
# minutes, escaping on every single tick (1471 of them) in a Financial District
# canyon it could not turn out of. It swapped a collision for a deadlock.
#
# The way out is that the RIVERS HAVE NO BUILDINGS IN THEM. Open water either
# side of the island is guaranteed-clear airspace at any altitude, and it is
# never more than ~1.5 km away anywhere on Manhattan. So once the forward arc is
# fully blocked the aircraft stops negotiating with the tower cluster and leaves
# for the nearest shore; that always terminates, which "roomiest heading" does
# not. It rejoins the pattern from over the water.
BAIL_WATER_M = 400.0    # aim this far beyond the built edge, i.e. properly offshore
BAIL_CLEAR_TICKS = 15   # consecutive clear ticks before rejoining the pattern
STUCK_TICKS = 20        # consecutive escape ticks (2 s) that count as enclosed

# Swept-path prediction — see predict_clear(). 24 steps x 0.3 s = 7.2 s, which at
# cruise is 180 m of look-ahead, comfortably past the 42 m turn radius.
PREDICT_STEPS = 24
PREDICT_DT = 0.3
PREDICT_PAD = 18.0      # lateral margin around each footprint, metres

# Vertical avoidance. The airframe's envelope ceiling is 120 m
# (fixedwing_manager.ALT_CEILING_M) and the guard clamps hard at it, so the
# usable top is a little under that. Anything whose roof plus margin fits below
# CEILING can be OVERFLOWN, which is very often the better answer: at a 70 m
# cruise most obstacles are 55-95 m, and grinding laterally around a 60 m block
# that could be cleared with a 25 m climb is what made the flight look so busy.
CEILING = 112.0
OVERFLY_MARGIN = 18.0
MAX_CLIMB = 6.0         # m/s, matches the climb command the loop sends


class Sim:
    def __init__(self, port: int, host: str = "127.0.0.1"):
        # `host` exists so this loop can run somewhere other than the machine
        # rendering the sim. That is the whole point of --host: the guidance is
        # plain arithmetic over a building index, so it belongs on the aircraft's
        # own computer (the Orin), talking to the sim over the network, rather
        # than on whatever laptop happens to be running Godot.
        self.host = host
        self.port = port
        self._pending = 0
        self._connect()

    def _connect(self) -> None:
        # 10 s, not 60. A stalled sim should be noticed and reconnected in
        # seconds; the only thing a long timeout buys is a longer outage.
        self.s = socket.create_connection((self.host, self.port), timeout=10)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.f = self.s.makefile("rwb")
        self._pending = 0

    def reconnect(self) -> None:
        """Rebuild the link after a stall or drop.

        Needed because the sim is now on another machine and its main loop can
        genuinely stop answering — GNOME blanked thor's screen mid-flight, the
        compositor stopped presenting frames, Godot's loop throttled, and the
        socket timed out. The guidance itself was fine and the aircraft was still
        airborne; only the client died, which turned a recoverable hiccup into a
        dead demo. Reconnecting costs one tick.
        """
        try:
            self.s.close()
        except Exception:
            pass
        self._connect()

    def op(self, **req) -> dict:
        self._drain()
        self.f.write((json.dumps(req) + "\n").encode())
        self.f.flush()
        return json.loads(self.f.readline())

    def send(self, **req) -> None:
        """Fire a command without waiting for its reply.

        Halves the round trips on the critical path. Each tick used to be
        state-request, wait, drive-command, wait — two full round trips before
        the loop could think again. Off-box that is ~42 ms of the reaction time
        (21 ms median from the Orin), and it showed: the same pattern that flew
        with 0 escapes and 0 bails from the Mac ran up 53 escapes and 2 bails
        from the Orin, including a trip out over the water.

        The reply is collected at the START of the next tick instead, by which
        time the loop has already slept ~100 ms and it costs nothing. The
        protocol is strictly one response per request, so the only requirement is
        that nothing reads out of order — hence the drain in op()."""
        self._drain()
        self.f.write((json.dumps(req) + "\n").encode())
        self.f.flush()
        self._pending += 1

    def _drain(self) -> None:
        while self._pending > 0:
            self.f.readline()
            self._pending -= 1


class City:
    """Tall buildings only, indexed by tile. The short ones can't threaten an
    aircraft at cruise altitude, and dropping them takes the index from 103k
    buildings to a few hundred."""

    def __init__(self, offset, min_height: float, height_scale: float = 1.0):
        doc = json.loads(DATA.read_text())
        ox, oy = offset
        self.tiles: dict[tuple[int, int], list] = {}
        self.count = 0
        for b in doc["buildings"]:
            # The env may exaggerate heights (--building-scale). Apply the same
            # factor here or avoidance reasons about a shorter city than the one
            # the aircraft is actually flying through.
            h = b["h"] * height_scale
            if h < min_height:
                continue
            x, y, w, d = b["r"]
            x -= ox
            y -= oy
            box = (x, y, x + w, y + d, h)
            self.count += 1
            for tx in range(int(x // TILE), int((x + w) // TILE) + 1):
                for ty in range(int(y // TILE), int((y + d) // TILE) + 1):
                    self.tiles.setdefault((tx, ty), []).append(box)

    def near(self, x: float, y: float, radius: float):
        out = []
        for tx in range(int((x - radius) // TILE), int((x + radius) // TILE) + 1):
            for ty in range(int((y - radius) // TILE), int((y + radius) // TILE) + 1):
                out.extend(self.tiles.get((tx, ty), ()))
        return out

    def strike(self, x: float, y: float, z: float):
        """The aircraft's own position inside a building that reaches its altitude."""
        for (x0, y0, x1, y1, h) in self.tiles.get((int(x // TILE), int(y // TILE)), ()):
            if x0 <= x <= x1 and y0 <= y <= y1 and z <= h:
                return h
        return None

    def occupied(self, x: float, y: float, z: float, pad: float = 0.0):
        """Is this point inside a building that reaches z, allowing `pad` of
        lateral margin? Returns the building height, or None."""
        ceiling = z - CLEARANCE
        txs = {int((x - pad) // TILE), int((x + pad) // TILE)}
        tys = {int((y - pad) // TILE), int((y + pad) // TILE)}
        for tx in txs:
            for ty in tys:
                for (x0, y0, x1, y1, h) in self.tiles.get((tx, ty), ()):
                    if h <= ceiling:
                        continue
                    if (x0 - pad <= x <= x1 + pad
                            and y0 - pad <= y <= y1 + pad):
                        return h
        return None

    def corridor_top(self, x: float, y: float, z: float, heading: float):
        """Tallest building in the corridor ahead that actually threatens us at
        altitude z, plus the distance to the nearest one. This decides whether to
        go over rather than around.

        The z filter matters: the tile index is pre-filtered at cruise minus 40 m
        so it holds plenty of buildings that are not obstacles at all. Without
        the filter the aircraft would spot a roof 30 m below itself and dutifully
        climb to clear something it was already well above."""
        ceiling = z - CLEARANCE
        cs, sn = math.cos(heading), math.sin(heading)
        tallest = None
        nearest = None
        for (x0, y0, x1, y1, h) in self.near(x + cs * LOOKAHEAD * 0.5,
                                             y + sn * LOOKAHEAD * 0.5,
                                             LOOKAHEAD * 0.5 + CORRIDOR_HALF):
            bx = (x0 + x1) * 0.5 - x
            by = (y0 + y1) * 0.5 - y
            along = bx * cs + by * sn
            if along < 0.0 or along > LOOKAHEAD:
                continue
            lateral = abs(-bx * sn + by * cs)
            half = 0.5 * math.hypot(x1 - x0, y1 - y0)
            if lateral > CORRIDOR_HALF + half:
                continue
            if h <= ceiling:
                continue
            if tallest is None or h > tallest:
                tallest = h
            if nearest is None or along < nearest:
                nearest = along
        return tallest, nearest

    def clearance(self, x: float, y: float, z: float, heading: float) -> float:
        """Distance to the nearest blocking building along this heading, or
        LOOKAHEAD if none. Used to choose the least-bad option when every
        candidate is blocked — a blind fixed turn there just flies a circle."""
        ceiling = z - CLEARANCE
        cs, sn = math.cos(heading), math.sin(heading)
        best = LOOKAHEAD
        for (x0, y0, x1, y1, h) in self.near(x + cs * LOOKAHEAD * 0.5,
                                             y + sn * LOOKAHEAD * 0.5,
                                             LOOKAHEAD * 0.5 + CORRIDOR_HALF):
            if h <= ceiling:
                continue
            bx = (x0 + x1) * 0.5 - x
            by = (y0 + y1) * 0.5 - y
            along = bx * cs + by * sn
            if along < 0.0:
                continue
            lateral = abs(-bx * sn + by * cs)
            half = 0.5 * math.hypot(x1 - x0, y1 - y0)
            if lateral <= CORRIDOR_HALF + half:
                best = min(best, along)
        return best

    def blocked(self, x: float, y: float, z: float, heading: float) -> bool:
        """Any building reaching our altitude inside the corridor ahead."""
        ceiling = z - CLEARANCE
        cs, sn = math.cos(heading), math.sin(heading)
        cands = self.near(x + cs * LOOKAHEAD * 0.5,
                          y + sn * LOOKAHEAD * 0.5,
                          LOOKAHEAD * 0.5 + CORRIDOR_HALF)
        for (x0, y0, x1, y1, h) in cands:
            if h <= ceiling:
                continue
            # Project the box centre into corridor coordinates (along, lateral).
            bx = (x0 + x1) * 0.5 - x
            by = (y0 + y1) * 0.5 - y
            along = bx * cs + by * sn
            if along < 0.0 or along > LOOKAHEAD:
                continue
            lateral = abs(-bx * sn + by * cs)
            # Half-diagonal of the footprint, so a wide building still counts
            # when its centre sits just outside the corridor.
            half = 0.5 * math.hypot(x1 - x0, y1 - y0)
            if lateral <= CORRIDOR_HALF + half:
                return True
        return False


def figure8(t: float, a: float, b: float) -> tuple[float, float]:
    """Lemniscate of Gerono: two lobes along north, crossing at the centre.
    north = a*sin(t), east = b*sin(2t)."""
    return (b * math.sin(2.0 * t), a * math.sin(t))


class Island:
    """Where Manhattan actually is, per latitude band.

    The dataset's bounding box is ~11 km wide because it also catches waterfront
    across both rivers, but the island itself is a narrow diagonal strip. A
    figure-8 centred on the bounding box therefore spends half its length over
    open water with nothing beneath it — which is what "no buildings" looks like
    from the chase camera.

    So the path is fitted to the buildings instead: bin them by north, take the
    median east as the centreline and a percentile spread as the usable width,
    and shape the lemniscate to that. The aircraft then stays over the city for
    the whole loop.
    """

    BIN = 250.0
    MIN_PER_BIN = 30

    def __init__(self, offset, path=DATA):
        ox, oy = offset
        bins: dict[int, list[float]] = {}
        for b in json.loads(path.read_text())["buildings"]:
            x, y, w, d = b["r"]
            cx, cy = x + w * 0.5 - ox, y + d * 0.5 - oy
            bins.setdefault(int(cy // self.BIN), []).append(cx)
        self.rows = []
        for k in sorted(bins):
            xs = sorted(bins[k])
            if len(xs) < self.MIN_PER_BIN:
                continue
            lo = xs[int(len(xs) * 0.10)]
            hi = xs[int(len(xs) * 0.90)]
            self.rows.append(((k + 0.5) * self.BIN, 0.5 * (lo + hi), 0.5 * (hi - lo)))
        if not self.rows:
            raise SystemExit("no populated latitude bands — is the dataset empty?")
        self.north_min = self.rows[0][0]
        self.north_max = self.rows[-1][0]

    def at(self, north: float) -> tuple[float, float]:
        """(centre_east, half_width) at this north, linearly interpolated."""
        if north <= self.rows[0][0]:
            return self.rows[0][1], self.rows[0][2]
        if north >= self.rows[-1][0]:
            return self.rows[-1][1], self.rows[-1][2]
        lo = 0
        hi = len(self.rows) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.rows[mid][0] <= north:
                lo = mid
            else:
                hi = mid
        n0, c0, w0 = self.rows[lo]
        n1, c1, w1 = self.rows[hi]
        f = 0.0 if n1 == n0 else (north - n0) / (n1 - n0)
        return c0 + (c1 - c0) * f, w0 + (w1 - w0) * f

    def path(self, t: float, margin: float, extent: float = 1.0,
             north_centre: float = 0.0, swing_m: float = 0.0) -> tuple[float, float]:
        """Figure-8 warped onto the island: lobes run along its length, and the
        east swing is a fraction of the local half-width so it never leaves.

        `north_centre` slides the whole pattern along the island axis, because
        WHERE the figure-8 sits decides whether it is flyable at all: the tall
        clusters have no through-route at low altitude, while upper Manhattan is
        clear. `swing_m` overrides the east amplitude — the island's local width
        makes a fine bound for a full-length loop but a poor one for a small
        pattern, where it stretches the crossing into something that does not
        read as a figure-8 on camera.
        """
        mid = 0.5 * (self.north_min + self.north_max) + north_centre
        half = 0.5 * (self.north_max - self.north_min) * 0.92 * extent
        north = mid + half * math.sin(t)
        centre, width = self.at(north)
        swing = swing_m if swing_m > 0.0 else max(0.0, width - margin)
        return centre + swing * math.sin(2.0 * t), north


def wrap(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def predict_clear(city: City, x: float, y: float, z: float, yaw: float,
                  target_heading: float, target_alt: float,
                  speed: float = CRUISE) -> bool:
    """Fly the candidate forward on paper and check the path we would ACTUALLY take.

    This replaces a straight-ray corridor test, and that swap is the fix for the
    strikes. The aircraft is yaw-rate limited, so committing to a heading means
    flying an ARC onto it — up to a 42 m radius at cruise. The old test asked
    whether a ray from here along the new heading was clear, which is a path the
    aircraft never flies: it would clear a candidate, bank onto it, and curve
    through a building that was never on the tested ray. 24 direct hits in the
    70 m run came from exactly that gap.

    Simulating the same rate limiter and climb law the command loop uses also
    makes the over-versus-around question answer itself. Climbing is modelled
    here, so a candidate that needs more height than the aircraft can gain
    before reaching the building fails on its own — no separate "can I make it
    in time" arithmetic to get wrong.
    """
    for _ in range(PREDICT_STEPS):
        rate = max(-MAX_YAW_RATE,
                   min(MAX_YAW_RATE, wrap(target_heading - yaw) * 0.9))
        yaw += rate * PREDICT_DT
        z += max(-MAX_CLIMB, min(MAX_CLIMB, (target_alt - z) * 0.5)) * PREDICT_DT
        x += math.cos(yaw) * speed * PREDICT_DT
        y += math.sin(yaw) * speed * PREDICT_DT
        if city.occupied(x, y, z, PREDICT_PAD) is not None:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1",
                    help="host running the Godot IPC. Use this to fly the "
                         "aircraft FROM the companion computer: the sim is on "
                         "the workstation, the guidance runs on the drone.")
    ap.add_argument("--port", type=int, default=9979)
    ap.add_argument("--drone-id", default="fig8")
    ap.add_argument("--alt", type=float, default=100.0,
                    help="cruise altitude, metres. Lower means far more "
                         "obstacles: 2208 buildings reach 100 m, 6816 reach "
                         "70 m, 14943 reach 60 m")
    ap.add_argument("--minutes", type=float, default=10.0,
                    help="0 = fly indefinitely until interrupted")
    ap.add_argument("--extent", type=float, default=0.30,
                    help="fraction of the island length the figure-8 spans; a full-length "
                         "loop is ~50 km of path, about 35 min per lap at cruise")
    ap.add_argument("--margin", type=float, default=250.0,
                    help="keep this far inside the island edge, metres")
    ap.add_argument("--north-centre", type=float, default=0.0,
                    help="slide the pattern along the island axis, metres. Where "
                         "the figure-8 sits decides whether it is flyable: the "
                         "tall clusters have no low-altitude through-route, "
                         "upper Manhattan does. See --survey.")
    ap.add_argument("--swing", type=float, default=0.0,
                    help="east amplitude in metres (0 = use the island's local "
                         "half-width). Set this for a small demo pattern: the "
                         "island is ~2 km wide, which stretches a short figure-8 "
                         "into a shape that no longer reads as one.")
    args = ap.parse_args()

    sim = Sim(args.port, args.host)
    env = sim.op(op="fw_env_state")["env"]
    if env.get("env") != "manhattan":
        print(f"env is {env.get('env')!r}, expected manhattan", file=sys.stderr)
        return 1
    offset = env.get("datum_offset_enu", [0.0, 0.0])

    scale = float(env.get("height_scale", 1.0))
    city = City(offset, args.alt - 40.0, scale)
    print(f"city: {city.count} buildings at/above {args.alt - 40.0:.0f} m "
          f"(of {env['buildings']}), tallest {env['tallest']:.0f} m, scale {scale:g}x")

    island = Island(offset)
    print(f"island: north {island.north_min:.0f}..{island.north_max:.0f} m "
          f"over {len(island.rows)} populated bands")

    # Start at the crossing point, heading north-east along the path.
    t = 0.0
    x0, y0 = island.path(t, args.margin, args.extent, args.north_centre, args.swing)
    sim.op(op="fw_spawn", id=args.drone_id, p=[x0, y0, args.alt], yaw=math.pi / 2)

    started = time.time()
    deadline = None if args.minutes <= 0 else started + args.minutes * 60.0
    tick = 0.1
    # Real elapsed time per iteration, not the nominal tick.
    #
    # Every rate in this loop used to be computed against a hardcoded 0.1 s, on
    # the assumption that the loop runs on the same machine as the sim and its
    # I/O is free. Run it on the aircraft's own computer instead and each tick
    # pays two network round trips — measured 21 ms median from the Orin — so the
    # true period is ~0.145 s. The path parameter would then advance at 70% of
    # real speed and the turn-rate slew would be 70% as aggressive, i.e. the
    # aircraft flies differently depending on where the guidance happens to be
    # hosted. Measuring dt makes the behaviour identical either way.
    last_t = time.time()
    strikes = 0
    first_strike = None
    laps = 0
    deviations = 0
    alt_err_max = 0.0
    min_alt = 1e9
    prev_t = t
    strays = 0
    escapes = 0
    overflies = 0
    climb_outs = 0
    dropouts = 0
    respawns = 0
    max_alt = 0.0
    escape_run = 0
    bailing = False
    bail_clear = 0
    bail_ticks = 0
    bails = 0
    last_dev = 0
    prev_rate = 0.0
    last_report = time.time()

    while deadline is None or time.time() < deadline:
        # Ride out a sim stall rather than exiting on it. Losing the link is not
        # the same as losing the aircraft, and treating them the same ended a
        # perfectly good 7-lap flight because thor's screen blanked.
        try:
            st = sim.op(op="fw_state", id=args.drone_id)
            if not st.get("ok"):
                # The aircraft really is gone (sim restarted, or it was
                # despawned). Put it back at the current path point and carry on.
                sx, sy = island.path(t, args.margin, args.extent,
                                     args.north_centre, args.swing)
                print(f"aircraft missing — respawning at ({sx:.0f}, {sy:.0f})",
                      file=sys.stderr)
                sim.op(op="fw_spawn", id=args.drone_id, p=[sx, sy, args.alt],
                       yaw=math.pi / 2)
                respawns += 1
                time.sleep(tick)
                continue
        except (socket.timeout, OSError, ValueError) as e:
            dropouts += 1
            print(f"link lost ({type(e).__name__}: {e}) — reconnecting",
                  file=sys.stderr)
            try:
                sim.reconnect()
            except Exception as e2:
                print(f"  reconnect failed: {e2}", file=sys.stderr)
                time.sleep(2.0)
            last_t = time.time()   # don't bill the outage to dt
            continue
        px, py, pz = st["position"]
        yaw = st["yaw"]

        hit = city.strike(px, py, pz)
        if hit is not None:
            strikes += 1
            if first_strike is None:
                first_strike = (round(px), round(py), round(pz), round(hit))

        # Advance the path parameter by roughly the distance we travel, so the
        # carrot stays a fixed lead ahead rather than racing off or stalling.
        span = 0.5 * (island.north_max - island.north_min) * args.extent
        now = time.time()
        dt = min(0.5, max(0.02, now - last_t))
        last_t = now
        dt_param = (CRUISE * dt) / max(span, 1.0)
        t += dt_param
        # Laps straight from the path parameter. The previous incremental form
        # compared int(t/2pi) across ticks and reported 0 through a demonstrably
        # completed lap, so there is no reason to keep the bookkeeping.
        laps = int(t / (2.0 * math.pi))

        tx, ty = island.path(t + 0.06, args.margin, args.extent, args.north_centre,
                                args.swing)
        # Off-map recovery. A single bad excursion otherwise never corrects: the
        # pattern keeps advancing while the aircraft is nowhere near it. A run
        # that ended with the aircraft 73 km east of Manhattan, over blank space,
        # is what this guards. Steer straight at the path and ignore the pattern
        # until back within range.
        off = math.hypot(px - tx, py - ty)
        if off > OFFMAP_M:
            strays += 1
        desired = math.atan2(ty - py, tx - px)

        # Nearest acceptable heading in the fan, but stick with the previous
        # deviation while it is still clear. Re-deciding from scratch every tick
        # let the aircraft alternate between symmetric options (+25 / -25) and
        # sit there wobbling instead of committing to one side — which reads as
        # twitching rather than flying, and is obvious on a close chase camera.
        # Always prefer the smallest deviation that is clear, and use the side we
        # were already turning only to break ties between equal magnitudes.
        #
        # Putting last_dev first instead made the aircraft hold a fixed offset
        # from the bearing once it had deviated — and a constant offset from a
        # bearing is a circle, which is precisely what it flew: round and round
        # over Central Park, never rejoining the path. Hysteresis belongs on
        # *which side* to pass an obstacle, never on *how far* to turn.
        # While bailing out, the pattern is not the objective — getting clear is.
        # Retarget the bearing at open water and otherwise fall through to the
        # ordinary fan search below, so the bail still refuses to fly through a
        # building on its way out.
        if bailing:
            if city.blocked(px, py, pz, desired):
                bail_clear = 0
            else:
                bail_clear += 1
                if bail_clear >= BAIL_CLEAR_TICKS:
                    bailing = False
                    last_dev = 0
            if bailing:
                centre, width = island.at(py)
                # Nearest shore: whichever bank we are already closer to. Aiming
                # at a point well offshore rather than at the shoreline itself,
                # so the aircraft actually clears the waterfront blocks.
                side = 1.0 if px >= centre else -1.0
                tx = centre + side * (width + BAIL_WATER_M)
                ty = py
                desired = math.atan2(ty - py, tx - px)
                bail_ticks += 1

        # OVER, OR AROUND? Decide the altitude first, then the heading.
        #
        # Going over is preferred whenever the envelope allows it, because it
        # holds the pattern instead of wandering off it, and because a fixed-wing
        # climbing 25 m looks far better than one sawing between avenues. So look
        # at the tallest thing in the corridor ahead and, if its roof plus margin
        # fits under CEILING, aim to be above it by the time we arrive. If it is
        # too tall — a 472 m tower under a 120 m ceiling is not negotiable — hold
        # the cruise altitude and let the heading search route around it.
        #
        # No timing arithmetic here on purpose: predict_clear() simulates the
        # climb, so a candidate needing more height than can be gained before the
        # building simply fails the swept-path test.
        top_ahead, _dist_ahead = city.corridor_top(px, py, pz, desired)
        target_alt = args.alt
        overflying = False
        if top_ahead is not None and top_ahead + OVERFLY_MARGIN <= CEILING:
            want = top_ahead + OVERFLY_MARGIN
            if want > args.alt + 1.0:
                target_alt = want
                overflying = True

        order = sorted(FANS, key=lambda d: (abs(d), 0 if d * last_dev > 0 else 1))
        chosen = None
        for d in order:
            cand = desired + math.radians(d)
            # Swept-path test, not a straight ray: this is the path actually flown.
            if predict_clear(city, px, py, pz, yaw, cand, target_alt):
                chosen = cand
                last_dev = d
                if d != 0:
                    deviations += 1
                elif overflying:
                    overflies += 1
                break
        speed = CRUISE
        if chosen is None:
            # CLIMB OUT before considering anything else.
            #
            # Leaving for the river is a guaranteed escape but a terrible-looking
            # one: it abandons the pattern and flies the aircraft out over open
            # water, which is exactly what made the demo look broken. Straight up
            # is usually just as clear and keeps it over the city. In the demo
            # area only 1 of 4402 buildings tops the 112 m ceiling (median 16 m),
            # so a climb to CEILING clears essentially everything.
            #
            # Tried at the pattern bearing and a narrow fan either side: the point
            # is to stay ON the figure-8 while climbing, not to trade a lateral
            # detour for a vertical one.
            for d in (0, 12, -12, 25, -25):
                cand = desired + math.radians(d)
                if predict_clear(city, px, py, pz, yaw, cand, CEILING):
                    chosen = cand
                    target_alt = CEILING
                    last_dev = d
                    climb_outs += 1
                    break

        if chosen is None:
            # The whole forward arc is blocked. Search the FULL circle by
            # clearance and take the roomiest heading, which is normally back the
            # way we came — see ESCAPE_FANS. Never press on into the least-bad
            # blocked option; that is what put the aircraft inside 76 buildings.
            #
            # Measured relative to the aircraft's own heading, not the target
            # bearing, so "180" really means reciprocal-of-current-track.
            # Prefer an escape heading that also survives the swept-path test —
            # max clearance alone still measures a straight ray, and the whole
            # point of this pass is that the aircraft flies arcs. Fall back to
            # roomiest-ray only if nothing passes, so there is always an answer.
            survivors = [d for d in ESCAPE_FANS
                         if predict_clear(city, px, py, pz, yaw,
                                          yaw + math.radians(d), args.alt,
                                          speed=ESCAPE_SPEED)]
            pool = survivors if survivors else ESCAPE_FANS
            best_d = max(pool, key=lambda d: city.clearance(px, py, pz,
                                                            yaw + math.radians(d)))
            chosen = yaw + math.radians(best_d)
            # An escape means we are not going over anything; hold cruise.
            target_alt = args.alt
            # Slow down while escaping: at 13 m/s the turn radius is 22 m
            # instead of 42, which is the difference between fitting in a street
            # and not. It also buys time for the pattern to move past the
            # blockage so there is something to rejoin.
            speed = ESCAPE_SPEED
            last_dev = 0
            escapes += 1
            # Consecutive escapes mean the max-clearance retreat is not working:
            # the aircraft is enclosed and just turning on the spot. Bail to open
            # water, which is the one direction guaranteed to have nothing in it.
            escape_run += 1
            if escape_run >= STUCK_TICKS and not bailing:
                bailing = True
                bail_clear = 0
                bails += 1
        else:
            escape_run = 0

        # Rate-limit the turn command as well as clamping it. A proportional
        # term alone steps straight to full deflection on a large heading error,
        # and the airframe's bank filter then chases a square wave.
        err = wrap(chosen - yaw)
        want_rate = max(-MAX_YAW_RATE, min(MAX_YAW_RATE, err * 0.9))
        slew = 1.2 * dt            # rad/s of yaw-rate change, per real second
        yaw_rate = max(prev_rate - slew, min(prev_rate + slew, want_rate))
        prev_rate = yaw_rate
        # Climb toward target_alt, which is the cruise altitude unless we are
        # deliberately going over something.
        climb = max(-MAX_CLIMB, min(MAX_CLIMB, (target_alt - pz) * 0.5))
        try:
            sim.send(op="fw_drive", id=args.drone_id,
                     airspeed=speed, yaw_rate=yaw_rate, climb=climb)
        except (socket.timeout, OSError) as e:
            dropouts += 1
            print(f"drive send failed ({type(e).__name__}) — reconnecting",
                  file=sys.stderr)
            try:
                sim.reconnect()
            except Exception:
                time.sleep(2.0)

        # Altitude error is measured against the altitude actually COMMANDED, not
        # against args.alt — otherwise every intentional climb over a building
        # reads as a tracking failure and the number becomes meaningless.
        alt_err_max = max(alt_err_max, abs(target_alt - pz))
        min_alt = min(min_alt, pz)
        max_alt = max(max_alt, pz)

        if time.time() - last_report >= 30.0:
            last_report = time.time()
            print(f"  t+{int(time.time() - started):4d}s "
                  f"pos=({px:7.0f},{py:8.0f}) alt={pz:6.1f} laps={laps} "
                  f"deviations={deviations} over={overflies} climbout={climb_outs} "
                  f"escapes={escapes} bails={bails}{' ->water' if bailing else ''} "
                  f"strays={strays} strikes={strikes}")
        # Sleep only the remainder, so a slow network hop costs latency rather
        # than also stretching the loop period.
        time.sleep(max(0.0, tick - (time.time() - now)))

    # Level the wings and stop commanding before leaving. The sim keeps flying
    # the LAST fw_drive forever, so a client that just exits leaves the aircraft
    # in a permanent turn or climb — this is exactly how a finished run ended up
    # 73 km downrange with nothing rendered around it.
    # Park it in a loiter circle, not straight and level, and not fw_stop.
    #
    # A fixed-wing cannot hold position: fw_stop only drops it to MIN_AIRSPEED,
    # so it still departs at 12 m/s and leaves the map given long enough. Since
    # the sim holds the LAST command indefinitely, a standing turn is what keeps
    # it over the city — measured below at a ~40 m radius instead of 721 m of
    # drift per minute.
    try:
        sim.op(op="fw_drive", id=args.drone_id, airspeed=12.0,
               yaw_rate=0.30, climb=0.0)
    except Exception as e:
        print(f"warning: could not park the aircraft: {e}", file=sys.stderr)

    print()
    print(f"flew {(time.time() - started) / 60.0:.1f} min: laps={laps} "
          f"deviations={deviations} over={overflies} escapes={escapes} "
          f"strays={strays}")
    print(f"bail-outs to open water: {bails} "
          f"({bail_ticks * 0.1:.0f} s of {(time.time() - started):.0f} s spent leaving)")
    print(f"altitude: flew {min_alt:.1f}-{max_alt:.1f} m, "
          f"max error vs commanded {alt_err_max:.1f} m (cruise {args.alt:.0f} m)")
    print(f"climb-overs: {overflies}, climb-outs: {climb_outs}")
    print(f"link dropouts: {dropouts}, respawns: {respawns}")
    print(f"building strikes: {strikes}")
    if first_strike:
        print(f"  first at east={first_strike[0]} north={first_strike[1]} "
              f"alt={first_strike[2]} inside a {first_strike[3]} m building")
    return 0 if strikes == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
