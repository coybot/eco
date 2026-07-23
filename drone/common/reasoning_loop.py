"""
Mission loop - VLM-based perception-reasoning-action for autonomous drones.

1. Receives a Mission from the cloud (Claude)
2. Continuously: perceives (VLM) -> decides -> acts (Nav2) -> repeat
3. Can ask cloud for help when stuck
4. Reports progress and completion back to the user

Runs on Orin Nano, NX, and AGX (variant-specific VLM chosen at install).
"""
from __future__ import annotations
# ^ Required, not decorative: VLMService/Nav2Bridge are conditionally
# imported (VLM_AVAILABLE/NAV2_AVAILABLE, below) since not every deployment
# has the VLM stack (e.g. a pure fixed-wing flight-SDK test environment with
# no llama_cpp at all) — but several method signatures reference those names
# in return-type annotations unconditionally. Without deferred evaluation, a
# module-level NameError on class definition itself made this file
# unimportable anywhere the VLM import legitimately failed (caught live: a
# plane-only SITL test box with no vlm.py present at all).

import sys
import time
import json
import math
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
from dataclasses import dataclass, field

# Add parent for imports
COMMON_DIR = Path(__file__).parent.absolute()
sys.path.insert(0, str(COMMON_DIR))

# VLM and Nav2
VLM_AVAILABLE = False
NAV2_AVAILABLE = False

try:
    from vlm import VLMService, VLMAction, ActionType, get_vlm_service
    VLM_AVAILABLE = True
except ImportError:
    pass

try:
    from nav2_bridge import Nav2Bridge, NavigationStatus, get_nav_bridge
    NAV2_AVAILABLE = True
except ImportError:
    pass

from perception import PerceptionService
from backends import Backend, HardwareBackend
from vehicle_class import VehicleClass, get_class
from spatial_memory import SpatialMemory, normalize_label, labels_match
import search_patterns
import mission_vocab

# Fallback image dimensions for NAVIGATE_TO_POINT's pixel->normalized conversion
# when the captured frame isn't a numpy array to read .shape from (SimBackend's
# capture_frame() returns raw JPEG bytes) — matches fixedwing_manager.gd's own
# CAM_VIEWPORT_W/H exactly, since that's the only camera actually producing
# non-array frames today.
CAM_VIEWPORT_W = 640
CAM_VIEWPORT_H = 480

# Labels worth an honest caveat on a COUNT result: SpatialMemory dedups by
# world position, which is exactly right for a parked car but only
# approximately right for something that can walk between sightings during an
# orbit — not a computed confidence interval (no velocity tracking exists),
# just an honest structural note so a count of a possibly-moving target isn't
# presented with false precision. Deliberately small/conservative; extend as
# real target classes are added (see change D).
POSSIBLY_MOVING_LABELS = {"person", "people", "pedestrian", "human", "animal", "dog", "cat"}


def _phase_wants_count(phase: Dict[str, Any]) -> bool:
    """Heuristic: does this phase's own objective/success text ask for a
    count? Used to require an actual COUNT action (not just any grounded-
    sounding completion) before a counting-flavored phase is allowed to
    conclude — a real gap found live: a car WAS detected and remembered
    (satisfying the existing "something relevant was seen" grounding check),
    yet the model later concluded via a bare MISSION_COMPLETE claiming "no
    cars found", never once calling count, contradicting its own memory.
    Grounding verifies something relevant was seen; it doesn't verify the
    specific claim (found vs. not, and how many) is consistent with memory."""
    text = f"{phase.get('objective', '')} {phase.get('success', '')}".lower()
    return "count" in text or "how many" in text


DRONE_SDK_AVAILABLE = False
try:
    import drone_sdk as _drone_sdk
    DRONE_SDK_AVAILABLE = True
except ImportError:
    pass


@dataclass
class Mission:
    """
    A mission from the cloud with multiple phases.
    
    Used for mission autonomy on Orin Nano, NX, and AGX (when VLM is available).
    """
    mission_id: str
    phases: List[Dict[str, Any]]
    conversation_id: Optional[str] = None
    original_message: Optional[str] = None
    
    # Tracking
    current_phase: int = 0
    start_time: float = 0.0
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Mission':
        return cls(
            mission_id=data.get('mission_id', ''),
            phases=data.get('phases', []),
            conversation_id=data.get('conversation_id'),
            original_message=data.get('original_message'),
        )
    
    def get_current_phase(self) -> Optional[Dict[str, Any]]:
        if self.current_phase < len(self.phases):
            return self.phases[self.current_phase]
        return None
    
    def advance_phase(self) -> bool:
        """Advance to next phase. Returns True if more phases remain."""
        self.current_phase += 1
        return self.current_phase < len(self.phases)
    
    def is_complete(self) -> bool:
        return self.current_phase >= len(self.phases)


@dataclass
class MissionResult:
    """Result of a mission execution."""
    success: bool
    summary: str
    phases_completed: int
    total_phases: int
    findings: List[str] = field(default_factory=list)
    photos: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    actions_taken: int = 0
    failure_reason: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'success': self.success,
            'summary': self.summary,
            'phases_completed': self.phases_completed,
            'total_phases': self.total_phases,
            'findings': self.findings,
            'photos': self.photos,
            'duration_seconds': self.duration_seconds,
            'actions_taken': self.actions_taken,
            'failure_reason': self.failure_reason,
        }


