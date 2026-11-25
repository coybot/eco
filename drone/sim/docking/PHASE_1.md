# Phase 1: improve the simulator

Branch `docking/phase1` (local, not pushed), from eco `main` @ `dfc4e4e`.

## 1. How the simulator worked before this phase

The repo has three simulators. The Godot project (`drone/sim/godot`) renders scenes for
camera work. The SITL harnesses fly real ArduPilot or PX4 firmware. Neither is headless
numpy, and neither models the physics this task needs. The simulator in scope is the
**headless kinematic sim** that CI's behaviour gate runs. It consists of `team_world.py`,
`vehicle_class.py`, `sensor_model.py`, `localization.py`, `comms.py`, `scenario.py`,
`scorecard.py` and `smart_layer.py`.

**Vehicles.** `vehicle_class.py` holds one frozen `VehicleClass` descriptor per kind:
`quadcopter`, `rover`, `fixedwing`, `vtol` and `crazyflie`, plus registered extras. A
descriptor is a capability list: kinematics type, sensing modality, radius, speed and
yaw-rate caps, ceiling, payload, comms and localisation profiles, and length scales.
**There is no mass, inertia, thrust or drag anywhere.** The existing `quadcopter` is
radius 0.15 m (0.30 m across), 3.0 m/s, 0.5 kg payload.

**Physics.** `KinematicWorld.integrate` takes a velocity command, clips it to the class
limits, and integrates position with forward Euler. Three motion models exist:

- holonomic 3-D (quadcopter, Crazyflie): `[vx, vy, vz, yaw_rate]` in body frame;
- unicycle 2-D (rover);
- Dubins airplane (fixed-wing, VTOL): speed clamped to `[min, max]`, yaw rate limited
  by bank as ~1/v, climb limited by a flight-path angle.

The simulator has no forces, attitude, motor dynamics or aerodynamics. Wind is a constant
drift velocity added to every vehicle (the `wind_shift` inject). Obstacles are
axis-aligned boxes. A collision is a surface distance below radius plus pad.

**Sensors.** Each class casts rays against the boxes: a 45-ray depth grid, a 72-ray
lidar, or the 50-ray Crazyflie hybrid. Clearance can be:

- `ideal`: a true-surface oracle;
- `realistic`: the nearest return;
- `reconstructed`: segments joined between adjacent returns.

Options exist for uniform range noise and dropout, and per-group impairment in
`sensor_model.py`: range noise, dropout, whole-frame dropout, monocular scale bias, and
latency through a ring buffer. `localization.py` perturbs the believed position under GPS
loss or spoofing. `comms.py` models range, line of sight, jamming and loss. Controllers
get an `Observation` built from these. The neighbour list, however, is computed from true
relative positions.

**Timing.** One fixed tick, `dt = 0.1 s` (10 Hz) by default. Each tick runs in this order:
localisation update, comms delivery, observation, controller, integration, collision
count. Every random stream is seeded, so runs are deterministic and need no GPU.

## 2. What was added

A new package, `drone/sim/flight/`. It is pure numpy, every name carries an SI unit
suffix, and its fixed step is 400 Hz (`physics_dt_s 0.0025`) with 100 Hz guidance:

