# Coffee delivery by Bee35 drones from a hovering Pioneer 3200: simulation report

Branches (local, not pushed):

| branch | what |
|---|---|
| `docking/phase1` | simulator improvements (rigid-body flight model) |
| `docking/phase2-quad-superseded` | the first concept |
| `docking/phase2-hex-superseded` | the second concept |
| `docking/phase2-bee35` | **this report** |

Details are in `PHASE_2.md` (vehicles, sizing, checks), `PHASE_3.md` (dock), `PHASE_4.md`
(balcony mission) and `DESIGN_LOG.md` section C (why the dock controller looks as it does).

## 1. The concept that was simulated

The user's concept of 2026-11-20:

1. A Makeflyeasy Pioneer 3200 carries four SpeedyBee Bee35 drones on a passive dock on its
   back. The dock follows the user's spec v0.1: four pockets with ridges, a starboard lip, a
   guide post per pocket, a catch bracket on each drone, and magnets. No servos.
2. The mothership hovers. A drone launches with an 8 oz coffee (0.29 kg).
3. The drone flies a clear path to the facade and finds the balcony by a three-beacon UWB
   triangle on the back wall marked "drop the coffee here".
4. It sets the box down and slides off the fork, then flies back and lands in its pocket.
5. The drone navigates by UWB ranges to that triangle and to a three-beacon triangle on the
   mothership, plus barometer, downward range and flow, and IMU. It has no camera and no
   GNSS.

## 2. Answers

| question | answer |
|---|---|
| Does the load fit the Pioneer's 5 kg payload? | Yes: 4.08 kg (dock 0.7, 4 drones 2.22, 4 coffees 1.16); margin 0.92 kg |
| What does it do to cruise? | An exposed drone row adds 37 N of drag at 21.5 m/s, roughly tripling cruise power. A real fairing (+7 N) is needed. |
| Flight time and energy | Bee35 hover 3.7 min loaded and 7.0 min empty (6S 1100 mAh, estimated). A delivery uses about 5.5 Wh of 20.7 usable, 71 s at 20–60 m standoff. Reach is about 450 m slant at a 20 % reserve. |
| **Does the balcony delivery work?** | **Yes: 97 %** of missions deliver and return to the dock hand-off point. The box lands 6 cm from the mark (median), at 0.6 m sign size. |
| Does the sign size matter? | **Yes.** A 0.3 m triangle gives 86 % (railing strikes). 0.6 m and 1.0 m perform the same. A sign barometer adds nothing. |
| **Does the passive dock recovery meet 95 % at moderate hover, 6 m/s?** | **Not as specified: 35 %**; 56 % with nominal sensors; 62 % with the spec's fourth anchor. **With two changes it does: 98 %** (nominal sensors), see section 5. |
| What gets it there? | The mothership holds position relative to the air (drifts with the mean wind) instead of over the ground: 89 %. Add a short-range UWB beacon at each pocket: **98 %**, and 97 % or better at every wind from 0 to 9 m/s. |
| Where does the dock work? | Up to 3 m/s with nominal sensors: 78–87 %, best 87 %. No cell reaches 95 %. |
| Does the launch meet the spec? | Every criterion except "does not touch a post", which the slot geometry makes impossible (100 % touch). Without that clause, 99.995 %. |
| Do the magnets hold? | Yes: lift-off at −2.3 g empty and −1.5 g with box; every hold-test case passes. |
| Three or four mothership beacons? | Four. The dock anchor adds about 10 points. With three, losing the fin anchor drops recovery to 11 %, because fore–aft position becomes unobservable. |
| **One delivery end to end** (launch × mission × recovery, nominal sensors, mission weather) | **62 %** with three anchors, 67 % with four. By weather: 83–86 % in calm or 3 m/s, 54 % at 6 m/s, under 10 % at 9 m/s. |
| A full four-drone sortie (dock steps only) | 54–59 % at 0–3 m/s, 9 % at 6 m/s, 0 % at 9 m/s |

## 3. Why the dock falls short

1. **Wind against a light drone and a low lip.** At 9 m/s an empty Bee35 (0.555 kg) carries
   1.5 N of drag plus gusts. The starboard side has only a 0.038 m, 45° lip, so drones are
   blown over it and fall into the starboard rotors' strike zone. That is the largest failure
   class, 11,026 of 57,600.
2. **The one-sided bracket out-torques the drone's yaw.** The spec's 0.5 N push on a fence at
   the bracket's lever arm is about 0.06 N m, the same as the Bee35's yaw authority at hover.
   A post in a slot gives no restoring moment.
3. **UWB bias against a ±0.10 m mouth.** The per-anchor bias of ±0.05 m puts the
   three-anchor estimate 0.05–0.15 m off at the bracket. The retry search recovers most
   misses but costs time against the 40 s limit.
