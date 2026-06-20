# Teaching a Drone to Fly Like a Pilot
## From Waypoint Hops to Smooth Obstacle Avoidance: A Development Chronicle

*Session 1 — policy_v2 through policy_v4_dr. Gauntlet2 (chained maneuvers) solved in Session 2.*

---

### The Problem

Commercial autopilot stacks send drones through discrete waypoints: fly to (A), stop, fly to (B), stop. It works, but it looks nothing like how a human pilot moves through space. A human pilot reads depth, accelerates into openings, arcs under ceilings, pops over walls — smooth, continuous, committed. The goal of this project was to replace the discrete-hop mid-level planner with a learned policy that produces that kind of motion: an **analog pilot**.

Two constraints shaped every design decision:
1. **On-device inference**: the model runs on a Jetson Orin Nano in a real-time control loop at 10 Hz. It must be small enough to compile through TensorRT and fast enough not to miss ticks.
2. **No ground truth map**: the policy only sees what a forward-facing depth camera sees — no prior map, no GPS, no knowledge of obstacle geometry.

---

### Policy v2 — The Baseline That Couldn't Steer

The first real policy was a single-layer GRU fed a 16-step history window of state vectors. State was minimal: body-frame goal vector (3), body velocity (3), previous yaw rate (1), **single forward clearance scalar** (1), altitude (1) = 11 dimensions.

Training data came from a simple expert: a proportional controller that slowed near obstacles and turned toward whatever lateral direction had more clearance. Fast to generate, easy to understand.

**What worked:** The policy smoothly decelerated toward obstacles and didn't crash into direct forward blocks.

**What failed:** It couldn't steer *around* obstacles. The clearance scalar told it *how far* to the nearest thing ahead, but not *where* the gap was. The expert that generated the data also navigated by slowing, not by banking — so the policy cloned exactly that helplessness.

**Hidden bug discovered later:** The `LearnedPlanner` wrapper pre-normalized inputs before passing them to the model, but the ONNX already contained internal normalization layers (mean/std buffers baked in at export). Double normalization → wrong scale → output velocities near zero. The policy appeared to "stall everywhere" until we traced the residuals back to the pre-processing path.

---

### Policy v3 — A Fan of Rays, But Only in 2D

The clearance scalar was replaced with a **9-ray horizontal fan**: rays spanning ±60° in front, returning per-ray range up to 10 m. State grew from 11 to 20 dimensions. The expert was upgraded to **blurred gap-following**: rather than averaging across the fan (which cancels on symmetric obstacles), it picked the ray with the maximum of `(blurred_clearance / DEPTH_MAX + k_align * cos_alignment_to_goal)` — thread the clearest path that's still pointed at the goal.

An early v3 variant (v3_rl) was fine-tuned with PPO. PPO reward: `+progress_to_goal − collision_penalty − time_penalty`. The RL fine-tune improved performance in open space but destabilized near-obstacle behavior. Root cause: the critic and actor shared a GRU trunk. Value function gradients corrupted the policy's obstacle-sensing features. **Fixed in v4: separate actor and critic GRU layers.**

**What worked:** The drone could now steer left/right around vertical pillars, navigate slalom courses, and find lateral gaps in scattered obstacles. Clearance improved measurably.

**What failed:** Everything was 2D. The depth fan had no vertical resolution. The drone couldn't duck under a ceiling or climb over a floor wall — it would just slow down and stall. Any obstacle that required a *vertical* maneuver was a dead end.

---

### Policy v4 — The Full 3D Stack

This is where the architecture became what's deployed today.

#### Depth Grid: 5×9 = 45 Rays

The 9-ray horizontal fan was expanded to a **5×9 grid**: 5 rows of vertical elevation (−35° to +35°) × 9 columns of azimuth (−40° to +40°). That's 45 rays, 90° HFOV × 70° VFOV — wide enough to catch gaps above and below. State grew to 56 dimensions.

The ray directions (unit vectors in body frame) are precomputed in `contract.py:RAY_DIRS` and used identically in training (analytic ray-AABB cast in `world3d.py`) and deployment (`reactive_planner.py`). There is no mismatch between training and inference depth representation.

#### A\* Oracle Teacher

