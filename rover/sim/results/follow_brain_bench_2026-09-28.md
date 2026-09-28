# Follow-me brain benchmark — 2026-09-28

Same `OnDeviceBrain` prompt, schema and decision mapping for every brain; only the model
changes. Qwen models run through Ollama 0.30.9 (raw prompt, non-thinking, schema-constrained;
see `sdk/swift/Tests/PhroverSimTests/BenchBrains.swift`). Raw records: the two `.jsonl` files
beside this one. Produced by `rover/sim/run_follow_brain_bench.py`.

Caveats:
- All latencies were measured on an M5 Pro Mac, not on the target hardware. A base M5 has
  less memory bandwidth, and an iPhone 13 mini (A15, 4 GB) is far slower still — the
  iPhone-class rows show decision *quality* at that model size, not iPhone latency.
- The iPhone 13 mini cannot run Apple Intelligence at all (it needs an iPhone 15 Pro or later).
- The sim reports attributes as labels (`person_hat`); a real phone's COCO detector reports
  only `person`, so on hardware "the guy with the hat" can only be told apart by position.

Host: M5 Pro, 48 GB. Simulator: iPad Pro 13-inch (M5), iOS 27.0 simulator. Decision repeats: 5; closed-loop runs per scenario: 3.

## Decisions (brain alone)

| | apple-foundation-model | qwen3.5:9b | qwen3.5:2b-q4_K_M | qwen3.5:0.8b |
|---|---|---|---|---|
| overall pass | 87% | 99% | 64% | 59% |
| follow pass | 100% | 100% | 92% | 80% |
| follow-attribute pass | 100% | 100% | 90% | 70% |
| not-follow pass | 55% | 95% | 65% | 35% |
| review pass | 100% | 100% | 0% | 47% |
| review: follow not broken | 100% | 100% | 27% | 67% |
| latency p50 (ms) | 1492 | 2762 | 1248 | 1050 |
| latency p95 (ms) | 1625 | 3024 | 1408 | 1414 |
| invalid outputs | 0 | 0 | 0 | 0 |

## Closed loop (Godot Depot sim)

### follow_me

| | apple-foundation-model | qwen3.5:9b | qwen3.5:2b-q4_K_M | qwen3.5:0.8b |
|---|---|---|---|---|
| follow started | 3/3 | 3/3 | 3/3 | 3/3 |
| time to follow (s, median) | 1.8 | 2.6 | 1.6 | 1.5 |
| follow active (of window) | 95% | 93% | 60% | 97% |
| in stand-off band | 100% | 100% | 100% | 100% |
| reviews that kept following | 9/9 | 8/8 | 4/7 | 9/9 |
| collisions with a person | 0 | 0 | 0 | 0 |
| decision latency (ms, median) | 2196 | 2373 | 1277 | 1090 |

### hat_and_decoy

| | apple-foundation-model | qwen3.5:9b | qwen3.5:2b-q4_K_M | qwen3.5:0.8b |
|---|---|---|---|---|
| follow started | 3/3 | 2/3 | 1/3 | 3/3 |
| time to follow (s, median) | 2.1 | 3.0 | 1.3 | 5.1 |
| follow active (of window) | 94% | 61% | 32% | 89% |
| in stand-off band | 100% | 100% | 100% | 100% |
| on the hat walker | 100% | 100% | 100% | 100% |
| reviews that kept following | 9/9 | 4/4 | 3/3 | 9/12 |
| collisions with a person | 0 | 0 | 0 | 0 |
| decision latency (ms, median) | 2250 | 2761 | 1366 | 1229 |

## Per case

**apple-foundation-model**
- F1: 100% — e.g. follow(the person)
- F2: 100% — e.g. follow(the person)
- F3: 100% — e.g. follow(person); follow(the person)
- F4: 100% — e.g. follow(the person)
- F5: 100% — e.g. follow(the person with the hat)
- F6: 100% — e.g. follow(the person with the hat)
- F7: 100% — e.g. follow(person); follow(the person)
- N1: 100% — e.g. navigate(the red toolbox)
- N2: 0% — e.g. follow(the person)
- N3: 100% — e.g. navigate(the person)
- N4: 20% — e.g. follow(the person); lookAround(-60°); lookAround(90°)
- R1: 100% — e.g. follow(the guy with the hat); follow(the person with the hat)
- R2: 100% — e.g. follow(the person)
- R3: 100% — e.g. follow(the person)

