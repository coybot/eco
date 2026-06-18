# Handoff prompt — make `gauntlet2` (chained under→over→through) work

> ## STATUS (2026-06-18) — singles deploy-ready; gauntlet2 fails SAFE, not solved
>
> **Deploy-ready (end-to-end policy, NO planner):** robust single-obstacle avoidance (over / under /
> through a corridor-spanning wall) — 20/20 under depth+target noise, ≥0.5 m clearance, within a
> **4.0 m altitude cap**; ~95% on 300 random 3D courses. Best model: **policy_v13_dr** (hidden=128)
> or policy_v10_dr. Validate locally (no Isaac) with `local_course.py --alt-cap 4.0 [--depth-noise
> 0.10 --target-noise 0.20 --trials 20]` using `~/.astral-venv`.
>
> **gauntlet2 is NOT solved end-to-end.** After 13 retrains (v5..v13) it **fails safe** — stalls/
> stops, does NOT collide (0/20 under noise). The reactive 16-frame policy can't reliably execute
> the multi-stage duck→climb→thread under a tight cap + noise.
>
> ### Approaches tried (so the next session doesn't repeat them)
> - **A\* hierarchical** (`HierarchicalPlanner`, `occupancy.py`): solved gauntlet2 with KNOWN boxes,
>   but a global planner needs map knowledge a forward-depth drone lacks, and online A* on
>   accumulated occupancy thrashes the target → **rejected as deploy path** (kept for reference).
> - **Reward/curriculum retrains:** anti-stall (hover-to-timeout was cheaper than collision);
>   un-skirtable walls (HY=9/SPAN=18 → commit over/under, not skirt); altitude cap baked into BC
>   oracle + RL env (no fly-over cheat); altitude-recovery `k_alt`; clearance-margin `k_clear` +
>   sensor-noise DR (noise-robust singles); decorrelate altitude↔over/under (BC + RL) so it climbs
>   over from low; heavy direct gauntlet exposure; network capacity 64→128. Each fixed singles or
>   moved the gauntlet failure (stall ↔ ram ↔ over-climb) but none robustly completed it.
> - **Confirmed root cause:** the policy used CURRENT ALTITUDE as the over/under cue; forced low by
>   the ceiling it wouldn't climb the next floor-wall. Decorrelation broke that (climbs over from
>   z=0.8 now), but in the *sequence* it still won't initiate the climb in the ~4 m gap after a duck.
>
> ### Untried bigger swings (need a decision, not a knob)
> True carried recurrent hidden state (vs the fixed 16-step window); a learned high-level mode/
> sub-goal selector; or relaxed assumptions (wider spacing / correlated-not-white target noise).
>
> ### Still pending for full deploy
> `run_prompt.py` feeds the policy only a center-clearance scalar — wire the real 5×9 depth-grid
> sampling from RealSense (the policy's actual input) + run the ArduPilot SITL gate.
>
> ---
> *(Original hierarchical-A\* note removed — superseded; see git log f6a175e/230d12f/6a02d31.)*

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
