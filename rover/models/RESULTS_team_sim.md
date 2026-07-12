# Live Depot team results — capability #9, real models, no scripted allocation

**Date**: 2026-07-10 · **Model**: `us.anthropic.claude-sonnet-4-6` (Bedrock, us-west-2),
**3 concurrent instances**
**Runner**: `eco/rover/sim/run_live_team.py` → `TeamCloudBrainTests`
(astral-sdk, `PhroverSimTests` target)
**Mission**: 3 rovers (`team-1`, `team-2`, `team-3`) told to claim an unclaimed room from
a shared team context, search it, and pick up a silent teammate's room if one goes quiet.
`team-3` is killed (`kill_rover`) ~12s in.
**Result**: 2 live runs (1 to find a cold-start bug, 1 clean) — see below. Documentation
run, not a pass/fail gate.

## This is a production change, not just a sim harness

Per explicit direction: capability #9 needed to be demonstrated by the **real model
reasoning about allocation itself** — not a scripted claim/bid algorithm the app runs on
the brain's behalf. That meant extending real production code, not just sim plumbing:

- **`RoverDecision`** (`RoverBrain.swift`): new `.claimRoom(String)` case.
- **`MissionContext`**: new `teamContext: TeamContext?` field (rooms + teammates +
  alive/claimed status) — `nil` for a solo mission, present only for a team one.
- **`MissionAgent`**: new `RoverTeamRadio` protocol (mirrors `RoverBattery`'s seam) —
  `currentTeamContext()` feeds the brain, `broadcastClaim(_:)` relays a `.claimRoom`
  decision to the team. `MissionAgent` only relays; it decides nothing.
- **`CloudBrain`'s wire format**: `ActRequest`/`ActResponse` extended to carry team
  context and the new action.
- **`aws/src/rover.py`** (the real Lambda): `MISSION_AGENT_SYSTEM_PROMPT` teaches
  `claimRoom` semantics and team reasoning; `DECIDE_TOOL`'s action enum gains
  `"claimRoom"`; `_describe_mission` renders team context into the prompt text.

Sim-side: `SimMesh` (in-process message bus, configurable latency/loss — 0.3s / 10% for
this run) and `GodotTeamRadio` (relays claims + heartbeats over the mesh, tracks
freshness — **does not decide anything**, matching the production seam's contract).

## Run 1: a real cold-start bug, found live

All three rovers independently concluded **every other rover was already dead** on their
very first tick and claimed rooms redundantly — because a teammate never-yet-heard-from
defaulted to "not responding" instead of "presumed alive with a grace period." Real
distributed systems need exactly this grace period; without it, negotiation never gets a
chance to happen before everyone's already claimed everything. **Fixed** in
`GodotTeamRadio` (a sim-side fix — presume alive until `missionStart + deadTimeout`, not
just `lastHeartbeat + deadTimeout`).

Notably, even under this bug, the models **self-corrected** as delayed claim broadcasts
arrived — team-2 saw team-1's claim and picked a different room; team-3 detected a
roomA/roomB conflict and adjusted. The reconciliation machinery worked; it just had
unnecessary redundant claims to reconcile away. Confirmed the fix produced clean,
conflict-light negotiation on the next run (below).

## Run 2 (post-fix): the transcript

**Clean initial split**: `team-1` claimed `roomA`, `team-2` claimed `roomB` — no overlap.
`team-3` also claimed `roomB` (a genuine race: its claim decision was made before
team-2's broadcast had crossed the mesh's 0.3s latency, not a repeat of the cold-start
bug) and can be seen mid-transcript still working the roomB/roomC ambiguity out.

**The actual test — team-3 killed, survivors recover**: `team-3` hit `.failed`
("no pose from sim" — its Godot body had just been despawned) around the scripted
12-second kill point. Both survivors **independently and correctly detected the
silence and moved to claim `team-3`'s room**:

> **team-2**: *"Claim roomC (-4.00, 8.00) — team-3 is not responding, picking up their
> claimed room."*
>
> **team-1**: *"Claim roomC (team-3 is NOT RESPONDING and roomC is unclaimed/unfinished)."*

Both survivors reaching the same correct conclusion from the same real degraded-mesh
data — via their own reasoning over `teamContext`, not a scripted "member_lost" event —
is the actual proof point for capability #9's "the team outlives any teammate." (The
resulting roomC double-claim is a second, smaller version of the same race as roomB
above — not chased further in this run; see caveats.)

**Unprompted anomaly reporting held up under concurrency too**: every rover independently
flagged the floor spill early, same as every solo capstone run.

## An interesting near-miss: `.say` isn't broadcast to teammates

`team-2` attempted natural-language team coordination: *"Team-3, roomC is unclaimed —
recommend you grab it!"* — but `RoverVoice.speak` is local-only (the operator's speaker),
never relayed over `SimMesh`. Only `.claimRoom` is a team-visible action. The model
reached for a coordination move the current wire protocol doesn't support relaying —
worth a real product decision: should team chatter be mesh-broadcast too, or should
`claimRoom`/heartbeats remain the only inter-rover channel (arguably cleaner — structured
coordination over free chatter)?

## Caveats

- One survivor logged a passing *"team-2 not responding"* moment while team-2 was
  actually still alive and working — most likely a rover that hadn't called
  `currentTeamContext()` (and therefore drained its mesh inbox) recently enough for a
  couple of heartbeats to look stale, rather than genuine packet loss (loss alone, at
  10%/heartbeat with a 4s/1s timeout/interval ratio, would rarely cause 4 consecutive
  drops). A real product would want mesh draining decoupled from brain-tick cadence.
  - roomB was ultimately double-claimed (team-2 and team-3) and not fully reconciled
  within this run's 20-tick-per-rover budget — a real deployment would want a tie-break
  rule (e.g., lowest rover id wins a tie) rather than relying purely on the models to
  notice and back off.
- `maxTicksPerUtterance: 20` per rover meant none reached `.done` within this run — not
  a failure, just a deliberately tight cap to bound cost across 3 concurrent live brains.

## Cost

Run 1: ~13 calls (stopped after confirming the cold-start pattern). Run 2: ~21 calls
across 3 concurrent rovers (~7 each) before being allowed to run to its tick caps. ~34
Bedrock calls total for this capability, on top of the ~90 already spent on solo-rover
verification (`RESULTS_capstone_sim.md`).

## Reproduce

```bash
cd eco/rover/sim
python3 run_live_team.py --seed 7
```
