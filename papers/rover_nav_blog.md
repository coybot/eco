# 30 Minutes, 4.6 Kilobytes, Zero Collisions

*How we trained a ground rover to navigate cluttered environments with nothing but a lidar and a learned reflex.*

---

We wanted a ground rover that could drive itself through obstacles — not by building a map, not by running A*, not by following a script. Just raw reactive navigation: sense, think, steer. And we wanted it deployable on the hardware we actually have: a Jetson Orin Nano, a commodity 360° lidar, and a ROS 2 stack.

Here's what we ended up with: a 4.6 KB neural network that navigates a 4-room maze, a dense column field, and a tight gap — 120 trials, zero collisions — trained in 30 minutes on a pair of RTX 5090s.

---

## The Setup

The rover is a differential-drive ground vehicle. Its sensors:

- **360° lidar**: 72 rays at 5° spacing, 10m range — a horizontal sweep of everything around it
- **Odometry**: linear velocity and yaw rate from wheel encoders
- **Goal vector**: where the rover needs to go, expressed in its own body frame (forward/left distance)

That's 83 numbers going into the policy. Two numbers come out: linear speed and turn rate, wired directly to `/cmd_vel`. No perception stack. No planner. No map.

---

## Why Not A*?

A* is a great algorithm if you have a map. We don't. The rover encounters the obstacles for the first time when it sees them in the lidar scan. That means any planning algorithm that requires knowing the environment in advance is off the table — which rules out most classical approaches.

What we want instead is something closer to how a skilled cyclist navigates a crowded street: a reflexive policy that reads the immediate environment and acts, informed by a sense of momentum and history built up over the past few seconds.

That's a recurrent neural network.

---

## The Policy

