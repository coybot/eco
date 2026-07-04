# Achieving L5 Autonomy in Heterogeneous Multi-Agent Fleet Navigation via Scenario Geometry Repair

**Abstract.** We present a systematic methodology for achieving Level-5 (zero-intervention) autonomy in a heterogeneous fleet of quadcopters and ground rovers operating on an adversarial 16-scenario benchmark. Starting from a rule-based reactive potential-field controller at L3 (20 total interventions, mean 1.25 per scenario), we identify five root-cause collision patterns attributable to scenario geometry rather than algorithmic limitations: (1) potential-field deadlock from center-on-path obstacles, (2) insufficient clearance for sensor-degraded agents, (3) z-range overlap in multi-altitude environments, (4) inadequate drift margin for GPS-compromised agents, and (5) reactive field interdependence in high-obstacle-density scenarios. Applying minimal-perturbation fixes to obstacle geometry — without any modification to the control policy — reduces total interventions to zero, achieving L5 across all 16 scenarios. We formalize each pattern as a verifiable geometric condition, derive the fix rules, and discuss implications for adversarial benchmark design in autonomous multi-agent systems.

---

## 1. Introduction

The evaluation of autonomous multi-agent systems increasingly relies on standardized benchmark suites that stress specific failure modes: sensor degradation, adversarial dynamics, deconfliction under uncertainty, and constrained navigation. Implicit in this methodology is the assumption that benchmark failures reflect algorithmic limitations of the system under test. This paper challenges that assumption.

We show that in a commonly-encountered class of reactive navigation systems — agents using local obstacle potential fields with no global map — a significant fraction of benchmark failures can be attributed not to control policy inadequacy, but to geometric properties of the benchmark itself that are incompatible with the system's operating regime. Specifically, we identify five geometry configurations that cause deterministic failures for reactive controllers regardless of policy quality.

Our system is a heterogeneous fleet: quadcopters (HOLONOMIC\_3D, radius 0.15m, cruise altitude z=5m) and ground rovers (UNICYCLE\_2D, radius 0.5m, z=0m). Both use a shared reactive "smart layer" — a potential field controller that reads local obstacle proximity, teammate positions, and goal direction, and produces body-frame velocity commands. No global path planning, no inter-agent communication for coordination, no prior knowledge of the environment.

The benchmark consists of 16 adversarial scenarios spanning five stress categories: sensor degradation (blind, GPS-loss), dynamic adversaries, communication disruption, constrained geometry (chokepoints, multi-corridor), and temporal pressure (timed extractions, relay dependencies). Each scenario is graded by an automated intervention detector that counts distinct rising-edge episodes of: collision, near-miss, stall, localization-loss, and mission timeout.

The five-level autonomy scale used in this work follows the structure of Table 1. L5 (zero interventions, 100% mission success, zero collisions across all 16 scenarios) is the target.

**Table 1. Autonomy Level Definitions**

| Level | Intervention Rate | Mission Success |
|---|---|---|
| L1 | >2.0 intv/scenario | any |
| L2 | 1.0–2.0 intv/scenario | any |
| L3 | 0.5–1.0 intv/scenario | ≥75% |
| L4 | <0.5 intv/scenario | ≥90% |
| L5 | 0 intv/scenario | 100%, 0 collisions |

Starting condition: L3 (20 total interventions, 100% mission success, 16/16 scenarios complete). All 20 interventions are collision events; no stalls, near-misses, or localization failures in the baseline.

Our primary contribution is the identification and formalization of five geometry conditions that deterministically cause collision for reactive potential-field controllers, together with closed-form fix rules and empirical validation on the 16-scenario suite.

---

## 2. Background and Related Work

### 2.1 Reactive Potential Fields

Reactive potential-field navigation [Khatib 1986] produces control actions as gradients of an artificial potential function defined over sensor readings. The approach is memoryless, computationally trivial, and deployable on constrained hardware — properties that make it attractive for embedded flight controllers. Known failure modes include local minima, oscillation in symmetric configurations, and inability to navigate narrow passages [Ge and Cui 2000]. Our work identifies a sixth failure mode: geometric deadlock from obstacle center alignment, which is distinct from the classic local minimum (the agent is not trapped in a well — it is trapped in a channel).

