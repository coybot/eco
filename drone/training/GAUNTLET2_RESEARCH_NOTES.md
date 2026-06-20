# Teaching a Drone to Navigate Obstacle Courses Without a Map
## Trial Log, Lessons Learned, and Data — v14 through v26

*Date: 2026-06-18. Branch: `feat/learned-pilot-3d`. Model: `policy_v26rnn_dr.onnx`.*

---

## The Problem

We wanted an autonomous drone that could navigate obstacle courses—fly under a ceiling, climb over a wall, thread through a window—without being given any map or pre-planned path. The drone has only:

- A **5×9 forward depth grid** (45 rays, 90°×70° FOV)
- Its current altitude and yaw
- A 3D vector toward the goal

This rules out classical planners. A* was explicitly rejected: it needs to know the course geometry in advance, which isn't available at deploy time. The drone had to learn to feel its way through.

The approach: train a **recurrent GRU policy** with PPO to output body-frame velocity `[vx, vy, vz, yaw_rate]`. Then throw it at gauntlet2—a chained under→over→through course—and see what breaks.

---

## System Architecture

**Policy**: GRU step-mode. Input: `(state[56], h_in[hidden])` → output: `(action[4], h_out[hidden])`.
- 56-dim state = 11 base dims + 45 depth rays
- Hidden = 128. No lookahead. No map. No planner at deploy.

**Training environment (`BoxEnv`)**: Vectorized PyTorch simulator with `n` parallel envs and `K` obstacle boxes per env. Boxes defined by center `(bcx, bcy, bcz)` and half-extents `(bhx, bhy, bhz)`. Kinematic integration at 1 Hz equivalent ticks.

**Rollout**: T=96. Gauntlet2 takes ~70 ticks. T=48 never sees terminal reward—policy can't learn to complete it. T=160 is unstable (gradient variance explodes).

**Domain randomization (DR)**: Position noise ramps from 0 at `dr_hold_frac` to full at `dr_full_frac`. Training noise: `depth_noise=0.07m`, `target_noise=0.15m`.

**Sequence training**: 40% of episodes are chained under→over→through sequences (gauntlet IS in training distribution from the start).

**Gauntlet2 geometry**:
- Obstacle 1 (ceiling): x=[0,2], z=[2.6,4.0]. Drone must duck to z<2.3m.
- Obstacle 2 (wall): center=(7.5,0,1.3), half=(0.7,4.0,1.3). Footprint x=[6.8,8.2], z=[0,2.6]. Drone must clear z≥2.9m.
- Obstacle 3 (window frame): opening y=[-0.8,0.8], z=[1.2,2.5]. Drone must thread through.

**Singles**: limbo (go under ceiling), over_wall (climb over a wall), window (thread through opening). All three were solved early and had to stay passing through every training iteration.

---

## The Iteration History

### v14rnn — Behavioral Cloning Only
BC-trained on expert trajectories. Collides everywhere in simulation. No obstacle avoidance learned.

### v15rnn — BC + Noise Augmentation
Added input noise during BC training. Still collides. BC alone is insufficient for reactive obstacle avoidance.

### v16rnn_dr — First Working Singles (T=48)
Switched to PPO. T=48, basic reward (progress + collision + altitude). Singles: 20/20. Gauntlet2: 0/20. **This was the singles deploy model** — SITL 3/3 confirmed.

Problem: T=48 means the policy never sees the full gauntlet episode. It has no gradient signal from completing the course.

### v17rnn_dr — T=160, Aggressive Progress
Doubled rollout to T=160, boosted `k_prog=3.0`. Collapsed immediately: `reach=0.60`, oscillating. Too much variance with long rollouts and strong shaping.

### v18rnn_dr — The Breakthrough Maneuver (T=96)
Settled on T=96. Reduced `k_prog`, added `k_clear` (clearance penalty from `min_box_dist`). 

**Key result**: The drone for the first time flew *over the wall* in gauntlet2. It discovered the under→over→through sequence. But it clipped the wall by only **0.24m** (HALT_R=0.3m = collision). 19/20 on singles, 0/20 on gauntlet2.

This told us: the policy knows what to do. It just clips. The remaining problem is purely about clearance.

### v19rnn_dr — More Clearance Penalty (k_clear=2.0)
Increased `k_clear`. Worse: clip distance dropped to **0.13m**. More penalty → smaller margin, not larger. 

Insight: the penalty was firing for horizontal approach, not vertical proximity. Increasing `k_clear` with isotropic distance was teaching the drone to avoid the wall's *sides*, which compressed the trajectory.

### v20rnn_dr — Even More (k_clear=4.0, 300 iter)
Pushed harder: `k_clear=4.0, clear_margin=1.0`. Still clips at **0.08m**. Diminishing returns. Clean model, no yaw drift.

### v21rnn_dr — Altitude Push + Penalty Overload (COLLAPSED)
Added `k_alt=2.0` to push the drone higher over the wall. Also raised `k_coll=50`. 

