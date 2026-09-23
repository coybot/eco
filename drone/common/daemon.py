#!/usr/bin/env python3
"""
Drone API Daemon - Listens for commands via AWS IoT Core MQTT

Receives LLM-generated Python code and executes it using the drone SDK.
Includes WiFi provisioning for initial setup and conversation support.

Uses AWS IoT Device SDK v2 for Python for improved performance and reliability.
"""

import json
import sys
import os
import logging
import tempfile
from typing import Optional
import subprocess
import yaml
import time
import re
import threading
from pathlib import Path
from datetime import datetime, timezone
from collections import deque
from concurrent.futures import Future

# Add drone-api folder to path for imports
DRONE_DIR = Path(__file__).parent.absolute()
sys.path.insert(0, str(DRONE_DIR))

# AWS IoT Device SDK v2
from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

from provisioning import ProvisioningService, get_or_create_drone_id
from command_id import compute_command_id

# Load configuration (may not exist on first boot)
config_path = DRONE_DIR / 'config.yaml'
if config_path.exists():
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f) or {}
else:
    config = {}

# Drone ID - the ID stored on the device wins, then config.yaml's drone_id (which
# only seeds a device that has none, because check_provisioning writes that key
# itself and it is therefore a record rather than a choice), then a fresh one.
DRONE_ID = get_or_create_drone_id(config.get('drone_id'))
IOT_ENDPOINT = config.get('iot_endpoint')
LOG_LEVEL = config.get('log_level', 'INFO')

# Control plane: "aws" (default, AWS IoT Core mTLS) or "gcs" (a local Ground
# Control Station's mosquitto broker - see gcs/README.md and
# config.yaml.example's `gcs:` section). Same topics either way; only the
# transport/auth differs (build_mqtt_connection(), below).
CONTROL_PLANE = config.get('control_plane', 'aws')
GCS_CONFIG = config.get('gcs', {}) or {}

# Vehicle type ('quadcopter' [default], 'rover', 'fixedwing') — drives which
# flight SDK / backend / VehicleClass the mission loop uses below. There is no
# cloud-registry write path for this yet (see the fixed-wing autonomy plan) —
# set explicitly in this drone's config.yaml until one exists.
VEHICLE_TYPE = config.get('vehicle_type', 'quadcopter')

# Training data capture — off by default; set record: true in config.yaml to enable.
_record_cfg = config.get('record', {})
if _record_cfg is True:
    _record_cfg = {}
if _record_cfg is not False and _record_cfg is not None:
    try:
        from data_recorder import configure
        _rec_dir = str(DRONE_DIR / _record_cfg.get('output_dir', 'recordings'))
        configure(enabled=True, output_dir=_rec_dir,
                  save_images=_record_cfg.get('save_images', True),
                  shard_size=_record_cfg.get('shard_size', 1000))
    except Exception as _e:
        pass  # data_recorder not installed — silently skip

# IoT Thing Shadow (offline-safe decommission/reset)
CERTS_DIR = DRONE_DIR / "certs"
THING_NAME_FILE = CERTS_DIR / "thing-name.txt"
FACTORY_RESET_PENDING_PATH = DRONE_DIR / "factory_reset_pending.json"
WIFI_CONFIG_APPLIED_PATH = DRONE_DIR / "wifi_config_applied.json"
BATTERY_CONFIG_PATH = DRONE_DIR / "battery_config.json"

# Default battery config (4S LiPo)
_battery_config = {
    'cellCount': 4,
    'capacityMah': 5000,
    'cellEmptyVoltage': 3.5,
    'cellFullVoltage': 4.2
}


def get_thing_name() -> str:
    """
    Return the AWS IoT ThingName for this device.

    In our install flow, fleet provisioning writes certs/thing-name.txt and the
    installer often also mirrors that into /etc/drone-id.
    """
    try:
        if THING_NAME_FILE.exists():
            name = THING_NAME_FILE.read_text().strip()
            if name:
                return name
    except Exception:
        pass
    return DRONE_ID


THING_NAME = get_thing_name()

# Ensure logs directory exists
(DRONE_DIR / 'logs').mkdir(exist_ok=True)


# ============================================
# Log Streaming Handler - publishes logs to MQTT
# ============================================
class MQTTLogHandler(logging.Handler):
    """Custom logging handler that publishes logs to MQTT."""
    
    def __init__(self, drone_id):
        super().__init__()
        self.drone_id = drone_id
        self._mqtt_connection = None
        self._log_buffer = deque(maxlen=100)  # Buffer logs until MQTT is connected
        self._buffer_lock = threading.Lock()
        
    def set_mqtt_connection(self, connection):
        """Set the MQTT connection after it's established."""
        self._mqtt_connection = connection
        # Flush buffered logs
        with self._buffer_lock:
            while self._log_buffer:
                log_entry = self._log_buffer.popleft()
                self._publish_log(log_entry)
    
    def _publish_log(self, log_entry):
        """Publish a log entry to MQTT."""
        if self._mqtt_connection:
            topic = f"drone/{self.drone_id}/logs"
            try:
                self._mqtt_connection.publish(
                    topic=topic,
                    payload=json.dumps(log_entry),
                    qos=mqtt.QoS.AT_MOST_ONCE
                )
            except Exception:
                pass  # Don't log errors from the log handler
    
    def emit(self, record):
        """Handle a log record."""
        try:
            log_entry = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'level': record.levelname,
                'source': 'daemon',
                'message': self.format(record),
                'logger': record.name
            }
            
            if self._mqtt_connection:
                self._publish_log(log_entry)
            else:
                # Buffer until MQTT is connected
                with self._buffer_lock:
                    self._log_buffer.append(log_entry)
        except Exception:
            pass  # Silently fail


# Create MQTT log handler instance
mqtt_log_handler = MQTTLogHandler(DRONE_ID)
mqtt_log_handler.setLevel(logging.INFO)
mqtt_log_handler.setFormatter(logging.Formatter('%(message)s'))

# Setup logging with both file and MQTT handlers
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(DRONE_DIR / 'logs' / 'drone.log')
    ]
)
logger = logging.getLogger(__name__)
logger.addHandler(mqtt_log_handler)


# Global reference to MQTT connection
_mqtt_connection = None

# Video streaming state
_video_process = None
_video_last_heartbeat = 0
VIDEO_HEARTBEAT_TIMEOUT = 120  # Stop streaming if no heartbeat for 120 seconds

# Status TTL in seconds (prevents offline flapping during transient MQTT drops)
STATUS_TTL_SECONDS = 30



def publish_code_log(source: str, level: str, message: str, code: str = None):
    """Publish a code execution log entry to MQTT."""
    global _mqtt_connection
    if not _mqtt_connection:
        return
    
    topic = f"drone/{DRONE_ID}/logs"
    log_entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'level': level,
        'source': source,
        'message': message
    }
    if code:
        log_entry['code'] = code
    
    try:
        _mqtt_connection.publish(
            topic=topic,
            payload=json.dumps(log_entry),
            qos=mqtt.QoS.AT_MOST_ONCE
        )
    except Exception as e:
        logger.debug(f"Failed to publish code log: {e}")


# SAFETY LAYER 5: Maximum execution time before forced landing
MAX_EXECUTION_TIME = 300  # 5 minutes - after this, watchdog forces landing


class ExecutionWatchdog:
    """Watchdog timer that forces drone to land if code execution takes too long."""
    
    def __init__(self, timeout_seconds: float):
        self.timeout = timeout_seconds
        self._timer = None
        self._triggered = False
    
    def start(self):
        """Start the watchdog timer."""
        self._timer = threading.Timer(self.timeout, self._on_timeout)
        self._timer.daemon = True
        self._timer.start()
        logger.debug(f"Execution watchdog started ({self.timeout}s timeout)")
    
    def cancel(self):
        """Cancel the watchdog timer (call when execution completes normally)."""
        if self._timer:
            self._timer.cancel()
            self._timer = None
    
    def _on_timeout(self):
        """Called when execution timeout is reached - force landing."""
        self._triggered = True
        logger.error(f"SAFETY: Execution timeout ({self.timeout}s) - forcing emergency land!")
        publish_code_log('safety', 'ERROR', f'Execution timeout after {self.timeout}s - forcing land')
        try:
            import drone_sdk
            if drone_sdk.is_armed():
                logger.warning("SAFETY: Watchdog forcing land due to timeout")
                drone_sdk.land()
        except Exception as e:
            logger.error(f"SAFETY: Watchdog land failed: {e}")
    
    @property
    def triggered(self) -> bool:
        return self._triggered


