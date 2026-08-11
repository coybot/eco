# Run in simulation (SITL)

You can run every SDK command against ArduPilot SITL (Software-In-The-Loop).
This is the fastest way to try the API without hardware, and it's also a
good safety net before flying new code on a real aircraft.

## What you'll need

- Python 3.10+
- ArduPilot SITL — the canonical setup is documented at
  [ardupilot.org/dev](https://ardupilot.org/dev/docs/sitl-with-gazebo.html).
  At minimum you need `sim_vehicle.py` from the ArduPilot tree.

No GPU, no Presidio hardware.

## 1. Start the simulator

In one terminal:

```bash
sim_vehicle.py -v ArduCopter --console --map
```

SITL exposes a MAVLink TCP listener on `127.0.0.1:5760` by default.

## 2. Point the SDK at SITL

In another terminal:

```bash
pip install -e .
export PRESIDIO_SDK_SERIAL_PORT=tcp:127.0.0.1:5760
```

Any URL starting with `tcp:`, `tcpin:`, `udp:`, `udpin:`, or `udpout:` is
treated as a network endpoint — the SDK skips the serial-device check and
hands the URL directly to `pymavlink`.

## 3. Fly

```python
import time
import drone.common.drone_sdk as drone

drone.takeoff(2.0)        # 2 m AGL
time.sleep(5)
drone.land()
drone.disconnect()
```

The exact same script flies real hardware — you only change the env var.

A runnable version is in [`examples/sitl/`](../examples/sitl/).

## What this covers (and what it doesn't)

SITL gives you:

- A full ArduPilot stack: arm/disarm, modes, takeoff/land, position and
  velocity control, telemetry — all behave like the real thing.
- A safe place to iterate on flight logic before touching real props.

SITL does **not** give you cameras, perception, or a 3D world. For that,
see [Run in Isaac Sim](isaac-sim.md).

## The other simulator in this repo

This page is about SITL against the raw SDK. There's a separate, larger
kinematic simulator (`drone/sim/`) used to validate the L5 fleet-autonomy
controller across a 16-scenario benchmark — see
[`l5-benchmark.md`](l5-benchmark.md) if that's what you're looking for.
