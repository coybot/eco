"""Backend seam: one actuation/sensing interface, two implementations.

MissionLoop (reasoning_loop.py) is the single reasoning layer that will grow
perception + memory + reasoning for fixed-wing. Today it talks directly to
drone_sdk / PerceptionService / Nav2Bridge, which only exist on real hardware —
there is no way to point it at a simulator. This module is that seam:

  HardwareBackend  — wraps today's on-device path (drone_sdk + PerceptionService +
                      Nav2Bridge). Default backend; preserves current behavior.
  SimBackend       — drives the Godot fixed-wing sim (fixedwing_manager.gd) over a
                      DepotClient, so the exact same MissionLoop code can fly in sim
                      now and the real Skywalker X8 + Orin NX later.

Both backends produce the same normalized `Detection` shape. Critically, both
compute `world_xyz` at ingest time (pose ⊕ range/bearing), not body-frame —
SpatialMemory only makes sense if a sighting is anchored in the world, not the
airframe, since a fixed-wing has moved 25 m by the time it reasons about it.
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol, Tuple


@dataclass
class Detection:
    """Normalized detection shared by the YOLO (hardware) and Godot (sim) sensing paths."""
    label: str
    score: float
    bbox: Optional[tuple] = None          # (x1,y1,x2,y2) pixels — hardware path only
    range_m: Optional[float] = None
    bearing_rad: Optional[float] = None   # body-frame bearing, +left, from forward
    world_xyz: Optional[Tuple[float, float, float]] = None  # world/NED meters
    # Top of the object in world height, when the sensing path can estimate it.
    # A sensed point says where something is, not how tall it is, and for
    # structure that difference decides whether it can be overflown at all.
    top_z: Optional[float] = None
    # Set on detections of another aircraft — the only channel a comms-denied
    # formation has for learning anything about a teammate.
    peer_id: Optional[str] = None


class Backend(Protocol):
    """Actuation/sensing seam for MissionLoop."""

    def capture_frame(self) -> Any: ...
    def detect(self) -> list: ...
    def get_pose(self) -> Optional[Tuple[float, float, float, float]]: ...
    def get_battery(self) -> Optional[dict]: ...
    def drive(self, airspeed: float, yaw_rate: float, climb: float = 0.0) -> None: ...
    def goto(self, north_m: float, east_m: float, alt_m: float) -> Any: ...
    def loiter(self, center: Optional[tuple], radius: float) -> None: ...
    def log_event(self, kind: str, data: dict) -> None: ...
    def unproject(self, nx: float, ny: float) -> Optional[Tuple[float, float, float]]: ...
    # Added for the general free-form-command capability (typed phases now route
    # through this seam instead of calling Nav2/drone_sdk directly — see the
    # fixed-wing autonomy plan's "change A"). Each has a genuinely different
    # implementation per vehicle (a plane can't hover/vertical-takeoff/LAND-mode
    # the way a quad does), which is the whole point of routing through here.
    def takeoff(self, alt_m: float) -> bool: ...
    def land(self, heading_deg: Optional[float] = None) -> bool: ...
    def rtl(self, alt_m: Optional[float] = None) -> bool: ...
    # change E: the FC-enforced safety envelope (geofence + altitude floor)
    # that "climb over the trees" actually depends on — perception only
    # triggers a climb, this is what guarantees it regardless.
    def configure_safety(self, fence_radius_m: Optional[float] = None,
                          fence_max_alt_m: Optional[float] = None,
                          min_alt_floor_m: Optional[float] = None) -> bool: ...


def world_from_range_bearing(
    pose: Optional[Tuple[float, float, float, float]],
    range_m: Optional[float],
    bearing_rad: Optional[float],
    elevation_rad: float = 0.0,
) -> Optional[Tuple[float, float, float]]:
    """(x,y,z,yaw) pose + body-frame range/bearing/elevation -> world xyz.

    Shared by both backends so a detection's world position is always derived the
    same way regardless of which sensing path produced the range/bearing — the
    single ingest point memory (SpatialMemory) reads from.
    """
    if pose is None or range_m is None or bearing_rad is None:
        return None
    x, y, z, yaw = pose
    fwd = range_m * math.cos(elevation_rad) * math.cos(bearing_rad)
    left = range_m * math.cos(elevation_rad) * math.sin(bearing_rad)
    up = range_m * math.sin(elevation_rad)
    c, s = math.cos(yaw), math.sin(yaw)
    wx = x + c * fwd - s * left
    wy = y + s * fwd + c * left
    wz = z + up
    return (wx, wy, wz)


class HardwareBackend:
    """Wraps today's on-device path: drone_sdk + PerceptionService + Nav2Bridge.

    Default backend for MissionLoop — behavior matches what MissionLoop did before
    the backend seam existed. Any of the three collaborators may be None (e.g. no
    drone_sdk on a dev machine); methods degrade to no-ops/None like the code they
    replace already did.

    Fixed-wing note: `nav` (Nav2Bridge) is a quad/rover ROS2 position-goal
    abstraction with no fixed-wing analog (a plane can't be teleport-commanded to
    a waypoint — same reason SimBackend.goto() is a heading-hold loop, not a
    single op). For `vehicle_class="fixedwing"`, pass `plane_sdk` (the
    eco/drone/common/plane_sdk module or a compatible object exposing
    goto/orbit/get_position) instead of `nav` — goto/loiter/drive route through
    it directly over MAVLink rather than through Nav2.
    """

    def __init__(self, drone_sdk=None, perception=None, nav=None,
                 vehicle_class: str = "quadcopter", plane_sdk=None):
        self._sdk = drone_sdk
        self._perception = perception
        self._nav = nav
        self._vehicle_class = vehicle_class
        self._plane = plane_sdk
        # Lazily captured (lat, lon) at first plane goto/loiter call, used to
        # convert the Backend protocol's north_m/east_m *offsets* (this seam's
        # convention — see goto()/loiter() below) into absolute GPS coordinates,
        # since a real fixed-wing navigates by lat/lon, not an ENU world frame.
        self._plane_origin_latlon = None

    def _is_fixedwing(self) -> bool:
        return self._vehicle_class == "fixedwing" and self._plane is not None

    def _plane_offset_to_latlon(self, north_m: float, east_m: float):
        """Convert a north/east offset (meters) to absolute (lat, lon), using the
        position captured at the first such call this session as the origin —
        consistent with Nav2Bridge's own `navigate_to_offset` semantics for the
        quad/rover path (offset from where the aircraft was, not a fixed world
        origin). Equirectangular approximation; fine at these ranges (<1km)."""
        if self._plane_origin_latlon is None:
            lat, lon, _alt = self._plane.get_position()
            self._plane_origin_latlon = (lat, lon)
        origin_lat, origin_lon = self._plane_origin_latlon
        dlat = north_m / 111320.0
        dlon = east_m / (111320.0 * math.cos(math.radians(origin_lat)))
        return origin_lat + dlat, origin_lon + dlon

    def capture_frame(self):
        if self._perception is not None:
            try:
                frame = self._perception.get_current_frame()
                if frame is not None:
                    return frame
            except Exception:
                pass
        if self._sdk is not None and hasattr(self._sdk, "capture_frame"):
            try:
                return self._sdk.capture_frame()
            except Exception:
                pass
        return None

    def detect(self) -> list:
        if self._perception is None:
            return []
        frame = self.capture_frame()
        if frame is None:
            return []
        pose = self.get_pose()
        out = []
        for d in self._perception.detect(frame):
            # PerceptionService.Detection.direction_deg: + = right of center.
            # Backend bearing convention: + = left (body frame, matches contract.py).
            bearing = math.radians(-d.direction_deg)
            out.append(Detection(
                label=d.label,
                score=d.confidence,
                bbox=d.bbox,
                range_m=d.distance_m,
                bearing_rad=bearing,
                world_xyz=world_from_range_bearing(pose, d.distance_m, bearing),
            ))
        return out

    def get_pose(self) -> Optional[Tuple[float, float, float, float]]:
        if self._is_fixedwing():
            try:
                lat, lon, alt_m = self._plane.get_position()
                roll_deg, pitch_deg, yaw_deg = self._plane.get_attitude()
            except Exception:
                return None
            if self._plane_origin_latlon is None:
                self._plane_origin_latlon = (lat, lon)
            origin_lat, origin_lon = self._plane_origin_latlon
            north_m = (lat - origin_lat) * 111320.0
            east_m = (lon - origin_lon) * 111320.0 * math.cos(math.radians(origin_lat))
            # Pose tuples are ENU (x=east, y=north, z=up, yaw) throughout this
            # seam — matches SimBackend's explicit convention (class docstring
            # below) and the world_xyz storage RETURN_TO_LANDMARK relies on
            # (goto() takes north-then-east, the opposite order, hence the
            # landmark.y/landmark.x swap at that call site).
            return (east_m, north_m, alt_m, math.radians(yaw_deg))
        if self._nav is not None:
            pose = self._nav.get_pose()
            if pose is not None:
                return pose
        return None

    def get_battery(self) -> Optional[dict]:
        if self._is_fixedwing():
            try:
                return self._plane.get_battery()
            except Exception:
                return None
        if self._sdk is None:
            return None
        try:
            return self._sdk.get_battery()
        except Exception:
            return None

    def drive(self, airspeed: float, yaw_rate: float, climb: float = 0.0) -> None:
        if self._is_fixedwing():
            # ArduPlane has no continuous airspeed/yaw_rate streaming primitive
            # wired here (unlike Sim's fw_drive) — real hardware commands go
            # through goto()/orbit() instead. Explicit no-op, not a misfire onto
            # a copter-shaped call (see plane_sdk.py module docstring).
            return
        # Copter/rover path unchanged: no fixed-wing airspeed/bank primitive on
        # real hardware (drone_sdk is quad-shaped). Best-effort fallback so the
        # call doesn't crash on a quad-only rig.
        if self._sdk is not None and hasattr(self._sdk, "set_velocity"):
            try:
                self._sdk.set_velocity(airspeed, 0.0, climb)
            except Exception:
                pass

    def goto(self, north_m: float, east_m: float, alt_m: float):
        if self._is_fixedwing():
            lat, lon = self._plane_offset_to_latlon(north_m, east_m)
            # max_alt MUST be passed explicitly here — self._plane.goto is
            # drone_sdk.goto (plane_sdk re-exports it, doesn't wrap it), whose
            # default max_alt is a copter-oriented 20m. Confirmed live in
            # ArduPlane SITL: without this, a min_clearance_alt climb-over-
            # obstacle leg (the actual mechanism behind "fly over the trees")
            # got silently clamped to 20m — exactly defeating the guarantee
            # this call exists to provide, with only an easy-to-miss log line
            # ("SAFETY: ... clamped to maximum 20.0") as a symptom.
            return self._plane.goto(lat, lon, alt_m, max_alt=self._plane.MAX_ALTITUDE)
        if self._nav is None:
            return None
        return self._nav.navigate_to_offset(north_m, east_m, alt_m)

    def loiter(self, center: Optional[tuple], radius: float) -> None:
        if self._is_fixedwing():
            # center is (x=east, y=north, z=up) — same ENU pose convention as
            # get_pose() above (callers build it directly from get_pose(), e.g.
            # reasoning_loop.py's `center = (pose[0], pose[1], pose[2])`).
            if center is not None:
                east_m, north_m = center[0], center[1]
                alt_m = center[2] if len(center) > 2 else None
            else:
                north_m, east_m, alt_m = 0.0, 0.0, None
            lat, lon = self._plane_offset_to_latlon(north_m, east_m)
            self._plane.orbit(lat, lon, radius_m=radius, alt_m=alt_m)
            return
        # No IRL orbit/loiter primitive yet (deferred fixed-wing SDK gap). Best
        # effort: hold near `center` via Nav2 point navigation.
        if self._nav is not None and center is not None:
            z = center[2] if len(center) > 2 else 0.0
            self._nav.navigate_to_position(center[0], center[1], z)

    def log_event(self, kind: str, data: dict) -> None:
        # No structured telemetry sink on real hardware yet (the Godot event log
        # this mirrors is sim-only) — a no-op here, not a stub: MissionLoop must
        # be able to call this unconditionally regardless of backend.
        pass

    def unproject(self, nx: float, ny: float) -> Optional[Tuple[float, float, float]]:
        # No camera-intrinsics-based pixel->world projection wired for real
        # hardware yet — callers (NAVIGATE_TO_POINT) fall back to the legacy
        # Nav2 depth-at-pixel path when this returns None, so real hardware
        # keeps its existing behavior unchanged.
        return None

    def takeoff(self, alt_m: float) -> bool:
        if self._is_fixedwing():
            try:
                return bool(self._plane.hand_launch(alt_m))
            except Exception:
                return False
        # Quad path: identical to what _exec_arm_and_takeoff did directly before
        # this seam existed — same two calls, same object.
        if self._sdk is None:
            return False
        try:
            self._sdk.arm()
            return bool(self._sdk.takeoff(alt_m))
        except Exception:
            return False

    def land(self, heading_deg: Optional[float] = None) -> bool:
        if self._is_fixedwing():
            # A flying wing cannot cut throttle and drop straight down (no
            # copter LAND mode) — it needs a real approach point + an
            # into-wind heading, and plane_sdk.land() will not guess one (see
            # its module docstring): guessing wrong risks a cross/downwind
            # landing on a real aircraft. Prefer a live FC wind estimate; if
            # none is available and the caller didn't supply heading_deg
            # either, fail safe rather than fabricate a heading.
            if heading_deg is None:
                wind = None
                if hasattr(self._plane, "get_wind_estimate"):
                    try:
                        wind = self._plane.get_wind_estimate()
                    except Exception:
                        wind = None
                if wind is None:
                    return False
                wind_from_deg, _speed_mps = wind
                heading_deg = (wind_from_deg + 180.0) % 360.0  # fly INTO the wind
            # Land near the captured launch point if we have one, else wherever
            # we are now (still requires the caller-resolved/wind heading above).
            if self._plane_origin_latlon is not None:
                approach_lat, approach_lon = self._plane_origin_latlon
            else:
                try:
                    approach_lat, approach_lon, _alt = self._plane.get_position()
                except Exception:
                    return False
            try:
                return bool(self._plane.land(approach_lat, approach_lon, heading_deg))
            except Exception:
                return False
        if self._sdk is None:
            return False
        try:
            self._sdk.land()
            return True
        except Exception:
            return False

    def rtl(self, alt_m: Optional[float] = None) -> bool:
        if self._is_fixedwing():
            try:
                return bool(self._plane.rtl())
            except Exception:
                return False
        # Quad/rover path: unchanged from what _exec_return_home did directly
        # before this seam existed (Nav2 offset back to home/origin).
        if self._nav is None:
            return False
        result = self._nav.navigate_to_offset(0.0, 0.0, alt_m if alt_m is not None else 5.0)
        # Compare against the literal string rather than importing
        # nav2_bridge.NavigationStatus here — NavigationStatus is a (str, Enum)
        # so this is exactly equivalent when a real NavigationResult comes
        # back, but doesn't add a fragile cross-module import to this file
        # just for one enum member.
        return getattr(result, 'status', 'failed') != 'failed'

    def configure_safety(self, fence_radius_m: Optional[float] = None,
                          fence_max_alt_m: Optional[float] = None,
                          min_alt_floor_m: Optional[float] = None) -> bool:
        if not self._is_fixedwing():
            # Out of scope for this plan (fixed-wing-focused) — a real
            # ArduCopter geofence would be a separate, deliberate addition,
            # not silently bundled in here. No-op success, not a failure:
            # MissionLoop must be able to call this unconditionally
            # regardless of backend (same contract as log_event()).
            return True
        # fence_max_alt_m/min_alt_floor_m default to plane_sdk's own
        # MAX_ALTITUDE/MIN_ALTITUDE constants (module-level attrs on the
        # plane_sdk module passed in as self._plane) rather than duplicating
        # those numbers here — single source of truth. fence_radius_m has no
        # natural per-airframe default (it's genuinely site-specific: how far
        # this mission is allowed to range from home) — 500 m is a
        # conservative placeholder that MUST be reviewed for the real flight
        # site, not trusted blindly; this is deliberately not something the
        # cloud/LLM mission planner sets per-command (see reasoning_loop.py's
        # call site).
        if fence_radius_m is None:
            fence_radius_m = 500.0
        if fence_max_alt_m is None:
            fence_max_alt_m = getattr(self._plane, "MAX_ALTITUDE", 120.0)
        if min_alt_floor_m is None:
            min_alt_floor_m = getattr(self._plane, "MIN_ALTITUDE", 15.0)
        try:
            envelope_ok = bool(self._plane.configure_safety_envelope(
                fence_radius_m, fence_max_alt_m, min_alt_floor_m))
            # ArduPlane's own TKOFF_THR_MINACC/MINSPD default to 0 (confirmed
            # in ArduPlane/Parameters.cpp — 0 disables the acceleration-based
            # launch-detection test entirely). Nothing in the mission-phase
            # dispatch path called configure_launch_detection() before this
            # fix, so any mission-driven arm_and_takeoff on a fixed-wing —
            # real hardware included, not just this SITL test — would arm
            # into TAKEOFF mode with launch detection effectively unconfigured
            # and could fail to ever un-suppress throttle for a real hand
            # throw. Called here, alongside the fence/floor envelope, so
            # every path that arms via the mission phase system (not just
            # sitl_plane.py's run_gate(), which calls it directly) gets this.
            launch_detect_ok = bool(self._plane.configure_launch_detection())
            return envelope_ok and launch_detect_ok
        except Exception:
            return False


class SimBackend:
    """Drives the Godot fixed-wing sim (fixedwing_manager.gd) via a DepotClient
    (rover/sim/depot_client.py), talking to the shared Godot IPC (ipc_server.gd).

    Coordinates are ENU (x=east, y=north, z=up), matching fixedwing_manager.gd.
    """

    def __init__(self, client, agent_id: str):
        self._client = client
        self._id = agent_id
        self._last_frame_digest = None
        self._repeat_frames = 0
        self.stale_frame_recoveries = 0

    # Recover on the FIRST repeated capture. Measured under a real VLM, the
    # render target re-freezes immediately after every recovery, so tolerating
    # even one stale frame per cycle left half of all captures stale. Nothing
    # can hold a fixed-wing still either (stop() is a 12 m/s loiter), so two
    # genuinely identical consecutive frames essentially cannot happen in
    # flight — and if one did, the only cost is a spare viewport rebuild.
    _STALE_FRAME_LIMIT = 1

    def capture_frame(self):
        # fixedwing_manager.gd's forward-camera IPC (fw_grab_frame). Requires
        # gui=True at launch (headless Godot's dummy renderer leaves it blank)
        # — same limitation grab_vantage has (see fw_eval.py).
        #
        # The render target freezes PERMANENTLY once the VLM starts using the
        # GPU, which on a mission run is essentially immediately. From then on
        # every capture is byte-identical stale pixels while detect() carries on
        # reporting the truth, so the model reasons about a photograph of the
        # past and looks blind rather than misinformed. This cost a whole
        # perception-gate run before being diagnosed; see
        # FixedWingManager.reset_camera and Bug 11 in
        # papers/fixed_wing_sitl_lessons_learned.md.
        frame = self._client.fw_grab_frame(self._id)
        if frame is None:
            return None

        digest = hashlib.md5(frame).digest()
        if digest == self._last_frame_digest:
            self._repeat_frames += 1
            if self._repeat_frames >= self._STALE_FRAME_LIMIT:
                self._recover_camera()
                fresh = self._client.fw_grab_frame(self._id)
                if fresh is not None:
                    frame = fresh
                    digest = hashlib.md5(frame).digest()
                self._repeat_frames = 0
        else:
            self._repeat_frames = 0

        self._last_frame_digest = digest
        return frame

    def _recover_camera(self):
        """Rebuild the forward camera, then let it render before the next read."""
        self.stale_frame_recoveries += 1
        try:
            self._client.fw_reset_camera(self._id)
        except Exception:
            return
        # A freshly built SubViewport has no content until it has drawn once.
        time.sleep(0.15)
        self.log_event("camera_recovered", {
            "reason": "stale forward-camera frames",
            "count": self.stale_frame_recoveries,
        })

    def detect(self) -> list:
        out = []
        for obj in self._client.fw_detect(self._id):
            world = obj.get("world")
            out.append(Detection(
                label=obj.get("label", "unknown"),
                score=float(obj.get("confidence", 0.0)),
                world_xyz=tuple(world) if world is not None else None,
                top_z=obj.get("top_z"),
                peer_id=obj.get("peer_id"),
            ))
        return out

    def get_pose(self) -> Optional[Tuple[float, float, float, float]]:
        st = self._client.fw_state(self._id)
        if not st.get("ok", True):
            return None
        pos = st.get("position")
        yaw = st.get("yaw")
        if pos is None or yaw is None:
            return None
        return (float(pos[0]), float(pos[1]), float(pos[2]), float(yaw))

    def get_battery(self) -> Optional[dict]:
        st = self._client.fw_state(self._id)
        level = st.get("battery_level")
        return {"remaining": level} if level is not None else None

    # Commanded-altitude floor for goto(). The demo flies deliberately low
    # run-ins, but a model free to name any altitude will eventually name one
    # that puts the aircraft in the ground; the sim's own envelope protection
    # is the last resort, not the intended control. Kept above that hard floor
    # so a normal descent never trips an envelope event.
    MIN_COMMANDED_ALT_M = 12.0

    def drive(self, airspeed: float, yaw_rate: float, climb: float = 0.0) -> None:
        self._client.fw_drive(self._id, airspeed, yaw_rate, climb)

    def _climb_for(self, current_alt: float, target_alt: Optional[float]) -> float:
        """Proportional climb command toward `target_alt`, floored for safety."""
        if target_alt is None:
            return 0.0
        want = max(float(target_alt), self.MIN_COMMANDED_ALT_M)
        return max(-8.0, min(8.0, 0.9 * (want - current_alt)))

    def goto(self, north_m: float, east_m: float, alt_m: float,
             timeout_s: float = 60.0, tol_m: float = 5.0) -> bool:
        """Best-effort point-to-point flight, composed from drive() + pose polling.

        The Godot fixed-wing IPC has no single-shot "fly to point" op (only
        drive/loiter) — a fixed-wing can't be teleport-commanded to a waypoint the
        way Nav2 does for quad/rover, so this is a simple proportional heading-hold
        loop, not a delegation to a richer primitive. `north_m`/`east_m` are ENU
        offsets from the world origin (this sim's coordinate convention), not an
        offset from the current position. Blocks the calling thread; callers on a
        real-time loop should prefer drive() directly with their own polling.

        `alt_m` used to be accepted and ignored — every caller's target altitude
        was silently dropped and the aircraft flew its whole mission at spawn
        height. It is now flown as a proportional climb command alongside the
        heading hold, floored at MIN_COMMANDED_ALT_M. Arrival is still judged on
        horizontal distance alone: a fixed-wing trades altitude far more slowly
        than ground track, so requiring both would stall the leg.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            pose = self.get_pose()
            if pose is None:
                return False
            x, y, z, yaw = pose
            dx, dy = east_m - x, north_m - y
            dist = math.hypot(dx, dy)
            if dist <= tol_m:
                return True
            desired = math.atan2(dy, dx)
            err = (desired - yaw + math.pi) % (2 * math.pi) - math.pi
            st = self._client.fw_state(self._id)
            cruise = float(st.get("airspeed", 18.0))
            yaw_rate = max(-0.6, min(0.6, err * 1.5))
            self.drive(cruise, yaw_rate, self._climb_for(z, alt_m))
            time.sleep(0.1)
        return False

    def loiter(self, center: Optional[tuple], radius: float) -> None:
        # True orbit-around-a-point geometry belongs to search_patterns.py (Slice
        # 2), which drives repeated drive() calls shaped into a circle. This is the
        # simple, always-safe fallback: min-airspeed straight-and-level, matching
        # fixedwing_manager.gd's own stop() semantics ("loiter, not true stop").
        self._client.fw_stop(self._id)

    def log_event(self, kind: str, data: dict) -> None:
        # Structured telemetry contract (Slice 2): pushes clarification/replan/
        # memory_landmark events into fixedwing_manager.gd's own event log
        # (alongside its internal pose_trace/inject_fired events), so reasoning
        # is inspectable via the same fw_events() call the harness already uses.
        self._client.fw_log_event(self._id, kind, data)

    def unproject(self, nx: float, ny: float) -> Optional[Tuple[float, float, float]]:
        # fixedwing_manager.gd's fw_unproject (built in Slice 1, verified live
        # then) was never actually wired into MissionLoop's action dispatch —
        # NAVIGATE_TO_POINT still called the legacy hardware-only Nav2 path,
        # which silently no-ops for SimBackend (confirmed live: repeated
        # identical navigate_to_point decisions with the aircraft never
        # actually moving). This is what closes that gap.
        world = self._client.fw_unproject(self._id, nx, ny)
        return tuple(world) if world is not None else None

    def takeoff(self, alt_m: float) -> bool:
        # KNOWN GAP: fixedwing_manager.gd/depot_client.py have no launch/ground-
        # roll physics at all — fw_spawn() places the aircraft already airborne
        # (grep confirms only fw_spawn/fw_despawn exist, no fw_takeoff/fw_land).
        # Honest best-effort given that: climb/descend to alt_m from wherever it
        # currently is, rather than claim a real launch sequence happened. A
        # real "arm_and_takeoff" phase test in sim (Milestone S1/S2) needs actual
        # ground/launch physics added to fixedwing_manager.gd to mean anything
        # beyond an altitude change — tracked as a follow-up, not silently
        # papered over here.
        pose = self.get_pose()
        if pose is None:
            return False
        x, y, _z, _yaw = pose
        return self.goto(y, x, alt_m)

    def land(self, heading_deg: Optional[float] = None) -> bool:
        # Same gap as takeoff() above: no touchdown/ground-contact mechanic
        # exists in the Godot fixed-wing sim. Honest best-effort: return toward
        # the world origin and descend to a low altitude; log the request so
        # it's inspectable via fw_events(), but this does NOT assert a real
        # landing occurred the way plane_sdk.land()'s _wait_for_disarm() does
        # on real hardware. Do not treat a True return here as proof of a safe
        # landing in a sim-based capability demo — see the follow-up note above.
        self.log_event("land_requested", {"heading_deg": heading_deg})
        ok = self.goto(0.0, 0.0, 2.0)
        self._client.fw_stop(self._id)
        return ok

    def rtl(self, alt_m: Optional[float] = None) -> bool:
        pose = self.get_pose()
        cruise_alt = alt_m if alt_m is not None else (pose[2] if pose else 20.0)
        return self.goto(0.0, 0.0, cruise_alt)

    def configure_safety(self, fence_radius_m: Optional[float] = None,
                          fence_max_alt_m: Optional[float] = None,
                          min_alt_floor_m: Optional[float] = None) -> bool:
        # No real MAVLink geofence exists in the Godot sim — genuinely
        # nothing to configure here (unlike takeoff()/land()'s "honest
        # best-effort" gaps above, there isn't even an approximate sim
        # equivalent to fall back to). No-op success so MissionLoop can call
        # this unconditionally regardless of backend, same as log_event().
        return True
