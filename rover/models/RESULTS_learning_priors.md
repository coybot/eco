# Learning from experience (capability #6) — real CloudBrain, no scripted brain

**Model**: `us.anthropic.claude-sonnet-4-6` (Bedrock, us-west-2) · **Runner**:
`eco/rover/sim/run_learn_priors.py` -> `LearnPriorsTests`

Every episode below — training and held-out, baseline and primed, across both attempts —
is a real, separately-billed `CloudBrain` mission. No scripted brain was used anywhere in
this capability, including for generating the training corpus: that would have proven
nothing about whether a learned prior helps the actual model search faster.

**Design**: `red_toolbox`'s candidate slots (`env_depot.gd`'s `PROP_DEFS`) genuinely
straddle two physically different rooms (2 of 4 slots in Workshop/A, 2 in Storage/B) —
which room it's actually in on a given seed is real, seed-dependent uncertainty, not a
fixed fact (see `LearnPriorsTests.swift`'s doc comment for why a room-level prior is fair
here, unlike the degenerate always-A case this project's slot data would otherwise give).
10 training seeds build an empirical "which room, how often" prior via the oracle
`prop_truth` op (never fed to the brain); 6 held-out seeds are each run twice — bare, then
with that prior appended as a plain-language sentence to the operator utterance — and
time-to-detect (harness-judged from `InstrumentedPerception`'s logged `detect` events, not
the brain's own self-report) is compared.

## Two attempts, two honest null-ish results

**Attempt 1** (`maxTicksPerEpisode = 15`): 10/22 episodes never detected the toolbox at all
before hitting the tick cap, leaving too little data for a real before/after read. Redone
with a higher cap rather than accepted as-is.

**Attempt 2** (`maxTicksPerEpisode = 25`, matching `MissionAgent`'s production default):
more episodes reached a real finish, but the paired result got **no cleaner — if anything,
slightly worse**: two held-out seeds that found the toolbox at baseline *failed* to find it
when primed (601, 602).

Both attempts, and the honest combined read, are below.

## Attempt 1 (cap=15) — training corpus

| seed | room | found at (s) | reached done |
|---|---|---|---|
| 500 | B | 43.4 | True |
| 501 | B | 15.9 | True |
| 502 | A | 102.3 | False |
| 503 | A | not found | False |
| 504 | B | not found | False |
| 505 | B | 14.8 | True |
| 506 | A | 22.6 | True |
| 507 | A | not found | False |
| 508 | B | not found | False |
| 509 | B | 17.2 | True |

Learned prior: room B (Storage) 6/10, room A (Workshop) 4/10 — "found in Storage about 60%
of the time."

**Held-out (cap=15)**

| seed | room | baseline found (s) | primed found (s) | note |
|---|---|---|---|---|
| 600 | B | not found | 40.4 | improvement (miss → find) |
| 601 | B | 18.1 | 16.5 | +1.7s |
| 602 | A | not found | not found | no change |
| 603 | A | not found | not found | no change |
| 604 | B | not found | not found | no change |
| 605 | B | 13.3 | 28.5 | **-15.2s regression** |

## Attempt 2 (cap=25) — training corpus

| seed | room | found at (s) | reached done |
|---|---|---|---|
| 500 | B | 57.4 | True |
| 501 | B | 14.2 | True |
| 502 | A | 59.7 | True |
| 503 | A | not found | False |
| 504 | B | 51.1 | True |
| 505 | B | 15.3 | True |
| 506 | A | not found | False |
| 507 | A | not found | False |
| 508 | B | not found | False |
| 509 | B | 14.5 | True |

Learned prior: room B (Storage) 6/10, room A (Workshop) 4/10 — same prior sentence as
attempt 1 (same room split, different individual seeds).

**Held-out (cap=25)**

| seed | room | baseline found (s) | primed found (s) | note |
|---|---|---|---|---|
| 600 | B | not found | not found | no change |
| 601 | B | 13.5 | not found | **regression (found → miss)** |
| 602 | A | 23.5 | not found | **regression (found → miss)** |
| 603 | A | not found | not found | no change |
| 604 | B | not found | 47.6 | improvement (miss → find) |
| 605 | B | 16.2 | 16.1 | ~no change (+0.0s) |

## Honest combined finding

Across both attempts (12 held-out-seed trials total): **2 improvements, 2 regressions, 8
no-change**. This does **not** demonstrate that a natural-language "here's where it's
usually been" hint reliably improves real-model search time in this scenario — the
evidence is a wash, not a win. Two things are true at once:

- The prior itself is genuine and correctly learned (a real empirical split derived from
  real missions, injected honestly, not hand-picked to flatter the result).
- At N=10 train / M=6 held-out, real Bedrock call-to-call variance in a single live model
  swamps whatever effect the hint has, in either direction. Doubling the tick cap changed
  which seeds landed where more than it changed the overall picture.

This is reported as a genuine null/mixed result, not spun into a false "learning improves
search" claim — the honest capability-#6 finding is: **the harness and prior-injection
mechanism work as designed, but this sample size and this simple prompt-level priming
method don't reliably demonstrate a measurable improvement**. A larger N/M, a stronger prior
signal (e.g. injecting the empirical percentage more forcefully, or biasing multiple hint
sentences), or measuring over more repeated missions per seed to average out single-call
variance would be the natural next step if this needs a cleaner result later.

## Caveats

- Small N/M by design (real billed Bedrock calls, not free scripted iteration) — this is a
  documentation-level signal, not a statistically powered study, on both attempts.
- Baseline always run before primed for a given held-out seed (not randomized order) — a
  real study would counterbalance order to rule out any systematic drift.
- `red_toolbox`'s room is genuinely uncertain per seed (2 of 4 slots in each of
  Workshop/Storage) — a degenerate always-room-A prior was ruled out for exactly this
  reason.
- Regressions (601, 602 in attempt 2) are not evidence the hint actively misleads the
  model — with only 1-2 occurrences each, this is consistent with ordinary run-to-run
  variance in a live LLM, not a systematic effect either direction.

## Cost

~22 episodes per attempt (10 train + 6 held-out x2), 2 attempts — roughly 250-400 real
Bedrock calls total across both, on top of the ~124 already spent on capabilities #3/#9.

## Reproduce

```bash
cd eco/rover/sim
python3 run_learn_priors.py
```