A simple proportional expert can't demonstrate the maneuver of ducking under a ceiling. It takes a global-planner teacher to know the correct move. We added a **privileged A\* oracle** in `planner3d.py`:

- 26-connected grid search in 3D at 0.5 m resolution
- Obstacle inflation of 0.55 m (drone body radius + margin)
- `zmax=ALT_CAP` constraint (4 m): the teacher can't route over a ceiling by flying above the deploy altitude cap — it must duck under, just like the real vehicle will
- Pure-pursuit follower with 0.9 m lookahead once the path is found

The key insight: the policy never sees the A\* plan. The oracle is **privileged** — it knows the full obstacle geometry, computes a collision-free path, and acts as the "demonstrator." The policy only sees the depth grid and tries to imitate the oracle's velocity outputs. At deploy time, there's no A\* running — the depth grid goes directly into the GRU.

#### Obstacle Geometry: Corridor-Spanning Walls

Early v3 window walls spanned only y ∈ [−4, 4]. The drone went around the end. When we rendered this and called it "threading the window," the user immediately caught it: *"It didn't go through the hole, liar."*

Fixed in v4: window jambs extend to y ± 8 m (and in training, `HY=9.0`). There is no end to fly around. The only path through the wall is the rectangular opening. Training on these truly inescapable obstacles forced the oracle to demonstrate genuine vertical and lateral commitment — and forced the cloned policy to learn it.

Similarly, "over" walls top out *below* the altitude cap so the drone can climb over within the deploy envelope. "Under" ceilings extend *above* the altitude cap so flying over isn't an option — must duck.

#### DAgger: Fixing Covariate Shift

Behavioral cloning has a fundamental problem: the expert always starts from clean, on-trajectory states. The policy, when deployed, eventually reaches states the expert never visited — near-misses, recovery situations, awkward headings. It has no training signal for those states, so it tends to make things worse.

We added **DAgger (Dataset Aggregation)**:
- With probability 1/3 each, run the episode with zero exploration noise, σ=0.3 perturbation, or σ=0.6 perturbation
- After each noisy action, relabel the resulting state with what the expert *would have done* from that state
- This populates the dataset with near-miss recoveries and off-axis approaches

The σ=0.6 DAgger trajectories were particularly important: they put the drone in wall-near states that the expert never self-generates, and give it clean expert supervision on what to do next (turn away, descend, find the gap).

#### Domain Randomization + Curriculum

