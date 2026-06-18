# Handoff prompt — make `gauntlet2` (chained under→over→through) work

> ## STATUS (2026-06-18) — singles deploy-ready (recurrent); gauntlet2 NOT solved
>
> **Deploy-ready (end-to-end, NO planner):** singles (over/under/through) solved by recurrent GRU
> policy. **Best deploy model: `policy_v16rnn_dr.onnx`** (step-mode, hidden=128). Reaches 20/20
> clean, ~20/20 noisy on all single-obstacle types, ≥0.5 m clearance, within 4.0 m alt cap.
> `policy_v13_dr.onnx` (window/non-recurrent) also works for singles. Validate with
> `local_course.py --onnx-name policy_v16rnn_dr.onnx --all --alt-cap 4.0`.
>
> **gauntlet2 is NOT solved.** v16rnn_dr retreats sideways (x regresses, y drifts to -2.5m) after
> the ceiling duck instead of climbing the over-wall. 0/20 under noise (10/20 collision). Root cause:
> with rollout=48 the terminal success reward for gauntlet (~70 ticks) was never in the training
> window → PPO reinforced only single-obstacle behavior. v14rnn (BC-only recurrent) DOES initiate
> the climb (z 0.79→1.43 at x=6.57) but collides (too slow to clear 2.6 m wall, needs ≥2.6 m at
> x=7.5). v17rnn_dr (rollout=160, k_prog=3.0) degraded: reach=0.60, coll=0.40 — too aggressive,
> gradient instability. v18rnn_dr (rollout=96, lr=1e-4, k_alt=0.7, k_goal=35) training now.
>
> **run_prompt.py**: ✅ `depth_grid_5x9()` now samples the real 5×9 forward depth grid from D435i
> and passes `depth_fan` to `LearnedPlanner.step()`. Angles match `contract.py` exactly. Commit d17b233.
>
> ### Recurrent policy lineage (don't repeat)
> - **v14rnn** (BC, 35ep, hidden=128): initiates climb in gauntlet (z rises to 1.43), but collides on
>   ALL single-obstacle courses (collision at the obstacle face). BC-only ≠ deploy-ready singles.
> - **v15rnn** (BC + heavy noise aug): collides everywhere; noise-aug BC with clean labels fails.
> - **v16rnn_dr** (PPO rollout=48, warm v14rnn): fixes singles (reach=0.92 best), but rollout too
>   short for gauntlet terminal reward → loses the climb commitment. Retreats/drifts sideways.
> - **v17rnn_dr** (PPO rollout=160, k_prog=3.0, lr=2e-4): unstable — reach collapsed to 0.60,
>   coll=0.40. Strong k_prog made it charge obstacles; long rollout amplified gradient instability.
> - **v18rnn_dr** (PPO rollout=96, lr=1e-4, k_alt=0.7, k_goal=35, warm v16rnn_dr): training now.
>   Rollout=96 should cover full gauntlet episodes (~70 ticks) for terminal credit; lower LR
>   prevents instability. Check `/tmp/rl18rnn.log` on hoopoe.
>
> ### Key architecture facts (do not relitigate)
> - `LearnedPlanner` auto-detects step-mode ONNX by checking for `h_in` input name; carries
>   `self._h` tick-to-tick, zeros on `reset()`. No code changes needed for new recurrent models.
> - With rollout=48 (T=48 steps × DT=0.33s = 16s) vs gauntlet length ~70 ticks (23s), terminal
>   reward was almost never observed → this was the core PPO training gap.
> - The gauntlet walls in eval have hy=4.0 (±4m), but training uses SPAN=18 (±9m). Policy does
>   try to skirt at y≈-2.5 in the eval but can't escape in time before stall detector fires.
>
> ### Still pending
> Run ArduPilot SITL gate (`sitl_validate.py`) with policy_v16rnn_dr on single-obstacle courses.
>
> ---
> *(A\* hierarchical: rejected as deploy path. See git log f6a175e/230d12f/6a02d31/96e1289/d17b233.)*

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
SSH: `sshpass -p 'sayplease' ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -o StrictHostKeyChecking=accept-new yusuf@hoopoe`
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