**Catastrophic failure**: reach dropped to 0.44 in singles. Root cause found later: `k_alt=2.0` creates a per-tick penalty of 2.48 when ducking (required to pass the ceiling). With 10 duck ticks, total duck cost = 24.8 ≈ `k_coll=25` (at the time). Gradient was equally happy to skip the duck and get hit. Training collapsed.

**Lesson**: `k_alt` safe range is 0.7–1.2. It penalizes descent below goal_z. During the duck maneuver, goal_z is above the drone → every duck tick is penalized. `k_alt=2.0` makes ducking as bad as crashing.

### v22rnn_dr — Tuned Alt, Still High Clear Margin (STALL)
Fixed `k_alt=1.0`. Kept `k_clear=5.0, clear_margin=1.2m`. Singles recovered. Gauntlet2: **stall**. The drone made it past the ceiling, descended to z=0.7m, approached the wall at x≈5.8m — and then drifted sideways, orbiting forever.

**Root cause**: `min_box_dist` is isotropic. At z=0.7m (post-duck, approaching wall), the drone is within the wall's z-range [0,2.6]. Distance to wall face: `|5.82−7.5|−0.7 = 0.98m`. With `clear_margin=1.2m`, the horizontal approach fires `(1.2−0.98)=0.22/m` penalty. The policy learned: "don't approach the wall at low altitude." So it didn't. It drifted sideways to stay outside the 1.2m danger zone. A stall, not a crash.

This was the key insight that unlocked the solution.

---

## The Core Insight: Isotropic vs. Directional Clearance

The `min_box_dist` reward penalizes **any approach** to a box — horizontal or vertical. For the wall crossing, this was wrong:

- We **want** horizontal approach at low altitude (duck then charge).
- We **don't want** vertical descent at high altitude (while above the wall, stay high).

The penalty needed to be **directional**: fire only when the drone is above the wall's top surface and inside its x-y footprint. Not when approaching from the side.

**`above_near(above_margin)`** — new reward term added to `BoxEnv`:

```python
def above_near(self, above_margin):
    box_top = self.bcz + self.bhz                              # top surface z
    above   = self.z[:, None] > box_top                       # drone is above top
    in_x    = (self.x[:, None] - self.bcx).abs() <= self.bhx  # inside x footprint
    in_y    = (self.y[:, None] - self.bcy).abs() <= self.bhy  # inside y footprint
    vert_dist = (self.z[:, None] - box_top).clamp(min=0.0)    # height above top
    near_k = ((above_margin - vert_dist).clamp(min=0.0)
              * (above & in_x & in_y).float()
              * (self.bmask > 0.5).float())
    return near_k.max(dim=1).values
```

This fires **only** when:
1. Drone is above the box's top surface
2. Drone is inside the box's x-y footprint
3. Drone is within `above_margin` meters of the top

Zero penalty for approaching from the side. The drone can charge the wall at z=0.7m without any avoidance signal.

Combined with dropping `clear_margin` from 1.2m back to 0.6m (only fires at near-collision, no horizontal avoidance), the stall was cured in v23.

---

## Tuning above_margin and k_above

With `above_near` fixed the directional problem. But we had to tune the magnitude right.

**Wall crossing math**: Wall 1.4m wide, vx≈0.2m/tick → 7 ticks to cross. At wall entry z=3.87m, wall top=2.6m, vertical gap=1.27m. Need to cross with z≥2.9m = at least 0.3m above top. Maximum descent over crossing: `(3.87−2.9)/7 = 0.139m/tick`. Signal must exceed progress pull of `k_prog=0.3/tick`.

**`above_margin` safe max**: For the single `over_wall` course (wall top≈3.21m, ALT_CAP=4.0m): if `above_margin` is too large, the penalty fires while the drone is still trying to reach the goal, making timeout cheaper than goal-reaching. Computed max: ~1.81m.

| Version | k_above | above_margin | Entry signal | Result |
|---------|---------|--------------|--------------|--------|
| v23 | 8.0 | 1.0m | 0 (vert_dist=1.27m > 1.0m at entry) | Collision 0.17m |
| v24 | 5.0 | 1.5m | 8×(1.5−1.27)=1.84/tick | Collision 0.17m |
| v25 | 8.0 | 1.5m | 8×(1.5−1.27)=1.84/tick (clean warmstart) | Collision 0.23m |
| **v26** | **8.0** | **1.7m** | **8×(1.7−1.27)=3.44/tick** | **✅ 20/20, clr=0.34m** |

The v23 fix fired too late — margin smaller than the entry gap, zero signal at wall entry. v24 fix had correct margin but k_above too weak. v25 had the right parameters but was warm-started from v23 (which had accumulated a yaw-drift habit from its failed attempts). **v24 inherited v23's drift.** Using the clean v20 checkpoint as warm-start, then v25→v26, produced a clean trajectory. Warm-start hygiene mattered.

---

