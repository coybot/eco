# Follow-target contract

How "follow me" works across vehicle classes. One contract, three geometries, two
implementations (Swift for phrover, Python for the aerial platforms).

The two stacks are separate deployables with no shared import path — the same split
`mission_vocab.py` documents for the cloud planner vs. the on-device dispatcher — so this
document is the canonical source and each stack mirrors it. Names below are the Swift
ones; the Python side uses the same shapes under `snake_case`.

## Why it is not navigation

Every nav path in the repo assumes a **static goal**, and the assumption is load-bearing:

| Mechanism | Assumes | Breaks on a moving target |
|---|---|---|
| `DriveProgressWatchdog` | distance to goal shrinks | a correctly held stand-off makes no progress → "Navigation stalled" |
| `PursuitController.reachedGoal` | the trip ends on arrival | following never arrives |
| `visualTargetApproachDecision` | brake and latch `.arrived` at a stand-off | the target keeps moving |
| `reasoning_loop`'s `ORBIT_POINT` | the centre is fixed | the centre is an estimate that moves |

So following is a **sibling** of navigation, not a mode of it. It reuses everything below
the loop (`FollowPolicy`, `PursuitController`, `ObstacleGuard`, `RotationCommand`,
`AStarPlanner`, and on the aerial side `Backend.loiter` / `search_patterns.orbit`) and
replaces only the loop and its terminal conditions.

## Shapes

- **`TargetSpec`** — free text naming what to follow ("me", "the person in the hat").
  Grounding quality is whatever the detector delivers; the text is carried verbatim
  regardless, because a smarter grounder can use it and the vehicle speaks it back when
  confirming the lock.
- **`TargetObservation`** — one candidate sighting, already grounded to the nav plane
  (`label`, `confidence`, `worldPoint`). Unprojection stays on the caller's side of this
  boundary so association is testable without ARKit or a sim.
- **`TargetTrack`** — the persistent estimate: `id`, `position`, `velocity`, `lastSeenAt`,
  `confidence`, plus `predictedPosition(after:)`.
- **`FollowPolicy`** — pure geometry. Takes own pose + track, returns a `Motion` and the
  signed range/bearing errors. Holds one piece of state: the deadband latch.

## The three geometries

Keyed to `vehicle_class.py`'s `Kinematics`, never to a vtype string.

| Class | Vehicles | Geometry | Can hold station? |
|---|---|---|---|
| `UNICYCLE_2D` | phrover, rover | stand-off on the target bearing; turn in place when badly off-boresight | yes — stop |
| `HOLONOMIC_3D` | quadcopter, crazyflie | stand-off in plan view + altitude hold, yaw to centre | yes — hover |
| `COORDINATED_TURN_3D` | fixed-wing | orbit whose centre is slaved to the predicted position, sensor held on centre | **no** |

Two rules fall out of the hardware, not from tuning:

1. **A ground rover never reverses to restore stand-off.** No rear sensing, and
   `phrover_manager.gd`'s person governor documents three rejected retreat/dodge designs
   before settling on a plain stop as the only provably safe response. A multirotor may.
2. **Speed decides what is even possible.** phrover tops out at 0.35 m/s against a 0.8–1.3
   m/s walk: it holds station with someone walking deliberately slowly and falls behind
   anyone else (`FollowPolicyTests.testFallsBehindANormalWalkingPace` asserts exactly
   this). A quadcopter at 3.0 m/s is comfortable. A fixed-wing at 25 m/s cruise and a
   ~42 m turn radius can only orbit — there is no speed at which it trails a pedestrian.

### The deadband

The single most important number. Enter and exit thresholds differ (0.25 m / 0.60 m for
ground); without the gap the vehicle creeps forward and back around the stand-off every
tick. The latch is state, so it is asserted directly rather than inferred from sampling.

## Identity — the actual hard part

Detectors here emit **no track ids**: phrover's `Detector` reports a bare COCO class per
frame. Identity is reconstructed by gating candidates against the predicted position
(0.8 m). That gate is the whole defence against following the wrong person.

- While the lock is fresh, a candidate outside the gate is **not** adopted — grabbing
  whatever else is in frame is precisely how a follow silently transfers to a passer-by.
- After `reacquireAfter`, the lock will re-attach to an ungated candidate, and
  `unverifiedReacquisitions` is incremented. Non-zero means the vehicle *may* be following
  someone else. It is surfaced, never hidden: without appearance re-identification nothing
  downstream can tell.
- The cloud brain's `imagePoint` is treated as a **selector, not a coordinate**. The point
  was chosen against a frame a round-trip old; by the time it lands, a walking person has
  moved and that pixel is often the wall behind them. It picks the label and seeds the
  tracker; association in the current frame does the rest.

**Open-vocabulary asymmetry:** the aerial stacks already have `OpenVocabDetector`
(YOLO-World, `set_classes`) and `GroundingDINO` on-device, so "the guy with the hat" is
more tractable there than on phrover's closed COCO set. On-device appearance re-ID over
the box crop is the shared upgrade that fixes both, and would also make the lock work
offline, where `OnDeviceBrain`'s substring grounding cannot.

## Safety

The target being followed **is** the forward obstacle, so the clearance stop has to be
relaxed — but only for them:

