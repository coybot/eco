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
import re
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
from situation import ObstacleTracker, PeerTracker, payload_block

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

# Camera-driven avoidance manoeuvre (ActionType.AVOID). A moderate turn rate
# rather than the airframe's 0.6 rad/s limit: the point is a deliberate,
# readable break, and the handler flies it for a measured number of seconds and
# then levels off, so a bigger rate would only make the timing twitchier.
AVOID_YAW_RATE = 0.35    # rad/s
AVOID_CLIMB_M = 25.0     # metres of height to gain on an "over" decision


def _compass_name(yaw_rad: float) -> str:
    """Heading as a compass point plus degrees, in the sim's ENU convention
    (yaw 0 = east, increasing toward north)."""
    deg = math.degrees(yaw_rad) % 360.0
    pts = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
    return f"{pts[int(round(deg / 45.0)) % 8]} ({deg:.0f} deg)"


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


# Verbs that mean "the aircraft is expected to have SEEN something". Only
# phases using one of these are subject to the grounding check: those are the
# phases whose completion asserts a sighting, and a sighting is the only kind
# of claim a detection can back.
_SIGHTING_VERBS = (
    "find", "locate", "search", "identify", "confirm", "spot", "sight",
    "detect", "observe", "photograph", "count", "look for", "inspect",
)


def _phase_wants_sighting(phase: Dict[str, Any]) -> bool:
    """Does completing this phase assert that something was seen?

    A transit phase ("fly east to the search area") asserts a POSITION, which
    the pose answers and no camera can. Running the grounding check against it
    demanded that some detected label appear in wording that names no object,
    which nothing could satisfy: the model reported "drone has reached the
    northern search area near east=400, north=60" and was refused for not
    having seen anything, repeatedly, until the phase hit its action limit.
    """
    text = f"{phase.get('objective', '')} {phase.get('success', '')}".lower()
    # "search area" is a place, not an instruction to search. Every transit
    # objective in these plans names one ("fly east toward the search area"),
    # so a bare substring test classified every transit as a sighting phase —
    # the exact phases this function exists to exempt.
    text = re.sub(r"\bsearch (area|zone|region|sector)\b", "", text)
    return any(re.search(rf"\b{v}", text) for v in _SIGHTING_VERBS)


def _phase_wants_delivery(phase: Dict[str, Any]) -> bool:
    """Does this phase's own text ask for the payload to be released?

    Same gap as _phase_wants_count, found the same way — by running it. A
    delivery phase ("release the water bottle ... success: water bottle
    delivered near person in red jacket") concluded with the model repeating
    the PREVIOUS phase's finding, "person in red jacket located and confirmed".
    That claim is perfectly grounded — a person really was detected — so the
    grounding check passed it, the phase completed, and the aircraft flew home
    with the bottle still aboard while the run reported success.

    Grounding asks "is this claim backed by something you saw". It cannot ask
    "is this claim the thing this phase was for". A phase whose success is
    defined by an ACT rather than an observation needs the act to have
    happened, and that is a fact about the vehicle, not a judgement about the
    model: the payload either left the aircraft or it did not.
    """
    text = f"{phase.get('objective', '')} {phase.get('success', '')}".lower()
    wants = any(w in text for w in ("deliver", "drop", "release"))
    what = any(w in text for w in ("bottle", "payload", "package"))
    return wants and what


