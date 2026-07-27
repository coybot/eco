"""Situational context blocks for the on-device VLM prompt.

A single camera frame cannot show a trend. It cannot show that the teammate
visible as a speck has been steadily descending for the last forty seconds, or
that the structure ahead spans 140 m and tops out above your altitude. Both are
things a pilot would know and reason from, and both are computed here by plain
deterministic geometry over the sensor history — then handed to the model as
text it can reason about.

The division of labour matters for the demo's honesty. Nothing in this module
decides anything: it measures, aggregates and describes. Whether a descending,
circling teammate is worth diverting to, and whether a wall ahead should be
rounded to the north or the south, are decisions left entirely to the model.
Deterministic code doing the trigonometry is the same arrangement as a real
aircraft's sensor fusion feeding a human pilot's judgement.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

# How long a sighting stays relevant. Long enough to see a trend develop, short
# enough that a teammate's behaviour two minutes ago doesn't argue for a
# diversion now.
PEER_HISTORY_S = 90.0

# A teammate is "low" relative to normal search altitude, and "circling" if its
# recent track stays inside a small area. Both thresholds are descriptive, not
# decision points — they only shape the wording handed to the model.
LOW_ALT_M = 28.0
CIRCLING_RADIUS_M = 90.0
DESCENDING_RATE_MPS = 0.35


def _compass(bearing_rad: float) -> str:
    pts = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
    idx = int(round(math.degrees(bearing_rad) % 360.0 / 45.0)) % 8
    return pts[idx]


@dataclass
class _RememberedWall:
    """A structure point retained after it has left the camera's view."""
    x: float
    y: float
    top: float

    @property
    def label(self):
        return "wall"

    @property
    def world_xyz(self):
        return (self.x, self.y, self.top)

    @property
    def top_z(self):
        return self.top


@dataclass
class PeerSighting:
    t: float
    x: float
    y: float
    z: float


class PeerTracker:
    """Rolling history of where each teammate has been seen, and what that implies.

    Fed from detect()'s "aircraft" rows. A searching aircraft sweeps a 60 deg
    cone, so it only catches a teammate for a fraction of each lap — the history
    is what turns those intermittent glimpses into a usable statement about
    behaviour, rather than a single instantaneous position that says nothing
    about intent.
    """

    def __init__(self, history_s: float = PEER_HISTORY_S):
        self.history_s = history_s
        self._tracks: Dict[str, Deque[PeerSighting]] = {}

    def observe(self, peer_id: str, x: float, y: float, z: float,
                t: Optional[float] = None) -> None:
        now = time.monotonic() if t is None else t
        track = self._tracks.setdefault(peer_id, deque())
        track.append(PeerSighting(now, x, y, z))
        cutoff = now - self.history_s
        while track and track[0].t < cutoff:
            track.popleft()

    def peers(self) -> List[str]:
        return [p for p, tr in self._tracks.items() if tr]

    def last(self, peer_id: str) -> Optional[PeerSighting]:
        track = self._tracks.get(peer_id)
        return track[-1] if track else None

    def summarize(self, own_xyz: Tuple[float, float, float]) -> str:
        """One line per teammate, or "" when none have been seen recently."""
        rows: List[str] = []
        for peer_id in sorted(self._tracks):
            track = self._tracks[peer_id]
            if not track:
                continue
            last = track[-1]
            dx, dy = last.x - own_xyz[0], last.y - own_xyz[1]
            dist = math.hypot(dx, dy)
            phrase = (f"- {peer_id}: last seen bearing {_compass(math.atan2(dy, dx))}, "
                      f"{dist:.0f} m away, altitude {last.z:.0f} m")

            notes: List[str] = []
            span = last.t - track[0].t
            if len(track) >= 3 and span > 5.0:
                rate = (last.z - track[0].z) / span
                if rate < -DESCENDING_RATE_MPS:
                    notes.append("descending")
                elif rate > DESCENDING_RATE_MPS:
                    notes.append("climbing")
                cx = sum(s.x for s in track) / len(track)
                cy = sum(s.y for s in track) / len(track)
                spread = max(math.hypot(s.x - cx, s.y - cy) for s in track)
                if spread < CIRCLING_RADIUS_M and span > 20.0:
                    notes.append(f"staying over one spot near ({cx:.0f}, {cy:.0f})")
            if last.z < LOW_ALT_M:
                notes.append("flying low")
            if notes:
                phrase += " — " + ", ".join(notes)
            phrase += f" [{len(track)} sightings over {span:.0f}s]"
            rows.append(phrase)
        return "\n".join(rows)