| module | feature |
|---|---|
| `frames.py` | world ENU / body FLU conventions, quaternions, Euler helpers |
| `rigid_body.py` | 6-DOF rigid body: constant-acceleration translation update (exact for constant force), Euler's equations with the gyroscopic term, exact exponential-map attitude update; tracks specific force for the IMU; mass and inertia can change mid-flight |
| `aero.py` | quadratic body drag, isotropic or per body axis; fixed-wing lift, drag, side force and moments from stability derivatives, with stall (linear lift blending into flat-plate lift past the stall angle) |
| `actuators.py` | motor bank with first-order lag (separate spin-up and spin-down constants) and hard saturation; multirotor mixer that gives up yaw first, then collective, then scales roll and pitch; pusher propeller with thrust falling off with airspeed; servos with lag, rate limit and travel limit (in `fixedwing.py`) |
| `wind.py` | steady wind (optional power-law shear), spatially coherent frozen von Kármán turbulence (random Fourier modes, two fields for the MIL-HDBK-1797 low-altitude anisotropy), 1-cosine discrete gusts, pluggable local sources (used in Phase 3 for airflow near the mothership) |
| `sensors.py` | generic channel with rate, delay plus jitter, white noise, random-walk bias, independent and burst dropouts; measurements carry the time taken. Built from it: IMU, AHRS output, GNSS (optional common-mode error shared between receivers), barometer, pitot, a fiducial camera with FOV, range limits and range-squared noise, and a radio link for arbitrary payloads |
| `estimation.py` | delayed-measurement Kalman filter: rolls back to a measurement's timestamp, updates, replays; innovation gating |
| `multirotor.py` | multirotor physics; cascaded controller (thrust vector with tilt and thrust limits and vertical priority, then quaternion attitude P, body-rate PID, mixer); IMU/GNSS navigation filter; `QuadStack` bundles them. **Controllers read only sensor outputs.** |
| `fixedwing.py` | fixed-wing 6-DOF with servos, pusher and attachable or releasable payloads (mass, inertia, weight moment, drag); autopilot with airspeed to throttle, altitude to pitch to elevator, line and orbit vector-field path following, course to bank to aileron, rudder turn coordination. Reads only sensors |
| `randomize.py` | seeded parameter distributions (uniform, truncated normal, lognormal, choice) |
| `batch.py` | headless multiprocess batch runner. Rows are sorted by seed and floats written at full precision, so the CSV is byte-identical for the same seeds whatever the worker count. A sidecar JSON records versions, platform and commit. Every column name must carry a unit suffix |
| `dynamic_world.py` | `DynamicWorld`, a drop-in `TeamWorld` backend. The existing `quadcopter` and `fixedwing` classes fly on the rigid-body models with their autopilots, using the same action contracts. Other classes fall through to the kinematic integrator. Opt-in only: `ScenarioRunner(..., backend="dynamic")`. The default stays `KinematicWorld` |

All vehicle and sensor numbers are in one file, `drone/sim/docking/vehicles.yaml`. In
this phase it holds:

- the small quadcopter (the subject of the task, used by the checks);
- `fixedwing_reference`: the published Aerosonde coefficients from Beard & McLain,
  "Small Unmanned Aircraft", appendix E;
- estimated rigid-body numbers for the existing `quadcopter` class, used only by
  `DynamicWorld`.

## 3. How it was checked

`python3 drone/sim/flight/checks_phase1.py` takes about 20 s on 8 workers on macOS
(Python 3.9, numpy 2.0). It was repeated headless on Linux with identical results. Results are in
`results/phase1/checks.csv`. The checks measure true state, as checks should. The
controllers under test still read only sensors.

| check | case | measured | expected | error | tolerance | result |
|---|---|---|---|---|---|---|
| E1 hover thrust = weight (N) | 1.0 kg | 9.8113 | 9.80665 | 0.05 % | 1 % | PASS |
| E1 hover thrust = weight (N) | 1.4 kg | 13.7358 | 13.7293 | 0.05 % | 1 % | PASS |
| E2 trim tilt = atan(D/W) (deg) | 5 m/s | 4.531 | 4.518 | 0.013 deg | 1 deg | PASS |
| E2 trim tilt = atan(D/W) (deg) | 10 m/s | 17.442 | 17.439 | 0.003 deg | 1 deg | PASS |
| E2 trim tilt = atan(D/W) (deg) | 15 m/s | 35.188 | 35.187 | 0.001 deg | 1 deg | PASS |
| E2 trim tilt = atan(D/W) (deg) | 20 m/s | 51.385 | 51.387 | 0.003 deg | 1 deg | PASS |
| E3 level-out deceleration = D/m (fit over 2 s) | 12 m/s | | | 0.02 % | 2 % | PASS |
| E3 level-out deceleration = D/m (fit over 2 s) | 18 m/s | | | 0.13 % | 2 % | PASS |
| E3b speed vs closed form v1/(1+k v1 t) | 12 m/s | | | 0.06 % of v1 | 1 % | PASS |
| E3b speed vs closed form v1/(1+k v1 t) | 18 m/s | | | 0.03 % of v1 | 1 % | PASS |
| E4 free fall energy conserved, spinning, no drag (J) | | 4927.83 | 4927.83 | 3.5e-10 | 1e-9 | PASS |
| E4b free fall with drag: energy lost = drag work (J) | | 981.956 | 981.956 | 1.7e-9 | 0.5 % | PASS |
| E5 batch reproduces from seeds | 16 seeds, 1 vs 8 workers, twice | 1 unique hash | 1 | | | PASS (other seeds give a different hash) |
| S1 turbulence sigma_u (m/s) | moderate, 50 m | 2.409 | 2.459 | -2.0 % | 15 % | PASS |
| S1 turbulence sigma_w (m/s) | moderate, 50 m | 1.534 | 1.543 | -0.6 % | 15 % | PASS |
| S1 turbulence u PSD vs von Kármán | third-octave bands, 0.02-4 Hz | x1.11 mean | 1 | | x1.25 | PASS |
| S1 turbulence w PSD vs von Kármán | third-octave bands, 0.02-4 Hz | x1.08 mean | 1 | | x1.25 | PASS |
| S2 motor tau up / down (s) | | 0.0325 / 0.050 | 0.030 / 0.050 | one 2.5 ms step | 10 % | PASS |
| S2 motor saturation (N) | 50 N command, 10 N max | 10.0 | 10.0 | | | PASS |
| S3 sensor delay (s) / rate (Hz) / dropout / noise | 50 Hz, 80 ms, 10 % | 0.080 / 50 / 0.102 / 0.1006 | 0.080 / 50 / 0.10 / 0.10 | | | PASS |
| S4 wing lift slope (per rad) | Aerosonde | 5.61 | 5.61 | | 1 % | PASS |
| S4 wing stalls past CLmax (CL 8 deg past / CLmax) | Aerosonde | 0.21 | < 0.85 | | | PASS |
| S4 lift = weight in trimmed level flight (N) | Aerosonde, 25 m/s | 132.40 | 132.39 | 0.01 % | 2 % | PASS |