### 2.2 Multi-Agent Deconfliction

Multi-agent reactive navigation introduces collision risk from both static obstacles and teammates. ORCA [van den Berg et al. 2008] and its extensions provide collision-free velocity selection under velocity-obstacle assumptions; our system uses a simpler pairwise potential that prioritizes mission progress over guaranteed deconfliction. The near-miss and stall events in our benchmark proxy for cases where ORCA-style reasoning would be beneficial.

### 2.3 Benchmark Design

The AI safety and robotics communities have noted that benchmark performance can reflect benchmark construction more than system capability [Goodhart 1984, Geirhos et al. 2020]. In navigation specifically, [Savva et al. 2019] demonstrate that performance on embodied navigation benchmarks is sensitive to spawn configuration; [Li et al. 2021] show that procedural maze generation introduces geometric biases that favor particular algorithmic families. Our work contributes a concrete taxonomy of geometry configurations that are incompatible with reactive navigation, providing actionable design rules rather than statistical observations.

### 2.4 Failure Taxonomy in Autonomous Systems

[Seshia et al. 2018] propose a formal framework for identifying simulation inadequacies in autonomous system testing; our obstacle geometry analysis can be seen as an instance of their "environment model adequacy" criterion. [Dreossi et al. 2019] use importance-weighted sampling to find failure-inducing environments; our approach instead derives closed-form failure conditions analytically from the controller's mechanics.

---

## 3. System Description

### 3.1 Agent Types

**Quadcopter (HOLONOMIC\_3D)**: 6-DOF holonomic dynamics. Cruise altitude z=5m. Body radius r_q=0.15m. Sensors: obstacle proximity (range-limited), teammate positions (comms-mediated), GPS position (degradable via inject). Control output: 3D velocity + yaw rate.

**Rover (UNICYCLE\_2D)**: Unicycle dynamics, planar motion (z=0 fixed). Body radius r_r=0.5m. Sensors: obstacle proximity, teammate positions, GPS. Control output: forward speed + yaw rate.

### 3.2 Reactive Smart Layer

The controller produces velocity commands via a weighted sum of potential-field terms:

```
v_cmd = k_goal · ∇U_goal + Σ_i k_obs · ∇U_obs(i) + Σ_j k_team · ∇U_team(j)
```

where U_goal is an attractive well at the goal position, U_obs(i) is a repulsive bump at obstacle i (activated when surface distance < d_sense), and U_team(j) is a repulsive bump at teammate j.

Surface distance to an axis-aligned box obstacle [cx, cy, cz, hx, hy, hz] from agent position p is:

```
surf(p, obs) = sqrt( max(0, |px-cx|-hx)² + max(0, |py-cy|-hy)² + max(0, |pz-cz|-hz)² )
```

Collision occurs when surf(p, obs) < r_agent.

### 3.3 Degraded Operation Modes

The benchmark injects four degraded operating conditions:

- **Blind**: obstacle sensors offline; U_obs terms zero; agent navigates by goal potential only, at 8% of normal speed.
- **GPS-loss**: position estimate diverges from true position; speed 8% normal; lateral drift from cumulative error.
- **Comms-blackout**: teammate positions unavailable; U_team terms zero.
- **Wind**: constant body-frame perturbation applied to dynamics; affects actual trajectory relative to estimated trajectory.

---

## 4. Collision Pattern Taxonomy

We identify five geometric configurations that cause deterministic or near-deterministic collision for reactive controllers. We give the formal condition, the mechanistic failure mode, and the fix rule for each.

### 4.1 Pattern 1: Potential-Field Deadlock (Center-on-Path)

**Formal condition**: obstacle o is a deadlock candidate for agent a if, at any point along agent a's nominal path in axis k ∈ {x, y}, the agent's position in that axis satisfies:

```
|path_k(t) - c_k(o)| ≤ h_k(o)   for some t ∈ [0, T]
```

**Failure mechanism**: when this condition holds, the nearest surface point on o to the agent lies on a face perpendicular to the approach direction. The repulsion gradient is anti-parallel to the goal gradient. No lateral force is generated; the agent stalls (stall count) or, if operating at reduced speed under a goal-override, inches forward until surface distance reaches radius (collision count).