4. **The launch slot.** 1 mm clearance around the post for 0.27 m of climb means the post is
   always touched, with impulsive peaks around 100 N or more.

## 4. Spec gaps found

- A downward range sensor at the drone's centre looks down the 0.130 m box well and never
  reads the 0.02 m cutoff. It was moved to (0.085, 0, −0.100) m.
- A3 flush on the fin top is blocked by the fin from every point below it. It was put on a
  2 cm mast.
- With σ 0.02 m range noise, a single-sample 0.02 m cutoff fires early. The sim requires 5
  consecutive samples.
- "Does not touch a post" for launch contradicts the slot geometry.
- Three mothership anchors give no redundancy for the fin anchor.

## 5. Design changes, simulated (2026-11-21)

Moderate hover, nominal sensors, 1,440 recoveries per variant on the same seeds. Full table
and failure counts: `PHASE_3.md` section 5.4.

| variant | target cell (6 m/s) | 9 m/s | all winds |
|---|---|---|---|
| baseline | 0.581 | 0.081 | 0.601 |
| taller lip (0.10 m) | 0.583 | 0.136 | 0.604 |
| windward recovery | 0.692 | 0.142 | 0.640 |
| 20 mm launch slot | 0.678 | 0.156 | 0.642 |
| two posts, two brackets | **0.239** | 0.061 | 0.272 |
| no hover motion at all | 0.636 | 0.097 | 0.614 |
| **mothership drifts with the mean wind** | **0.894** | **0.850** | **0.864** |
| drift + lip + windward + slot + A4 | 0.889 | 0.869 | 0.872 |
| **drift + a short-range beacon at each pocket** | **0.981** | **0.967** | **0.974** |
| same, beacons as noisy as the airframe anchors | 0.961 | 0.953 | 0.965 |
| drift + beacons + lip + windward + slot + A4 | 0.978 | 0.983 | 0.980 |

1. **Drift with the wind.** The mothership holds position relative to the air, and still
   hovers against gusts. This removes the mean airspeed from the approach. It is a
   mothership flight-mode change, not hardware. **Cost:** the mothership moves downwind at
   the wind speed, 120–180 m during one 20 s recovery at 6–9 m/s. It would drift only for
   each drone's final approach and fly back upwind between drones. That operating pattern,
   and the drone's return to a moving mothership, are **not simulated yet**: in the sim,
   the turbulence stays and only the mean wind is removed in the dock frame.
2. **A short-range UWB beacon at each pocket**, on the port rail beside the post. The gain
   is geometry: beacons within a metre of the post, not 1.5–3 m away on the wingtips and
   fin. Even beacons as noisy as the airframe anchors give 96 %.
3. **Drop the two-post bracket.** As modelled it halves recovery and launch success.
4. The lip, windward orientation and wider slot add nothing measurable once 1 and 2 are in.
   The wider slot still reduces launch post contact somewhat.
5. **Remaining failures** with 1 and 2 (37 of 1,440) are mostly touchdowns at 0.50–0.70 m/s
   from the hard motor cutoff. A thrust ramp-down is the next thing to try.
6. For the balcony: sign at least 0.6 m across, and drop marks at least 1 m from a divider
   (`PHASE_4.md`).

Not yet checked: the beacon variant in gusty hover, with ranging noise × 2 and with an anchor
lost (the full 288-cell run).

To re-run a variant: `just remote-run <name> "python -m docksim.matrix run --exp pocket_drift
--trials 60"` on the remote box, then `python -m docksim.compare --hover moderate`.

## 6. Caveats

Several model inputs are estimates or placeholders from the spec:

- Bee35 thrust, inertia and battery;
- rotor, wing and fin positions;
- the magnet curve;
- the UWB error model (no multipath structure);
- the urban wind model, a parametric model, not CFD;
- the rotor inflow model.

The dock and mission results are combined as independent events, sharing only the wind.
The dock controller is this project's own design (`DESIGN_LOG.md` section C). A better
controller could move the 6 m/s numbers, but not the physical limits listed in section 3.

## 7. Reproduce

From `drone/sim`, using the repo venv (`just venv`):

| recipe | what it does |
|---|---|
| `just test` | 30 tests |
| `just dock-sizing` | sizing |
| `just dock-checks` | hold test and position hold |
| `just dock-trial recovery 3 moderate 6` | one recovery trial |
| `just dock-summary` | dock matrix tables |
| `just mission B5 3` | one balcony mission |
| `just mission-summary` | mission tables |
| `python -m mission.endtoend` | end-to-end numbers |

Batches go to a remote Linux box named by `$SIM_REMOTE`. All runs are seeded and
deterministic.