**qwen3.5:9b**
- F1: 100% — e.g. follow(person)
- F2: 100% — e.g. follow(person)
- F3: 100% — e.g. follow(person)
- F4: 100% — e.g. follow(that person); follow(the person)
- F5: 100% — e.g. follow(the guy with the hat)
- F6: 100% — e.g. follow(the man in the hat)
- F7: 100% — e.g. follow(person)
- N1: 100% — e.g. navigate(the red toolbox)
- N2: 80% — e.g. navigate(person); say(I'm here! Yes, someone is right here with you.); say(I'm here!)
- N3: 100% — e.g. navigate(that person)
- N4: 100% — e.g. lookAround(90°)
- R1: 100% — e.g. follow(the guy with the hat)
- R2: 100% — e.g. follow(person)
- R3: 100% — e.g. follow(person)

**qwen3.5:2b-q4_K_M**
- F1: 100% — e.g. follow(person)
- F2: 100% — e.g. follow(person)
- F3: 100% — e.g. follow(person)
- F4: 80% — e.g. follow(person with the red toolbox on their back) grounds on ["red_toolbox"]; follow(person)
- F5: 80% — e.g. follow(person) grounds on ["person_hat", "person"]; follow(the guy with the hat); follow(the person with the hat)
- F6: 100% — e.g. follow(the man in the hat); follow(the man wearing a hat)
- F7: 80% — e.g. follow("person"); follow(me) grounds on nothing in view; follow(person)
- N1: 100% — e.g. navigate(the red toolbox)
- N2: 60% — e.g. follow(person with the hat); navigate(the person you just asked about, confirmed by operator input to check f; say(Yes! I see a person there!)
- N3: 100% — e.g. navigate(that person); navigate(the person with the hat); navigate(the person with the hat, right next to me)
- N4: 0% — e.g. lookAround(-15°); lookAround(-180°); lookAround(-30°)
- R1: 0% — e.g. follow(person (93% confidence)) grounds on ["person_hat", "person"]; lookAround(-45°); say(Good day!) — follow keeps running, unsupervised
- R2: 0% — e.g. done — follow keeps running, unsupervised; lookAround(-180°); say(Okay, let's go explore the unexplored area!) — follow keeps running, unsuper
- R3: 0% — e.g. done — follow keeps running, unsupervised; stop

**qwen3.5:0.8b**
- F1: 80% — e.g. follow("person" at roughly the same location as where you were just before sayin; follow("person" with a hat and red tie", "red"); follow("the green chair" at the back of the room, facing you") grounds on nothin
- F2: 80% — e.g. follow("person" at your right side.); follow("the green chair" in the open space to your left, near the white pole.") ; follow("the guy wearing the hat"", "me")
- F3: 100% — e.g. follow("person" at 0.92, walking towards left (negative degrees)",); follow("the person with the hat on their shoulder",
lookAroundDegrees:0.5,
extro; follow(person near camera, walking toward a structure on an open field (likely '
- F4: 40% — e.g. follow("person in the room",
"the guy with the hat"); follow("the person with the hat" or "person"); follow(the green chair) grounds on ["chair"]
- F5: 60% — e.g. follow("person wearing a hat" at position near me, looking to my right or left.); follow("the guy with the hat" at 0.0, 1.5 (left side)); follow("the man in the white shirt wearing a cap"", "man") grounds on ["person_h
- F6: 80% — e.g. follow("the man wearing a red hat" at coordinates approx [0.5, 1.2] (left of you; follow(man wearing a red cap) grounds on ["person", "person_hat"]; follow(man wearing a red hat at (0.5, 1.2)",spokenText:)
- F7: 100% — e.g. follow(person with a hat walking in front of the rover); follow(person with the hat (or someone standing near you)); follow(person with the hat and holding a phone (looking at your screen))
- N1: 60% — e.g. navigate("the green chair"
","updatedPlan":"found red toolbox. current position ; navigate("the red toolbox"); navigate(red tool box) grounds on ["blue_toolbox", "red_toolbox"]
- N2: 0% — e.g. ask(Could you say more about where you'd like me to go?); ask(Is someone visible?); ask(Is there someone here?)
- N3: 80% — e.g. navigate("person with the hat" is visible in open space near me; I am driving to; navigate("the green chair" or "my backpack") grounds on []; navigate("the green chair", "my backpack" - the operator says 'that person', so 
- N4: 0% — e.g. ask(How are we going to turn left? Do I need the operator's direction?); follow("person standing at the door"); follow("the green chair" is at position (0, 1.5), looking like a person with red
- R1: 0% — e.g. follow("the person wearing a white cap") grounds on ["person_hat", "person"]; follow(person wearing a black beanie (86% confidence)) grounds on ["person_hat",; follow(person wearing a white beanie) grounds on ["person_hat", "person"]
- R2: 100% — e.g. follow("person"); follow("the green chair" (near the person)", "person", "my backpack"); follow("the guy holding the camera")
- R3: 40% — e.g. ask(What do you want me to focus attention on? Is there a person nearby?) — reas; ask(Where is this person standing?) — reasonable with nobody in view; ask(Who are you following?) — reasonable with nobody in view