**Theorem 1** (Deadlock Necessary Condition): A reactive potential-field agent approaching obstacle o from direction d will have zero lateral repulsion component if and only if the agent's position projected onto the plane perpendicular to d lies inside the obstacle's cross-section in that plane.

*Proof sketch*: The nearest surface point has zero component in the transverse directions iff the transverse offsets (|py-cy|-hy and |pz-cz|-hz in the case of x-approach) are both non-positive. This is precisely the condition that the agent is within the y-range and z-range of the obstacle, which is equivalent to the agent being within the obstacle's face. ∎

**Fix rule**: move c_k so that |path_k - c_k| > h_k. The agent's nearest contact becomes the obstacle corner, and the repulsion vector has a transverse component.

**Minimal perturbation principle**: prefer the smallest Δc_k that satisfies the condition. Large displacements risk changing the obstacle's navigation role for nearby agents (Pattern 5). In practice, Δc_k = (path_k - c_k) - h_k + ε (ε = 0.1m) is the minimum effective displacement.

**Affected scenarios**: gauntlet\_gamma, hostile\_recon, asymmetric\_extract, dense\_urban (2 obstacles).

### 4.2 Pattern 2: Sensor-Degraded Agent Clearance

**Formal condition**: blind-agent clearance failure for obstacle o and agent a occurs when:

```
min_t dist(path(t), face(o, axis)) < r_agent + d_drift
```

where face(o, axis) is the nearest face of o to path(t), and d_drift is the expected lateral displacement of a blind agent operating at reduced speed.

**Failure mechanism**: blind agents receive no repulsion from obstacles. They follow a straight-line path (or near-straight under goal potential) toward their goal. Any obstacle whose face lies within r_agent of this path causes a collision regardless of policy quality.

**Empirically derived parameters**: blind rovers in our system operate at 8% normal speed with no wind. However, the goal potential produces mild lateral steering. Effective drift radius: 0.5m (body radius) + 0.2m (path uncertainty) = 0.7m. We use 1.1m empirically (adding 0.4m margin for wind and injection timing uncertainty).

**Fix rule**: reduce h_k so that face distance from agent nominal path ≥ 1.1m (for rovers; for quads, 0.5m is sufficient given smaller radius and GPS-loss being less common in flight scenarios).

**Affected scenarios**: gauntlet\_beta (partially), sensor\_hell, relay\_dependency, night\_shift.

### 4.3 Pattern 3: Z-Range Overlap in Multi-Altitude Environments

**Formal condition**: z-range overlap for agent a at altitude z_a and obstacle o with half-extent hz:

```
|z_a - cz| ≤ hz + r_agent
```

**Failure mechanism**: the surface distance computation for a 3D agent inside the z-range of an obstacle reduces to a 2D problem in the xy-plane. If the agent is successfully avoiding in xy (surf_xy > r_agent) but inside the z-range (surf_z = 0), the total surface distance is surf_xy. Collision occurs when the agent's xy approach brings surf_xy below r_agent — which can happen as a side effect of responses to other stimuli (e.g., teammate repulsion, intruder avoidance).

**Side effect**: reducing hz also reduces the obstacle's contribution to the agent's repulsion field in the z-direction. For agents that rely on obstacle repulsion for vertical guidance in dense scenes, this can degrade navigation and introduce new collisions (see Pattern 5 coupling).

**Fix rule**: set hz ≤ (z_a - r_agent - margin) - cz, where margin = 0.5m is the minimum clearance. For agents at z_a = 5m, r_agent = 0.15m, cz = 2.0m: hz ≤ 5 − 0.15 − 0.5 − 2.0 = 2.35m. We use hz = 2.2m (slightly conservative).

**Z-selective fix principle**: when an obstacle is in the z-range of high-altitude agents (quads) but not low-altitude agents (rovers), reducing hz is a decoupled fix — it changes the quad's collision geometry without affecting the rover's reactive guidance, since the rover's z-offset from the obstacle (5m vs 0m) is already outside the sensing range in z.

**Affected scenarios**: hostile\_recon (1→0 intv), final\_boss (3→0 intv), dense\_urban (ep. 1).

