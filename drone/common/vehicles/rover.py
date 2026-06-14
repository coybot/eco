"""
Waveshare UGV Rover SDK — serial JSON via ugv_jetson HTTP bridge.

Sends movement commands to the ugv_jetson Flask app (localhost:5000)
which owns the /dev/ttyTHS1 serial port. Command format:
  POST /send_command  command=base -c {"T":1,"L":<left>,"R":<right>}
  T=1: wheel speed control, L/R in range ~[-1.0, 1.0]
  T=0: emergency stop

config.yaml keys:
  vehicle_type: rover
  ugv_host: localhost     # optional
  ugv_port: 5000          # optional
"""

import time
import math
import logging
import sys
import threading
import urllib.request
import urllib.parse
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

MAX_LINEAR_SPEED = 1.0    # m/s
MAX_ANGULAR_SPEED = 1.57  # rad/s

_armed = False
_lock = threading.Lock()


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


def _send(left, right):
    """Send wheel speed command to ugv_jetson HTTP bridge."""
    config = _load_config()
    host = config.get('ugv_host', 'localhost')
    port = config.get('ugv_port', 5000)
    url = f'http://{host}:{port}/send_command'
    cmd = '{' + f'"T":1,"L":{float(left):.3f},"R":{float(right):.3f}' + '}'
    data = urllib.parse.urlencode({'command': f'base -c {cmd}'}).encode()
    try:
        req = urllib.request.Request(url, data=data, method='POST')
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception as e:
        _log(f"Warning: could not send rover command: {e}")
        return False


def _send_stop():
    config = _load_config()
    host = config.get('ugv_host', 'localhost')
    port = config.get('ugv_port', 5000)
    url = f'http://{host}:{port}/send_command'
    data = urllib.parse.urlencode({'command': 'base -c {"T":1,"L":0,"R":0}'}).encode()
    try:
        req = urllib.request.Request(url, data=data, method='POST')
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception as e:
        _log(f"Warning: could not stop rover: {e}")
        return False


# =============================================================================
# Lifecycle
# =============================================================================

def arm():
    global _armed
    _armed = True
    _log("Rover armed")
    return True


def disarm():
    global _armed
    stop()
    _armed = False
    _log("Rover disarmed")
    return True


def safe_disarm():
    return disarm()


def is_armed():
    return _armed


# =============================================================================
# Motion commands
# =============================================================================

def stop():
    _send_stop()
    _log("Rover stopped")


def drive(speed_mps=0.5, duration_sec=1.0):
    """Drive forward (positive) or backward (negative) for duration_sec."""
    speed_mps = max(-MAX_LINEAR_SPEED, min(MAX_LINEAR_SPEED, speed_mps))
    direction = 'forward' if speed_mps >= 0 else 'backward'
    _log(f"Driving {direction} at {abs(speed_mps):.2f} m/s for {duration_sec}s...")
    _send(speed_mps, speed_mps)
    time.sleep(duration_sec)
    stop()


def turn(angle_deg, speed_rad_s=0.5):
    """Turn in place by angle_deg (positive=left/CCW)."""
    speed_rad_s = min(abs(speed_rad_s), MAX_ANGULAR_SPEED)
    angle_rad = math.radians(angle_deg)
    duration = abs(angle_rad) / speed_rad_s
    direction = 1.0 if angle_deg >= 0 else -1.0
    _log(f"Turning {angle_deg:.1f}° ({duration:.1f}s)...")
    _send(-direction * speed_rad_s, direction * speed_rad_s)
    time.sleep(duration)
    stop()


def set_velocity(vx, vy=0.0, omega=0.0):
    """Set continuous velocity: vx=forward m/s, omega=angular rad/s."""
    vx = max(-MAX_LINEAR_SPEED, min(MAX_LINEAR_SPEED, vx))
    left = vx - omega * 0.15
    right = vx + omega * 0.15
    _log(f"Set velocity: vx={vx:.2f} omega={omega:.2f}")
    _send(left, right)


def wait(seconds):
    _log(f"Waiting {seconds}s...")
    time.sleep(seconds)


# =============================================================================
# Telemetry stubs
# =============================================================================

def get_position():
    return (0.0, 0.0, 0.0)


def get_attitude():
    return (0.0, 0.0, 0.0)


def get_battery():
    return {'voltage': 0, 'remaining': -1, 'current': None}


def get_telemetry():
    return {
        'position': {'latitude': 0.0, 'longitude': 0.0, 'altitude': 0.0},
        'attitude': {'roll': 0.0, 'pitch': 0.0, 'yaw': 0.0},
        'battery': -1,
        'armed': _armed,
        'mode': 'ROVER',
    }


def get_flight_mode():
    return 'ROVER'


def goto(lat, lon, tolerance_m=0.5):
    _log(f"goto() requires Nav2 — not available on this rover")
    return False


# =============================================================================
# Quadcopter stubs
# =============================================================================

def safe_disarm():
    return disarm()


def takeoff(*args, **kwargs):
    raise NotImplementedError("takeoff() is not supported on rovers — use drive()/goto()")


def land(*args, **kwargs):
    raise NotImplementedError("land() is not supported on rovers — use stop()/disarm()")
