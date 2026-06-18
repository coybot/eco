# Handoff prompt — make `gauntlet2` (chained under→over→through) work

> ## ✅ RESOLVED (2026-06-17) — hierarchical A*+policy
>
> `gauntlet2` now **reaches** (under→over→through, no collision). The fix is the recommended
> hierarchical direction, implemented as `HierarchicalPlanner` in
> `eco/drone/common/reactive_planner.py`: run A* on an occupancy estimate for the global route,
> then feed the policy a target **redirected along the next path segment at cruise range (6 m)**
> instead of the far global goal. The policy keeps cruise speed and reads each segment as a single
> over/under/through problem (which it already solves), so it commits to the climb out of the duck
> instead of skirting the wall. Outer layer owns goal-reach + stall; the inner policy's gates are
> disabled. **No retraining** — same `policy_v4_dr.onnx` weights (md5 `74f6f58…`).
>
> Verified two ways with identical numbers:
> - Local CPU kinematic harness `eco/drone/training/local_course.py` (mirrors `run_pass`, no Isaac).
> - Isaac render on hoopoe (`record_comparison.py`, hierarchical is now the default; `--policy-alone`
>   reproduces the old stall). Videos: `eco/drone/training/videos_gauntlet2/{chase,fpv}_gauntlet2.mp4`.
>
> | course | policy-alone | hierarchical |
> |---|---|---|
> | limbo / over_wall / window | reach | reach (clr 0.70 / 0.87 / 0.41) |
> | **gauntlet2** | stall @ x≈5 | **reach, no coll, clr 0.46, 71 ticks** |
> | 300 random 3D | 97.3% / 2.0% coll | 97.0% / 3.0% coll (parity) |
>
> **Open items for a real vehicle (not needed for the sim proof):** the sim passes the *known*
> course boxes to A* (`step(..., boxes=...)`). On-vehicle, replace that with a local voxel map
> accumulated from the depth grid — the `astar_path` interface is unchanged. Window-thread
> clearance is ~0.46 m (above the 0.3 m halt radius but below the 0.5 m single-course spec);
> tighten later via depth-map A* or a window-centering term if desired. SITL gate not yet re-run
> with the hierarchical wrapper.
>
> Tuning that matters (in `HierarchicalPlanner.__init__`): `cruise_horizon=6.0` and `lookahead=2.0`
> are load-bearing — `cruise_horizon<6` makes the policy decelerate and stall mid-gauntlet;
> `inflate>0.55` closes the under-gap/window so A* finds no path.

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