def execute_code(code: str, conversation_id: str = None) -> dict:
    """Execute LLM-generated Python code in a restricted sandbox."""
    
    # Publish code execution start to logs
    publish_code_log('llm_code', 'INFO', f"Executing LLM-generated code", code=code)

    # Safety gate: block dangerous operations
    BLOCKED_PATTERNS = [
        (r"\bdisarm\s*\(", "disarm() - use safe_disarm() on ground or land() in flight"),
        (r"MAV_CMD_COMPONENT_ARM_DISARM", "direct arm/disarm commands"),
        (r"\b__import__\s*\(", "__import__() - dynamic imports not allowed"),
        (r"\beval\s*\(", "eval() - not allowed"),
        (r"\bexec\s*\(", "exec() - nested exec not allowed"),
        (r"\bcompile\s*\(", "compile() - not allowed"),
        (r"\bopen\s*\([^)]*['\"][wa]", "file write operations"),
        (r"\bos\.system\s*\(", "os.system() - shell commands not allowed"),
        (r"\bsubprocess\.", "subprocess module - not allowed"),
        (r"\b__builtins__", "accessing __builtins__"),
        (r"\bglobals\s*\(\s*\)", "globals() - not allowed"),
        (r"\blocals\s*\(\s*\)", "locals() - not allowed"),
        (r"\bgetattr\s*\([^)]*['\"]__", "accessing dunder attributes"),
        (r"\bsetattr\s*\(", "setattr() - not allowed"),
        (r"\bdelattr\s*\(", "delattr() - not allowed"),
        # SAFETY LAYER 5: Block infinite loops that would prevent landing
        (r"\bwhile\s+True\s*:", "infinite while True loop - drone must land"),
        (r"\bwhile\s+1\s*:", "infinite while 1 loop - drone must land"),
        (r"\bfor\s+\w+\s+in\s+iter\s*\(\s*int\s*,", "infinite iter() loop"),
    ]
    
    for pattern, description in BLOCKED_PATTERNS:
        if re.search(pattern, code):
            message = f"Blocked unsafe code: {description}"
            logger.warning(message)
            publish_code_log('llm_code', 'WARNING', message, code=code)
            return {'success': False, 'error': message, 'stderr': message}
    
    # Create temp file with the code
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        # Prepend SDK import
        f.write(f"import sys\n")
        f.write(f"sys.path.insert(0, '{DRONE_DIR}')\n")
        f.write("from drone_sdk import *\n\n")
        
        # Add conversation ID as global variable if provided
        if conversation_id:
            f.write(f"CONVERSATION_ID = '{conversation_id}'\n\n")
        
        f.write(code)
        temp_path = f.name
    
    logger.info(f"Executing code from {temp_path}")
    logger.debug(f"Code:\n{code}")
    
    try:
        # Execute in-process using exec() with restricted builtins
        import io
        import contextlib
        import builtins
        import drone_sdk
        
        # Capture stdout
        stdout_capture = io.StringIO()
        
        # Create restricted builtins - only allow safe functions
        SAFE_BUILTINS = {
            'abs', 'all', 'any', 'bool', 'bytes', 'callable', 'chr', 'dict',
            'divmod', 'enumerate', 'filter', 'float', 'format', 'frozenset',
            'hasattr', 'hash', 'hex', 'id', 'int', 'isinstance', 'issubclass',
            'iter', 'len', 'list', 'map', 'max', 'min', 'next', 'oct', 'ord',
            'pow', 'print', 'range', 'repr', 'reversed', 'round', 'set',
            'slice', 'sorted', 'str', 'sum', 'tuple', 'type', 'zip',
            'True', 'False', 'None', 'Exception', 'ValueError', 'TypeError',
            'RuntimeError', 'KeyError', 'IndexError', 'AttributeError',
        }
        
        restricted_builtins = {
            name: getattr(builtins, name) 
            for name in SAFE_BUILTINS 
            if hasattr(builtins, name)
        }
        
        # Build execution namespace with SDK functions and restricted builtins
        exec_globals = {
            '__name__': '__main__',
            '__builtins__': restricted_builtins,
        }
        exec_locals = {}
        
        # Import SDK into namespace (these are the allowed drone control functions)
        for name in dir(drone_sdk):
            if not name.startswith('_'):
                exec_globals[name] = getattr(drone_sdk, name)
        
        # Add safe modules
        import math
        import time as time_module
        exec_globals['math'] = math
        exec_globals['time'] = time_module
        exec_globals['sleep'] = time_module.sleep
        
        # Add conversation ID if provided
        if conversation_id:
            exec_globals['CONVERSATION_ID'] = conversation_id

        # Pre-populate home position so LLM code can use home_lat/home_lon/home_alt
        # without needing to call get_position() first
        try:
            _home = drone_sdk.get_position()
            exec_globals['home_lat'] = _home[0]
            exec_globals['home_lon'] = _home[1]
            exec_globals['home_alt'] = _home[2]
        except Exception:
            exec_globals['home_lat'] = 0.0
            exec_globals['home_lon'] = 0.0
            exec_globals['home_alt'] = 0.0
        
        # SAFETY LAYER 5: Start execution watchdog timer
        watchdog = ExecutionWatchdog(MAX_EXECUTION_TIME)
        watchdog.start()
        
        try:
            # Execute with stdout capture in restricted environment
            with contextlib.redirect_stdout(stdout_capture):
                exec(code, exec_globals, exec_locals)
        finally:
            # Cancel watchdog when execution completes (success or failure)
            watchdog.cancel()
        
        # Check if watchdog was triggered (timeout occurred)
        if watchdog.triggered:
            return {
                'success': False,
                'error': f'Execution timeout after {MAX_EXECUTION_TIME}s',
                'stderr': f'Code execution exceeded {MAX_EXECUTION_TIME}s limit'
            }
        
        stdout_output = stdout_capture.getvalue()
        
        output = {
            'success': True,
            'stdout': stdout_output,
            'stderr': '',
            'returncode': 0
        }
        
        # Try to extract media URLs from stdout
        image_urls = extract_image_urls(stdout_output)
        video_urls = extract_video_urls(stdout_output)

        # Generated code that starts a recording and never stops it is a
        # plausible LLM slip, and the footage is already on the vehicle by
        # then. Recover it here, on the success path, where the URLs can still
        # reach the operator — the finally block below is a resource-cleanup
        # backstop for the exception path and cannot return anything.
        for url in _recover_unstopped_recording(conversation_id):
            (video_urls if url.lower().endswith('.mp4') else image_urls).append(url)

        if image_urls:
            output['image_urls'] = image_urls
        if video_urls:
            output['video_urls'] = video_urls
        
        logger.info(f"Execution successful:\n{stdout_output}")
        # Publish stdout to logs
        if stdout_output.strip():
            for line in stdout_output.strip().split('\n'):
                if line.strip():
                    publish_code_log('llm_code', 'INFO', line.strip())
        
        return output
    
    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        logger.error(f"Execution error: {error_msg}")
        publish_code_log('llm_code', 'ERROR', f"Execution error: {e}")
        return {'success': False, 'error': str(e), 'stderr': error_msg}
    
    finally:
        # Stop any recording the generated code started but never stopped. The
        # recorder runs on its own thread and holds the camera, so a snippet
        # that calls start_recording() and then raises would otherwise leave
        # the device claimed until the recorder's own max_seconds expired -
        # with the WebRTC producer unable to reclaim it in the meantime. The
        # mission path does the same thing in MissionLoop._cleanup.
        # Cleanup only: on the success path the recording was already stopped
        # (and its URLs returned) above. This catches the exception path, where
        # there is no response left to attach anything to — the goal here is
        # purely to release the camera and the thread.
        for url in _recover_unstopped_recording(conversation_id):
            logger.warning(f"Discarding media from an unstopped recording after an error: {url}")

        # SAFETY LAYER 1: Always ensure drone lands if still armed after execution
        # This catches: exceptions mid-flight, code that forgets to land, infinite loops that timeout
        try:
            import drone_sdk
            if drone_sdk.is_armed():
                logger.warning("SAFETY: Drone still armed after code execution - forcing emergency land!")
                publish_code_log('safety', 'WARNING', 'Forcing emergency land - code did not land properly')
                try:
                    drone_sdk.land()
                    logger.info("SAFETY: Emergency land command sent successfully")
                except Exception as land_err:
                    logger.error(f"SAFETY: Emergency land() failed: {land_err}")
                    # Last resort: try to set LAND mode directly
                    try:
                        drone_sdk._connect().set_mode('LAND')
                        logger.info("SAFETY: Set LAND mode as fallback")
                    except Exception as mode_err:
                        logger.error(f"SAFETY: Failed to set LAND mode: {mode_err}")
        except Exception as safety_err:
            logger.error(f"SAFETY: Error in safety check: {safety_err}")
        finally:
            # Always disconnect MAVLink
            try:
                drone_sdk.disconnect()
            except:
                pass
        
        # Clean up temp file
        try:
            os.unlink(temp_path)
        except:
            pass


