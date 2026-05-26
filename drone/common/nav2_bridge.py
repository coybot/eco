"""
Nav2 Bridge - Connect VLM decisions to ROS 2 Nav2 navigation stack.

This module translates VLM actions (like "navigate to point [x,y] on image")
into Nav2 goals that the drone can execute with obstacle avoidance.

Integrates with:
- Nav2 for path planning and obstacle avoidance
- cuVSLAM for localization and mapping
- Isaac ROS for perception
"""

import time
import json
import math
import threading
from typing import Optional, Dict, Any, Tuple, Callable
from dataclasses import dataclass
from enum import Enum

# Try to import ROS 2 - gracefully handle if not available
ROS_AVAILABLE = False
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.action import ActionClient
    from geometry_msgs.msg import PoseStamped, Point, Quaternion
    from nav2_msgs.action import NavigateToPose
    from std_msgs.msg import Header
    from tf2_ros import Buffer, TransformListener
    ROS_AVAILABLE = True
except ImportError:
    print("ROS 2 not available - Nav2 bridge will operate in simulation mode")


class NavigationStatus(str, Enum):
    """Status of a navigation goal."""
    IDLE = "idle"
    NAVIGATING = "navigating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class NavigationGoal:
    """A navigation goal for the drone."""
    # Target position in map frame (meters)
    x: float
    y: float
    z: float  # Altitude
    
    # Target orientation (yaw in radians)
    yaw: Optional[float] = None
    
    # Source information
    source: str = "vlm"  # "vlm", "user", "mission"
    
    # Original pixel coordinates (if from VLM)
    pixel_x: Optional[int] = None
    pixel_y: Optional[int] = None


@dataclass
class NavigationResult:
    """Result of a navigation attempt."""
    status: NavigationStatus
    message: str
    final_position: Optional[Tuple[float, float, float]] = None
    distance_traveled: float = 0.0
    time_elapsed: float = 0.0


class DepthProjector:
    """
    Project 2D pixel coordinates to 3D world coordinates using depth.
    
    This is the core of SPF-style navigation: VLM points to where to go
    on the image, and we convert that to a 3D waypoint.
    """
    
    def __init__(
        self,
        camera_fx: float = 600.0,  # Focal length x
        camera_fy: float = 600.0,  # Focal length y
        camera_cx: float = 320.0,  # Principal point x
        camera_cy: float = 240.0,  # Principal point y
        image_width: int = 640,
        image_height: int = 480,
    ):
        """
        Initialize depth projector with camera intrinsics.
        
        Args:
            camera_fx, camera_fy: Focal lengths in pixels
            camera_cx, camera_cy: Principal point (image center)
            image_width, image_height: Image dimensions
        """
        self.fx = camera_fx
        self.fy = camera_fy
        self.cx = camera_cx
        self.cy = camera_cy
        self.width = image_width
        self.height = image_height
    
    def pixel_to_3d(
        self,
        pixel_x: int,
        pixel_y: int,
        depth_m: float,
    ) -> Tuple[float, float, float]:
        """
        Convert pixel coordinate + depth to 3D point in camera frame.
        
        Args:
            pixel_x, pixel_y: Pixel coordinates
            depth_m: Depth in meters
        
        Returns:
            (x, y, z) in camera frame (x=right, y=down, z=forward)
        """
        # Convert to normalized image coordinates
        x_norm = (pixel_x - self.cx) / self.fx
        y_norm = (pixel_y - self.cy) / self.fy
        
        # Project to 3D
        z = depth_m
        x = x_norm * depth_m
        y = y_norm * depth_m
        
        return (x, y, z)
    
    def camera_to_body(
        self,
        cam_x: float,
        cam_y: float,
        cam_z: float,
    ) -> Tuple[float, float, float]:
        """
        Convert camera frame to body frame.
        
        Assumes camera is forward-facing:
        - Camera: x=right, y=down, z=forward
        - Body: x=forward, y=left, z=up
        
        Returns:
            (x, y, z) in body frame
        """
        body_x = cam_z   # Camera forward -> Body forward
        body_y = -cam_x  # Camera right -> Body left (negated)
        body_z = -cam_y  # Camera down -> Body up (negated)
        
        return (body_x, body_y, body_z)
    
    def pixel_to_waypoint(
        self,
        pixel_x: int,
        pixel_y: int,
        depth_m: float,
        current_pose: Tuple[float, float, float, float] = None,  # x, y, z, yaw
    ) -> Tuple[float, float, float]:
        """
        Convert pixel + depth to world waypoint.
        
        Args:
            pixel_x, pixel_y: Pixel coordinates from VLM
            depth_m: Depth at that pixel
            current_pose: Current drone pose (x, y, z, yaw) in world frame
        
        Returns:
            (x, y, z) waypoint in world frame
        """
        # Pixel to 3D in camera frame
        cam_point = self.pixel_to_3d(pixel_x, pixel_y, depth_m)
        
        # Camera to body frame
        body_point = self.camera_to_body(*cam_point)
        
        if current_pose is None:
            # No pose provided - return body-relative
            return body_point
        
        # Transform to world frame
        x, y, z, yaw = current_pose
        
        # Rotate body point by current yaw
        cos_yaw = math.cos(yaw)
        sin_yaw = math.sin(yaw)
        
        world_dx = body_point[0] * cos_yaw - body_point[1] * sin_yaw
        world_dy = body_point[0] * sin_yaw + body_point[1] * cos_yaw
        world_dz = body_point[2]
        
        # Add to current position
        world_x = x + world_dx
        world_y = y + world_dy
        world_z = z + world_dz
        
        return (world_x, world_y, world_z)


