# e2e tests — all 4 drone types

One place to test **quadcopter**, **rover**, **fixed-wing** (all three via the
`DroneOperator` app) and **phrover** (the iPhone-brained WAVE ROVER, via the public
`PhroverOperator` app + `RoverNav`/`PhroverKit`/`PhroverCloud` in the sibling `presidio-sdk`
repo — see `../../sdk`) — runnable headlessly for CI, or from a real phone/simulator by
a human.

See `scenarios.yaml` for the exact mission text and success criteria per type — the
headless regression and the phone UI tests assert the same thing.

## Headless regression (no phone, no AWS needed for the fast tier)

```bash
cd <repo-root>                              # so `eco` is importable
python3 -m eco.e2e.run_e2e --type all --tier fast
```

This runs:
- the **vehicle-behavior gate** — `eco/drone/training/sim_validate.py` (kinematics/jerk
  regression for quad/rover/fixed-wing) + `test_vehicle_class.py` + `test_l5_parity.py`.
- a **mocked app-contract check** per type (`harness/mock_cloud.py`,
  `harness/mock_rover_converse.py`) — proves the harness sends the right mission and
  interprets the response correctly, without touching AWS.
- for phrover, `RoverNavTests` in the sibling `presidio-sdk` repo (on-device navigation,
  offline) — run via `xcodebuild test` against an iOS Simulator, since RoverNav now
  shares a package with ARKit-dependent code that can't build on plain macOS.

Writes `scorecard.json` next to this file (gitignored — CI uploads it as an artifact).

### Live tier — against a real drone (Presidio Sim or hardware) + the real AWS dev stack

Bring a sim vehicle Online first (Presidio Sim = the project's Godot engine, see
`eco/drone/sim/BRINGUP_MAC.md`; works on Linux or macOS, Godot itself is cross-platform):

```bash
DRONES="sim-quadcopter-e2e:quadcopter" ENV=plaza bash eco/drone/sim/launch_fleet_mac.sh
# repeat for sim-rover-e2e:rover, sim-fixedwing-e2e:fixedwing
```

Then, with `ISHMAEL_API_BASE` / `ISHMAEL_API_TOKEN` / `ISHMAEL_IOT_ENDPOINT` set (same env
vars `eco/sim/mobile/app_client.py` already reads):

```bash
python3 -m eco.e2e.run_e2e --type all --tier live
```

phrover has no cloud "brain" step to bring up — its live-tier dialog check just needs
`ISHMAEL_API_BASE`/`ISHMAEL_API_TOKEN` pointed at the deployed stack; its navigation check
is the same offline `RoverNavTests` run as the fast tier.

## From a phone or simulator (a human, or CI on a Mac/emulator runner)

```bash
eco/e2e/run_phone.sh --type quadcopter --platform ios
eco/e2e/run_phone.sh --type rover      --platform android
eco/e2e/run_phone.sh --type fixedwing  --platform ios
eco/e2e/run_phone.sh --type phrover    --platform ios      # starts a mock chassis for you
```

This drives the **real app** (build + install + run its XCUITest/UIAutomator suite),
screenshots each milestone, and asserts the mission's response arrives — see
`eco/sim/mobile/ios_sim_driver.md` for what's actually happening under the hood on iOS.

### Fully manual (a real device, no automation)

1. Bring a sim vehicle Online (see the live-tier steps above), or use real hardware.
2. Build/install the right app — DroneOperator for quad/rover/fixed-wing, PhroverOperator
   (in the sibling `presidio-sdk` repo, `sdk/examples/PhroverOperator`) for phrover — and
   sign in as the user that owns the drone.
3. Open the drone, send the mission from `scenarios.yaml`, confirm the photo/response
   arrives.
4. For phrover with no physical WAVE ROVER chassis: run
   `python3 eco/e2e/harness/mock_esp32.py --port 8080` on your laptop and point
   `RoverConfig`'s host at it (same WiFi network) instead of a real base.

## CI

- `e2e-fast.yml` — the fast tier above, on every push/PR. No creds needed.
- `ui-sim.yml` — `run_phone.sh` against an iOS Simulator + Android emulator, on PRs
  touching the client apps.
- `e2e-live.yml` — the live tier, nightly + manual dispatch, on a self-hosted macOS
  runner (needs a Mac for Presidio Sim / the iOS Simulator).