def _recover_unstopped_recording(conversation_id) -> list:
    """Stop a recording the generated code started but never stopped.

    Returns the uploaded URLs (possibly empty). Never raises: this runs on the
    cleanup path, where an exception would mask whatever actually went wrong.

    It matters beyond tidiness because the recorder holds the camera on its own
    thread, so a leaked one keeps the WebRTC producer from reclaiming the
    device until its max_seconds expires.
    """
    try:
        import drone_sdk
        if not drone_sdk.is_recording():
            return []
        logger.warning("Code finished with a recording still running - stopping it")
        return drone_sdk.stop_recording(conversation_id=conversation_id) or []
    except Exception as e:
        logger.error(f"Failed to stop a leftover recording: {e}")
        return []


def _extract_media_urls(stdout: str, extensions: str) -> list:
    """Extract S3 URLs with the given extensions from generated-code stdout.

    The codegen path has no structured return channel — the SDK verbs print
    their URLs and this scrapes them back out — so an extension missing here
    means media that was really captured and uploaded never reaches the app.
    """
    pattern = r'https://[a-zA-Z0-9\-]+\.s3\.amazonaws\.com/[^\s\'"]+\.(?:' + extensions + r')'
    # dict.fromkeys dedups while preserving capture order, which set() lost —
    # a look_around's photos arriving shuffled made the app's image_choice
    # numbering disagree with the order they were taken in.
    return list(dict.fromkeys(re.findall(pattern, stdout)))


def extract_image_urls(stdout: str) -> list:
    """Extract S3 image URLs from stdout."""
    return _extract_media_urls(stdout, 'jpg|jpeg|png')


def extract_video_urls(stdout: str) -> list:
    """Extract S3 video URLs from stdout (record_video / stop_recording)."""
    return _extract_media_urls(stdout, 'mp4')


def _shadow_base() -> str:
    return f"$aws/things/{THING_NAME}/shadow"


def _shadow_topics() -> dict:
    base = _shadow_base()
    return {
        "get": f"{base}/get",
        "get_accepted": f"{base}/get/accepted",
        "get_rejected": f"{base}/get/rejected",
        "update": f"{base}/update",
        "update_accepted": f"{base}/update/accepted",
        "update_rejected": f"{base}/update/rejected",
        "delta": f"{base}/update/delta",
    }


def _write_factory_reset_pending(nonce: str, reason: str, source: str):
    """Persist reset intent so we can clear shadow after re-provision + reconnect."""
    try:
        FACTORY_RESET_PENDING_PATH.write_text(json.dumps({
            "nonce": nonce,
            "reason": reason,
            "source": source,
            "requestedAt": datetime.now(timezone.utc).isoformat()
        }))
    except Exception as e:
        logger.warning(f"Failed to persist pending factory reset: {e}")


def _read_factory_reset_pending() -> Optional[dict]:
    try:
        if not FACTORY_RESET_PENDING_PATH.exists():
            return None
        return json.loads(FACTORY_RESET_PENDING_PATH.read_text() or "{}")
    except Exception as e:
        logger.warning(f"Failed reading pending factory reset file: {e}")
        return None


def _clear_factory_reset_pending():
    try:
        if FACTORY_RESET_PENDING_PATH.exists():
            FACTORY_RESET_PENDING_PATH.unlink()
    except Exception as e:
        logger.warning(f"Failed deleting pending factory reset file: {e}")


def _read_wifi_config_applied() -> Optional[dict]:
    try:
        if not WIFI_CONFIG_APPLIED_PATH.exists():
            return None
        return json.loads(WIFI_CONFIG_APPLIED_PATH.read_text() or "{}")
    except Exception as e:
        logger.warning(f"Failed reading wifi config applied file: {e}")
        return None


def _write_wifi_config_applied(nonce: str, status: str, error: Optional[str] = None):
    try:
        WIFI_CONFIG_APPLIED_PATH.write_text(json.dumps({
            "nonce": nonce,
            "status": status,
            "error": error,
            "appliedAt": datetime.now(timezone.utc).isoformat()
        }))
    except Exception as e:
        logger.warning(f"Failed persisting wifi config applied state: {e}")


def _handle_wifi_config(wifi_config: dict, source: str):
    """
    Apply desired WiFi configuration.

    Expected: {"nonce": "...", "requestedAt": "...", "networks": [...]}
    """
    if not isinstance(wifi_config, dict):
        return

    nonce = wifi_config.get("nonce")
    networks = wifi_config.get("networks") or []
    if not nonce:
        logger.warning("wifiConfig missing nonce; ignoring")
        return

    applied = _read_wifi_config_applied() or {}
    if applied.get("nonce") == nonce and applied.get("status") == "ok":
        logger.info(f"Ignoring already-applied wifiConfig nonce={nonce}")
        return

    def worker():
        logger.info(f"Applying wifiConfig nonce={nonce} (source={source})")
        try:
            from wifi_manager import WiFiManager
            wm = WiFiManager()
            ok = wm.apply_network_list(networks)
            if not ok:
                raise RuntimeError("WiFi manager returned failure applying network list")

            _write_wifi_config_applied(nonce=nonce, status="ok")

            publish_shadow_update({
                "state": {
                    "desired": {
                        "wifiConfig": None
                    },
                    "reported": {
                        "wifiConfig": {
                            "nonce": nonce,
                            "status": "ok",
                            "appliedAt": datetime.now(timezone.utc).isoformat()
                        }
                    }
                }
            })
            logger.info(f"Applied wifiConfig nonce={nonce}")
        except Exception as e:
            err = str(e)
            _write_wifi_config_applied(nonce=nonce, status="error", error=err)
            publish_shadow_update({
                "state": {
                    "reported": {
                        "wifiConfig": {
                            "nonce": nonce,
                            "status": "error",
                            "error": err,
                            "appliedAt": datetime.now(timezone.utc).isoformat()
                        }
                    }
                }
            })
            logger.warning(f"Failed applying wifiConfig nonce={nonce}: {err}")

    threading.Thread(target=worker, daemon=True).start()


def _load_battery_config():
    """Load battery config from file if it exists."""
    global _battery_config
    try:
        if BATTERY_CONFIG_PATH.exists():
            loaded = json.loads(BATTERY_CONFIG_PATH.read_text() or "{}")
            if loaded.get("cellCount"):
                _battery_config = loaded
                logger.info(f"Loaded battery config: {_battery_config}")
    except Exception as e:
        logger.warning(f"Failed loading battery config: {e}")


def _save_battery_config():
    """Persist battery config to file."""
    try:
        BATTERY_CONFIG_PATH.write_text(json.dumps(_battery_config))
    except Exception as e:
        logger.warning(f"Failed saving battery config: {e}")


