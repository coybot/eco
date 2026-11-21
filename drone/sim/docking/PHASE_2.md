# Phase 2 (revised 2026-11-20): Bee35 drones, the passive dock, and sizing

Branch `docking/phase2-bee35` (local, not pushed), on top of `docking/phase2-hex-superseded`.

This phase replaces two earlier versions. The quadcopter-on-a-rail version is on branch
`docking/phase2-quad-superseded`, and the hexacopter version is on
`docking/phase2-hex-superseded` (6d711b0, kept with its last results). Neither is used below.

## 1. What changed, and why

On 2026-11-20 the user set a new concept:

1. Drop the difficult-topology scenario. The path from the sky to the balcony side of the
   building is clear.
2. The delivery drones are **SpeedyBee Bee35** (3.5 in ducted). The mothership is the
   **Makeflyeasy Pioneer 3200**.
3. The drone finds the target balcony by a sign on its back wall. The sign carries **three
   UWB beacons in a triangle** and reads "drop the coffee here".
4. The mothership carries **three beacons in a triangle**.
5. Four drones sit in an enclosure on top of the mothership. They launch from it and land
   back on it while the mothership hovers. The dock is the user's "Passive four-drone dock:
   simulation spec" v0.1.

Everything below follows from these five points and the spec.

## 2. Configuration

All numbers are in `docking/vehicles.yaml`, in five new blocks: `bee35`, `dock`,
`mothership_airframe`, `dock_sensors`, `dock_ops`, plus `dock_matrix` and
`balcony_mission`. The spec's JSON config is transcribed with a unit on every name. Tags:

- **(E)** estimate from the spec;
- **(P)** placeholder from the spec;
- **[D]** decided here, with the reason next to it;
- **[U]** from the user.

Decisions that depart from the spec, or that fill a gap it leaves:

| item | decision | why |
|---|---|---|
| mothership anchors | **A1, A2, A3** (both wingtips and the fin top); the spec's four-anchor set (adds A4 at the dock rear) runs as variant `spec4` | the user said three beacons in a triangle; these three make the widest triangle around the dock |
| A3 mount | on a 0.02 m mast above a fin top lowered to y = 0.28 | with the anchor flush on the fin top, the fin itself blocks it from every point below 0.30 m. That puts the whole approach in non-line-of-sight. |
| range/flow sensor | at body (+0.085, 0, −0.100), not the centre | **spec gap.** At the centre the sensor looks down the 0.130 m box well and reads the fuselage at 0.146 m, so the spec's cutoff "range ≤ 0.02" can never fire. The chosen point sees flat floor when seated and during the descent at hold z +0.18. |
| range accuracy | the quoted 0.04 m is 2σ | the spec does not say |
| motor cutoff | range ≤ 0.02 on **5 consecutive** samples | with σ 0.02 m, a single-sample test at 0.02 m fires early often enough to drop the drone from several cm |
| compass | 3° σ, of which 70 % of the variance is a per-run bias | magnetic disturbance near a carbon airframe is mostly a bias |
| broadcast errors | Gauss–Markov, τ 5 s | GNSS-like. The spec gives only σ. |
| hover motion | three cascaded first-order lags per axis | position, velocity and acceleration all stay continuous; the stationary RMS matches the spec table (checked) |
| wind direction | from within ±45° of the nose | a hovering quadplane points into wind |
| Bee35 rotors | at (±0.0575, ±0.0575) m; yaw torque 0.012 m × thrust | 89 mm props in ducts inside the 0.21 m box |
| battery | 6S 1100 mAh, 24.4 Wh, 85 % usable | typical for the class |
| hover efficiency | 0.29 (ideal / electrical, open-rotor basis) | gives about 180 W hover at 0.555 kg, consistent with the 5–7 min hover reported for 3.5 in cinewhoops |
| physics | MuJoCo 4.0.1 at 1 kHz, control at 500 Hz | the spec asks 240 Hz or more with continuous collision detection (CCD). At 1 kHz and the 0.3 m/s worst contact speed, a step moves 0.3 mm, a tenth of the 3 mm fence. MuJoCo has no CCD. |

## 3. Sizing (`python -m docksim.sizing`, `docking/results/dock/sizing.json`)

### Bee35 power and flight time

| | mass | thrust needed / max | hover power | hover time |
|---|---|---|---|---|
| empty | 0.555 kg | 16 % | 177 W | **7.0 min** |
| with 0.290 kg coffee | 0.845 kg | 24 % | 333 W | **3.7 min** |

### Mission energy

The example mission is 80 m slant each way at 6 m/s, with:

- 35 s at the balcony;
- 8 s launch;
- 20 s nominal recovery;
- two extra docking attempts.

It uses **8.2 Wh of 20.7 Wh usable** (61 % left). At a 20 % reserve, the largest slant
distance is **about 450 m**. The balcony mission sim (Phase 4) measured a median of 5.5 Wh
per mission: 70 s, 20–60 m standoff, 2.5 Wh more in strong wind.

### Carry on the Pioneer 3200 (5.0 kg payload, placeholder)