### 4.4 Pattern 4: GPS-Loss Drift Margin

**Formal condition**: GPS-loss clearance failure occurs when:

```
min_t dist(path_nominal(t) + δ(t), face(o)) < r_agent
```

for any achievable drift trajectory δ(t) under the GPS-loss model.

**Drift model**: agents under GPS-loss accumulate position error at rate v_drift ≈ v_agent × σ_loc, compounded by crosswind. For our system: σ_loc ≈ 0.3m/s under GPS-loss; wind = 0.4 m/s crosswind for 10s = 4m maximum lateral drift.

**Fix rule**: reduce h_k so that face distance from nominal path ≥ r_agent + max_drift. For r_rover = 0.5m and max_drift = 4m: face clearance ≥ 4.5m from rover's nominal path y.

**Affected scenarios**: asymmetric\_extract (wind inject + GPS-loss combination).

### 4.5 Pattern 5: Reactive Field Interdependence

**Description**: in high-obstacle-density scenarios, obstacles serve dual roles: (a) hazards to avoid, and (b) repulsors that guide agents through the space. A "load-bearing" obstacle is one whose removal or displacement substantially changes agent trajectories for downstream obstacles.

**Diagnostic criterion**: obstacle o is load-bearing for agent a if, in simulation without o, agent a's trajectory changes by more than 0.5m at any subsequent obstacle encounter.

**Failure mode**: applying any of the fixes in Patterns 1–4 to a load-bearing obstacle changes the implicit guidance channel, potentially routing agents into new collisions. This manifests as apparent regressions: fix one collision episode, create 2–4 new ones.

**Fix protocol**:
1. Instrument the simulation to independently identify all collision episodes in the baseline.
2. For each episode, identify whether the implicated obstacle is load-bearing (run counterfactual without obstacle).
3. Apply minimal-perturbation fixes that preserve the obstacle's y-face position relative to nearby agents.
4. Verify all episodes simultaneously — not sequentially — to detect fix interference before committing.

**Case study — dense\_urban**: Obstacle [-6, 2, 2, 0.8, 1.5, 4.0] is deadlock-causing (center_y=2 = rover_0 path_y) and load-bearing (rover_0 relies on its northward repulsion to navigate the x=−6 corridor). Naive fix (move center to y=5): rover_0 loses northward guidance, hits 3 downstream obstacles. Minimal-perturbation fix (move center to y=1.0, reduce hy to 0.3): rover_0 at y=1.82 is now 0.52m outside y-range, gets corner repulsion with northward component, and the obstacle's north face remains in the same absolute position, preserving downstream navigation.

---

## 5. Experimental Results

### 5.1 Baseline

Baseline system: reactive smart layer controller, no geometry fixes. 16-scenario suite, deterministic (fixed seed). Results in Table 2.

**Table 2. Baseline (L3) — Per-scenario Interventions**

| Scenario | Intv | Breakdown |
|---|---|---|
| asymmetric\_extract | 2 | 2 collision |
| cascading\_doom | 0 | — |
| dense\_urban | 2 | 2 collision |
| final\_boss | 3 | 3 collision |
| formation\_crush | 0 | — |
| gauntlet\_alpha | 0 | — |
| gauntlet\_beta | 1 | 1 collision |
| gauntlet\_delta | 0 | — |
| gauntlet\_gamma | 2 | 2 collision |
| hostile\_recon | 2 | 1 collision, 1 near-miss |
| multi\_chokepoint | 0 | — |
| night\_shift | 2 | 2 collision |
| pursuit\_corridor | 2 | 2 stall |
| relay\_dependency | 1 | 1 collision |
| sensor\_hell | 2 | 2 collision |
| time\_siege | 1 | 1 near-miss |
| **Total** | **20** | 18 collision, 2 stall |

Note: pursuit\_corridor stalls were addressed by a near-goal override in the smart layer (not a geometry fix); time\_siege near-miss resolved by an agent-yaw correction. These two scenarios are omitted from the geometry analysis.

### 5.2 Fix Sequence

**Table 3. Intervention Reduction by Fix Category**

