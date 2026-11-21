# Docking design log

What was tried, in order, what the simulator said, and what was kept or dropped. The
numbers in section A come from development batches: seeded, randomised, light turbulence,
19 m/s, near-body flow gain 1.0. Section B holds the final paired comparisons
(`phase3.py`, 120 seeds per arm, the same seeds for every arm).

**Caveat for every entry.** The airflow next to the mothership comes from an UNVALIDATED
model (`wake.py`, gain 1.0). Phase 4 raises it to find the margin.

## A. Development history (how the design got here)

| # | change | trials | captured | strikes | what it showed | kept? |
|---|---|---|---|---|---|---|
| A1 | First working loop: rigid 0.42 m probe on body +z; funnel 0.20 m mouth, 40 deg half-angle, pitched 48 deg under the fuselage; RTK + link + camera relative navigation | 3 calm | 3/3 | 0 | concept works: ~2 cm relative-navigation error, 6 cm mouth-entry error | yes |
| A2 | Same, randomised, light turbulence | 24 | 20 (83 %) | 0 | three never left station (0.15 m tolerance too tight in gusts). One run's relative navigation diverged by 3.2 m after a **500 N contact spike** | fix below |
| A3 | Funnel rim modelled as an edge; penalty depth capped at 2 cm (no kilonewton forces from a mis-classified penetration) | 40 | 34 (85 %) | 3 | spikes still ~640 N, not caused by the contact model | yes (correctness) |
| A4 | Acceleration disturbance observer (IMU vs commanded), 1.5 Hz | 40 | 27 (68 %) | 2 | more corridor aborts; it fights the tilt limit and the motor lag | **dropped** |
| A5 | **Gimballed probe** (2-axis, pivot at the quad CG, 0.15 N m/rad spring, 0.03 N m s/rad damper, 25 deg stop) vs rigid | 40 + 40 | 38 vs 34 | 1 vs 3 | **Rigid probe: a 20 N side load at a 0.42 m tip is 8 N m, about 8x the quad's control torque.** The funnel twists the quad, the probe whips, contact chatters. Median peak force 30 N vs 81 N; worst 141 N vs 644 N; worst relative-navigation error 5 cm vs 7.1 m | **gimbal kept** |
| A6 | Committed-phase guidance: keep reduced lateral control until the tip is in the mouth; abort on a predicted mouth miss or on passing the throat unlatched. Quad guards now collide (penalty) with fuselage, wing, rail and funnel exterior instead of passing through | 80 | 77 (96 %) | 0 | removed a hole where a quad that missed the mouth kept "closing" past the throat, up beside the fuselage, into a lift rotor | yes |
| A7 | 6 N axial push-in once seated (the latch detent needs 3 N; the position loop gave < 1 N) | 80 | 66 (83 %) | 0 | latch now engages, but 12 seat overloads (> 80 N, up to 162 N): a 1 kg quad at 0.5 m/s on a rigid stop | partial |
| A8 | **Sprung seat** (1500 N/m, 50 N s/m, 3 cm travel) + gimbal angle encoders (1 deg) so guidance knows where the tip is + 6 cm pylon lowering the throat | 80 | **79 (98.8 %)** | **0** | seat force max 67 N (< 80 N); 68/80 first attempt | **yes: baseline** |

Bugs found during this phase that were not design choices:

- **Camera never fed:** no frames were pushed into the camera model, so relative
  navigation ran on RTK + link alone.
- **Over-trusting the link:** relative navigation used the link-differenced position as
  if the quad's own position were exact. During a GNSS outage burst (a modelled event),
  0.3 m of own-navigation drift went straight into it. The quad's own covariance is now
  added to the measurement variance, and gate recovery was added.
- **Tumbling crashed the sim:** a quad with a dead motor spun up without bound until the
  run crashed. Rotational aerodynamic damping and a 200 rad/s guard were added.

## B. Final comparisons (phase3.py)

TBD after run.

## C. Passive dock recovery controller (Bee35, 2026-11-20)

Development batches of 16–32 seeded recoveries in the target cell (moderate hover, 6 m/s),
after calm checks. "Pass" uses every criterion in spec section 12. Only the end state is
committed; this table is the record of why the controller looks as it does.