class MissionLoop:
    """
    VLM-based mission execution loop for Orin Nano, NX, and AGX.
    
    Uses Qwen3-VL to perceive the environment and decide actions,
    with Nav2 for navigation execution. Variant-specific VLM size
    (e.g. 2B on Nano, 8B on NX/AGX32, 32B on AGX64) is chosen at install.
    """
    
    # Safety limits
    MAX_ACTIONS = 200           # Maximum actions before forced termination
    MAX_DURATION_SECONDS = 1800  # Maximum duration (30 minutes)
    MAX_PHASE_ACTIONS = 50      # Maximum actions per phase
    MAX_REPLANS_PER_PHASE = 2   # Bounded retries before a phase failure is a true abort
    MAX_CONSECUTIVE_VLM_FAILURES = 3  # Bounded before a run of unusable VLM output becomes a phase failure
    # Fraction of an in-progress SEARCH_AREA plan that must be flown before a
    # COUNT of zero for that exact target is honored — confirmed live this
    # needed to be a code-level gate, not just prompt guidance ("count once
    # confident you've covered enough" was NOT reliably followed): a real run
    # searched only 4 of 32 planned legs, found nothing, and reported a
    # confident zero for a target that was genuinely ~150m away and never
    # reached. A real nonzero count is NOT gated by this — finding something
    # early is a legitimately strong signal on its own; it's specifically an
    # unearned "definitely zero" that needs real coverage behind it.
    MIN_SEARCH_COVERAGE_BEFORE_ZERO_COUNT = 0.5

    def __init__(
        self,
        mqtt_client=None,
        conversation_id: str = None,
        on_progress: Callable[[str], None] = None,
        drone_sdk=None,
        backend: Optional[Backend] = None,
        vehicle_class: Optional[VehicleClass] = None,
    ):
        """
        Initialize mission loop.

        Args:
            mqtt_client: MQTT client for cloud communication
            conversation_id: Current conversation ID
            on_progress: Callback for progress updates
            drone_sdk: Drone SDK for camera/telemetry (optional)
            backend: Actuation/sensing backend (backends.Backend). Defaults to a
                HardwareBackend wrapping today's collaborators (perception/nav/
                drone_sdk), preserving prior behavior — pass a SimBackend to fly a
                sim vehicle (e.g. the Godot fixed-wing sim) through this same loop.
            vehicle_class: Capability descriptor (vehicle_class.VehicleClass).
                Defaults to "quadcopter" to match current on-device behavior.
        """
        self.mqtt_client = mqtt_client
        self.conversation_id = conversation_id
        self.on_progress = on_progress
        self.drone_sdk = drone_sdk
        self.backend: Optional[Backend] = backend
        self.vehicle_class: VehicleClass = vehicle_class or get_class("quadcopter")

        # World-frame landmark memory. merge_radius scales with sense_range_m: the
        # default 1.5m (SpatialMemory's own default) was tuned for the quad's ~10m
        # detection range; a fixed-wing spotting a target from up to 80m has much
        # larger single-glimpse cross-range error, so widen proportionally. For the
        # quadcopter default (sense_range_m=10.0) this reduces to exactly 1.5m —
        # unchanged from before this seam existed.
        merge_radius = 1.5 * max(1.0, self.vehicle_class.sense_range_m / 10.0)
        self.memory = SpatialMemory(merge_radius=merge_radius)

        # VLM and Nav2 (lazy loaded)
        self._vlm: Optional[VLMService] = None
        self._nav: Optional[Nav2Bridge] = None
        self._perception: Optional[PerceptionService] = None

        # State tracking
        self._current_mission: Optional[Mission] = None
        self._history: List[str] = []
        self._findings: List[str] = []
        self._photos: List[str] = []
        self._home_lat: Optional[float] = None
        self._home_lon: Optional[float] = None
        self._home_alt: Optional[float] = None

        # In-progress search_patterns waypoint plan for ActionType.SEARCH_AREA —
        # one leg is flown per VLM decision (matching every other action's "one
        # bounded movement per decision" shape), so the plan persists across ticks.
        self._search_plan: List[tuple] = []
        self._search_idx: int = 0
        self._search_target: Optional[str] = None

    def _get_vlm(self) -> Optional[VLMService]:
        """Lazy-load VLM service."""
        if not VLM_AVAILABLE:
            return None
        if self._vlm is None:
            self._vlm = get_vlm_service()
        return self._vlm
    
    def _get_nav(self) -> Optional[Nav2Bridge]:
        """Lazy-load Nav2 bridge."""
        if not NAV2_AVAILABLE:
            return None
        if self._nav is None:
            self._nav = get_nav_bridge()
        return self._nav
    
    def _get_perception(self) -> PerceptionService:
        """Lazy-load perception service."""
        if self._perception is None:
            self._perception = PerceptionService()
        return self._perception

    def _get_backend(self) -> Backend:
        """Lazy-build the actuation/sensing backend.

        Defaults to a HardwareBackend wrapping today's collaborators, so quad/rover
        missions behave exactly as they did before the backend seam existed. Callers
        that want to fly a sim vehicle (e.g. fixed-wing in Godot) pass an explicit
        `backend=` to the constructor instead of relying on this default.
        """
        if self.backend is None:
            sdk = _drone_sdk if DRONE_SDK_AVAILABLE else self.drone_sdk
            self.backend = HardwareBackend(
                drone_sdk=sdk,
                perception=self._get_perception(),
                nav=self._get_nav(),
            )
        return self.backend

    def run(self, mission: Mission) -> MissionResult:
        """
        Execute a mission using VLM-based perception-action loop.

        Wraps _run_impl with optional training-data capture: starts a recorder episode so
        per-frame det/vlm/plan rows share an episode id, and logs the final MissionResult
        (the success/fail reward signal). No-op unless a recorder is enabled.
        """
        try:
            from data_recorder import get_default
            recorder = get_default()
        except Exception:
            recorder = None

        if recorder is not None and recorder.enabled:
            recorder.start_episode(
                {"message": mission.original_message, "phases": len(mission.phases)}
            )

        result = self._run_impl(mission)

        if recorder is not None and recorder.enabled:
            try:
                recorder.record_mission(result)
            except Exception:
                pass

        return result

    def _run_impl(self, mission: Mission) -> MissionResult:
        mission.start_time = time.time()
        self._current_mission = mission
        self._history = []
        self._findings = []
        self._photos = []
        self._home_lat = None
        self._home_lon = None
        self._home_alt = None
        self._search_plan = []
        self._search_idx = 0
        self._search_target = None
        actions_taken = 0

        self._report_progress(f"Starting mission: {mission.original_message or 'Unknown'}", phase=0)
        self._report_progress(f"Phases: {len(mission.phases)}")

        # Capture home position for GPS/offset navigation
        if DRONE_SDK_AVAILABLE:
            try:
                self._home_lat, self._home_lon, self._home_alt = _drone_sdk.get_position()
            except Exception:
                pass

        # Ceiling guard runs for the entire mission as a safety thread
        if DRONE_SDK_AVAILABLE:
            try:
                _drone_sdk.start_ceiling_guard()
            except Exception:
                pass

        # FC-enforced safety envelope (geofence + altitude floor) — the real
        # guarantee behind any "climb over an obstacle" decision downstream;
        # perception/reasoning only ever *trigger* a climb, this is what
        # enforces it regardless (change E). No-ops on quad/rover/sim
        # backends that don't have (or need) this — see configure_safety()'s
        # per-backend docstrings. Deliberately called with no mission-
        # specific arguments: fence radius/altitude floor are airframe/site
        # safety parameters, not something a free-form command should be
        # able to set.
        try:
            if not self._get_backend().configure_safety():
                self._report_progress("WARNING: safety envelope not fully configured")
        except Exception as e:
            self._report_progress(f"WARNING: configure_safety() failed: {e}")

        try:
            # Execute each phase
            while not mission.is_complete():
                phase = mission.get_current_phase()
                if phase is None:
                    break
                
                phase_num = mission.current_phase + 1
                self._report_progress(f"Phase {phase_num}/{len(mission.phases)}: {phase.get('objective', 'Unknown')}")
                try:
                    from data_recorder import get_default
                    get_default().set_phase(phase.get('objective'))
                except Exception:
                    pass
                
                # Execute phase — a fixed-wing can't just hover-and-abort on failure
                # (that's meaningless mid-air), so a failed phase gets a bounded
                # number of loiter-and-retry replans before it becomes a true abort.
                # Each retry re-enters the phase with a fresh action budget and
                # whatever memory/history the failed attempt already accumulated,
                # so e.g. a VLM phase that ran out of actions searching blind can
                # choose SEARCH_AREA/RETURN_TO_LANDMARK on the next attempt.
                replans_used = 0
                while True:
                    phase_result = self._execute_phase(phase, mission)
                    actions_taken += phase_result.get('actions', 0)

                    if not phase_result.get('failed'):
                        break

                    reason = phase_result.get('reason', 'Unknown')
                    elapsed = time.time() - mission.start_time
                    battery = self._get_backend().get_battery()
                    battery_ok = battery is None or battery.get('remaining', 100.0) > 15.0
                    budget_ok = (
                        replans_used < self.MAX_REPLANS_PER_PHASE
                        and elapsed < self.MAX_DURATION_SECONDS * 0.9
                        and battery_ok
                    )
                    if not budget_ok:
                        return MissionResult(
                            success=False,
                            summary=f"Failed at phase {phase_num}",
                            phases_completed=mission.current_phase,
                            total_phases=len(mission.phases),
                            findings=self._findings,
                            photos=self._photos,
                            duration_seconds=time.time() - mission.start_time,
                            actions_taken=actions_taken,
                            failure_reason=reason,
                        )

                    replans_used += 1
                    self._replan(phase, reason, replans_used)

                # Check safety limits
                elapsed = time.time() - mission.start_time
                if elapsed >= self.MAX_DURATION_SECONDS:
                    return MissionResult(
                        success=False,
                        summary="Mission timeout",
                        phases_completed=mission.current_phase,
                        total_phases=len(mission.phases),
                        findings=self._findings,
                        photos=self._photos,
                        duration_seconds=elapsed,
                        actions_taken=actions_taken,
                        failure_reason=f"Exceeded time limit ({self.MAX_DURATION_SECONDS}s)",
                    )
                
                if actions_taken >= self.MAX_ACTIONS:
                    return MissionResult(
                        success=False,
                        summary="Action limit reached",
                        phases_completed=mission.current_phase,
                        total_phases=len(mission.phases),
                        findings=self._findings,
                        photos=self._photos,
                        duration_seconds=elapsed,
                        actions_taken=actions_taken,
                        failure_reason=f"Exceeded action limit ({self.MAX_ACTIONS})",
                    )
                
                # Advance to next phase
                mission.advance_phase()
            
            # Mission complete
            return MissionResult(
                success=True,
                summary="Mission completed successfully",
                phases_completed=len(mission.phases),
                total_phases=len(mission.phases),
                findings=self._findings,
                photos=self._photos,
                duration_seconds=time.time() - mission.start_time,
                actions_taken=actions_taken,
            )
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            
            return MissionResult(
                success=False,
                summary="Error during mission",
                phases_completed=mission.current_phase,
                total_phases=len(mission.phases),
                findings=self._findings,
                photos=self._photos,
                duration_seconds=time.time() - mission.start_time,
                actions_taken=actions_taken,
                failure_reason=str(e),
            )
        
        finally:
            if DRONE_SDK_AVAILABLE:
                try:
                    _drone_sdk.stop_ceiling_guard()
                except Exception:
                    pass
            # self._findings is passed by reference into whichever MissionResult
            # was already constructed above (every return path uses findings=
            # self._findings, never a copy) — extending it here, in the one place
            # that always runs, makes memory inspectable post-flight without
            # duplicating this at every return statement.
            self._findings.extend(self._memory_finding_strings())
            self._cleanup()
    
    # Phase-type -> executor-method-name. Keys MUST match mission_vocab.
    # PHASE_SCHEMAS exactly (see _CHECK_PHASE_VOCAB_SYNC below, evaluated once
    # at import time) — this is what makes drift between the cloud planner's
    # vocabulary and what this dispatcher actually accepts a hard failure
    # instead of a silent no-op mission (the fixed-wing autonomy plan's
    # "change B": ending the previously-hand-duplicated phase-type lists).
    _PHASE_DISPATCH = {
        "arm_and_takeoff": "_exec_arm_and_takeoff",
        "nav": "_exec_nav",
        "go_to_gps": "_exec_go_to_gps",
        "fly_circle": "_exec_fly_circle",
        "look_around": "_exec_look_around",
        "capture_photo": "_exec_capture_photo",
        "return_home": "_exec_return_home",
        "land": "_exec_land",
    }

    def _execute_phase(self, phase: Dict[str, Any], mission: Mission) -> Dict[str, Any]:
        """Dispatch a phase to the appropriate executor based on its type field.
        No `type` (or an unrecognized one) falls through to the open-ended VLM
        loop — matching the cloud prompt's "untyped phase" contract."""
        phase_type = phase.get('type')
        method_name = self._PHASE_DISPATCH.get(phase_type)
        if method_name is not None:
            return getattr(self, method_name)(phase)
        return self._exec_vlm_phase(phase, mission)

    # ------------------------------------------------------------------ #
    # Typed phase executors — all actuation goes through the `backend`     #
    # seam (backends.py), NOT Nav2/drone_sdk directly. This is what makes  #
    # typed phases vehicle-agnostic: HardwareBackend routes to Nav2/       #
    # drone_sdk for a quad/rover (identical calls to before this rewrite)  #
    # or to plane_sdk for a fixed-wing; SimBackend routes to Godot. Before #
    # this change every typed phase silently no-op'd/failed on a plane —  #
    # see the fixed-wing autonomy plan's "change A".                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _goto_ok(result) -> bool:
        """Normalize backend.goto()'s return value across implementations:
        Nav2 (quad/rover) returns a NavigationResult (check .status, preserving
        the exact original failure detection); plane_sdk.goto() (fixed-wing
        hardware) is a fire-and-forget MAVLink send with no ACK and returns
        None (treated as accepted, matching how the existing VLM-loop action
        handlers already call backend.goto() without checking a return value
        at all); SimBackend.goto() returns a real bool (used directly).

        Compares against the literal string "failed" rather than importing
        NavigationStatus.FAILED — NavigationStatus is a (str, Enum) so this is
        exactly equivalent for a real NavigationResult, without this method
        blowing up if NAV2_AVAILABLE is False (NavigationStatus is None) but
        some other object happens to carry an unrelated `.status` attribute.
        """
        if result is None:
            return True
        if hasattr(result, 'status'):
            return getattr(result, 'status') != 'failed'
        return bool(result)

    def _gps_to_home_offset(self, lat: float, lon: float) -> tuple:
        """Same flat-earth conversion Nav2Bridge.navigate_to_gps uses internally
        (nav2_bridge.py) — duplicated here (not imported) so _exec_go_to_gps can
        route through the vehicle-agnostic backend.goto() (offset-based) rather
        than Nav2 directly, while producing IDENTICAL north_m/east_m for the
        quad path (zero behavior change there)."""
        north_m = (lat - self._home_lat) * 111320.0
        east_m = (lon - self._home_lon) * 111320.0 * math.cos(math.radians(self._home_lat))
        return north_m, east_m

    def _exec_arm_and_takeoff(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        alt = phase.get('altitude_m', 5.0)
        self._report_progress(f"Arming and taking off to {alt}m")
        try:
            ok = self._get_backend().takeoff(alt)
        except Exception as e:
            return {'failed': True, 'reason': str(e), 'actions': 1}
        if not ok:
            return {'failed': True, 'reason': 'takeoff did not report reaching altitude', 'actions': 1}
        return {'success': True, 'actions': 1}

    def _apply_clearance(self, alt_m: float, phase: Dict[str, Any]) -> float:
        """change E's climb-to-clear mechanism: a transit phase can carry a
        min_clearance_alt (e.g. "there's a tree line on this route, don't go
        below Xm") — this only ever raises the commanded altitude for the
        leg, never lowers it below what the phase already asked for. The
        REAL guarantee is still the FC-enforced altitude floor
        (configure_safety() at mission start); this is what lets a mission
        *express* a known obstacle instead of relying solely on the floor's
        much more conservative global minimum."""
        min_clearance = phase.get('min_clearance_alt')
        if min_clearance is not None and min_clearance > alt_m:
            self._report_progress(
                f"Climbing to {min_clearance}m for this leg's min_clearance_alt "
                f"(was {alt_m}m)"
            )
            return min_clearance
        return alt_m

    def _exec_nav(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        north_m = phase.get('north_m', 0.0)
        east_m = phase.get('east_m', 0.0)
        alt_m = self._apply_clearance(phase.get('alt_m', 5.0), phase)
        desc = phase.get('description', f'N={north_m}m E={east_m}m')
        self._report_progress(f"Navigating: {desc}")
        try:
            result = self._get_backend().goto(north_m, east_m, alt_m)
        except Exception as e:
            return {'failed': True, 'reason': str(e), 'actions': 1}
        if not self._goto_ok(result):
            reason = getattr(result, 'message', 'goto() did not succeed')
            return {'failed': True, 'reason': reason, 'actions': 1}
        return {'success': True, 'actions': 1}

    def _exec_go_to_gps(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        lat = phase.get('lat')
        lon = phase.get('lon')
        alt_m = self._apply_clearance(phase.get('alt_m', 15.0), phase)
        desc = phase.get('description', f'{lat},{lon}')
        self._report_progress(f"Flying to GPS: {desc}")
        if lat is None or lon is None:
            return {'failed': True, 'reason': 'Missing GPS coordinates in phase', 'actions': 0}
        if self._home_lat is None:
            return {'failed': True, 'reason': 'Home position unknown — GPS fix required', 'actions': 0}
        north_m, east_m = self._gps_to_home_offset(lat, lon)
        try:
            result = self._get_backend().goto(north_m, east_m, alt_m)
        except Exception as e:
            return {'failed': True, 'reason': str(e), 'actions': 1}
        if not self._goto_ok(result):
            reason = getattr(result, 'message', 'goto() did not succeed')
            return {'failed': True, 'reason': reason, 'actions': 1}
        return {'success': True, 'actions': 1}

    def _exec_fly_circle(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        radius_m = phase.get('radius_m', 10.0)
        alt_m = phase.get('altitude_m', 5.0)
        n_waypoints = phase.get('waypoints', 8)
        # Same clamp search_patterns.orbit() already applies for exactly this
        # reason: a radius tighter than the vehicle's own minimum turn radius
        # is not a "blocked" waypoint (SimBackend.goto()'s generic timeout
        # fallback message is misleading here) — it's physically unflyable,
        # confirmed live: a cloud-planned 20m circle for this fixed-wing
        # (turn radius ~42m) made every waypoint time out, reported as
        # "blocked", with the real cause invisible in that message. The cloud
        # prompt is told about this floor too (mission_vocab.py) so it
        # shouldn't need clamping in practice — this is the code-level
        # backstop, not the only line of defense.
        min_radius = search_patterns.turn_radius_m(self.vehicle_class) * 1.05
        if radius_m < min_radius:
            self._report_progress(
                f"Requested circle radius {radius_m}m is tighter than this "
                f"vehicle's minimum turn radius ({min_radius:.0f}m) — clamping"
            )
            radius_m = min_radius
        self._report_progress(f"Flying circle: radius={radius_m}m altitude={alt_m}m")
        backend = self._get_backend()
        # Same geometry as before this rewrite: each waypoint is an offset from
        # home/origin (a FIXED reference — confirmed against nav2_bridge.py's
        # navigate_to_offset docstring and backends.py's SimBackend.goto()
        # docstring, both "offset from home/world-origin", not current
        # position), so this traces the same circle for the quad path exactly
        # as before (backend.goto() delegates straight to the same Nav2 call).
        for i in range(n_waypoints):
            angle = (2 * math.pi * i) / n_waypoints
            north_m = radius_m * math.cos(angle)
            east_m = radius_m * math.sin(angle)
            self._report_progress(f"Circle waypoint {i + 1}/{n_waypoints}")
            try:
                result = backend.goto(north_m, east_m, alt_m)
            except Exception as e:
                return {'failed': True, 'reason': f'Waypoint {i + 1}: {e}', 'actions': i + 1}
            if not self._goto_ok(result):
                reason = getattr(result, 'message', 'blocked')
                return {'failed': True, 'reason': f'Waypoint {i + 1} blocked: {reason}', 'actions': i + 1}
        return {'success': True, 'actions': n_waypoints}

    def _exec_look_around(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        directions = phase.get('directions', 4)
        self._report_progress(f"Looking around ({directions} directions)")
        if not DRONE_SDK_AVAILABLE:
            return {'failed': True, 'reason': 'drone_sdk not available', 'actions': 0}
        try:
            urls = _drone_sdk.look_around(directions=directions)
            if urls:
                self._photos.extend(urls)
            return {'success': True, 'actions': 1}
        except Exception as e:
            return {'failed': True, 'reason': str(e), 'actions': 1}

    def _exec_capture_photo(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        self._report_progress("Capturing photo")
        if not DRONE_SDK_AVAILABLE:
            return {'failed': True, 'reason': 'drone_sdk not available', 'actions': 0}
        try:
            url = _drone_sdk.capture_photo(upload=True)
            if url:
                self._photos.append(url)
            return {'success': True, 'actions': 1}
        except Exception as e:
            return {'failed': True, 'reason': str(e), 'actions': 1}

    def _exec_return_home(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        alt_m = phase.get('alt_m', 5.0)
        self._report_progress("Returning home")
        try:
            ok = self._get_backend().rtl(alt_m)
        except Exception as e:
            return {'failed': True, 'reason': str(e), 'actions': 1}
        if not ok:
            return {'failed': True, 'reason': 'rtl() did not succeed', 'actions': 1}
        return {'success': True, 'actions': 1}

    def _exec_land(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        self._report_progress("Landing")
        heading_deg = phase.get('heading_deg')  # fixed-wing only; ignored by quad
        try:
            ok = self._get_backend().land(heading_deg)
        except Exception as e:
            return {'failed': True, 'reason': str(e), 'actions': 1}
        if not ok:
            # For a fixed-wing this commonly means no heading_deg was given AND
            # no live FC wind estimate was available — see
            # HardwareBackend.land()'s docstring on why that fails safe instead
            # of guessing an approach heading, rather than a generic error.
            return {'failed': True, 'reason': 'land() did not succeed (fixed-wing: '
                                               'may need an explicit heading_deg or '
                                               'a live wind estimate)', 'actions': 1}
        return {'success': True, 'actions': 1}

    # ------------------------------------------------------------------ #
    # VLM phase executor (open-ended: perceive → decide → act loop)        #
    # ------------------------------------------------------------------ #

    def _exec_vlm_phase(self, phase: Dict[str, Any], mission: Mission) -> Dict[str, Any]:
        """Execute an open-ended phase using the onboard VLM perception-action loop."""
        vlm = self._get_vlm()
        if vlm is None or not vlm.is_available():
            return {'failed': True, 'reason': 'VLM not available (Qwen3-VL not loaded)', 'actions': 0}

        nav = self._get_nav()
        perception = self._get_perception()
        backend = self._get_backend()

        phase_actions = 0
        consecutive_vlm_failures = 0
        already_reported_grounded_finding = False
        count_recorded_this_phase = False

        while phase_actions < self.MAX_PHASE_ACTIONS:
            # 1. Capture current frame (via the backend, so this works identically
            # whether we're flying real hardware or a sim vehicle — see backends.py)
            try:
                frame = backend.capture_frame()
            except Exception as e:
                print(f"Failed to capture frame: {e}")
                frame = None
            
            if frame is None:
                self._report_progress("Warning: No camera frame available")
                time.sleep(1)
                phase_actions += 1
                continue

            # 2. Detect objects via the backend and update world-frame memory. Gate
            # out anything beyond this vehicle's sense range as a safety net — sim
            # backends already range-gate internally (e.g. fixedwing_manager.gd's
            # DETECT_RANGE), so range_m is None there and this is a no-op; hardware
            # detections carry a real range_m and get gated here.
            try:
                detections = backend.detect()
            except Exception as e:
                print(f"Failed to detect: {e}")
                detections = []
            sense_range = self.vehicle_class.sense_range_m
            detections = [d for d in detections if d.range_m is None or d.range_m <= sense_range]
            for d in detections:
                if d.world_xyz is not None:
                    lm = self.memory.update(d.label, d.world_xyz[0], d.world_xyz[1], d.world_xyz[2], d.score)
                    if lm.hits == 1:
                        # First sighting of this landmark (not a re-merge into an
                        # existing one) — worth a telemetry event, not every update.
                        backend.log_event("memory_landmark", {
                            "label": lm.label, "x": lm.x, "y": lm.y, "z": lm.z, "score": lm.score,
                        })

            # 3. Get drone state
            drone_state = self._get_drone_state()

            # 4. Ask VLM what to do — current detections and remembered world-frame
            # landmarks both go into the prompt, so it can reason over "what I see
            # right now" and "what I've seen before" (e.g. to decide RETURN_TO_LANDMARK
            # for a target that's since passed out of view/range).
            action = vlm.decide(
                image=frame,
                mission_phase=phase,
                drone_state=drone_state,
                history=self._history[-10:],  # Last 10 actions
                detections=detections,
                memory=self.memory.all(),
            )

            phase_actions += 1

            # Hard guard, not just a prompt instruction — confirmed live that
            # asking nicely in the system prompt did NOT stop this: the model
            # claimed "silo located and reported" while the only actual
            # detection that whole tick was water_tower (a real detection,
            # just of the WRONG object) — a coarser "were there ANY
            # detections" guard would have missed this exact case, since
            # there genuinely were some, just not of what the phase actually
            # asked for. Correlates the phase's own objective/success wording
            # against every label actually detected/remembered so far
            # (substring match, same tolerant comparison as labels_match) —
            # if nothing detected/remembered so far is even mentioned in what
            # this phase is asking for, a "found it" claim isn't grounded,
            # regardless of what else happened to be in view.
            if action.action_type in (ActionType.REPORT, ActionType.PHASE_COMPLETE,
                                       ActionType.MISSION_COMPLETE, ActionType.MISSION_FAILED):
                # Extra hard guard specifically for counting phases, checked
                # BEFORE the grounding check below (and covering MISSION_FAILED
                # too, which the grounding check doesn't) — a real gap found
                # live: a car WAS detected and remembered (satisfying the
                # existing "something relevant was seen" grounding check
                # below), yet the model concluded via a bare MISSION_COMPLETE
                # claiming "no cars found" — never once calling the count
                # action, contradicting its own memory. Grounding verifies
                # SOMETHING relevant was seen; it does not verify that THIS
                # claim (found vs. not found, and how many) is consistent
                # with what memory actually contains. The fix isn't to parse
                # the claim's text for negation (fragile) — it's to require
                # a real count action to have actually run for a phase whose
                # own objective/success text asks for one, before honoring
                # any conclusion about it.
                if _phase_wants_count(phase) and not count_recorded_this_phase:
                    self._report_progress(
                        f"Rejecting {action.action_type.value} for a counting phase — "
                        f"no count action has run yet this phase (\"{action.message}\")"
                    )
                    action = VLMAction(
                        action_type=ActionType.SEARCH_AREA,
                        target_object=action.target_object,
                        reasoning="Overridden: a counting phase must produce a count before concluding",
                    )
                elif action.action_type == ActionType.MISSION_FAILED:
                    pass  # not subject to the grounding check below (a failure
                          # claim doesn't assert a positive finding the way
                          # REPORT/PHASE_COMPLETE/MISSION_COMPLETE do)
                else:
                    objective_blob = normalize_label(
                        f"{phase.get('objective', '')} {phase.get('success', '')}"
                    )
                    known_labels = {normalize_label(d.label) for d in detections}
                    known_labels |= {normalize_label(lm.label) for lm in self.memory.all()}
                    grounded = any(label and label in objective_blob for label in known_labels)
                    if not grounded:
                        self._report_progress(
                            f"Rejecting ungrounded {action.action_type.value} "
                            f"(\"{action.message}\") — nothing matching this phase's objective "
                            f"has actually been detected or remembered"
                        )
                        action = VLMAction(
                            action_type=ActionType.SEARCH_AREA,
                            target_object=None,
                            reasoning="Overridden: claimed a finding not backed by any matching detection/memory",
                        )
                    elif action.action_type == ActionType.REPORT:
                        # Second hard guard, same reason as the first: the system
                        # prompt already explicitly tells the model "if RECENT
                        # ACTIONS shows you reporting the same finding, don't
                        # report it again — call phase_complete instead." Confirmed
                        # live that it does NOT reliably follow this — one real run
                        # reported the identical already-grounded finding 26 times
                        # in a row until the action budget ran out, never once
                        # escalating on its own. Once a grounded finding has been
                        # reported once this phase, force any further REPORT into
                        # PHASE_COMPLETE instead of trusting the model to notice.
                        if already_reported_grounded_finding:
                            self._report_progress(
                                f"Already reported this phase's finding once — forcing phase_complete "
                                f"instead of repeating (\"{action.message}\")"
                            )
                            action = VLMAction(
                                action_type=ActionType.PHASE_COMPLETE,
                                message=action.message,
                                reasoning="Overridden: repeated an already-reported grounded finding",
                            )
                        else:
                            already_reported_grounded_finding = True

            elif action.action_type == ActionType.COUNT:
                # Deliberately NOT the same "ungrounded claim" rejection REPORT
                # gets above — a real, computed zero is always a valid, honest
                # answer (there's nothing to hallucinate about correctly
                # reporting "covered the area, found none"). Shares the same
                # anti-repetition state as REPORT (already_reported_grounded_
                # finding) since both represent "I've delivered my grounded
                # finding for this phase, stop repeating it".
                if not action.target_object:
                    self._report_progress("count action had no target_object — treating as a search decision instead")
                    action = VLMAction(
                        action_type=ActionType.SEARCH_AREA,
                        target_object=None,
                        reasoning="Overridden: count requested with no target_object",
                    )
                elif (
                    self.memory.count(action.target_object) == 0
                    and self._search_target is not None
                    and labels_match(action.target_object, self._search_target)
                    and self._search_plan
                    and self._search_idx < len(self._search_plan) * self.MIN_SEARCH_COVERAGE_BEFORE_ZERO_COUNT
                ):
                    # A confident zero needs real coverage behind it — see
                    # MIN_SEARCH_COVERAGE_BEFORE_ZERO_COUNT's docstring for the
                    # live failure this guards against. Only applies to a
                    # zero for the SAME target an active search plan is
                    # already covering; a nonzero count (something already
                    # found) is never held back by this.
                    covered_pct = 100 * self._search_idx / len(self._search_plan)
                    self._report_progress(
                        f"Rejecting a zero count for {action.target_object!r} — only "
                        f"{covered_pct:.0f}% of the planned search area covered so far "
                        f"({self._search_idx}/{len(self._search_plan)} legs); continuing "
                        f"to search instead of concluding none exist"
                    )
                    action = VLMAction(
                        action_type=ActionType.SEARCH_AREA,
                        target_object=action.target_object,
                        reasoning="Overridden: zero count claimed with insufficient search coverage",
                    )
                elif already_reported_grounded_finding:
                    n = self.memory.count(action.target_object)
                    self._report_progress(
                        f"Already reported a count this phase — forcing phase_complete "
                        f"instead of counting again (still {n} {action.target_object})"
                    )
                    action = VLMAction(
                        action_type=ActionType.PHASE_COMPLETE,
                        message=f"Counted {n} {action.target_object}",
                        reasoning="Overridden: count already reported this phase",
                    )
                else:
                    already_reported_grounded_finding = True

            self._history.append(f"{action.action_type.value}: {action.message or action.reasoning or ''}"[:100])

            # 4. Handle action
            if action.action_type == ActionType.PHASE_COMPLETE:
                self._report_progress(f"Phase complete: {action.message}")
                return {'success': True, 'actions': phase_actions}
            
            elif action.action_type == ActionType.MISSION_COMPLETE:
                self._report_progress(f"Mission complete: {action.message}")
                # This shouldn't happen mid-phase, but handle it
                return {'success': True, 'actions': phase_actions, 'mission_complete': True}
            
            elif action.action_type == ActionType.MISSION_FAILED:
                self._report_progress(f"Mission failed: {action.message}")
                return {'failed': True, 'reason': action.message, 'actions': phase_actions}
            
            elif action.action_type == ActionType.NAVIGATE_TO_POINT:
                if action.point_x is not None and action.point_y is not None:
                    # Prefer the backend seam's unproject() (real for
                    # SimBackend, via fixedwing_manager.gd's own raycast —
                    # built in Slice 1 but never actually wired in here until
                    # now). Confirmed live: the old nav.navigate_to_point()
                    # path silently no-ops against SimBackend — same class of
                    # bug as NAVIGATE_TO_OBJECT's legacy perception.find_nearest()
                    # crash above, just a no-op instead of a crash, which is
                    # arguably worse (looks like it's working; it isn't).
                    if hasattr(frame, "shape"):
                        img_h, img_w = frame.shape[0], frame.shape[1]
                    else:
                        img_w, img_h = CAM_VIEWPORT_W, CAM_VIEWPORT_H
                    nx = max(0.0, min(1.0, action.point_x / max(img_w, 1)))
                    ny = max(0.0, min(1.0, action.point_y / max(img_h, 1)))
                    world = backend.unproject(nx, ny)
                    if world is not None:
                        self._report_progress(f"Navigating to point ({action.point_x}, {action.point_y})")
                        # (north_m, east_m, alt_m) vs world_xyz's (east, north, up) — same swap as elsewhere.
                        backend.goto(world[1], world[0], world[2])
                    elif nav:
                        depth = self._get_depth_at_point(frame, action.point_x, action.point_y)
                        if depth is None:
                            depth = 3.0  # Default 3 meters if no depth
                        self._report_progress(f"Navigating to point ({action.point_x}, {action.point_y})")
                        result = nav.navigate_to_point(action.point_x, action.point_y, depth)
                        if result.status == NavigationStatus.FAILED:
                            self._history.append(f"Navigation failed: {result.message}")
                    else:
                        self._history.append(f"Could not resolve point ({action.point_x}, {action.point_y}) to a world position")
                else:
                    self._report_progress("Navigation requested but no point coordinates given")
            
            elif action.action_type == ActionType.NAVIGATE_TO_OBJECT:
                # Resolve via THIS tick's detections (backend.detect(), already
                # computed above) first, falling back to memory — NOT
                # perception.find_nearest(), which reaches for a hardware-only
                # camera module (camera.common.auto) and crashes unconditionally
                # against SimBackend. Confirmed live: the model choosing this
                # perfectly reasonable action brought the whole mission down
                # with an unhandled ModuleNotFoundError — a bug in this
                # handler, not something the VLM did wrong.
                if action.target_object:
                    # labels_match: the model writes target_object as prose
                    # ("water tower", or "water tower on the right") not the
                    # exact snake_case detection label ("water_tower") — exact
                    # (even normalized) equality still silently misses
                    # qualified descriptions (confirmed live: repeated "Object
                    # not found" even with the target visible in CURRENT
                    # DETECTIONS) — substring containment after normalizing
                    # both sides catches these too.
                    match = next(
                        (d for d in detections
                         if labels_match(action.target_object, d.label) and d.world_xyz is not None),
                        None,
                    )
                    target_xyz = match.world_xyz if match is not None else None
                    if target_xyz is None:
                        landmark = self.memory.nearest(action.target_object)
                        if landmark is not None:
                            target_xyz = (landmark.x, landmark.y, landmark.z)
                    if target_xyz is not None:
                        self._report_progress(f"Navigating to {action.target_object}")
                        # backend.goto(north_m, east_m, alt_m) — world_xyz is
                        # (east, north, up), same swap as RETURN_TO_LANDMARK above.
                        backend.goto(target_xyz[1], target_xyz[0], target_xyz[2])
                    else:
                        self._history.append(f"Object not found: {action.target_object}")
                else:
                    self._report_progress("navigate_to_object requested with no target_object")

            elif action.action_type == ActionType.RETURN_TO_LANDMARK:
                if action.target_object:
                    landmark = self.memory.nearest(action.target_object)
                    if landmark is not None:
                        # Approach and STAND OFF, don't fly onto the exact landmark
                        # coordinate — a fixed-wing can't hover/stop there, so a
                        # goto() straight to the landmark flies the aircraft through
                        # and past it; by the next VLM tick the target is behind/out
                        # of the forward FOV again, and the model just calls
                        # return_to_landmark again forever (confirmed live: an 8B
                        # run looped this exact way for its entire action budget,
                        # never re-detecting the target after the first sighting).
                        # Stopping half a sense-range short keeps the landmark ahead
                        # of and within the forward camera's detection envelope.
                        pose = backend.get_pose()
                        target_x, target_y = landmark.x, landmark.y
                        if pose is not None:
                            cx, cy = pose[0], pose[1]
                            dx, dy = target_x - cx, target_y - cy
                            dist = math.hypot(dx, dy)
                            standoff = self.vehicle_class.sense_range_m * 0.5
                            if dist > standoff:
                                frac = (dist - standoff) / dist
                                target_x = cx + dx * frac
                                target_y = cy + dy * frac
                        self._report_progress(
                            f"Returning toward remembered {action.target_object} at "
                            f"({landmark.x:.0f}, {landmark.y:.0f}, {landmark.z:.0f}), "
                            f"standing off to keep it in view"
                        )
                        # backend.goto(north_m, east_m, alt_m) — note the argument
                        # ORDER is (north, east), while world-frame x/y here are
                        # (east, north) (see backends.world_from_range_bearing).
                        # Passing x/y positionally without swapping would feed an
                        # east-value into the north_m slot and vice versa, silently
                        # flying toward the mirror-image point — caught because
                        # Slice 1's fw_eval.py never actually exercised this call
                        # (it drives SimBackend.drive() directly, not goto()).
                        backend.goto(target_y, target_x, landmark.z)
                    else:
                        self._history.append(f"No memory of landmark: {action.target_object}")
                else:
                    self._report_progress("return_to_landmark requested with no target_object")

            elif action.action_type == ActionType.SEARCH_AREA:
                # Normalized so slightly different phrasing tick-to-tick
                # ("water tower" vs "the water tower") doesn't spuriously
                # look like a new target and restart the search plan.
                target = normalize_label(action.target_object) if action.target_object else "target"
                need_new_plan = (
                    self._search_target != target
                    or not self._search_plan
                    or self._search_idx >= len(self._search_plan)
                )
                if need_new_plan:
                    landmark = self.memory.nearest(target) if action.target_object else None
                    pose = backend.get_pose()
                    if landmark is not None:
                        center = (landmark.x, landmark.y)
                    elif pose is not None:
                        center = (pose[0], pose[1])
                    else:
                        center = (0.0, 0.0)
                    self._search_plan = search_patterns.expanding_orbit(center, self.vehicle_class)
                    self._search_idx = 0
                    self._search_target = target
                    self._report_progress(f"Starting expanding-orbit search for {target}")
                if self._search_idx < len(self._search_plan):
                    wx, wy = self._search_plan[self._search_idx]
                    self._search_idx += 1
                    pose = backend.get_pose()
                    alt = pose[2] if pose is not None else 50.0
                    self._report_progress(
                        f"Search leg {self._search_idx}/{len(self._search_plan)} for {target} "
                        f"toward ({wx:.0f}, {wy:.0f})"
                    )
                    # Same (north_m, east_m) argument order as RETURN_TO_LANDMARK above —
                    # wx/wy here are (east, north), so they swap into the call too.
                    backend.goto(wy, wx, alt)
                else:
                    self._history.append(f"Search pattern exhausted for {target}")

            elif action.action_type == ActionType.COUNT:
                # The actual counting is done HERE, by SpatialMemory, not by
                # the VLM's own arithmetic — action.target_object is only used
                # to select which memory bucket to count (see matching()'s
                # docstring for why this is authoritative: it's the same
                # geo-dedup that already backs RETURN_TO_LANDMARK/REPORT).
                target = action.target_object
                matches = self.memory.matching(target)
                n = len(matches)
                low_confidence = sum(1 for lm in matches if lm.hits == 1)
                locations = [(round(lm.x, 1), round(lm.y, 1), round(lm.z, 1)) for lm in matches]
                confidence_note = f", {low_confidence} seen only once" if low_confidence else ""
                moving_note = (
                    " (target may move between sightings — this reflects distinct "
                    "positions seen during the orbit, not a simultaneous snapshot)"
                    if normalize_label(target) in POSSIBLY_MOVING_LABELS else ""
                )
                msg = f"Counted {n} {target}{confidence_note}{moving_note}"
                self._report_progress(f"Count: {msg}")
                self._findings.append(msg)
                backend.log_event("count_reported", {
                    "target": target, "count": n,
                    "low_confidence": low_confidence, "locations": locations,
                })
                self._send_report(msg)
                count_recorded_this_phase = True

            elif action.action_type == ActionType.CAPTURE_PHOTO:
                self._report_progress("Capturing photo")
                if self.drone_sdk:
                    url = self.drone_sdk.capture_photo(upload=True)
                    if url:
                        self._photos.append(url)
                        self._history.append(f"Photo captured: {url}")
            
            elif action.action_type == ActionType.REPORT:
                if action.message:
                    self._report_progress(f"Finding: {action.message}")
                    self._findings.append(action.message)
                    self._send_report(action.message)
            
            elif action.action_type == ActionType.ASK_CLOUD:
                if action.parse_failed:
                    # The model produced nothing usable (confirmed live: a
                    # genuinely empty completion, even after decide() already
                    # retried once) — this must not be treated like a normal
                    # decision. Take a real, safe, bounded action instead of
                    # silently doing nothing while the aircraft continues on
                    # whatever it was last commanded to do.
                    consecutive_vlm_failures += 1
                    self._report_progress(
                        f"VLM produced no usable output ({consecutive_vlm_failures}/"
                        f"{self.MAX_CONSECUTIVE_VLM_FAILURES}) — loitering, not guessing"
                    )
                    backend.log_event("vlm_parse_failed", {
                        "consecutive": consecutive_vlm_failures, "message": action.message,
                    })
                    pose = backend.get_pose()
                    center = (pose[0], pose[1], pose[2]) if pose is not None else None
                    backend.loiter(center, self.vehicle_class.sense_range_m)
                    if consecutive_vlm_failures >= self.MAX_CONSECUTIVE_VLM_FAILURES:
                        return {
                            'failed': True,
                            'reason': f'VLM produced no usable output {consecutive_vlm_failures} times in a row',
                            'actions': phase_actions,
                        }
                else:
                    # A real cloud-advice request (not a parse failure) — the
                    # actual round trip is deferred (see the fixed-wing AI
                    # plan's Deferred section: no IoT TopicRule wired yet).
                    # Loiter rather than doing nothing while unimplemented.
                    self._report_progress(f"Asking cloud for help: {action.message}")
                    pose = backend.get_pose()
                    center = (pose[0], pose[1], pose[2]) if pose is not None else None
                    backend.loiter(center, self.vehicle_class.sense_range_m)

            if not (action.action_type == ActionType.ASK_CLOUD and action.parse_failed):
                consecutive_vlm_failures = 0

            # Small delay between actions
            time.sleep(0.2)
        
        # Phase didn't complete within action limit
        return {'failed': True, 'reason': 'Phase action limit reached', 'actions': phase_actions}
    
    def _get_drone_state(self) -> Dict[str, Any]:
        """Get current drone state for VLM context."""
        state = {}
        
        if self.drone_sdk:
            try:
                battery = self.drone_sdk.get_battery()
                state['battery'] = battery.get('remaining', -1)
            except:
                pass
            
            try:
                lat, lon, alt = self.drone_sdk.get_position()
                state['position'] = f"({lat:.6f}, {lon:.6f})"
                state['altitude'] = alt
            except:
                pass
        
        # Get pose from Nav2 if available
        nav = self._get_nav()
        if nav:
            pose = nav.get_pose()
            if pose:
                state['pose'] = f"({pose[0]:.2f}, {pose[1]:.2f}, {pose[2]:.2f})"
        
        return state
    
    def _get_depth_at_point(self, frame, x: int, y: int) -> Optional[float]:
        """Get depth at a pixel coordinate."""
        perception = self._get_perception()
        try:
            return perception.get_depth_at(x, y)
        except:
            return None
    
    def _send_report(self, message: str):
        """Send a report message to the user via MQTT."""
        if self.mqtt_client and self.conversation_id:
            try:
                from drone_sdk import DRONE_ID
                topic = f"drone/{DRONE_ID}/chat/{self.conversation_id}/response"
                payload = {
                    'type': 'report',
                    'message': message,
                    'timestamp': time.time(),
                }
                self.mqtt_client.publish(topic, json.dumps(payload))
            except:
                pass
    
    def _report_progress(self, message: str, phase: int = None, status: str = "in_progress"):
        """Report progress to the user."""
        print(f"[MISSION] {message}")
        
        if self.on_progress:
            self.on_progress(message)
        
        if self.mqtt_client and self.conversation_id:
            try:
                from drone_sdk import DRONE_ID
                from datetime import datetime, timezone
                import json
                
                # Publish to progress topic in proper format for iOS client
                topic = f"drone/{DRONE_ID}/chat/{self.conversation_id}/progress"
                
                payload = {
                    'droneId': DRONE_ID,
                    'conversation_id': self.conversation_id,
                    'message_type': 'mission_progress',
                    'mission_id': self._current_mission.mission_id if self._current_mission else 'unknown',
                    'phase': phase or (self._current_mission.current_phase + 1 if self._current_mission else 1),
                    'total_phases': len(self._current_mission.phases) if self._current_mission else 1,
                    'objective': message,
                    'status': status,
                    'text': message,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                }
                
                self.mqtt_client.publish(topic=topic, payload=json.dumps(payload))
            except Exception as e:
                print(f"Failed to publish progress: {e}")
    
    def _replan(self, phase: Dict[str, Any], reason: str, attempt: int) -> None:
        """Loiter and clear transient per-phase state before retrying a failed
        phase — the bounded alternative to aborting (see _run_impl). Cleared
        state (not memory itself, which must persist) so a retry starts a fresh
        SEARCH_AREA pattern rather than resuming a stale one from the failed
        attempt.
        """
        backend = self._get_backend()
        pose = backend.get_pose()
        center = (pose[0], pose[1], pose[2]) if pose is not None else None
        objective = phase.get('objective') or phase.get('type', 'unknown')
        self._report_progress(f"Replanning (attempt {attempt}/{self.MAX_REPLANS_PER_PHASE}) after: {reason}")
        backend.log_event("replan", {"reason": reason, "attempt": attempt, "phase": objective})
        backend.loiter(center, self.vehicle_class.sense_range_m)
        self._history.append(f"replan attempt {attempt}: {reason}")
        self._search_plan = []
        self._search_idx = 0
        self._search_target = None

    def _memory_finding_strings(self) -> List[str]:
        """Format persistent world-frame landmarks for MissionResult.findings —
        makes memory inspectable after a flight, not just usable mid-mission."""
        return [
            f"memory: {lm.label} at ({lm.x:.1f}, {lm.y:.1f}, {lm.z:.1f}) "
            f"score={lm.score:.2f} hits={lm.hits}"
            for lm in self.memory.all()
        ]

    def _cleanup(self):
        """Clean up resources."""
        if self._perception:
            self._perception.release()


# Evaluated once at import time: a typed phase this dispatcher accepts but
# mission_vocab doesn't describe (or vice versa) is a real drift bug — the
# cloud planner and on-device dispatcher disagreeing about the phase-type
# vocabulary is exactly the failure mode "change B" exists to catch, so this
# fails loudly at import rather than silently at mission-execution time.
_dispatch_keys = set(MissionLoop._PHASE_DISPATCH.keys())
_vocab_keys = set(mission_vocab.PHASE_SCHEMAS.keys())
assert _dispatch_keys == _vocab_keys, (
    f"reasoning_loop._PHASE_DISPATCH and mission_vocab.PHASE_SCHEMAS have "
    f"drifted — only in dispatch: {_dispatch_keys - _vocab_keys}, only in "
    f"vocab: {_vocab_keys - _dispatch_keys}"
)

# Same idea for the VLM action vocabulary, one direction only: every
# capability mission_vocab.VLM_CAPABILITIES describes to the cloud planner
# must be a real ActionType vlm.py can actually parse/dispatch (catches a
# renamed/typo'd action silently going undescribed-but-broken). Not a strict
# equality check — VLM_CAPABILITIES deliberately omits the terminal/control
# actions (phase_complete/mission_complete/mission_failed), which aren't
# "capabilities" a planner composes around.
if VLM_AVAILABLE:
    _capability_keys = set(mission_vocab.VLM_CAPABILITIES.keys())
    _action_values = {a.value for a in ActionType}
    assert _capability_keys <= _action_values, (
        f"mission_vocab.VLM_CAPABILITIES describes actions vlm.ActionType "
        f"doesn't have: {_capability_keys - _action_values}"
    )


def run_mission(mission: Mission, mqtt_client=None, conversation_id: str = None, drone_sdk=None) -> MissionResult:
    """
    Convenience function to run a mission.
    
    Args:
        mission: Mission to execute
        mqtt_client: Optional MQTT client
        conversation_id: Optional conversation ID
        drone_sdk: Optional drone SDK
    
    Returns:
        MissionResult
    """
    loop = MissionLoop(
        mqtt_client=mqtt_client,
        conversation_id=conversation_id,
        drone_sdk=drone_sdk,
    )
    return loop.run(mission)


def test_mission_loop():
    """Test the VLM-based mission loop."""
    print("Testing VLM Mission Loop...")
    
    if not VLM_AVAILABLE:
        print("VLM not available - skipping test")
        return
    
    # Create a simple mission
    mission = Mission(
        mission_id="test_001",
        phases=[
            {
                "objective": "Observe the surroundings",
                "success": "Report what you see",
                "evaluation_criteria": "Any observation is valid",
            },
            {
                "objective": "Take a photo of something interesting",
                "success": "Photo captured",
            },
        ],
        original_message="Look around and take a picture of something cool",
    )
    
    print(f"\nMission: {mission.original_message}")
    print(f"Phases: {len(mission.phases)}")
    
    # Run the loop
    loop = MissionLoop()
    loop.MAX_PHASE_ACTIONS = 3  # Quick test
    
    result = loop.run(mission)
    
    print(f"\n=== Result ===")
    print(f"Success: {result.success}")
    print(f"Summary: {result.summary}")
    print(f"Phases: {result.phases_completed}/{result.total_phases}")
    print(f"Actions taken: {result.actions_taken}")
    print(f"Duration: {result.duration_seconds:.1f}s")
    if result.findings:
        print(f"Findings: {result.findings}")
    if result.failure_reason:
        print(f"Failure reason: {result.failure_reason}")
    
    print("\nVLM mission loop test complete.")


if __name__ == "__main__":
    test_mission_loop()