> Suppress the forward-clearance stop only while the cone reading agrees with the target's
> own range to within `followClearanceMatchTolerance` (0.30 m). Anything else entering the
> cone still stops the vehicle.

Comms watchdog and tip detection stay unconditional. At the 2.5 m ground stand-off the
exemption should never fire in normal operation — it matters only when the target backs
into the vehicle, and there the right response is to stop.

Following is open-ended by nature, so it carries a hard time budget
(`maxFollowDuration`, 300 s) per `adr/0011-time-budget-rule.md`.

**Loss state machine:** `acquiring → following → searching → lost`. On dropout, coast on
the prediction, then pulse-turn toward the last known bearing (the same pulse-and-settle
cadence `rotateForScan` uses — spinning continuously sweeps past the target before
detection gets a stable frame), then give up with a spoken reason. A fixed-wing cannot
stop, so it keeps orbiting the last estimate and widens the circle instead.

## Command surface

`RoverDecision.follow(NavigationTarget)` (Swift) / `action: "follow"` on `/rover/act`.

Following is a **mode, not a trip**: the decision starts it and returns. The mission loop
then leaves it alone for `followReviewInterval` (10 s) at a time, so the brain gets a
periodic look-in — with `followState` in its context — without burning its tick budget
spinning. Repeating `.follow` for the same target is deliberately a no-op: a brain
re-stating its intent must not restart the lock and lose the track it already has.
`stop`, an emergency-stop utterance, and `MissionAgent`'s own cancel all end it.

## Standard metrics

The same assertions serve every class, so an aerial implementation is judged the way the
ground one was:

- time-in-band % and max excursion from the stand-off
- track-id switches (against sim ground truth) and `unverifiedReacquisitions`
- collisions with the target: zero
- target-lost events and recovery count
- aerial only: minimum slant range and altitude-floor violations

## Status

All four vehicle classes follow, and all four are tested against the live Godot sim.

| Piece | State |
|---|---|
| `FollowPolicy` / `FollowParams` — three geometries, both stacks | `RoverNavTests/FollowPolicyTests` (Swift, 15), `common/tests/test_follow.py` (Python, 23) |
| `TargetTracker` + safety gate | `PhroverKitTests/FollowTrackingTests` (16) and the Python suite above |
| phrover — `FollowController` | `PhroverSimTests/FollowMeTests` (3), live depot sim |
| quadcopter, rover, fixed-wing — `FollowExecutor` | `sim/tests/test_follow_target.py`, live depot + surveil_truck sims |
| `.follow` decision + `/rover/act` contract (phrover) | e2e fast tier |
| `follow_target` action + `reasoning_loop` dispatch (aerial) | implemented; vocab parity asserted at import |
| Appearance re-ID | **not built** |

Measured in sim (see the test's own output):

| Class | Geometry | Result |
|---|---|---|
| quadcopter | holonomic, 3 m stand-off | 100% in band, advanced 4.4 m behind the walker |
| rover | unicycle, 2.5 m stand-off | 90% in band, advanced 5.4 m |
| fixed-wing | orbit, 41.7 m radius | 100% in ring, 3.4 revolutions of a *moving* truck, never lost it |
| phrover | unicycle, 2.5 m stand-off | ≥80% in band over 60 s, zero collisions |

### What the sim can and cannot prove

`GodotPerception`'s `nx` is a bearing approximation, not a camera projection, and there is
no phrover POV camera in Godot (`capturedFrameJPEG()` returns `nil`). The sim therefore
proves the control law, the deadband, stand-off holding, loss/re-acquire, the safety gate
and association between two candidates — the depot carries a second walker for exactly
that, reported with the same `person` label as the first. It cannot prove appearance
grounding; "the guy with the hat" is testable there only as label discrimination.

### Aerial, next

- `ActionType.FOLLOW_TARGET` in `vlm.py`, a `VLM_CAPABILITIES` entry in `mission_vocab.py`
  (which renders into the cloud planner prompt), and a `reasoning_loop.py` dispatch entry —
  that file asserts dispatch/vocab parity at import, so a half-added action fails loudly.
- `drone/common/follow.py`: the Python mirror. Reuse `Backend.loiter(center, radius)` and
  `search_patterns.orbit(...)`; the delta from the existing `ORBIT_POINT` handler is only
  that the centre is a moving estimate re-commanded per lap segment, and `_aim_sensor_at`
  already holds the sensor on it.
- `SpatialMemory` is the wrong reuse for a track — it is a static landmark store (EMA
  alpha 0.4, 1.5 m merge radius) with no velocity. It needs a velocity-carrying sibling.
- `target_selector.py`'s `_VERBS` regex has no "follow" and cannot capture attributes; the
  open-vocab path should pass the whole noun phrase to `OpenVocabDetector.set_classes()`.
- The patrolling pickup truck in `env_surveil_truck.gd` is the ready-made aerial follow
  target, the way the depot's walker is the ground one.

**Policy gate before any aerial follow flight:** sustained tracking of a person by an
aircraft is an operations-over-people question (Part 107) and a privacy question. It needs
an explicit decision plus an enforced altitude floor and stand-off in `configure_safety` —
not just code.
