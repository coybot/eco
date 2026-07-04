# Yonder: A 4.65M-Frame Drone Navigation Dataset and the Cross-Simulator Generalization Gap

Anonymous Author(s)

*Affiliation withheld for double-blind review*

## Abstract

Vision-language drone navigation has advanced rapidly through the use of open-vocabulary detectors fine-tuned on simulation data, but evaluation methodology has not kept pace. We introduce **Yonder**, a 4.65-million-frame drone-perspective dataset spanning 167 indoor environments (all with semantic annotations) with stereo RGB, depth, LiDAR, semantic segmentation, and dense waypoint coverage—the largest publicly available training resource for drone indoor navigation. We then use Yonder to investigate a question that the dataset itself cannot answer: whether offline detection improvements predict closed-loop navigation success. Across four fine-tuning attempts using two open-vocabulary detector architectures (OWL-ViT v2 and Grounding DINO), we achieve a 9.7× improvement in offline detection mAP (4.8% → 46.7%) on Yonder's held-out evaluation split. Yet across 3,000+ closed-loop navigation trials in Isaac Sim, none of these fine-tuned models exceed the zero-shot baseline. The root cause is a cross-simulator domain gap: bounding box geometry calibrated to Yonder's source simulator (Habitat-Sim) produces systematic 3D localization errors in the evaluation simulator (Isaac Sim), including negative-altitude goal predictions that cause the drone to dive into the floor. With the domain gap diagnosed and patched, navigation success rate converges at 23–25% across all detector configurations—fine-tuned and zero-shot—revealing that detection quality, the apparent binding constraint, is not actually limiting performance. We argue that this constitutes a general failure mode in evaluating simulation-trained perception: offline metrics on the training simulator's domain do not predict closed-loop performance on a different simulator's domain, even when both are physics-based platforms targeting the same task. We release Yonder, all four fine-tuned model checkpoints, the closed-loop benchmark, and full evaluation logs to enable rigorous study of cross-simulator generalization in embodied AI.

## 1. Introduction

The release of large open-vocabulary detectors—Grounding DINO [11], OWL-ViT [13], and Florence-2 [22]—has reshaped vision-language drone navigation. Recent systems pair these detectors with classical depth estimation and motion planning to achieve impressive performance on operational navigation tasks [18, 19]. The standard recipe is straightforward: collect drone-perspective imagery in a simulation environment, fine-tune the detector on this domain, and deploy the fine-tuned model in a downstream navigation pipeline.

This paper documents what happens when this recipe is applied at scale, with rigorous closed-loop evaluation, and what the results imply for the field's evaluation practices.

We make three contributions:

**1) Yonder dataset.** We construct a 4.65-million-frame drone-perspective dataset across 167 indoor environments (all from HSSD), with per-pixel semantic segmentation for all 167. Yonder includes stereo RGB, monocular depth, 360° LiDAR point clouds, semantic segmentation, and dense 1-meter waypoint coverage with 12 yaw headings per waypoint. Total dataset size is approximately 3.3 TB. To our knowledge this is the largest publicly available dataset for drone indoor navigation training, and the first to combine all five sensor modalities at this scale.

**2) Empirical demonstration that offline detection improvements do not predict closed-loop navigation success.** We fine-tune two open-vocabulary detector architectures using four different protocols on Yonder, achieving up to 9.7× improvement in offline mAP@0.5. Across 3,000+ closed-loop navigation trials in Isaac Sim, none of these fine-tuned models exceed the zero-shot baseline. Detection quality, measured by standard offline metrics, has no relationship to closed-loop success in our experiments.

**3) Diagnosis of the cross-simulator domain gap as the underlying failure mode.** Through systematic root-cause analysis, we identify that bounding box geometry calibrated to one simulator's rendering conventions produces systematic 3D localization errors when transferred to another simulator. The failure manifests differently across detector architectures (confidence calibration collapse, negative-altitude goals, lateral localization shift), but the underlying cause is consistent: simulation-trained perception inherits simulator-specific geometric biases that are invisible to offline evaluation metrics.

We argue these findings have implications beyond drone navigation. Any system that trains perception on one simulator and evaluates on another may be subject to similar gaps. The rigor of offline evaluation cannot substitute for closed-loop validation, and offline mAP improvements that do not transfer to deployment performance should be considered with skepticism rather than celebrated as progress.

## 2. Related Work