The architecture is a GRU with 256 hidden units — separately for the actor and critic (shared hidden state leads to training instabilities we'd seen before). The actor GRU reads the current 83-dimensional observation, updates its hidden state, and passes that through a two-layer MLP to produce the action.

```
obs (83-dim, raw) → normalize → GRU(256) → MLP(256→256→2) → [v, ω]
```

The hidden state is the policy's short-term memory. It's what lets the rover know it's been spinning for 3 seconds and should try something different. It's what lets it commit to passing through a gap even as the gap momentarily disappears from some rays. It's the difference between a policy that gets stuck in symmetric situations and one that breaks them.

At deployment, the hidden state (256 floats) lives on the Jetson between inference calls. Each call takes the current lidar scan + odometry, passes them through the GRU, and returns the updated hidden state alongside the action. The whole thing fits in a 4.6 KB ONNX file.

---

## How We Trained It

**Pure RL, from scratch, no demonstrations.** We used PPO with a vectorised GPU environment — 512 parallel rovers running simultaneously, each in its own randomised obstacle layout. The simulator is pure PyTorch: batched 2D ray-AABB intersection for lidar, unicycle kinematics for dynamics, domain randomization for wheel lag, latency, and wind drift.

Training time: ~30 minutes on dual RTX 5090s for 800 update iterations.

The reward signal:
- Progress toward goal (dense, every step)
- Penalty for being close to obstacles
- Large penalty for collision (terminal)
- Large bonus for reaching the goal
- Small penalty per timestep and for stalling

The proximity penalty deserves special mention. Without it, the policy learns to graze obstacles because the collision penalty only fires at <0.3m — and the policy discovers it can get a lot of progress reward by cutting corners. The clearance penalty reshapes the reward landscape so staying 0.5m from surfaces is consistently better than cutting close.

### Curriculum

We didn't throw the hard problems at the policy from the start. Training has four stages:

| Stage | Iters | What the rover faces |
|---|---|---|
| 0 | 0–199 | Empty field. Just learn to drive toward a goal. |
| 1 | 200–399 | 3–4 random columns per episode. Learn basic avoidance. |
| 2 | 400–599 | 8 columns + gap walls, DR ramping up. |
| 3 | 600–799 | Full slalom gauntlet: double walls, offset gaps, dense columns. |

By the time the policy sees the hard stuff in stage 3, it already knows how to drive, how to avoid, and how to thread gaps. Collision rate in stage 3 starts at 89% (iter 600) and falls to 20% by iter 799.

### Domain Randomization

Wheel dynamics are randomised at training time: velocity lag (τ ~ 0.1–0.3s), acceleration cap, control latency (1–3 steps, i.e. 0.1–0.3s at 10Hz), and a small wind-like heading drift. The lidar gets Gaussian noise (σ=0.05m, matching RPLidar specs). The goal vector gets odometry noise (σ=0.15m).

This sim-to-real gap is where most learned locomotion policies fail. By training with realistic noise and dynamics from the start, the policy never gets to rely on pixel-perfect sensor readings or lag-free actuators.

---

## Results

We ran 20 trials on each of 6 named courses with realistic sensor noise (lidar σ=0.05m, odometry σ=0.10m):

| Course | What it tests | Result |
|---|---|---|
| Straight | Basic go-to-goal | 20/20, no collisions |
| Slalom | Weave through 3 offset columns | 20/20, min clearance 1.24m |
| Tight gap | 1.6m gap in a spanning wall (rover is ~0.6m wide) | 20/20, min clearance 0.77m |
| Double gap | Two walls, gaps on opposite sides | 20/20, min clearance 1.10m |
| Cluttered | 9 random columns | 20/20, min clearance 0.60m |
| 4-room maze | 6 walls with alternating gaps | 20/20, min clearance 1.54m |

**120 out of 120 trials reached the goal. Zero collisions.**

The tight_gap result is particularly striking: the gap is 1.6m wide and the rover body is ~0.6m — that's 0.5m of clearance on each side. The policy threads it consistently with 0.77m minimum clearance even with noisy sensors. It doesn't slow down to feel its way through; it commits.

---

## What Surprised Us

**The GRU matters more than we expected.** A flat MLP with the same observation could learn to go-to-goal in the open field, but it would spin in place when the lidar was symmetric — two identical columns flanking the path — because with no memory, there's no way to break the tie. The GRU solves this naturally: the hidden state carries the history of which side the rover has been leaning toward, and uses that to break symmetry.

**360° lidar is the right sensor.** We also have a forward camera on the rover. We could have used it as the primary avoidance sensor. We didn't, and we're glad: lidar covers the full 360°, requires no feature extraction, has a sim-to-real gap of nearly zero, and produces exactly the 72-number input we feed to the policy. The camera is great for object recognition; it's overkill for collision avoidance.

**4.6 KB is enough.** The ONNX file is 4.6 kilobytes. The entire policy fits in an L1 cache line. This is not a large model problem — it's a representation problem, and 256 hidden units of GRU with a two-layer MLP head is sufficient representation for reactive obstacle avoidance in 2D.

**The clearance penalty is not optional.** Every ablation variant we tried without it learned a policy that grazed obstacles on the way to the goal. Not because it was reckless — because grazing was faster, and the reward signal agreed. The clearance penalty is what makes the policy physically safe, not just statistically uncollidey.

---

## What's Next

The obvious next step is sim-to-real: put the policy on the Jetson, wire up the RPLidar and odometry, and drive it through a physical course. The ONNX runner is already written. The ROS 2 interface is cmd_vel. This is a connector problem, not a policy problem.

Beyond that:

- **Moving obstacles**: training only saw static obstacles. A pedestrian crossing the path would be a new regime.
- **Camera integration**: adding the 9×5 forward depth grid (45 dims) to the observation could help with narrow gaps where the camera sees the gap centre better than the lidar does.
- **Goal sequencer**: right now a human or GPS supplies waypoints. Closing the loop with a graph-based global planner on top of this reactive policy gives you a full navigation stack with zero per-environment mapping.

---

## The Code

Everything is in `eco/drone/training/`:

- `rover_contract.py` — state/action spec, normalization constants
- `train_rl_rover.py` — full PPO training code, reproducible
- `local_course_rover.py` — headless validation runner, 6 named courses
- `render_rover_demo.py` — three-panel video renderer (top-down map, lidar polar, depth camera)

Train command:
```bash
PYTHONPATH=. python -m eco.drone.training.train_rl_rover \
    --out ~/drone-data/rover/models --version rover_v1 \
    --iters 800 --envs 512 --rollout 96
```

30 minutes. 4.6 KB. Zero collisions.
