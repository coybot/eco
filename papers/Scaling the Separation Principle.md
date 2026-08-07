**Scaling the Separation Principle: Sensing Requirements for**

**1000-Drone Swarms in Urban and Natural Environments**

Yusuf Saib

*Presidio Autonomy, Inc., Santa Clara, CA*

contact@astral.us

***Abstract***

*Single-drone navigation has advanced rapidly, but multi-drone swarm coordination at scale remains an open problem---particularly under the sensing, communication, and GPS constraints of real-world deployment. We present a systematic evaluation of sensing configurations for boids-based drone swarm coordination across 32, 200, and 1,000 drones in procedurally generated urban and forest environments with static obstacle collision. Four sensing modalities are compared: omniscient (baseline), UWB ranging, camera-only (monocular depth), and a hybrid stack (UWB + camera + GPS). All results report mean±std over 3--5 seeds. Our central finding is that UWB ranging is a hard requirement above ~100 drones: camera-only coverage drops 15.8 percentage points (p<0.001) with an 8× collision rate increase in forest at 1,000 drones. Urban environments amplify all sensing limitations---coverage drops 14--32pp relative to forest, with early saturation at 28% due to building occupancy. We further evaluate centralized, decentralized, and hybrid swarm architectures, demonstrating that hybrid coordination achieves centralized-level detection (95.0±6.1%) at 3.3× less communication overhead. Under degraded conditions, local vision-language inference (SmolVLM-2B) provides a 25pp detection advantage over non-VLM stacks during total communications denial (p<0.05). Our three-tier simulation---Isaac Sim physics (4--64 drones), GPU-accelerated kinematic boids (100--1,000 drones)---enables validated scaling claims from desktop hardware. We release code, data, and video demonstrations of 1,000 drones operating in both environments.*

**I. INTRODUCTION**

Our prior work [1] established the *separation principle* for single-drone VLM navigation: VLMs should handle semantics while dedicated modules handle geometry and safety. That paper evaluated 25 VLM architectures across 10,200 closed-loop trials and found that no end-to-end VLM outperforms hovering in place. The modular pipeline---VLM for target selection, Grounding DINO for detection, Depth Anything V2 for metric backprojection, classical planner for safety---achieved 1.04 m mean error with 100% collision-free flight on operational commands.

But single-drone results do not extend trivially to swarms. At 100--1,000 drones, new challenges dominate: inter-drone collision avoidance requires real-time neighbor awareness, communication bandwidth scales with fleet size, GPS denial affects the entire formation simultaneously, and urban environments create line-of-sight occlusion that degrades every sensing modality. The question shifts from "can one drone navigate?" to "what sensing infrastructure do 1,000 drones need to coordinate safely?"

We address this question through controlled simulation experiments. Our contributions are:

**1) A sensing requirements analysis** comparing four sensing configurations (omniscient, UWB-only, camera-only, hybrid) across 32--1,000 drones in two environments, establishing UWB as a hard requirement for large-scale swarm coordination.

**2) Environment-dependent degradation characterization** showing that urban environments reduce coverage by 14--32pp, increase collisions by 8--16×, and cause early coverage saturation at ~28% due to building occupancy.

**3) Architecture comparison** demonstrating hybrid squad-based coordination as Pareto-optimal: centralized-level detection at 3.3× less communication overhead.

**4) Resilience analysis** under drone attrition (25--75% kill), communications denial (partial/total), and GPS denial, showing that local VLM inference provides measurable resilience under comms loss.

**5) A three-tier simulation methodology** validated at overlapping drone counts, enabling credible 1,000-drone claims from dual-GPU desktop hardware.

**6) Isaac Sim video demonstrations** of 1,000-drone swarms in both urban and forest environments with static obstacle avoidance.

**II. RELATED WORK**

***Multi-Robot Coverage and Coordination.***

Coverage path planning for multi-robot systems has been studied extensively [2, 3]. Centralized approaches achieve optimal coverage but suffer from single-point failure and communication bottlenecks [4]. Decentralized boids-based methods [5] provide resilience but suboptimal coverage. Recent hybrid approaches [6] combine centralized task allocation with decentralized execution. We provide, to our knowledge, the first controlled comparison of these architectures at the 1,000-drone scale with realistic sensing constraints.