class ObstacleTracker:
    """Turns "wall" detections into a statement about what is across the route.

    detect() returns one row per marker along a structure, which individually
    say very little. Aggregated, they give distance, lateral span, and — most
    usefully — where the structure ENDS, which is what a decision to route
    around it actually needs. Only structure roughly ahead is reported, so a
    wall already passed does not keep arguing for a turn.
    """

    AHEAD_HALF_ANGLE = math.radians(25.0)

    def __init__(self):
        # Structure stays known once seen. A wall does not cease to exist when
        # the aircraft turns away from it, but a detections-only view says
        # exactly that: observed live, two decisions after first sighting the
        # obstacle block vanished while the model went on reasoning about "the
        # wall ahead" from its own history, with no coordinates left to act on.
        self._known: Dict[Tuple[int, int], float] = {}

    def remember(self, detections) -> None:
        for d in detections:
            label = getattr(d, "label", None) or (
                d.get("label") if isinstance(d, dict) else None)
            world = getattr(d, "world_xyz", None) or (
                d.get("world") if isinstance(d, dict) else None)
            if label != "wall" or not world:
                continue
            top = getattr(d, "top_z", None)
            if top is None and isinstance(d, dict):
                top = d.get("top_z")
            self._known[(int(round(world[0])), int(round(world[1])))] = float(
                top if top is not None else world[2])

    def summarize(self, own_xyz: Tuple[float, float, float], yaw: float,
                  detections) -> str:
        self.remember(detections)
        detections = list(detections) + [
            _RememberedWall(x, y, top) for (x, y), top in self._known.items()]
        pts = []
        seen = set()
        for d in detections:
            label = getattr(d, "label", None) or (
                d.get("label") if isinstance(d, dict) else None)
            world = getattr(d, "world_xyz", None) or (
                d.get("world") if isinstance(d, dict) else None)
            if label != "wall" or not world:
                continue
            # How TALL the structure is, not how high the sensed point sits.
            # Markers sit at mid-height (a marker at the top would never fall
            # inside the downward-pitched FOV), so using the sensed z as the top
            # would halve every wall and invite the model to overfly something
            # it cannot clear.
            top = getattr(d, "top_z", None)
            if top is None and isinstance(d, dict):
                top = d.get("top_z")
            if top is None:
                top = world[2]
            key = (int(round(world[0])), int(round(world[1])))
            if key in seen:
                continue
            seen.add(key)
            dx, dy = world[0] - own_xyz[0], world[1] - own_xyz[1]
            bearing = math.atan2(dy, dx)
            rel = (bearing - yaw + math.pi) % (2 * math.pi) - math.pi
            pts.append((math.hypot(dx, dy), rel, world, float(top)))
        if not pts:
            return ""
        if not any(abs(rel) <= self.AHEAD_HALF_ANGLE for _, rel, _, _ in pts):
            return ""   # structure exists, but not across the current course

        nearest = min(p[0] for p in pts)
        top = max(p[3] for p in pts)
        ys = [p[2][1] for p in pts]
        xs = [p[2][0] for p in pts]  # noqa: E501 — world coords of each sensed marker
        south = (sum(xs) / len(xs), min(ys))
        north = (sum(xs) / len(xs), max(ys))
        rows = [
            f"- Structure across your course: nearest edge {nearest:.0f} m ahead, "
            f"top of it about {top:.0f} m above ground (you are at {own_xyz[2]:.0f} m).",
            f"- It spans from ({south[0]:.0f}, {south[1]:.0f}) to "
            f"({north[0]:.0f}, {north[1]:.0f}) — {abs(north[1] - south[1]):.0f} m wide. "
            f"Those two ends are where it stops; beyond them the way is open.",
        ]
        if top > own_xyz[2]:
            rows.append("- It is taller than your current altitude, so flying straight "
                        "on will not clear it.")
        # Name the action that can actually reach those coordinates. Observed
        # live: the model correctly described the wall and the need to go round
        # it on every single decision, then chose navigate_to_point every time —
        # which can only aim at what is on camera and so kept walking it into
        # the obstacle. It was not misjudging the situation; it had the right
        # plan and reached for a tool that cannot carry it out. Which way to go,
        # or whether to go at all, is still entirely its call.
        clear_s = (south[0] - 60.0, south[1] - 60.0)
        clear_n = (north[0] - 60.0, north[1] + 60.0)
        rows.append(
            f"- navigate_to_point cannot route around this: it only aims at what is "
            f"already on camera. To go around, use navigate_to_world with a point "
            f"past one end — for example ({clear_s[0]:.0f}, {clear_s[1]:.0f}) to the "
            f"south, or ({clear_n[0]:.0f}, {clear_n[1]:.0f}) to the north — and then "
            f"continue east once past it.")
        return "\n".join(rows)


class PerceptionSampler:
    """Background thread that keeps a PeerTracker fed between decisions.

    A decision takes seconds and the sensor sweeps a 60 deg cone, so sampling
    only at decision time catches a teammate a couple of times a minute at
    best — nowhere near enough to tell "descending and circling" from "passing
    through". Polling at ~1 Hz turns those glimpses into an actual trend.

    It only ever READS: detections in, tracker updated, nothing commanded. It
    holds its own client connection so it cannot interleave with the mission
    thread's requests on a shared socket.
    """

    def __init__(self, backend, tracker: PeerTracker, hz: float = 1.0,
                 on_error=None):
        self.backend = backend
        self.tracker = tracker
        self.period = 1.0 / max(hz, 0.05)
        self.on_error = on_error
        self.samples = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "PerceptionSampler":
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="perception-sampler")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                for d in self.backend.detect():
                    if getattr(d, "label", None) != "aircraft":
                        continue
                    world = getattr(d, "world_xyz", None)
                    if world:
                        self.tracker.observe(getattr(d, "peer_id", None) or "teammate",
                                             world[0], world[1], world[2])
                self.samples += 1
            except Exception as exc:
                # A dropped poll is not worth killing the sampler over — the
                # mission thread is the one that matters, and a gap in peer
                # history just makes the trend slightly coarser.
                if self.on_error is not None:
                    self.on_error(exc)
            self._stop.wait(self.period)


def payload_block(remaining: int, capacity: int = 1) -> str:
    if remaining <= 0:
        return ("- You have released all payloads and are carrying nothing. "
                "You cannot deliver again.")
    return (f"- Carrying {remaining} of {capacity} payload(s), ready to release. "
            f"You cannot pick one back up once released.")
