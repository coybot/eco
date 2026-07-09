# Live mission-cognition results — real model, real production loop, sim world

**Date**: 2026-07-08 · **Model**: `us.anthropic.claude-sonnet-4-6` (Bedrock, us-west-2)
**Runner**: `eco/e2e/run_live_mission.sh` → `CloudBrainLiveMissionTests` (astral-sdk,
`PhroverKitLiveProbes` target)
**Result**: **2/2 tests passed** (`testLiveGreenChairMission` 24.7s,
`testLiveAsksWhenGoalIsUnknowable` 24.1s) — **15 real model decisions, zero scripted**.

## What was real vs simulated

Real: `MissionAgent` (production loop, its actual 25-tick cap), `CloudBrain` (production
HTTP wire path), `/rover/act`'s `act_handler` + system prompt + `decide` tool schema
(the literal `aws/src/rover.py` code, served by `e2e/harness/live_rover_act_bridge.py`),
and Bedrock Claude. Simulated: the house (start room, hallway behind opening_1, chair
room behind opening_2, 3.5 m chair visibility) and the human's replies. No brain script
anywhere — the model chose every action.

## Capability-by-capability, with the model's actual moves

| Capability | Evidence (from the transcript) |
|---|---|
| **Asking when needed** | Given *"take this to the spot I told you about yesterday"* (empty memory), its first move — before any driving — was: *"I don't have any memory of a specific spot from yesterday — each session starts fresh for me. Could you describe or point to where you'd like me to deliver this?"* Hard-asserted: ask precedes navigation. |
| **Memory (conversational)** | After the human answered *"it's the chair, in the other room"*, it re-planned around the answer and completed the delivery. |
| **Exploration under ambiguity** | Chair not visible, two unexplored openings: it picked `opening_1` and drove through — no coordinate guessing, only real frontier candidates. |
| **Reasoning over discovery (dead end)** | Next tick, context showed `opening_1: visited`, nothing seen. Its own written plan: *"1. [DONE] Tried opening_1 — no chair found. 2. [IN PROGRESS] Explore opening_2…"* — then explored `opening_2`. Hard-asserted: it never re-entered the hallway. |
| **Planning** | It authored a numbered plan unprompted at its first move and rewrote it every tick with [DONE]/[IN PROGRESS] markers, through to *"4. [DONE] Returned to start pose"*. The plan echo is production machinery; the content is entirely the model's. |
| **Grounding** | With the chair visible, it navigated by pointing at the detection's image point (0.5, 0.5) → unprojected to the chair's world position. |
| **"Then come back" (memory, no special-casing)** | After reaching the chair it announced *"Heading back to the start now"* and navigated `worldPoint (0, 0)` — read from mission memory; nothing in production code parses "come back". |
| **Object permanence** | Follow-up *"go back to the chair"* with detections suppressed entirely (never re-saw the chair): first move was `navigate worldPoint (6.00, -3.00)` — the remembered chair coordinate — then `done`. Hard-asserted. |

## Hard assertions that enforced the bar (path-agnostic)

- Mission terminates within the agent's real 25-tick cap (actual: 6–7 ticks per mission).
- Rover physically reaches the chair, then the start, in that order.
- Every navigation target corresponds to a real place (±0.5 m) — no hallucinated coords.
- The hallway is entered at most once (no re-exploring a known dead end).
- In the unknowable-goal scenario: at least one ask, occurring before any navigation.
- The permanence leg reaches the chair with zero detections for the entire leg.

## Notes / honest caveats

- Scenario 1 didn't ask a question — it searched efficiently instead, which the design
  deliberately does not penalize; ask-when-needed is enforced in scenario 2 where asking
  is provably the only rational move.
- In scenario 2 the model returned home after the delivery without being asked —
  harmless initiative, not asserted either way.
- This proves the **cloud tier**. The **on-device tier** (OnDeviceBrain) still lacks a
  real-model run: FoundationModels is available in a bare macOS process on this host but
  reports unavailable inside the iOS Simulator test runner (and PhroverKit can't build
  for plain macOS due to ARKit imports). Three workarounds were attempted and are
  definitively blocked: "My Mac (Designed for iPad)" (unit tests without a host app are
  unsupported there), Mac Catalyst (the AWS SDK xcframework ships no Catalyst slice), and
  a physical-device destination (several Apple-Intelligence-capable iPads are registered
  with this Mac but all were offline). **The moment a registered iPad/iPhone is plugged
  in**: `xcodebuild test -scheme astral-sdk-Package -destination 'platform=iOS,id=<udid>'
  -only-testing:PhroverKitLiveProbes` runs the real on-device probe as-is. See
  PHROVER_SETUP.md — assigned for real-device verification.
- Cost: ~15 Bedrock calls per full run. This suite is env-gated
  (`LIVE_ROVER_ACT_URL`) and lives in `PhroverKitLiveProbes`, which the fast e2e gate
  never runs.

## Reproduce

```bash
AWS_PROFILE=astral bash eco/e2e/run_live_mission.sh
```
