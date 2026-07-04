# Reactive Ground Rover Navigation via Recurrent RL with 360° Lidar

**Abstract**

We present a learned reactive navigation policy for differential-drive ground robots that achieves zero-collision traversal of cluttered environments and multi-wall mazes using only 360° lidar and odometry — no map, no global planner, no demonstrations. The policy is a recurrent actor-critic (GRU, hidden size 256) trained with proximal policy optimisation across 512 parallel simulated environments on commodity GPU hardware. A four-stage curriculum advances from open-field goal-seeking to dense obstacle fields and tight slalom gauntlets, with domain randomisation covering wheel lag, control latency, sensor noise, and heading drift throughout. Training from scratch requires approximately 30 minutes on a dual-RTX-5090 workstation. The exported ONNX policy weighs 4.6 KB and runs in step-mode — a single 83-dimensional observation plus a 256-float GRU carry-state per inference call — making it deployable on a Jetson Orin Nano at 10 Hz with negligible compute overhead. Evaluated on six named courses (straight, slalom, tight gap, double gap, cluttered field, 4-room maze) across 20 noisy trials each (RPLidar noise σ=0.05m, odometry σ=0.10m), the policy achieves a 100% goal-reach rate with zero collisions across 120 trials. Minimum clearance in the tightest configuration (1.6m gap, rover body ~0.6m) is 0.77m. We report the reward formulation, curriculum schedule, architecture, and training curve in full, and release all code for reproducibility.

**Keywords**: mobile robot navigation, reinforcement learning, recurrent policy, lidar, obstacle avoidance, sim-to-real, curriculum learning

---

## Proposed Venue

- **RA-L / ICRA 2027** (Robotics and Automation Letters + ICRA presentation)
- Alternate: **CoRL 2026** if framing emphasises learned reactive policies

## What Would Strengthen the Paper

1. **Sim-to-real results** — physical rover runs on the same 6 courses; measure gap between sim and real reach/clearance rates
2. **Ablation study** — GRU vs MLP, 360° vs forward-only lidar (36 rays ±90°), curriculum vs no curriculum, with/without DR, with/without clearance penalty
3. **Baseline comparison** — VFH (Vector Field Histogram) analytic planner as a reference; we have `demo_rover_gauntlet.py` implementing VFH already
4. **Longer training run** — 1600 iters, hidden=512 to see if stage-3 reach exceeds 56%
5. **Dynamic obstacles** — single moving obstacle to probe generalisation limits

## Figures Needed

1. System diagram: lidar → GRU → cmd_vel, with carry-state annotation
2. Curriculum overview: 4 panels showing stage 0–3 obstacle layouts (can extract frames from videos)
3. Training curves: reach rate and collision rate vs iter, with stage boundaries marked as vertical lines
4. Course maps with trajectory overlays (one per course, rover path in gradient colour)
5. Lidar polar view snapshots: approaching gap, mid-gap, clear field (video frames)
6. Results table (already above)
7. (Future) Sim-to-real comparison side by side
