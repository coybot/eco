"""Envelope-guarded SimBackend — a last-resort backstop, and a scoring signal.

The demo claims the aircraft avoids a wall using its camera. Verifying that
claim needs a way to tell "it avoided the wall" apart from "it happened not to
hit the wall this time", and it needs the sim to not quietly fly through solid
geometry when the model gets it wrong (fixed-wing bodies in this sim have no
collision shape, so nothing stops them).

This wraps SimBackend and checks the occupancy grid ahead of the aircraft — on
every drive command AND continuously on its own watchdog thread, because most
of a mission's wall-clock is spent waiting on VLM inference with no drive
command in flight at all. If the aircraft is about to fly into structure, it
turns away and logs an `envelope_protection` event.

The point is NOT to make avoidance work — an intervention means the model
FAILED to avoid the wall itself. That is why take selection requires zero
interventions: the guard exists so a failure is recorded and visible rather
than becoming a clip of an aircraft passing through a wall. Treat the event
count as the score, not as a safety feature doing its job.
"""
from __future__ import annotations

import base64
import math
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

from backends import SimBackend  # noqa: E402


class EnvelopeGuardedSimBackend(SimBackend):
    LOOKAHEAD_M = 60.0
    STEP_M = 3.0
    # How hard to break away, and how long to stay committed. A single tick of
    # yaw would be undone by the next drive() from the mission thread, so an
    # intervention holds for a moment.
    ESCAPE_YAW_RATE = 0.6
    ESCAPE_HOLD_S = 0.6

    # The mission thread only commands the aircraft while a leg is executing.
    # Between legs it captures a frame and waits several seconds on VLM
    # inference, and nothing holds a fixed-wing still — so the aircraft keeps
    # flying its last command, unwatched. That gap is longer than the lookahead
    # (4-8 s at 14 m/s is 55-110 m vs LOOKAHEAD_M = 60), which is how a run
    # scored `envelope_events = 0` while its track went straight through the
    # wall: the guard was not overruled, it was never asked. A guard that only
    # runs inside drive() cannot support the claim its count is used to make.
    WATCHDOG_HZ = 10.0
    CRUISE_MS = 14.0
    # How long the path must stay clear before the aircraft counts as having
    # finished with an obstacle. See the reset in _guarded_drive().
    ENCOUNTER_CLEAR_S = 2.0
    # ~30 min of 10 Hz track; a run is a few minutes.
    TRACK_LIMIT = 20000

    def __init__(self, client, agent_id: str):
        super().__init__(client, agent_id)
        self.envelope_events = 0
        self.legs_refused = 0
        self._grid = None
        self._escape_until = 0.0
        self._escape_dir = 1.0
        self._in_escape = False
        self._clear_since = 0.0
        # 10 Hz flown track, sampled by the watchdog — see _watch().
        self.track: list = []
        # Serialises the mission thread's drive() against the watchdog's, so the
        # two cannot interleave halfway through an escape decision.
        self._lock = threading.RLock()
        self._last_airspeed = self.CRUISE_MS
        self._stop = threading.Event()
        self._watchdog = threading.Thread(target=self._watch, daemon=True,
                                          name=f"envelope-{agent_id}")
        self._watchdog.start()

    def close(self) -> None:
        self._stop.set()
        self._watchdog.join(timeout=2.0)
        parent = getattr(super(), "close", None)
        if parent is not None:
            parent()

    def _watch(self) -> None:
        """Keep checking the envelope even when nobody is flying the aircraft."""
        period = 1.0 / self.WATCHDOG_HZ
        while not self._stop.wait(period):
            try:
                with self._lock:
                    pose = self.get_pose()
                    if pose is None:
                        continue
                    x, y, z, yaw = pose
                    # Record the track while we are here. The sim's own
                    # pose_trace runs at 1 Hz — 14 m between samples at cruise —
                    # and the gate's most important assertion, "did the track go
                    # through the wall", was being evaluated on straight chords
                    # between those samples. Rounding the wall's north end puts
                    # the aircraft within ~60 m of a span that ends at +-70 m,
                    # so a chord can cut the corner and report a crossing that
                    # never happened. This is the same 10 Hz the guard makes its
                    # own decisions on, so the score and the guard cannot
                    # disagree about where the aircraft was.
                    if len(self.track) < self.TRACK_LIMIT:
                        self.track.append({"x": x, "y": y, "z": z})
                    # Only intervene when there is something to intervene about;
                    # staying silent otherwise leaves the mission thread's own
                    # commands untouched.
                    if (time.monotonic() < self._escape_until
                            or self._blocked_ahead(x, y, yaw, z) is not None):
                        self._guarded_drive(self._last_airspeed, 0.0, 0.0, pose)
            except Exception:
                # A watchdog that dies on a transient IPC hiccup is worse than
                # useless, because its silence still reads as "nothing to report".
                continue

    def _load_grid(self):
        if self._grid is None:
            g = self._client.fw_grid(self._id)
            if g:
                self._grid = {
                    "occ": base64.b64decode(g["occ"]),
                    "res": g["res"], "origin": g["origin"],
                    "w": g["w"], "h": g["h"],
                }
        return self._grid

    def _occupied(self, x: float, y: float) -> bool:
        g = self._load_grid()
        if not g:
            return False
        gx = int((x - g["origin"][0]) / g["res"])
        gy = int((y - g["origin"][1]) / g["res"])
        if gx < 0 or gy < 0 or gx >= g["w"] or gy >= g["h"]:
            return False
        return g["occ"][gy * g["w"] + gx] == 1

    # Tallest structure in the scene. The occupancy grid is purely 2D, so
    # without this the guard would treat an aircraft cruising at 90 m as being
    # about to hit a 50 m wall and shove it off course — penalising the model
    # for a perfectly good answer to "get past this obstacle". A single
    # scene-wide height is a deliberate simplification of a 2.5D problem; it is
    # safe because it over-estimates (nothing here is taller than the wall).
    STRUCTURE_TOP_M = 50.0
    OVERFLY_MARGIN_M = 10.0

    def _blocked_ahead(self, x, y, yaw, z: float = 0.0):
        """Distance to structure along the current heading, or None if clear."""
        if z > self.STRUCTURE_TOP_M + self.OVERFLY_MARGIN_M:
            return None
        d = self.STEP_M
        while d <= self.LOOKAHEAD_M:
            if self._occupied(x + math.cos(yaw) * d, y + math.sin(yaw) * d):
                return d
            d += self.STEP_M
        return None

    def _clear_side(self, x, y, yaw, z: float = 0.0):
        """Which way to break: whichever side has more room."""
        best_dir, best_clear = 1.0, -1.0
        for sign in (1.0, -1.0):
            probe = yaw + sign * math.radians(60.0)
            hit = self._blocked_ahead(x, y, probe, z)
            clear = self.LOOKAHEAD_M if hit is None else hit
            if clear > best_clear:
                best_clear, best_dir = clear, sign
        return best_dir

    def path_blocked(self, to_xy, alt_m: float = 0.0) -> bool:
        """Does the straight line from here to `to_xy` pass through structure?

        goto() is a heading hold — it flies straight at whatever it is given —
        so a destination on the far side of a wall produces a leg straight
        THROUGH the wall. Aircraft have no collision body here, so that is not
        even visibly a crash; the track simply passes through solid geometry,
        which is worse than a crash because it looks like success.

        Letting the vehicle refuse such a leg and say why is the same
        arrangement as the delivery gates: deterministic code declines an
        unflyable command, and the model decides what to do instead. It is not
        route planning — nothing here suggests a way around.
        """
        pose = self.get_pose()
        if pose is None:
            return False
        # A leg flown above everything in the scene cannot pass through
        # anything, however its ground track looks on a 2D grid.
        if max(alt_m, pose[2]) > self.STRUCTURE_TOP_M + self.OVERFLY_MARGIN_M:
            return False
        x, y = pose[0], pose[1]
        tx, ty = float(to_xy[0]), float(to_xy[1])
        dist = math.hypot(tx - x, ty - y)
        if dist < 1.0:
            return False
        steps = max(2, int(dist / self.STEP_M))
        for i in range(1, steps + 1):
            f = i / steps
            if self._occupied(x + (tx - x) * f, y + (ty - y) * f):
                return True
        return False

    def goto(self, north_m: float, east_m: float, alt_m: float,
             timeout_s: float = 60.0, tol_m: float = 5.0) -> bool:
        """Decline any straight leg that would pass through structure.

        The check lives here rather than in individual action handlers because
        it is a property of the VEHICLE, not of the reason for flying. Checking
        it only in navigate_to_world left every other path — a pixel unprojected
        by navigate_to_point, a search leg, an orbit leg, a return to a
        landmark — free to fly straight through the wall, and the track duly
        did, while the envelope guard shoved at it. One rule at the one place
        every leg passes through.
        """
        if self.path_blocked((east_m, north_m), alt_m):
            self.legs_refused += 1
            self.log_event("leg_refused", {
                "reason": "path crosses structure",
                "requested": [east_m, north_m],
            })
            return False
        return super().goto(north_m, east_m, alt_m, timeout_s, tol_m)

    def drive(self, airspeed: float, yaw_rate: float, climb: float = 0.0) -> None:
        with self._lock:
            self._last_airspeed = airspeed
            pose = self.get_pose()
            if pose is None:
                return super().drive(airspeed, yaw_rate, climb)
            return self._guarded_drive(airspeed, yaw_rate, climb, pose)

    def _guarded_drive(self, airspeed: float, yaw_rate: float, climb: float,
                       pose) -> None:
        """The actual check. Called by the mission thread and by the watchdog,
        both holding the lock, so an escape decision is never half-applied."""
        x, y, z, yaw = pose

        now = time.monotonic()
        if now < self._escape_until:
            # Mid-breakaway. Hold the turn until the way ahead is ACTUALLY
            # clear, not for a fixed interval: a timed nudge turned the
            # aircraft ~20 degrees and handed control straight back, so it
            # re-aimed at the wall and crossed it anyway — measured, 11
            # interventions in one run and the track still went through the
            # wall. Since nothing can hold a fixed-wing still, a guard that
            # stops guarding on a timer does not actually prevent anything.
            if self._blocked_ahead(x, y, yaw, z) is None:
                self._escape_until = 0.0
                self._in_escape = False
                return super().drive(airspeed, yaw_rate, climb)
            self._escape_until = now + self.ESCAPE_HOLD_S
            return super().drive(airspeed, self._escape_dir * self.ESCAPE_YAW_RATE, climb)

        hit = self._blocked_ahead(x, y, yaw, z)
        if hit is not None:
            self._escape_dir = self._clear_side(x, y, yaw, z)
            self._escape_until = now + self.ESCAPE_HOLD_S
            # Count one intervention per ENCOUNTER, not per drive() tick.
            # Holding the turn until clear (rather than for a fixed interval)
            # made the aircraft dip in and out of escape mode as the heading
            # swung, and each re-entry scored another event: one run reported
            # 281 where earlier runs reported 11, purely because the counter's
            # meaning changed underneath the metric it was being judged by.
            self._clear_since = 0.0
            if not self._in_escape:
                self.envelope_events += 1
            self._in_escape = True
            self.log_event("envelope_protection", {
                "limit": "structure_ahead",
                "distance_m": hit,
                "pose": [x, y, z],
                "break_direction": "left" if self._escape_dir > 0 else "right",
                "note": "the model failed to avoid this itself",
            })
            return super().drive(airspeed, self._escape_dir * self.ESCAPE_YAW_RATE, climb)

        # An encounter ends only once the path has been continuously clear for
        # ENCOUNTER_CLEAR_S — not on the first clear tick.
        #
        # Both halves of that are load-bearing, and getting either wrong
        # corrupts the count in a different direction. Never resetting here
        # leaves `_in_escape` stuck true when the hold expires without the
        # escape branch running again, silently suppressing every later
        # encounter. Resetting on the first clear tick instead makes one long
        # scrape along a wall score once per heading swing: measured, 223
        # "encounters" for a single approach.
        now = time.monotonic()
        if self._clear_since == 0.0:
            self._clear_since = now
        elif now - self._clear_since >= self.ENCOUNTER_CLEAR_S:
            self._escape_until = 0.0
            self._in_escape = False
        return super().drive(airspeed, yaw_rate, climb)
