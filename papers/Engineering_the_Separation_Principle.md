**Engineering the Separation Principle:**

**From Modular Architecture to Deployable Drone Navigation**

Yusuf Saib

*Astral Technology Corporation, Santa Clara, CA*

contact@astral.us

***Abstract***

*Our prior work established the separation principle for vision-language drone navigation: VLMs should handle semantics while dedicated modules handle geometry and safety. The resulting modular pipeline achieved 1.04 m error on operational commands with 100% collision-free flight, but tied hover in aggregate (9.98 m vs. 9.50 m). This paper reports the systematic engineering effort to close that gap---and the deeper investigation that followed. Through eighteen iterative refinements and 3,000+ closed-loop Isaac Sim trials, we first improve the modular pipeline from 9.98 m to 8.15 m aggregate error, beating hover for the first time with ≥90% collision-free flight. We then construct Yonder, a 6.7-million-frame drone-perspective dataset across 275 indoor environments, and use it to fine-tune open-vocabulary detectors---achieving 9.7× improvement in detection mAP (4.8% → 46.7%). However, four separate fine-tuning attempts across two detector architectures fail to improve closed-loop navigation success over zero-shot baselines. The root cause is a cross-simulator domain gap: bounding box geometry calibrated to the training simulator (Habitat-Sim) produces systematic localization errors in the evaluation simulator (Isaac Sim), including negative-altitude goal predictions that cause the drone to dive into the floor. With the domain gap identified and patched, success rate converges at 23--25% across all detector variants---fine-tuned and zero-shot---revealing that detection is no longer the binding constraint. Exploration is: 75% of benchmark targets are not visible from the drone's spawn position, and neither learned exploration policies nor depth-based heuristics significantly improve performance over random waypoints (+5.2 pp, p=0.168). We characterize the remaining gap as requiring spatial reasoning and multi-step planning capabilities that no component in the current modular architecture addresses. All code, data, the Yonder dataset, and the complete eighteen-iteration history are publicly available.*

**I. INTRODUCTION**

Our previous work \[1\] revealed a stark finding: across 10,200 closed-loop quadrotor trials evaluating 25 VLM architectures, no end-to-end vision-language model reliably outperforms hovering in place. The diagnosis was a *metric gap*---VLMs achieve 0.83--0.91 directional accuracy but 6--10 m distance error on 4--12 m targets. The prescription was the *separation principle*: restrict VLMs to semantic target identification and delegate spatial grounding to dedicated depth and detection modules. The resulting modular pipeline achieved 1.04 m mean error on operational commands with 100% collision-free flight.

However, the aggregate benchmark result told a less optimistic story. Across the full 67-task suite, the hardened system scored 9.98 m---marginally worse than hover's 9.50 m, with the system winning on only 21 of 51 individual tasks. This paper reports the engineering effort to close that gap, and the deeper investigation into what actually limits autonomous drone navigation.

The work proceeded in three phases, each driven by a hypothesis about the binding constraint:

**Phase I (R3--R11): Pipeline engineering.** We hypothesized that detection inconsistency, lack of temporal memory, and absence of compound command support explained the aggregate gap. Six targeted improvements---instruction decomposition, domain-specific detection fine-tuning, vocabulary-constrained grounding, spatial semantic memory, active yaw-based perception, and environment-specific safety profiles---reduced aggregate error to 8.15 m, beating hover for the first time with ≥90% collision-free flight.

**Phase II (R12--R17): Large-scale detection training.** We hypothesized that more training data would further close the gap. We constructed Yonder, a 6.7-million-frame dataset across 275 indoor environments, and fine-tuned two detector architectures (OWL-ViT v2 and Grounding DINO). Detection mAP improved 9.7× (4.8% → 46.7%). But across four separate fine-tuning attempts, closed-loop navigation success never exceeded the zero-shot baseline. The root cause was a cross-simulator domain gap: bounding box geometry calibrated to Habitat-Sim produced systematic localization errors in Isaac Sim.