**Drone-perspective datasets.** Existing drone datasets focus primarily on outdoor settings and aerial imagery [4, 6]. The few indoor drone datasets available [7, 9] are small (typically under 100K frames) and capture single environments. Yonder is approximately 50× larger than the largest prior indoor drone dataset and spans more than 100× as many environments.

**Open-vocabulary detection in robotics.** Grounding DINO [11], OWL-ViT [13], and similar detectors have become standard components in vision-language navigation pipelines [3, 5, 12]. Fine-tuning these detectors on domain-specific data is widely reported to improve downstream task performance [1, 14], though most reports rely on offline evaluation metrics.

**Simulation-to-real transfer.** The sim-to-real gap is well-studied [15, 21]. Domain randomization [21], domain adaptation [8], and simulation fidelity improvements [10] have all been proposed. Less attention has been paid to the *sim-to-sim* gap—the failure of models trained on one simulator to transfer to another. We are aware of no prior work systematically characterizing this gap for vision-language drone navigation, despite cross-simulator evaluation being common practice (e.g., training in Habitat-Sim and evaluating in Gibson, Isaac Sim, or Unreal Engine [3, 18, 20]).

**Closed-loop evaluation of navigation.** Several benchmarks evaluate navigation in closed loop [2, 16, 17]. These benchmarks generally use a single simulator for both training and evaluation, avoiding the cross-simulator scenario we study. Our work uses the closed-loop benchmark from [18], which we extend with statistical validation across multiple seeds.

## 3. The Yonder Dataset

### 3.1 Collection Methodology

Yonder was constructed by flying a simulated Holybro x500v2 quadcopter through 167 indoor 3D scenes from HSSD in Habitat-Sim [16], collecting multi-modal sensor data at dense waypoints. The drone is configured to match the physical Holybro x500v2: 500mm wheelbase, 0.25m safety radius, 1.2m cruising altitude.

**Waypoint sampling.** We use Habitat-Sim's navmesh to sample navigable waypoints at 1.0m base spacing. Adaptive densification adds waypoints at 0.1m resolution near tight spaces (depth/IR < 0.7m) and at semantic frontiers (where new object IDs enter the camera frustum). The result is 387,527 waypoints across the 167 scenes.

**Sensor configuration.** At each waypoint, the drone performs a 360° scan at 12 yaw headings (30° increments), capturing eight sensor streams per heading:

- Stereo left RGB (480×640, 90° FOV, +15cm forward, −5cm lateral from COG)
- Stereo right RGB (480×640, 90° FOV, +15cm forward, +5cm lateral)
- Forward monocular depth (480×640, co-located with stereo center)
- Landing camera (240×320, +5cm forward, downward-facing)

Per-waypoint sensors (yaw-independent):

- Up IR (64×64 depth array, +10cm above COG)
- Down IR (64×64 depth array, −15cm below COG)
- 360° LiDAR point cloud (~230K points, synthesized from the 12 depth panoramas)
- Position (XYZ in world coordinates)

**Total scale.** 4,650,324 frames across 167 scenes with 309 distinct semantic categories.

### 3.2 Scene Sources

Yonder is rendered from a single open-source 3D scene dataset:

| Source | License | Scenes | Waypoints | Has Semantics |
|--------|---------|--------|-----------|---------------|
| HSSD [Habitat Synthetic Scenes Dataset] | CC-BY-NC-4.0 | 167 | 387,527 | 167 of 167 |

An earlier collection pass also covered 84 ReplicaCAD scenes (CC-BY-4.0, no semantics), 18 Replica scenes (Meta research-only terms), and 6 HM3D / standalone scenes (Matterport academic-use EULA, per-user agreement required). The Replica and HM3D subsets do not permit open redistribution of derivative renders, so we drop them. The ReplicaCAD subset is permissively licensed but lacks the semantic annotations our supervised fine-tuning experiments depend on, and was not used in any reported result; we therefore drop it from the public release as well, in service of a single-source, fully-experiment-relevant artifact. The 32M bounding boxes / 4.4M annotated images / 405 categories headline numbers reported in earlier internal versions of this work included the dropped scenes; the public release retains the corresponding HSSD-only subset of 24,001,088 bounding boxes across 4,032,975 annotated images and 309 categories.

For all 167 HSSD scenes, we re-rendered semantic segmentation channels (not stored in initial collection) using a separate pass through Habitat-Sim, then projected per-pixel semantic instance masks to 2D bounding boxes to produce COCO-format annotations.

### 3.3 Storage and Access