E1 to E5 are the task's exit criteria. **All pass.** S1 to S4 support the features claimed.

New unit tests are in `drone/sim/tests/test_flight.py` (10 tests, about 2 s). They cover
frame conventions, exact free fall, drag, hover, sensor delay and dropout, the delayed
Kalman filter, turbulence coherence, unit-suffix enforcement, batch reproducibility, and
the dynamic backend running an existing scenario.

**Existing scenes and vehicles:** see `results/phase1/regression.md`. It covers:

- both pytest suites;
- the CI fast tier for quadcopter, rover and fixed-wing;
- every seed scenario on the default kinematic backend, compared value for value with
  the base commit;
- a smoke run of the quad and fixed-wing scenarios on the new opt-in rigid-body backend.

Bugs found while checking, and fixed:

- A test-harness clock restart froze the sensors. Sensors are scheduled on absolute
  time, so restarting the clock stops them; the harness now carries time across calls.
- numpy 2.0 with Apple Accelerate emits spurious floating-point warnings on every
  `matmul`. They are silenced locally, with an explicit finiteness check that raises on a
  real overflow.
- The first turbulence field (160 modes) gave a vehicle a sparse line spectrum. It now
  uses 1000 jittered modes per field, and the spectrum is checked in third-octave bands.

## 4. What is still uncertain

- **AHRS is statistical.** The attitude sensor is the autopilot estimator's output, with
  bias, noise and delay. It is not an EKF run on the IMU, so estimator transients after
  violent manoeuvres are not modelled.
- **Rotor aerodynamics are minimal.** Thrust has no dependence on inflow or airspeed, and
  there is no rotor H-force (linear "rotor drag"), blade flapping, or ground or ceiling
  effect. The multiplier hook exists; Phase 3 uses it for an unvalidated near-body effect.
  At 20 m/s, real rotors in edgewise flight produce noticeably different thrust per watt.
  This is a known gap for the docking speeds.
- **Drag area is constant (isotropic) by default.** A tilted multirotor shows more area to
  the flow. Per-axis drag is supported and becomes a Phase 3/4 sensitivity case.
- **Fixed-wing model is linear.** Stability derivatives are linear in alpha, beta and the
  rates. Stall is modelled in lift and drag but not in pitching moment. There is no
  dynamic stall, propwash over the wing, or Reynolds or compressibility effect.
- **Simulated stall-speed check moved to Phase 2.** The published Aerosonde coefficients
  are elevator-limited near alpha 9 deg, so they never reach the stall angle. The check
  runs on the quadplane instead.
- **Turbulence limits.** It is a finite set of modes: a line spectrum with the right band
  energy, nothing below 1 m wavelength, one altitude's length scales per run, and no
  building wakes. Each body samples it at a single point, so there are no span-wise gust
  gradients.
- **Cross-platform bit-identity is not claimed.** Runs are byte-identical for the same
  seeds on the same machine. The checks were re-run headless on a remote Linux box (kernel
  7.0, Python 3.12, numpy 2.4) and **all pass with the same printed values**. The
  reproducibility CSV there is byte-identical across worker counts, but not identical to
  the macOS one: the largest difference is 7e-15, from last-bit libm differences.