Real quadrotors have velocity lag (the commanded velocity doesn't appear instantly), actuation latency, acceleration limits, and wind. A policy trained on instant-response kinematics breaks in SITL the first time these dynamics kick in.

We added a **DynamicsDR** module (`dynamics.py`) that wraps the policy's velocity commands:
- Velocity lag: first-order filter, τ ∈ [0.03, 0.30] s
- Actuation latency: ring buffer delay, 0–2 ticks (0–200 ms)
- Acceleration cap: 4–12 m/s²
- Wind: 0–1.2 m/s constant offset

DR is applied on a **curriculum**: hold at ideal kinematics for the first 30% of RL iterations, ramp to full DR by 75%. Without this curriculum, the policy never sees clean learning signal early on and RL diverges. With it, the early BC warm-start builds good representations before reality kicks in.

#### Training Pipeline

```
A* oracle → BC dataset (6000 episodes, DAgger)
    → train.py (GRU, 16-step window, 64-hidden, 30 epochs)
    → policy_v4.pt  (warm-start for RL)
    → train_rl.py (PPO, DR curriculum, separate actor/critic GRU)
    → policy_v4_dr.onnx  (114 KB, deployed)
```

Training runs on hoopoe (dual RTX 5090). The full pipeline takes about 90 minutes — 20 min for dataset generation, 70 min for RL fine-tune.

---

### Results: Three Obstacles, All Solved

The policy was evaluated on three corridor-spanning obstacle courses where the only valid path requires a genuine vertical or spatial maneuver:

| Course | Maneuver | Min Clearance | Outcome |
|--------|----------|---------------|---------|
| **Limbo** | Duck UNDER ceiling (z 1.5–5.0) | 0.67 m | ✅ Reached goal |
| **Over Wall** | Climb OVER floor wall (z 0–2.8) | 0.72 m | ✅ Reached goal |
| **Window** | Thread THROUGH rectangular opening | 0.53 m | ✅ Reached goal |

Aggregate over 100 random 3D scenarios (scattered prisms, barriers, windows):
- **97.3% goal reach rate**
- **2.0% collision rate**
- Median clearance: 0.61 m

ArduPilot SITL gate (full DR dynamics, real autopilot firmware): **3/3 passes**.

---

### The Lessons

**1. A scalar clearance is not a depth sensor.**
The single forward clearance tells you *how far* the nearest thing is. It tells you nothing about where the gap is. Once we gave the policy a 2D grid of directional ranges, steering appeared as an emergent behavior — the policy learned to point its body toward the clearest opening without being explicitly told how.

**2. The oracle must respect the same constraints as the vehicle.**
If the A\* teacher is allowed to route above the altitude cap to avoid a ceiling, it will — and the cloned policy will learn to fly high as a cheat strategy. In simulation that works; on the vehicle it doesn't. The fix was `zmax=ALT_CAP` in the A\* call. Every time the teacher was given an escape route the vehicle doesn't have, the policy learned the cheat.

**3. Covariate shift is real and DAgger matters.**
A policy trained only on clean expert trajectories will reach a near-miss state, have no training signal, and make it worse. Adding 30% DAgger noise (σ=0.3, 0.6) and relabeling those states with oracle supervision dropped the collision rate from ~8% to 2%.

**4. Shared actor/critic GRU = training instability.**
With a shared trunk, PPO value function gradients corrupted the policy features run-to-run. Separating the actor GRU (for policy) and critic GRU (for value estimation) made training deterministic across seeds.

**5. "Flying around" a wall is not the same as "going through" it.**
Obstacle geometry must be inescapable to force the intended maneuver. If a wall is shorter than the drone's lateral range, the policy will take the easy path around the end — and generalize nothing about vertical commitment.

**6. DR curriculum matters as much as DR itself.**
Throwing full domain randomization at the BC warm-start policy immediately causes RL to diverge: the policy is fighting dynamics noise before it has reliable obstacle representations. The 30%/75% curriculum holds kinematics clean long enough for the policy to build competent representations, then gradually hardens reality.

---

### What Didn't Work

- **A\* as the runtime planner**: We considered using A\* live at deployment, routing around obstacles in real time. Rejected: 0.5 m grid search over a 15 m corridor takes ~120 ms on the Orin — too slow for a 100 ms control loop, and A\* needs a full 3D map the vehicle doesn't have.

- **Averaging the depth fan for steering**: Taking the mean of depth rays across a direction cancels on symmetric obstacles (equal clearance left and right → zero lateral command → drone stalls in the middle of the corridor). The blurred argmax approach (pick the *best* ray with some goal-alignment bias) was strictly better.

- **Jerk penalty too high**: Early PPO had k_jerk=0.05. The policy became so smooth it wouldn't commit to rapid altitude changes — exactly the movements needed for limbo and over-wall. Dropping k_jerk to 0.005 let the policy execute fast vertical arcs without PPO penalizing the jerk.

- **Full ±π yaw training (v3_rl)**: Training with 100% random initial headings produced a very robust turn-then-go policy, but diluted obstacle competence enough that SITL go_over and slalom courses timed out (1/3 success). The fix was a 60/40 mix: 60% start roughly facing the goal, 40% full-range.

---

### The Cliffhanger: Gauntlet2

After individual obstacles worked, the natural test was to chain them: UNDER a ceiling → OVER a wall → THROUGH a window, three in series, no gaps to fly around. The policy ducked under the first obstacle correctly — then stalled at the second wall.

The failure mode was altitude bias: the policy ducked low for the ceiling and then wouldn't climb back up for the over-wall. It had learned "low altitude = safety" from ceiling-heavy training and couldn't break that habit. This is a **multi-stage commitment** problem: a purely reactive forward-camera policy sees the next wall and acts on it, but the correct action for the current wall depends on what the *next* wall will require.

That problem — and its solution — is the story of Session 2.

---

*Files: `eco/drone/training/`, `eco/drone/common/reactive_planner.py`, `eco/drone/models/policy_v4_dr.onnx`*  
*Training host: hoopoe (dual RTX 5090)*  
*Deploy target: Jetson Orin Nano (`jetson@rover`)*