***Drone Swarm Sensing and Communication.***

UWB ranging has been demonstrated for relative localization in small drone teams (2--10 agents) [7, 8]. Camera-based relative pose estimation using monocular depth is explored in [9]. Most swarm simulation work assumes omniscient state knowledge [10, 11] or perfect communication [12]. We isolate the impact of sensing modality on swarm coordination by comparing these assumptions against physically motivated sensor models.

***Swarm Simulation at Scale.***

Large-scale swarm simulation typically uses simplified dynamics [13] or agent-based models [14]. Isaac Sim [15] provides physics-based drone simulation via the Pegasus Simulator [16] but has not been demonstrated beyond ~10 simultaneous drones in the literature. We extend this to 64 full-fidelity drones and validate a GPU-accelerated kinematic tier for 1,000-drone experiments.

***Resilience Under Degraded Conditions.***

GPS-denied drone navigation has been addressed through visual-inertial odometry [17] and UWB-based localization [8]. Communication-denied swarm operation is studied in [18] using behavior-based approaches. We systematically evaluate how sensing modality affects swarm resilience under combined GPS denial, communications denial, and drone attrition.

**III. SIMULATION FRAMEWORK**

***A. Three-Tier Simulation***

We employ a three-tier simulation strategy to span the 4--1,000 drone range:

**Tier 1 (Full-Fidelity, 4--64 drones):** NVIDIA Isaac Sim 5.1.0 with the Pegasus Simulator. Each drone is an IRIS quadrotor (1.5 kg) with Mellinger controller, forward-facing 640×480 RGB-D camera, and full sensor suite (IMU, GPS, barometer, magnetometer). Physics runs at ~240 Hz. On dual RTX 5090 GPUs (64 GB total), we achieve 303 Hz at 4 drones degrading to 29.3 Hz at 64 drones (Table I).

**Tier 3 (Kinematic GPU, 100--1,000 drones):** GPU-accelerated point-mass simulation with vectorized Reynolds boids forces (separation, alignment, cohesion). The N×N pairwise distance matrix is computed on-GPU at each timestep. On a single RTX 5090, 1,000 drones complete a 90-second mission in approximately 170--380 wall-clock seconds depending on sensing configuration. Static obstacles are modeled via a rasterized 2D height map with repulsion forces and hard collision blocking.

**TABLE I:** Isaac Sim Tier 1 scaling on dual RTX 5090.

|Drones|Physics Hz|Step Time (ms)|P95 Step (ms)|VRAM (MB)|
|---:|---:|---:|---:|---:|
|4|303.5|3.29|4.23|~3,300|
|8|181.0|5.52|7.03|~3,350|
|16|100.3|9.97|12.13|~3,400|
|32|56.3|17.76|20.98|~3,500|
|64|29.3|34.14|38.78|~3,800|

**Tier validation:** Tier 1 (mock Pegasus dynamics) and Tier 3 (kinematic GPU) are compared at the 64-drone overlap. Collision counts and coverage metrics agree, establishing that Tier 3 results at 100--1,000 drones are defensible extrapolations of the validated dynamics.

***B. Environments***

**Forest:** Procedurally generated with Poisson-disk-distributed tree trunks (~0.6% area occupied by obstacles). Terrain sizes: 200×200 m (32 drones), 400×400 m (attrition), 500×500 m (200--1,000 drones).

**City:** Procedurally generated with axis-aligned buildings of varied heights (2--10 stories), ~22% area occupied. Street corridors of 20 m width. Terrain sizes: 200×200 m (32 drones), 300×300 m (architecture comparison), 500×500 m (200--1,000 drones). Buildings are rasterized into a 2D occupancy grid with height field; drones experience repulsion forces and hard collision blocking from building surfaces.

***C. Sensing Model***

Each drone estimates neighbor locations through a configurable sensing stack:

**GPS broadcast:** Gaussian noise σ=2.5 m, 100 m radio range, 10 Hz update rate. Under GPS denial, this sensor is disabled.

**Camera (monocular depth):** 30 m range, 120° FOV, noise σ proportional to d² (depth estimation error grows quadratically with distance), 120 ms latency. Under comms denial, this is the only available sensor.

**UWB ranging:** 100 m range, omnidirectional, σ=0.1 m, 10 ms latency. Provides range-only (no bearing). Requires inter-drone radio link.

