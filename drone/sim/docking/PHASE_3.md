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
   wind. Section 5.6 simulates a thrust ramp-down (`pd_soft`): it removes them.

### 5.5 Pocket beacons with drift, full matrix (2026-11-21)

`pocket_drift` over all 288 cells (3 hover levels × 4 winds × 3 neighbour counts × box or
none × ranging noise × 1 or × 2 × no anchor or one anchor lost), 60 trials each, 17,280
recoveries. The baseline on the same seeds is shown for comparison.

| condition | baseline | pocket_drift |
|---|---|---|
| all 288 cells | 0.380 | **0.913** |
| calm / moderate / gusty hover | 0.416 / 0.386 / 0.338 | 0.951 / 0.933 / 0.855 |
| ranging noise × 1 / × 2 | 0.484 / 0.277 | 0.943 / 0.884 |
| no anchor lost / one lost | 0.466 / 0.294 | 0.930 / 0.896 |
| A1 / A2 / A3 (fin) lost | — | 0.893 / 0.908 / 0.888 |
| 0 / 3 / 6 / 9 m/s | 0.566 / 0.569 / 0.345 / 0.040 | 0.920 / 0.917 / 0.912 / 0.903 |
| **target cell, moderate 6 m/s, all four sensor conditions** | 0.358 | **0.931** |
| target cell: nominal / one lost / noise × 2 / both | 0.581 / 0.364 / 0.306 / 0.183 | 0.981 / 0.950 / 0.917 / 0.878 |
| cells at 95 % or better | 0 of 288 | **126 of 288** |
| cells at 90 % or better | 8 of 288 | 188 of 288 |

Nominal sensors, no anchor lost, by hover and wind:

| hover | 0 m/s | 3 m/s | 6 m/s | 9 m/s |
|---|---|---|---|---|
| calm | 0.983 | 0.978 | 0.989 | 0.975 |
| moderate | 0.972 | 0.978 | 0.981 | 0.967 |
| gusty | 0.939 | 0.908 | 0.914 | 0.911 |

What the full run shows:

1. **Wind speed no longer matters much** (90–92 % at every speed, over all conditions). The
   remaining sensitivity is to **gusty hover** and to **ranging noise × 2**.
2. **Losing an anchor is no longer critical.** Without pocket beacons, losing the fin anchor
   A3 dropped recovery to 11 % (section 5.3). With them, losing any one airframe anchor
   costs 3–4 points, and A3 is no worse than A1.
3. **Gusty hover is the weak spot: 86 %**, and 92 % even with nominal sensors. Its failures
   are prop strikes (249 of 834), final position (122), post speed (105) and not seated
   (89): the dock moving under the drone during contact, not navigation.
4. The worst cells are all gusty with noise × 2, 63–77 %.
5. Every failure class is still present at a few percent. Touchdown speed (278) is the class
   the soft cutoff addresses.

### 5.6 Navigation and controller options (2026-11-21)

Four options in the drone's software, each off by default (`Pilot` options in
`docksim/drone.py`, experiments `pd_*` and `base_ctl` in `docksim/matrix.py`):

| option | what it does |
|---|---|
| `soft_cutoff_s` 0.5 | at the cutoff, ramps the collective from the estimated thrust to 0 over 0.5 s instead of switching the motors off |
| `bias_cal_frac` 0.5 | measures each anchor's bias while parked and subtracts it in flight. Half the bias is left as residual, a guess for multipath that does not carry from the pocket to the approach. The filter's noise per anchor includes the residual |
| `dock_ff` | uses the mothership's broadcast acceleration as position-loop feedforward |
| `dob_tau_s` 0.1 | a disturbance observer: measured acceleration minus thrust over mass, low-passed at 0.1 s, cancelled as feedforward. Active in free flight only, handed over to the integrator without a bump before contact |

Each option alone on `pocket_drift`, moderate hover, nominal sensors, 60 trials × 24 cells
(1,440 recoveries):