def _handle_battery_config(battery_config: dict, source: str):
    """
    Apply desired battery configuration.

    Expected: {"nonce": "...", "requestedAt": "...", "cellCount": 4, "capacityMah": 5000, ...}
    """
    global _battery_config
    
    if not isinstance(battery_config, dict):
        return

    nonce = battery_config.get("nonce")
    if not nonce:
        logger.warning("batteryConfig missing nonce; ignoring")
        return

    cell_count = battery_config.get("cellCount")
    capacity_mah = battery_config.get("capacityMah")
    cell_empty = battery_config.get("cellEmptyVoltage")
    cell_full = battery_config.get("cellFullVoltage")

    # Validate
    if not all([cell_count, capacity_mah, cell_empty, cell_full]):
        logger.warning(f"batteryConfig missing required fields; ignoring")
        return

    logger.info(f"Applying batteryConfig nonce={nonce} (source={source})")

    def worker():
        global _battery_config
        try:
            # Update in-memory config
            _battery_config = {
                'cellCount': int(cell_count),
                'capacityMah': int(capacity_mah),
                'cellEmptyVoltage': float(cell_empty),
                'cellFullVoltage': float(cell_full)
            }
            _save_battery_config()

            # Set ArduPilot parameters via MAVLink
            try:
                from drone_sdk import _connect
                from pymavlink import mavutil
                import time

                m = _connect()

                def set_param(name, value):
                    name_bytes = name.encode("utf-8")
                    m.mav.param_set_send(
                        m.target_system,
                        m.target_component,
                        name_bytes,
                        float(value),
                        mavutil.mavlink.MAV_PARAM_TYPE_REAL32
                    )
                    # Wait briefly for ack
                    start = time.time()
                    while time.time() - start < 2:
                        msg = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
                        if msg and msg.param_id.strip(chr(0)) == name:
                            logger.info(f"Set FC param {name} = {msg.param_value}")
                            return True
                    return False

                # Set ArduPilot battery parameters
                set_param("BATT_CAPACITY", capacity_mah)
                # Calculate low/critical voltages
                low_volt = cell_count * 3.5  # 3.5V per cell = low
                crit_volt = cell_count * 3.3  # 3.3V per cell = critical
                set_param("BATT_LOW_VOLT", low_volt)
                set_param("BATT_CRT_VOLT", crit_volt)

                logger.info(f"Set FC battery params: capacity={capacity_mah}mAh, low={low_volt}V, crit={crit_volt}V")
            except Exception as fc_err:
                logger.warning(f"Could not set FC battery params: {fc_err}")

            # Report success to shadow
            publish_shadow_update({
                "state": {
                    "desired": {
                        "batteryConfig": None
                    },
                    "reported": {
                        "batteryConfig": {
                            "nonce": nonce,
                            "status": "ok",
                            "appliedAt": datetime.now(timezone.utc).isoformat(),
                            **_battery_config
                        }
                    }
                }
            })
            logger.info(f"Applied batteryConfig nonce={nonce}")
        except Exception as e:
            err = str(e)
            publish_shadow_update({
                "state": {
                    "reported": {
                        "batteryConfig": {
                            "nonce": nonce,
                            "status": "error",
                            "error": err,
                            "appliedAt": datetime.now(timezone.utc).isoformat()
                        }
                    }
                }
            })
            logger.warning(f"Failed applying batteryConfig nonce={nonce}: {err}")

    threading.Thread(target=worker, daemon=True).start()