class Nav2Bridge:
    """
    Bridge between VLM decisions and Nav2 navigation.
    
    Handles:
    - Converting VLM point actions to Nav2 goals
    - Sending goals to Nav2 action server
    - Monitoring navigation progress
    - Reporting status back
    """
    
    def __init__(
        self,
        on_status_change: Callable[[NavigationStatus, str], None] = None,
        use_ros: bool = True,
    ):
        """
        Initialize Nav2 bridge.
        
        Args:
            on_status_change: Callback for status updates
            use_ros: Whether to use ROS 2 (False for simulation/testing)
        """
        self.on_status_change = on_status_change
        self.use_ros = use_ros and ROS_AVAILABLE
        
        self._status = NavigationStatus.IDLE
        self._current_goal: Optional[NavigationGoal] = None
        self._depth_projector = DepthProjector()
        
        # ROS 2 components
        self._node = None
        self._nav_client = None
        self._tf_buffer = None
        self._tf_listener = None
        
        # Current pose (updated from cuVSLAM or TF)
        self._current_pose: Optional[Tuple[float, float, float, float]] = None
        self._pose_lock = threading.Lock()
        
        if self.use_ros:
            self._init_ros()
    
    def _init_ros(self):
        """Initialize ROS 2 node and action client."""
        try:
            if not rclpy.ok():
                rclpy.init()
            
            self._node = rclpy.create_node('nav2_bridge')
            
            # Nav2 action client
            self._nav_client = ActionClient(
                self._node,
                NavigateToPose,
                'navigate_to_pose'
            )
            
            # TF for pose
            self._tf_buffer = Buffer()
            self._tf_listener = TransformListener(self._tf_buffer, self._node)
            
            print("Nav2 bridge initialized with ROS 2")
            
        except Exception as e:
            print(f"Failed to initialize ROS 2: {e}")
            self.use_ros = False
    
    def set_camera_intrinsics(
        self,
        fx: float,
        fy: float,
        cx: float,
        cy: float,
        width: int,
        height: int,
    ):
        """Set camera intrinsics for depth projection."""
        self._depth_projector = DepthProjector(fx, fy, cx, cy, width, height)
    
    def update_pose(self, x: float, y: float, z: float, yaw: float):
        """Update current pose (call this from cuVSLAM or TF callback)."""
        with self._pose_lock:
            self._current_pose = (x, y, z, yaw)
    
    def get_pose(self) -> Optional[Tuple[float, float, float, float]]:
        """Get current pose."""
        with self._pose_lock:
            return self._current_pose
    
    def navigate_to_point(
        self,
        pixel_x: int,
        pixel_y: int,
        depth_m: float,
        altitude: float = None,
    ) -> NavigationResult:
        """
        Navigate to a point specified by pixel coordinates.
        
        This is the main interface for VLM-based navigation (SPF-style).
        
        Args:
            pixel_x, pixel_y: Pixel coordinates from VLM
            depth_m: Depth at that pixel (from depth sensor)
            altitude: Override altitude (if None, maintains current)
        
        Returns:
            NavigationResult
        """
        # Get current pose
        current_pose = self.get_pose()
        if current_pose is None:
            # Use origin if no pose available
            current_pose = (0, 0, 1.5, 0)  # Default hover position
        
        # Project pixel to world coordinates
        world_x, world_y, world_z = self._depth_projector.pixel_to_waypoint(
            pixel_x, pixel_y, depth_m, current_pose
        )
        
        # Override altitude if specified
        if altitude is not None:
            world_z = altitude
        
        # Create goal
        goal = NavigationGoal(
            x=world_x,
            y=world_y,
            z=world_z,
            pixel_x=pixel_x,
            pixel_y=pixel_y,
            source="vlm",
        )
        
        return self.navigate_to_goal(goal)
    
    def navigate_to_position(
        self,
        x: float,
        y: float,
        z: float,
        yaw: float = None,
    ) -> NavigationResult:
        """
        Navigate to an absolute position in world frame.
        
        Args:
            x, y, z: Target position in meters
            yaw: Target orientation in radians (optional)
        
        Returns:
            NavigationResult
        """
        goal = NavigationGoal(x=x, y=y, z=z, yaw=yaw, source="direct")
        return self.navigate_to_goal(goal)
    
    def navigate_to_offset(
        self,
        north_m: float,
        east_m: float,
        alt_m: float,
    ) -> NavigationResult:
        """
        Navigate to a position offset from home in meters (North/East/Up).

        Home is the Nav2 map origin (0, 0). ROS ENU convention: x=east, y=north, z=up.
        """
        goal = NavigationGoal(x=east_m, y=north_m, z=alt_m, source="offset")
        return self.navigate_to_goal(goal)

    def navigate_to_gps(
        self,
        lat: float,
        lon: float,
        alt_m: float,
        home_lat: float,
        home_lon: float,
    ) -> NavigationResult:
        """
        Navigate to GPS coordinates by converting to local map-frame offset from home.

        Uses a flat-earth approximation valid for distances up to ~a few km.
        """
        north_m = (lat - home_lat) * 111320.0
        east_m = (lon - home_lon) * 111320.0 * math.cos(math.radians(home_lat))
        return self.navigate_to_offset(north_m, east_m, alt_m)

    def navigate_to_goal(self, goal: NavigationGoal) -> NavigationResult:
        """
        Navigate to a NavigationGoal.
        
        Args:
            goal: Navigation goal
        
        Returns:
            NavigationResult
        """
        self._current_goal = goal
        self._set_status(NavigationStatus.NAVIGATING, f"Navigating to ({goal.x:.2f}, {goal.y:.2f}, {goal.z:.2f})")
        
        start_time = time.time()
        start_pose = self.get_pose()
        
        if self.use_ros:
            result = self._navigate_ros(goal)
        else:
            result = self._navigate_simulated(goal)
        
        # Calculate distance traveled
        end_pose = self.get_pose()
        distance = 0.0
        if start_pose and end_pose:
            dx = end_pose[0] - start_pose[0]
            dy = end_pose[1] - start_pose[1]
            dz = end_pose[2] - start_pose[2]
            distance = math.sqrt(dx*dx + dy*dy + dz*dz)
        
        result.distance_traveled = distance
        result.time_elapsed = time.time() - start_time
        
        return result
    
    def _navigate_ros(self, goal: NavigationGoal) -> NavigationResult:
        """Navigate using ROS 2 Nav2."""
        if not self._nav_client.wait_for_server(timeout_sec=5.0):
            self._set_status(NavigationStatus.FAILED, "Nav2 server not available")
            return NavigationResult(
                status=NavigationStatus.FAILED,
                message="Nav2 action server not available"
            )
        
        # Create Nav2 goal message
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = PoseStamped()
        goal_msg.pose.header = Header()
        goal_msg.pose.header.frame_id = "map"
        goal_msg.pose.header.stamp = self._node.get_clock().now().to_msg()
        goal_msg.pose.pose.position = Point(x=goal.x, y=goal.y, z=goal.z)
        
        # Set orientation (yaw only for drone)
        if goal.yaw is not None:
            # Convert yaw to quaternion
            goal_msg.pose.pose.orientation = Quaternion(
                x=0.0,
                y=0.0,
                z=math.sin(goal.yaw / 2),
                w=math.cos(goal.yaw / 2),
            )
        else:
            goal_msg.pose.pose.orientation = Quaternion(x=0.0, y=0.0, z=0.0, w=1.0)
        
        # Send goal
        send_goal_future = self._nav_client.send_goal_async(goal_msg)
        rclpy.spin_until_future_complete(self._node, send_goal_future, timeout_sec=5.0)
        
        goal_handle = send_goal_future.result()
        if not goal_handle.accepted:
            self._set_status(NavigationStatus.FAILED, "Goal rejected by Nav2")
            return NavigationResult(
                status=NavigationStatus.FAILED,
                message="Navigation goal rejected"
            )
        
        # Wait for result
        result_future = goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self._node, result_future, timeout_sec=120.0)
        
        result = result_future.result()
        if result.result:
            self._set_status(NavigationStatus.SUCCEEDED, "Navigation complete")
            return NavigationResult(
                status=NavigationStatus.SUCCEEDED,
                message="Reached destination",
                final_position=(goal.x, goal.y, goal.z),
            )
        else:
            self._set_status(NavigationStatus.FAILED, "Navigation failed")
            return NavigationResult(
                status=NavigationStatus.FAILED,
                message="Failed to reach destination"
            )
    
    def _navigate_simulated(self, goal: NavigationGoal) -> NavigationResult:
        """Simulated navigation for testing without ROS."""
        print(f"[SIM] Navigating to ({goal.x:.2f}, {goal.y:.2f}, {goal.z:.2f})")
        
        # Simulate navigation time based on distance
        current_pose = self.get_pose() or (0, 0, 1.5, 0)
        dx = goal.x - current_pose[0]
        dy = goal.y - current_pose[1]
        dz = goal.z - current_pose[2]
        distance = math.sqrt(dx*dx + dy*dy + dz*dz)
        
        # Assume 1 m/s speed
        nav_time = distance / 1.0
        time.sleep(min(nav_time, 5.0))  # Cap at 5 seconds for testing
        
        # Update pose to destination
        yaw = goal.yaw if goal.yaw is not None else current_pose[3]
        self.update_pose(goal.x, goal.y, goal.z, yaw)
        
        self._set_status(NavigationStatus.SUCCEEDED, "Navigation complete (simulated)")
        return NavigationResult(
            status=NavigationStatus.SUCCEEDED,
            message="Reached destination (simulated)",
            final_position=(goal.x, goal.y, goal.z),
        )
    
    def cancel_navigation(self) -> bool:
        """Cancel current navigation goal."""
        if self._status != NavigationStatus.NAVIGATING:
            return False
        
        if self.use_ros and self._nav_client:
            # Cancel in Nav2
            pass  # TODO: Implement cancel
        
        self._set_status(NavigationStatus.CANCELLED, "Navigation cancelled")
        return True
    
    def _set_status(self, status: NavigationStatus, message: str):
        """Update status and notify callback."""
        self._status = status
        print(f"[NAV] {status.value}: {message}")
        
        if self.on_status_change:
            self.on_status_change(status, message)
    
    def get_status(self) -> NavigationStatus:
        """Get current navigation status."""
        return self._status
    
    def is_navigating(self) -> bool:
        """Check if currently navigating."""
        return self._status == NavigationStatus.NAVIGATING
    
    def shutdown(self):
        """Shutdown the bridge."""
        if self._node:
            self._node.destroy_node()


