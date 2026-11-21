# Phase 3 (revised 2026-11-20): launch from and recovery to the passive dock

Branch `docking/phase2-bee35` (local, not pushed). Spec: "Passive four-drone dock:
simulation spec" v0.1. Configuration and sizing: `PHASE_2.md`.

Results: the spec's full matrix (288 cells × 200 trials = 57,600 launches and 57,600
recoveries, `baseline`) and the four-anchor variant (`spec4`, 50 per cell = 14,400 each).
Tables: `docking/results/dock/summary.md`.

## 1. The simulation

**Physics.** MuJoCo 4.0.1, 1 kHz, headless and deterministic (same seed, same result). The
simulation runs in the dock frame D. The mothership's hover motion enters as fictitious
forces, so the drone and dock contact in one rigid frame with no moving kinematic bodies:

- gravity_D = Rᵀ(g − a_O);
- per body: −m[α×r + ω×(ω×r) + 2ω×v] and −I_W α.

Air velocity in D is Rᵀ(w − v_O) − ω×r, plus the rotor inflow.

**Dock geometry, exactly as in spec section 3:**

- floor plate with four box wells;
- three triangular ridges and two end wedges (convex meshes);
- starboard lip wedge;
- guide posts: capsule, r = 0.005 m, height 0.28 m, soft tip sphere;
- pylons and the fairing wedge;
- fuselage, wing and fin boxes. These block UWB lines; the fin carries A3.

**Drone.** Body box, four steel skid pads, and the catch bracket: end plate, slot walls and
two 27° fences, at y = 0.015–0.045 m. The box and rim hang under the drone for launch. Mass
and inertia follow spec section 4, with the box folded in when carried.

**Contact.** The friction pairs follow the spec table through geom priority. Restitution
maps to MuJoCo's damping ratio: e = 0.1 → ζ 0.59, e = 0.2 → ζ 0.46.

**Forces:**

- magnets: per pad, 3 N quadratic to zero at a 6 mm gap, only within 10 mm horizontal
  alignment;
- four rotors with a 0.04 s lag, yaw torque 0.012 m × thrust;
- isotropic drag (CdA 0.03 m²) against wind, turbulence, mothership motion and rotor inflow
  (4 m/s at the rotor plane, linear to zero at 0.5 m above, averaged over the footprint).

**Sensors (spec section 6):**

- UWB: 20 Hz per anchor, σ 0.05 m, ±0.05 m per-anchor bias, 2 % dropout, +0.10–0.30 m when
  the fuselage, wing, fin or a parked drone blocks the line (ray cast);
- down range: 5-ray, 6° beam, σ = half the quoted accuracy, clipped at 0.02 m;
- optical flow: velocity relative to the surface below, valid above 0.08 m;
- broadcast: 10 Hz, 50–100 ms delay, Gauss–Markov errors;
- compass and AHRS;
- IMU.

**Monitoring (truth side, for scoring only):**

- first post contact speed;
- touchdown speed (first pad/body contact with floor, ridge or lip);
- contact with parked drones above y = 0.06 m;
- peak contact force;
- parts inside a strike zone (body corners, fence tips, box corners);
- final pose 1.5 s after motors off.

## 2. The drone's software

**Relative navigation.** A 6-state filter (position and velocity relative to the dock)
in a level frame aligned with the broadcast heading. Prediction uses the IMU acceleration
minus the dock acceleration differentiated from the broadcast velocity. This is the spec's
"broadcast velocity as feed-forward". Updates:

- UWB ranges;
- down range against the dock shape, using the nearest-surface association described in the
  design log;
- optical flow;
- a position fix when the post seats in the slot (the slot fixes x and z).

**Flight control.**

- PID position loop: kp 9, kv 6, ki 4, integrator limit 6 m/s².
- World-frame thrust vector, limited to 35° tilt and 95 % thrust.
- Attitude PD on the AHRS attitude, with yaw kp 120.
- X mixer with yaw at the lowest priority.

**Launch (spec 9).**

1. Idle, then ramp at 20 N/s with the attitude held level.
2. Lift-off is 3 consecutive range samples ≥ 0.05 m.
3. Climb at 1.2 m/s to y = 2, then move 3 m to starboard.

