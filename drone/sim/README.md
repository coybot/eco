# Sim hosts

Godot is the simulator. Isaac Sim has been removed (see the end of this file);
the only other sim code left is the headless kinematic model CI runs.

## Godot 4 — what the environments run on

`drone/sim/godot/` is a self-contained Godot 4 project with sixteen
environments, including three built from real-world public-domain data
(`manhattan`, `manhattan_photo`, `baylands`). It needs no AWS account, no
drone and no network, and every asset is committed, so a plain clone runs:

```bash
godot --path drone/sim/godot -- --fleet= --env=baylands --ipc-port=9978 --seed=0
```

It speaks newline-delimited JSON over TCP (`scripts/ipc_server.gd`), and
`godot_engine.py` also serves it on a Unix socket, so `engine_client.py` and
`sim_drone_daemon.py` drive it either way. `launch_fleet_mac.sh` (this Mac) and
`godot_launch_fleet.py` (a Linux host) bring up Godot plus one daemon per drone.

Dataset builders for the real-world scenes live in `tools/` — USGS 3DEP
elevation, USDA NAIP imagery, USGS National Map tiles, OpenStreetMap vectors
and CC0 facade photographs from ambientCG. All public domain or CC0, no API
key. See the Simulator section of the repository README for how to add a
scene of your own.

Further reading: `README_FIXEDWING.md` (the fixed-wing model and its
perception gates), `README_SAR_DEMO.md`, `BRINGUP_MAC.md`.

## Headless kinematic sim (CI)

`team_world.py`, `scenario.py`, `scorecard.py`, `comms.py`, `localization.py`,
`sensor_model.py` and `smart_layer.py` are a numpy-only kinematic model of
quad, rover, fixed-wing and Crazyflie teams, with scripted injects (comms
blackout, GPS loss, ...). No rendering. CI's `e2e-fast` behaviour gate runs on
it (`drone/common/tests/test_l5_parity.py` and friends), because Godot cannot
render cameras headless on a CI runner, and it is the only place the Crazyflie
is simulated. Replace that gate before removing it.

## Isaac Sim — removed

The Isaac path (hoopoe GPU host) is gone: `isaac_vehicle.py`, `sim_engine.py`,
`fleet_worker.py`, `fleet_bridge.py`, `fleet_mqtt.py`, `launch_fleet.py`,
`sim_dataset_recorder.py`, `BRINGUP.md`, the `ishmael/` test director and the
Isaac render scripts `drone/training/render_session1.py` and
`render_fixedwing_demo.py`. `sim_sdk.py` keeps the non-Isaac helpers
(`latlon_to_xy`, `xy_to_latlon`, `FrameBus`, HOME constants).
`drone/training/record_comparison.py` stays because `local_course.py` imports
its courses; its own `main()` rendered through Isaac and no longer runs.
Git history has the rest.