## Final Configuration (v26)

```
--k-prog 1.5 --k-alt 1.0 --k-clear 0.6 --clear-margin 0.6
--k-above 8.0 --above-margin 1.7
--k-coll 25.0 --k-goal 40.0
--depth-noise 0.07 --target-noise 0.15
--rollout 96 --envs 256 --iters 500 --lr 2e-5
```

---

## Results

**Gauntlet2 (local validation, 20 trials)**
- Success rate: 20/20
- Min clearance: 0.34m (HALT_R=0.3m)
- Trajectory: duck to z≈0.75m under ceiling → climb to z≈3.9m over wall (entry 3.87m, exit 3.48m) → descend and thread window at z≈1.9m

**Singles (local validation, 20 trials each)**
- limbo: 20/20
- over_wall: 20/20
- window: 18/20 (2 miss-threads at extreme yaw)

**SITL (ArduPilot, 3 courses)**
- go_over: reached ✅, min_clear=2.16m
- go_under: reached ✅, min_clear=1.22m
- slalom_3d: reached ✅, min_clear=1.39m

**Videos** (Isaac Sim, v26rnn_dr):
- `chase_gauntlet2.mp4` / `fpv_gauntlet2.mp4` — hero run, full under→over→through
- `chase_limbo.mp4` / `fpv_limbo.mp4`
- `chase_over_wall.mp4` / `fpv_over_wall.mp4`
- `chase_window.mp4` / `fpv_window.mp4`

---

## What Didn't Work (and Why)

1. **A***: Rejected at the design level. Needs map geometry at planning time. Not deploy-ready.
2. **BC-only (v14, v15)**: No reactive avoidance. Can't generalize to novel obstacle positions.
3. **T=48 rollout**: Policy never sees gauntlet completion. Learns singles only.
4. **T=160 rollout**: Too much variance. Training collapses.
5. **Large `k_alt` (v21)**: Creates gradient competition between ducking and collision. Safe range: 0.7–1.2.
6. **Isotropic `min_box_dist` with large margin (v22)**: Penalizes horizontal approach. Teaches lateral avoidance at low altitude. Causes stall, not collision.
7. **`above_margin` < entry gap (v23)**: Zero signal when drone enters wall footprint at high altitude. Drone descends free for several ticks before penalty fires. Too late.
8. **k_above too weak (v24)**: 1.84/tick signal not enough to overcome progress gradient. Still descends.
9. **Warm-start from drift-corrupted checkpoint (v24)**: Inherits yaw-drift habit (yaw reaching −27.8° post-duck, lateral offset 1.9m). Fix: identify the last clean checkpoint before drift appeared and warm-start from there.

---

## Lessons for Future Work

**The gradient must win at entry, not at collision.**  
The penalty for descending over the wall must fire the moment the drone crosses the wall's x-y boundary at high altitude — not when it's already 0.3m from the surface. Compute the entry signal explicitly and make sure it's >> k_prog before training.

**Directional reward shaping for directional constraints.**  
Isotropic distance is wrong for obstacles that have direction-dependent constraints (e.g., "stay above this wall, not away from it"). Decompose into footprint check × vertical distance.

**Warm-start hygiene.**  
A policy can learn bad habits (yaw drift, lateral orbiting) and pass them to fine-tuned descendants. Track the last clean checkpoint. If a run develops new failure modes, trace back to the divergence point and warm-start from there instead of from the latest model.

**Rollout length is a hard constraint, not a hyperparameter.**  
Rollout must exceed max episode length (gauntlet ~70 ticks → T=96 minimum). This isn't tunable—it's correctness.

**Local validation is fast enough to iterate without Isaac.**  
`local_course.py` mirrors Isaac kinematics on CPU using only `onnxruntime`. A 20-trial gauntlet run takes ~30 seconds. This enables tight iteration loops without waiting for Isaac rendering.

**The single-to-gauntlet gap is about reward, not capacity.**  
The GRU at hidden=128 has ample capacity for all maneuvers. The gap between singles-solved and gauntlet-solved was entirely a reward engineering problem. The policy knew how to maneuver; it was receiving the wrong gradient signal.

---

## Model Lineage Quick Reference

| Model | Status | Key change |
|-------|--------|------------|
| v16rnn_dr | Singles deploy, SITL 3/3 | T=48 baseline |
| v18rnn_dr | First gauntlet maneuver | T=96, clip=0.24m |
| v22rnn_dr | Stall (fix: above_near) | k_clear=5.0, margin=1.2m |
| v23rnn_dr | Collision 0.17m | above_near added, margin too small |
| v25rnn_dr | Collision 0.23m | Clean warmstart, margin=1.5m |
| **v26rnn_dr** | **✅ Gauntlet solved** | margin=1.7m, entry signal 3.44/tick |

---

*Model file: `eco/drone/models/policy_v26rnn_dr.onnx` (355,933 bytes)*  
*Videos: `eco/drone/training/videos/`*