Variant `sidestep`: rise to 0.05 m, move 0.07 m to starboard off the post, then climb.

**Recovery (spec 10).** These are the spec's steps, with the changes the simulator forced,
each justified in `DESIGN_LOG.md` section C:

| spec step | as built |
|---|---|
| 2–4 hold point, align, descend at 0.3 m/s to 0.17 m | reference decelerates into the target; vertical integrator frozen during the ramp; approach starts once height has settled within 0.02 m |
| 6 move toward −z at 0.10 m/s | x stiff for the first 0.10 m, then **compliant** (weak spring, flow-damped, wind integrator held) so the fences can steer the drone |
| 9 stop detection, sideways speed < 0.02 m/s | measured with **optical flow** (relative to the dock), held 0.3 s, accepted only within 0.08–0.30 m of travel and with the range within 0.05 m of the approach height |
| 10 keep a 0.5 N push | the 0.5 N push sits **on top of holding the post position**, with the integrator running |
| 11 descend at 0.15 m/s | **hold at the post until heading is within 4°** (the ridges need it; a post in a slot does not resist yaw), then descend with x stiff |
| 13 motors off at range ≤ 0.02 | range ≤ 0.02 on 5 consecutive samples |
| abort: no catch after 0.30 m | also an approach timeout; the **retry is offset ±0.08 m in x** (a constant UWB bias repeats the miss otherwise) |
| abort: error > 0.20 m below 0.5 m | as specified, measured on z only during the compliant approach |
| after three aborts, fallback | as specified |

## 3. Pass criteria (spec 12), as scored

**Recovery passes** when all of these hold:

- final |x|, |z| ≤ 5 mm from the pocket centre;
- heading error < 5°;
- first post contact ≤ 0.3 m/s;
- touchdown ≤ 0.5 m/s;
- no part in a strike zone;
- no contact with a parked drone while the landing drone is above y = 0.06 m;
- motors off within 40 s of clearance;
- seated: y within 3 mm of the seat and motors off.

**Launch passes** when:

- it clears y = 1.0 within 3 s of leaving the magnets;
- it touches no post, ridge or parked drone after leaving the seat;
- no part enters a strike zone.

**Hold test:** in `PHASE_2.md` section 4. It passes.

## 4. Findings that do not depend on the final numbers

1. **The launch cannot meet "does not touch a post".** Seated, the post sits in a slot with
   1 mm clearance each side, and the bracket rides the 0.28 m post for 0.27 m of climb. Every
   launch touches the post: median 26 ms of contact, impulsive peaks around 100 N. Without
   the post clause, launches pass 100 %. Release comes at a median 19.4 N, and the drone
   clears 1 m within 0.55 s (p95). The spec's own sequence is the cause. Either the
   criterion has to allow sliding contact with the post, or the slot needs a lead-in and more
   clearance. The `sidestep` variant does not help: the drone must first rise 0.038 m past
   the lip with the slot still around the post.
2. **The one-sided bracket out-torques the drone's yaw.** A 0.5 N push on a fence at about
   0.11 m lever arm is about 0.06 N m. A Bee35 has about 0.065 N m of yaw authority at hover.
   Fence contact yaws the drone, and a post in a slot gives no yaw restoring moment. Pushing
   toward a pivot behind the centre of mass is unstable. The controller copes: stiff yaw,
   yaw mixed last, a heading gate before the final descent. The design would be more robust
   with two posts and a bracket on each side, or a bracket on the centreline.
3. **UWB bias against the bracket mouth.** The per-anchor bias (±0.05 m, constant in a run)
   puts the triangle-anchor estimate 0.05–0.15 m off at the bracket height. The mouth catches
   ±0.10 m. The retry search (±0.08 m) recovers most misses. A dock-mounted anchor such as
   the spec's A4 removes most of the bias geometry (variant `spec4`).
4. **Losing the fin anchor A3 is not survivable with three anchors** (DOP 46). Losing A1 or
   A2 costs a smaller drop (DOP 3.5–4.8).
