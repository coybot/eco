# SITL (Software-In-The-Loop) example

Run the drone SDK against a simulated ArduPilot copter — no drone, no GPU.

## What you'll need

- Python 3.10+ (already required by this package)
- ArduPilot SITL: see <https://ardupilot.org/dev/docs/sitl-with-gazebo.html> for
  the canonical setup. The minimum is `sim_vehicle.py` from the ArduPilot tree.

## Start the simulator

In one terminal:

```bash
sim_vehicle.py -v ArduCopter --console --map
```

This opens a MAVLink TCP listener on `127.0.0.1:5760` by default.

## Talk to it from the SDK

In another terminal:

```bash
export PRESIDIO_SDK_SERIAL_PORT=tcp:127.0.0.1:5760
python fly_sitl.py
```

`fly_sitl.py` is a minimal arm/takeoff/hover/land that uses the same SDK
calls you'd run on real hardware — the only thing that changes is the
`PRESIDIO_SDK_SERIAL_PORT` env var.

## Why this works

`drone.common.drone_sdk._connect()` recognizes URLs starting with `tcp:`,
`tcpin:`, `udp:`, `udpin:`, or `udpout:` and skips the serial-device check.
Everything downstream (arm, takeoff, velocity commands, telemetry) is
pymavlink under the hood and is transport-agnostic.

## See it exercised against the L5 benchmark too

`drone/sim/sitl_l5.py` flies the classical L5 reactive controller (not this
example's raw SDK calls) through the same real ArduPilot SITL, on both
ArduCopter and ArduRover — see [`docs/l5-benchmark.md`](../../docs/l5-benchmark.md).

## Next step

For a perception-in-the-loop sim with cameras, ROS 2, and Nav2, see
[`docs/isaac-sim.md`](../../docs/isaac-sim.md).
