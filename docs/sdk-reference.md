# SDK reference (`drone.common.drone_sdk`)

Flat function-per-call API over MAVLink/pymavlink, safe to import directly
(`import drone.common.drone_sdk as drone`) or use flat on-device (installed
files are copied flat into `~/drone-api/`, see
[`install-companion.md`](install-companion.md)). See
[`examples/`](../examples/) for runnable scripts.

## Connection & config

| Function | Signature | What it does |
|---|---|---|
| `set_config_path` | `(path)` | Override the directory where `config.yaml`, `certs/`, and logs live — for running as an installed package with config elsewhere. Equivalent to the `COYBOT_SDK_CONFIG_DIR` env var. |
| `disconnect` | `()` | Close the MAVLink connection and reset state. Thread-safe. |

Connection itself is lazy and implicit — the first SDK call that needs the
flight controller connects automatically, reading `serial_port`/`baud_rate`
from `config.yaml` (or `COYBOT_SDK_SERIAL_PORT`/`COYBOT_SDK_BAUD_RATE` env
overrides — see [`quickstart-aws.md`](quickstart-aws.md)'s SITL note and
[`config-reference.md`](config-reference.md)).

## Arm / disarm / flight

| Function | Signature | What it does |
|---|---|---|
| `arm` | `()` | Arm the drone motors. Returns `True` only if actually armed. |
| `disarm` | `()` | Disarm the drone motors — **in-air use is your responsibility**; see `safe_disarm()`. |
| `safe_disarm` | `()` | Disarm — only when on the ground (relative altitude < 1 m). Use this for ground disarm from generated/LLM code; `daemon.py`'s safety gate blocks raw `disarm()` calls by regex. |
| `takeoff` | `(altitude_m)` | Take off to the given altitude. Arms automatically if needed. Altitude is clamped to `MIN_ALTITUDE..MAX_ALTITUDE`. |
| `land` | `()` | Set `LAND` mode; the flight controller handles descent and auto-disarm. |
| `goto` | `(lat, lon, alt, max_alt)` | Fly to GPS coordinates. |
| `set_velocity` | `(vx, vy, vz)` | Set velocity in body frame. |
| `set_yaw` | `(angle_deg, relative)` | Set yaw angle in degrees. |
| `wait` | `(seconds)` | Wait for the given duration. |

## Telemetry

| Function | Signature | Returns |
|---|---|---|
| `get_position` | `()` | `(lat, lon, alt_m)` |
| `get_attitude` | `()` | `(roll, pitch, yaw)` in degrees |
| `get_battery` | `()` | dict: voltage, remaining %, current |
| `get_flight_mode` | `()` | current mode as a string |
| `is_armed` | `()` | armed state, from a fresh heartbeat |
| `get_telemetry` | `()` | everything above in one call |
| `get_ceiling_distance` | `()` | distance to ceiling via upward-facing rangefinder, in meters |

## Safety layers

| Function | Signature | What it does |
|---|---|---|
| `start_ceiling_guard` | `(min_clearance=0.5)` | Background thread that freezes altitude if ceiling clearance drops below `min_clearance` (m) — for indoor flight. |
| `stop_ceiling_guard` | `()` | Stop it. |
| `configure_failsafes` | `()` | Configures flight-controller failsafes for autonomous operation (safety layer 3). |
| `configure_battery_monitoring` | `(n_cells, capacity_mah, low_voltage, critical_voltage)` | Configures PX4/ArduPilot battery monitoring parameters. |
| `setup_drone` | `()` | Runs initial setup — battery monitoring + other defaults. Call once per drone. |

## Motors, camera, photos

| Function | Signature | What it does |
|---|---|---|
| `motor_test` | `(motor_num=None, throttle_pct=15, duration_sec=2)` | Spin one motor (1–4) or all sequentially, without arming. **Props off.** |
| `release_camera` | `()` | Release the camera so another process (e.g. video streaming) can use it. |
| `capture_photo` | `(save_path, upload)` | Capture a photo, optionally upload it. |
| `look_around` | `(directions)` | Rotate to N evenly-spaced headings, capturing a photo at each. |
| `upload_photo` | `(local_path, conversation_id)` | Upload a photo and return its public URL — to S3 in AWS mode, to the GCS's local image store in GCS mode. Same call either way; the backend is selected by `control_plane` (see [`control-plane.md`](control-plane.md)). |

## Errors

Connection failures raise `ConnectionError` with a message telling you what
to check (USB/serial path, or — for SITL — to set `COYBOT_SDK_SERIAL_PORT`).
There's no other custom exception hierarchy; MAVLink timeouts and the like
surface as whatever `pymavlink` raises.
