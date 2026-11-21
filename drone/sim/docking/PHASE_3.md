# Phase 3 (revised 2026-11-20): launch from and recovery to the passive dock

Branch `docking/phase2-bee35` (local, not pushed). Spec: "Passive four-drone dock:
simulation spec" v0.1. Configuration and sizing: `PHASE_2.md`.

> **Status.** The spec's full matrix (288 cells × 200 trials = 57,600 launches and
> 57,600 recoveries, `baseline`) and the four-anchor variant (`spec4`, 50 per cell) are
> running on the remote Linux box. Section 5 gives the 50-per-cell diagnostic run of the
> previous controller build. The final tables replace it when the run lands
> (`docking/results/dock/summary.md`).

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

### 5.2 Final run (baseline 200 per cell; spec4 50 per cell)

*Pending: written from `docking/results/dock/summary.md` when the run completes.*