5. **Wind is the dominant variable.** In the 50-per-cell diagnostic run with nominal
   sensors:
   - 0–3 m/s: 80–90 % pass;
   - 6 m/s: 35–60 %;
   - 9 m/s: about 5 %.

   At 9 m/s, empty drones are blown off the starboard side, over the 0.038 m lip. They fall
   into the starboard rotors' strike zone, during the final push or after a cutoff on the
   lip. Contact with parked neighbours grows with neighbour count and wind. A taller
   starboard lip, or landing from the windward side, are the obvious design levers.
6. **Spec gap: the downward range sensor at the centre looks down the box well** and can
   never read the 0.02 m cutoff (see `PHASE_2.md`).
7. **The magnets are sized right.** Lift-off needs −2.3 g empty and −1.5 g loaded, and the
   release thrust (17–20 N) is half the motors' maximum.

## 5. Results

### 5.1 Diagnostic run, previous controller build (50 per cell, partial: 3,300 trials)

This run was stopped and replaced by the final run. Its controller lacked C14–C17 (design
log). It is kept here only to show where the failures are.

- **Recovery:** 36 % overall. Target cell 27 %; target cell with nominal sensors 46 %. 4 of
  288 cells at ≥ 95 %. By wind: 0 m/s 58 %, 3 m/s 54 %, 6 m/s 29 %, 9 m/s 2 %. Ranging noise
  ×2: 27 % against 45 %. One anchor off: 28 % against 44 %; with A3 lost, 11 %.
- **Launch:** 12 % by the spec's criteria, every failure "touched post"; **100 % without the
  post clause**.

### 5.2 Final run

**Recovery, baseline (three anchors A1–A3), 57,600 trials**

| | pass fraction (95 % CI) |
|---|---|
| all cells | **0.377** (0.373–0.381) |
| **target cell: moderate hover, 6 m/s (spec: 95 %)** | **0.348** (0.335–0.362) |
| target cell, nominal sensors (noise ×1, no anchor lost) | 0.560 (0.532–0.588) |
| cells at ≥ 95 % | **0 of 288** |

Nominal sensors, by hover level and mean wind:

| hover \ wind | 0 m/s | 3 m/s | 6 m/s | 9 m/s |
|---|---|---|---|---|
| calm | 0.86 | 0.87 | 0.60 | 0.10 |
| moderate | 0.87 | 0.86 | 0.56 | 0.08 |
| gusty | 0.78 | 0.78 | 0.46 | 0.05 |

The other variables, over all cells:

| variable | effect on pass fraction |
|---|---|
| ranging noise ×2 | 0.48 → 0.27 |
| one anchor off | 0.46 → 0.30; A1 or A2 lost 0.39, **A3 lost 0.11** |
| parked neighbours 0 / 1 / 2 | 0.39 / 0.38 / 0.37 |
| box on launch | no effect on recovery (as expected) |

Failure reasons (first failing criterion):

| reason | count | what it is |
|---|---|---|
| prop strike | 11,026 | blown off the dock into the starboard rotors' strike zone |
| not landed in time | 6,815 | |
| touched a parked drone | 6,704 | |
| not seated | 3,932 | |
| post contact > 0.3 m/s | 2,748 | |
| over 40 s | 1,720 | |
| touchdown > 0.5 m/s | 1,314 | |
| final position > 5 mm | 887 | |
| fallback after 3 aborts | 751 | |

Passing recoveries take 20.5 s median (25.5 s p90) from clearance to motors off. They average
0.7 aborts and end 3 mm from the pocket centre (median). Peak contact force is 50 N median and
230 N p95, magnets included. Post contact speed is 0.44 m/s at p95.

**Recovery, spec4 (four anchors, adds A4 at the dock rear), 14,400 trials**

| | baseline | spec4 |
|---|---|---|
| all cells | 0.377 | **0.480** |
| target cell | 0.348 | **0.463** |
| target cell, nominal sensors | 0.560 | **0.623** |
| wind 0 / 3 / 6 / 9 m/s | 0.57 / 0.56 / 0.34 / 0.04 | 0.72 / 0.71 / 0.43 / 0.06 |
| one anchor off | 0.30 | **0.44** (A3 lost: 0.41 instead of 0.11) |
| cells at ≥ 95 % | 0 | 9 |