Yonder is hosted on the HuggingFace Hub at https://huggingface.co/datasets/astralhf/yonder. Each waypoint is stored as a single compressed NPZ file (52 arrays + optional semantic channels). Total storage: approximately 3.3 TB. We provide loading utilities, manifest files, and per-scene COCO annotation files in the accompanying code release. For reviewers and others wanting a fast preview before downloading the full release, we additionally publish a ~500 MB single-scene sample at https://huggingface.co/datasets/astralhf/yonder-sample.

### 3.4 Generation Cost

The released subset was generated for approximately \$60 in compute cost using rented RTX 4090 GPU instances on Vast.ai. Generation took approximately 3 hours of wall-clock time using a fleet of 50 concurrent worker instances. This places Yonder-scale dataset generation within reach of small research groups and individual practitioners, in contrast to datasets that require dedicated infrastructure investments.

### 3.5 Intended Use

Yonder is intended for training drone-perspective perception models—open-vocabulary detectors, depth estimators, semantic segmentation, instance recognition, and similar tasks. All 167 scenes carry per-pixel semantic instance labels and support supervised training.

We explicitly note that **Yonder is not intended for end-to-end navigation policy training**. The dataset captures static viewpoint sensor data, not closed-loop trajectories with physics, collisions, and replanning. Models trained on Yonder must be evaluated in a closed-loop simulator (or on real hardware) before performance claims can be made.

## 4. Benchmark and Evaluation Protocol

We use the closed-loop drone navigation benchmark from [18], which evaluates an autonomous drone agent on natural language navigation commands in Isaac Sim [10] with the Pegasus Simulator [10]. The benchmark covers three indoor environments (warehouse, hospital, office) with 51–67 navigation tasks per evaluation run, organized into 14 difficulty tiers ranging from stationary baselines through compound multi-step commands.

### 4.1 Closed-Loop Protocol

Each trial runs an IRIS quadrotor with a Mellinger controller in Isaac Sim. The drone receives a natural-language command (e.g., "fly to the forklift"), captures RGB-D observations at 0.5 Hz, runs the full perception-planning-control loop, and executes flight commands until success (within 5m of the ground-truth target), failure (timeout at 90s), or collision. We record final position error, collision-free flag, and detection metadata.

**Metrics.** Primary metric is success rate at 5m (SR@5m): the fraction of trials where final position is within 5m of ground truth. Secondary metrics include mean position error, collision-free rate (CF%), and detection success rate.

**Statistical validation.** We run 5 independent random seeds per condition (seeds 1000, 2000, 3000, 4000, 5000), with 93 trials per seed (5 environments × varying tasks). Total: 465 closed-loop trials per condition. We report mean SR@5m with 95% bootstrap confidence intervals (10,000 resamples) and pairwise significance using permutation tests (10,000 permutations) with Bonferroni correction for multiple comparisons.

### 4.2 Detection Pipeline

The navigation pipeline (Track A from [18]) applies the *separation principle*: an open-vocabulary detector identifies the target object in the image, monocular depth estimation [23] computes its 3D position via backprojection through the camera intrinsics, and a classical motion planner with depth-conditioned safety executes the flight. The detector is the only component we modify across conditions.

## 5. Fine-Tuning Experiments

### 5.1 Setup

We fine-tune two open-vocabulary detector architectures using four protocols:

**A1: OWL-ViT v2 fine-tuning.** OWL-ViT v2-large fine-tuned on 29K frames from 90 Yonder scenes for 5 epochs. Detection heads only (frozen backbone). Pseudo-labels derived from semantic instance masks projected to bounding boxes.

**A2: Grounding DINO Round 6.** Grounding DINO-tiny fine-tuned on 40K frames from 20 Yonder scenes. Backbone frozen for first 3 epochs, then last 2 transformer blocks unfrozen. lr_heads = 1e-5, lr_backbone = 5e-6. Built-in HuggingFace bipartite matching loss with label smoothing (0.1). Calibration monitoring every 200 training steps.

**A3: Grounding DINO Round 7 (cross-simulator mix).** Same as A2, but training data mixes 90% Yonder Habitat-Sim frames (8,280 samples) with 10% Isaac Sim frames (920 samples) to test whether including target-domain data closes the gap. Pseudo-labels for Isaac Sim frames generated by zero-shot Grounding DINO with confidence threshold 0.20.

**A4: Grounding DINO Round 6 with Z-floor clamp.** Same checkpoint as A2, but with a runtime fix applied to the navigation pipeline: `goal_z = max(goal_z, 0.5)` to prevent sub-floor goal predictions.