# Singleton instance
_nav_bridge = None

def get_nav_bridge() -> Nav2Bridge:
    """Get or create the Nav2 bridge singleton."""
    global _nav_bridge
    if _nav_bridge is None:
        _nav_bridge = Nav2Bridge()
    return _nav_bridge


# Test function
def test_nav2_bridge():
    """Test the Nav2 bridge."""
    print("Testing Nav2 Bridge...")
    
    bridge = Nav2Bridge(use_ros=False)  # Use simulated mode
    
    # Set initial pose
    bridge.update_pose(0, 0, 1.5, 0)
    
    # Test point navigation (simulating VLM output)
    print("\nTest 1: Navigate to pixel point")
    result = bridge.navigate_to_point(
        pixel_x=400,  # Right of center
        pixel_y=240,  # Center height
        depth_m=3.0,  # 3 meters away
    )
    print(f"Result: {result.status.value} - {result.message}")
    print(f"Distance: {result.distance_traveled:.2f}m, Time: {result.time_elapsed:.2f}s")
    
    # Test direct position navigation
    print("\nTest 2: Navigate to position")
    result = bridge.navigate_to_position(x=5.0, y=2.0, z=2.0)
    print(f"Result: {result.status.value} - {result.message}")
    
    print("\nNav2 bridge test complete")


if __name__ == "__main__":
    test_nav2_bridge()
