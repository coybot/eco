# Live Depot capstone results — real model, real production loop, Godot sim world

**Date**: 2026-07-10 · **Model**: `us.anthropic.claude-sonnet-4-6` (Bedrock, us-west-2)
**Runner**: `eco/rover/sim/run_live_capstone.py` → `CloudBrainCapstoneTests`
(coybot-sdk, `PhroverSimTests` target)
**Mission**: *"Search the depot for the red toolbox, tell me if anything's out of place,
and stay out of the paint room."* (seed 7)
**Result**: 4 live runs — none reached `.done` (all stopped deliberately once behavior was
well characterized; see below). This is a documentation run (`run once, keep transcript`
per the design plan), which grew to four passes as real bugs and real model-behavior
questions surfaced along the way. Not a pass/fail gate the way the scripted-brain
`CapstoneTests` are — those assert completion; this documents what the real model
actually does.

## What was real vs. simulated

Real: `MissionAgent` (production loop), `CloudBrain` (production HTTP wire path),
`/rover/act`'s `act_handler` + system prompt + `decide` tool schema (the literal
`aws/src/rover.py` code, served locally by `e2e/harness/live_rover_act_bridge.py`), and
Bedrock Claude. Simulated: the Depot (Godot, `eco/drone/sim/godot/scripts/env_depot.gd` +
`phrover_manager.gd`) — rooms, doors, props, geofence, battery. No brain script anywhere —
the model chose every action across all four runs.

