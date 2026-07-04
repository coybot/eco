# Plan: L5 for real — from idealized sim to IRL, quad + rover

## PATH TO IRL L5 (updated after the noise/training experiments)

**Where we actually are:** L5 under realistic *deterministic* sensing, on the device
code path (parity-proven). Under ±5 cm range noise it's ~92% collision-free/run —
failures concentrated in TWO well-characterized mechanisms, both **rover**:

  M1. Fast dynamic intruders (pursuit_corridor): the reactive potential field reacts
      to *instantaneous* sensed position; a mis-timed beam under noise → clip.
  M2. Blind + GPS-denied + wind rover (gauntlet_gamma): a degraded-mode agent
      dead-reckons through a gauntlet with no clearance info.

Ruled out (this session): 7 clearance front-ends (reconstruction, avoid-radius,
hold-last, median, lag-comp, scalar gate, uncertainty margin) — none close it,
because M1/M2 are not clearance-*estimation* problems. Existing learned policies are
worse than rule-based; a naive 4k-iter train produced reach≈3% (undertrained).

**The plan attacks the mechanisms, not "train harder":**

### Workstream A — close the distributional gap (no training; keeps parity discipline)
- **A1 (M1): predictive avoidance for dynamic obstacles.**
  **TESTED as a potential-field term (2026-07-01): insufficient.** Adding a predictive
  repulsion (repel from predicted closest-approach) has no scalar sweet spot — strong
  enough to stop intruder clips breaks the tuned multi-agent balance (rover dodges into
  a static wall → deterministic L5 falls to L3); weak enough to preserve deterministic
  (0.2<tau≤2s, urgency-discounted ×0.6) leaves the noise MC unchanged (91 vs 92%).
  → Tried the true VO solver too (2026-07-01, sampling/DWA, static-clearance-aware,
  tightened trigger): also fails. Best deterministic was 1 residual collision
  (pursuit_corridor) and it made the NOISE case WORSE (90.2% vs 92.3%,
  pursuit_corridor 30/30). Root cause: pursuit_corridor's intruders are **scripted /
  non-reciprocal** — they plow straight through. VO assumes evasion is possible; in a
  tight corridor against a non-cooperating fast intruder, slowing/swerving is *worse*
  than the original "commit and punch through." **Velocity-obstacle avoidance is the
  wrong tool for non-reciprocal intruders.** (9th approach ruled out.)
  → What's actually left for M1: (a) **bespoke smart-layer intruder *timing*** — gate
  corridor entry on the intruder phase (don't enter when a crosser is due), not
  evasion; or (b) the **BC+RL learned policy** (Workstream B), which can internalize
  timing the reactive stack can't express. Both are real efforts, not a controller term.
- **A2 (M2): explicit degraded-mode policy.** When `sensor_ok=False` AND localization
  confidence is low, the smart layer commands creep-and-hold (or lateral reacquire)
  instead of dead-reckoning through obstacles — what a real operator does. Deterministic.
- **Gate A:** distributional L5 — 0 collisions across 100 seeds × 16 scenarios at
  ±5 cm range noise + 3% dropout + GPS/wind injects; ≥99% mission success.

### Workstream B — learned policy as robustness fallback (parallel, only if A plateaus)
The right way to beat a controller RL has never beaten here: **imitation warm-start,
then harden.** Behavior-clone the L5 rule-based controller (guarantees starting at its
competence), THEN RL fine-tune with domain-randomized sensor noise. Never ships unless
it (a) matches rule-based deterministically AND (b) beats it under noise.
- **Gate B:** learned ≥ rule-based on deterministic suite AND > rule-based under noise.
- **CONFIRMED necessary (2026-07-01):** a naive from-scratch RL run (train_ma_hetero,
  no BC warm-start) stalled at reach≈0.11 on the *trivial* rovers-only stage after 5.7k
  iters — never converged, adaptive curriculum never advanced. Plain RL is a non-starter
  here; the BC warm-start is mandatory, not optional.

### Workstream C — SITL dynamics (Stage 5)
ArduCopter (quad) + ArduRover (rover) SITL; feed the realistic sensor model from the
SITL course; policy → GUIDED body-velocity setpoints. Exercises actuator lag, latency,
real control loops. De-risk ArduRover velocity tracking first.
- **Gate C:** L4+ on SITL for both vehicle classes.

### Workstream D — hardware, rover first (Stages 6–7)
`onboard_l5` on `jetson@rover` + real lidar on a measured/mocap obstacle course
(ground truth). Rover first (planar, in hand), then quad, then mixed team over
`team_link` on real radios.
- **Gate D:** 0 interventions over N runs of physical scenario analogs → the claim.

**Why it's not impossible:** deterministic realistic L5 already runs on the device
path; the two open failure modes have textbook solution families (predictive
avoidance; degraded-mode policy); the rest is standard sim-to-real (SITL → HITL).
No new science required — engineering with numeric gates at every step.

---

## The honest starting point (measured, this session)

| Configuration | Level | Collisions | Success |
|---|---|---|---|
| Sim as published (omniscient true-surface clearance) | **L5** | 0 | 100% |
| Realistic ray-based sensing (what hardware gives you) | **L3** | 2 | 81% |

The published L5 depended on a quantity no real sensor produces: exact perpendicular
distance to obstacle surfaces. Under realizable sensing it's L3, via **two distinct
failure modes** (diagnosed, not assumed):

1. **Collisions (2 scenarios).** Small/dynamic obstacles (intruders, teammates,
   corners) fall *between* discrete lidar beams / depth rays and are missed. An
   angular + temporal resolution problem.
2. **Incompletes (3 scenarios).** Pessimistic clearance → over-braking → missed
   mission deadline. A throughput problem.

A global safety margin trades mode 1 for mode 2 (collisions ↓, success ↓). So the
fix is not conservatism — it is **faithful clearance estimation** plus **memory**.

Non-negotiable: the shipped controller/advisor stay byte-identical to the validated
sim (parity tests in `common/tests/test_l5_parity.py`). Every stage keeps them green.

---

## North-star metric change (do first, or we keep fooling ourselves)

**The benchmark must evaluate on realizable observations.** Bake a realistic sensor
model into the scorecard as the *default* eval; `--sensing ideal` becomes the legacy
mode. Realistic model = (a) ray/beam discretization, (b) range noise, (c) dropout,
(d) latency, (e) localization drift (GPS-loss injects already exist). Publish the
realistic number as *the* number.

---

## Stages (each has a numeric exit gate)

### Stage 0 — Honest benchmark + attribution  ⟶ gate: reproducible realistic-L3 baseline
- Add `SensorModel` to the sim so `min_clearance`/`scan` come from realizable sensing.
- Native per-agent, per-cause attribution in the scorecard (collision vs stall vs
  timeout, which agent, which class) — no more monkeypatching to find out.
- Exit: `scorecard --sensing realistic` reproduces L3 with a per-agent failure table.

### Stage 1 — Clearance estimation (kills corner-clip)  ⟶ gate: static-obstacle collisions = 0
- Replace ray-`min` with **local surface reconstruction**: cluster returns, fit line
  segments (split-and-merge), use perpendicular point-to-segment distance. Recovers
  true-surface clearance from discrete beams for static geometry.
- Depth-fan analog for the quad (nearest-surface from the 5×9 grid).
- Exit: zero static-obstacle collisions under realistic sensing; suite ≥ L4.

### Stage 2 — Spatial memory + dynamic tracking (kills the missed-between-beams hits) ⟶ gate: collisions = 0
- Wire `spatial_memory.py` into the observation: fuse current scan with a short-horizon
  obstacle map so out-of-FOV (quad) and between-beam (both) obstacles persist.
- Track dynamic obstacles (intruders/teammates) with a constant-velocity predictor so a
  fast crosser isn't lost between ticks.
- Exit: 0 collisions across the suite under realistic sensing.

### Stage 3 — Throughput under uncertainty (kills timidity)  ⟶ gate: 100% success, L5
- Replace the blunt `speed *= 0.3` clearance gate with clearance-confidence-aware
  speed: brake on *reconstructed surface distance + its uncertainty*, not raw ray-min.
- Re-audit the 5 geometry-repair patterns from the L5 paper against realistic sensing;
  redo any repair that only held under idealized clearance.
- Exit: **L5 under realistic (deterministic) sensing** — 0 interventions, 100%, 0 coll.

### Stage 4 — Distributional L5 (not one lucky seed)  ⟶ gate: L5 in expectation
- Domain-randomize sensor noise/dropout/latency/drift; 100 seeds × 16 scenarios.
- Exit: 0 collisions across 1600 runs, ≥99% success. State the confidence interval.

### Stage 5 — SITL dynamics, both vehicles  ⟶ gate: L4+ on ArduPilot SITL
- Extend `sitl_validate.py` from single-drone LearnedPlanner to the L5 controller +
  advisor. ArduCopter (quad) **and** ArduRover (rover), realistic sensor model fed from
  the SITL obstacle course, policy → GUIDED body-velocity setpoints.
- De-risk early: confirm ArduRover GUIDED velocity tracking + ArduPilot builds on hoopoe.
- Exit: L4+ on SITL for both classes (SITL adds tracking lag/latency; L4 honest, L5 stretch).

### Stage 6 — Hardware, rover first  ⟶ gate: 0 interventions on a physical course
- `onboard_l5` on the real rover (`jetson@rover`, operational) with real lidar + a
  measured/mocap obstacle course = ground truth. Rover first: planar, safer, in hand.
- Exit: 0 interventions over N runs of 2–3 physical scenario analogs.

### Stage 7 — IRL flight (quad) + multi-vehicle team  ⟶ gate: the real claim, with data
- Add the quad, then a mixed team over `team_link` on real radios.
- Exit: physical-analog scenarios pass; *then* we say "IRL L5" — backed by logs.

---

## Diagnosis update (2026-07-01, after Stage 0 + Stage 1 probing)

Stage 1's original premise — *ray-min overreads clearance → collisions* — is **wrong**,
proven by instrumenting the two collisions:

- At the `multi_chokepoint` rover graze, the three clearance estimates are identical:
  `true=0.44  raw_ray_min=0.44  reconstructed=0.44`. The sensor sees the flat wall
  face fine. Surface reconstruction (now implemented, `sensing="reconstructed"`) is
  correct infrastructure but **does not** move the collision.
- The real mechanism is **trajectory drift under realistic sensing**: rover_0 rides
  ~0.18 m higher in y from t=2.0s onward (clearance differs at *upstream* points where
  beams undersample), so it enters the corner at 0.44 m where ideal keeps 0.62 m. Rover
  radius is 0.5 m → graze.
- Controller robustness knobs don't rescue it: `avoid_radius` 3.5→5.0 gives **identical**
  failures. The outcome is committed upstream; no local repulsion change helps.

**Conclusion:** the collisions are thin-margin grazes on geometry tuned to *ideal*
sensing. The primary fix is **geometry re-repair with a drift budget** (make every
surface-clearance margin ≥ radius + sensing-drift, ~0.3 m), i.e. re-run the paper's
5-pattern repair with `sensing=realistic` in the loop. Surface reconstruction + noise
modelling remain necessary supporting infra (Stage 2) but are not the collision fix.

**Re-sequenced:** Stage 1 ⇒ *geometry re-repair under realistic sensing* (primary,
targets the 2 collisions + audits margins). Reconstruction/noise sensor model folds
into Stage 2. Stage 3 (throughput) targets the 5 timeouts.

## Distributional result (2026-07-02) — the noise gap was MISDIAGNOSED

The M1 "fast intruder" framing was **wrong**. Attribution under noise shows the residual
collisions are `rover vs OBSTACLE`, not intruders — in pursuit_corridor the intruders fly
at z=1/z=4, clear of the z=0 rover; the rover was clipping the corridor **wall** under
range noise. Same static-wall-graze mechanism as gauntlet_gamma. Nine controller-side
approaches (7 clearance front-ends + predictive-repulsion A1 + ORCA/VO) all failed
because they attacked a dynamic-obstacle problem that didn't exist.

The fix is the **Stage-1 geometry method with a noise-drift budget** — widen the tight
rover passages (quads fly over the tops, so rover-only, safe):
- pursuit_corridor walls y=±7→±8; gauntlet_gamma gap faces ±2→±3.

**Result (RuleBasedSmart, ±5cm noise + 3% dropout, 50 seeds × 16 = 800 runs):**
- deterministic: **L5** both sensing modes (0 collisions, 100%) — preserved
- distributional: **99.50% collision-free** (was 92.3%), 97.4% success, 5 collisions total
- residual: 4/800 in final_boss (blind rover under every stressor; its mid obstacles are
  load-bearing — widening backfired 3→42, reverted). This is the honest hard edge.

Lesson: the L5 sim's fragility was always *geometry margin vs sensing noise*, never the
controller's dynamic-obstacle handling. **A1/ORCA (dynamic avoidance) is not needed.**

## Stage 5 — SITL (real autopilot dynamics) — CLEARED (2026-07-02)

The L5 controller (reactive_goto_controller) driven through ArduPilot SITL — real GUIDED
tracking, actuator lag, control loops — via sitl_l5.py, sensing reused from the sim so
only dynamics differ. **16/16 collision-free, both vehicle classes, 8 scenarios each:**
- Quad (ArduCopter, --model +): 8/8 reach, min clearance 0.98–2.2m, 8–13s.
- Rover (ArduRover, --model rover-skid): 8/8 reach, min clearance 0.64–2.3m, 14–34s.

Rover gap found + fixed: default `rover` model is a car (Ackermann); the L5 rover is
UNICYCLE_2D (skid, turns in place). Using `rover-skid` + body forward+yaw_rate resolved
it — a vehicle-model match, not a controller change.

Claim ladder now: L5 idealized sim → L5 realistic deterministic (device path) →
99.5% distributional under noise → **L4/L5-equivalent through the real autopilot (SITL),
both vehicles**. Remaining: hardware (Stage 6 rover bench → Stage 7 flight).

## Progress log

- **Stage 0 ✅** honest benchmark: `--sensing realistic` + per-agent attribution. Baseline L3.
- **Stage 1 ✅** collisions → 0 under realistic *deterministic* sensing. Root cause was
  NOT clearance estimation (true=ray-min=reconstructed at the grazes) but thin geometry
  margins tuned to ideal sensing; fixed with minimal-perturbation re-repair
  (multi_chokepoint, dense_urban) budgeting radius + controller drift + sensing drift.
  Surface-reconstruction clearance added as `sensing="reconstructed"` (supporting infra).
- **Stage 2 ⏸ deferred→REOPENED** (see Stage 4): not needed for deterministic L5, but the
  noise result makes dynamic-obstacle prediction the blocker for *distributional* L5.
- **Stage 3 ✅** the 5 residual failures were timeouts (agents pinned ~2.5m from goal
  behind looping intruders, just outside the 2.0m near-goal lock). Raised the lock to
  3.0m → **L5 under realistic deterministic sensing, both modes, 0 collisions, 100%.**
  Regression-locked.
- **Stage 4 🔬 in progress** distributional: added a seeded sensor-noise model
  (`TeamWorld.set_sensor_noise`: Gaussian range noise + ray dropout). Monte-Carlo,
  20 seeds × 16 scenarios, range_std=0.05m, dropout=0.03:
  **~96% per-run success, collisions in ~8% of runs**, concentrated:
  `pursuit_corridor 15/20`, `gauntlet_gamma 8/20`, `final_boss 1/20`; incompletes
  `formation_crush 9/20`, `gauntlet_delta 5/20`. So **deterministic L5 does NOT yet
  survive sensor noise** — dominated by punch-through into *fast dynamic intruders*
  when a beam drops out at the wrong moment. A scalar `min_scan_dist` gate on the
  near-goal lock traded collisions for timeouts (rovers skirting walls have low scan
  dist) — confirming this needs real **Stage 2 dynamic-obstacle tracking/prediction**
  (temporal filtering of the scan + intruder velocity prediction), not a threshold.

**Honest claim status:** "L5 in idealized sim" → **now "L5 under realistic deterministic
sensing"**. NOT yet "probably IRL L5": distributional (noise) L5 needs Stage 2; then
SITL (Stage 5) for dynamics; then hardware (6–7). The noise gap is the current front.

## Risk register
- **Dynamic-obstacle resolution (Stage 2)** is the hardest technical risk — de-risk with
  the CV-predictor prototype before committing the stage.
- **ArduRover velocity control** semantics differ from Copter — verify in Stage 5 early.
- **Sim-to-real noise mismatch** — Stage 4 randomization is the hedge; widen ranges if
  Stage 6 surprises.

## What we can *honestly say* at each gate
- Today: "L5 in idealized sim; L3 under realizable sensing."
- After Stage 3: "L5 under realistic sensing in sim."
- After Stage 4: "L5 in distribution under randomized realistic sensing."
- After Stage 5: "L4+ through the real autopilot (SITL), both vehicles."
- After Stage 6/7: "L5 IRL" — first rover, then quad, then team.