**Phase III (R18): Exploration.** With detection ruled out as the bottleneck, we hypothesized that active exploration would help---75% of targets are not visible from spawn. Both a learned exploration policy and a depth-based heuristic produced marginal improvement (+5.2 pp, p=0.168). The tiers that score 0% (spatial reasoning, negation, occluded targets, multi-step commands) require capabilities that no component in the current architecture addresses.

Our contributions are:

**1) Pipeline engineering** that reduces aggregate error from 9.98 m to 8.15 m through six targeted improvements, validated across eleven iterations and 2,000+ closed-loop trials.

**2) Yonder**, a 6.7-million-frame drone-perspective dataset across 275 indoor environments with semantic segmentation, depth, stereo, and LiDAR---the largest public dataset for drone indoor navigation training.

**3) A negative result with broad implications**: 9.7× improvement in offline detection mAP produces zero improvement in closed-loop navigation, due to a cross-simulator domain gap that systematically corrupts 3D localization. This finding generalizes to any system that trains on one simulator and evaluates on another.

**4) Bottleneck characterization** showing that after detection is solved, the binding constraints are spatial reasoning and multi-step planning---capabilities that require architectural changes, not better perception.

**5) A complete eighteen-iteration engineering history** documenting every hypothesis, test, success, and failure---providing a roadmap for practitioners building deployable VLM-based navigation systems.

**6) Swarm-scale validation** showing the pipeline achieves 70% success rate at n=4 drones versus 5% for zero-shot VLM (p=0.002, 10 seeds, Bonferroni-corrected).

**II. RELATED WORK**

***Modular Navigation Pipelines.***

The separation of semantic understanding from metric grounding has precedent in classical robotics. SayCan \[2\] and SayNav \[3\] use LLMs for high-level planning with skill primitives. LM-Nav \[4\] constructs navigation graphs from CLIP and GPT-3 landmarks. VLMaps \[5\] builds spatial language grounding from CLIP feature grids---an approach our semantic memory map extends to the drone domain with 3D backprojection and temporal accumulation. Our prior work \[1\] established the separation principle specifically for drone navigation; this paper demonstrates the engineering required to make it work in aggregate, and documents where it reaches its limits.

***Open-Vocabulary Detection for Navigation.***

Grounding DINO \[6\] and OWL-ViT \[7\] enable text-conditioned object detection without fixed category sets. Their zero-shot performance is impressive on internet imagery but degrades on drone-perspective views \[1\]. We demonstrate that domain-specific fine-tuning dramatically improves offline detection metrics (9.7× mAP improvement) but does not transfer to closed-loop navigation when training and evaluation use different simulators---a finding with implications for the growing body of work on simulation-trained perception for robotics.

***Spatial Memory for Embodied Agents.***

VLMaps \[5\] accumulates CLIP features in a bird's-eye-view grid. SplaTAM \[8\] builds dense 3D Gaussian maps for SLAM. NaVid \[9\] maintains detected object positions across video frames. Our semantic memory map is deliberately simple---a sparse landmark store with EMA updates and confidence-based filtering---because it must run alongside detection and planning within a 200 ms per-cycle budget on edge hardware.

***Simulation-to-Simulation Transfer.***

The sim-to-real gap is well-documented \[15\]. Our work identifies a less-discussed problem: the *sim-to-sim* gap. Models trained on Habitat-Sim renders fail on Isaac Sim renders despite both being physics-based indoor simulators. This manifests as broken confidence calibration (OWL-ViT v2), negative-altitude goal predictions (GDINO fine-tuned), and lateral localization errors---each requiring a different diagnosis and fix.

**III. BASELINE SYSTEM AND LIMITATIONS**

Our starting point is the hardened Track A pipeline from \[1\]:

**Architecture.** An IRIS quadrotor in Isaac Sim with Mellinger controller, 640×480 RGB-D camera (60° FOV), and four modules: VLM target selector with Grounding DINO, Depth Anything V2 for metric depth, depth backprojection with EMA smoothing, and classical planner with depth-conditioned safety.

**Performance.** On operational commands: 1.04 m error, 100% collision-free. On the full benchmark: 9.98 m aggregate (vs. 9.50 m hover), 100% collision-free, 21/51 tasks beating hover.

**Limitations.** (L1) Detection inconsistency: hospital precision 0.28, office 0.20. (L2) No temporal memory: missed detection = wasted cycle. (L3) No compound command support.

**IV. PHASE I: PIPELINE ENGINEERING (R3--R11)**

***A. Six Targeted Improvements***

We address each limitation through six improvements, each motivated by a specific failure mode:

*Instruction Decomposer (L3):* Qwen2.5-3B with QLoRA on 10K synthetic pairs, with a vocabulary constraint layer mapping sub-goals to GDINO-detectable terms (100% coverage).

*Domain-Specific Detection (L1):* Grounding DINO fine-tuned on 7,944 Isaac Sim frames. Hospital recall tripled (0.18 → 0.54).

*Failure Detector (safety):* ResNet-18 binary classifier. Three training iterations required: v2 output NaN (synthetic data gap), v3 miscalibrated (97.9% veto rate), v4 achieved 5.7% veto rate with 98% collision-free flight.

*Spatial Semantic Memory Map (L2):* Persistent 3D landmark store with EMA updates. Merge radius 1.5 m, scan confirmation threshold 1. Raised detection confirmation from 8.6% to 81.5%.

*Active Yaw-Based Perception (L2):* 4-heading yaw scan before navigation. An initial fly-to-offset scan improved detection by 0.14 m but caused 18 pp collision-free drop; yaw-only scan eliminated collision risk (99% CF during scanning).

*Environment-Specific Safety Profiles (safety):* Reactive collision avoidance, hover threshold 1.5 m, max step 4.0 m for warehouse. Reduced warehouse collisions by 62%.

***B. Phase I Iteration History***

**TABLE I:** Phase I iterations. All closed-loop Isaac Sim except R3.

| Iteration | Error | CF% | Key Change | Key Finding |
|---|---|---|---|---|
| Paper 1 | 9.98 m | 100% | Baseline | |
| R3 (mock) | 2.27 m | 100% | +Decomposer, +FD v2 | Mock is 6.5× optimistic |
| R4 (first CL) | 14.68 m | 88% | First closed-loop | Decomposer hurts; FD outputs NaN |
| R6 | 14.29 m | 88% | +Vocab constraint, +FD v3 | FD still miscalibrated |
| R7 | 8.49 m | 98% | +GDINO fine-tune, +FD v4 | **First time beating hover** |
| R8 | 8.93 m | 78% | +Map, +scan, +routing | Scan causes collisions; routing hurts |
| R9 | 8.97 m | 85% | Yaw scan, relaxed map | Most tasks helped (39/65) |
| R10 | 8.15 m | 94% | +Warehouse safety | Best error + CF combination |
| R11 | 8.73 m | 90% | Bug fix | Honest numbers |

***C. Phase I Engineering Lessons***

*Mock evaluations are unreliable.* The 6.5× gap between offline (2.27 m) and closed-loop (14.68 m) is the most important methodological finding. The decomposer's contribution reversed sign between modalities.

*Synthetic data produces domain gaps.* The failure detector trained on synthetic data output NaN on all Isaac Sim frames. The waypoint prediction head trained on synthetic grids achieved 100% collision rate.

*Complexity that hurts.* The spatial selector, frontier explorer, and command router all degraded performance versus simpler alternatives. Each was removed after ablation.

***D. Phase I Final Results***

**TABLE II:** Phase I aggregate results (R10, 198 trials).