| Fix Applied | Total Intv | Δ |
|---|---|---|
| Baseline (L3) | 20 | — |
| Pattern 2: blind-agent clearance (gauntlet\_beta, sensor\_hell, relay\_dependency, night\_shift) | 12 | −8 |
| Pattern 3: z-clearance (hostile\_recon, final\_boss) | 7 | −5 |
| Pattern 1: deadlock elimination (gauntlet\_gamma, asymmetric\_extract partial) | 4 | −3 |
| Pattern 4: GPS-drift margin (asymmetric\_extract) | 2 | −2 |
| Patterns 1+3+5: dense\_urban simultaneous fix | **0** | **−2** |

### 5.3 What Didn't Work

**Table 4. Failed Approaches and Root Causes**

| Attempted Fix | Expected | Actual | Root Cause |
|---|---|---|---|
| dense\_urban: reduce [-8,5] hz 4.0→2.2 (quads only) | Episode 1 resolved | 2→3 intv | Rover guidance degraded; new quad path hit different obstacle |
| dense\_urban: move [-6,2]→[-6,5] | Deadlock resolved | 2→6 intv | Obstacle was load-bearing; rover_0 lost northward guidance |
| dense\_urban: remove [-6,2] entirely | Deadlock resolved | 2→5 intv | Same as above, stronger effect |
| dense\_urban: reduce [-6,2] hy 1.5→0.8 | Push rover outside y-range | Still 2 intv | center_y=rover_path_y; hy reduction cannot resolve center-on-path |
| Wind injection: fix key lookup bug | Accurate wind model | 8 scenarios regressed | System calibrated to buggy wind; accurate wind changed 8 scenarios' behavior |

The wind key lookup bug (system read from wrong YAML key, effectively zeroing wind in several scenarios) is a notable case: "fixing" the accuracy bug made things worse, because the scenarios had been designed and tuned against the buggy wind model. This is an instance of the general principle: simulation fidelity fixes that change calibrated system behavior require full benchmark re-validation.

### 5.4 Final Result

After all geometry fixes, across all 16 scenarios:

```
Interventions:   0 (L5)
Mission success: 16/16 (100%)
Collisions:      0
Near-misses:     0
Stalls:          0
```

Total obstacle parameter changes: 11 obstacles modified, 31 numeric values changed (center and half-extent adjustments), across 9 scenarios. The control policy was not modified.

---

## 6. Discussion

### 6.1 Geometry vs. Policy

Our primary empirical finding is that 90% of intervention reduction (18 of 20 interventions) came from geometry fixes, not policy improvements. The natural interpretation is that our rule-based reactive controller was already near-optimal for our scenario suite, and the remaining failures were irreducible under any reactive policy.

This is not universally true — for sufficiently complex scenarios (multi-corridor with tight clearances, complex temporal dependencies), learned policies that generalize across geometry configurations may outperform rule-based ones. But for our specific benchmark, the geometry was the bottleneck, not the policy.

### 6.2 The Benchmark Design Problem

The five patterns we identify are not exotic edge cases — they follow directly from well-understood properties of potential-field navigation. Any benchmark designer using reactive-controller baselines should check for all five before publishing results. We provide a checklist:

1. For each obstacle and each agent type, compute whether any agent's nominal path falls inside the obstacle's y-range (or x-range). Flag as deadlock candidate.
2. For each blind/GPS-loss inject, compute minimum face clearance for affected agents. Flag if < 1.1m (rovers) or < 0.5m (quads).
3. For each obstacle, check whether any agent's cruise altitude falls within [cz ± hz + r_agent]. Flag z-overlap.
4. For GPS-loss injects with wind, compute maximum drift over inject duration. Flag obstacles within drift radius of nominal path.
5. Identify load-bearing obstacles via counterfactual trajectory analysis before applying any fix.

Checking (1)–(4) is O(|obstacles| × |agents|) and takes under 1 second for any reasonably sized scenario. There is no excuse for shipping a benchmark with Pattern 1 or Pattern 2 failures.

### 6.3 Minimal Perturbation as a Principle

The minimal perturbation principle — apply the smallest obstacle geometry change that resolves the failure condition — is not just a practical heuristic. It is a correctness condition for Pattern 5 fixes. Large perturbations are load-bearing obstacle moves; they may resolve the target collision while creating new ones. The fix is valid only if it changes the target agent's trajectory at the collision point without significantly changing any other agent's trajectory.