**Known sim-fidelity gaps, stated up front:**
- `GodotPerception.capturedFrameJPEG()` returns `nil` — no per-phrover camera view exists
  in Godot yet (a Phase-2 gap noted in that file's doc comment). This is a *text-only*
  cloud-brain run: multi-step planning, memory, exploration, and unprompted reporting are
  genuinely tested; open-vocabulary vision grounding is not (matches
  `CloudBrainLiveMissionTests`' own "vision grounding is probed separately" precedent).
- Godot's `detect()` bakes color into the object label itself (`red_toolbox` /
  `blue_toolbox`), rather than a generic `toolbox` class a real COCO-style detector would
  emit with color inferred from pixels. The model can therefore distinguish them from the
  label alone — capability #7 (asking under genuine visual ambiguity) is not fairly
  exercised by this scenario as built; a real camera feed would be needed to test it
  honestly.

## Capability-by-capability, with the model's actual moves (drawn across all 4 runs)

| Capability | Evidence (from the transcripts) |
|---|---|
| **Reporting what matters, unprompted (#8)** | Every run's first response, before any exploration: correctly flagged the floor spill unprompted — e.g. *"I can already see a spill on the floor near my current position, which may be worth noting."* Consistent across all 4 runs; folded naturally into the opening acknowledgment rather than a separate scripted trigger. |
| **Planning with reconsideration (#3, partial)** | Authored a numbered plan on tick 1 and rewrote it nearly every tick with `[DONE]`/`[IN PROGRESS]` markers against a *growing* candidate list — tracking what had been checked and what remained, entirely in free-form plan text. Run 4 additionally prioritized by opening width unprompted ("HIGH PRIORITY (widest opening)"). **Not** an explicit contingency tree (see below). |
| **Goal-directed exploration (#5)** | Run 2 checked all 11 known openings exactly once each, in a sensible order, before running out of new candidates — no re-visits of a room already marked done. |
| **Persistent world model (#2)** | Correctly distinguished `blue_toolbox` from the target `red_toolbox` by label across runs ("that's not the red one we're after") and carried object sightings forward across ticks without re-derivation. |
| **Recovering from a blocked/failed navigation (#3)** | Runs 3 and 4: when `GodotMotion`'s guard-stall bail-out (added after Run 1, see below) returned `.failed`, the model correctly narrated the failure and *usually* picked a different candidate — e.g. *"Navigation to opening_11 stalled — likely blocked... pivoting to opening_12 next."* This is real, useful recovery behavior neither the scripted brain's design nor Run 1 (pre-fix) demonstrated. |

## Two real bugs found and fixed via these runs

**Run 1** stalled for 10+ minutes: the model tried to navigate to an `imagePoint` derived
from `box_3` — a prop that sits inside the paint/keep-out room — despite having said
"avoid the paint room" in its own plan. The rover reached box_3's vicinity and sat
motionless, `guard_stopped`: `GodotMotion`'s planner kept succeeding (so the replan
fail-fast built for door-block scenarios never fired) while the reactive safety guard held
it just short of `goalTolerance`. Neither `.arrived` nor `.failed` ever resolved. **Fixed**
by adding a "no real progress for N ticks" bail-out to `GodotMotion.drive()`, confirmed
harmless against the full scripted-brain suite before re-running live (see that file's
design comment). Runs 3–4 confirm the fix works — the model gets `.failed` promptly and
usually reacts sensibly.

**Runs 2 and 3's original video pass** captured zero usable frames — `run_live_capstone.py`
launched Godot headless (`gui=False`), which returns empty/erroring vantage textures on
this Mac (the exact same finding from Phase 1's smoke-test work, not re-applied here).
**Fixed** by switching to `gui=True`; verified with a free (non-billed) frame-capture
check before spending a 4th live run. `capstone_full.mp4` is from Run 4, post-fix.

**Honest finding on the paint-room instruction**: the model's own stated intent ("avoid
the paint room") did not prevent Run 1 from navigating toward an object inside it. Its
geofence compliance is only as good as its own reasoning over world coordinates — unlike
the scripted brain, which has an explicit `keepOutRects` check, nothing here graded or
enforced it. Worth a dedicated per-scenario check once a real camera exists.

## Two distinct ways the model got stuck (both real model limitations, not sim bugs)

**Run 2**: after exhausting all 11 known exploration candidates without finding the red
toolbox, the model issued **14 consecutive, functionally identical `lookAround
{angle: 6.28}`** calls (a full 2π rotation, which returns the rover to its *starting*
heading — so a repeat from an unmoved position yields no new information) instead of
concluding "I've checked everything, reporting back." Confirmed as a genuine dead end (not
a sim gap) by independently verifying the grid/geometry still supported progress from that
position — the scripted brain resolves the equivalent situation via its explicit
recovery-vantage fallback.

**Run 4**: after a `.failed` navigation, the model repeatedly **narrated** the same
intent — *"switching to explore opening_12 next"* — five ticks in a row **without ever
issuing the actual `.explore` decision** that would carry it out. Distinct from Run 2's
repeat-scan: here the stated intent and the executed action diverged.

Both suggest the same underlying gap: the tool schema / system prompt has no explicit
"I'm out of good options — report back and stop" action distinct from continuing to act,
so a model that talks itself into a corner has no clean way out other than the hard
`maxTicksPerUtterance` cap.

## The contingency-tree question

The capabilities framework describes capability #3 as maintaining an inspectable
*contingency tree* — multiple named branches, chosen among rather than reactively
replanned. What's observed here (and in the scripted brain) is closer to single-path
reactive planning with a rewritten to-do list: real, useful, and clearly reconsiders, but
not an explicit tree of alternatives held and compared. Worth a dedicated follow-up
scenario (e.g., present two named contingencies up front — "if door A is blocked, take
the interior route via C" — and check the transcript for genuine branch-selection
language) rather than inferring it from capstone transcripts alone.

## Cost

Runs: 1 (~13 calls, stalled), 2 (41 calls), 3 (~26 calls, stopped for the video bug), 4
(~13 calls, stopped once the say-without-follow-through pattern was confirmed) — roughly
90 Bedrock calls total across this write-up, meaningfully more than planned because two
real bugs (the guard stall, the headless video capture) surfaced mid-run and needed a
fresh run to verify each fix. Env-gated (`LIVE_ROVER_ACT_URL`), lives in
`PhroverSimTests/CloudBrainCapstoneTests`, never runs in the fast/scripted-brain gate.

## Reproduce

```bash
cd eco/rover/sim
python3 run_live_capstone.py --seed 7
```