def publish_shadow_update(payload: dict) -> bool:
    """Publish a Thing Shadow update via MQTT (device side)."""
    global _mqtt_connection
    if not _mqtt_connection:
        return False
    try:
        topics = _shadow_topics()
        _mqtt_connection.publish(
            topic=topics["update"],
            payload=json.dumps(payload),
            qos=mqtt.QoS.AT_LEAST_ONCE
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to publish shadow update: {e}")
        return False


def ack_factory_reset_if_pending():
    """
    If we previously started a factory reset, clear desired.decommission after we reconnect.

    This prevents an endless "reset loop" where desired state keeps re-triggering reset even
    after the drone has been re-provisioned.
    """
    pending = _read_factory_reset_pending()
    if not pending:
        return

    nonce = pending.get("nonce") or "unknown"
    logger.info(f"Acking completed factory reset (nonce={nonce}) via Thing Shadow")

    ok = publish_shadow_update({
        "state": {
            "desired": {
                "decommission": None
            },
            "reported": {
                "decommission": {
                    "nonce": nonce,
                    "completedAt": datetime.now(timezone.utc).isoformat()
                }
            }
        }
    })
    if ok:
        _clear_factory_reset_pending()


def _handle_decommission(action: str, reason: str, nonce: Optional[str], source: str):
    if action != "factory_reset":
        logger.warning(f"Ignoring unknown decommission action: {action}")
        return

    nonce = nonce or f"no-nonce-{int(time.time())}"
    _write_factory_reset_pending(nonce=nonce, reason=reason, source=source)

    # Best-effort: report we are starting a reset (desired is cleared after reconnect).
    publish_shadow_update({
        "state": {
            "reported": {
                "decommission": {
                    "nonce": nonce,
                    "status": "resetting",
                    "startedAt": datetime.now(timezone.utc).isoformat()
                }
            }
        }
    })

    handle_factory_reset(reason)


def on_shadow_get_accepted(topic, payload, **kwargs):
    try:
        data = json.loads(payload)
        desired = (data.get("state") or {}).get("desired") or {}
        decommission = desired.get("decommission") or {}
        action = decommission.get("action")
        if action:
            _handle_decommission(
                action=action,
                reason=decommission.get("reason", "Unknown"),
                nonce=decommission.get("nonce"),
                source="shadow_get"
            )
        wifi_config = desired.get("wifiConfig") or {}
        if wifi_config:
            _handle_wifi_config(wifi_config=wifi_config, source="shadow_get")
        battery_config = desired.get("batteryConfig") or {}
        if battery_config:
            _handle_battery_config(battery_config=battery_config, source="shadow_get")
    except Exception as e:
        logger.warning(f"Failed handling shadow get accepted: {e}")


def on_shadow_delta(topic, payload, **kwargs):
    try:
        data = json.loads(payload)
        # Delta payload shape: {"version":..., "timestamp":..., "state": {...desired-delta...}}
        state = data.get("state") or {}
        decommission = state.get("decommission") or {}
        action = decommission.get("action")
        if action:
            _handle_decommission(
                action=action,
                reason=decommission.get("reason", "Unknown"),
                nonce=decommission.get("nonce"),
                source="shadow_delta"
            )
        wifi_config = state.get("wifiConfig") or {}
        if wifi_config:
            _handle_wifi_config(wifi_config=wifi_config, source="shadow_delta")
        battery_config = state.get("batteryConfig") or {}
        if battery_config:
            _handle_battery_config(battery_config=battery_config, source="shadow_delta")
    except Exception as e:
        logger.warning(f"Failed handling shadow delta: {e}")


def on_shadow_rejected(topic, payload, **kwargs):
    try:
        data = json.loads(payload)
        logger.warning(f"Thing shadow request rejected: {data}")
    except Exception:
        logger.warning("Thing shadow request rejected (unparseable payload)")


def request_shadow_get():
    global _mqtt_connection
    if not _mqtt_connection:
        return
    try:
        topics = _shadow_topics()
        _mqtt_connection.publish(
            topic=topics["get"],
            payload=json.dumps({}),
            qos=mqtt.QoS.AT_LEAST_ONCE
        )
    except Exception as e:
        logger.warning(f"Failed to request shadow get: {e}")


def handle_factory_reset(reason: str):
    """Perform factory reset - clear WiFi and start provisioning hotspot."""
    logger.warning(f"Factory reset requested: {reason}")
    
    try:
        # Run factory reset script to clear WiFi
        result = subprocess.run(
            [sys.executable, str(DRONE_DIR / 'factory_reset.py'), '--force'],
            capture_output=True,
            text=True,
            timeout=30
        )
        logger.info(f"Factory reset output: {result.stdout}")
        if result.stderr:
            logger.warning(f"Factory reset stderr: {result.stderr}")
        
        # Start provisioning hotspot immediately (5 minutes)
        logger.info("Starting provisioning hotspot...")
        from provisioning import ProvisioningService
        service = ProvisioningService(timeout=300)  # 5 minutes
        
        result = service.run_provisioning()
        
        if result.success:
            logger.info("Re-provisioning successful!")
            # Update config with new credentials
            config['drone_id'] = DRONE_ID
            if result.user_id:
                config['user_id'] = result.user_id
            with open(config_path, 'w') as f:
                yaml.dump(config, f)
            logger.info("Restarting daemon to apply new config...")
            # Restart ourselves
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            logger.warning(f"Re-provisioning failed or timed out: {result.error}")
            logger.info("Rebooting to retry...")
            subprocess.run(['sudo', 'reboot'], check=False)
        
    except Exception as e:
        logger.error(f"Factory reset failed: {e}")


def on_command(topic, payload, **kwargs):
    """Handle incoming command from IoT Core (legacy endpoint)."""
    global _mqtt_connection
    
    try:
        data = json.loads(payload)
        
        # Check for factory reset command
        if data.get('action') == 'factory_reset':
            _handle_decommission(
                action="factory_reset",
                reason=data.get('reason', 'Unknown'),
                nonce=data.get("nonce"),
                source="command_topic"
            )
            return
        
        original_cmd = data.get('original_command', 'unknown')
        code = data.get('code', '')
        
        logger.info(f"Received command: {original_cmd}")
        logger.info(f"Generated code:\n{code}")
        
        # Execute the code
        result = execute_code(code)
        
        # TTL: 10 seconds from now
        ttl = int(time.time()) + STATUS_TTL_SECONDS
        
        # Publish result back to status topic
        if _mqtt_connection:
            # Truncate stdout if too long (MQTT has 128KB limit)
            if result.get('stdout') and len(result['stdout']) > 4000:
                result['stdout'] = result['stdout'][:4000] + '\n... (truncated)'
            
            status_topic = f"drone/{DRONE_ID}/status"
            _mqtt_connection.publish(
                topic=status_topic,
                payload=json.dumps({
                    'droneId': DRONE_ID,
                    'status': 'online',
                    'lastUpdate': datetime.now(timezone.utc).isoformat(),
                    'ttl': ttl,
                    'original_command': original_cmd,
                    'result': result
                }),
                qos=mqtt.QoS.AT_MOST_ONCE
            )
            logger.info(f"Published result to {status_topic}")
        
    except Exception as e:
        logger.error(f"Error handling command: {e}")


_recent_command_ids = deque(maxlen=100)  # Track recently processed command IDs
_command_dedup_lock = threading.Lock()

def on_chat_command(topic, payload, **kwargs):
    """Handle incoming chat command from IoT Core."""
    global _mqtt_connection
    
    try:
        data = json.loads(payload)
        
        # Deduplicate commands - MQTT QoS 1 can redeliver messages
        message_id = compute_command_id(data)
        
        with _command_dedup_lock:
            if message_id in _recent_command_ids:
                logger.info(f"Skipping duplicate command (message_id={message_id[:8]}...)")
                return
            _recent_command_ids.append(message_id)
        
        # Stop video streaming before executing chat commands
        # This ensures camera is available if code uses capture_photo()
        if _video_process is not None and _video_process.poll() is None:
            logger.info("Stopping video streaming for chat command (releasing camera)")
            stop_video_streaming(reason="chat command - releasing camera for capture_photo()")
            time.sleep(0.5)  # Brief pause to ensure camera is fully released
        
        action = data.get('action', 'execute')
        conversation_id = data.get('conversation_id', '')
        original_message = data.get('original_message', '')
        
        logger.info(f"Received chat command: {action}")
        logger.info(f"Conversation: {conversation_id}")
        
        # Handle mission-based action (Nano / NX / AGX with VLM)
        if action == 'mission':
            mission_id = data.get('mission_id', 'unknown')
            phases = data.get('phases', [])
            logger.info(f"Received mission {mission_id} with {len(phases)} phases")
            # Log the phases as dispatched. Without this there is no record of what
            # the planner actually asked for, so an aircraft that flies to a
            # different altitude than the operator typed cannot be told apart from
            # a planner that asked for the wrong altitude in the first place.
            for _i, _phase in enumerate(phases, 1):
                logger.info(f"  phase {_i}/{len(phases)}: {json.dumps(_phase, default=str)[:2000]}")
            
            # Run mission execution in a separate thread to not block MQTT
            def execute_mission_async():
                try:
                    from reasoning_loop import Mission, MissionLoop

                    # Create Mission object
                    mission = Mission(
                        mission_id=mission_id,
                        phases=phases,
                        conversation_id=conversation_id,
                        original_message=original_message,
                    )

                    # Without this, MissionLoop always defaulted to a quadcopter
                    # HardwareBackend regardless of the actual airframe (see the
                    # fixed-wing autonomy plan's M0) — build the fixed-wing
                    # backend/capability descriptor explicitly when configured.
                    loop_kwargs = {
                        'mqtt_client': _mqtt_connection,
                        'conversation_id': conversation_id,
                    }
                    if VEHICLE_TYPE == 'fixedwing':
                        from vehicle_class import get_class
                        from backends import HardwareBackend
                        try:
                            import plane_sdk
                        except Exception as e:
                            logger.error(f"plane_sdk unavailable ({e}) — "
                                         f"fixedwing mission cannot fly")
                            raise
                        perception = None
                        try:
                            from perception import PerceptionService
                            perception = PerceptionService()
                        except Exception as e:
                            logger.warning(f"PerceptionService unavailable: {e}")
                        loop_kwargs['backend'] = HardwareBackend(
                            perception=perception,
                            vehicle_class='fixedwing',
                            plane_sdk=plane_sdk,
                        )
                        loop_kwargs['vehicle_class'] = get_class('fixedwing')

                    # Create mission loop with MQTT client for communication
                    loop = MissionLoop(**loop_kwargs)
                    
                    # Execute the mission
                    result = loop.run(mission)
                    
                    # Send result back
                    response_payload = {
                        'droneId': DRONE_ID,
                        'conversation_id': conversation_id,
                        'original_message': original_message,
                        'mission_id': mission_id,
                        'result': result.to_dict(),
                        'image_urls': result.photos,
                        # Matches the field sim_drone_daemon.py has always
                        # published, so the sim and hardware paths finally
                        # agree on the wire and the cloud needs one reader.
                        'video_urls': result.videos,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                    
                    # Log before publishing, not after: a publish that raises is
                    # exactly when the local log is the only copy of why the
                    # mission failed, and logging afterwards loses it to the
                    # generic handler below.
                    logger.info(
                        f"Mission completed: {result.success} "
                        f"({result.phases_completed}/{result.total_phases} phases)"
                    )
                    if not result.success:
                        # MissionResult has carried failure_reason all along and
                        # nothing logged it, so every failure read as an anonymous
                        # "0/N phases" here and "Unknown error" in the app.
                        logger.error(
                            f"Mission {mission_id} failed: "
                            f"{result.failure_reason or 'no reason recorded'} "
                            f"(summary: {result.summary}, "
                            f"actions_taken={result.actions_taken}, "
                            f"duration={result.duration_seconds:.1f}s)"
                        )

                    response_topic = f"drone/{DRONE_ID}/chat/{conversation_id}/response"
                    _mqtt_connection.publish(
                        topic=response_topic,
                        payload=json.dumps(response_payload),
                        qos=mqtt.QoS.AT_MOST_ONCE
                    )
                    
                except Exception as e:
                    import traceback
                    logger.error(f"Mission execution error: {e}")
                    logger.error(traceback.format_exc())
                    
                    # Send error response
                    error_payload = {
                        'droneId': DRONE_ID,
                        'conversation_id': conversation_id,
                        'mission_id': mission_id,
                        'result': {'success': False, 'error': str(e)},
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                    response_topic = f"drone/{DRONE_ID}/chat/{conversation_id}/response"
                    _mqtt_connection.publish(
                        topic=response_topic,
                        payload=json.dumps(error_payload),
                        qos=mqtt.QoS.AT_MOST_ONCE
                    )
            
            threading.Thread(target=execute_mission_async, daemon=True).start()
            return
        
        # Code execution
        code = data.get('code', '')
        follow_up = data.get('follow_up', False)
        
        logger.info(f"Code:\n{code}")
        
        # Execute the code with conversation context
        result = execute_code(code, conversation_id=conversation_id)
        
        # Build response payload
        response_payload = {
            'droneId': DRONE_ID,
            'conversation_id': conversation_id,
            'original_message': original_message,
            'result': result,
            'follow_up': follow_up,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Include any captured media
        if result.get('image_urls'):
            response_payload['image_urls'] = result['image_urls']
        if result.get('video_urls'):
            response_payload['video_urls'] = result['video_urls']
        
        # Publish to conversation-specific response topic
        if _mqtt_connection:
            response_topic = f"drone/{DRONE_ID}/chat/{conversation_id}/response"
            logger.info(f"Publishing response to {response_topic}")
            try:
                _mqtt_connection.publish(
                    topic=response_topic,
                    payload=json.dumps(response_payload),
                    qos=mqtt.QoS.AT_MOST_ONCE
                )
                logger.info(f"Published response successfully")
            except Exception as pub_err:
                logger.error(f"Failed to publish response: {pub_err}")
            
            # Also publish status update
            status_topic = f"drone/{DRONE_ID}/status"
            _mqtt_connection.publish(
                topic=status_topic,
                payload=json.dumps({
                    'droneId': DRONE_ID,
                    'status': 'online',
                    'lastUpdate': datetime.now(timezone.utc).isoformat(),
                    'ttl': int(time.time()) + STATUS_TTL_SECONDS
                }),
                qos=mqtt.QoS.AT_MOST_ONCE
            )
        
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.error(f"Invalid chat payload: {e}")
        logger.error(f"Raw payload (first 200 bytes): {payload[:200]!r}")
        return
    except Exception as e:
        logger.error(f"Error handling chat command: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        # Try to send error response
        if _mqtt_connection:
            try:
                conversation_id = data.get('conversation_id') if isinstance(data, dict) else None
                if not conversation_id:
                    return
                error_payload = {
                    'droneId': DRONE_ID,
                    'conversation_id': conversation_id,
                    'result': {'success': False, 'error': str(e)},
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                response_topic = f"drone/{DRONE_ID}/chat/{conversation_id}/response"
                _mqtt_connection.publish(
                    topic=response_topic,
                    payload=json.dumps(error_payload),
                    qos=mqtt.QoS.AT_MOST_ONCE
                )
            except Exception as pub_err:
                logger.error(f"Failed to publish error response: {pub_err}")


def start_video_streaming():
    """Start the video producer process."""
    global _video_process, _video_last_heartbeat
    
    if _video_process is not None and _video_process.poll() is None:
        logger.info("Video streaming already running")
        _video_last_heartbeat = time.time()
        return True
    
    logger.info("Starting video streaming...")
    video_script = DRONE_DIR / 'video_producer.py'

    if not video_script.exists():
        logger.error(f"Video producer script not found: {video_script}")
        return False

    # Prefer the venv Python (has aiortc, boto3, opencv) over sys.executable
    venv_python = DRONE_DIR / 'venv' / 'bin' / 'python'
    python_exe = str(venv_python) if venv_python.exists() else sys.executable
    logger.info(f"Using Python: {python_exe}")

    try:
        _video_process = subprocess.Popen(
            [python_exe, str(video_script)],
            cwd=str(DRONE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        _video_last_heartbeat = time.time()
        logger.info(f"Video streaming started (PID: {_video_process.pid})")
        
        # Start a thread to log output
        def log_output():
            try:
                for line in _video_process.stdout:
                    logger.info(f"[video] {line.rstrip()}")
            except:
                pass
        
        threading.Thread(target=log_output, daemon=True).start()
        return True
        
    except Exception as e:
        logger.error(f"Failed to start video streaming: {e}")
        return False


def stop_video_streaming(reason="requested"):
    """Stop the video producer process."""
    global _video_process, _video_last_heartbeat
    
    _video_last_heartbeat = 0
    
    if _video_process is None:
        logger.debug("Video streaming not running")
        return
    
    if _video_process.poll() is not None:
        logger.debug("Video process already stopped")
        _video_process = None
        return
    
    logger.info(f"Stopping video streaming ({reason})...")
    try:
        _video_process.terminate()
        try:
            _video_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Video process didn't stop gracefully, killing...")
            _video_process.kill()
            _video_process.wait()
        logger.info("Video streaming stopped")
    except Exception as e:
        logger.error(f"Error stopping video: {e}")
    finally:
        _video_process = None


def check_video_watchdog():
    """Check if video streaming should be stopped due to missing heartbeats."""
    global _video_process, _video_last_heartbeat
    
    if _video_process is None or _video_process.poll() is not None:
        return
    
    if _video_last_heartbeat == 0:
        return
    
    elapsed = time.time() - _video_last_heartbeat
    if elapsed > VIDEO_HEARTBEAT_TIMEOUT:
        logger.warning(f"Video heartbeat timeout ({elapsed:.1f}s > {VIDEO_HEARTBEAT_TIMEOUT}s)")
        stop_video_streaming(reason="heartbeat timeout - iOS app disconnected?")


def on_video_command(topic, payload, **kwargs):
    """Handle video streaming commands from iOS app."""
    global _video_last_heartbeat, _mqtt_connection

    if CONTROL_PLANE == 'gcs':
        # Video streaming uses AWS Kinesis Video Streams WebRTC - no local
        # equivalent in GCS mode (see gcs/README.md's "degraded features").
        logger.info("Video command received but unsupported in GCS mode (control_plane: gcs)")
        if _mqtt_connection:
            _mqtt_connection.publish(
                topic=f"drone/{DRONE_ID}/video/status",
                payload=json.dumps({
                    'droneId': DRONE_ID,
                    'streaming': False,
                    'error': 'video is unsupported in GCS mode',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }),
                qos=mqtt.QoS.AT_MOST_ONCE
            )
        return

    try:
        data = json.loads(payload)
        action = data.get('action', '')

        if action == 'start':
            logger.info("Video start command received")
            success = start_video_streaming()
            
            # Send response
            if _mqtt_connection:
                response_topic = f"drone/{DRONE_ID}/video/status"
                _mqtt_connection.publish(
                    topic=response_topic,
                    payload=json.dumps({
                        'droneId': DRONE_ID,
                        'streaming': success,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }),
                    qos=mqtt.QoS.AT_MOST_ONCE
                )
                
        elif action == 'stop':
            logger.info("Video stop command received")
            stop_video_streaming(reason="iOS app requested stop")
            
            # Send response
            if _mqtt_connection:
                response_topic = f"drone/{DRONE_ID}/video/status"
                _mqtt_connection.publish(
                    topic=response_topic,
                    payload=json.dumps({
                        'droneId': DRONE_ID,
                        'streaming': False,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }),
                    qos=mqtt.QoS.AT_MOST_ONCE
                )
                
        elif action == 'heartbeat':
            # Update heartbeat timestamp to keep streaming alive
            if _video_process is not None and _video_process.poll() is None:
                _video_last_heartbeat = time.time()
                logger.debug("Video heartbeat received")
            else:
                logger.debug("Video heartbeat received but not streaming")
                
        else:
            logger.warning(f"Unknown video action: {action}")
            
    except Exception as e:
        logger.error(f"Error handling video command: {e}")


def check_provisioning():
    """Check if WiFi provisioning is needed and run it."""
    service = ProvisioningService(timeout=60)
    
    if service.needs_provisioning():
        logger.info("=" * 60)
        logger.info("WIFI NOT CONFIGURED - STARTING PROVISIONING MODE")
        logger.info(f"Connect to hotspot: {service.wifi.get_hotspot_name()}")
        logger.info("Open http://192.168.4.1 in a browser or use the app")
        logger.info("=" * 60)
        
        result = service.run_provisioning()
        
        if result.success:
            logger.info("Provisioning successful!")
            # Update config with drone_id and user_id
            config['drone_id'] = DRONE_ID
            if result.user_id:
                config['user_id'] = result.user_id
                with open(config_path, 'w') as f:
                    yaml.dump(config, f)
                logger.info(f"Saved drone_id={DRONE_ID} to config.yaml")
            else:
                # The dump above is what persists it; without a user_id nothing
                # was written, and claiming otherwise sent us looking in the
                # wrong place for where a stale drone_id came from.
                logger.info("No user_id from provisioning; config.yaml not updated")
            return True
        else:
            logger.warning(f"Provisioning failed: {result.error}")
            logger.info("Will retry on next boot")
            return False
    
    return True


def get_simulated_telemetry():
    """Generate simulated telemetry for testing without PX4 hardware."""
    import math
    import random
    
    # Simulate position that drifts slowly
    base_lat = 37.7749  # San Francisco
    base_lon = -122.4194
    t = time.time()
    
    return {
        'position': {
            'latitude': base_lat + 0.0001 * math.sin(t / 30),
            'longitude': base_lon + 0.0001 * math.cos(t / 30),
            'altitude': 10.0 + 2.0 * math.sin(t / 10)
        },
        'attitude': {
            'roll': 2.0 * math.sin(t / 5),
            'pitch': 1.5 * math.cos(t / 7),
            'yaw': (t * 5) % 360 - 180  # Slow rotation
        },
        'battery': max(20, 85 - (t % 3600) / 60),  # Slowly decreasing
        'armed': False,
        'mode': 'STABILIZE'
    }


# Track if we've done initial flight controller setup
_fc_configured = False


def ensure_flight_controller_setup():
    """Ensure flight controller is configured (battery monitoring + failsafes)."""
    global _fc_configured
    
    if _fc_configured:
        return
    
    try:
        from drone_sdk import configure_battery_monitoring, configure_failsafes
        import yaml
        
        # Load config
        config_path = DRONE_DIR / 'config.yaml'
        if config_path.exists():
            with open(config_path, 'r') as f:
                cfg = yaml.safe_load(f) or {}
        else:
            cfg = {}
        
        battery_cfg = cfg.get('battery', {})
        
        # Configure battery monitoring
        logger.info("Configuring battery monitoring on flight controller...")
        configure_battery_monitoring(
            n_cells=battery_cfg.get('cells', 4),
            capacity_mah=battery_cfg.get('capacity_mah', 5000),
            low_voltage=battery_cfg.get('low_voltage_per_cell', 3.5),
            critical_voltage=battery_cfg.get('critical_voltage_per_cell', 3.3)
        )
        logger.info("Battery monitoring configured")
        
        # SAFETY LAYER 4: Configure hardware failsafes on startup
        logger.info("Configuring flight controller failsafes...")
        configure_failsafes()
        logger.info("Failsafes configured")
        
        _fc_configured = True
        
    except Exception as e:
        logger.warning(f"Could not configure flight controller: {e}")
        _fc_configured = True  # Don't retry every heartbeat


# Cache for capabilities detection (don't re-detect every heartbeat)
_cached_capabilities = None
_capabilities_checked = False


def detect_capabilities():
    """Detect drone capabilities (VLM availability, variant, Nav2 status)."""
    global _cached_capabilities, _capabilities_checked
    
    if _capabilities_checked:
        return _cached_capabilities
    
    capabilities = {
        'variant': 'unknown',
        'vlm_available': False,
        'nav2_available': False,
    }
    
    try:
        # Models dir: install layout (~/drone-api/models) or repo (drone/models)
        _models_dir = DRONE_DIR / 'models'
        if not _models_dir.exists():
            _models_dir = DRONE_DIR.parent / 'models'
        sys.path.insert(0, str(_models_dir))
        from setup_models import detect_orin_variant

        variant = detect_orin_variant()
        capabilities['variant'] = variant

        # Check for VLM model
        vlm_path = _models_dir / 'vlm.gguf'
        if vlm_path.exists():
            capabilities['vlm_available'] = True
        
        # Check for Nav2 (ROS 2)
        try:
            import rclpy
            capabilities['nav2_available'] = True
        except ImportError:
            capabilities['nav2_available'] = False
        
        logger.info(f"Detected capabilities: variant={variant}, vlm={capabilities['vlm_available']}, nav2={capabilities['nav2_available']}")
        
    except Exception as e:
        logger.warning(f"Could not detect capabilities: {e}")
    
    _cached_capabilities = capabilities
    _capabilities_checked = True
    return capabilities


def publish_heartbeat():
    """Publish heartbeat status with telemetry to indicate drone is online."""
    global _mqtt_connection
    
    if not _mqtt_connection:
        return
    
    status_topic = f"drone/{DRONE_ID}/status"
    
    # TTL: use a slightly longer window to avoid transient offline
    now_ms = int(time.time() * 1000)
    ttl = int(time.time()) + STATUS_TTL_SECONDS
    
    # Detect capabilities (cached after first call)
    capabilities = detect_capabilities()
    
    heartbeat = {
        'droneId': DRONE_ID,
        'status': 'online',
        'lastUpdate': now_ms,  # Epoch milliseconds
        'ttl': ttl,
        'armed': False,  # Default - will be updated from telemetry if available
        'capabilities': capabilities,
        'variant': capabilities.get('variant', 'unknown'),
    }
    
    # Collect telemetry from flight controller
    try:
        from drone_sdk import get_telemetry, get_battery
        telemetry = get_telemetry()
        
        # Get battery percentage - if FC returns -1, calculate from voltage
        battery_pct = telemetry.get('battery')
        if battery_pct is None or battery_pct < 0:
            # Try to calculate from voltage using battery config
            try:
                battery_data = get_battery()
                voltage = battery_data.get('voltage', 0)
                if voltage > 0:
                    # Calculate percentage from voltage
                    cell_count = _battery_config['cellCount']
                    cell_empty = _battery_config['cellEmptyVoltage']
                    cell_full = _battery_config['cellFullVoltage']
                    
                    empty_pack = cell_empty * cell_count
                    full_pack = cell_full * cell_count
                    
                    if voltage <= empty_pack:
                        battery_pct = 0
                    elif voltage >= full_pack:
                        battery_pct = 100
                    else:
                        battery_pct = int(100 * (voltage - empty_pack) / (full_pack - empty_pack))
                    
                    heartbeat['voltage'] = round(voltage, 2)
            except Exception as volt_err:
                logger.debug(f"Could not get battery voltage: {volt_err}")
        
        if battery_pct is not None and battery_pct >= 0:
            heartbeat['battery'] = battery_pct
        
        # Include armed status
        if telemetry.get('armed') is not None:
            heartbeat['armed'] = telemetry['armed']
        
        # Include flight mode
        if telemetry.get('mode') is not None:
            heartbeat['mode'] = telemetry['mode']
        
        # Include position if available
        if telemetry.get('position') is not None:
            heartbeat['position'] = telemetry['position']
        
        # Include attitude if available
        if telemetry.get('attitude') is not None:
            heartbeat['attitude'] = telemetry['attitude']
            
    except Exception as e:
        logger.warning(f"Could not collect telemetry for heartbeat: {e}")
    
    _mqtt_connection.publish(
        topic=status_topic,
        payload=json.dumps(heartbeat),
        qos=mqtt.QoS.AT_MOST_ONCE
    )
    logger.info(f"Published heartbeat (battery={heartbeat.get('battery', 'N/A')}%, armed={heartbeat.get('armed', 'N/A')})")


def on_connection_interrupted(connection, error, **kwargs):
    """Callback when connection is interrupted."""
    logger.warning(f"Connection interrupted: {error}")


def on_connection_resumed(connection, return_code, session_present, **kwargs):
    """Callback when connection is resumed."""
    logger.info(f"Connection resumed (return_code={return_code}, session_present={session_present})")
    
    # Resubscribe to topics if session wasn't present
    if not session_present:
        resubscribe_topics(connection)


def resubscribe_topics(connection):
    """Resubscribe to all topics."""
    # Subscribe to command topic (legacy)
    command_topic = f"drone/{DRONE_ID}/command"
    logger.info(f"Subscribing to {command_topic}")
    subscribe_future, _ = connection.subscribe(
        topic=command_topic,
        qos=mqtt.QoS.AT_LEAST_ONCE,
        callback=on_command
    )
    subscribe_future.result()
    logger.info(f"Subscribed to {command_topic}")
    
    # Subscribe to chat command topic (wildcard for any conversation)
    chat_topic = f"drone/{DRONE_ID}/chat/+/command"
    logger.info(f"Subscribing to {chat_topic}")
    subscribe_future, _ = connection.subscribe(
        topic=chat_topic,
        qos=mqtt.QoS.AT_LEAST_ONCE,
        callback=on_chat_command
    )
    subscribe_future.result()
    logger.info(f"Subscribed to {chat_topic}")
    
    # Subscribe to video control topic
    video_topic = f"drone/{DRONE_ID}/video/command"
    logger.info(f"Subscribing to {video_topic}")
    subscribe_future, _ = connection.subscribe(
        topic=video_topic,
        qos=mqtt.QoS.AT_LEAST_ONCE,
        callback=on_video_command
    )
    subscribe_future.result()
    logger.info(f"Subscribed to {video_topic}")

    # Thing Shadow (offline-safe decommission/reset) is an AWS IoT-specific
    # service - there's no equivalent to subscribe to on a local GCS
    # mosquitto broker, so this whole block is AWS-only. See gcs/README.md's
    # "degraded features" section: wifi/battery-config/factory-reset are
    # store-only (REST) in GCS mode rather than live-pushed via shadow.
    if CONTROL_PLANE == 'aws':
        topics = _shadow_topics()
        logger.info(f"Subscribing to {topics['get_accepted']}")
        subscribe_future, _ = connection.subscribe(
            topic=topics["get_accepted"],
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_shadow_get_accepted
        )
        subscribe_future.result()

        logger.info(f"Subscribing to {topics['get_rejected']}")
        subscribe_future, _ = connection.subscribe(
            topic=topics["get_rejected"],
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_shadow_rejected
        )
        subscribe_future.result()

        logger.info(f"Subscribing to {topics['delta']}")
        subscribe_future, _ = connection.subscribe(
            topic=topics["delta"],
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_shadow_delta
        )
        subscribe_future.result()

        logger.info(f"Subscribing to {topics['update_rejected']}")
        subscribe_future, _ = connection.subscribe(
            topic=topics["update_rejected"],
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_shadow_rejected
        )
        subscribe_future.result()

    # Initial pull of desired state (AWS-only, see guard above)
    if CONTROL_PLANE == 'aws':
        request_shadow_get()


def _build_aws_mqtt_connection():
    """AWS IoT Core, mTLS with device certs. Unchanged from before the GCS
    control-plane switch was added."""
    if not IOT_ENDPOINT:
        logger.error("IoT endpoint not configured in config.yaml!")
        logger.info("Please configure iot_endpoint in config.yaml")
        sys.exit(1)

    cert_dir = DRONE_DIR / 'certs'
    root_ca = cert_dir / 'root-ca.pem'
    private_key = cert_dir / 'private.key'
    certificate = cert_dir / 'device.pem'

    for path in [root_ca, private_key, certificate]:
        if not path.exists():
            logger.error(f"Missing certificate: {path}")
            sys.exit(1)

    # Initialize event loop group for SDK v2
    event_loop_group = io.EventLoopGroup(1)
    host_resolver = io.DefaultHostResolver(event_loop_group)
    client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

    client_id = f"{DRONE_ID}-{int(time.time())}"
    logger.info(f"Creating MQTT connection with client_id: {client_id}")

    mqtt_connection = mqtt_connection_builder.mtls_from_path(
        endpoint=IOT_ENDPOINT,
        port=8883,
        cert_filepath=str(certificate),
        pri_key_filepath=str(private_key),
        ca_filepath=str(root_ca),
        client_bootstrap=client_bootstrap,
        client_id=client_id,
        clean_session=False,
        keep_alive_secs=120,
        ping_timeout_ms=30000,  # 30 second ping timeout (was defaulting to ~10s)
        on_connection_interrupted=on_connection_interrupted,
        on_connection_resumed=on_connection_resumed
    )

    logger.info(f"Connecting to {IOT_ENDPOINT}...")
    connect_future = mqtt_connection.connect()
    connect_future.result()  # Wait for connection
    logger.info("Connected to AWS IoT Core!")
    return mqtt_connection


def _build_gcs_mqtt_connection():
    """A local Ground Control Station's mosquitto broker instead of AWS IoT
    Core - plain TCP (or TLS with a self-signed CA) and username/password
    auth instead of mTLS certs. See gcs/README.md and this file's
    config.yaml.example `gcs:` section. Returns drone/common/gcs_mqtt.py's
    PahoMqttAdapter, which exposes the same .publish/.subscribe/.disconnect
    surface as the awsiot mqtt_connection above, so every call site below
    (resubscribe_topics, on_command, publish_heartbeat, ...) works unmodified
    regardless of which control plane is active."""
    from gcs_mqtt import PahoMqttAdapter  # local import: awscrt/AWS IoT SDK not needed for this path

    host = GCS_CONFIG.get('mqtt_host')
    port = int(GCS_CONFIG.get('mqtt_port', 1883))
    if not host:
        logger.error("control_plane: gcs but gcs.mqtt_host is not configured in config.yaml!")
        sys.exit(1)

    client_id = f"{DRONE_ID}-{int(time.time())}"
    logger.info(f"Creating GCS MQTT connection to {host}:{port} with client_id: {client_id}")

    connection = PahoMqttAdapter(
        host=host, port=port, client_id=client_id,
        username=DRONE_ID, password=GCS_CONFIG.get('auth_token'),
        tls=bool(GCS_CONFIG.get('mqtt_tls', False)), ca_cert=GCS_CONFIG.get('mqtt_ca_cert'),
        on_connection_interrupted=on_connection_interrupted,
        on_connection_resumed=on_connection_resumed,
    )

    logger.info(f"Connecting to GCS mosquitto broker at {host}:{port}...")
    connect_future = connection.connect()
    connect_future.result()
    logger.info("Connected to GCS!")
    return connection


def build_mqtt_connection():
    """Dispatches on CONTROL_PLANE (config.yaml's `control_plane: aws|gcs`,
    default aws)."""
    if CONTROL_PLANE == 'gcs':
        return _build_gcs_mqtt_connection()
    return _build_aws_mqtt_connection()


def main():
    """Main daemon loop."""
    global _mqtt_connection

    # Load battery configuration from disk
    _load_battery_config()

    # Step 1: Check WiFi provisioning
    if not check_provisioning():
        logger.info("No WiFi configured, exiting. Power cycle to retry setup.")
        sys.exit(0)

    # Step 2: Connect to the configured control plane (AWS IoT Core, or a
    # local GCS - see build_mqtt_connection() above).
    mqtt_connection = build_mqtt_connection()

    # Store global reference
    _mqtt_connection = mqtt_connection
    
    # Enable MQTT log streaming now that we're connected
    mqtt_log_handler.set_mqtt_connection(mqtt_connection)
    logger.info("Log streaming enabled")
    
    # Subscribe to topics
    resubscribe_topics(mqtt_connection)

    # If we previously performed a factory reset, clear desired.decommission
    # now that we're back online (Thing Shadow, AWS-only - see guard above).
    if CONTROL_PLANE == 'aws':
        ack_factory_reset_if_pending()
    
    logger.info(f"Drone {DRONE_ID} ready and listening for commands...")
    
    # Heartbeat interval (seconds)
    HEARTBEAT_INTERVAL = 5
    last_heartbeat = 0
    
    # Keep running and publish heartbeats
    try:
        while True:
            current_time = time.time()
            if current_time - last_heartbeat >= HEARTBEAT_INTERVAL:
                publish_heartbeat()
                last_heartbeat = current_time
            
            # Check video watchdog (stop streaming if iOS app disconnected)
            check_video_watchdog()
            
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        stop_video_streaming(reason="daemon shutdown")
        disconnect_future = mqtt_connection.disconnect()
        disconnect_future.result()


if __name__ == "__main__":
    main()
