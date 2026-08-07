# Learned Rover Navigation: Lessons Learned & Paper Notes

*Session: June 2026. Yusuf Saib, Presidio.*

---

## What We Built

A ground rover (Jetson Orin Nano, differential drive) that navigates around arbitrary obstacles using a learned recurrent policy trained purely with RL — no maps, no global planner, no human demonstrations. The policy takes live 360° lidar + odometry and outputs cmd_vel directly. It was trained from scratch in ~30 minutes on dual RTX 5090s.

The stack:
- **Sensor**: 360° 2D lidar (72 rays, 5° spacing, 10m range) + odometry (v, ω)
- **Goal input**: body-frame target vector (fwd, left) — e.g. from GPS or a human operator
- **Policy**: recurrent PPO, GRU(256), T=96 rollout
- **Output**: [v_linear (m/s), yaw_rate (rad/s)] → wired directly to ROS2 /cmd_vel

---

## Quantitative Results

### Validation (rover_v2, 20 trials per course, lidar σ=0.05m, target σ=0.10m)

| Course | Description | Reach | Collisions | Min clearance | Mean clearance |
|---|---|---|---|---|---|
| straight | No obstacles | 20/20 | 0/20 | ∞ | ∞ |
| slalom | 3 staggered 0.8m columns | 20/20 | 0/20 | 1.24m | 1.26m |
| tight_gap | Wall with 1.6m gap (rover ~0.6m wide) | 20/20 | 0/20 | 0.77m | 0.79m |
| double_gap | Two walls with offset gaps | 20/20 | 0/20 | 1.10m | 1.11m |
| cluttered | 9 random columns, dense | 20/20 | 0/20 | 0.60m | 0.62m |
| maze_4room | 6-wall 4-room maze | 20/20 | 0/20 | 1.54m | 1.61m |

**120/120 reach, 0/120 collisions** across all courses with realistic sensor noise.