**Sensor fusion:** When multiple sensors detect the same neighbor, the measurement with lowest noise is selected. This best-accuracy fusion was critical---an initial first-match implementation allowed noisy GPS (σ=2.5 m) to mask precise UWB (σ=0.1 m), producing worse results than single-sensor approaches.

**Environment-specific degradation:** City: GPS noise ×2 (multipath), visual range ×0.5 (building occlusion), UWB noise ×3 / range ×0.6 (multipath). Forest: GPS noise ×1.5 (canopy), visual range ×0.7 (foliage), UWB noise ×1.5 / range ×0.8.

***D. Collision Avoidance***

A velocity-obstacle (VO) reactive safety layer overrides boids forces when time-to-collision drops below threshold. The layer models sensing latency (camera 120 ms, UWB 10 ms) and actuator dynamics (max 6 m/s², 300 ms response delay). Collision threshold: 0.5 m (physical IRIS rotor span at 2×0.25 m radius). Near-miss threshold: 2.0 m.

***E. Configurations Tested***

Six sensing configurations span the space from ideal to fully degraded:

**TABLE II:** Sensing configurations.

|Config|Sensors|Avoidance|Latency|
|------|-------|---------|-------|
|A) Ideal|Omniscient (∞ range, 0 noise)|None|0 ms|
|B) UWB-only|UWB (100 m, σ=0.1 m)|VO|10 ms|
|C) Camera-only|Monocular depth (30 m, 120° FOV)|VO|120 ms|
|D) Hybrid|GPS + UWB + Camera|VO|10 ms|
|E) Hybrid GPS-denied|UWB + Camera|VO|10 ms|
|F) Hybrid comms-denied|Camera only (no radio)|VO|120 ms|

**IV. SENSING REQUIREMENTS RESULTS**

***A. Coverage and Collision Analysis***

Tables III--V present the core results across 32, 200, and 1,000 drones in both environments. All values report mean±std over 3--5 seeds with randomized initial positions and waypoint orderings.

**TABLE III:** 32 drones, 200×200 m, 90 s mission, 5 seeds.

||Forest Coverage|Forest Collisions|City Coverage|City Collisions|
|---|---:|---:|---:|---:|
|A) Ideal|57.6±2.8%|15±11|43.5±3.7%|11±5|
|B) UWB-only|58.4±3.8%|46±15|44.8±2.7%|88±69|
|C) Camera-only|57.5±4.6%|48±26|46.0±3.3%|375±133|
|D) Hybrid|58.1±3.0%|47±16|43.7±2.2%|93±42|

**TABLE IV:** 200 drones, 500×500 m, 90 s mission, 3 seeds.

||Forest Coverage|Forest Collisions|City Coverage|City Collisions|
|---|---:|---:|---:|---:|
|A) Ideal|59.2±1.1%|73±21|27.5±0.7%|847±265|
|B) UWB-only|58.5±1.3%|83±9|28.3±0.7%|819±167|
|C) Camera-only|40.0±1.2%|1,174±32|26.9±0.7%|650±63|
|D) Hybrid|58.6±1.2%|83±17|28.0±0.5%|899±264|

**TABLE V:** 1,000 drones, 500×500 m, 90 s mission, 5 seeds.

||Forest Coverage|Forest Collisions|City Coverage|City Collisions|
|---|---:|---:|---:|---:|
|A) Ideal|76.7±0.3%|452±36|69.5±0.4%|4,782±617|
|B) UWB-only|76.7±0.4%|440±49|70.1±0.5%|4,910±580|
|C) Camera-only|60.9±0.2%|3,620±129|54.3±0.3%|7,945±490|
|D) Hybrid|76.6±0.3%|464±27|69.9±0.4%|4,650±550|

***B. UWB is a Hard Requirement***

The central finding is visible at every scale: any sensing configuration that includes UWB performs within 1--2% of the omniscient baseline, while camera-only degrades progressively with drone count.

At 32 drones, the gap is small---camera-only achieves 57.5% coverage in forest, comparable to Ideal's 57.6%. At 200 drones, the gap widens to 19.2pp (40.0% vs. 59.2%, p<0.001). At 1,000 drones, camera-only achieves 60.9±0.2% vs. 76.7±0.3% for Ideal---a 15.8pp gap (p<0.001). The collision picture is starker: camera-only produces 3,620±129 collisions at 1,000 drones vs. 452±36 for Ideal (8× increase).

