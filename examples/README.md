# Examples

Runnable examples that demonstrate the drone SDK (`drone.common.drone_sdk`).

## Setup

```bash
pip install -e .
```

For the camera example, install one of the camera extras:

```bash
pip install -e ".[camera-oak]"        # OAK-D Lite
pip install -e ".[camera-realsense]"  # Intel RealSense D435i
pip install -e ".[all]"               # Both
```

Copy `drone/common/config.yaml.example` to `config.yaml` in the directory
you run the example from, and edit `serial_port` to match your flight
controller (a stable `/dev/serial/by-id/...` path is recommended over
`/dev/ttyACM*`).

## Examples

- `motor_test.py` — Spin each motor in turn (props off!).
- `arm_takeoff_land.py` — Minimal flight cycle: arm, take off to 2 m, hover, land.
- `camera_capture.py` — Capture a single frame from an OAK-D Lite or RealSense.
- `sitl/` — Same arm/takeoff/land cycle, but against ArduPilot SITL. No
  hardware required — useful for trying the API before you have a drone.

## Safety

The flight examples in this directory run against real hardware. Read the
source before running and keep manual override available. The `sitl/`
example is the exception — it talks to a simulator, so it's safe to run
anywhere.