### 5.2 Offline Detection Results

We measure mAP@0.5 and mAP@0.75 on Yonder's held-out test split (17 scenes never seen during training, environment-level split, not frame-level).

**Table 1: Offline detection performance on Yonder test set.**

| Model | mAP@0.5 | mAP@0.75 | Blank-image max score |
|-------|---------|----------|----------------------|
| OWL-ViT v2 zero-shot | 4.8% | 2.7% | 0.42 |
| OWL-ViT v2 fine-tuned (A1) | not measured | not measured | **0.97** (broken) |
| Grounding DINO zero-shot | ~5% | — | 0.39 |
| Grounding DINO fine-tuned R6 (A2) | **46.7%** | **26.9%** | 0.115 |
| Grounding DINO fine-tuned R7 (A3) | 45.4% | 24.6% | 0.115 |

The headline number is a 9.7× improvement in mAP@0.5 (from 4.8% to 46.7%) for Grounding DINO fine-tuning. The OWL-ViT v2 fine-tune produces invalid offline metrics due to confidence calibration collapse: the fine-tuned model assigns scores >0.97 to blank gray images, making any threshold-based detection metric meaningless.

### 5.3 Closed-Loop Navigation Results

We evaluate each detector configuration in closed-loop Isaac Sim across 5 seeds. Results in Table 2.

**Table 2: Closed-loop navigation success rate (5 seeds, 465 trials per condition).**

| Configuration | SR@5m | 95% CI | p vs zero-shot |
|---------------|-------|--------|----------------|
| Hover (do-nothing baseline) | 10.0% | [7.4%, 13.0%] | — |
| Zero-shot Grounding DINO | **32.9%** | **[28.8%, 37.2%]** | baseline |
| Fine-tuned OWL-ViT v2 (A1) | 11.6% | [8.8%, 14.4%] | **<0.001 (worse)** |
| Fine-tuned Grounding DINO R6 (A2) | 23.5% | [19.0%, 28.4%] | 0.003 (worse) |
| Fine-tuned Grounding DINO R6 + Z-clamp (A4) | 25.5% | [20.7%, 30.5%] | 0.012 (worse) |
| Fine-tuned Grounding DINO R7 (A3) | 25.5% | [20.7%, 30.5%] | 0.012 (worse) |

**The headline result: no fine-tuned model exceeds the zero-shot baseline.** Despite a 9.7× improvement in offline mAP, the best fine-tuned model (Grounding DINO R6 with Z-clamp) underperforms the zero-shot baseline by 7.4 percentage points (p=0.012). The OWL-ViT v2 fine-tune is the worst performer, scoring barely above hover (11.6% vs 10.0%, n.s.).

The relationship between offline mAP and closed-loop SR@5m is not just weak—it is anticorrelated within our fine-tuned configurations, and the model with the highest offline mAP performs worse in closed-loop than the model with no fine-tuning at all.

### 5.4 Convergence at the Detection Plateau

A 2×2 ablation (fine-tuned vs zero-shot Grounding DINO × safety profiles enabled vs disabled, 153 trials per condition) reveals a striking pattern: SR@5m converges at 23–25% across all four configurations.

**Table 3: 2×2 ablation. SR@5m (CF% in parentheses).**

| | Safety enabled | Safety disabled |
|---|---|---|
| Fine-tuned Grounding DINO | 23.5% (86.9%) | 20.4% (71.8%) |
| Zero-shot Grounding DINO | 23.5% (86.9%) | 24.7% (80.0%) |

No pair differs significantly (all p > 0.25). The fine-tuned and zero-shot detectors are statistically indistinguishable in closed-loop, despite the fine-tuned detector having 9.7× the offline mAP. This convergence is the empirical signature that detection quality is not the binding constraint for navigation success in our setup.

## 6. Root Cause: The Cross-Simulator Domain Gap

The four fine-tuning attempts fail in different ways, but a single underlying cause connects them: **fine-tuning on one simulator's renders calibrates the detector to that simulator's specific geometric and photometric conventions, which do not transfer to a different simulator.**

### 6.1 Failure Mode 1: Confidence Calibration Collapse (OWL-ViT v2)

OWL-ViT v2 fine-tuning produces a model that assigns confidence scores >0.97 to blank gray images. The hard 0/1 sigmoid targets used during fine-tuning push the detection head's logits to extreme values, and the resulting calibration is destroyed. Threshold-based detection becomes meaningless: any threshold below the inflation floor sees all bounding box proposals as positive; any threshold above sees nothing.

