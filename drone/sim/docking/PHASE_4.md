# Phase 4 (revised 2026-11-20): the balcony mission, and the whole delivery

Branch `docking/phase2-bee35` (local, not pushed). Code: `mission/`. Numbers:
`balcony_mission` in `docking/vehicles.yaml`.

## 1. What is simulated

A Bee35 flies one delivery:

1. Start from the dock's depart point: 2 m up and 3 m to starboard of its pocket (Phase 3's
   launch hand-off).
2. Fly to the target balcony.
3. Set the coffee box down on the mark.
4. Fly back to the recovery start point: 3 m up and 3 m starboard of its pocket (Phase 3's
   recovery hand-off).

The dock steps are Phase 3's. This phase covers everything between them.

**The building (scenarios B1–B6, seeded).** A stacked facade, with balconies above, below
and on both sides of the target, each with railings, slabs and dividers. Following the
user's instruction, there are no trees, wires, nets or awnings: the path from the sky to the
facade is clear.

| | what varies |
|---|---|
| B1 | typical: 1.2–1.8 m deep, 2.5–4 m wide, solid railing 1.0–1.15 m, ceiling 2.6–2.9 m, floors 2–12 |
| B2 | glass railing: UWB passes through; the down range has no reading on it |
| B3 | small: 0.9–1.1 m deep, 1.5–2.0 m wide |
| B4 | recessed loggia, 2.0–2.5 m deep, with side walls |
| B5 | high floor (18–28) in strong wind (7–11 m/s), gusty hover |
| B6 | sign 0.4–0.6 m from a side divider |

Further settings:

- **Mothership hover:** 20–60 m out from the facade, ±20 m lateral, at 70 m AGL or 10 m above
  the roof, nose into the wind, with Phase 3's hover motion.
- **Wind:** the urban model from the earlier delivery work, with mean and shear, turbulence,
  gusts, and windward up/downdrafts with leeward shelter. Mean 1–7 m/s (B5: 7–11 m/s).

**The sign** ("drop the coffee here"). Three UWB beacons in an equilateral triangle on the
back wall:

- side 0.6 m in the baseline (an A1/A2-poster-sized sign), 0.3 and 1.0 m as variants;
- centre 1.5 m above the floor;
- the drop mark on the floor, 0.6 m out from the wall under the sign.

These three numbers are install data that the drone knows. The user did not mention a
barometer in the sign, so the baseline has none; variant `sign_baro` adds one.

**The drone** has no camera and no GNSS. Its sensors use Phase 3's models:

- UWB to the sign's three beacons and to the mothership's three anchors, with
  non-line-of-sight bias when the line crosses concrete;
- barometer;
- down range and flow (valid below 8 m; no reading on glass);
- IMU;
- the mothership's broadcast.

**The box release is passive.** The drone lowers the box until the down range reads 0.15 m,
when the box touches the floor. It then slides 0.15 m along its own x axis to pull the fork
rails out from under the rim, and climbs.

## 2. Navigation and guidance

**One filter in the sign frame, 13 states:**

- position and velocity;
- barometer bias;
- **the mothership's frame error, split in two**: its constant survey error of the sign
  (3 states) and its broadcast pose error (3 states, Gauss–Markov, σ 0.5 m, τ 5 s).

The mothership places its anchors in the sign frame using its own estimate of where the sign
is, so all three anchors share one error. That error must be a state, and both shortcuts
failed in development:

- treating it as independent range noise made the filter overconfident (60 correlated ranges
  per second) and it rejected the sign beacons;
- a random walk let its poorly observed vertical part drift to the mirror solution near the
  near-coplanar mothership anchors.

The filter re-opens its frame-error covariance after 1 s of continuous rejections from one
source.

**The last 8 m to the mothership** run on Phase 3's dock-frame filter: UWB to the mothership
triangle, IMU, and a weak barometer. Guidance there works in dock coordinates. Switching at
25 m failed, because the 3 m triangle gives poor geometry from that far.

**The mission states:**

| step | what the drone does |
|---|---|
| TRANSIT | to a point 10 m out from the wall at entry height, at 5 m/s, slowing to 1 m/s from 12 m |
| APPROACH | level, to the gate 4 m out |
| ENTER | at 0.5 m/s to above the drop mark, at entry height: underside 1.85 m above the floor, so the box clears a 1.15 m railing by 0.57 m and the drone clears a 2.6 m slab by 0.69 m |
| SET_DOWN, RELEASE | as described in section 1 |
| CLIMB, EXIT | back out to the gate at entry height |
| RETURN | to the recovery start point |
| HANDOVER | within 0.3 m for 1 s, in dock coordinates |

