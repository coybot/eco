"""Follow a MOVING target — the Python half of the follow-target contract.

Mirrors PhroverKit's `FollowPolicy`/`TargetTracker` (Swift, sdk/swift/Sources) shape for
shape; the two stacks are separate deployables in two languages with no shared import
path, so the contract lives in eco/docs/follow-target.md and each side implements it.

Why this is not `nav`/`orbit_point`: every existing motion primitive here assumes a STATIC
goal. `goto()` terminates on arrival, `ORBIT_POINT` circles a fixed world point, and
`search_patterns` plans a whole pattern up front. Following is a servo — the goal is
re-estimated every tick, there is a stand-off band to hold, and there is no arrival.

Geometry is selected by the vehicle's `Kinematics`, never by a vtype string (vehicle_class
.py's own rule). The three cases differ in kind, not in tuning:

  UNICYCLE_2D          rover      hold stand-off on the target bearing; turn in place when
                                  badly off-boresight; NEVER reverse (no rear sensing)
  HOLONOMIC_3D         quadcopter same stand-off + altitude hold; can translate while yawed
  COORDINATED_TURN_3D  fixedwing  cannot hover, so "following" IS a moving orbit whose
                                  centre is slaved to the target's predicted position

Pure stdlib (dataclasses + math), like vehicle_class.py, so it imports with no torch /
Godot / ROS and unit-tests without a sim.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

try:  # normal on-device layout (drone/common/*.py installed flat)
    from vehicle_class import Kinematics, VehicleClass
except ImportError:  # package-style import (tests, tooling)
    from .vehicle_class import Kinematics, VehicleClass


Point = Tuple[float, float]


# --------------------------------------------------------------------------- track
@dataclass
class TargetTrack:
    """The persistent estimate of what is being followed.

    `id` is the identity of the *lock*, not of a detection: it must survive occlusion and
    re-acquisition. A change of id mid-follow means the tracker swapped targets — the
    failure mode vision-only following is most prone to.
    """
    id: int
    position: Point
    velocity: Point = (0.0, 0.0)
    last_seen_at: float = 0.0
    confidence: float = 1.0

    def predicted_position(self, after_s: float) -> Point:
        return (self.position[0] + self.velocity[0] * after_s,
                self.position[1] + self.velocity[1] * after_s)


@dataclass
class TargetObservation:
    """One candidate sighting, already grounded to the world plane by the caller.

    World points rather than image points on purpose: grounding needs a backend, and
    keeping it on the caller's side leaves association — the part that decides whether the
    vehicle is still following the right thing — testable with no sim at all.
    """
    label: str
    confidence: float
    world_xy: Point


# --------------------------------------------------------------------------- policy
@dataclass
class FollowParams:
    geometry: Kinematics
    standoff: float          # m
    deadband_enter: float    # m — stop translating inside this error
    deadband_exit: float     # m — resume outside it; the gap is what stops oscillation
    max_speed: float         # m/s
    bearing_tolerance: float = 0.15      # rad
    rotate_in_place_angle: float = 0.7   # rad (unicycle only)
    speed_gain: float = 0.5              # 1/s
    lead_time: float = 0.5               # s
    max_track_age: float = 1.0           # s
    allows_reverse: bool = False
    orbit_radius: float = 0.0            # m (COORDINATED_TURN_3D only)
    follow_altitude: float = 0.0         # m AGL (aerial only; 0 = hold current)

    @classmethod
    def for_vehicle(cls, vc: VehicleClass, standoff: Optional[float] = None,
                    altitude: Optional[float] = None) -> "FollowParams":
        """Derive a follow geometry from the vehicle's own capability descriptor.

        Keyed on `kinematics`, so adding a vehicle class needs no change here as long as
        its kinematics is one of the three.
        """
        if vc.kinematics is Kinematics.COORDINATED_TURN_3D:
            # Orbit radius must be at or above the airframe's own minimum turn radius —
            # a tighter circle is not suboptimal, it is unflyable. Same quantity
            # search_patterns.turn_radius_m() derives; recomputed here rather than
            # imported so this module stays dependency-free.
            radius = max(vc.max_speed_mps / max(vc.max_yaw_rate_radps, 1e-6),
                         standoff or 0.0)
            return cls(geometry=vc.kinematics,
                       standoff=radius,
                       deadband_enter=0.0,
                       deadband_exit=float("inf"),
                       max_speed=vc.max_speed_mps,
                       speed_gain=1.0,
                       lead_time=2.0,
                       max_track_age=5.0,
                       allows_reverse=False,
                       orbit_radius=radius,
                       follow_altitude=altitude if altitude is not None else 40.0)
        if vc.kinematics is Kinematics.HOLONOMIC_3D:
            hold = standoff if standoff is not None else 5.0
            return cls(geometry=vc.kinematics,
                       standoff=hold,
                       deadband_enter=0.5,
                       deadband_exit=1.5,
                       max_speed=vc.max_speed_mps,
                       speed_gain=0.6,
                       lead_time=0.8,
                       max_track_age=1.5,
                       # A multirotor can hold station off-axis, so it may restore a
                       # stand-off the target has closed inside.
                       allows_reverse=True,
                       follow_altitude=altitude if altitude is not None else 4.0)
        # UNICYCLE_2D (and anything else planar).
        hold = standoff if standoff is not None else 2.5
        return cls(geometry=vc.kinematics,
                   standoff=hold,
                   deadband_enter=0.25,
                   deadband_exit=0.60,
                   max_speed=vc.max_speed_mps,
                   speed_gain=0.5,
                   lead_time=0.5,
                   max_track_age=1.0,
                   # A ground vehicle does NOT back away: no rear sensing, and a retreat
                   # is a losing tail-chase against anything that walks. Stop instead.
                   allows_reverse=False)


@dataclass
class FollowOutput:
    motion: str                    # hold | turn_in_place | advance | retreat | orbit
    range_error: float             # + = too far, - = too close
    bearing_error: float           # rad, CCW positive
    predicted_target: Point
    is_stale: bool
    goal: Optional[Point] = None   # advance / retreat / orbit centre
    speed: float = 0.0             # advance / retreat
    yaw_error: float = 0.0         # turn_in_place
    radius: float = 0.0            # orbit


# Below this closing speed a target counts as stationary, and a vehicle in the band stops
# rather than creeping after sensor noise.
_PACING_THRESHOLD_MPS = 0.05


def normalize_angle(a: float) -> float:
    """Wrap to (-pi, pi]."""
    r = math.fmod(a, 2 * math.pi)
    if r > math.pi:
        r -= 2 * math.pi
    if r <= -math.pi:
        r += 2 * math.pi
    return r


class FollowPolicy:
    """Stateless apart from the deadband latch — see `FollowParams.deadband_*`."""

    def __init__(self, params: FollowParams):
        self.params = params
        self._holding = False

    @property
    def in_band(self) -> bool:
        return self._holding

    def reset(self) -> None:
        self._holding = False

    def step(self, pose_xy: Point, yaw: float, track: TargetTrack,
             now: Optional[float] = None) -> FollowOutput:
        now = time.time() if now is None else now
        p = self.params
        since_seen = max(0.0, now - track.last_seen_at)
        predicted = track.predicted_position(since_seen + p.lead_time)

        dx = predicted[0] - pose_xy[0]
        dy = predicted[1] - pose_xy[1]
        rng = math.hypot(dx, dy)
        range_error = rng - p.standoff
        # A target sitting on top of the vehicle has no meaningful bearing; hold heading
        # rather than spinning on numerical noise.
        bearing_error = normalize_angle(math.atan2(dy, dx) - yaw) if rng > 1e-3 else 0.0
        is_stale = since_seen > p.max_track_age

        ux0, uy0 = (dx / rng, dy / rng) if rng > 1e-3 else (math.cos(yaw), math.sin(yaw))
        self._update_band(abs(range_error))

        out = FollowOutput(motion="hold", range_error=range_error,
                           bearing_error=bearing_error, predicted_target=predicted,
                           is_stale=is_stale)

        # A fixed-wing has no hold and no reverse: min airspeed is well above zero, so the
        # only way to stay with a target is to keep circling it. Band state is irrelevant,
        # and a stale track keeps it circling the last estimate rather than stopping —
        # which it cannot do anyway.
        if p.geometry is Kinematics.COORDINATED_TURN_3D:
            out.motion = "orbit"
            out.goal = predicted
            out.radius = p.orbit_radius
            return out

        if self._holding:
            # In the band, the deadband suppresses the range CORRECTION — not motion
            # itself. A vehicle that stops dead behind a target that keeps walking has to
            # start again the moment the gap reopens, which is stick-slip: measured at 36
            # start/stop cycles a minute behind a steady 0.25 m/s walk. Pacing the target
            # at its own speed instead keeps the stand-off without hunting for it, and
            # still comes to a genuine stop the moment the target does.
            closing = track.velocity[0] * ux0 + track.velocity[1] * uy0
            if closing > _PACING_THRESHOLD_MPS:
                out.motion = "advance"
                out.goal = (predicted[0] - ux0 * p.standoff, predicted[1] - uy0 * p.standoff)
                out.speed = min(p.max_speed, closing)
            elif abs(bearing_error) > p.bearing_tolerance:
                out.motion, out.yaw_error = "turn_in_place", bearing_error
            return out

        speed = self._speed_for(range_error, track, (ux0, uy0))

        if range_error < 0:
            if not p.allows_reverse:
                if abs(bearing_error) > p.bearing_tolerance:
                    out.motion, out.yaw_error = "turn_in_place", bearing_error
                return out
            ux, uy = -ux0, -uy0
            out.motion = "retreat"
            out.goal = (predicted[0] + ux * p.standoff, predicted[1] + uy * p.standoff)
            out.speed = speed
            return out

        # Unicycle: arcing toward a target far off-boresight wastes ground and can lose it
        # out of a narrow camera FOV. Square up first.
        if p.geometry is Kinematics.UNICYCLE_2D and abs(bearing_error) > p.rotate_in_place_angle:
            out.motion, out.yaw_error = "turn_in_place", bearing_error
            return out

        ux, uy = ux0, uy0
        out.motion = "advance"
        out.goal = (predicted[0] - ux * p.standoff, predicted[1] - uy * p.standoff)
        out.speed = speed
        return out

    def _speed_for(self, range_error: float, track: TargetTrack,
                   line_of_sight: Point) -> float:
        """Proportional correction PLUS the target's own speed along the line of sight.

        Without the feedforward term a pure P-controller cannot hold a stand-off behind
        anything that keeps moving: it settles wherever `gain * error` happens to equal the
        target's speed, which for a rover chasing a 0.4 m/s walk is about 0.8 m too far
        back — measured in the Godot depot before this term existed. Only the receding
        component is fed forward; a target closing in is handled by the deadband and, for
        a vehicle that cannot reverse, by stopping.
        """
        p = self.params
        closing = track.velocity[0] * line_of_sight[0] + track.velocity[1] * line_of_sight[1]
        return min(p.max_speed, abs(range_error) * p.speed_gain + max(0.0, closing))

    def _update_band(self, error: float) -> None:
        if self._holding:
            if error >= self.params.deadband_exit:
                self._holding = False
        elif error <= self.params.deadband_enter:
            self._holding = True


# --------------------------------------------------------------------------- tracker
@dataclass
class TrackerParams:
    association_radius: float = 0.8   # m — beyond this from the prediction, not our target
    position_alpha: float = 0.7
    velocity_alpha: float = 0.35
    max_speed: float = 3.0            # m/s — beyond this it is an association error
    reacquire_after: float = 1.5      # s coasting before an ungated candidate is accepted
    lose_after: float = 10.0          # s coasting before the lock is declared lost
    min_confidence: float = 0.35

    @classmethod
    def for_vehicle(cls, vc: VehicleClass) -> "TrackerParams":
        """Gate and speed limits scaled to the vehicle's own world.

        A fixed-wing sees its target from hundreds of metres with metres of localization
        error; an 0.8 m gate that suits a rover indoors would reject every real sighting.
        """
        if vc.kinematics is Kinematics.COORDINATED_TURN_3D:
            return cls(association_radius=40.0, max_speed=30.0,
                       reacquire_after=5.0, lose_after=30.0)
        if vc.kinematics is Kinematics.HOLONOMIC_3D:
            return cls(association_radius=3.0, max_speed=10.0,
                       reacquire_after=2.0, lose_after=15.0)
        return cls()


class TargetTracker:
    """Turns per-frame detections into one persistent track.

    Detectors here emit no track ids, so identity is reconstructed by gating candidates
    against the predicted position. That gate is the whole defence against following the
    wrong thing: while the lock is fresh a candidate outside it is NOT adopted, because
    grabbing whatever else is in frame is exactly how a follow transfers to a passer-by.
    `unverified_reacquisitions` counts every time the lock was re-attached to a candidate
    the gate could not vouch for, so a caller (and a test) can tell a clean follow from a
    lucky one.
    """

    def __init__(self, params: Optional[TrackerParams] = None):
        self.params = params or TrackerParams()
        self.query: Optional[str] = None
        self.track: Optional[TargetTrack] = None
        self.state = "idle"   # idle | acquiring | tracking | coasting | lost
        self.unverified_reacquisitions = 0
        self._seed: Optional[Point] = None
        self._next_id = 1
        self._last_observed_at: Optional[float] = None

    def lock(self, query: str, seed: Optional[Point] = None) -> None:
        self.query = query
        self._seed = seed
        self.track = None
        self._last_observed_at = None
        self.unverified_reacquisitions = 0
        self.state = "acquiring"

    def release(self) -> None:
        self.query = None
        self._seed = None
        self.track = None
        self._last_observed_at = None
        self.state = "idle"

    def seconds_since_seen(self, now: Optional[float] = None) -> Optional[float]:
        if self._last_observed_at is None:
            return None
        return (time.time() if now is None else now) - self._last_observed_at

    def update(self, observations: Sequence[TargetObservation],
               now: Optional[float] = None) -> Optional[TargetTrack]:
        if self.query is None:
            return None
        now = time.time() if now is None else now
        p = self.params
        candidates = [o for o in observations
                      if o.confidence >= p.min_confidence and labels_match(self.query, o.label)]

        if self.track is None:
            picked = self._initial_pick(candidates)
            if picked is not None:
                self.track = TargetTrack(id=self._next_id, position=picked.world_xy,
                                         velocity=(0.0, 0.0), last_seen_at=now,
                                         confidence=picked.confidence)
                self._next_id += 1
                self._last_observed_at = now
                self.state = "tracking"
            return self.track

        unseen_for = now - (self._last_observed_at
                            if self._last_observed_at is not None else self.track.last_seen_at)
        predicted = self.track.predicted_position(unseen_for)

        gated = _nearest(predicted, candidates, p.association_radius)
        if gated is not None:
            self._apply(gated, now, unseen_for)
            return self.track

        # Nothing inside the gate: coast on the prediction while the lock is fresh.
        if unseen_for >= p.reacquire_after:
            fallback = _nearest(predicted, candidates, float("inf"))
            if fallback is not None:
                self.unverified_reacquisitions += 1
                self._apply(fallback, now, unseen_for)
                return self.track

        self.state = "lost" if unseen_for >= p.lose_after else "coasting"
        return self.track

    # -- internals ---------------------------------------------------------
    def _initial_pick(self, candidates):
        if not candidates:
            return None
        if self._seed is not None:
            return min(candidates, key=lambda o: _dist(o.world_xy, self._seed))
        return max(candidates, key=lambda o: o.confidence)

    def _apply(self, obs: TargetObservation, now: float, unseen_for: float) -> None:
        p = self.params
        old = self.track
        px = old.position[0] + (obs.world_xy[0] - old.position[0]) * p.position_alpha
        py = old.position[1] + (obs.world_xy[1] - old.position[1]) * p.position_alpha

        # Velocity only means anything across a real time gap; two detections in the same
        # millisecond would divide by ~0 and produce a nonsense prediction.
        if unseen_for > 0.02:
            mvx = (px - old.position[0]) / unseen_for
            mvy = (py - old.position[1]) / unseen_for
            vx = old.velocity[0] + (mvx - old.velocity[0]) * p.velocity_alpha
            vy = old.velocity[1] + (mvy - old.velocity[1]) * p.velocity_alpha
            speed = math.hypot(vx, vy)
            if speed > p.max_speed and speed > 0:
                vx, vy = vx * p.max_speed / speed, vy * p.max_speed / speed
            old.velocity = (vx, vy)

        old.position = (px, py)
        old.last_seen_at = now
        old.confidence = obs.confidence
        self._last_observed_at = now
        self.state = "tracking"


def labels_match(query: str, label: str) -> bool:
    """Loose noun match between a free-text follow request and a detector label.

    Same spirit as reasoning_loop's own `labels_match`: detectors report a class, the
    operator speaks a phrase, and the phrase usually contains the class.
    """
    q = (query or "").strip().lower()
    l = (label or "").strip().lower()
    if not q or not l:
        return False
    if q == l or l in q or q in l:
        return True
    q_tokens = set(q.replace("-", " ").split())
    l_tokens = set(l.replace("-", " ").split())
    return bool(q_tokens & l_tokens)


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _nearest(point: Point, candidates, within: float):
    inside = [o for o in candidates if _dist(o.world_xy, point) <= within]
    if not inside:
        return None
    return min(inside, key=lambda o: _dist(o.world_xy, point))


# --------------------------------------------------------------------------- executor
class FollowExecutor:
    """Runs a follow against the `Backend` seam (backends.py), so one implementation
    serves quadcopter, rover and fixed-wing in both sim and hardware.

    Only uses Backend methods that already exist — `get_pose`, `detect`, `drive`,
    `loiter`, `log_event` — so no backend needs a follow-specific capability.

    Frames: everything here is ENU (x=east, y=north), matching `Detection.world_xyz` and
    fixedwing_manager. `Backend.goto` is the one API in north/east order, which is why
    this class drives with `drive()` rather than re-goal-ing every tick.
    """

    def __init__(self, backend, vehicle_class: VehicleClass, query: str,
                 seed: Optional[Point] = None,
                 standoff: Optional[float] = None,
                 altitude: Optional[float] = None,
                 params: Optional[FollowParams] = None,
                 tracker: Optional[TargetTracker] = None):
        self.backend = backend
        self.vc = vehicle_class
        self.params = params or FollowParams.for_vehicle(vehicle_class, standoff, altitude)
        self.policy = FollowPolicy(self.params)
        self.tracker = tracker or TargetTracker(TrackerParams.for_vehicle(vehicle_class))
        self.tracker.lock(query, seed)
        self.query = query
        self.samples: list = []          # (t, range_to_target) for scoring
        self.last_output: Optional[FollowOutput] = None

    # -- one tick ----------------------------------------------------------
    def step(self, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        pose = self.backend.get_pose()
        if pose is None:
            return "no_pose"
        x, y, z, yaw = pose

        self.tracker.update(self._observations(), now=now)
        track = self.tracker.track
        if track is None:
            self._command_hold(z)
            return "acquiring"
        if self.tracker.state == "lost":
            self._command_hold(z)
            return "lost"

        out = self.policy.step((x, y), yaw, track, now=now)
        self.last_output = out
        self.samples.append((now, math.hypot(track.position[0] - x, track.position[1] - y)))
        self._command(out, z, yaw)
        return self.tracker.state

    def run(self, duration_s: float, tick_hz: float = 5.0,
            should_stop=None) -> dict:
        """Follow for `duration_s`, then stop. Returns a scorecard the sim tests assert on."""
        deadline = time.time() + duration_s
        period = 1.0 / max(tick_hz, 0.1)
        states = []
        while time.time() < deadline:
            state = self.step()
            states.append(state)
            if should_stop is not None and should_stop(self):
                break
            time.sleep(period)
        self._command_hold(None)
        return self.scorecard(states)

    def scorecard(self, states: Sequence[str] = ()) -> dict:
        ranges = [r for _, r in self.samples]
        band_lo = self.params.standoff - self.params.deadband_exit
        band_hi = self.params.standoff + self.params.deadband_exit
        if self.params.geometry is Kinematics.COORDINATED_TURN_3D:
            # An orbit is judged by whether the target stays inside a ring around the
            # commanded radius, not by a stand-off band it has no way to hold.
            band_lo, band_hi = 0.4 * self.params.orbit_radius, 2.2 * self.params.orbit_radius
        in_band = [r for r in ranges if band_lo <= r <= band_hi]
        return {
            "query": self.query,
            "geometry": self.params.geometry.value,
            "standoff": self.params.standoff,
            "samples": len(ranges),
            "in_band_fraction": (len(in_band) / len(ranges)) if ranges else 0.0,
            "band": [band_lo, band_hi],
            "min_range": min(ranges) if ranges else None,
            "max_range": max(ranges) if ranges else None,
            "track_id": self.tracker.track.id if self.tracker.track else None,
            "unverified_reacquisitions": self.tracker.unverified_reacquisitions,
            "final_state": states[-1] if states else self.tracker.state,
            "lost_ticks": sum(1 for s in states if s == "lost"),
        }

    def stop(self) -> None:
        self.tracker.release()
        self._command_hold(None)
        # Always recentre: a sensor left cocked silently points every later detection and
        # unprojection the wrong way, long after the follow ended.
        self._aim_sensor(None)

    def _aim_sensor(self, centre: Optional[Point]) -> None:
        aim = getattr(self.backend, "aim_sensor", None)
        if aim is None:
            return   # backends with a fixed forward sensor have nothing to point
        try:
            aim(centre)
        except Exception:
            pass

    # -- internals ---------------------------------------------------------
    def _observations(self) -> list:
        out = []
        for d in self.backend.detect():
            world = getattr(d, "world_xyz", None)
            if world is None or len(world) < 2:
                continue
            out.append(TargetObservation(label=getattr(d, "label", ""),
                                         confidence=float(getattr(d, "score", 0.0) or 0.0),
                                         world_xy=(float(world[0]), float(world[1]))))
        return out

    def _climb_for(self, z: Optional[float]) -> float:
        if z is None or self.params.follow_altitude <= 0:
            return 0.0
        return max(-5.0, min(5.0, 0.6 * (self.params.follow_altitude - z)))

    def _yaw_rate(self, bearing_error: float) -> float:
        limit = self.vc.max_yaw_rate_radps
        return max(-limit, min(limit, 2.0 * bearing_error))

    # Orbit guidance gain: how hard a radial error pulls the commanded course off the
    # tangent. 1.0 means a full-radius error asks for a 45-degree intercept.
    ORBIT_GAIN = 1.0

    def _command_orbit(self, out: FollowOutput, z: float, yaw: float) -> None:
        """Steer the circle directly rather than calling `Backend.loiter`.

        `loiter` cannot serve here: SimBackend's is a min-airspeed straight-and-level hold,
        and the real orbit primitives on either side — `search_patterns.orbit`'s waypoint
        laps and `plane_sdk.orbit`'s ArduPilot command — both take a STATIC centre. The
        whole point of following is that the centre moves, so the circle has to be flown as
        a steering law that is re-evaluated every tick: aim down the tangent, and bend the
        commanded course toward or away from the centre by the radial error.
        """
        pose = self.backend.get_pose()
        if pose is None:
            return
        x, y, _, _ = pose
        cx, cy = out.goal
        dx, dy = cx - x, cy - y
        r = math.hypot(dx, dy)
        bearing_to_centre = math.atan2(dy, dx)
        radial_error = r - self.params.orbit_radius
        # Counter-clockwise: the tangent sits a quarter turn back from the centre bearing;
        # a positive (outside-the-circle) error rotates the command back toward the centre.
        correction = math.atan(self.ORBIT_GAIN * radial_error
                               / max(self.params.orbit_radius, 1.0))
        desired = bearing_to_centre - math.pi / 2 + correction
        yaw_rate = self._yaw_rate(normalize_angle(desired - yaw))
        airspeed = max(self.vc.min_speed_mps, self.vc.max_speed_mps)
        self.backend.drive(airspeed, yaw_rate, self._climb_for(z))

        # Both the camera and the detector follow the airframe's nose, so an aircraft
        # flying a circle stares down the tangent and never sees the thing it is circling
        # — the target would be lost within a quarter lap. Re-aimed every tick because the
        # offset is relative to a nose that is swinging all the way round.
        self._aim_sensor((cx, cy))

    def _command_hold(self, z: Optional[float]) -> None:
        # A fixed-wing cannot stop; holding for it means keeping the last orbit rather
        # than commanding a zero airspeed the airframe would stall at.
        if self.params.geometry is Kinematics.COORDINATED_TURN_3D:
            return
        try:
            self.backend.drive(0.0, 0.0, self._climb_for(z))
        except Exception:
            pass

    def _command(self, out: FollowOutput, z: float, yaw: float) -> None:
        climb = self._climb_for(z)
        if out.motion == "orbit":
            self._command_orbit(out, z, yaw)
            return
        if out.motion == "hold":
            self.backend.drive(0.0, 0.0, climb)
            return
        if out.motion == "turn_in_place":
            self.backend.drive(0.0, self._yaw_rate(out.yaw_error), climb)
            return
        if out.motion == "retreat":
            self.backend.drive(-out.speed, self._yaw_rate(out.bearing_error), climb)
            return
        self.backend.drive(out.speed, self._yaw_rate(out.bearing_error), climb)
