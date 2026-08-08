# Two-drone SAR demo — how to run it

The showcase video: two fixed-wing drones, one tasking, no radio link, and every
in-flight decision made by an 8B vision-language model running on the aircraft.

Everything below runs on this Mac. Godot must run with a real rendering driver
(`gui=True`, which the harnesses set) — headless Godot's dummy driver leaves
every captured frame blank.

```bash
PY=/Users/jsaib/.presidio-venv/bin/python3     # the venv with llama_cpp + cv2
cd eco/drone/sim
```

## What is real, and what is scene direction

Real: the mission decomposition (Bedrock), every perception and every decision
(Qwen3-VL-8B Q4 on the actual forward-camera frames), the flight dynamics, the
occlusion, the ballistic drop, and the peer sightings.

Deterministic, and labelled as such wherever it is shown: the ballistic release
solution, envelope protection, sensor pointing, and the situational summaries in
`situation.py` (which measure and describe — they never choose an action).

Scene direction, i.e. the film set rather than the drones: the target runs for
the tunnel when an aircraft comes in close and low. That is the actor's
behaviour, triggered by proximity rather than a wall clock so it stays in step
with however long inference takes. What the drones do about him is entirely
theirs.

## The gates, in order

Each milestone has a gate that runs against the live sim. Run them in order
after any change to the sensing or brain layers.

```bash
$PY -u tests/test_situation.py        # prompt-block geometry, pinned memory
$PY -u tests/test_brain_v2.py         # label matching, delivery gates, orbit targeting
$PY -u tests/test_fw_altitude.py      # M2: climb, envelope floor, goto altitude
$PY -u tests/test_env_sar.py          # M3: scene to spec, trigger, tunnel occlusion
$PY -u tests/test_fw_sensing.py       # M4: class ranges, gimbal, peer sensing, stigmergy
$PY -u tests/test_capture_recovery.py # forward camera survives VLM GPU load (slow)
$PY -u fw_gate_vlm.py --out gate_out  # M1: red-jacket recognition (~10 min)
$PY -u fw_wall_eval.py --runs 10      # M6: camera-based wall avoidance
```

Regression after any Godot script edit:

```bash
PYTHONPATH=../../..:../../rover/sim:../common:. $PY -m pytest tests/ -q \
  --ignore=tests/test_fw_altitude.py --ignore=tests/test_env_sar.py \
  --ignore=tests/test_fw_sensing.py --ignore=tests/test_capture_recovery.py \
  --ignore=tests/test_situation.py --ignore=tests/test_brain_v2.py
$PY -u ../../rover/sim/depot_smoke.py
```

`tests/test_fixedwing_coordinates.py` has two pre-existing failures: it tries to
`import` a GDScript file as a Python module, which cannot work. Not a regression.

## Shooting the video

```bash
AWS_PROFILE=presidio $PY -u fw_swarm_demo.py --takes 1 --drones 1   # M7, single aircraft
AWS_PROFILE=presidio $PY -u fw_swarm_demo.py --takes 1              # M8, both
AWS_PROFILE=presidio $PY -u fw_swarm_demo.py --takes 20 --record runs   # M10, farm takes
$PY -u fw_edit.py --take runs/take_07 --report swarm_report.json --out final.mp4
```

Plans are cached in `swarm_plan_cache.json` after the first run, so take-farming
does not re-plan and every take is comparable. `--refresh-plan` forces a new one.

Take selection is scored, not eyeballed: `swarm_report.json` marks a take
`all_beats_pass` only if the beats check out against **scene truth**
(`fw_env_state`/`fw_prop_truth`), not against what the drones claimed. A take
with any `envelope_protection` event is a failure by definition — an
intervention means the model did not avoid the obstacle and deterministic code
rescued it.

## If a harness dies at startup

`RuntimeError: Godot did not print 'IPC ready'` almost always means an orphaned
Godot is still holding the port — killing a harness with Ctrl-C or `pkill` stops
the Python side but leaves its Godot child running, since only a clean exit
calls `GodotProcess.stop()`. Check and clear it:

```bash
pgrep -af godot && pkill -f godot
```

Each harness has its own default port (gate 9991, wall 9979, swarm 9978, tests
9982–9986), so two harnesses can run concurrently, but two copies of the *same*
one cannot. Pass `--port` to run a second.

## Two failure modes that look like something else

**The forward camera freezes permanently once the VLM touches the GPU.** Every
capture after that is byte-identical stale pixels while `detect()` keeps
reporting the truth, so the model reads as blind when it is merely being shown a
photograph of the past. This cost a whole perception-gate run (scored 70%;
actually 98%). `SimBackend.capture_frame` now detects and rebuilds, but if you
write a new capture path, hash consecutive captures and treat two identical ones
from two different poses as a hard error. See Bug 11 in
`papers/fixed_wing_sitl_lessons_learned.md`.

**Phase wording is load-bearing.** The on-device grounding guard checks whether a
detected label appears as a substring of the phase's objective text,
one-directionally. "Find the individual in crimson outerwear" contains no
detectable label, so every report gets rejected and the phase silently never
completes — no error, just a mission that runs out of actions. Write objectives
with the detector's plain nouns. See `mission_vocab.PHASE_WORDING_RULES`.

## Claims the footage supports

- The model runs on Orin NX 16GB-class hardware — the weights are 5 GB at Q4.
  Do **not** claim measured Orin NX latency; it has not been measured. Decision
  latency on this Mac is in the report.
- Simulated flight. Say so.
- "No radio link" is architecturally true: there is no inter-drone messaging
  anywhere in the stack. What one aircraft learns about another, it learns by
  looking at it.
