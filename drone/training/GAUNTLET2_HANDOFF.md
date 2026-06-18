# Handoff prompt — make `gauntlet2` (chained under→over→through) work

> ## STATUS (2026-06-18) -- GAUNTLET2 SOLVED. policy_v26rnn_dr.onnx. Pending SITL gate.
>
> **Deploy-ready (singles):** policy_v16rnn_dr.onnx (step-mode GRU, SITL 3/3). run_prompt.py wired.
> **Gauntlet2 solved:** policy_v26rnn_dr.onnx — 20/20 reach, 0/20 collision, min_clr=0.34m (noisy).
>   Pending ArduPilot SITL gate before declaring fully deploy-ready.
>   Singles also pass (limbo 20/20 0.52m, over_wall 20/20 0.88m, window 18/20 0.36m).
>
> ### Root cause analysis
> MATH: wall x=[6.8,8.2] (1.4m wide), vx~0.14m/tick -> 10 ticks to cross.
> At vz=0.19m/tick descent, drone drops 1.9m. From z=3.65m entry -> z_exit=1.75m < wall top 2.6m.
> Fix requires strong gradient signal THROUGHOUT the wall crossing to maintain altitude.
>
> **k_alt stability limit**: k_alt only penalizes z<goal_z. During limbo duck (z=0.76m, 10 ticks):
> - k_alt=0.7: duck penalty = 0.87/tick x 10 = 8.7 << k_coll=25 (stable)
> - k_alt=1.0: duck penalty = 1.24/tick x 10 = 12.4 (safe margin)
> - k_alt=2.0: duck penalty = 2.48/tick x 10 = 24.8 ~= k_coll=25 (COLLAPSE -- v21)
>   PPO gradients explode when altitude penalty matches collision penalty during duck.
>   DO NOT use k_alt > 1.5. Safe range: 0.7-1.2.
>
> **clear_margin root problem**: min_box_dist is isotropic -- fires for HORIZONTAL approach too.
> At z=0.70m (post-duck), wall side face is only 0.98m away (inside wall z-range).
> clear_margin=1.2m -> penalty fires at x=5.6m (1.2m from wall face at low altitude).
> Policy learns "avoid wall at low z" -> drifts sideways instead of climbing (v22 stall).
>
> **New fix: k_above / above_margin** (added to train_rl.py BoxEnv.above_near()):
> Fires ONLY when drone is in box's x-y footprint AND above its top surface.
> above_near = max over boxes of (above_margin - (z - box_top)).clamp(0) * in_footprint
> - At x=5.82 (outside wall x-footprint |5.82-7.5|=1.68>0.7): near=0. No horizontal avoidance.
> - At x=7.0, z=3.4 (over wall, vert_dist=0.8m): near=0.2, penalty=8x0.2=1.6/tick.
> - At x=7.0, z=3.0 (vert_dist=0.4m): near=0.6, penalty=4.8/tick >> k_prog.
> - During ceiling duck (z=0.7, drone BELOW ceiling top=5.0m): near=0. ✅
> - For window sill (top=1.2m, drone at z=2.0m): near=0.2, penalty=1.6/tick x 4-5 ticks = 7 << k_goal.
>
> ### Recurrent policy lineage (don't repeat)
> - **v14rnn** (BC): collides singles. BC-only fails.
> - **v15rnn** (BC+noise): collides everywhere.
> - **v16rnn_dr** (T=48, k_clear=0.8): DEPLOY singles, SITL 3/3. Gauntlet 0/20.
> - **v17rnn_dr** (T=160, k_prog=3.0): COLLAPSED (0.60 reach). Large k_prog + long rollout.
> - **v18rnn_dr** (T=96, lr=1e-4, k_clear=0.8, k_goal=35, 600 iter): BREAKTHROUGH maneuver.
>   Clips wall (clr=0.24m). k_clear isotropic fires too late.
> - **v19rnn_dr** (k_clear=2.0, margin=0.6): WORSE (clr=0.13m). Margin too small.
> - **v20rnn_dr** (k_clear=4.0, margin=1.0, lr=3e-5, 300 iter): STILL CLIPS (clr=0.08m).
>   Penalty at entry too small to stop 0.19m/tick descent.
> - **v21rnn_dr** (k_alt=2.0, k_clear=6.0, margin=1.2, k_goal=50): COLLAPSED (0.44 reach).
>   k_alt=2.0 duck penalty nearly equals k_coll, PPO gradients destabilize.
> - **v22rnn_dr** (k_alt=1.0, k_clear=5.0, margin=1.2, k_goal=40, lr=3e-5, 400 iter,
>   warm v20rnn_dr_ac.pt): STALL (gauntlet2 stays at z=0.70m). clear_margin=1.2m creates
>   horizontal approach penalty (box_dist to wall side face 0.98m < 1.2m), policy drifts sideways.
> - **v23rnn_dr** (k_above=8.0, above_margin=1.0, k_clear=0.6, margin=0.6, k_alt=1.0, k_goal=40,
>   lr=3e-5, 400 iter, warm v20rnn_dr_ac.pt): COLLISION (clr=0.17-0.20m). Stall FIXED!
>   Drone now climbs to z=4.01m and attempts wall crossing. But above_margin=1.0m fires TOO LATE:
>   wall entry z=3.75m → vert_dist=1.15m > 1.0m → above_near=0 at entry → free descent 0.43m.
>   Then descends 1.13m total over 9 ticks. Needs above_near to fire AT ENTRY.
> - **v24rnn_dr** (k_above=5.0, above_margin=1.5m, k_clear=2.0, margin=0.6, k_alt=1.0, k_goal=40,
>   lr=3e-5, 400 iter, warm v23rnn_dr_ac.pt): COLLISION (clr=0.17m). above_near fires at entry
>   (z=3.78m, near=0.25, 1.25/tick) but too weak. Also: v23 warm-start baked in yaw-drift habit
>   (yaw=-27.8° at t=40, y=1.9m lateral drift). Still descends 1.08m total, clips wall.
>   k_above=5.0 insufficient: needs k_above=8.0 AND above_margin=1.5m TOGETHER.
> - **v25rnn_dr** (k_above=8.0, above_margin=1.5m, k_clear=0.6, margin=0.6, k_alt=1.0, k_goal=40,
>   lr=2e-5, 500 iter, warm v20rnn_dr_ac.pt): COLLISION (clr=0.23-0.25m). Stall fixed, yaw-drift
>   reduced (-10° vs -27.8°), above_near fires 1.84/tick at entry. vz=0.149m/tick vs need 0.139.
>   Gap: 0.01m/tick. Increasing above_margin to 1.7m to push entry signal to 3.44/tick.
> - **v26rnn_dr** (k_above=8.0, above_margin=1.7m, k_clear=0.6, margin=0.6, k_alt=1.0, k_goal=40,
>   lr=2e-5, 500 iter, warm v25rnn_dr_ac.pt): ✅ GAUNTLET2 SOLVED.
>   20/20 reach, 0/20 collision, min_clr=0.34m under depth+target noise.
>   Clean trajectory: duck z=0.94 → climb z=4.05 → wall clr=0.35m → window z=2.08 → goal.
>   Singles: limbo 20/20 0.52m, over_wall 20/20 0.88m, window 18/20 0.36m (2 stall misses).
>   Above_near at entry: vert_dist=1.27m, near=0.43, penalty=3.44/tick >> k_prog=0.3/tick.
>
> ### Key facts (do not relitigate)
> - LearnedPlanner auto-detects step-mode ONNX (h_in input), carries self._h tick-to-tick.
> - T=96 required (gauntlet ~70 ticks). T=48 = no gauntlet credit. T=160 = unstable.
> - 40% RL episodes are SEQUENCE mode (under->over->through). Gauntlet IS in distribution.
> - k_alt safe range: 0.7-1.2. Above ~1.5 collapses training during limbo duck.
> - k_above/above_margin: vertical-only above-surface clearance, replaces large clear_margin.
>   above_near() in BoxEnv fires ONLY when in box x-y footprint AND above box top.
> - Flat copy on hoopoe: BOTH /home/yusuf/ AND ~/astral-training/eco/drone/training/.
>
> ---
> *(A* rejected. Git: f6a175e/230d12f/6a02d31/96e1289/d17b233/d493dff/08d8be8/c2a0b2b)*
Paste everything below into a fresh Claude Code session (run from `~/code/ys/a`).

