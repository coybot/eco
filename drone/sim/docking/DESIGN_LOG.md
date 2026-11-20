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