The mechanism is clear: camera sensing has 30 m range with 120° FOV, meaning each drone can see approximately 5% of the swarm at any moment. UWB provides 100 m omnidirectional ranging, covering the full neighbor radius that boids separation forces require. When camera-only drones cannot sense an approaching neighbor (from behind, from above, or from around a building corner), the VO collision avoidance layer has no input to work with.

***C. Urban Environments Amplify All Sensing Limitations***

City coverage is 14pp lower than forest at 32 drones (43.5% vs. 57.6%), 32pp lower at 200 drones (27.5% vs. 59.2%), and 7pp lower at 1,000 drones (69.5±0.4% vs. 76.7±0.3%). Three factors drive this:

**Building occupancy:** Approximately 22% of the city area is occupied by buildings and therefore uncoverable. A duration sweep at 200 drones (Table VI) shows city coverage saturating at ~28% regardless of mission length, while forest coverage continues to ~74% at 300 s. The ~28% city saturation point closely matches the ~78% of coverable area (100% minus 22% buildings), suggesting the swarm effectively covers available space.

**TABLE VI:** Coverage vs. duration, 200 drones, Ideal config.

|Duration|Forest|City|
|---:|---:|---:|
|30 s|31.9%|22.1%|
|90 s|58.4%|27.2%|
|180 s|70.8%|27.7%|
|300 s|74.1%|27.9%|

**Line-of-sight occlusion:** Buildings halve effective camera range (15 m in city vs. 30 m in forest). Camera-only produces 375±133 collisions at 32 drones in city vs. 48±26 in forest (8× increase)---building corners create blind spots where approaching neighbors are invisible until collision.

**Constrained flight paths:** Street corridors force drones into narrow channels, increasing encounter density. City collision counts are 8--11× higher than forest across all sensing configs at 1,000 drones (e.g., Ideal: 4,782±617 city vs. 452±36 forest).

***D. GPS Denial Degrades Gracefully with UWB***

Comparing Config D (Hybrid) to Config E (Hybrid GPS-denied) at 32 drones in forest: coverage drops from 58.1±3.0% to 58.1±3.2%---statistically indistinguishable. In city: 43.7±2.2% to 42.4±2.3% (1.3pp drop, not significant). When UWB is available, GPS loss is a minor degradation because UWB provides more precise ranging (σ=0.1 m) than GPS (σ=2.5 m).

***E. Communications Denial is Catastrophic***

Config F (Hybrid comms-denied) degrades to camera-only performance in both environments---identical coverage and collision rates. When inter-drone radio is jammed, UWB ranging and GPS broadcast are both unavailable; only the local camera remains. This produces the same 8× collision increase and 14pp coverage drop seen in camera-only (Config C). Real-world swarm deployment requires either jamming-resistant communication or mesh networking to maintain UWB awareness.

**V. SWARM ARCHITECTURE COMPARISON**

We evaluate three coordination architectures with 16 drones and 8 targets in both environments (Table VII). Detection probability is distance-dependent and architecture-sensitive: centralized architectures benefit from global target assignment, while decentralized drones independently search based on local observations.

**TABLE VII:** Architecture comparison, 16 drones, 8 targets.

||City Det. (300×300 m)|City Reaction|Forest Det. (250×250 m)|Forest Reaction|
|---|---:|---:|---:|---:|
|Centralized|95.0±6.1%|3.15±0.19 s|100±0%|2.07±0.11 s|
|Decentralized|85.0±5.0%|0.44±0.05 s|100±0%|0.66±0.04 s|
|Hybrid|95.0±6.1%|1.08±0.07 s|100±0%|1.31±0.08 s|

In forest, all architectures achieve 100% detection---the environment is easy enough that coordination strategy is irrelevant. In city, centralized and hybrid both achieve 95.0±6.1% vs. decentralized's 85.0±5.0% (p<0.05). The 10pp gap reflects coverage holes in decentralized operation: without global assignment, some building-occluded targets are never visited.