**The fourth anchor at the dock is worth about 10 points everywhere and removes the A3
single point of failure.** It does not fix wind.

**Launch, 57,600 trials.** By the spec's criteria, 0.112. Every failure but 3 is "touched a
post", for the slot reason in section 4. **Without the post clause: 0.99995** (2 never left
the magnets, 1 cleared 1 m slowly). Release thrust median 19.5 N; clearing 1 m takes 0.55 s
at p95. Post contact lasts 26 ms median (76 ms p95). The peak post force is 136 N median and
430 N p95, impulsive: the magnets let go, the drone jumps, and the slot strikes the post. A
lead-in on the slot or a softer post would cut these. The four-anchor variant changes
nothing for launch.

### 5.3 Verdict against spec section 12

- **Recovery: target not met.** 35 % in the moderate/6 m/s cell against 95 %, 56 % with
  nominal sensors, 62 % with four anchors. At 0–3 m/s with nominal sensors the dock reaches
  78–87 %. Above about 5 m/s, wind decides: the light empty drone, a 0.038 m lip, and yaw
  authority comparable to the bracket's contact torque.
- **Launch: the clause "does not touch a post" cannot be met by this geometry.** Every other
  launch criterion passes essentially always.
- **Hold test: passes** (`PHASE_2.md` section 4).
- **Position hold (spec 7): met at the approach height** (0.05 m p95), **marginal at 2 m**
  (0.08–0.10 m).

Design changes that the results point to, in order of expected effect:

1. **A taller and steeper starboard lip, or a closed side.** Most prop strikes are drones
   blown over it.
2. **A dock anchor (A4).** Measured: +10 points, and no single point of failure.
3. **Two posts, one each side, or a centreline bracket.** This removes the yaw torque that
   decides many 6 m/s failures.
4. **Recover from the windward side**, chosen per sortie. The spec fixes starboard.
5. **A slot lead-in with more clearance, plus a launch criterion that allows sliding contact
   with the post.**

Section 5.4 simulates each of these, plus two changes to how the mothership hovers and a
short-range beacon at each pocket.

### 5.4 Design variants (2026-11-21)

Each variant is a config override in `docksim/matrix.py` (`EXPS`) on top of
`docking/vehicles.yaml`. Every variant runs the same seeds as the baseline, so the rows pair
up trial for trial. The table below covers moderate hover with nominal sensors and no anchor
lost: 24 cells (4 winds × 3 neighbour counts × box or none), 60 trials each, 1,440
recoveries per variant (1,200 for `spec4`). The 6 m/s column is the spec's target cell
(n = 360; a 0.98 result has a 95 % interval of about 0.96–0.99). Tables:
`docking/results/dock/variants_moderate.md`, and `variants.md` for the geometry variants
over all 72 hover × wind × neighbour × box cells.

| variant | what changes | recovery | 0 m/s | 3 m/s | **6 m/s** | 9 m/s | launch without the post clause |
|---|---|---|---|---|---|---|---|
| baseline | spec as built: A1–A3, 38 mm lip, one post, wind from the nose | 0.601 | 0.869 | 0.872 | **0.581** | 0.081 | 1.000 |
| spec4 | adds the dock anchor A4 | 0.631 | 0.903 | 0.893 | **0.623** | 0.103 | 1.000 |
| lip10 | starboard lip 0.10 m high, about 60° | 0.604 | 0.853 | 0.844 | **0.583** | 0.136 | 1.000 |
| dual | two posts at x ± 0.075 m, a bracket on each, range sensor on the centreline | 0.272 | 0.383 | 0.403 | **0.239** | 0.061 | 0.531 |
| windward | mothership yaws so the wind comes from starboard ± 20° | 0.640 | 0.869 | 0.858 | **0.692** | 0.142 | 1.000 |
| slot20 | launch slot 20 mm (5 mm clearance each side) | 0.642 | 0.878 | 0.856 | **0.678** | 0.156 | 0.985 |
| all | lip10 + dual + windward + slot20 + A4 | 0.331 | 0.492 | 0.361 | **0.353** | 0.119 | 0.846 |
| still | the mothership holds its position with no hover motion at all | 0.614 | 0.833 | 0.889 | **0.636** | 0.097 | 1.000 |
| **drift** | the mothership holds position relative to the air, drifting with the mean wind | 0.864 | 0.869 | 0.842 | **0.894** | 0.850 | 1.000 |
| still_drift | both | 0.853 | 0.833 | 0.850 | **0.847** | 0.883 | 1.000 |
| all_drift | all + drift | 0.491 | 0.486 | 0.508 | **0.472** | 0.497 | 0.878 |
| all_drift_nodual | lip10 + windward + slot20 + A4 + drift | 0.872 | 0.875 | 0.853 | **0.889** | 0.869 | 1.000 |
| **pocket_drift** | A1–A3, **a short-range UWB beacon at each pocket**, drift | **0.974** | 0.972 | 0.978 | **0.981** | 0.967 | 1.000 |
| pocket_same_drift | as pocket_drift, with beacons as noisy as A1–A3 | 0.965 | 0.975 | 0.972 | **0.961** | 0.953 | 1.000 |
| pocket_all_drift_nodual | pocket beacons + all_drift_nodual | 0.980 | 0.978 | 0.981 | **0.978** | 0.983 | 1.000 |

