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
| **Does the passive dock recovery meet 95 % at moderate hover, 6 m/s?** | **No: 35 %**; 56 % with nominal sensors; 62 % with the spec's fourth anchor. |
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

## 5. Recommended next steps (not simulated)

1. A taller, steeper starboard lip or a closed side.
2. Keep a dock anchor (A4).
3. Two posts with brackets on both sides, or a centreline bracket.
4. Recover from the windward side.
5. A slot lead-in with more clearance, and a launch criterion that allows sliding post
   contact.
6. Sign at least 0.6 m across, and drop marks at least 1 m from a divider.

Each is a change in `docking/vehicles.yaml` and a re-run:

- `just remote-run <name> "python -m docksim.matrix run --exp baseline --trials 200"` on the
  remote box;
- `just missions` for the mission sim.

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