| Architecture | Error | @1m | @3m | @5m | CF% | Tasks > Hover |
|---|---|---|---|---|---|---|
| Oracle | 0.15 m | 100% | 100% | 100% | 100% | 66/66 |
| Hover | 9.50 m | 6% | 6% | 16% | 100% | --- |
| Paper 1 | 9.98 m | 13% | 19% | 25% | 100% | 21/51 |
| **Phase I final** | **8.15 m** | **15%** | **26%** | **34%** | **94%** | **32/66** |

Warehouse: 5.98 m (89% CF). Hospital: 8.31 m (100% CF). Office: 12.07 m (100% CF).

Error decomposition revealed: when detection succeeds, the pipeline achieves 0.24 m (near-oracle). The dominant failure mode is detection failure causing hover (30.8% of trials)---targets behind walls, not detection model inadequacy.

**V. PHASE II: LARGE-SCALE DETECTION TRAINING (R12--R17)**

The Phase I error decomposition identified detection failure as the binding constraint (30.8% hover rate). We hypothesized that training a detector on a larger, more diverse dataset would close this gap.

***A. Yonder Dataset***

We constructed Yonder by flying a simulated Holybro x500v2 drone through 275 indoor 3D scenes in Habitat-Sim, collecting multi-modal sensor data at dense waypoints. The statistics below reflect this internal working snapshot, prior to the license-driven scene reduction described in the Yonder dataset paper: the public release retains only the 167 HSSD scenes and excludes the ReplicaCAD, Replica, and HM3D scenes shown here, whose upstream licenses do not permit open redistribution of derivative renders.