def _parse_local_coord(text: str):
    """Extract an explicit local-frame point from objective text, e.g.
    "east=400, north=0" -> (400.0, 0.0). Tolerant of spacing and of the
    "east_m"/"north_m" spellings. Returns (x_east, y_north) or None. Used to
    pin a SEARCH_AREA to a fixed centre instead of the drone's drifting pose."""
    if not text:
        return None
    m_e = re.search(r'east(?:_m)?\s*=\s*(-?\d+(?:\.\d+)?)', text, re.IGNORECASE)
    m_n = re.search(r'north(?:_m)?\s*=\s*(-?\d+(?:\.\d+)?)', text, re.IGNORECASE)
    if m_e and m_n:
        return (float(m_e.group(1)), float(m_n.group(1)))
    return None


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
    # Recorded clips (MP4 URLs) from start_recording/stop_recording phases.
    # Separate from `photos` so the daemon can publish `video_urls` alongside
    # `image_urls` — the field the sim daemon has always published and the
    # cloud now reads.
    videos: List[str] = field(default_factory=list)
    # Structured counterpart to the prose "memory: <label> at (...)" lines
    # _memory_finding_strings() already appends to `findings` — one dict per
    # distinct object seen this mission (see MissionLoop._landmarks_out).
    # Population is ENU-only here; the daemon (fw_gcs_daemon.landmarks_payload)
    # is what knows the mission's lat/lon datum and enriches this on the way out.
    landmarks: List[Dict[str, Any]] = field(default_factory=list)
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
            'videos': self.videos,
            'landmarks': self.landmarks,
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
    # Search legs flown per VLM decision. Waypoints are a turn radius apart so
    # the aircraft can carve them, which is far finer than decisions need to be.
    # detect() still runs after every leg and the run breaks off on a sighting,
    # so this changes how often the model THINKS, not how often it looks.
    SEARCH_LEGS_PER_DECISION = 6
    MAX_REPLANS_PER_PHASE = 2   # Bounded retries before a phase failure is a true abort
    MAX_CONSECUTIVE_VLM_FAILURES = 3  # Bounded before a run of unusable VLM output becomes a phase failure
    # Fraction of an in-progress SEARCH_AREA plan that must be flown before
    # (a) a COUNT of zero for that exact target is honored, or (b) a counting
    # phase is allowed to CONCLUDE (report/phase_complete/mission_complete)
    # off the back of a count at all — confirmed live this needed to be a
    # code-level gate, not just prompt guidance ("count once confident you've
    # covered enough" was NOT reliably followed). Two separate real failures
    # motivated this: (a) a run searched only 4 of 32 planned legs, found
    # nothing, and reported a confident zero for a target genuinely ~150m
    # away and never reached; (b) a DIFFERENT run searched the same 4 of 32
    # legs, found ONE real match, and immediately concluded the phase with
    # that partial tally against a ground truth of 5. The COUNT action
    # itself is never blocked by this either way — it's cheap, repeatable,
    # and accurate for whatever's in memory right now; this only withholds
    # trusting a count as either "definitely zero" or "the final tally"
    # until the search has covered enough ground to back that claim.
    MIN_SEARCH_COVERAGE_BEFORE_ZERO_COUNT = 0.5
    # Cap on per-mission landmark photo uploads (see _maybe_photo_landmark) — a
    # mission that stumbles into a field of near-identical objects shouldn't
    # fire off dozens of synchronous PUTs.
    MAX_LANDMARK_PHOTOS_PER_MISSION = 8

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
        self._videos: List[str] = []
        self._recorder = None
        self._home_yaw_rad: Optional[float] = None
        # Same shared-reference trick as _findings: every MissionResult
        # construction site passes landmarks=self._landmarks_out, and the
        # `finally` block below extends the SAME list right before returning —
        # so whichever result the caller already built comes back populated
        # without a second return path to keep in sync.
        self._landmarks_out: List[Dict[str, Any]] = []
        self._landmark_photos_taken: int = 0
        self._home_lat: Optional[float] = None
        self._home_lon: Optional[float] = None
        self._home_alt: Optional[float] = None

        # In-progress search_patterns waypoint plan for ActionType.SEARCH_AREA —
        # one leg is flown per VLM decision (matching every other action's "one
        # bounded movement per decision" shape), so the plan persists across ticks.
        self._search_plan: List[tuple] = []
        self._search_idx: int = 0
        self._search_target: Optional[str] = None

        # Perception/behaviour context for the prompt. These are per-instance,
        # so two aircraft running two MissionLoops on two threads keep entirely
        # separate pictures of the world — which is the point, since they are
        # meant to be reasoning independently with no link between them.
        self._last_detections: List[Any] = []
        self.peers = PeerTracker()
        self.obstacles = ObstacleTracker()
        self.payload_remaining: int = 0
        self.payload_capacity: int = 0
        # Feed the model the ground-truth OBSTACLES AHEAD block? Set False for
        # genuinely camera-driven avoidance — see _situation_blocks. Default True
        # so existing missions and recorded gate numbers are unchanged.
        self.obstacles_from_truth: bool = True
        # Optional hook: (frame_bytes, detections, action, wall_clock) for each
        # decision, so a harness can record exactly what the model saw.
        self.on_tick: Optional[Callable] = None

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
        self._videos = []
        self._recorder = None
        self._home_yaw_rad = None
        self._landmarks_out = []
        self._landmark_photos_taken = 0
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

        # Capture the heading we started at, ONCE, so body-relative phases
        # ("10m ahead and 5 to the right") resolve against a fixed reference.
        # Deliberately not the live heading at each phase: a rectangle whose
        # orientation depends on whichever way the aircraft happened to be
        # pointing when the phase began is unpredictable to the operator and
        # un-replannable after a failed leg.
        try:
            pose = self._get_backend().get_pose()
            if pose is not None and pose[3] is not None:
                self._home_yaw_rad = float(pose[3])
        except Exception:
            pass
        if self._home_yaw_rad is None:
            self._report_progress(
                "No heading available at start - treating 'ahead' as north "
                "for any body-relative phase"
            )
            self._home_yaw_rad = 0.0

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
                            videos=self._videos,
                            landmarks=self._landmarks_out,
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
                        videos=self._videos,
                        landmarks=self._landmarks_out,
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
                        videos=self._videos,
                        landmarks=self._landmarks_out,
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
                videos=self._videos,
                landmarks=self._landmarks_out,
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
                videos=self._videos,
                landmarks=self._landmarks_out,
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
            self._landmarks_out.extend(self._landmark_dicts())
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
        "fly_rect": "_exec_fly_rect",
        "look_around": "_exec_look_around",
        "capture_photo": "_exec_capture_photo",
        "start_recording": "_exec_start_recording",
        "stop_recording": "_exec_stop_recording",
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

    def _body_to_home_offset(self, forward_m: float, right_m: float) -> tuple:
        """Body-frame (forward, right) -> the (north_m, east_m) offset-from-home
        that backend.goto() already speaks.

        The rotation reference is the heading captured once at mission start
        (_home_yaw_rad), not the live heading — see _run_impl. Compass
        convention: yaw 0 = north, increasing clockwise, so +right is east at
        yaw 0. This is the same convention search_patterns._body_to_enu uses;
        the two must agree or a rectangle comes out mirrored.
        """
        yaw = self._home_yaw_rad or 0.0
        north_m = forward_m * math.cos(yaw) - right_m * math.sin(yaw)
        east_m = forward_m * math.sin(yaw) + right_m * math.cos(yaw)
        return north_m, east_m

    def _exec_fly_rect(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        forward_m = phase.get('forward_m', 10.0)
        right_m = phase.get('right_m', 10.0)
        origin_forward_m = phase.get('origin_forward_m', 0.0)
        origin_right_m = phase.get('origin_right_m', 0.0)
        alt_m = self._apply_clearance(phase.get('altitude_m', 5.0), phase)
        per_side = phase.get('waypoints_per_side', 1)

        heading_deg = math.degrees(self._home_yaw_rad or 0.0)
        # search_patterns.rectangle applies the same body->ENU rotation and the
        # same turn-radius treatment fly_circle's clamp does, so the geometry
        # lives in one place for both patterns.
        pts = search_patterns.rectangle(
            (forward_m, right_m),
            (origin_forward_m, origin_right_m),
            self.vehicle_class,
            heading_deg=heading_deg,
            waypoints_per_side=per_side,
        )
        if not self.vehicle_class.can_hover:
            min_side = 2.0 * search_patterns.turn_radius_m(self.vehicle_class)
            if min(abs(forward_m), abs(right_m)) < min_side:
                # Same class of failure _exec_fly_circle's radius clamp guards:
                # left unclamped this reads as a string of blocked waypoints
                # with the real cause invisible.
                self._report_progress(
                    f"Requested {forward_m}x{right_m}m rectangle has a side "
                    f"tighter than this vehicle can turn in ({min_side:.0f}m) "
                    f"- enlarging it to stay flyable"
                )

        self._report_progress(
            f"Flying rectangle: {forward_m}m ahead x {right_m}m right, "
            f"altitude={alt_m}m ({len(pts)} waypoints)"
        )
        backend = self._get_backend()
        for i, (east_m, north_m) in enumerate(pts):
            self._report_progress(f"Rectangle waypoint {i + 1}/{len(pts)}")
            try:
                result = backend.goto(north_m, east_m, alt_m)
            except Exception as e:
                return {'failed': True, 'reason': f'Waypoint {i + 1}: {e}', 'actions': i + 1}
            if not self._goto_ok(result):
                reason = getattr(result, 'message', 'blocked')
                return {'failed': True, 'reason': f'Waypoint {i + 1} blocked: {reason}', 'actions': i + 1}
        return {'success': True, 'actions': len(pts)}

    def _exec_start_recording(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        mode = phase.get('mode', 'video')
        fps = phase.get('fps', 6.0)
        interval_s = phase.get('interval_s', 3.0)
        max_seconds = phase.get('max_seconds', 120.0)

        if self._recorder is not None and self._recorder.is_recording:
            # Not an error: a planner that emitted two start_recording phases
            # still wants one recording, and failing the phase would abort a
            # flight over a planning slip.
            self._report_progress("Already recording - continuing the existing recording")
            return {'success': True, 'actions': 0}

        try:
            from media_recorder import MediaRecorder
        except ImportError as e:
            return {'failed': True, 'reason': f'media_recorder unavailable: {e}', 'actions': 0}

        self._report_progress(f"Starting {mode} recording")
        try:
            # backend.capture_frame, not the SDK camera directly: it is already
            # implemented for hardware (via PerceptionService, the shared owner
            # of the device) and for the sim, so one recorder covers every
            # vehicle and nothing has to arbitrate for the camera here.
            self._recorder = MediaRecorder(
                self._get_backend().capture_frame,
                mode=mode,
                fps=fps,
                interval_s=interval_s,
                max_seconds=max_seconds,
            )
            self._recorder.start()
        except Exception as e:
            self._recorder = None
            return {'failed': True, 'reason': f'could not start recording: {e}', 'actions': 0}
        return {'success': True, 'actions': 1}

    def _exec_stop_recording(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        urls = self._finish_recording()
        if not urls:
            # A recording that produced nothing is worth saying out loud, but
            # it must not fail the mission: the flight itself succeeded and
            # aborting here would throw away everything after this phase.
            self._report_progress("Recording produced no media")
            return {'success': True, 'actions': 1}
        self._report_progress(f"Recording finished: {len(urls)} file(s)")
        return {'success': True, 'actions': 1}

    def _finish_recording(self) -> List[str]:
        """Stop any live recording, upload it, and file the URLs.

        Split out of _exec_stop_recording because _cleanup() needs the same
        behaviour: a mission that aborts mid-pattern should still deliver the
        footage it already shot, which is often exactly what explains the
        abort. Safe to call when nothing is recording.
        """
        recorder = self._recorder
        self._recorder = None
        if recorder is None:
            return []

        try:
            artifacts = recorder.stop()
        except Exception as e:
            self._report_progress(f"WARNING: stopping the recording failed: {e}")
            return []
        if not artifacts:
            return []

        sdk = _drone_sdk if DRONE_SDK_AVAILABLE else self.drone_sdk
        if sdk is None:
            self._report_progress("WARNING: recorded media cannot be uploaded (no SDK)")
            return []
        if not hasattr(sdk, 'upload_video_bytes') or not hasattr(sdk, 'upload_media'):
            # The sim daemon injects a shim that duck-types only capture_photo/
            # look_around (see _exec_look_around). Say which capability is
            # missing rather than dying on a bare AttributeError.
            self._report_progress(
                "WARNING: this SDK cannot upload recorded media "
                "(no upload_media/upload_video_bytes) - discarding the recording"
            )
            return []

        conversation_id = self.conversation_id
        urls: List[str] = []
        for artifact in artifacts:
            try:
                if isinstance(artifact, (bytes, bytearray)):
                    url = sdk.upload_video_bytes(bytes(artifact), conversation_id)
                    if url:
                        self._videos.append(url)
                else:
                    url = sdk.upload_media(str(artifact), conversation_id)
                    if url:
                        self._photos.append(url)
                    try:
                        Path(artifact).unlink()
                    except OSError:
                        pass
                if url:
                    urls.append(url)
            except Exception as e:
                self._report_progress(f"WARNING: uploading recorded media failed: {e}")
        return urls

    def _exec_look_around(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        directions = phase.get('directions', 4)
        self._report_progress(f"Looking around ({directions} directions)")
        # Same hardware-first precedence as _get_backend(): the real module-level
        # _drone_sdk wins when it's actually importable (on-aircraft), otherwise
        # fall back to whatever was passed into the constructor — which is how
        # the sim daemon's GcsPhotoUploader shim (duck-typing capture_photo/
        # look_around) gets to answer typed photo phases at all.
        sdk = _drone_sdk if DRONE_SDK_AVAILABLE else self.drone_sdk
        if sdk is None:
            return {'failed': True, 'reason': 'drone_sdk not available', 'actions': 0}
        try:
            urls = sdk.look_around(directions=directions)
            if urls:
                self._photos.extend(urls)
            return {'success': True, 'actions': 1}
        except Exception as e:
            return {'failed': True, 'reason': str(e), 'actions': 1}

    def _exec_capture_photo(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        self._report_progress("Capturing photo")
        sdk = _drone_sdk if DRONE_SDK_AVAILABLE else self.drone_sdk
        if sdk is None:
            return {'failed': True, 'reason': 'drone_sdk not available', 'actions': 0}
        try:
            url = sdk.capture_photo(upload=True)
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

    def abort(self) -> None:
        """Ask the loop to stop at the next decision boundary.

        Without this a harness that runs out of time has no way to stop a
        mission: it joins with a timeout, gets a thread that is still flying,
        and scores the take anyway while the aircraft keeps moving. Observed —
        a drone credited with 6 decisions and a failed search had in fact been
        cut off mid-run, and its thread went on printing into a torn-down
        harness (OSError: Bad file descriptor).
        """
        self._aborted = True

    def _phase_action_budget(self) -> int:
        """How many decisions this phase may take before it is cut off.

        The budget exists to stop a phase looping forever. A planned search is
        not a loop: it is a finite list of legs, one action each, and cutting it
        off part-way means the aircraft reports "not found" having flown a
        fraction of the area it was told to cover. Measured — a 32-leg expanding
        search reached leg 12 of 32 before the phase died at the default 14, and
        an earlier take only passed its search beat because the target happened
        to lie in an early leg. That is a budget that silently depends on where
        the target is.

        Raising the global limit instead is worse, and was tried: at 40 the
        SEARCH could finish but the TRANSIT phase used the extra rope to wander
        to (55, 465) — 465 m north of a route due east — where at 14 it would
        have been cut off and replanned long before. Different phases fail in
        different directions, so the allowance follows the plan the phase is
        actually working through.
        """
        base = self.MAX_PHASE_ACTIONS
        # A caller can refuse the search allowance. A staged shoot is thirty
        # seconds of a specific situation, not a mission: granting it a 32-leg
        # search budget let one fly 1500 m away looking for something that was
        # 90 m behind it, with two cameras recording the whole excursion.
        if self._search_plan and not getattr(self, "hard_action_cap", False):
            # Enough to finish the planned legs, plus the same slack any phase
            # gets for the decisions around them (closing in, reporting).
            return max(base, len(self._search_plan) + base)
        return base

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

        while phase_actions < self._phase_action_budget():
            if getattr(self, "_aborted", False):
                return {'failed': True, 'reason': 'aborted by the harness',
                        'actions': phase_actions, 'aborted': True}
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
            self._last_detections = detections

            # Teammates are intercepted BEFORE the memory update below. Ordinary
            # memory accumulates distinct objects, which is right for things that
            # stay put and wrong for an aircraft: one teammate sighted along its
            # track would otherwise become a scattering of phantom landmarks, and
            # then get counted, searched for and reported as findings. It is
            # pinned instead, so exactly one always-current entry exists and
            # RETURN_TO_LANDMARK can still act on it.
            peers = [d for d in detections if d.label == "aircraft"]
            detections = [d for d in detections if d.label != "aircraft"]
            for d in peers:
                if d.world_xyz is None:
                    continue
                self.peers.observe(d.peer_id or "teammate",
                                   d.world_xyz[0], d.world_xyz[1], d.world_xyz[2])
                self.memory.pin("teammate", d.world_xyz[0], d.world_xyz[1], d.world_xyz[2],
                                d.score)

            for d in detections:
                if d.world_xyz is not None:
                    lm = self.memory.update(d.label, d.world_xyz[0], d.world_xyz[1], d.world_xyz[2], d.score)
                    if lm.hits == 1:
                        # First sighting of this landmark (not a re-merge into an
                        # existing one) — worth a telemetry event, not every update.
                        backend.log_event("memory_landmark", {
                            "label": lm.label, "x": lm.x, "y": lm.y, "z": lm.z, "score": lm.score,
                        })
                        self._maybe_photo_landmark(lm, frame)

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
                extra_context=self._situation_blocks(peers, detections),
            )

            phase_actions += 1

            # Give the caller the exact frame the model saw alongside what it
            # decided from it. Recorded footage otherwise shows a chase camera's
            # view, which is not what the model was looking at — and the demo's
            # whole claim is about what the onboard model actually saw.
            if self.on_tick is not None:
                try:
                    self.on_tick(frame, detections, action, time.time())
                except Exception as exc:
                    print(f"on_tick hook failed: {exc}")

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
                # A count having run at all isn't enough on its own either — a
                # real gap found live: the model found ONE person after just
                # 4 of 32 planned search legs, called count, then immediately
                # concluded the phase with that partial tally (ground truth
                # was 5). MIN_SEARCH_COVERAGE_BEFORE_ZERO_COUNT's own "a real
                # nonzero count is NOT gated" reasoning is about trusting that
                # something was genuinely found — it says nothing about
                # whether the TALLY is complete, which is what a counting
                # phase's objective ("count ALL X") actually promises. COUNT
                # itself stays unblocked either way (repeatable, cheap,
                # accurate for whatever's in memory right now); this only
                # withholds the CONCLUSION until the search has covered
                # enough ground for that tally to be trustworthy.
                search_underway = (
                    self._search_target is not None
                    and self._search_plan
                    and self._search_idx < len(self._search_plan)
                )
                insufficient_coverage = (
                    search_underway
                    and self._search_idx < len(self._search_plan) * self.MIN_SEARCH_COVERAGE_BEFORE_ZERO_COUNT
                )
                if _phase_wants_count(phase) and (not count_recorded_this_phase or insufficient_coverage):
                    if not count_recorded_this_phase:
                        reason = "no count action has run yet this phase"
                    else:
                        covered_pct = 100 * self._search_idx / len(self._search_plan)
                        reason = f"only {covered_pct:.0f}% of the planned search area covered so far — the tally may be incomplete"
                    self._report_progress(
                        f"Rejecting {action.action_type.value} for a counting phase — "
                        f"{reason} (\"{action.message}\")"
                    )
                    action = VLMAction(
                        action_type=ActionType.SEARCH_AREA,
                        target_object=action.target_object,
                        reasoning="Overridden: a counting phase must cover enough of the search area before concluding",
                    )
                elif (_phase_wants_delivery(phase)
                      and getattr(self, "payload_remaining", 0) > 0):
                    # The payload is demonstrably still aboard, so whatever this
                    # phase was, it was not completed. No judgement about the
                    # model is involved and no route is suggested: it is told
                    # the plain fact and decides what to do about it.
                    self._report_progress(
                        f"Rejecting {action.action_type.value} for a delivery phase — "
                        f"the payload has not been released (\"{action.message}\")")
                    self._history.append(
                        "Claimed this phase was complete while still carrying the "
                        "payload. The phase asks for the payload to be delivered; "
                        "releasing it is what completes it.")
                    continue
                elif action.action_type == ActionType.MISSION_FAILED:
                    pass  # not subject to the grounding check below (a failure
                          # claim doesn't assert a positive finding the way
                          # REPORT/PHASE_COMPLETE/MISSION_COMPLETE do)
                elif not _phase_wants_sighting(phase):
                    # Nothing to ground. This phase does not ask the aircraft to
                    # find or observe anything, so there is no sighting its
                    # completion could be checked against — the claim it makes
                    # is about where the aircraft is, which the pose answers.
                    #
                    # The grounding check demands that some DETECTED label
                    # appear in the phase's own wording, which a transit phase
                    # cannot satisfy: it names no object at all. Observed live,
                    # and caused by an earlier fix — taking the wall out of the
                    # tasking (so the on-device model has to find it rather than
                    # being told) also removed the last detectable noun from the
                    # transit objective. The model reported, correctly, "drone
                    # has reached the northern search area near east=400,
                    # north=60", and was refused for not having seen anything.
                    pass
                else:
                    objective_blob = normalize_label(
                        f"{phase.get('objective', '')} {phase.get('success', '')}"
                    )
                    known_labels = {normalize_label(d.label) for d in detections}
                    known_labels |= {normalize_label(lm.label) for lm in self.memory.all()}
                    # Seeing a teammate is never evidence for the objective. It
                    # is pinned in memory so the aircraft can fly to it, but a
                    # mission phrased around aircraft ("rendezvous with the other
                    # aircraft") would otherwise let the mere sight of one ground
                    # any claim of success.
                    known_labels -= self.NON_GROUNDING_LABELS
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
                    elif nav and self.backend is None:
                        # Nav2 path, for vehicles actually flown through it. NOT
                        # a fallback when a backend exists: against a fixed-wing
                        # this shim reports "navigation complete (simulated)"
                        # without moving anything, so an unresolvable pixel
                        # turned into a silent no-op while the aircraft carried
                        # straight on at cruise speed — observed live, flying
                        # into the wall it had just correctly identified while
                        # the envelope guard fought it 60 times in one run.
                        depth = self._get_depth_at_point(frame, action.point_x, action.point_y)
                        if depth is None:
                            depth = 3.0  # Default 3 meters if no depth
                        self._report_progress(f"Navigating to point ({action.point_x}, {action.point_y})")
                        result = nav.navigate_to_point(action.point_x, action.point_y, depth)
                        if result.status == NavigationStatus.FAILED:
                            self._history.append(f"Navigation failed: {result.message}")
                    else:
                        # Name the likely confusion rather than just the failure.
                        #
                        # Observed live: an aircraft whose objective was "the
                        # search area centred at east=400, north=0" issued
                        # navigate_to_point(400, 0) six times in one run. Those
                        # are the objective's WORLD coordinates dropped into the
                        # PIXEL slots — and pixel y=0 is the top row of the
                        # frame, i.e. sky, so it can never resolve. The old
                        # message ("use navigate_to_world with coordinates
                        # instead") was already correct and the model repeated
                        # the pick anyway, because it does not read as a
                        # description of the mistake actually made. Two actions
                        # take a pair of numbers; when the objective names a
                        # coordinate, choosing the wrong one puts the right
                        # numbers in the wrong units.
                        hint = ""
                        pair = f"{int(action.point_x)}, {int(action.point_y)}"
                        blob = f"{phase.get('objective', '')} {phase.get('success', '')}"
                        if re.search(rf"{int(action.point_x)}\D{{0,12}}{int(action.point_y)}", blob):
                            hint = (f" ({pair}) is the world coordinate from this phase's "
                                    f"objective, not a pixel. navigate_to_point takes a "
                                    f"pixel in the {CAM_VIEWPORT_W}x{CAM_VIEWPORT_H} image; "
                                    f"navigate_to_world takes world coordinates like that one.")
                        self._history.append(
                            f"Could not resolve point ({pair}) to a world position.{hint}")
                        self._report_progress(
                            f"Point ({pair}) does not resolve to anywhere on the ground")
                        # Hold off ONLY if the aircraft is actually coasting at
                        # something. A refused world leg means the obstacle is
                        # on the commanded line, so reversing is right; an
                        # unresolvable pixel says nothing about what is ahead.
                        #
                        # Reversing unconditionally here was measurably worse
                        # than doing nothing: 32 unresolvable picks in one take,
                        # each throwing away a perfectly good heading, and an
                        # aircraft that had flown the whole mission the take
                        # before never got past its transit. The wasted decision
                        # is only compounded when the coast leads somewhere bad.
                        if self._structure_ahead(backend):
                            self._hold_off(backend)
                else:
                    self._report_progress("Navigation requested but no point coordinates given")

            elif action.action_type == ActionType.NAVIGATE_TO_WORLD:
                # Most of the spatial information the model is given — memory,
                # obstacle ends, detection positions — is world coordinates, and
                # until this action existed there was no way to act on any of it
                # except by pointing at something already on camera. That is why
                # the aircraft could describe the wall correctly and still fly
                # into it: it could see the way around but could not ask to take it.
                if action.world_x is None or action.world_y is None:
                    self._report_progress("navigate_to_world given no coordinates")
                else:
                    pose = backend.get_pose()
                    alt = float(action.alt_m) if action.alt_m is not None else (
                        pose[2] if pose is not None else 35.0)
                    wx, wy = float(action.world_x), float(action.world_y)
                    # goto() is a heading hold: it flies STRAIGHT at whatever it
                    # is given. A destination on the far side of an obstacle
                    # therefore produces a leg straight through the obstacle —
                    # and since aircraft have no collision body in the sim, that
                    # is not even a visible crash, just a track passing through
                    # solid geometry, which is worse because it looks like
                    # success. The vehicle declines the leg and says why; where
                    # to go instead stays the model's decision, exactly like the
                    # delivery gates.
                    blocked = getattr(backend, "path_blocked", None)
                    refused = blocked is not None and blocked((wx, wy))
                    if refused:
                        msg = (f"Cannot fly directly to ({wx:.0f}, {wy:.0f}) — that "
                               f"straight line passes through structure. Route via a "
                               f"clear point to one side first, then continue.")
                        self._report_progress(msg)
                        self._history.append(msg)
                        backend.log_event("leg_refused", {
                            "reason": "path crosses structure",
                            "requested": [wx, wy]})
                        arrived = False
                        # Refusing is not enough on its own. Nothing can hold a
                        # fixed-wing still — it flies at minimum airspeed no
                        # matter what — so a refused leg that simply does
                        # nothing leaves the aircraft coasting toward the very
                        # obstacle it was just told it cannot cross, for the
                        # several seconds the next decision takes. It then
                        # arrives there regardless, and the envelope guard is
                        # left shoving at it. The vehicle turns away and holds
                        # instead, which is what an aircraft given an
                        # unflyable clearance actually does. Reversing course
                        # is deliberately the dumbest possible choice: it buys
                        # thinking room without expressing any opinion about
                        # which way around the obstacle to go.
                        self._hold_off(backend)
                    else:
                        self._report_progress(
                            f"Flying to world ({wx:.0f}, {wy:.0f}) at {alt:.0f} m")
                        arrived = backend.goto(wy, wx, alt, timeout_s=90.0, tol_m=15.0)
                    # Record WHERE it went, not just that it navigated. The
                    # generic history line carries only the action type and the
                    # reasoning, so an aircraft that had already reached a point
                    # saw no evidence of that and kept re-issuing the same leg —
                    # observed live, flying to the same coordinates over and over
                    # while the objective stayed unmet. Stating the destination
                    # and the resulting position makes the repetition visible to
                    # the model, which is the only thing that can break the loop.
                    if not refused:
                        now = backend.get_pose()
                        where = (f"now at ({now[0]:.0f}, {now[1]:.0f})" if now
                                 else "position unknown")
                        # Factual only. An earlier version appended "Do not fly
                        # here again; pick a different destination" and the
                        # model read it as a warning off navigate_to_world
                        # ITSELF, not off that one destination: it fell back to
                        # navigate_to_point at the dead centre of the frame and
                        # flew straight ahead for the rest of the mission,
                        # ending 3 km from the goal with the wall long behind
                        # it. Telling a model what NOT to do, in a line it
                        # re-reads every turn, is a good way to have it stop
                        # doing something you needed.
                        self._history.append(
                            f"{'Reached' if arrived else 'Did not reach'} world "
                            f"({wx:.0f}, {wy:.0f}); {where}.")
            
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
                if action.target_object:
                    target = normalize_label(action.target_object)
                elif self._search_target is not None:
                    # The VLM sometimes omits target_object on a SEARCH_AREA
                    # call even mid-search — confirmed live: falling back to
                    # a bare "target" here didn't match the real target label
                    # from prior turns, silently resetting the expanding-orbit
                    # search plan back to leg 0 every time it happened. That
                    # meant real search coverage never accumulated, letting
                    # the zero-count coverage gate below pass right after
                    # each reset (search_idx trivially small again). Stick
                    # with whatever we were already searching for instead.
                    target = self._search_target
                else:
                    target = "target"
                # Fuzzy, not exact: normalize_label alone doesn't catch the
                # model saying "the pickup truck" one decision and "pickup
                # truck" the next — normalize_label("the pickup truck") !=
                # normalize_label("pickup truck"), so an exact-equality check
                # here treated ordinary phrasing drift as a brand-new target
                # and threw away the entire in-progress expanding-orbit plan
                # (back to leg 0) on almost every decision. Confirmed live:
                # a search that should converge within its first ~6-leg lap
                # instead restarted repeatedly and never got there. labels_match
                # is the same permissive substring/equality test SpatialMemory
                # already uses for this exact class of phrasing variance.
                same_target = (self._search_target is not None
                              and labels_match(self._search_target, target))
                need_new_plan = (
                    not same_target
                    or not self._search_plan
                    or self._search_idx >= len(self._search_plan)
                )
                if need_new_plan:
                    landmark = self.memory.nearest(target) if action.target_object else None
                    pose = backend.get_pose()
                    # An explicit local coordinate in the phase objective
                    # ("east=400, north=0") pins the search to a FIXED point.
                    # Without this the centre defaults to the drone's current
                    # pose, and a fixed-wing (which cannot hover) then flies the
                    # orbit forward, re-centres on the advanced pose next replan,
                    # and drifts away indefinitely instead of covering the named
                    # area — observed live drifting to east>2500. A remembered
                    # sighting still wins (we've actually seen the thing); the
                    # objective coordinate beats a bare pose fallback.
                    obj_center = _parse_local_coord(phase.get("objective", ""))
                    if landmark is not None:
                        center = (landmark.x, landmark.y)
                    elif obj_center is not None:
                        center = obj_center
                    elif pose is not None:
                        center = (pose[0], pose[1])
                    else:
                        center = (0.0, 0.0)
                    self._search_plan = search_patterns.expanding_orbit(center, self.vehicle_class)
                    self._search_idx = 0
                    self._search_target = target
                    self._report_progress(f"Starting expanding-orbit search for {target}")
                if self._search_idx < len(self._search_plan):
                    # Fly a run of legs per decision, watching between each, and
                    # break off the moment the target appears.
                    #
                    # One leg per decision meant one full VLM inference for every
                    # 46 m of arc — the waypoints are spaced a turn radius apart
                    # so the aircraft can carve them, not because a decision is
                    # needed that often. Measured: ~7 s of thinking per 3.3 s of
                    # flying, 186 inferences in a single take, an hour per take.
                    # That is what makes take-farming impractical, and it buys
                    # nothing: the model cannot act on what it has not seen, and
                    # between legs it saw nothing, because a leg is a blocking
                    # goto() with no sensing inside it.
                    #
                    # Sensing cadence is UNCHANGED — detect() still runs after
                    # every leg, exactly as often as before. Only the thinking
                    # is batched, and it stops early on a sighting, so the model
                    # still gets the decision at the moment it matters. Matches
                    # ORBIT_POINT, which already flies a lap per decision for
                    # the same reason.
                    flown = 0
                    hit = None
                    while (self._search_idx < len(self._search_plan)
                           and flown < self.SEARCH_LEGS_PER_DECISION):
                        wx, wy = self._search_plan[self._search_idx]
                        self._search_idx += 1
                        flown += 1
                        pose = backend.get_pose()
                        alt = pose[2] if pose is not None else 50.0
                        self._report_progress(
                            f"Search leg {self._search_idx}/{len(self._search_plan)} for {target} "
                            f"toward ({wx:.0f}, {wy:.0f})"
                        )
                        # Same (north_m, east_m) argument order as RETURN_TO_LANDMARK
                        # above — wx/wy here are (east, north), so they swap into
                        # the call too.
                        # Arrival tolerance sized to the airframe, not the
                        # 5 m default. A fixed-wing with a ~23 m turn radius
                        # frequently CANNOT get within 5 m of a point: it
                        # overshoots and circles, and goto() then runs to its
                        # 60 s timeout on a leg that should take three seconds.
                        # Measured — a take spent 5 minutes on inference and
                        # twenty on flight, 28 s per decision, and truncated in
                        # the search phase. Passing a waypoint close enough to
                        # have searched it is the actual requirement; hitting it
                        # exactly is not. Half the turn radius (~21 m here, on
                        # a 41.7 m radius) rather than the whole of it: legs are
                        # spaced about one radius apart, so a full-radius
                        # tolerance would count the NEXT waypoint as already
                        # reached and the pattern would collapse.
                        backend.goto(wy, wx, alt, timeout_s=20.0,
                                     tol_m=max(15.0, 0.5 * search_patterns.turn_radius_m(
                                         self.vehicle_class)))
                        try:
                            for d in backend.detect():
                                if labels_match(getattr(d, "label", ""), target):
                                    hit = d
                                    break
                        except Exception:
                            pass
                        if hit is not None:
                            break
                    if hit is not None:
                        self._history.append(
                            f"Broke off the search at leg {self._search_idx} of "
                            f"{len(self._search_plan)}: {getattr(hit, 'label', 'something')} "
                            f"is in view now.")
                else:
                    self._history.append(f"Search pattern exhausted for {target}")

            elif action.action_type == ActionType.AVOID:
                # Camera-driven obstacle avoidance: a RELATIVE manoeuvre, held for
                # one decision interval.
                #
                # Every other spatial action here is expressed in world
                # coordinates, and those coordinates exist only because the sim
                # supplied them (detect() is a ground-truth oracle, and
                # navigate_to_point resolves a pixel with a sim raycast). So none
                # of them can support a claim that the aircraft avoided something
                # using its camera. This one can: it carries no coordinates, and
                # the model's only inputs for it are the image and the mission.
                #
                # Rate-based, because that is what the airframe takes and because
                # a turn rate is the honest expression of "break left" — the model
                # is not being asked where the far side of the building is, only
                # which way to go and whether it can climb.
                pose = backend.get_pose()
                direction = (action.direction or "").strip().lower()
                mag = float(action.magnitude_deg or 35.0)
                mag = max(10.0, min(90.0, mag))
                if direction not in ("left", "right", "over"):
                    self._history.append(
                        f"avoid ignored: direction {action.direction!r} is not "
                        f"left/right/over")
                else:
                    cruise = getattr(backend, "CRUISE_MS", 20.0)
                    # TIME-BOUNDED, and that is essential rather than tidy. The
                    # sim holds the last drive command until the next one, and a
                    # decision here takes ~11 s on the Orin's 2B model. A bare
                    # 0.23 rad/s turn rate left standing for 11 s is a 145 deg
                    # turn — the aircraft would spin past the gap it was aiming
                    # for and keep going. So the manoeuvre is flown for exactly
                    # as long as it needs and then levelled off, and the aircraft
                    # coasts straight while the model thinks about the next one.
                    if direction == "over":
                        climb_rate = 5.0
                        secs = min(5.0, AVOID_CLIMB_M / climb_rate)
                        alt_now = f"{pose[2]:.0f} m" if pose is not None else "?"
                        self._report_progress(
                            f"Avoiding: climbing over it (from {alt_now}, "
                            f"+{AVOID_CLIMB_M:.0f} m)")
                        backend.drive(airspeed=cruise, yaw_rate=0.0,
                                      climb=climb_rate)
                    else:
                        # Positive yaw_rate is a LEFT (counter-clockwise) turn in
                        # this sim's ENU convention — see fixedwing_manager's bank
                        # sign note — so "left" is positive.
                        rate = AVOID_YAW_RATE * (1.0 if direction == "left" else -1.0)
                        secs = min(6.0, math.radians(mag) / AVOID_YAW_RATE)
                        self._report_progress(
                            f"Avoiding: breaking {direction} {mag:.0f} deg")
                        backend.drive(airspeed=cruise, yaw_rate=rate, climb=0.0)
                    time.sleep(secs)
                    # Level off. Without this the turn or climb stands until the
                    # next decision and the manoeuvre becomes unbounded again.
                    backend.drive(airspeed=cruise, yaw_rate=0.0, climb=0.0)
                    self._history.append(
                        f"avoid: {direction}"
                        + ("" if direction == "over" else f" {mag:.0f} deg")
                        + f" ({secs:.1f} s)")

            elif action.action_type == ActionType.ORBIT_POINT:
                # Circle a point and keep watching it. One lap per decision, so
                # the model re-evaluates every lap and can break off the moment
                # something changes, rather than committing to a fixed wait.
                pose = backend.get_pose()
                center = self._orbit_center(action, pose)
                if center is None:
                    self._history.append(
                        "orbit_point ignored: no world point given and none in memory")
                else:
                    radius = float(action.radius_m or 60.0)
                    alt = float(action.alt_m) if action.alt_m is not None else (
                        pose[2] if pose is not None else 30.0)
                    self._report_progress(
                        f"Orbiting ({center[0]:.0f}, {center[1]:.0f}) at r={radius:.0f} m, "
                        f"{alt:.0f} m, watching for {action.target_object or 'activity'}")
                    # Hold the sensor on the orbit centre for the whole lap.
                    # Without this the aircraft circles with its camera aimed
                    # along the tangent and never actually sees the thing it is
                    # circling — the reason sensor pointing exists at all.
                    self._aim_sensor_at(center)
                    try:
                        lap = search_patterns.orbit(center, radius, self.vehicle_class, laps=1)
                        for wx, wy in lap:
                            backend.goto(wy, wx, alt, timeout_s=25.0, tol_m=12.0)
                            # Re-aim each leg: the offset is relative to the
                            # airframe's nose, which swings right round a lap.
                            self._aim_sensor_at(center)
                    finally:
                        # Always recentre. A sensor left cocked would silently
                        # point every later detection and unprojection the wrong
                        # way, long after the orbit ended.
                        self._aim_sensor_at(None)
                    backend.log_event("orbit_lap", {
                        "center": [center[0], center[1]],
                        "radius_m": radius, "alt_m": alt,
                        "watching_for": action.target_object,
                    })

            elif action.action_type == ActionType.DROP_PAYLOAD:
                self._exec_drop_payload(action, backend)

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
        # Telemetry must never be able to end a flight. A print raised
        # OSError: Bad file descriptor mid-mission on a long background run —
        # stdout had gone from under it — and because nothing here caught it,
        # the exception unwound through the phase, the mission, and the entire
        # four-take batch. Losing a log line is acceptable; losing the run is
        # not, and the aircraft is still flying either way.
        try:
            print(f"[MISSION] {message}", flush=True)
        except Exception:
            pass

        if self.on_progress:
            try:
                self.on_progress(message)
            except Exception:
                pass
        
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
    
    # ------------------------------------------------------------------
    # ORBIT_POINT / DROP_PAYLOAD support
    # ------------------------------------------------------------------
    # A release is only authorised while the target is actually in view and
    # this close (see _exec_drop_payload for why the gates are mechanical).
    DROP_MAX_RANGE_M = 60.0
    DROP_RUN_IN_ALT_M = 15.0
    # How long the terminal run-in may take before giving up with the payload
    # still aboard. A run from DROP_MAX_RANGE_M to the release point is ~3 s of
    # flying; this is generous enough for a target that is walking away.
    DROP_RUN_IN_TIMEOUT_S = 45.0

    # Labels that can never ground a claim of mission success — they say
    # something about the formation, not about the objective.
    NON_GROUNDING_LABELS = {"aircraft", "teammate"}

    def _situation_blocks(self, peers, detections) -> Dict[str, str]:
        """The prompt blocks that a single frame cannot convey.

        All three are computed by deterministic geometry over sensor history —
        they describe the situation, they do not choose a response to it. What
        to do about a teammate circling low, or a wall across the route, stays
        entirely with the model.
        """
        blocks: Dict[str, str] = {}
        # State the frame geometry explicitly. Without it the model has no idea
        # what coordinate space navigate_to_point lives in: observed live,
        # it repeatedly picked pixel (500, 500) out of a 640x480 frame, which
        # cannot resolve to anywhere on the ground and silently wasted a
        # decision every time while the aircraft kept flying.
        blocks["CAMERA"] = (
            f"- The image is {CAM_VIEWPORT_W} wide by {CAM_VIEWPORT_H} tall. "
            f"Pixel coordinates must fall inside that, with y measured downward "
            f"from the top; the ground is in the lower half.")
        pose = None
        try:
            pose = self._get_backend().get_pose()
        except Exception:
            pass
        if pose is None:
            return blocks
        own = (pose[0], pose[1], pose[2])

        # Where the aircraft actually is, in the same frame as everything else
        # it is given.
        #
        # This was missing entirely, and the pose was already being fetched here
        # for the other blocks. Every spatial thing the model reads is in world
        # coordinates — memory landmarks, obstacle ends, navigate_to_world,
        # phase objectives and success criteria written by the planner — and it
        # was never told its own. Observed live: a transit phase whose success
        # read "aircraft is in the vicinity of ... east=400, north=60" could not
        # be judged at all, so the model simply kept navigating; fifteen
        # decisions, not one of them a completion claim, until the phase hit its
        # action limit. It was not refusing to finish, it had no way to know it
        # had arrived.
        blocks["POSITION"] = (
            f"- You are at east {pose[0]:.0f}, north {pose[1]:.0f}, altitude "
            f"{pose[2]:.0f} m, heading {_compass_name(pose[3])}. These are the "
            f"same world coordinates used by navigate_to_world, by the landmarks "
            f"in memory, and by any coordinates in the mission phase above.")

        peer_text = self.peers.summarize(own)
        if peer_text:
            blocks["PEER OBSERVATIONS (teammates you have SEEN — there is no radio link)"] = (
                peer_text)

        # OBSTACLES AHEAD is ground truth, and that is the whole reason it can be
        # switched off here.
        #
        # ObstacleTracker is fed from backend.detect(), which in the sim is an
        # oracle over the scene graph — exact distance, exact span, exact roof
        # height. A model handed that block is not avoiding obstacles from its
        # camera, it is reading coordinates off a list while a JPEG happens to be
        # attached. So "camera avoidance" mode withholds the block: the image and
        # the mission are then genuinely the only things describing what is in the
        # way, and ActionType.AVOID is the only action that can act on it.
        #
        # Default stays ON. Every mission and gate number recorded to date was
        # measured with this block present, and silently removing it would
        # invalidate all of them.
        if self.obstacles_from_truth:
            obstacle_text = self.obstacles.summarize(own, pose[3], detections)
            if obstacle_text:
                blocks["OBSTACLES AHEAD"] = obstacle_text
        else:
            # Withholding the block is not enough on its own. Observed live: with
            # no OBSTACLES AHEAD present the model still answered
            # navigate_to_world for a building filling its camera — and its
            # reasoning was correct ("looking directly into the side of a
            # building, which blocks forward progress"), so it saw the obstacle
            # and simply reached for the wrong action. There are a dozen action
            # types and the prompt talks up navigate_to_world, including for
            # "the end of an obstacle listed in OBSTACLES AHEAD" — advice that
            # makes no sense when there is no such list. So say plainly which
            # action applies and why the coordinate-based one cannot.
            blocks["OBSTACLE POLICY (CAMERA ONLY)"] = (
                "- You have NO obstacle list this mission. What is in your way is "
                "whatever you can SEE in the image.\n"
                "- If something solid is close and ahead of you, you MUST reply "
                "with the `avoid` action and a `direction` of \"left\", \"right\" "
                "or \"over\". Do NOT use navigate_to_world or navigate_to_point to "
                "get around it: you have no coordinates for it and no pixel can "
                "name the far side of a building.\n"
                "- Choose \"over\" if you can see the TOP EDGE of it with sky "
                "above; otherwise choose whichever side shows more open sky.")

        if self.payload_capacity:
            blocks["PAYLOAD"] = payload_block(self.payload_remaining, self.payload_capacity)
        return blocks

    def _hold_off(self, backend, seconds: float = 5.0) -> None:
        """Turn away and hold, after a leg the vehicle refused to fly.

        Buys the next decision some room. Deliberately a plain reversal rather
        than a turn toward any particular side: it must not smuggle in a choice
        about how to get around the obstacle, which is the model's to make.
        """
        pose = backend.get_pose()
        if pose is None:
            return
        rate = 0.6   # rad/s, the airframe limit — a half-turn takes ~5 s
        t_end = time.monotonic() + seconds
        while time.monotonic() < t_end:
            backend.drive(14.0, rate, 0.0)
            time.sleep(0.1)
        backend.log_event("hold_off", {"reason": "refused leg", "seconds": seconds})

    def _orbit_center(self, action, pose):
        """Where to orbit: the model's world point, else a remembered landmark.

        Falling back to memory matters because the point worth watching is
        usually somewhere the target was last seen — which is exactly the
        moment it is no longer visible to read coordinates off.
        """
        if action.world_x is not None and action.world_y is not None:
            return (float(action.world_x), float(action.world_y))
        if action.target_object:
            origin = (pose[0], pose[1], pose[2]) if pose else (0.0, 0.0, 0.0)
            lm = self.memory.nearest(action.target_object, origin)
            if lm is not None:
                return (lm.x, lm.y)
        return None

    def _aim_sensor_at(self, center) -> None:
        """Point the sensor at an ENU point, or recentre it when given None.

        Optional capability: backends without a steerable sensor simply do not
        implement it, and everything else still works — the aircraft just can't
        watch a point it isn't flying at.
        """
        backend = self._get_backend()
        fn = getattr(backend, "aim_sensor", None)
        if fn is None:
            return
        try:
            fn(center)
        except Exception as exc:
            self._report_progress(f"sensor aim failed: {exc}")

    def _exec_drop_payload(self, action, backend) -> None:
        """Release the payload, but only on a target confirmed right now.

        The gates below are deliberately mechanical, and they are not the
        model second-guessing itself: they are the difference between "the
        model believes it is over the target" and "the aircraft can currently
        see the target". A payload cannot be recovered once released, and a
        delivery to the wrong person is the single most damaging thing this
        mission can do, so a decision made several seconds and a hundred metres
        ago is not sufficient authority to let go of it.
        """
        target = action.target_object or "target"
        if getattr(self, "payload_remaining", 0) <= 0:
            self._history.append("drop_payload refused: nothing left to release")
            self._report_progress("Cannot deliver — payload already released")
            return

        # Gate 1: the target must be visible in THIS perception pass.
        fresh = [d for d in (self._last_detections or [])
                 if labels_match(d.label, target) or labels_match(target, d.label)]
        if not fresh:
            self._history.append(
                f"drop_payload refused: {target} not visible in the current frame")
            self._report_progress(
                f"Holding payload — {target} is not in view right now")
            return

        # Gate 2: close enough that a release can plausibly land near it.
        pose = backend.get_pose()
        best = None
        if pose is not None:
            for d in fresh:
                if d.world_xyz:
                    dist = math.hypot(d.world_xyz[0] - pose[0], d.world_xyz[1] - pose[1])
                    if best is None or dist < best[0]:
                        best = (dist, d)
        if best is None:
            self._history.append("drop_payload refused: no world position for the target")
            return
        dist, det = best
        if dist > self.DROP_MAX_RANGE_M:
            self._report_progress(
                f"Too far to deliver ({dist:.0f} m) — closing on {target} first")
            backend.goto(det.world_xyz[1], det.world_xyz[0],
                         self.DROP_RUN_IN_ALT_M, timeout_s=40.0, tol_m=15.0)
            return

        release = getattr(backend, "drop_payload", None)
        if release is None:
            self._history.append("drop_payload unsupported by this backend")
            return

        # Terminal run-in: fly at the target until the release point, then let go.
        #
        # Without this the payload was released the instant the target came
        # within DROP_MAX_RANGE_M, which is 60 m — while a bottle dropped at
        # 14 m/s from 15 m only carries v*sqrt(2h/g), about 24 m. Measured: a
        # release at 47 m put the bottle 71 m from the person, against a 10 m
        # assertion. The gate was letting go at ranges from which no release
        # could possibly land near the target, so the delivery beat could only
        # ever have succeeded by accident.
        #
        # This is deterministic weapons-style geometry, not the model flying:
        # the model decides WHETHER and at WHOM, this decides WHEN to let go,
        # exactly as labelled in the plan. The target is re-checked at 2 Hz all
        # the way in, so if it disappears — into the tunnel, say — the run is
        # abandoned with the payload still aboard.
        det = self._run_in_to_release(backend, target, det)
        if det is None:
            return

        result = release()
        if result:
            self.payload_remaining = max(0, self.payload_remaining - 1)
            # Record WHAT it was aimed at, at the moment of release. A delivery
            # is accurate if the bottle lands near the person it was aimed at;
            # this target walks to a drop zone after the tunnel, so scoring the
            # bottle against where he ended up measured the person's walk, not
            # the aircraft's aim — 106 m reported for a release that happened
            # 24 m from him.
            try:
                backend.log_event("delivery_aim", {
                    "target_xy": [det.world_xyz[0], det.world_xyz[1]],
                    "label": target})
            except Exception:
                pass
            pose = backend.get_pose()
            final = (math.hypot(det.world_xyz[0] - pose[0], det.world_xyz[1] - pose[1])
                     if pose is not None else float("nan"))
            self._report_progress(
                f"Payload released for {target} at {final:.0f} m")
            self._history.append(f"Released payload for {target}")
        else:
            self._history.append("drop_payload: release refused by the vehicle")

    def _structure_ahead(self, backend, look_m: float = 60.0) -> bool:
        """Is there structure on the aircraft's current heading?

        Asks the vehicle's own path check about a point straight ahead, so this
        agrees by construction with whatever the envelope guard would do. False
        whenever the backend cannot answer — a vehicle with no obstacle model
        should not have flight decisions made on its behalf by a guess.
        """
        blocked = getattr(backend, "path_blocked", None)
        if blocked is None:
            return False
        pose = backend.get_pose()
        if pose is None:
            return False
        try:
            return bool(blocked((pose[0] + math.cos(pose[3]) * look_m,
                                 pose[1] + math.sin(pose[3]) * look_m), pose[2]))
        except Exception:
            return False

    def _release_range_m(self, pose) -> float:
        """Horizontal distance a payload carries when let go: v*sqrt(2h/g)."""
        alt = max(1.0, pose[2] if pose is not None else self.DROP_RUN_IN_ALT_M)
        speed = getattr(self.vehicle_class, "cruise_speed_mps", 14.0) or 14.0
        return speed * math.sqrt(2.0 * alt / 9.81)

    def _run_in_to_release(self, backend, target, det):
        """Close to the ballistic release point, keeping eyes on the target.

        Returns the latest detection to release on, or None if the run was
        abandoned (target lost, or it could not be closed in time).
        """
        deadline = time.monotonic() + self.DROP_RUN_IN_TIMEOUT_S
        while time.monotonic() < deadline:
            pose = backend.get_pose()
            if pose is None:
                return None
            dist = math.hypot(det.world_xyz[0] - pose[0], det.world_xyz[1] - pose[1])
            want = self._release_range_m(pose)
            if dist <= want:
                return det
            # Steer at the target, descending to the release altitude. goto()
            # would fly all the way to it and overshoot the release point.
            backend.goto(det.world_xyz[1], det.world_xyz[0], self.DROP_RUN_IN_ALT_M,
                         timeout_s=1.0, tol_m=max(5.0, want))
            time.sleep(0.5)
            seen = [d for d in backend.detect()
                    if (labels_match(d.label, target) or labels_match(target, d.label))
                    and d.world_xyz]
            if not seen:
                self._history.append(
                    f"Broke off the delivery run: lost sight of {target} on the way in, "
                    f"still carrying the payload.")
                self._report_progress(f"Aborted run-in — {target} no longer visible")
                return None
            pose = backend.get_pose()
            det = min(seen, key=lambda d: math.hypot(d.world_xyz[0] - pose[0],
                                                     d.world_xyz[1] - pose[1]))
        self._history.append(
            f"Delivery run-in timed out before reaching release range for {target}.")
        return None

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

    def _maybe_photo_landmark(self, lm, frame) -> None:
        """Capture one photo of a landmark the instant it's first seen (called
        from the perception block only when lm.hits == 1 — never on a re-merge,
        so each distinct object gets at most one shot).

        `self.drone_sdk` here is the daemon's photo shim (fw_gcs_daemon.
        GcsPhotoUploader) when the sim wires one in — it duck-types capture_photo/
        look_around (used by the typed phase executors below) plus this extra
        upload_frame() method. Real on-aircraft `drone_sdk` has no such method,
        so getattr(..., 'upload_frame', None) is None there and this is a
        guaranteed no-op on hardware — no config flag needed to keep the two
        environments apart.
        """
        if frame is None or self._landmark_photos_taken >= self.MAX_LANDMARK_PHOTOS_PER_MISSION:
            return
        upload = getattr(self.drone_sdk, "upload_frame", None)
        if upload is None:
            return
        try:
            url = upload(frame, f"landmark_{normalize_label(lm.label)}")
        except Exception as e:
            print(f"landmark photo upload failed: {e}")
            return
        if url:
            lm.image_url = url
            self._landmark_photos_taken += 1

    # Structured sibling of _memory_finding_strings() above, for
    # MissionResult.landmarks — everything an operator-facing map/query needs
    # per distinct object, in ENU (this class doesn't know a lat/lon datum;
    # fw_gcs_daemon.landmarks_payload adds lat/lon and applies the cap/sort
    # once it has one). "teammate" is excluded: it's a pinned peer-aircraft
    # position, not a sighted object, and would otherwise show up on a map as
    # a bogus ground target.
    def _landmark_dicts(self) -> List[Dict[str, Any]]:
        return [
            {
                "label": lm.label,
                "east_m": round(lm.x, 1),
                "north_m": round(lm.y, 1),
                "alt_m": round(lm.z, 1),
                "score": round(lm.score, 3),
                "hits": lm.hits,
                "image_url": lm.image_url,
            }
            for lm in self.memory.all()
            if normalize_label(lm.label) != "teammate"
        ]

    def _cleanup(self):
        """Clean up resources."""
        # Before anything else: a mission that aborted mid-pattern still has a
        # recorder thread running and footage buffered. That footage is often
        # what explains the abort, so deliver it rather than dropping it — and
        # stop the thread regardless, since it outlives the phase that started
        # it and is bounded only by its own max_seconds.
        try:
            self._finish_recording()
        except Exception as e:
            self._report_progress(f"WARNING: recorder cleanup failed: {e}")
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