The pocket beacons (`dock_sensors.pocket_beacons` in `vehicles.yaml`, off by default) are
modelled as follows:

- one UWB beacon per pocket, on a 0.05 m stand on the port rail beside the post, at
  (pocket x, 0.05, −0.19) m;
- ranged by the drone's existing tag;
- heard only within 2 m;
- σ 0.03 m with a per-beacon bias within ± 0.02 m (estimates for a short, calibrated,
  line-of-sight link);
- blocked by bodies, with the same NLOS model as A1–A3.

The filter weights each anchor by its own noise.

What the variants show:

1. **Wind against the hovering mothership was the main problem, and drifting removes most of
   it.** A mothership that holds a ground position in 6–9 m/s wind puts the full airspeed on
   the drone during the approach. A mothership that holds position relative to the air
   (drifting with the mean wind, still hovering against gusts) leaves only turbulence.
   Recovery at 9 m/s goes from 8 % to 85 %. Removing the hover motion alone (`still`) barely
   helps; the hover motion was never the issue. The sim models drift by removing the mean
   wind in the dock frame and keeping the turbulence. The mothership then travels downwind
   at the wind speed, 120–180 m per 20 s recovery at 6–9 m/s. So in practice it would
   drift only for each final approach. That pattern is not simulated.
2. **Close-in navigation is the second problem, and pocket beacons solve it.** With drift
   and four pocket beacons, the target cell reaches **98 %** and every wind is at 97 % or
   better. Beacons as noisy and biased as the airframe anchors still give 96 %, so the gain
   is geometry (beacons 0.1–1 m from the post, around it), not beacon quality. The
   airframe triangle is 1.5–3 m away, so its ± 0.05 m biases multiply into 0.05–0.15 m at
   the bracket.
3. **The two-post bracket is harmful as modelled.** Its mouths catch ± 0.06 m instead of
   ± 0.10 m and the drone can seat on the wrong post; "not seated" and "final position"
   dominate. It also halves launch success, because both brackets ride their posts. Every
   combination that includes it does worse than the same combination without it.
4. **The other geometry changes are small next to drift and beacons.** The taller lip and
   the windward orientation move the target cell by 0 and 11 points without drift, and by
   less than 1 point (within noise) once drift or beacons are in. The 20 mm slot helps launches touch the post less
   (82 % instead of 89 %), but most launches still touch it.
5. **What fails with drift and pocket beacons (37 of 1,440):** touchdown faster than
   0.5 m/s (24, all between 0.50 and 0.70 m/s, at every wind speed), prop strike (4),
   over 40 s (3), and single cases of the rest. The touchdown failures come from the motor
   cutoff sequence (five range samples at 0.02 m, then a drop), not from navigation or
   wind. A thrust ramp-down instead of a hard cutoff is the likely fix (not yet simulated).

**Not yet checked:** the beacon variant in gusty hover, with ranging noise × 2 and with an
anchor lost. A full 288-cell run of `pocket_drift` is the next result.