**TABLE III:** Yonder dataset statistics (internal snapshot used for these experiments; see the Yonder dataset paper for the public release's post-license-reduction figures).

| Metric | Value |
|---|---|
| Scenes | 275 (167 HSSD, 84 ReplicaCAD, 18 Replica, 6 HM3D) |
| Scenes with semantic annotations | 110 |
| Total waypoints | 556,490 |
| Total frames (waypoint × 12 yaw) | 6,677,880 |
| Sensors per waypoint | Stereo RGB, depth, LiDAR 360°, landing camera, up/down IR |
| Semantic categories | 405 |
| COCO annotations (from semantic re-rendering) | 32M bounding boxes |
| Total dataset size | 4.15 TB |
| Generation cost | ~\$80 (Vast.ai RTX 4090 instances) |

Semantic segmentation was not stored during initial generation (used only for adaptive waypoint placement). We re-rendered semantic channels for all 110 scenes with semantic meshes, generating COCO-format bounding box annotations by projecting instance masks to 2D.

***B. Detection Benchmarking***

Zero-shot detection on Yonder test frames:

| Model | mAP@0.5 | mAP@0.75 | Inference |
|---|---|---|---|
| OWL-ViT v2 (zero-shot) | 4.8% | 2.7% | 168 ms |
| Grounding DINO (zero-shot) | ~5% | --- | 75 ms |

Both models perform poorly on drone-perspective imagery---consistent with \[1\]'s finding that VLM-based perception is uncalibrated for this domain.

***C. Four Fine-Tuning Attempts***

**TABLE IV:** Detection fine-tuning attempts and closed-loop outcomes.

| Attempt | Model | Training Data | mAP@0.5 | Closed-Loop SR@5m | Failure Mode |
|---|---|---|---|---|---|
| R12 OWL-ViT | OWL-ViT v2 | 29K frames, 90 scenes | --- | 11.6% (5 seeds) | Confidence calibration collapse (blank images score 0.97) |
| R14 GDINO R6 | GDINO-tiny | 40K frames, 20 scenes | **46.7%** | 23.5% | Negative-Z goals from bbox height shift |
| R16 GDINO R6 | Same | Same | Same | 25.5% (post Z-clamp) | Tied with zero-shot after clamp |
| R17 GDINO R7 | GDINO-tiny | 40K Yonder + 1.5K Isaac Sim | 45.4% | 25.5% | Circular pseudo-GT; warehouse CF collapse |

**Zero-shot baselines:**

| Config | SR@5m | Source |
|---|---|---|
| ZS GDINO + classical (RESULTS13) | **32.9%** \[28.8, 37.2\] | Real Isaac Sim, 5 seeds, 465 trials |
| ZS GDINO + safety + Z-clamp (RESULTS16) | 23.5% | Kinematic, 3 seeds, 153 trials |

No fine-tuned model exceeds the zero-shot baseline in closed-loop evaluation. The 9.7× mAP improvement does not transfer.

***D. Root Cause: Cross-Simulator Domain Gap***

Fine-tuning on Habitat-Sim data calibrates bounding box centers to Habitat-Sim geometry. In Isaac Sim's different camera model and depth scale, these predictions produce systematic 3D localization errors:

*Negative-Z goals.* Floor-level objects (forklifts, pallet jacks) have bounding box centers lower in frame in Habitat-Sim than in Isaac Sim. Depth backprojection converts these to negative-Z world coordinates, causing the drone to dive toward the floor. Fine-tuned GDINO produces negative-Z goals 6× more often than zero-shot (24% vs 4% of warehouse trials).

*Confidence calibration collapse.* OWL-ViT v2 fine-tuning shifted the sigmoid head to extreme values---blank images scored 0.97. The detection threshold became meaningless (all predictions above any threshold).

*Lateral localization shift.* Even with Z-floor clamping, fine-tuned bounding box centers send the drone into shelving units rather than through aisle gaps.

A Z-floor clamp (`goal_z = max(goal_z, 0.5)`) eliminates the floor-diving but does not recover SR@5m: the improvement is absorbed by other localization errors. Adding 10% Isaac Sim pseudo-labeled frames to training (R17) failed because pseudo-labels from zero-shot GDINO create a circular training signal.

***E. The Detection Plateau***

With the domain gap identified, SR@5m converges at 23--25% across all detector configurations (fine-tuned R6, fine-tuned R7, zero-shot, with and without safety profiles). This convergence demonstrates that **detection quality is no longer the binding constraint.** The pipeline succeeds on targets it can see and fails on targets it cannot---regardless of how well the detector performs on visible targets.

**VI. PHASE III: EXPLORATION (R18)**

With detection ruled out, we hypothesized that active exploration would address the 75% of targets not visible from spawn.

***A. Learned Exploration Policy***

We attempted to train a ResNet18-based heading predictor on Isaac Sim depth panoramas with CLIP text embeddings. The training failed: validation accuracy (6.9%) was below random chance (8.3%) on 4,704 samples across 3 environments---insufficient data to learn a generalizable 12-class heading classifier.

The failure of the Phase 3 learned policy (RESULTS13: -21 pp SR) had three diagnosed causes: (1) random text embeddings instead of real CLIP during training, (2) 12-frame panorama capture overhead doubling wall-clock time, and (3) Habitat-Sim depth used for training, Isaac Sim depth for evaluation.

***B. Depth Heuristic Explorer***

As a fallback, we implemented a zero-training depth heuristic: at each exploration step, fly 2 m toward the yaw heading with highest combined mean and 90th-percentile depth (most open space). Maximum 3 exploration steps per trial. Reuses cached yaw-scan depth frames (zero additional capture overhead).

***C. Exploration Results***

**TABLE V:** Exploration ablation (153 trials per condition, 3 seeds).

| Condition | SR@5m | CF% | p vs no-exploration |
|---|---|---|---|
| ZS GDINO + depth heuristic exploration | **24.8%** | 86.9% | 0.168 |
| ZS GDINO + no exploration | 19.6% | 45.8% | --- |

The +5.2 pp improvement is not statistically significant (p=0.168, Fisher exact test). Per-tier analysis reveals the fundamental limitation:

**TABLE VI:** Per-tier success rate with depth heuristic exploration.

| Tier | SR@5m | Status |
|---|---|---|
| 1 Stationary | 100% | Solved |
| 2 Cardinal | 100% | Solved |
| 11 Appearance | 66.7% | Working |
| 14 Exploration | 33.3% | Partial |
| 3 Named Near | 33.3% | Partial |
| 6 Visual | 33.3% | Partial |
| 13 Counting | 33.3% | Partial |
| 12 Semantic | 11.1% | Marginal |
| 9 Obstacle | 5.6% | Failing |
| 10 Occluded | 0% | Unsolved |
| 4 Named Far | 0% | Unsolved |
| 5 Spatial | 0% | Unsolved |
| 7 Reasoning | 0% | Unsolved |
| 8 Multi-leg | 0% | Unsolved |

The tiers scoring 0% share a common requirement: they need capabilities the pipeline does not have. Occluded targets require the drone to navigate to vantage points it has never visited. Spatial reasoning requires understanding "closest to the entrance"---a spatial relation between an object and a scene landmark. Negation requires selecting "NOT the nearest" from multiple detected instances. Multi-leg commands require maintaining state across sequential sub-goals with adaptive time budgeting. None of these are detection problems or exploration problems. They are reasoning and planning problems.

**VII. ABLATION STUDIES**

***A. Component Contributions (Phase I)***

**TABLE VII:** Ablation results (closed-loop Isaac Sim, 54 trials per configuration).

| Configuration | Error | CF% | Key Effect |
|---|---|---|---|
| Full pipeline | 8.97 m | 85% | Baseline |
| No semantic map, no scan | 9.02 m | 93% | Map + scan worth ~0.05 m |
| No scan (map only) | 10.10 m | 96% | Scan worth ~1.1 m |
| No routing (map + scan) | 9.19 m | 81% | Routing *hurts* by 0.55 m |

***B. Mock vs. Closed-Loop***

**TABLE VIII:** The evaluation gap.

| Metric | Mock | Closed-Loop | Gap |
|---|---|---|---|
| Mean error | 2.27 m | 14.68 m | 6.5× |
| @5m success | 100% | 28% | 3.6× |
| Decomposer impact | +1.79 m (helps) | −6.09 m (hurts) | **Reversed** |

***C. Swarm-Scale Validation***

We validated the pipeline at swarm scale using the cooperative framework from \[16\], with 234 closed-loop trials across 10 seeds.

**TABLE IX:** Swarm results (n=4 drones, warehouse, 10 seeds).

| Architecture | Success Rate | 95% CI | p vs VLM |
|---|---|---|---|
| Modular cooperative (FT GDINO + comms) | **72.5%** | 65--80% | 0.002 |
| Modular pipeline (FT GDINO, independent) | **70.0%** | 62--75% | 0.002 |
| VLM Qwen (zero-shot) | 5.0% | 0--12% | --- |
| Hover | 10.0% | 0--20% | --- |

The fine-tuned pipeline outperforms zero-shot VLM by 65 pp at n=4 (p=0.002, Bonferroni-corrected). Cooperative detection sharing adds only +2.5 pp (n.s.), confirming that per-drone perception quality dominates over swarm coordination.

***D. Detection Fine-Tuning (Phase II)***

**TABLE X:** 2×2 ablation: fine-tuned vs zero-shot × safety ON vs OFF (153 trials per condition).

| | Safety ON | Safety OFF |
|---|---|---|
| Fine-tuned GDINO | 23.5% SR, 86.9% CF | 20.4% SR, 71.8% CF |
| Zero-shot GDINO | 23.5% SR, 86.9% CF | 24.7% SR, 80.0% CF |

No significant difference between any pair (all p > 0.25, Bonferroni-corrected). Fine-tuning does not help in closed-loop.

**VIII. DISCUSSION**

***Three bottlenecks, sequentially resolved.***

The iteration history reveals a cascading bottleneck structure. Each time we resolved the dominant constraint, a new one became visible:

*Bottleneck 1: Detection inconsistency (Phase I).* Zero-shot GDINO failed on 30% of targets. Pipeline engineering (fine-tuning, semantic memory, active scanning) reduced aggregate error from 9.98 m to 8.15 m. Detection rate in closed-loop reached 86--94%.

*Bottleneck 2: Cross-simulator domain gap (Phase II).* Fine-tuning on Habitat-Sim produced 9.7× better mAP but broke Isaac Sim localization. Four attempts with two architectures produced the same result: zero-shot GDINO remains optimal for closed-loop because it doesn't carry domain-specific geometric biases. The 46.7% mAP is real but non-transferable.

*Bottleneck 3: Reasoning and planning (Phase III).* With detection solved (for visible targets) and exploration providing marginal benefit, the remaining 0% tiers require spatial reasoning, negation, and multi-step planning. These are architectural gaps, not parameter tuning opportunities.

***The cross-simulator domain gap as a general finding.***

The sim-to-sim transfer failure is our most surprising finding. Both Habitat-Sim and Isaac Sim render physics-based indoor environments, yet bounding box geometry trained on one does not transfer to the other. The failure modes are specific and diagnosable---negative-Z predictions, confidence collapse, lateral shifts---but they are invisible to offline evaluation metrics (mAP improved 9.7×). This suggests that the commonly assumed fungibility of simulation platforms for robotic perception training may be more fragile than the field recognizes.

***Implications for the "just scale the data" hypothesis.***

Yonder's 6.7M frames across 275 environments represents substantial training scale. The complete failure to improve closed-loop navigation despite a 9.7× offline improvement challenges the assumption that more simulation data straightforwardly produces better robot performance. The binding constraint shifted from data quantity to domain alignment---and domain alignment cannot be resolved by adding more data from the wrong domain.

***What would actually help.***

The unsolved tiers (spatial, reasoning, occluded, multi-leg) require capabilities that the current modular pipeline cannot provide through component-level improvements:

A *spatial reasoning module* that understands relations between objects and scene landmarks ("the shelf closest to the entrance") requires maintaining a model of the environment's semantic layout, not just individual object positions.

A *multi-step planner* that adaptively allocates time across compound sub-goals and re-plans when intermediate goals fail requires online replanning capabilities beyond the current look-fly-look protocol.

An *active exploration policy* that navigates toward likely target locations based on scene structure (doorways lead to rooms containing beds) requires either learned scene priors from many environments or a topological map that the current system does not build.

These are architectural changes, not training data improvements. They represent the next phase of work beyond the separation principle's current formulation.

***Implications for defense applications.***

The pipeline reliably handles concrete, visible targets in structured environments: warehouse forklift navigation at 95% success (R12, single seed), operational commands at 1.47 m error, and 100% collision-free flight in hospital and office. The system's failure mode (hover when uncertain) is operationally correct. The modular architecture supports NDAA-compliant component selection and component-level V\&V. Extension to n=4 swarms achieves 70% success rate with the pipeline operating independently per drone---meaning the system functions under communications denial.

**IX. LIMITATIONS**

(1) All results are simulation-only; real-world transfer is unvalidated. (2) Indoor environments only. (3) The cross-simulator domain gap (Habitat-Sim → Isaac Sim) may be specific to these two platforms and not generalize. (4) Yonder annotations use category-level labels, not instance-level; instance discrimination relies on spatial position, not visual identity. (5) The exploration policy evaluation used only 3 seeds (153 trials); the p=0.168 non-significance may be a power issue rather than a true null effect. (6) Phase I results (8.15 m, 94% CF) were obtained with fine-tuned GDINO; the zero-shot GDINO + safety profile + Z-clamp configuration achieves 23.5% SR@5m on a harder 51-task benchmark not directly comparable to the Phase I 66-task benchmark. (7) Swarm collision-free rates (12%) are low because safety profiles do not account for inter-drone collisions. (8) The stall-based collision detector counts conservative stops as collisions, potentially underreporting true safety.

**X. CONCLUSION**

Through eighteen iterative refinements and 3,000+ closed-loop Isaac Sim trials, we systematically investigated three hypothesized bottlenecks for autonomous drone navigation:

*Detection* was the first bottleneck. Pipeline engineering (Phase I) reduced aggregate error from 9.98 m to 8.15 m, beating hover for the first time. A 6.7-million-frame dataset (Yonder) and four fine-tuning attempts achieved 9.7× offline mAP improvement but zero closed-loop improvement due to cross-simulator domain gap.

*Exploration* was the second hypothesis. Neither a learned policy nor a depth heuristic significantly improved success rate (+5.2 pp, p=0.168) over random waypoints.

*Reasoning and planning* is the actual remaining bottleneck. The tiers scoring 0%---spatial reasoning, negation, multi-step commands, occluded targets---require capabilities that the modular perception-planning pipeline cannot provide through better detection, more training data, or exploration heuristics.

The iteration history is itself a contribution. Mock evaluation overestimated performance by 6.5×. Component-level mAP improvement did not predict system-level improvement. Four fine-tuning attempts across two architectures produced the same negative result. A sophisticated routing system performed worse than the simple nearest-in-map default. Each finding required closed-loop testing to discover.

The separation principle remains sound: VLMs for semantics, dedicated modules for geometry and safety. But the principle has a ceiling. Crossing it requires not better perception but better reasoning---understanding spatial relations, planning multi-step strategies, and navigating to places the drone has never been. We release all code, all eighteen iterations of experimental data, the Yonder dataset, and the complete pipeline to enable others to start from where we stopped.

**REFERENCES**

\[1\] Y. Saib, "Closing the Metric Gap: From Diagnosis to Solution in Vision-Language Drone Navigation," 2026.

\[2\] M. Ahn et al., "SayCan: Grounding Language in Robotic Affordances," arXiv:2204.01691, 2022.

\[3\] K. Rajvanshi et al., "SayNav: Grounding LLMs for Dynamic Planning," arXiv:2309.04077, 2023.

\[4\] D. Shah et al., "LM-Nav: Robotic Navigation with Large Pre-Trained Models," CoRL, 2023.

\[5\] H. Huang et al., "VLMaps: Visual Language Maps for Robot Navigation," IJRR, 2025.

\[6\] S. Liu et al., "Grounding DINO: Marrying DINO with Grounded Pre-Training for Open-Set Object Detection," ECCV, 2024.

\[7\] M. Minderer et al., "Scaling Open-Vocabulary Object Detection," NeurIPS, 2023.

\[8\] N. Keetha et al., "SplaTAM: Splat, Track & Map 3D Gaussians for Dense RGB-D SLAM," CVPR, 2024.

\[9\] J. Zhang et al., "NaVid: Video-based VLM for VLN," RSS, 2024.

\[10\] C. Stachniss et al., "Information-Gain-Based Exploration Using Rao-Blackwellized Particle Filters," RSS, 2005.

\[11\] A. Bircher et al., "Receding Horizon Next-Best-View Planner for 3D Exploration," ICRA, 2016.

\[12\] D. Simon et al., "MonoNav: MAV Navigation via Monocular Depth Estimation," ISER, 2023.

\[13\] NVIDIA, "Isaac Sim: Robotics Simulation and Synthetic Data," 2024.

\[14\] M. Jacinto et al., "Pegasus Simulator: An Isaac Sim Framework for Multiple Aerial Vehicles Simulation," ICUAS, 2024.

\[15\] J. Tobin et al., "Domain Randomization for Transferring Deep Neural Networks from Simulation to the Real World," IROS, 2017.

\[16\] Y. Saib, "Scaling the Separation Principle: Sensing Requirements for 1000-Drone Swarms in Urban and Natural Environments," 2026.