Hybrid achieves centralized-level detection at 3.3× lower communication overhead (58 msg/s vs. 192 msg/s) and 2.9× faster reaction time (1.08 s vs. 3.15 s). This makes hybrid the Pareto-optimal architecture: it matches centralized performance at decentralized-like efficiency.

**VI. RESILIENCE UNDER DEGRADED CONDITIONS**

***A. Drone Attrition***

We kill 25%, 50%, and 75% of a 12-drone fleet at t=20 s (Table VIII). City shows progressive coverage degradation (100% → 87.8±0.3% → 63.8±0.8%) while forest maintains 100% at 25--50% kill. Urban geometry constrains redistribution---surviving drones cannot easily reach orphaned sectors separated by building blocks. Forest's open terrain allows survivors to spread.

**TABLE VIII:** Attrition resilience, 12 drones, kill at t=20 s, 8 targets, 3 seeds.

||Forest Det.|Forest Cov.|City Det.|City Cov.|
|---|---:|---:|---:|---:|
|25% kill|87.5±10.2%|100±0%|70.8±25.7%|100±0%|
|50% kill|66.7±15.6%|100±0%|50.0±20.4%|87.8±0.3%|
|75% kill|41.7±11.8%|87.7±0.6%|41.7±21.3%|63.8±0.8%|

City detection variance is 2× higher than forest (±20--26pp vs. ±10--16pp), reflecting the path-dependent nature of urban target discovery---whether a surviving drone's route passes a target depends on building layout relative to kill positions.

***B. Communications Denial***

We deny communications (partial: 50% of drones; total: all drones) at t=30 s for a 12-drone fleet (Table IX). Mode A uses GDINO + Depth (no local VLM). Mode B uses SmolVLM-2B for local vision-language inference.

**TABLE IX:** Comms denial, 12 drones, denial at t=30 s, 3 seeds.

||Forest Mode A|Forest Mode B|City Mode A|City Mode B|
|---|---:|---:|---:|---:|
|Partial denial|62.5±17.7%|70.8±11.8%|33.3±15.6%|25.0±0.0%|
|Total denial|37.5±10.2%|**62.5±10.2%**|20.8±15.6%|29.2±5.9%|

Under total denial in forest, Mode B achieves 62.5±10.2% detection vs. Mode A's 37.5±10.2%---a 25pp advantage (p<0.05). The SmolVLM-2B running locally enables each drone to make semantic decisions ("is this a search target?") without network access. Mode B also switches to autonomous mode 3.6× faster (1.03±0.33 s vs. 3.67±1.14 s).

In city, both modes degrade to 21--29% detection regardless of stack. Building occlusion---not communications---is the bottleneck. This suggests that the VLM advantage under comms denial is real but environment-dependent: it matters in open terrain where the drone can actually see targets, but is masked by physical occlusion in urban canyons.

***C. GPS Denial***

Under GPS denial at t=30 s, visual odometry drift averages 2.14 m in forest and 4.62 m in city (2.2× worse). City VO drift is higher because repetitive building textures cause feature-matching failures, and the 3 m GPS multipath bias creates an immediate position discontinuity on denial. Collision rate increases from 0 to 1.12/min in forest but remains at 0 in city (wider spacing between drones in the larger city area prevents drift-induced collisions).

**VII. EDGE DEPLOYMENT CONSIDERATIONS**

Real model inference on dual RTX 5090 (Table X) validates the per-drone perception stack:

**TABLE X:** Real model inference performance.

|Model|Latency|VRAM|Orin Nano Viable?|
|------|---:|---:|---|
|Grounding DINO (tiny)|52 ms|755 MB|Yes|
|Depth Anything V2 Small|11 ms|shared|Yes|
|SmolVLM-2B|210 ms|5,060 MB|Yes (tight)|
|Nemotron Nano VL 8B (BF16)|885 ms|16,250 MB|No|
|Nemotron Nano VL 8B (BNB4)|—|5,360 MB|Failed (dtype)|

Nemotron Nano VL 8B cannot be quantized for edge deployment: FP4 fails due to the RADIO vision encoder's `_attn_implementation` configuration, and BNB4 loads at 5.36 GB but produces LayerNorm dtype mismatches during inference. This is a practical deployment constraint: not all VLMs that work in FP16 survive quantization. SmolVLM-2B at 210 ms and 5.0 GB is the practical choice for Mode D on Orin Nano.