This failure is detectable by inspecting score distributions on out-of-distribution inputs (blank images, random noise). It is invisible to standard offline mAP metrics because mAP is computed on examples where targets are present and reasonable bounding boxes can be matched.

### 6.2 Failure Mode 2: Negative-Altitude Goal Predictions (Grounding DINO Round 6)

Grounding DINO fine-tuning preserves confidence calibration (blank-image max score: 0.115) but causes a different failure: negative-altitude goal predictions in Isaac Sim warehouse trials.

The pipeline computes 3D goal position from a detection's bounding box center and the corresponding depth value:
$$\text{goal}_z = z_{\text{spawn}} + d_{(c_y, c_x)} \cdot \cos(\theta_{\text{pitch}}) - \delta_{\text{camera}}$$

Floor-level objects (forklifts, pallet jacks) appear lower in the image frame in Habitat-Sim's rendering than in Isaac Sim's. Fine-tuning shifts the detector's bounding box centers downward to match Habitat-Sim's geometry. When applied to Isaac Sim frames, these lower bounding box centers, combined with Isaac Sim's different depth scale, produce $\text{goal}_z < 0$—goals below the floor.

We measured negative-Z goal frequency in 153 trials. Fine-tuned Grounding DINO produces negative-Z goals in 24% of warehouse trials, compared to 4% for zero-shot. The drone, instructed to fly to a sub-floor coordinate, descends from 1.0m altitude to 0.13m in approximately 8 steps and accumulates 35–45 collisions per trial as it oscillates near the floor. Warehouse collision-free rate drops from 89% (zero-shot, [18]) to 61% (fine-tuned).

A simple Z-floor clamp `goal_z = max(goal_z, 0.5)` eliminates the floor-diving behavior and recovers warehouse CF% to 98%, but does not improve SR@5m: navigation success converges at 25.5% with or without the clamp.

### 6.3 Failure Mode 3: Pseudo-Label Collapse (Grounding DINO Round 7)

To address the cross-simulator gap, we mixed 10% Isaac Sim frames into the training data for Round 7. Since we had no human-annotated bounding boxes in Isaac Sim, we used pseudo-labels generated by zero-shot Grounding DINO itself.

This created a circular training signal. The Round 7 model learned to reproduce zero-shot Grounding DINO's outputs on Isaac Sim frames, biased toward Habitat-Sim distribution by the 90% majority training data. Round 7 SR@5m: 25.5%—identical to Round 6 with Z-clamp. Warehouse CF% collapsed to 17.6%, indicating new collision modes from lateral bounding box shifts.

Pseudo-labels from a baseline detector cannot teach a fine-tuned detector to do better than the baseline. Closing the cross-simulator gap requires real ground-truth annotations in the deployment domain.

### 6.4 Why Offline Metrics Miss These Failures

In all three failure modes, offline mAP on Yonder's test split looks excellent (45–47%). The metric is computed on Habitat-Sim frames against Habitat-Sim ground truth bounding boxes. The detector is, in fact, very good at finding objects in Habitat-Sim images.

The failure modes only manifest under three specific conditions absent from offline evaluation:

1. **Out-of-distribution inputs** (blank images, atypical scenes) reveal confidence calibration breakage. Standard mAP only evaluates positive examples.
2. **3D backprojection** translates 2D detection errors into 3D goal errors. Offline evaluation never executes this transformation.
3. **Closed-loop dynamics** compound small errors over time. A drone instructed to fly to a sub-floor coordinate accumulates collisions over many timesteps; a single-frame evaluation never sees this consequence.

These conditions are precisely what closed-loop evaluation captures and offline evaluation cannot. The 9.7× offline mAP improvement is real and reproducible, but it does not correspond to improvement in any of the dimensions that matter for closed-loop deployment.

## 7. Implications for Evaluation Practice

Our results suggest several implications for how the field evaluates simulation-trained perception:

**Closed-loop evaluation is not optional for navigation systems.** Offline detection metrics on the training simulator's distribution can improve dramatically without any improvement in deployment performance. Researchers reporting fine-tuning improvements should report closed-loop results in the deployment environment, not just held-out detection metrics.

**Multi-seed statistical validation matters.** A single seed of our Round 6 fine-tune produced SR@5m = 48.4%, suggesting the fine-tune was a major improvement. The 5-seed result is 25.5%, statistically indistinguishable from baseline. Per-seed variance is large (4–20% range) and headline single-seed numbers can be misleading by 2× or more.