**Give-ups:**

- 120 s at the balcony without a drop;
- the estimated return energy reaching into a 20 % reserve;
- 60 s near the mothership without a handover.

## 3. Results (300 missions per scenario per variant; `docking/results/mission/summary.md`)

### Baseline: 0.6 m sign, no sign barometer

| scen | delivered | delivered and back at the dock | collisions | box from mark, median / p90 | handover error, median / p90 | time | energy |
|---|---|---|---|---|---|---|---|
| B1 typical | 1.000 | 0.997 | 0 | 0.06 / 0.10 m | 0.26 / 0.40 m | 72 s | 5.5 Wh |
| B2 glass railing | 0.993 | 0.990 | 2 | 0.05 / 0.09 m | 0.25 / 0.39 m | 71 s | 5.5 Wh |
| B3 small | 1.000 | 0.997 | 0 | 0.06 / 0.10 m | 0.26 / 0.38 m | 71 s | 5.5 Wh |
| B4 loggia | 0.993 | 0.987 | 2 | 0.06 / 0.09 m | 0.26 / 0.39 m | 72 s | 5.5 Wh |
| B5 strong wind | 1.000 | 0.917 | 9 (sign) | 0.07 / 0.13 m | 0.22 / 0.38 m | 71 s | 5.9 Wh |
| B6 sign by a divider | 0.950 | 0.947 | 15 (divider 10, railing 4) | 0.06 / 0.09 m | 0.25 / 0.38 m | 71 s | 5.5 Wh |
| **all** | **0.989** | **0.972** | 28 | 0.06 / 0.10 m | 0.25 / 0.39 m | 71 s | 5.5 Wh |

How the failures happen:

- **B6 (sign 0.4–0.6 m from a divider):** lateral error at the balcony. At the gate, 4 m out,
  the drone is about 0.5 m from where it thinks it is (median). A 0.6 m triangle gives
  lateral DOP of about 7 at that distance.
- **B5 (strong wind):** returns that time out at the mothership (16), and set-down contacts
  with the sign in facade gusts (9).

An earlier version of the mission lost 54 % of the B5 returns. The cause was the Bee35 tilt
limit of 35° borrowed from the dock approach: it caps airspeed at about 14 m/s, below the
gusts above a roof in 10 m/s wind. Transit now allows 55°; the airframe has a thrust/weight
ratio of about 6.

### Variants (all scenarios)

| variant | delivered and back | collisions | box from mark, median | gate error, median |
|---|---|---|---|---|
| baseline (0.6 m sign) | **0.972** | 28 | 0.06 m | 0.48 m |
| **0.3 m sign** | **0.861** | 222 (railing 136, slab 36) | 0.08 m | 0.78 m |
| 1.0 m sign | 0.917 (B1–B5: 0.971) | 123 (divider 87, all in B6) | 0.05 m | 0.33 m |
| sign with a barometer | 0.967 | 37 | 0.06 m | 0.47 m |
| UWB noise and bias ×2 | **0.849** | 245 (railing 140) | 0.10 m | 0.79 m |

- **Sign size matters most.** A 0.3 m triangle loses 11 points, mostly to railing and slab
  strikes. Its vertical baseline (0.26 m) gives about 0.4 m height error at the railing,
  against 0.57–0.69 m of clearance.
- **A 1.0 m sign is not better than 0.6 m here**, but only because of a scenario artifact.
  In B6 a 1.0 m sign 0.4–0.6 m from a divider overlaps the divider itself, which can't
  happen in reality. Without B6, 0.6 m and 1.0 m perform the same (0.977 vs 0.971). The gate
  error does fall from 0.48 to 0.33 m. Recommendation: at least 0.6 m.
- **A barometer in the sign does not help** (0.967 vs 0.972). Once the drone is over the
  balcony, the down range fixes height better. Before that, the sign triangle's vertical
  information and the drone's own barometer suffice for a 1.6 m tall entry slot.
- **UWB quality matters as much as sign size.** Doubling noise and bias costs 12 points.

### What the user's beacon concept needs

The three-beacon sign works for the balcony, provided it is at least 0.6 m across and the
drop mark is at least 1 m from a divider. The three-beacon mothership triangle works for
the return to within 0.3 m of the recovery start point (handover error p90 0.39 m). The
weak link is the dock itself (Phase 3), not the delivery.