| variant | recovery | 0 m/s | 3 m/s | **6 m/s** | 9 m/s | main failures |
|---|---|---|---|---|---|---|
| pocket_drift | 0.974 | 0.972 | 0.978 | **0.981** | 0.967 | touchdown speed 24, prop strike 4 |
| + soft cutoff | **0.988** | 0.989 | 0.994 | **0.994** | 0.972 | final position 6, prop strike 4, touchdown speed 1 |
| + parked bias calibration | 0.976 | 0.981 | 0.978 | **0.975** | 0.972 | touchdown speed 21 |
| + dock acceleration feedforward | 0.984 | 0.983 | 0.983 | **0.978** | **0.992** | touchdown speed 15 |
| + disturbance observer | 0.968 | 0.972 | 0.964 | **0.956** | 0.981 | touchdown speed 25, prop strike 11, post speed 4 |
| spec baseline + all four (`base_ctl`) | 0.670 | 0.944 | 0.947 | **0.672** | 0.117 | prop strike 152, not seated 128 |

All four together on `pocket_drift` (`pd_ctl`), and the same without the disturbance
observer (`pd_nodob`), full 288-cell matrix at 30 trials per cell
(8,640 recoveries), against `pocket_drift` and the baseline on the same seeds (1–30):

| condition | baseline | pocket_drift | pd_ctl | pd_nodob (no observer) |
|---|---|---|---|---|
| all 288 cells | 0.382 | 0.915 | **0.940** | **0.943** |
| calm / moderate / gusty hover | 0.420 / 0.389 / 0.339 | 0.955 / 0.933 / 0.858 | 0.986 / 0.968 / **0.867** | 0.984 / 0.969 / **0.876** |
| ranging noise × 1 / × 2 | 0.483 / 0.282 | 0.944 / 0.887 | 0.954 / 0.927 | 0.952 / 0.935 |
| no anchor lost / one lost | 0.472 / 0.292 | 0.933 / 0.898 | 0.947 / 0.933 | 0.956 / 0.931 |
| 0 / 3 / 6 / 9 m/s | 0.576 / 0.567 / 0.350 / 0.036 | 0.922 / 0.922 / 0.915 / 0.902 | 0.944 / 0.937 / 0.949 / 0.931 | 0.945 / 0.947 / 0.946 / 0.935 |
| **target cell, all four sensor conditions** (n = 720) | 0.361 | 0.938 | **0.969** | **0.967** |
| target cell, nominal sensors (n = 180) | 0.589 | 0.972 | 0.983 | 0.978 |
| moderate hover, nominal sensors | 0.601 | 0.969 | 0.986 | 0.988 |
| gusty hover, nominal sensors | 0.521 | 0.915 | **0.908** | 0.918 |
| cells at 95 % or better (30 trials per cell) | 3 of 288 | 122 of 288 | **172 of 288** | 162 of 288 |

Paired trial for trial, 668 recoveries fail without the options and pass with them, and 451
go the other way. Between `pd_ctl` and `pd_nodob` the counts are 436 and 409. About one trial
in ten flips with any change to the software, because each trial is chaotic. Only the net
difference means anything; 436 against 409 is within chance.

What the options show:

1. **The soft cutoff is the one clear gain.** Touchdown-speed failures fall from 150 to
   28–46 in the full matrix. It costs nothing measurable.
2. **Feedforward of the dock's acceleration helps at 9 m/s** (96.7 % to 99.2 %) and is
   otherwise within noise.
3. **Parked bias calibration adds nothing** with pocket beacons. The beacons already fix the
   geometry that made bias matter. The result depends on the assumed 50 % residual.
4. **The disturbance observer hurts on its own and makes no difference in combination.**
   Alone: 97.4 % to 96.8 %, prop strikes 4 to 11; its estimate goes wrong at the hand-over
   near the dock, where contact begins. With the other three options, removing it changes
   recovery from 0.940 to 0.943, within noise. It is not worth its complexity.
5. **The controller alone does not close the gap.** On the spec dock the four options take
   recovery from 60 % to 67 %; at 0–3 m/s they reach 94–95 %, but 6 and 9 m/s stay at 67 %
   and 12 %. Drift and pocket beacons are what move the target cell.
6. **Gusty hover is now the limit** (87 %). Its failures are prop strikes, final position
   and not seated: the dock moving under the drone during contact.

### 5.7 A mothership built to the spec (2026-11-21)

The rows of the mothership spec (REPORT section 5) that the dock sim can represent, on top of
drift, pocket beacons and the three useful controller options (soft cutoff, parked bias
calibration, dock feedforward; no disturbance observer). Experiments `sp_*` in
`docksim/matrix.py`:

| experiment | change to the mothership |
|---|---|
| sp_rotors | lift rotors at z ± 1.00 m (was ± 0.55 m): the strike zone starts 0.51 m outboard of the dock floor (was 0.06 m), and the rotor inflow moves with it |
| sp_steady | half the hover motion at every level: gusty becomes moderate (0.5 m, 0.2 m, 5°, 2° rms), moderate becomes 0.25 m, 0.1 m, 2.5°, 1° |
| sp_bcast | motion broadcast at 50 Hz with 10–20 ms delay (was 10 Hz, 50–100 ms); dock acceleration filter 0.1 s (was 0.3 s) |
| sp_all | all three |

Not represented: the dock on the centre of gravity (the sim already rotates the dock about
its own centre, so every result assumes it), thrust margin, rotor count and wing area (they
act through hover steadiness, which the sim prescribes), payload, endurance and fairing
(sizing).

Each row alone, nominal sensors, 72 cells × 30 trials (2,160 recoveries), against
`pd_nodob` on the same seeds:

| variant | all | calm | moderate | gusty | 9 m/s | target cell | prop strikes |
|---|---|---|---|---|---|---|---|
| pocket_drift | 0.955 | 0.979 | 0.969 | 0.915 | 0.946 | 0.972 | 16 |
| pd_nodob | 0.967 | 0.994 | 0.988 | 0.918 | 0.956 | 0.978 | 15 |
| sp_rotors | 0.967 | 0.994 | 0.988 | 0.919 | 0.954 | 0.978 | **2** |
| **sp_steady** | **0.994** | 0.994 | 0.992 | **0.994** | 0.983 | 0.994 | 2 |
| sp_bcast | 0.968 | 0.996 | 0.997 | 0.910 | 0.956 | 0.989 | 17 |
| **sp_all** | **0.995** | 0.999 | 0.996 | **0.990** | 0.991 | **1.000** | 0 |

All three together, full matrix (288 cells × 30 trials, 8,640 recoveries):

| condition | baseline | pocket_drift | pd_nodob | **sp_all** |
|---|---|---|---|---|
| all 288 cells | 0.382 | 0.915 | 0.943 | **0.982** |
| calm / moderate / gusty hover (spec levels) | 0.420 / 0.389 / 0.339 | 0.955 / 0.933 / 0.858 | 0.984 / 0.969 / 0.876 | 0.985 / 0.984 / **0.976** |
| ranging noise × 2 | 0.282 | 0.887 | 0.935 | 0.977 |
| one airframe anchor lost | 0.292 | 0.898 | 0.931 | 0.975 |
| 9 m/s | 0.036 | 0.902 | 0.935 | 0.971 |
| **target cell, all four sensor conditions** | 0.361 | 0.938 | 0.967 | **0.988** |
| cells at 95 % or better | 3 of 288 | 122 of 288 | 162 of 288 | **254 of 288** |
| worst cell | 0.00 | 0.63 | 0.70 | **0.90** |

The hover levels in this table are the levels the airframe meets the spec in. With half the
motion, the "gusty" row is what a spec mothership does in gusty air.

What it shows:

1. **Steadier hover is the row that matters.** Halving the hover motion takes gusty hover
   from 92 % to 99 % with nominal sensors, and 9 m/s from 96 % to 98 %. The dock's last
   failures were the dock moving under the drone during contact; a steadier mothership
   removes them.
2. **Rotor clearance removes prop strikes (15 to 2) but not failures.** The drones that
   would have hit a rotor still miss the pocket and fail another way (final position, not
   seated). In the air the difference is large: a strike loses the drone and may damage the
   mothership, while a miss can retry. Keep the row for safety, not for the success rate.
3. **The 50 Hz broadcast helps at moderate hover** (98.8 % to 99.7 %, target cell 97.8 %
   to 98.9 %) and not in gusty hover.
4. **Together they reach 98.2 % over all 288 cells**, 98.8 % at the target cell under all
   four sensor conditions, and no cell below 90 %. 254 of 288 cells reach 95 %.
5. **What is left** (155 of 8,640): final position 53, no landing in time 41, touchdown
   speed 18, not seated 16, post speed 14. Prop strikes: 2.
6. **Launch** meets every criterion except "does not touch a post" in every trial, as before.

Caveats: the steadier hover is prescribed, not flown. Whether an airframe meets
"half the Pioneer's assumed hover motion" depends on its size, thrust margin and wing area,
which this sim does not model. The Pioneer's own hover motion is itself an estimate (spec
section 8.2). Drift still assumes a perfect wind estimate.
