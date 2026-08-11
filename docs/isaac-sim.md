# Run in Isaac Sim

[Isaac Sim](https://developer.nvidia.com/isaac-sim) gives you a 3D world
with cameras and physics. Pair it with ArduPilot SITL and this repo's own
ROS 2 package (`drone/ros2_ws/src/presidio_drone/`), and you can fly the
same autonomy stack you'd run on real hardware — without the hardware.

This page covers the **glue**, not Isaac Sim itself. NVIDIA's docs are the
source of truth for installation and core workflows.

> Isaac Sim requires an NVIDIA RTX GPU and ~50 GB of disk. If you just want
> to try the SDK API, the lighter-weight [SITL guide](simulation.md) is a
> better starting point.

## What you'll need

- **Isaac Sim** (4.0+ recommended). Install via the
  [Omniverse launcher](https://docs.omniverse.nvidia.com/isaacsim/latest/install_workstation.html).
- **ROS 2 Humble** (or Jazzy). Set up with
  [Nav2](https://docs.nav2.org/getting_started/index.html).
- **ArduPilot SITL** with the
  [Isaac plugin](https://ardupilot.org/dev/docs/sitl-with-isaac.html)
  or `mavros` bridge.
- This repo's `drone/ros2_ws/src/presidio_drone/` ROS 2 package.

## How the pieces fit together

```
┌──────────────┐   MAVLink    ┌──────────────┐   ROS 2    ┌──────────────┐
│  Your code   │ ───────────► │ ArduPilot    │ ─────────► │  Nav2 +      │
│ (drone_sdk)  │              │   SITL       │            │ presidio_drone│
└──────────────┘              └──────┬───────┘            └──────┬───────┘
                                     │                           │
                                     │ physics + cameras         │
                                     ▼                           ▼
                              ┌──────────────────────────────────────┐
                              │           Isaac Sim                  │
                              │   (USD scene, RGBD camera, IMU)      │
                              └──────────────────────────────────────┘
```

You write Python that calls `drone.common.drone_sdk` (just like real
hardware). It talks MAVLink to ArduPilot SITL, which is driven by Isaac
Sim's physics. The `presidio_drone` ROS 2 node bridges Nav2 commands into
MAVLink velocity setpoints.

## 1. Bring up the simulator

Launch Isaac Sim, open a USD scene with a quadrotor + RGBD camera, and start
the ArduPilot SITL bridge. NVIDIA's
[Pegasus Simulator](https://github.com/PegasusSimulator/PegasusSimulator)
is a good starting scene if you don't want to author one.

## 2. Connect the SDK

```bash
export PRESIDIO_SDK_SERIAL_PORT=tcp:127.0.0.1:5760
```

From here, every example in [`examples/`](../examples/) works against the
simulator. Try `arm_takeoff_land.py` first — if that flies, your bridge is
good.

## 3. Bring up Nav2

Build and launch the ROS 2 package:

```bash
cd drone/ros2_ws
colcon build --packages-select presidio_drone
source install/setup.bash
ros2 launch presidio_drone bringup.launch.py
```

`presidio_drone` subscribes to Nav2's `/cmd_vel` and forwards it as MAVLink
velocity commands. You can now send Nav2 goals from RViz or
`ros2 action send_goal` and watch the drone move in Isaac.

## Why this is worth the setup

- **Catch bugs before flight.** Real drones are expensive and slow to
  iterate against. SIL eliminates the most common autonomy bugs (frame
  conventions, mode-switch races, velocity-clamp surprises) on a laptop.
- **Reproducibility.** A USD scene + a launch file is a deterministic test
  fixture.
- **Same API as the real thing.** `drone_sdk` calls don't change between
  sim and hardware. What flies in Isaac flies on the real drone.

## Caveats

- Isaac Sim version churn is real. NVIDIA breaks APIs on a roughly
  quarterly cadence — pin to a known-good version in your environment.
- Sim cameras are too clean. Real-world perception failures (motion blur,
  rolling shutter, lens flare, low light) usually need photoreal scenes or
  domain randomization to surface in sim.

## Next steps

- Run the [SITL guide](simulation.md) first if you haven't already — it
  isolates the MAVLink connection from the rest of the stack.
- Browse [`examples/`](../examples/) for runnable starting points.