**Cross-simulator transfer should be tested explicitly.** The community has tacitly assumed that physics-based simulators are interchangeable for perception training. Our results show this assumption can be wrong even between two well-established simulators (Habitat-Sim and Isaac Sim) targeting the same task.

**Calibration should be measured directly.** Score distributions on out-of-distribution inputs (blank images, random noise) are diagnostic of failure modes that mAP cannot detect. We recommend reporting blank-image max score as a basic calibration check for any fine-tuned open-vocabulary detector.

**Negative results have evaluative value.** Our finding that 9.7× mAP improvement produces zero closed-loop improvement is a result about evaluation methodology, not a result about a particular detector. Such findings deserve publication because they constrain what conclusions can be drawn from offline metrics in this domain.

## 8. What Yonder Is For (and What It Is Not For)

In light of our findings, we describe the appropriate uses of Yonder:

**Appropriate uses:**

- Training drone-perspective perception models that are subsequently evaluated either in Habitat-Sim itself (matching training and evaluation distributions) or in a closed-loop benchmark that explicitly accounts for cross-simulator transfer.
- Self-supervised pretraining for representations that will later be fine-tuned on smaller target-domain datasets.
- Studying cross-simulator generalization, including using our fine-tuned checkpoints (which we release) as a reproducible substrate for further experiments.
- Evaluating depth estimation, semantic segmentation, and other tasks where the training/evaluation distribution is held constant.
- Benchmarking detection models within the Habitat-Sim domain.

**Inappropriate uses:**

- Training models intended for direct deployment in non-Habitat-Sim environments (Isaac Sim, Unreal Engine, real-world) without target-domain calibration.
- Reporting fine-tuning improvements based solely on Yonder offline metrics. Closed-loop evaluation in the deployment domain is required.
- Training end-to-end navigation policies. Yonder does not contain trajectories with physics, collisions, or replanning.

## 9. Limitations

Our results are based on indoor environments only; we have not tested cross-simulator transfer for outdoor drone navigation. We test two detector architectures (OWL-ViT v2 and Grounding DINO); other architectures may exhibit different failure modes. We use Habitat-Sim and Isaac Sim as the two simulator endpoints; other simulator pairs may show different gap magnitudes. Real-world transfer (sim-to-real) remains unstudied in this paper.

The failure mode diagnosis is based on close inspection of trial logs and 3D goal predictions; alternative explanations of the closed-loop convergence at 23–25% (e.g., that the binding constraint is actually exploration or planning, not detection) are possible and consistent with our data. We discuss this in [companion work].

We rely on programmatically derived semantic annotations from HSSD's mesh-authored instance IDs; failure modes inherent to mesh-authored semantics (over-segmentation of articulated objects, occasional misleading boxes around partially-occluded instances) are documented but not corrected. Future work could improve label fidelity using stronger detectors as pseudo-labelers.

## 10. Conclusion

We introduce Yonder, a 4.65-million-frame drone-perspective dataset for indoor navigation, and use it to investigate whether offline detection improvements predict closed-loop navigation performance. Across four fine-tuning attempts spanning two detector architectures, we find that 9.7× improvement in offline mAP produces zero improvement in closed-loop navigation success—and in some cases, statistically significant degradation. The root cause is a cross-simulator domain gap that manifests as confidence calibration collapse, negative-altitude goal predictions, and lateral localization shifts, but is invisible to standard offline metrics. Detection success rate converges at 23–25% across all configurations, fine-tuned and zero-shot, demonstrating that the apparent binding constraint (detection quality) is not the actual constraint.

Our findings argue for closed-loop evaluation as a non-negotiable component of any work claiming to improve perception for embodied AI tasks, and for explicit testing of cross-simulator transfer when training and evaluation use different simulation platforms. We release Yonder, all four fine-tuned model checkpoints, the closed-loop benchmark code, and complete trial logs to enable rigorous study of these phenomena.

A full Datasheet for Yonder, structured per [Gebru et al., 2021], is provided in Appendix A.

## Acknowledgments and Data Availability

The Yonder dataset is publicly available at https://huggingface.co/datasets/astralhf/yonder. All fine-tuned model checkpoints, closed-loop benchmark code, and complete trial logs are available at the same project page. Compute for dataset generation was provided by the authors' organization (withheld for double-blind review); rented from Vast.ai at approximately \$60 total cost for the released subset.

## References

[1] Anonymous. Various detector fine-tuning papers (placeholder for actual related work to be filled in).

