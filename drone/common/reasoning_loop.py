"""
Mission loop - VLM-based perception-reasoning-action for autonomous drones.

1. Receives a Mission from the cloud (Claude)
2. Continuously: perceives (VLM) -> decides -> acts (Nav2) -> repeat
3. Can ask cloud for help when stuck
4. Reports progress and completion back to the user

Runs on Orin Nano, NX, and AGX (variant-specific VLM chosen at install).
"""

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
    
    def __init__(
        self,
        mqtt_client=None,
        conversation_id: str = None,
        on_progress: Callable[[str], None] = None,
        drone_sdk=None,
    ):
        """
        Initialize mission loop.
        
        Args:
            mqtt_client: MQTT client for cloud communication
            conversation_id: Current conversation ID
            on_progress: Callback for progress updates
            drone_sdk: Drone SDK for camera/telemetry (optional)
        """
        self.mqtt_client = mqtt_client
        self.conversation_id = conversation_id
        self.on_progress = on_progress
        self.drone_sdk = drone_sdk
        
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
                
                # Execute phase
                phase_result = self._execute_phase(phase, mission)
                actions_taken += phase_result.get('actions', 0)
                
                if phase_result.get('failed'):
                    return MissionResult(
                        success=False,
                        summary=f"Failed at phase {phase_num}",
                        phases_completed=mission.current_phase,
                        total_phases=len(mission.phases),
                        findings=self._findings,
                        photos=self._photos,
                        duration_seconds=time.time() - mission.start_time,
                        actions_taken=actions_taken,
                        failure_reason=phase_result.get('reason', 'Unknown'),
                    )
                
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
            self._cleanup()
    
    def _execute_phase(self, phase: Dict[str, Any], mission: Mission) -> Dict[str, Any]:
        """Dispatch a phase to the appropriate executor based on its type field."""
        phase_type = phase.get('type')
        if phase_type == 'arm_and_takeoff':
            return self._exec_arm_and_takeoff(phase)
        elif phase_type == 'nav':
            return self._exec_nav(phase)
        elif phase_type == 'go_to_gps':
            return self._exec_go_to_gps(phase)
        elif phase_type == 'fly_circle':
            return self._exec_fly_circle(phase)
        elif phase_type == 'look_around':
            return self._exec_look_around(phase)
        elif phase_type == 'capture_photo':
            return self._exec_capture_photo(phase)
        elif phase_type == 'return_home':
            return self._exec_return_home(phase)
        elif phase_type == 'land':
            return self._exec_land(phase)
        else:
            return self._exec_vlm_phase(phase, mission)

    # ------------------------------------------------------------------ #
    # Typed phase executors — all navigation goes through Nav2             #
    # ------------------------------------------------------------------ #

    def _exec_arm_and_takeoff(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        alt = phase.get('altitude_m', 5.0)
        self._report_progress(f"Arming and taking off to {alt}m")
        if not DRONE_SDK_AVAILABLE:
            return {'failed': True, 'reason': 'drone_sdk not available', 'actions': 0}
        try:
            _drone_sdk.arm()
            _drone_sdk.takeoff(alt)
            return {'success': True, 'actions': 1}
        except Exception as e:
            return {'failed': True, 'reason': str(e), 'actions': 1}

    def _exec_nav(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        north_m = phase.get('north_m', 0.0)
        east_m = phase.get('east_m', 0.0)
        alt_m = phase.get('alt_m', 5.0)
        desc = phase.get('description', f'N={north_m}m E={east_m}m')
        self._report_progress(f"Navigating: {desc}")
        nav = self._get_nav()
        if nav is None:
            return {'failed': True, 'reason': 'Nav2 not available', 'actions': 0}
        result = nav.navigate_to_offset(north_m, east_m, alt_m)
        if result.status == NavigationStatus.FAILED:
            return {'failed': True, 'reason': result.message, 'actions': 1}
        return {'success': True, 'actions': 1}

    def _exec_go_to_gps(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        lat = phase.get('lat')
        lon = phase.get('lon')
        alt_m = phase.get('alt_m', 15.0)
        desc = phase.get('description', f'{lat},{lon}')
        self._report_progress(f"Flying to GPS: {desc}")
        nav = self._get_nav()
        if nav is None:
            return {'failed': True, 'reason': 'Nav2 not available', 'actions': 0}
        if lat is None or lon is None:
            return {'failed': True, 'reason': 'Missing GPS coordinates in phase', 'actions': 0}
        if self._home_lat is None:
            return {'failed': True, 'reason': 'Home position unknown — GPS fix required', 'actions': 0}
        result = nav.navigate_to_gps(lat, lon, alt_m, self._home_lat, self._home_lon)
        if result.status == NavigationStatus.FAILED:
            return {'failed': True, 'reason': result.message, 'actions': 1}
        return {'success': True, 'actions': 1}

    def _exec_fly_circle(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        radius_m = phase.get('radius_m', 10.0)
        alt_m = phase.get('altitude_m', 5.0)
        n_waypoints = phase.get('waypoints', 8)
        self._report_progress(f"Flying circle: radius={radius_m}m altitude={alt_m}m")
        nav = self._get_nav()
        if nav is None:
            return {'failed': True, 'reason': 'Nav2 not available', 'actions': 0}
        for i in range(n_waypoints):
            angle = (2 * math.pi * i) / n_waypoints
            north_m = radius_m * math.cos(angle)
            east_m = radius_m * math.sin(angle)
            self._report_progress(f"Circle waypoint {i + 1}/{n_waypoints}")
            result = nav.navigate_to_offset(north_m, east_m, alt_m)
            if result.status == NavigationStatus.FAILED:
                return {'failed': True, 'reason': f'Waypoint {i + 1} blocked: {result.message}', 'actions': i + 1}
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
        nav = self._get_nav()
        if nav is None:
            return {'failed': True, 'reason': 'Nav2 not available', 'actions': 0}
        result = nav.navigate_to_offset(0.0, 0.0, alt_m)
        if result.status == NavigationStatus.FAILED:
            return {'failed': True, 'reason': result.message, 'actions': 1}
        return {'success': True, 'actions': 1}

    def _exec_land(self, phase: Dict[str, Any]) -> Dict[str, Any]:
        self._report_progress("Landing")
        if not DRONE_SDK_AVAILABLE:
            return {'failed': True, 'reason': 'drone_sdk not available', 'actions': 0}
        try:
            _drone_sdk.land()
            return {'success': True, 'actions': 1}
        except Exception as e:
            return {'failed': True, 'reason': str(e), 'actions': 1}

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

        phase_actions = 0

        while phase_actions < self.MAX_PHASE_ACTIONS:
            # 1. Capture current frame
            try:
                frame = perception.get_current_frame()
                if frame is None:
                    # Try to get from drone SDK
                    if self.drone_sdk:
                        frame = self.drone_sdk.capture_frame()
            except Exception as e:
                print(f"Failed to capture frame: {e}")
                frame = None
            
            if frame is None:
                self._report_progress("Warning: No camera frame available")
                time.sleep(1)
                phase_actions += 1
                continue
            
            # 2. Get drone state
            drone_state = self._get_drone_state()
            
            # 3. Ask VLM what to do
            action = vlm.decide(
                image=frame,
                mission_phase=phase,
                drone_state=drone_state,
                history=self._history[-10:],  # Last 10 actions
            )
            
            phase_actions += 1
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
                if nav and action.point_x is not None and action.point_y is not None:
                    # Get depth at point
                    depth = self._get_depth_at_point(frame, action.point_x, action.point_y)
                    if depth is None:
                        depth = 3.0  # Default 3 meters if no depth
                    
                    self._report_progress(f"Navigating to point ({action.point_x}, {action.point_y})")
                    result = nav.navigate_to_point(action.point_x, action.point_y, depth)
                    
                    if result.status == NavigationStatus.FAILED:
                        self._history.append(f"Navigation failed: {result.message}")
                else:
                    self._report_progress("Navigation requested but Nav2 not available")
            
            elif action.action_type == ActionType.NAVIGATE_TO_OBJECT:
                if nav and action.target_object:
                    # Find object position using perception
                    detection = perception.find_nearest(action.target_object)
                    if detection:
                        self._report_progress(f"Navigating to {action.target_object}")
                        # Use detection position
                        nav.navigate_to_position(
                            detection.x, detection.y, detection.z
                        )
                    else:
                        self._history.append(f"Object not found: {action.target_object}")
            
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
                self._report_progress(f"Asking cloud for help: {action.message}")
                # TODO: Implement cloud advice request
            
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
    
    def _cleanup(self):
        """Clean up resources."""
        if self._perception:
            self._perception.release()


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