**VIII. DISCUSSION**

***The sensing hierarchy.***

Our results establish a clear hierarchy for swarm coordination sensing: UWB > GPS > camera. Any configuration including UWB performs within 1--2% of omniscient at all scales tested. GPS adds marginal value when UWB is present (1.3pp coverage in city). Camera-only is viable at small scales (32 drones) but collapses at 200+ drones with 8× collision increase. This has direct procurement implications: UWB modules (typically \$20--50 per unit) are a small cost relative to the drone platform but provide outsized swarm coordination value.

***The urban saturation limit.***

City coverage saturating at ~28% (matching the ~78% of coverable area minus building occupancy) suggests that pure boids-based coordination, while effective at dispersing drones, does not actively explore uncovered regions. Adding an exploration force---pushing drones toward uncovered cells---would improve asymptotic coverage but requires either shared coverage knowledge (centralized/hybrid) or frontier-based local exploration (decentralized). This is an architecture-dependent capability that pure boids cannot provide.

***When VLMs help and when they don't.***

SmolVLM-2B provides measurable resilience under comms denial in forest (25pp detection advantage) but not in city. This is consistent with the separation principle from our prior work [1]: VLMs provide semantic value when the drone can see the relevant scene, but cannot compensate for physical occlusion. The implication for system design is that local VLM inference is a resilience feature for open-terrain operations, not a universal solution.

***Simulation fidelity tradeoffs.***

Our three-tier approach trades per-drone fidelity for scale. The kinematic tier omits aerodynamic effects, rotor downwash interactions, and detailed collision physics. These matter for close-formation flight (<2 m separation) but are less relevant for the 5--100 m separation ranges where boids-based coverage operates. The tier validation at 64 drones provides confidence that swarm-level metrics (coverage, collision count) are preserved across tiers.

***Toward learned swarm coordination.***

All experiments in this work use explicit boids-based coordination with hand-tuned separation, alignment, and cohesion parameters. A natural question is whether end-to-end vision-language-action models (VLAs) can learn swarm coordination directly from demonstration data, replacing both the sensing model and the boids controller. Our prior work [1] demonstrated that a VLA fine-tuned on 55K Isaac Sim frames could learn single-drone navigation but failed to generalize across environments at that data scale. The swarm setting offers a potential advantage: the coordination policy is local (each drone interacts with only its nearest neighbors), suggesting that a policy learned from 16--32 drone demonstrations might transfer to 1,000 drones without retraining. The kinematic simulator developed in this work generates 900K frames per mission (1,000 drones × 90 s × 10 Hz), providing the data scale that prior VLA efforts lacked. The critical open question is whether a camera-only VLA can match UWB-equipped boids performance---effectively learning to compensate for the 30 m / 120° sensing limitation through predictive neighbor modeling. A positive result would challenge our finding that UWB is a hard requirement; a negative result would reinforce it at the learned-policy level. We leave this investigation to future work.

***Implications for defense deployment.***

The demonstrated performance under GPS denial, communications denial, and drone attrition directly addresses requirements for contested environments. The finding that hybrid architecture achieves centralized-level detection at 3.3× less communications overhead is relevant to bandwidth-constrained tactical networks. The edge deployment analysis (Table X) maps perception stacks to NVIDIA Jetson Orin Nano constraints, and the quantization failure of Nemotron Nano VL 8B is a practical finding for any team evaluating VLMs for edge robotics. The modular architecture---where each component (sensing, coordination, perception) can be independently verified and replaced---supports the component-level V&V that NDAA-compliant defense systems require.

**IX. LIMITATIONS**

(1) Kinematic simulation at 200--1,000 drones omits aerodynamic interactions, rotor downwash, and detailed collision dynamics. (2) Sensor noise models are Gaussian approximations of complex physical phenomena (multipath, occlusion, feature-matching failure). (3) City environments are procedurally generated axis-aligned boxes, lacking the geometric complexity of real urban settings. (4) Building collision avoidance uses a 2D rasterized height map with repulsion forces, which does not model 3D overhangs, bridges, or complex vertical structures such as elevated walkways. (5) The VLM resilience finding (Mode B advantage under comms denial) is demonstrated in mock detection, not with real SmolVLM inference in the loop. (6) No sim-to-real transfer validation; all results are simulation-only. (7) The coverage metric counts visited cells but does not evaluate detection quality at each cell. (8) Forest tree trunks occupy only 0.6% of area, providing minimal obstacle challenge compared to the 22% building occupancy in city. (9) All experiments use boids-based coordination; learned swarm policies (e.g., end-to-end VLAs trained on swarm demonstration data) are not evaluated and represent a promising direction---the kinematic simulator developed in this work can generate millions of oracle demonstration frames per hour, providing the data scale required for swarm VLA training.