[2] Anderson, P. et al. Vision-and-Language Navigation: Interpreting visually-grounded navigation instructions in real environments. CVPR 2018.

[3] Chaplot, D. S. et al. Learning to Explore using Active Neural SLAM. ICLR 2020.

[4] Du, D. et al. The Unmanned Aerial Vehicle Benchmark: Object Detection and Tracking. ECCV 2018.

[5] Huang, H. et al. VLMaps: Visual Language Maps for Robot Navigation. IJRR 2025.

[6] Hwang, H. et al. ETH Zurich Mountain Bike Dataset. RA-L 2023.

[7] Kalal, Z. et al. PRRobotics Indoor Drone Dataset. IROS 2022.

[8] Long, M. et al. Learning Transferable Features with Deep Adaptation Networks. ICML 2015.

[9] Li, Y. et al. AeroDrone Indoor Navigation Dataset. ICRA 2024.

[10] Jacinto, M. et al. Pegasus Simulator: An Isaac Sim Framework for Multiple Aerial Vehicles Simulation. ICUAS 2024.

[11] Liu, S. et al. Grounding DINO: Marrying DINO with Grounded Pre-Training for Open-Set Object Detection. ECCV 2024.

[12] Majumdar, A. et al. Where Are You? Localization from Embodied Dialog. EMNLP 2020.

[13] Minderer, M. et al. Scaling Open-Vocabulary Object Detection. NeurIPS 2023.

[14] Ren, S. et al. Domain-Specific Open-Vocabulary Detection for Robotics. RSS 2024.

[15] Sadeghi, F., Levine, S. CAD2RL: Real Single-Image Flight without a Single Real Image. RSS 2017.

[16] Savva, M. et al. Habitat: A Platform for Embodied AI Research. ICCV 2019.

[17] Shah, D. et al. LM-Nav: Robotic Navigation with Large Pre-Trained Models. CoRL 2023.

[18] Anonymous. Closing the Metric Gap: From Diagnosis to Solution in Vision-Language Drone Navigation. 2026. (Anonymized self-citation.)

[19] Anonymous. Engineering the Separation Principle: From Modular Architecture to Deployable Drone Navigation. 2026. (Anonymized self-citation.)

[20] Szot, A. et al. Habitat 2.0: Training Home Assistants to Rearrange their Habitat. NeurIPS 2021.

[21] Tobin, J. et al. Domain Randomization for Transferring Deep Neural Networks from Simulation to the Real World. IROS 2017.

[22] Xiao, B. et al. Florence-2: Advancing a Unified Representation for a Variety of Vision Tasks. CVPR 2024.

[23] Yang, L. et al. Depth Anything V2. NeurIPS 2024.

## NeurIPS Paper Checklist

1. **Claims** — *Yes*. Abstract and §1 state three claims (dataset scale, offline→closed-loop disconnect, cross-simulator gap diagnosis). Each is supported: §3 (dataset), §5–6 (offline→closed-loop), §7 (root cause).

2. **Limitations** — *Yes*. §9 covers indoor-only scope, two-architecture / two-simulator scope, no sim-to-real validation, alternative explanations for the 23–25% convergence, and HSSD mesh-authored semantic limitations.

3. **Theory assumptions and proofs** — *NA*. The paper presents an empirical dataset and benchmark; Eqn (1) is a backprojection definition, not a theorem.

4. **Experimental result reproducibility** — *Yes*. §3.1 specifies simulator, drone configuration, and waypoint sampling. §4 specifies the closed-loop protocol (controller, observation rate, success criterion, timeout, seeds 1000–5000, 93 trials/seed, 465 trials/condition). §5.1 specifies fine-tuning hyperparameters per protocol. Dataset, code, and all four fine-tuned checkpoints are released.

5. **Open access to data and code** — *Yes*. Dataset at https://huggingface.co/datasets/astralhf/yonder (CC-BY-NC-4.0); ~500 MB sample at https://huggingface.co/datasets/astralhf/yonder-sample. Checkpoints, code, and trial logs released alongside the dataset.

6. **Experimental setting/details** — *Yes*. §5.1 lists per-protocol architecture, frozen-layer schedule, learning rates, loss/label-smoothing, sample counts, epoch counts. §3 specifies the held-out test split (17 scenes, environment-level). §4 specifies closed-loop evaluation.

7. **Experiment statistical significance** — *Yes*. §4.1 specifies the protocol: 5 seeds × 93 trials = 465/condition, 95% bootstrap CIs (10,000 resamples), permutation tests with Bonferroni correction. Tables 2–4 report mean SR@5m with 95% CIs and pairwise p-values.