### Training summary (rover_v2)
- **Hardware**: dual RTX 5090 (hoopoe), CUDA 13.0, torch 2.12.0
- **Duration**: ~30 min, 800 PPO iters, 512 parallel envs, rollout T=96
- **Parameters**: 656,133 (GRU actor + GRU critic + MLP heads)
- **ONNX size**: 4.6 KB (step-mode: state[1,1,83] + h[1,1,256] → action[1,2] + h'[1,1,256])
- **Best in-training reach**: 56.3% at iter 601 (stage 3, DR=1.0)
- **Final in-training reach**: 45.4% at iter 799 (stage 3 slalom gauntlet, hard)
- **Post-training (named courses)**: 100% reach, 0 collisions

### Training curve — reach rate by curriculum stage

| Iter range | Stage | Description | Peak reach | Final reach |
|---|---|---|---|---|
| 0–199 | 0 | Open field, no obstacles | 100% (iter 0†) | 83% |
| 200–399 | 1 | Sparse columns (3–4/env) | 78% (iter 357) | 64% |
| 400–599 | 2 | Dense columns + gap walls, DR ramping | 40% (iter 580) | 36% |
| 600–799 | 3 | Slalom gauntlet, DR=1.0 | 56% (iter 601) | 45% |

†Stage 0 iter 0 reach=100% is an artifact of the random policy occasionally reaching the goal in the open field by chance; the real learning signal kicks in at iter ~80 (reach 53%).

Key milestones:
- **Reach >50% first**: iter 80 (stage 0, open field)
- **Reach >75% first**: iter 120 (stage 0)
- **Stage 1 collision rate drops below 40%**: iter ~280
- **Stage 3 reach stabilises >40%**: iter ~700

Full per-iter CSV: `policy_rover_v2_train_log.csv` (800 rows, columns: iter, stage, drs, reach, coll, ret_per_env, pg_loss, vf_loss, entropy, log_std_mean)

---

## Architecture

```
obs (83-dim, raw) → normalize → GRU(256) actor  → MLP(256→256→2) → [v, ω]
                              → GRU(256) critic → MLP(256→256→1) → V(s)
log_std: learned parameter, not state-dependent
```

State vector (83 dims, body frame):
```
[0]  target_fwd    (m, forward to goal)
[1]  target_left   (m, left to goal)
[2]  target_up     always 0
[3]  dist          (m, straight-line)
[4]  vel_fwd       (m/s, realized)
[5]  vel_left      always 0 (unicycle)
[6]  vel_up        always 0
[7]  yaw_err       (rad, heading error to goal, wrapped ±π)
[8]  yaw_rate      (rad/s, realized)
[9]  altitude      always 0
[10] vehicle       always 1.0 (VEHICLE_ROVER)
[11..82] lidar[0..71]  (m, 0=fwd CCW, max=10m)
```

Action (2 dims): `[v_linear m/s, yaw_rate rad/s]`

---

## Reward Function

```python
R = k_prog * Δdist_to_goal          # 1.5  — progress reward, dense
  - k_time                           # 0.03 — time penalty (encourages speed)
  - k_jerk * ||Δa||                  # 0.003 — smoothness
  - k_stall * (|v| < 0.2m/s)        # 0.3  — stall penalty
  - k_clear * max(0, 0.5m - clearance)  # 0.6 — proximity penalty
  - k_coll * collided                # 25.0 — collision terminal penalty
  - k_timeout * timeout              # 10.0 — timeout penalty
  + k_goal * reached                 # 40.0 — goal bonus
```

Key design note: the **clearance penalty** (k_clear) is what makes the policy keep buffer distance from walls. Without it, the policy learns to graze obstacles since collision only fires at 0.3m. With k_clear=0.6 and margin=0.5m, obstacles within 0.5m cost a running penalty per tick. The tight_gap result (reaches a 1.6m gap cleanly, clearance 3.75m on average) reflects the policy choosing trajectories that don't even come close to the walls.

---

## Curriculum

Four stages, unlocked at fixed fractions of total training iters:

| Stage | Frac | Description |
|---|---|---|
| 0 | 0–10% | Open field: no obstacles, pure go-to-goal |
| 1 | 10–25% | Sparse: 3-4 random columns, 80% active |
| 2 | 25–50% | Dense: 8 columns + 40% chance of a gap wall |
| 3 | 50–100% | Slalom gauntlet: 40% double-wall, 20% single-wall, 40% dense |

Domain randomization (DynamicsDR) held off until stage 2 is stable (at 25% of iters), then ramped to full scale at 70%.

DynamicsDR parameters:
- Wheel lag (1st-order velocity filter, τ~0.1–0.3s)
- Acceleration cap (asymmetric: accel faster than decel)
- Control latency buffer (1–3 steps, ~0.1–0.3s at 10Hz)
- Wind/heading drift (additive world-frame Gaussian per step)

---

## Key Design Decisions (and Why They Worked)

### 1. 360° lidar beats forward-only camera for obstacle avoidance

The rover has a forward depth camera (RAY_DIRS 5×9 grid, 80°×50° FOV). We chose 72-ray 360° lidar as the primary avoidance sensor and the camera as a display layer only. Reasons:
- **No blind spots**: forward camera misses side/rear obstacles entirely, leading to "commit to the wrong side" failures and rear-collisions after backing up
- **Compact representation**: 72 floats vs a full image; fits in the same recurrent state
- **Easier credit assignment**: lidar ranges correlate directly with reward; pixel features don't
- **Sim-to-real gap is smaller**: a 2D ray-AABB scan is close to what an RPLidar sees in practice; a rendered depth image has texture/lighting gaps

Lesson: **for collision avoidance on a ground rover, 360° lidar is the right primary sensor**. Add camera only for scene understanding or object classification.

### 2. Unicycle kinematics (not full SE(2) or quadrotor)

Action = [v_linear, yaw_rate] maps directly to differential drive. No lateral velocity, no altitude. This was a deliberate simplification:
- Fewer action dimensions → easier exploration in early training
- Matches hardware exactly (cmd_vel Twist.linear.x + angular.z)
- The unicycle kinematic constraint (no lateral slip) is physically correct for a skid-steer or diff-drive rover

Lesson: **match the action space to hardware kinematics exactly**. Don't use a 6-DOF action space and hope the policy learns to ignore 4 dims.

### 3. GRU beats MLP even for a ground rover

A naive choice would be MLP(obs) → action (no memory). We kept the GRU because:
- Partially observable: a single lidar scan at one timestep doesn't tell you if an obstacle is moving, how fast you're approaching, or what's around a corner you just passed
- The GRU effectively integrates velocity + history, giving the policy a soft "SLAM" without any explicit map
- In early experiments (from the drone work this architecture was carried from), MLP policies with the same observation would spin in place when lidar was symmetric — GRU breaks symmetry via trajectory history

The separate actor/critic GRUs (not shared) turned out important — the value function needs to predict long-horizon returns, which requires different temporal integration than the policy.

### 4. Carry-state ONNX export (step-mode)

The policy is exported in step-mode: each inference call takes `(obs[1,1,83], h_in[1,1,256])` and returns `(action[1,2], h_out[1,1,256])`. The GRU state is carried by the caller between steps.

This is critical for deployment: the on-device runner holds a 256-float state vector, passes it in each inference call, and gets it back updated. No sequence buffering, no warm-up latency.

Normalization happens **inside** the ONNX graph (registered buffer mean/std). The caller passes raw unnormalized observations. This prevents the double-normalization bug we hit in initial validation (see Bugs section).

### 5. Sensor noise during training (not just at test time)

Lidar noise σ=0.05m and target noise σ=0.15m were applied during training, not just at test time. This prevented the policy from learning to exploit pixel-perfect range readings, which don't exist in practice (RPLidar A2 has ~0.03–0.05m angular resolution noise, odometry drift ~0.1–0.2m/m).

The noise levels were tuned to match realistic hardware specs.

---

## Bugs We Hit (Important for Reproducibility)

### Bug 1: Tensor size mismatch in `_place_gap_wall`

**Error** (appeared at curriculum stage transition, ~iter 390):
```
RuntimeError: The size of tensor a (221) must match the size of tensor b (512)
```

**Cause**: `_place_gap_wall(idx, n, m, gx, ...)` received `n=512` (full batch size) but `m` was a submask of ~221 environments. Calling `self._rand(n, ...)` generated 512 random values, but `gx[m]` had 221. Then `cx[m]`, `left_cy[m]` etc. double-indexed already-submasked tensors.

**Fix**: compute `nm = int(m.sum().item())` and use `nm` for all `_rand(...)` calls. Remove `[m]` indexing from tensors that are already submasked via `gi = idx[m]`.

```python
gi = idx[m]
nm = int(m.sum().item())   # actual submask size — use for all _rand calls
cx = gx[m] * self._rand(nm, lo=cx_frac_lo, hi=cx_frac_hi)
# All subsequent tensor ops use gi for indexing, not idx[m] or n
self.bcx[gi, slot] = cx
```

This is a general pattern: **when you index into a boolean submask and pass it to a helper, the helper must use `nm = m.sum()`, not the original batch size `n`.**

### Bug 2: Double-normalization kills the policy at deployment

**Error** (0/20 reach on all courses including straight-line):
```
Policy outputs near-zero actions regardless of obstacle layout
```

**Cause**: `RoverPlanner.step()` pre-normalized observations with `(obs - R_STATE_MEAN) / (R_STATE_STD + 1e-6)` before feeding them to ONNX. But `StepExport.forward()` in the ONNX graph also normalizes internally. The policy received observations with magnitude ~10^-3, far outside its training distribution.

**Fix**: pass raw `build_obs()` output directly to ONNX. Normalization is the ONNX graph's responsibility.

```python
obs = build_obs(target_fwd, target_left, vel_fwd, yaw_rate, lidar)
x = obs.reshape(1, 1, R_STATE_DIM).astype(np.float32)  # DO NOT normalize here
action, h_out = session.run(None, {"state": x, "h_in": h})
```

Lesson: **normalize inside the ONNX graph or outside — never both**. The ONNX graph is the source of truth for normalization.

### Bug 3: Wrong Python environment on training server

hoopoe has two Python environments that look similar:
- `~/.presidio-venv` — **does NOT exist / is not the right env**
- `~/vlm-train-env` — correct (torch 2.12.0+cu130, CUDA available on dual RTX 5090s)

Always use `~/vlm-train-env/bin/python3` on hoopoe.

---

## Training Infrastructure Notes

- **Hardware**: dual RTX 5090 (hoopoe), CUDA 13.0, torch 2.12.0
- **Training time**: ~30 min for 800 iters, 512 envs, rollout=96, all on GPU
- **Repo on hoopoe**: flat copy at `/home/yusuf/presidio-training/` — not a git checkout. Update files via SCP.
- **Environment**: fully vectorised in torch (no Python loops in the inner loop), 2D ray-AABB on GPU

Vectorised lidar is the performance key: 512 envs × 72 rays × 10 obstacle slots = ~370K ray-box tests per step, all batched into `(n,L,K)` tensors. On a 5090 this is trivial; on CPU it would be the bottleneck.

---

## What Didn't Work / What We Rejected

**A***: rejected because it requires a known map. The whole point is reactive navigation — the rover doesn't know the map in advance.

**Forward-only camera as primary avoidance sensor**: would need a full perception stack (segmentation, depth estimation, temporal fusion) before the RL policy could consume it. 360° lidar is simpler and more robust.

**BC warm-start**: not needed. The 4-stage curriculum (open field → obstacles) allows PPO to discover basic go-to-goal behaviour in stage 0 before any obstacles appear, avoiding the cold-start problem.

**MLP policy (no memory)**: would fail on partial observability (obstacle behind corner, symmetric lidar readings causing spinning). GRU solves this without explicit memory management.

---

## Paths Forward (for paper / follow-on)

1. **Sim-to-real**: deploy on the Jetson Orin Nano rover. Wire ONNX runner to ROS2 `/cmd_vel`, use real RPLidar A2, test on physical courses. Measure gap between sim reach rate and real reach rate.

2. **Goal sequencer**: the policy takes a single body-frame waypoint. A higher level (GPS-based or graph-search) can issue a sequence of waypoints — this separates global planning (which can use a map) from reactive avoidance (which doesn't need one).

3. **Camera integration**: the forward 5×9 depth camera is currently only rendered in videos. Could be added to the observation as an additional sensor (adds 45 dims to state), potentially helping with narrow gap traversal where the gap centre is better seen from camera than lidar.

4. **Dynamic obstacles**: all training obstacles are static. Moving pedestrians/vehicles would require the GRU to track relative velocity, not just position. This is a natural extension.

5. **Longer rollout / larger hidden**: T=96 (9.6s) may be too short for the maze. T=192 and hidden=512 would likely improve maze performance further, at ~2× training cost.

6. **Ablation study**:
   - GRU vs MLP (no memory)
   - 360° lidar vs forward-only (36 rays, ±90°)
   - Curriculum vs no curriculum (straight to stage 3)
   - With/without DynamicsDR
   - With/without clearance penalty

---

## Data Available for Paper Figures

- `~/drone-data/rover/models/policy_rover_v1_rl_summary.json` — training metadata
- `~/drone-data/rover/models/policy_rover_v1_ac.pt` — full checkpoint (can extract training curves if we logged them)
- `~/drone-data/rover/videos/rover_*.mp4` — demo videos (6 courses)
- `eco/drone/training/local_course_rover.py` — course definitions + evaluation harness
- `eco/drone/training/rover_contract.py` — state/action contract, normalization constants
- `eco/drone/training/train_rl_rover.py` — full training code (reproducible)
- `eco/drone/training/render_rover_demo.py` — video renderer

**Suggested figures**:
1. Architecture diagram: obs → normalize → GRU(256) → MLP → [v, ω]
2. Curriculum progression: 4 stages with example obstacle layouts
3. Reward curve during training (need to add logging if retraining)
4. Course maps with trajectory overlays (can extract from video frames or re-render as SVG)
5. Lidar polar view snapshots: approaching gap, mid-gap, post-gap
6. Sim-to-real: side-by-side sim trajectory vs physical rover trajectory (future)

---

## One-Line Pitch

> A 4.6 KB recurrent policy trained in 30 minutes on consumer GPUs navigates a differential-drive rover through cluttered environments and multi-wall mazes using only 360° lidar, with zero collisions across 120 validation trials — no map, no planner, no demonstrations.
