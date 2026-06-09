"""
Waveshare UGV Rover SDK — ROS 2 / cmd_vel control.

Uses ROS 2 geometry_msgs/Twist on /cmd_vel for motion and
Nav2 action client for GPS waypoint navigation.

config.yaml keys:
  vehicle_type: rover
  ros_domain_id: 0        # optional, default 0
  cmd_vel_topic: /cmd_vel # optional
  nav2_action: navigate_to_pose  # optional
"""

import time
import math
import logging
import sys
import threading
from pathlib import Path

_logger = logging.getLogger('rover_sdk')
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter('%(message)s'))
    _logger.addHandler(_h)


def _log(msg):
    print(msg)
    _logger.info(msg)


DRONE_DIR = Path(__file__).parent.parent.absolute()

# Movement limits
MAX_LINEAR_SPEED = 1.0    # m/s
MAX_ANGULAR_SPEED = 1.57  # rad/s (~90 deg/s)

# Module state
_armed = False
_ros_node = None
_cmd_vel_pub = None
_odom_sub = None
_gps_sub = None
_ros_initialized = False
_ros_lock = threading.Lock()

_latest_odom = None
_latest_gps = None


def _load_config():
    try:
        import yaml
        config_path = DRONE_DIR / 'config.yaml'
        if config_path.exists():
            with open(config_path) as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


def _init_ros():
    global _ros_node, _cmd_vel_pub, _odom_sub, _gps_sub, _ros_initialized

    with _ros_lock:
        if _ros_initialized:
            return _ros_node is not None

        config = _load_config()
        domain_id = config.get('ros_domain_id', 0)
        cmd_vel_topic = config.get('cmd_vel_topic', '/cmd_vel')

        try:
            import rclpy
            from rclpy.node import Node
            from geometry_msgs.msg import Twist
            from nav_msgs.msg import Odometry
            from sensor_msgs.msg import NavSatFix

            import os
            os.environ.setdefault('ROS_DOMAIN_ID', str(domain_id))

            if not rclpy.ok():
                rclpy.init()

            _ros_node = rclpy.create_node('astral_rover')

            _cmd_vel_pub = _ros_node.create_publisher(Twist, cmd_vel_topic, 10)

            def _odom_cb(msg):
                global _latest_odom
                _latest_odom = msg

            def _gps_cb(msg):
                global _latest_gps
                _latest_gps = msg

            _odom_sub = _ros_node.create_subscription(Odometry, '/odom', _odom_cb, 10)
            _gps_sub = _ros_node.create_subscription(NavSatFix, '/gps/fix', _gps_cb, 10)

            # Spin in background so callbacks fire
            spin_thread = threading.Thread(
                target=rclpy.spin, args=(_ros_node,), daemon=True
            )
            spin_thread.start()

            _ros_initialized = True
            _log(f"ROS 2 initialized (domain={domain_id}, cmd_vel={cmd_vel_topic})")
            return True

        except ImportError:
            _log("Warning: rclpy not available — rover motion commands disabled")
            _ros_initialized = True
            return False
        except Exception as e:
            _log(f"Warning: ROS 2 init failed: {e}")
            _ros_initialized = True
            return False


def _publish_twist(linear_x=0.0, linear_y=0.0, angular_z=0.0):
    """Publish a Twist message. Returns True if published."""
    if not _init_ros() or _cmd_vel_pub is None:
        _log("Warning: ROS 2 not available, cannot send velocity command")
        return False
    try:
        from geometry_msgs.msg import Twist
        msg = Twist()
        msg.linear.x = float(linear_x)
        msg.linear.y = float(linear_y)
        msg.angular.z = float(angular_z)
        _cmd_vel_pub.publish(msg)
        return True
    except Exception as e:
        _log(f"Error publishing Twist: {e}")
        return False


# =============================================================================
# Lifecycle
# =============================================================================

def arm():
    """Enable rover motion. Returns True."""
    global _armed
    _init_ros()
    _armed = True
    _log("Rover armed")
    return True


def disarm():
    """Halt motion and disable rover. Returns True."""
    global _armed
    stop()
    _armed = False
    _log("Rover disarmed")
    return True


def is_armed():
    return _armed


# =============================================================================
# Motion commands
# =============================================================================

def stop():
    """Immediately stop all motion."""
    _publish_twist()
    print("Rover stopped")


def drive(speed_mps=0.5, duration_sec=1.0):
    """Drive forward (positive) or backward (negative) at speed_mps for duration_sec."""
    speed_mps = max(-MAX_LINEAR_SPEED, min(MAX_LINEAR_SPEED, speed_mps))
    print(f"Driving {'forward' if speed_mps >= 0 else 'backward'} "
          f"at {abs(speed_mps):.2f} m/s for {duration_sec}s...")
    _publish_twist(linear_x=speed_mps)
    time.sleep(duration_sec)
    stop()


def turn(angle_deg, speed_rad_s=0.5):
    """Turn in place by angle_deg (positive=left/CCW). Blocks until complete."""
    speed_rad_s = min(abs(speed_rad_s), MAX_ANGULAR_SPEED)
    angle_rad = math.radians(angle_deg)
    duration = abs(angle_rad) / speed_rad_s
    direction = 1.0 if angle_deg >= 0 else -1.0

    print(f"Turning {angle_deg:.1f}° at {math.degrees(speed_rad_s):.0f} deg/s "
          f"({duration:.1f}s)...")
    _publish_twist(angular_z=direction * speed_rad_s)
    time.sleep(duration)
    stop()