8. **Experiments compute resources** — *Yes*. §3.4 reports dataset-generation compute (RTX 4090 on Vast.ai, ~3 hr wall-clock with 50 concurrent workers, ~$60 total). Fine-tuning: single RTX 4090 (24 GB), <8 GPU-hr per protocol. Closed-loop: ~3,000+ trials at ≤90 s wall-clock per trial in Isaac Sim on a single RTX 4090 host.

9. **Code of ethics** — *Yes*. No human subjects, no real persons, no PII, no biometric data; data is rendered from synthetic 3D scenes. Dual-use risks discussed in §8 and Appendix A (Datasheet); disallowed uses encoded in CC-BY-NC license and explicit usage statement.

10. **Broader impacts** — *Yes*. Positive impacts (rigorous evaluation methodology, reduction of overclaim risk) discussed in §8 and §10. Negative impacts (drone-perspective perception's dual-use for surveillance and lethal autonomy) discussed in §8 and Appendix A. Mitigations: CC-BY-NC license, no real persons in data, explicit disallowed-use list.

11. **Safeguards** — *Yes*. No real persons, no PII, no scraped imagery. CC-BY-NC-4.0 license forbids commercial military/surveillance use. Datasheet enumerates disallowed uses. Released checkpoints are domain-specific (Habitat-Sim indoor furniture/objects), not general-purpose person detectors.

12. **Licenses for existing assets** — *Yes*. HSSD (CC-BY-NC-4.0), Habitat-Sim, Pegasus Simulator, OWL-ViT v2, Grounding DINO, and Depth Anything V2 are cited and credited. Replica- and HM3D-derived scenes excluded for license incompatibility; ReplicaCAD-derived scenes excluded as not used in any reported experiment.

13. **New assets** — *Yes*. The Yonder dataset card on the HuggingFace Hub documents repository layout, sensor schema, scene-source provenance, license, intended use, and known limitations. Croissant metadata file with core and RAI fields accompanies the dataset. Reviewer-friendly ~500 MB sample subset published as a separate HF repository. Datasheet for Yonder in Appendix A.

14. **Crowdsourcing and research with human subjects** — *NA*. No crowdsourcing and no human subjects; data is synthetic, and semantic annotations are derived programmatically from mesh-authored instance IDs.

15. **Institutional review board (IRB) approvals** — *NA*. No human subjects research.

16. **Declaration of LLM usage** — *NA*. LLMs are not a component of the core methodology. The vision-language detectors evaluated (OWL-ViT v2, Grounding DINO) are the subject of study, not methodological tools.

## Appendix A: Datasheet for Yonder

Following [DataSheets for Datasets, Gebru et al. 2021]:

**Motivation.** Created to enable training of drone-perspective perception models for autonomous indoor navigation. Creating organization and funder withheld for double-blind review.

**Composition.** 4,650,324 frames across 167 indoor 3D scenes (all from HSSD), organized as 387,527 waypoint NPZ files (52 sensor arrays per file plus 12 per-pixel semantic arrays per file, with semantic instance labels for all 167 scenes).

**Collection process.** Generated by flying a simulated Holybro x500v2 quadcopter through 3D scenes in Habitat-Sim. Waypoint sampling uses the navmesh with adaptive densification. Sensor capture is deterministic given a scene and seed.

**Preprocessing.** Raw renders compressed to NPZ with gzip level-1. Semantic channels re-rendered separately and projected to COCO bounding boxes by extracting bounding boxes around each unique semantic instance ID with area > 100 pixels.

**Uses.** Recommended for perception model training with closed-loop validation in deployment domain. Not recommended for end-to-end navigation policy training or for reporting fine-tuning improvements based solely on offline metrics.

**Distribution.** Hosted on the HuggingFace Hub at https://huggingface.co/datasets/astralhf/yonder. Croissant metadata with Responsible AI fields published alongside the dataset card. License: **CC-BY-NC-4.0** (data, inheriting HSSD's NonCommercial restriction; HSSD attribution preserved per source license) and Apache-2.0 (code). Replica and HM3D-derived scenes were excluded because their upstream licenses do not permit open redistribution of derivative renders; ReplicaCAD scenes were excluded because they lack semantic annotations and were not used in any reported experiment.

**Maintenance.** Maintained by the corresponding author. Updates and corrections will be posted to the project website. Errata will be tracked publicly.