| item | mass |
|---|---|
| dock, posts, fairing, beacons | 0.7 kg (spec range 0.6–0.8) |
| 4 × Bee35 with bracket | 2.22 kg |
| 4 × coffee box | 1.16 kg |
| **total** | **4.08 kg (4.18 kg with a 0.8 kg dock); margin 0.92 kg** |

### Cruise drag of the loaded dock

The spec puts cruise out of scope, but the drag decides whether the Pioneer can carry this
at all. Figures are at 21.5 m/s, against 20.9 N for the clean airframe:

| case | extra drag area | extra drag | increase |
|---|---|---|---|
| four drones fully exposed, plus the dock | 0.13 m² | 37 N | **+176 %** |
| rear three half-shielded | 0.085 m² | 24 N | +115 % |
| fully faired enclosure | 0.025 m² | 7 N | +34 % |

An exposed drone row would roughly triple cruise power. The "enclosure" in the user's
message has to be a real fairing for cruise endurance to survive.

### Magnet margins

| | release thrust | lift-off load factor |
|---|---|---|
| empty | 17.4 N (50 % of max) | −2.2 g (simulated −2.3 g) |
| with box | 20.3 N (58 %) | −1.45 g (simulated −1.5 g) |

The spec's "leaves the magnets at about 20 N" matches the loaded case. The simulated
launches released at a median **19.4 N**.

### Mothership rotor clearance

- P1 and P4 descend with the drone's footprint 0.265 m from a starboard rotor axis. That is
  inside the 0.28 m disk radius and the 0.30 m strike radius horizontally.
- The strike zone ends at y = −0.02 and the drone never goes below y = 0, so the clearance is
  vertical: 0.02 m when seated.
- The drone does fly through the edge of the starboard rotors' inflow (up to y = 0.4).

So prop strikes happen only when a drone falls off the dock. Phase 3 shows that this is
exactly how it happens in strong wind.

### UWB geometry

Worst horizontal dilution over the hold, approach and seated points, with height from the
down range:

| anchors | worst DOP | note |
|---|---|---|
| triangle (A1, A2, A3) | 2.6 | |
| triangle minus A1 / minus A2 | 3.5 / 4.8 | |
| **triangle minus A3** | **46** | fore–aft position is unobservable without the fin anchor |
| spec4 (A1–A4) | 2.6 | |
| spec4 minus any one | ≤ 4.7 | |

**The three-anchor triangle has no redundancy for the fin anchor.** The spec's test matrix
includes "one anchor off". With three anchors, losing A3 is the main source of that cell's
failures (Phase 3: 11 % pass with A3 lost, against about 40 % for A1 or A2 lost, in the
partial run).

## 4. Fixed-input checks (`python -m docksim.checks`, `docking/results/dock/checks.json`)

**H1. Magnet hold test (spec 12).** A parked drone, empty and with box, under:

- vertical load factor −0.5, 0, +1, +1.5 g;
- 0.3 g sideways in ±x and ±z, also at −0.5 g;
- a steady 3 N rearward force, also at −0.5 g.

It stays seated in every case. The largest shift is 1.1 mm toward starboard under 0.3 g, where
the drone settles against the lip inside the 2 mm pocket clearance. The pass limit is 3 mm
(pocket clearance + 1 mm), because the spec says only "stays seated". Lift-off happens at
−2.3 g empty and −1.5 g with the box.

**H2.** A parked drone through 20 s of gusty hover in 9 m/s wind moves 2.5 mm empty and
0.4 mm with the box. It does not lift.

**P1. Position hold** (spec 7: 0.10 m or better, 95 % of the time, calm air). The p95
horizontal error over 12 seeds:

| point | calm, median (worst) | moderate, median (worst) |
|---|---|---|
| hold point (y = 2.0 m) | 0.08 m (0.11 m); 8 of 12 seeds meet 0.10 | 0.10 m (0.12 m) |
| approach height (y = 0.17 m) | 0.05 m (0.07 m) | 0.05 m (0.08 m) |

Heading error p95 is about 4°. **The requirement holds at the approach height and is
marginal at 2 m.** The limit is the per-anchor UWB bias (±0.05 m), which no amount of
averaging removes.

## 5. Code

| path | what |
|---|---|
| `docksim/config.py` | config loader |
| `docksim/sizing.py` | section 3 |
| `docksim/scene.py` | MJCF: dock, airframe shapes, drones, bracket, box |
| `docksim/motion.py` | hover motion and wind |
| `docksim/sim.py` | dock-frame physics: magnets, rotors, drag, inflow, fictitious forces, contacts |
| `docksim/sensors.py` | UWB, down range, flow, broadcast, AHRS/compass/IMU |
| `docksim/drone.py` | relative navigation, flight control, launch and recovery state machines |
| `docksim/trial.py` | one trial plus the pass criteria |
| `docksim/checks.py` | section 4 |
| `docksim/matrix.py` | spec 13 test matrix |
| `docksim/summarise.py` | matrix tables |
| `tests/test_docksim.py` | 6 tests: seated stability, fence funnelling, magnet lift-off, hover-motion RMS, launch release thrust, one calm recovery |

Recipes: `just dock-sizing`, `just dock-checks`, `just dock-trial`, `just dock-matrix`,
`just dock-summary`.