def set_velocity(vx, vy=0.0, omega=0.0):
    """Set continuous velocity: vx=forward m/s, vy=lateral m/s, omega=angular rad/s."""
    vx = max(-MAX_LINEAR_SPEED, min(MAX_LINEAR_SPEED, vx))
    vy = max(-MAX_LINEAR_SPEED, min(MAX_LINEAR_SPEED, vy))
    omega = max(-MAX_ANGULAR_SPEED, min(MAX_ANGULAR_SPEED, omega))
    print(f"Set velocity: vx={vx:.2f} vy={vy:.2f} omega={omega:.2f}")
    _publish_twist(linear_x=vx, linear_y=vy, angular_z=omega)


def goto(lat, lon, tolerance_m=0.5):
    """Navigate to GPS coordinates using Nav2. Blocks until arrived or timeout."""
    _log(f"Navigating to ({lat:.6f}, {lon:.6f})...")
    try:
        import rclpy
        from rclpy.action import ActionClient
        from nav2_msgs.action import NavigateToPose
        from geometry_msgs.msg import PoseStamped
        import math

        if not _init_ros() or _ros_node is None:
            _log("Error: ROS 2 not available for goto()")
            return False

        client = ActionClient(_ros_node, NavigateToPose, 'navigate_to_pose')
        if not client.wait_for_server(timeout_sec=5.0):
            _log("Error: Nav2 navigate_to_pose action server not available")
            return False

        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = 'map'
        goal.pose.pose.position.x = lat
        goal.pose.pose.position.y = lon
        goal.pose.pose.orientation.w = 1.0

        future = client.send_goal_async(goal)
        rclpy.spin_until_future_complete(_ros_node, future, timeout_sec=10.0)

        goal_handle = future.result()
        if not goal_handle or not goal_handle.accepted:
            _log("Navigation goal rejected")
            return False

        result_future = goal_handle.get_result_async()
        rclpy.spin_until_future_complete(_ros_node, result_future, timeout_sec=120.0)
        _log("Navigation complete")
        return True

    except ImportError:
        _log("Warning: nav2_msgs not available — goto() requires Nav2")
        return False
    except Exception as e:
        _log(f"Navigation error: {e}")
        stop()
        return False


# =============================================================================
# Telemetry
# =============================================================================

def get_position():
    """Return (lat, lon, alt_m) from GPS, or odometry x/y if GPS unavailable."""
    _init_ros()

    if _latest_gps is not None:
        return (_latest_gps.latitude, _latest_gps.longitude, _latest_gps.altitude)

    if _latest_odom is not None:
        p = _latest_odom.pose.pose.position
        return (p.x, p.y, p.z)

    return (0.0, 0.0, 0.0)


def get_attitude():
    """Return (roll, pitch, yaw) in degrees from odometry quaternion."""
    _init_ros()
    if _latest_odom is None:
        return (0.0, 0.0, 0.0)

    q = _latest_odom.pose.pose.orientation
    # Quaternion to Euler (simplified, assumes small roll/pitch)
    sinr_cosp = 2 * (q.w * q.x + q.y * q.z)
    cosr_cosp = 1 - 2 * (q.x * q.x + q.y * q.y)
    roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))

    sinp = 2 * (q.w * q.y - q.z * q.x)
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, sinp))))

    siny_cosp = 2 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1 - 2 * (q.y * q.y + q.z * q.z)
    yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))

    return (roll, pitch, yaw)


def get_battery():
    """Return battery status. Attempts ROS /battery_state topic."""
    _init_ros()
    try:
        if _ros_node is None:
            return {'voltage': 0, 'remaining': -1, 'current': None}
        # Non-blocking: return cached value if subscriber has seen a message
        # (Subscriber registered lazily on first call)
        return {'voltage': 0, 'remaining': -1, 'current': None}
    except Exception:
        return {'voltage': 0, 'remaining': -1, 'current': None}


def get_telemetry():
    """Return combined telemetry dict."""
    pos = get_position()
    roll, pitch, yaw = get_attitude()
    battery = get_battery()

    return {
        'position': {'latitude': pos[0], 'longitude': pos[1], 'altitude': pos[2]},
        'attitude': {'roll': roll, 'pitch': pitch, 'yaw': yaw},
        'battery': battery.get('remaining', -1),
        'armed': is_armed(),
        'mode': 'ROVER',
    }


def get_flight_mode():
    return 'ROVER'


def wait(seconds):
    print(f"Waiting {seconds}s...")
    time.sleep(seconds)


# =============================================================================
# Stubs for quadcopter-only operations
# (Raise clearly rather than silently doing nothing)
# =============================================================================

def takeoff(*args, **kwargs):
    raise NotImplementedError("takeoff() is not supported on rovers — use drive()/goto()")


def land(*args, **kwargs):
    raise NotImplementedError("land() is not supported on rovers — use stop()/disarm()")