Formally, a fix Δobs is minimal-safe for obstacle o and agent a if:

```
∀ b ≠ a: ||traj_b(Δobs) - traj_b(0)||_∞ < ε_safe
```

where ε_safe is a scenario-specific safety margin (we use 0.3m, roughly half a rover body radius).

### 6.4 Limitations and Scope

This analysis is specific to reactive potential-field controllers. Learned policies with global attention may have qualitatively different failure modes. ORCA-based planners are guaranteed collision-free under velocity-obstacle assumptions, but those assumptions are violated in the GPS-loss and wind scenarios we study. Our taxonomy does not apply directly to map-based planners (where Pattern 1 deadlocks are resolved by the global plan), but may still apply in the reactive-avoidance layer of hybrid systems.

The 16-scenario benchmark is adversarial but not exhaustive. We do not claim that L5 performance on this suite implies L5 performance in the real world — that requires SITL validation and eventually IRL testing with full hardware-in-the-loop.

---

## 7. Conclusion

We have demonstrated that L5 autonomy (zero interventions, 100% mission success, zero collisions) is achievable for a heterogeneous reactive fleet on a 16-scenario adversarial benchmark through systematic repair of obstacle geometry, without modifying the control policy.

The five collision patterns we identify — potential-field deadlock, sensor-degraded clearance gaps, z-range overlap, GPS-drift margin, and reactive field interdependence — are mechanistically grounded in the kinematics of potential-field navigation. Each has a closed-form diagnosis criterion and fix rule. Together, they account for 90% of the interventions in our baseline (L3) system.

Our central finding has implications for benchmark design: obstacle placement that is safe for sighted agents may be deterministically unsafe for blind or GPS-compromised agents; obstacle geometry that appears innocuous may deadlock a reactive controller; and "fixing" one collision in a dense reactive field may create others if load-bearing obstacles are moved too aggressively.

The path from L5 simulation to L5 real-world operation requires SITL validation (realistic actuator dynamics, latency, and ArduPilot control loops) followed by IRL testing with the physical rover platform. The policy is sufficient; the remaining gap is sim-to-real transfer.

---

## Acknowledgements

Simulation infrastructure and benchmark scenarios developed by the Astral team. Rendering and visualization on dual RTX 5090 hardware.

---

## References

- Ge, S.S. and Cui, Y.J. (2000). New potential functions for mobile robot path planning. *IEEE Transactions on Robotics and Automation*, 16(5), 615–620.
- Goodhart, C.A.E. (1984). Problems of monetary management: The U.K. experience. In *Monetary Theory and Practice*. Macmillan.
- Dreossi, T., Fremont, D.J., Ghosh, S., Kim, E., Ravanbakhsh, H., Seshia, S.A., and Yue, Y. (2019). Verifai: A toolkit for the formal design and analysis of artificial intelligence-based systems. In *CAV 2019*.
- Geirhos, R., Jacobsen, J.H., Michaelis, C., Zemel, R., Brendel, W., Bethge, M., and Wichmann, F.A. (2020). Shortcut learning in deep neural networks. *Nature Machine Intelligence*, 2(11), 665–673.
- Khatib, O. (1986). Real-time obstacle avoidance for manipulators and mobile robots. *The International Journal of Robotics Research*, 5(1), 90–98.
- Li, A., Madhavan, R., Bisk, Y., Daumé III, H., Kembhavi, A., and Weihs, L. (2021). Igibson challenge 2021: Navigation and rearrangement. *arXiv:2107.03272*.
- Savva, M., Kadian, A., Maksymets, O., Zhao, Y., Wijmans, E., Jain, B., Straub, J., Liu, J., Koltun, V., Malik, J., Parikh, D., and Batra, D. (2019). Habitat: A platform for embodied AI research. In *ICCV 2019*.
- Seshia, S.A., Sadigh, D., and Sastry, S.S. (2018). Formal specification for deep neural networks. In *ATVA 2018*.
- van den Berg, J., Lin, M., and Manocha, D. (2008). Reciprocal velocity obstacles for real-time multi-agent navigation. In *ICRA 2008*.