---

You are continuing a project that built a learned mid-level "analog pilot" for Astral
drones: a small GRU policy that emits body-frame velocity setpoints `[vx,vy,vz,yaw_rate]`
which ArduPilot tracks (GUIDED). It flies smooth paths around/over/under/through obstacles
from a forward depth grid. The current model is `policy_v4_dr` and it WORKS for single
obstacles and dense random courses, but FAILS one deliberately hard course — `gauntlet2`.
**Your job: make `gauntlet2` complete (reach the goal, no collision) while keeping
everything else working.** Don't over-claim — verify by rendering and report honestly.

## The failure
`gauntlet2` = three corridor-spanning obstacles in a row (so the drone can't fly around):
duck UNDER a ceiling (x≈3.5), climb OVER a wall (x≈7.5), thread a WINDOW (x≈11), goal (14,0,2).
Defined in `eco/drone/training/record_comparison.py` `COURSES["gauntlet2"]`.
Current behavior: it ducks under fine, then **stalls in the gap (~x=5)** — keeps ~0.7 m
clearance (no collision) but stops progressing toward the over-wall; the planner's
stall-detector then ends the run. Root cause hypothesis: the *reactive* policy (depth grid +
16-step history) can't sustain the multi-stage commitment duck→climb→thread; coming out of
the duck at low altitude it doesn't commit to the climb.

## What already works (don't regress it)
`policy_v4_dr.onnx`: 97.3% reach / 2.0% collision on 300 random 3D scenarios; single
`limbo` (under), `over_wall` (over), `window` (through, full-corridor wall — genuinely
threaded, verified) all reach with ≥0.5 m clearance; passes ArduPilot SITL on single courses.

## Things already tried (helped, but did NOT fix gauntlet2)
- Sequence training mode (explicit under→over→through) in dataset + RL env.
- 16-step GRU history (was 8) for anticipation/memory.
- More agile expert (max_accel 2.6, max_jerk 5.0), lower RL jerk penalty (k_jerk 0.005).
- Wider obstacle spacing in the eval course.
Net effect: best-DR reach 0.87→0.96, gauntlet2 went from *colliding* to *stalling*. Still no finish.

## The most promising direction (recommended)
**Hierarchical: run the A\* planner ONLINE and have the policy track its waypoints.**
The privileged A\* oracle (`planner3d.astar_path` + `expert.step_follow`) ALREADY solves
gauntlet2 — the whole reason BC works. The only gap is that the reactive net can't replicate
global planning. So ship planner+policy, not policy-alone:
1. Build an occupancy estimate online (accumulate the depth grid into a local voxel map as the
   drone moves, or — for the sim eval — use the known course geometry as a first proof).
2. Run A\* on it each replan tick to get a path; feed the **next waypoint** as the policy's
   `target_xyz` (the policy already tracks a moving local target well — that's what the oracle
   does). The policy handles local smoothing/avoidance; A\* handles the global route.
3. Validate on gauntlet2 (must reach + no collision) then random courses + SITL.

Other levers if you prefer to keep it end-to-end: true carried RNN hidden state (not a fixed
window) or an explicit accumulated occupancy-map input; sub-goal/progress-past-each-obstacle
reward shaping; remove the deploy stall-detector and add an anti-stall reward; train directly
on the gauntlet2 geometry distribution.

## Where everything is
- Code: `eco/drone/training/` (this is a nested git repo `eco/`, branch `feat/learned-pilot-3d`,
  commit that has all this). Key files: `contract.py` (56-dim state, 5×9 depth grid),
  `planner3d.py` (A\* + PathFollower), `expert.py` (oracle `step_follow`), `world3d.py`
  (3D prisms + `window_prisms` + `depth_grid`), `dataset.py` (A\*-oracle BC data; obstacle
  modes: scattered/barrier/window/sequence), `train.py` (BC GRU→ONNX, SEQ_LEN=16),
  `train_rl.py` (PPO, SEPARATE actor/critic, DR curriculum, vectorized env with same modes),
  `validate_v3.py`, `sitl_validate.py`, `record_comparison.py` (Isaac render).
- Deploy wrapper: `eco/drone/common/reactive_planner.py` `LearnedPlanner` (depth-grid input,
  rule-planner fallback, range_gate (0.3,15) m, lenient stall detector).
- Model: `eco/drone/models/policy_v4_dr.onnx` (ONNX self-normalizes; ~114 KB; Orin TensorRT path).

## Training box (hoopoe = dual RTX 5090; NO A100)
SSH: `sshpass -p "$HOOPOE_SSH_PASSWORD" ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -o StrictHostKeyChecking=accept-new yusuf@hoopoe`
- Isaac/torch python: `/home/yusuf/isaac-sim-env/bin/python3` (has numpy, onnxruntime, pymavlink).
- Training package synced to `~/astral-training/`; run as `cd ~/astral-training && PYTHONPATH=. <py> -m eco.drone.training.<mod>`.
- Models staged at `/home/yusuf/models/`.
- Isaac render + SITL run from `/home/yusuf/` using FLAT copies of `record_comparison.py`,
  `reactive_planner.py`, `isaac_vehicle.py`, `world3d.py`, `contract.py`, `video_record.py`.
- ArduPilot SITL binary built at `~/ardupilot/build/sitl/bin/arducopter`.
- Helper scripts on hoopoe: `/tmp/run_v4seq.sh` (dataset→BC→RL+DR), `/tmp/run_v4_render.sh`,
  `/tmp/run_v4_sitl.sh`. Logs at `/tmp/*.log`. Everything runs via `nohup ... &` (long jobs);
  poll logs with `until grep -q DONE ...`.

## Run / verify recipes
- Retrain: edit episodes/iters in `/tmp/run_v4seq.sh`; `ssh hoopoe 'nohup bash /tmp/run_v4seq.sh > /tmp/x.log 2>&1 &'`. (~20 min for 7000 eps + 800 RL iters.)
- Validate: `cd ~/astral-training/eco/drone/training && PYTHONPATH=/home/yusuf/astral-training <py> validate_v3.py --models-dir /home/yusuf/models --n 300`
- Render gauntlet2: stage model to `/home/yusuf/models`, copy the flat files to `/home/yusuf/`,
  `<py> record_comparison.py --models-dir /home/yusuf/models --onnx-name policy_v4_dr.onnx --out /tmp/astral_course --env none`; pull `/tmp/astral_course/chase_gauntlet2.mp4`.
- SITL gate: `bash /tmp/run_v4_sitl.sh` (3D courses through real ArduPilot).

## Pitfalls (these bit me — save yourself the time)
- **Stale flat copies on `/home/yusuf/`**: if you change `SEQ_LEN` or the state contract, the
  ONNX input dims change; you MUST re-copy `reactive_planner.py` + `contract.py` + `world3d.py`
  to BOTH `~/astral-training/eco/drone/training/` AND `/home/yusuf/`, or you get
  `onnxruntime ... INVALID_ARGUMENT: Got invalid dimensions for input: state_window`.
- **Goal must be < 15 m** (LearnedPlanner range_gate) or it rejects at tick 1.
- **Separate actor/critic GRUs are essential** — a shared trunk made PPO collapse run-to-run.
- **DR curriculum must hold-then-ramp** (hold kinematic ~30%, ramp to full by 75%); cold full-DR collapses to 0 reach.
- The depth grid is FORWARD-only (FPV-style). Full-corridor walls force real over/under/through
  (no fly-around) — keep eval walls full-width so a "reach" can't be cheated.
- SSH drops intermittently (password auth) — just retry.

Start by reproducing the gauntlet2 stall (render it, watch `chase_gauntlet2.mp4`), then pursue
the hierarchical planner direction. Verify with a render showing the drone visibly doing
under→over→through, and report the honest reach/collision/clearance numbers.