| # | change | target-cell pass | what the simulator showed | kept? |
|---|---|---|---|---|
| C1 | Spec sequence, 6-state filter (UWB + down range + flow), PID position loop | calm 14/16, target 3/16 | the filter took no measurements at first (samples dropped between filter ticks: a bug). After the fix, height from UWB alone near the anchor plane drifted 0.2 m and the drone sank onto the lip | fix |
| C2 | Down range used at every height, height re-anchored after 3 rejections | — | the beam (0.085 m forward of centre) fell into the 0.130 m box well or onto the lip slope with 5–10 cm of position error; terrain-matched height then corrupted the estimate | partly |
| C3 | Range used only where the map is flat within ±3 cm | 0/24 | almost nowhere on the dock is flat at that scale: height fell back to UWB | **dropped** |
| C4 | Nearest-surface association (3×3 map samples within ±3 cm); sensor moved to (0.085, 0, −0.100) | 3/24 | height fixed. Truth diagnostics then showed control error, not navigation error | kept |
| C5 | Position loop kp 4 → 9, kv 4 → 6, ki 1 → 4 | 1/24 | drag swings −0.6 to −1.1 N in 15 % turbulence; kp 4 let 0.15–0.2 m through and tripped the 0.20 m abort. Stiffer loop: errors fine, but approaches now failed | kept |
| C6 | Descent reference decelerates; vertical integrator frozen while descending; approach starts only once height has settled | 3/24 | integrator wind-up had carried the drone 0.13 m below 0.17 m onto the lip | kept |
| C7 | Stop detection on a windowed mean speed | 5/24 | spec's "sideways speed < 0.02 m/s" is at the noise level of the estimate | superseded by C11 |
| C8 | **Funnel test: a free drone pushed with 0.5 N seats from ±0.09 m (error ≤ 1.6 mm)** | — | the geometry works. With 0.5 N push and μ 0.2 on a 27° fence, the post slides only if x stiffness is low: at kp 3 the drone stalls about 9 cm off centre, which looks like a catch | analysis |
| C9 | Compliant x through the approach (several variants, a separate funnel stage) | 2–5/24 | limp x lets gusts push the drone 0.1–0.15 m sideways before contact | superseded |
| C10 | **Yaw gains 30 → 120.** The post contact torqued the drone 1° → 36° about the post | 12/24 | 0.5 N on a fence at the bracket's 0.11 m lever arm is about 0.06 N m, the same as the quad's whole yaw authority at hover. A post in a slot does not resist yaw. | kept |
| C11 | Stop and catch measured with optical flow (velocity relative to the dock), not the UWB-fused estimate | — | UWB anchor biases kept moving the estimate while the drone was physically blocked | kept |
| C12 | **Yaw mixed last** (thrust, roll, pitch first) | 16/32 | when yaw saturated, plain clipping took thrust authority: the drone climbed and fell off the post | kept |
| C13 | x stiff again after the catch; hold height at the post until heading is within 4°; z holds the post position with the integrator running, plus the spec's 0.5 N push | 17–18/32 | net x force at the post yaws the drone about the post (pushing toward a pivot behind the centre of mass is unstable); a fixed push alone lost the post in port-side gusts | kept |
| C14 | x compliant only after 0.10 m of travel (when the mouth can reach the post) | 19/32 | less gust drift before contact | kept |
| C15 | Catch must be an abrupt stop | 10/32 (calm 22/32) | rejects real catches: approaches run slower than the threshold | **dropped** (option `abrupt_catch`) |
| C16 | After a "no catch" abort, retry at x ± 0.08 m | 19/32 (calm 29/32) | a constant UWB bias makes a plain retry miss the same way | kept |
| C17 | Integrator limit 3 → 6 m/s² | 14/24 (9 m/s: 1/24) | no effect in the dock sim. In the mission sim the 2 m/s² limit let 10 m/s gusts blow the drone 100 m downwind | kept |
| C18 | Soft cutoff: collective ramps from the estimated thrust to 0 over 0.5 s, instead of motors off (`soft_cutoff_s`) | pocket_drift moderate 0.974 → **0.988**; target cell 0.981 → 0.994 | replays of three 0.54–0.62 m/s touchdowns gave 0.19–0.35 m/s. Touchdown failures 24 → 1 of 1,440 | option, on in `pd_ctl` |
| C19 | UWB bias calibrated in the pocket before launch, half the bias left as residual (`bias_cal_frac`) | 0.974 → 0.976 | within noise. With pocket beacons the remaining bias does not decide the catch. The 0.5 residual is a guess for multipath that does not carry from the pocket to the approach | option, on in `pd_ctl` |
| C20 | Mothership acceleration from the broadcast, as feedforward (`dock_ff`) | 0.974 → 0.984; 9 m/s 0.967 → 0.992 | small gain, mostly at 9 m/s | option, on in `pd_ctl` |
| C21 | Disturbance observer (thrust and IMU, 0.1 s low-pass), free flight only, handed over to the integrator without a bump (`dob_tau_s`) | 0.974 → **0.968** | prop strikes 4 → 11, post speed 0 → 4. It cancels gusts well in free flight, but its estimate is wrong at the hand-over near the dock, where ground effect and contact begin | option, on in `pd_ctl`; likely better off (Phase 3 section 5.6) |

What remains is not a tuning problem. See Phase 3: wind above about 6 m/s, the one-sided
bracket's yaw torque against a small quad's yaw authority, and the UWB bias against the
±0.10 m mouth.