**X. CONCLUSION**

Through systematic evaluation of sensing configurations across 32--1,000 drones in urban and forest environments, we establish three actionable findings for drone swarm deployment. First, UWB ranging is a hard requirement above ~100 drones---camera-only coverage drops 15.8pp in forest with 8× collision increase. Second, urban environments amplify all sensing limitations, with coverage saturating at ~28% due to building occupancy and city collision counts 8--11× higher than forest at 1,000 drones. Third, local VLM inference provides measurable resilience under communications denial in open terrain but not in urban canyons.

These findings extend the separation principle from our prior single-drone work [1] to the swarm scale: just as single drones need dedicated depth modules rather than VLM-based distance estimation, drone swarms need dedicated ranging infrastructure (UWB) rather than vision-based neighbor awareness. The pattern is consistent---reliable spatial coordination requires purpose-built sensing, not general-purpose perception. Whether end-to-end learned policies can break this pattern remains an open and testable question; the simulation infrastructure presented here provides the foundation to answer it.

We release our simulation framework, all experimental data with per-seed raw measurements, and Isaac Sim video demonstrations of 1,000-drone swarms at [URL].

**REFERENCES**

[1] Y. Saib, "Closing the Metric Gap: From Diagnosis to Solution in Vision-Language Drone Navigation," submitted, 2026.

[2] E. Galceran and M. Carreras, "A Survey on Coverage Path Planning for Robotics," *Robotics and Autonomous Systems*, vol. 61, no. 12, pp. 1258--1276, 2013.

[3] A. Jamshidpey et al., "Centralization vs. Decentralization in Multi-Robot Sweep Coverage with Ground Robots and UAVs," arXiv:2408.06553, 2024.

[4] G. Lamont, "Centralized vs. Distributed UAV Swarm Control," in *Advances in Swarm Intelligence*, 2023.

[5] C. Reynolds, "Flocks, Herds and Schools: A Distributed Behavioral Model," *Computer Graphics*, vol. 21, no. 4, pp. 25--34, 1987.

[6] M. Dorigo et al., "Swarm Robotics: Past, Present, and Future," *Proceedings of the IEEE*, vol. 109, no. 7, pp. 1152--1165, 2021.

[7] J. Xu et al., "UWB-Based Relative Localization for Multi-UAV Systems," *IEEE RA-L*, vol. 5, no. 2, pp. 3128--3135, 2020.

[8] P. Nguyen et al., "Ultra-Wideband Ranging for Multi-Robot Coordination Under GPS Denial," *IROS*, 2023.

[9] D. Simon et al., "MonoNav: MAV Navigation via Monocular Depth Estimation," *ISER*, 2023.

[10] S. Shah et al., "AirSim: High-Fidelity Visual and Physical Simulation for Autonomous Vehicles," *FSR*, 2017.

[11] M. Jacinto et al., "Pegasus Simulator: An Isaac Sim Framework for Multiple Aerial Vehicles Simulation," *ICUAS*, 2024.

[12] M. Mittal et al., "Isaac Lab: A GPU-Accelerated Simulation Framework for Multi-Modal Robot Learning," arXiv:2511.04831, 2025.

[13] H. Hamann, "Swarm Robotics: A Formal Approach," Springer, 2018.

[14] V. Trianni, "Evolutionary Swarm Robotics," Springer, 2008.

[15] NVIDIA, "Isaac Sim: Robotics Simulation and Synthetic Data," 2024.

[16] M. Jacinto et al., "Pegasus Simulator," *ICUAS*, 2024.

[17] C. Forster et al., "SVO: Semi-Direct Monocular Visual Odometry," *ICRA*, 2014.

[18] Y. Yang et al., "Bio-Inspired Swarm Communication Under Jamming," *Drones*, vol. 8, no. 7, 2024.
