"""Envelope-guarded SimBackend — a last-resort backstop, and a scoring signal.

The demo claims the aircraft avoids a wall using its camera. Verifying that
claim needs a way to tell "it avoided the wall" apart from "it happened not to
hit the wall this time", and it needs the sim to not quietly fly through solid
geometry when the model gets it wrong (fixed-wing bodies in this sim have no
collision shape, so nothing stops them).

This wraps SimBackend and checks the occupancy grid ahead of every drive
command. If the aircraft is about to fly into structure, it turns away and logs
an `envelope_protection` event.

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

    def __init__(self, client, agent_id: str):
        super().__init__(client, agent_id)
        self.envelope_events = 0
        self._grid = None
        self._escape_until = 0.0
        self._escape_dir = 1.0

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

    def _blocked_ahead(self, x, y, yaw):
        """Distance to structure along the current heading, or None if clear."""
        d = self.STEP_M
        while d <= self.LOOKAHEAD_M:
            if self._occupied(x + math.cos(yaw) * d, y + math.sin(yaw) * d):
                return d
            d += self.STEP_M
        return None

    def _clear_side(self, x, y, yaw):
        """Which way to break: whichever side has more room."""
        best_dir, best_clear = 1.0, -1.0
        for sign in (1.0, -1.0):
            probe = yaw + sign * math.radians(60.0)
            hit = self._blocked_ahead(x, y, probe)
            clear = self.LOOKAHEAD_M if hit is None else hit
            if clear > best_clear:
                best_clear, best_dir = clear, sign
        return best_dir

    def drive(self, airspeed: float, yaw_rate: float, climb: float = 0.0) -> None:
        pose = self.get_pose()
        if pose is None:
            return super().drive(airspeed, yaw_rate, climb)
        x, y, z, yaw = pose

        now = time.monotonic()
        if now < self._escape_until:
            # Mid-breakaway: keep turning, and ignore what the mission thread
            # asked for until clear.
            return super().drive(airspeed, self._escape_dir * self.ESCAPE_YAW_RATE, climb)

        hit = self._blocked_ahead(x, y, yaw)
        if hit is not None:
            self._escape_dir = self._clear_side(x, y, yaw)
            self._escape_until = now + self.ESCAPE_HOLD_S
            self.envelope_events += 1
            self.log_event("envelope_protection", {
                "limit": "structure_ahead",
                "distance_m": hit,
                "pose": [x, y, z],
                "break_direction": "left" if self._escape_dir > 0 else "right",
                "note": "the model failed to avoid this itself",
            })
            return super().drive(airspeed, self._escape_dir * self.ESCAPE_YAW_RATE, climb)

        return super().drive(airspeed, yaw_rate, climb)
