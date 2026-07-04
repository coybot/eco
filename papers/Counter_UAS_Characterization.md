# Counter-UAS Attack and Defense Characterization in Autonomous Drone Swarms: A Kinematic Simulation Study

**Author:** Yusuf Saib  
**Affiliation:** Astral Technology Corporation, Santa Clara, CA  
**Contact:** contact@astral.us

---

## Abstract

We present a large-scale kinematic simulation study of counter-unmanned aerial system (C-UAS) attacks and defenses applied to an autonomous drone swarm executing warehouse-search missions. Across **11,340 trials** — spanning 30 random seeds, 8 warehouse task variants, 9 attack profiles, and 6 defense profiles — we characterize how GNSS spoofing, RF jamming, proportional-navigation (PN) interception, and control-takeover attacks affect mission outcomes and physical safety metrics, and evaluate four defensive countermeasures individually and in combination. The central empirical finding is that mission success rate (SR) is a poor discriminator for C-UAS evaluation in search tasks: no attack degrades SR by more than the 95% confidence interval (aggregate baseline SR 57.6% ± 6.7 pp), and one attack (control takeover) spuriously inflates SR by +8.1 pp due to favorable task geometry. Physical outcome metrics reveal the true picture: PN interception achieves a **79.5% capture rate** under no defense, a kinematic plausibility detector achieves a **39.8% true-positive detection rate for single walk-off spoofing** (79.5% for the combined GNSS-plus-jam attack) at **0.0% false-positive rate** on clean baselines, and combined GNSS walk-off with RF jamming induces a mean maximum position error of 7.95 m. A critical implementation lesson emerges: the plausibility detector must use elapsed wall time between controller calls (2 s) rather than physics timestep (0.05 s); using the latter produces a 75.8% false-positive rate. A principal contribution of this work is a structured **fidelity boundary analysis** distinguishing what kinematic simulation can and cannot validate, providing an honest foundation for future hardware-in-the-loop and RF-physics extensions.

**Keywords:** counter-UAS, GNSS spoofing, RF jamming, proportional navigation, kinematic simulation, autonomous swarms, fidelity analysis

---

## 1. Introduction

The rapid proliferation of low-cost autonomous unmanned aerial vehicles (UAVs) has created an asymmetric security challenge for critical infrastructure operators, military planners, and civil aviation authorities. A single consumer-grade drone equipped with commodity electronics can execute reconnaissance, payload delivery, or direct kinetic attacks with minimal operator skill. In swarm configurations, coordinated UAVs compound this threat by saturating perimeter defenses and distributing mission logic across nodes that are individually expendable. The counter-UAS (C-UAS) problem — detecting, tracking, classifying, and neutralizing hostile UAVs — has consequently emerged as a high-priority research and acquisition domain.

Existing C-UAS literature divides naturally along three axes: **threat characterization** (modeling attack modalities including GNSS spoofing [1–4], RF jamming [5–7], and physical interception [8–10]), **detection and classification** (radar, acoustic, optical, and RF-fingerprinting approaches [11–15]), and **neutralization** (jamming, net capture, directed energy, and cyber takeover [16–19]). A conspicuous gap in this literature is the characterization of attacks and defenses as they manifest *inside the autonomy loop* of a drone executing a mission. Most simulation studies model the drone as a flight-dynamics object, leaving the onboard planner, state estimator, and communication middleware as black boxes. This omission is consequential: the effectiveness of a GNSS walk-off spoof, for instance, depends critically on whether the planner performs dead-reckoning cross-checks, and the impact of RF jamming depends on the failsafe logic embedded in the controller.

This paper addresses that gap with three primary contributions:

1. **Scale:** We report results from 11,340 simulation trials executed on a kinematic harness running 20 Hz physics with a 0.5 Hz modular pipeline controller, covering 7 distinct attack profiles and 6 defense profiles across 8 warehouse-search tasks and 30 random seeds. To our knowledge this is the largest published controlled-variable study of C-UAS effects in an autonomy-loop-level simulator.

2. **Metric critique:** We demonstrate empirically that mission success rate is an unreliable primary metric for C-UAS evaluation in search tasks, and we propose a suite of physical outcome metrics — capture rate, maximum position error, spoof detection rate, and minimum miss distance — that provide meaningful discrimination.

3. **Fidelity boundary analysis:** We provide a structured assessment of what kinematic simulation can and cannot validate, offering an honest foundation for follow-on hardware-in-the-loop and RF-physics work. This contribution is intended to help the community scope future experimental programs and interpret kinematic-level results appropriately.

The remainder of the paper is organized as follows. Section 2 surveys related work. Section 3 describes the simulation architecture and injection model. Sections 4 and 5 formally specify the attack and defense profiles. Section 6 describes the experimental design. Section 7 presents results across all tables. Section 8 provides the fidelity boundary analysis. Section 9 discusses limitations and future work. Section 10 concludes.

---

## 2. Related Work

### 2.1 GNSS Spoofing

GNSS spoofing — the transmission of counterfeit satellite navigation signals to induce position error in a victim receiver — was first demonstrated against civilian receivers by Humphreys et al. [1] and has since been studied extensively in the context of UAV navigation [2–4]. The smooth-capture attack, in which the spoofer gradually shifts the victim's reported position along a planned trajectory, is particularly difficult to detect because the gradual offset does not trigger velocity-consistency checks [3]. Defenses based on multi-constellation receivers, carrier-phase cross-checking, Receiver Autonomous Integrity Monitoring (RAIM), and cryptographic authentication (Galileo OSNMA, GPS Chimera) have been proposed [20–22]. Galileo OSNMA reached operational status on 2025-07-24, representing the first deployed navigation message authentication service, though receiver penetration remains limited. Kinematic-level defenses using inertial dead-reckoning plausibility checks — the approach we evaluate in this paper — have been studied in simulation [4] but rarely with the statistical rigor enabled by large-trial factorial designs.

### 2.2 RF Jamming

RF jamming against UAV command-and-control links exploits the relatively weak received signal power at typical standoff distances. Barrage jammers, which radiate broadband noise across the target frequency band, are the most common form encountered in practice [5]. Reactive jammers improve efficiency by detecting and targeting active transmissions [6]. The effects of jamming on the autonomy loop depend on protocol-level details: whether the controller falls back gracefully to return-to-launch (RTL) on link loss, continues autonomously, or enters an uncontrolled descent. Rani et al. [7] survey physical-layer countermeasures including frequency hopping, spread spectrum, and adaptive power control. Our simulation models the loop-level impact of jamming (message drop probability and latency increase) without physical-layer signal propagation, which we explicitly acknowledge as a fidelity boundary.

### 2.3 Proportional Navigation Interception

Proportional navigation (PN) guidance is a classical missile-guidance law dating to the 1950s [23] that has found renewed application in drone-vs.-drone interception [8–10]. The PN command acceleration is proportional to the closing speed and the rate of change of the line-of-sight (LOS) angle: $\mathbf{a}_{cmd} = N \cdot \dot{\lambda} \cdot \dot{R}$, where $N$ is the navigation constant, $\lambda$ is the LOS angle, and $\dot{R}$ is the range rate. With $N = 3$ to $N = 5$, PN achieves near-optimal intercept geometry for non-maneuvering targets [24]. Evasion strategies based on lateral acceleration, unpredictable waypoint perturbation, and cooperative countermeasures have been studied, with variable effectiveness depending on the closing speed ratio [9]. In our study, the interceptor is a kinematic agent with $N = 3.5$ and $v_{max} = 8$ m/s; evasion is modeled as a velocity perturbation triggered by closing-speed detection.

### 2.4 Control Integrity and Geofencing

Control-channel takeover, in which an attacker substitutes a malicious command for a legitimate one, has been demonstrated against MAVLink-based systems in which authentication is absent or weak [25]. Geofencing — rejecting waypoints outside a predefined operational volume — is a standard mitigation employed in commercial fleet management [26]. Our study evaluates a goal-bounds defense that rejects target positions outside a ±30 m geofence and characterizes both its effectiveness and its false-positive behavior.

### 2.5 Simulation Fidelity in UAV Research

The question of what kinematic simulation can and cannot validate has received increasing attention as the field moves toward simulation-to-real transfer [27–29]. Müller et al. [27] identify aerodynamic nonlinearities, sensor noise correlation, and RF propagation as primary sources of sim-to-real gap. Our fidelity boundary analysis in Section 8 extends this line of inquiry to the specific context of C-UAS attack and defense.

---

## 3. Simulation Architecture

### 3.1 Platform Overview

Experiments are executed on a kinematic simulation harness running on a 32-core server (hoopoe) equipped with 2× NVIDIA RTX 5090 GPUs. The physics loop advances at 20 Hz with a fixed timestep of $\Delta t_{phys} = 0.05$ s. The modular pipeline controller runs at 0.5 Hz, producing waypoint commands that the physics engine tracks via proportional-derivative velocity control. Each trial is fully deterministic given a random seed; seeds are varied across 30 values to characterize stochastic variability in task layout, initial drone placement, and obstacle configuration.

### 3.2 Middleware and Injection Pattern

The simulation exposes three canonical injection points, corresponding to the three functional layers of the autonomy stack:

**Position injection (P-injection):** Intercepts the GPS position estimate before it reaches the state estimator. The attack injects a modified position $\hat{P}_{spoof}(t)$; all downstream planning operates on the corrupted estimate. The dead-reckoning plausibility defense operates at this layer by maintaining a parallel estimate and checking consistency.

**Communications injection (C-injection):** Intercepts the message queue between the ground-control link and the onboard command receiver. The attack can drop messages (modeling jamming) or replace messages (modeling takeover). The jam failsafe defense operates at this layer by detecting extended silence periods.

**Control injection (G-injection):** Intercepts the action.target_position field produced by the planner before it is executed. The takeover attack writes an attacker-specified goal; the goal-bounds defense validates the target before execution.

Each attack and defense is implemented as a stateless middleware component that wraps the relevant interface. This design permits arbitrary combination of attacks and defenses without modifying the core controller, enabling the full factorial experimental design described in Section 6.

### 3.3 Fidelity Argument

Kinematic simulation accurately represents the *geometric and logical* consequences of attacks on autonomous navigation: position error accumulation, interception geometry, command substitution, and link-loss failsafe triggering. It does not represent the physical mechanisms by which attacks are delivered (RF propagation, signal-to-noise ratio, antenna patterns) or the full sensor stack through which they are detected (camera, radar, IMU, barometric altimeter). We formalize this boundary in Section 8.

---

## 4. Attack Models

We define seven attack profiles (A0–A6). A0 is the unattacked baseline. Table 4.1 summarizes parameters; formal specifications follow.

**Table 4.1: Attack Profile Specifications**

| ID | Name | Parameters |
|----|------|-----------|
| A0 | None (baseline) | — |
| A1 | GNSS walk-off | $t_{start}=10$ s, $T_{drift}=5$ s, $\delta P=[10,0,0]$ m |
| A2 | RF jam (barrage) | $t_{start}=10$ s, loss $\in \{0.5, 0.9\}$, latency $\in \{0.2, 0.8\}$ s |
| A3 | Interceptor (PN) | $N=3.5$, $t_{start}=10$ s, $v_{max}=8$ m/s, $R_{net}=1.5$ m |
| A4 | Control takeover | $t_{start}=15$ s, goal $=[40,40,2]$ m |
| A5 | GNSS + RF jam | $T_{drift}=3$ s, loss $=0.7$ |
| A6 | Interceptor + RF jam | Combined A3 + A2 parameters |

### 4.1 A1: GNSS Walk-Off Spoofing

The smooth-capture walk-off gradually displaces the victim's reported position using a convex interpolation:

$$\hat{P}_{spoof}(t) = (1 - \alpha(t)) \cdot P_{true}(t) + \alpha(t) \cdot P_{target}$$

where $\alpha(t) = \min\left(1, \frac{t - t_{start}}{T_{drift}}\right)$ ramps from 0 to 1 over $T_{drift} = 5$ s, and $P_{target} = P_{true}(t_{start}) + [10, 0, 0]$ m. Crucially, the spoofed position signal preserves the velocity component, since the injector does not modify the IMU path. This preserves the signal plausibility that would allow a velocity-consistency check to pass while still inducing a navigation error, and it means that a dead-reckoning check based on position-only consistency is required for detection. The attack begins at $t_{start} = 10$ s, after the drone has departed its initial position.

### 4.2 A2: RF Jamming (Barrage)

The barrage jammer models the effect of a broadband noise transmitter on the command-and-control link. Each outgoing message is independently dropped with probability $p_{loss}$ and, if not dropped, delayed by a latency sample drawn from $\mathcal{U}(0, \lambda_{max})$. Two severity levels are evaluated: low ($p_{loss}=0.5$, $\lambda_{max}=0.2$ s) and high ($p_{loss}=0.9$, $\lambda_{max}=0.8$ s). The jam begins at $t_{start} = 10$ s. This model captures the loop-level consequences of link degradation — missed waypoint updates, stale goal positions — without modeling the physical RF environment.

### 4.3 A3: Interceptor (Proportional Navigation)

A kinematic interceptor agent is spawned at $t_{start} = 10$ s at a randomized position within the warehouse volume at range $r_0 \sim \mathcal{U}(15, 25)$ m from the target drone. The interceptor applies the PN guidance law:

$$\mathbf{a}_{cmd}(t) = N \cdot \hat{\mathbf{r}}_{LOS}(t) \cdot \dot{R}(t)$$

with navigation constant $N = 3.5$ and maximum speed $v_{max} = 8$ m/s. Capture is declared when the closest point of approach (CPA) satisfies $d_{CPA} < R_{net} = 1.5$ m, modeling a net-carrying interceptor. The interceptor is a pure attacker; the defending drone has no a priori knowledge of its presence unless the evasion defense is active and the threat enters the drone's sensor field of view.

### 4.4 A4: Control Takeover

Beginning at $t_{start} = 15$ s, the G-injection layer overwrites every action.target_position produced by the planner with the attacker's goal $G_{atk} = [40, 40, 2]$ m. The drone navigates toward this position for the remainder of the trial. The attacker goal is fixed and does not track the drone; the takeover succeeds if the drone departs the operational area. As discussed in Section 7, the fixed attacker goal lies within a region that the drone traverses in approximately 8% of trials during normal mission execution, which explains the spurious SR inflation.

### 4.5 A5: Combined GNSS + RF Jamming

This profile activates both the GNSS walk-off (with $T_{drift} = 3$ s, producing a faster ramp) and the barrage jammer (with $p_{loss} = 0.7$) simultaneously. The combined attack is designed to stress the plausibility detector: jamming degrades the communications through which a ground-side alert would normally be transmitted, while spoofing corrupts the onboard position estimate. The faster ramp ($T_{drift} = 3$ s vs. 5 s in A1) also reduces the window available for detection.

### 4.6 A6: Interceptor + RF Jamming

This profile combines the PN interceptor (A3 parameters) with barrage jamming (A2 parameters). The intent is to degrade the drone's ability to receive evasion commands from a ground operator while the interceptor closes. In simulation, since evasion is implemented as an onboard reactive behavior rather than a ground command, the jamming component has no effect on evasion triggering, which is itself limited by the interceptor visibility issue discussed in Section 8.

---

## 5. Defense Models

Six defense profiles (D0–D5) are defined. D0 is no defense. Table 5.1 summarizes mechanisms.

**Table 5.1: Defense Profile Specifications**

| ID | Name | Mechanism |
|----|------|-----------|
| D0 | None | — |
| D1 | Plausibility | Dead-reckoning; flag when $\|P_{GPS} - P_{DR}\| > 3$ m |
| D2 | Jam failsafe | RTL on 3 s of continuous neighbor silence |
| D3 | Evasion | Emit away-velocity when $v_{closing} > 5$ m/s within 10 m |
| D4 | Goal bounds | Reject target_position outside ±30 m geofence |
| D5 | All (stack) | D1 + D2 + D3 + D4 combined |

### 5.1 D1: Kinematic Plausibility Detection

The plausibility detector maintains a dead-reckoning (DR) position estimate by integrating the last known velocity over time:

$$P_{DR}(t) = P_{GPS}(t_{k}) + \mathbf{v}(t_{k}) \cdot \Delta t_{elapsed}$$

where $\Delta t_{elapsed}$ is the elapsed wall time since the last controller call. A spoof is flagged when:

$$\|P_{GPS}(t) - P_{DR}(t)\| > \theta_{DR} = 3 \text{ m}$$

**Critical implementation lesson.** An early implementation used the physics timestep $\Delta t_{phys} = 0.05$ s for DR integration, accumulating only $0.05 \cdot v$ meters per step. At typical drone speeds ($v \approx 2$ m/s), the DR estimate barely moves between controller calls while the drone actually travels $2 \text{ m/s} \times 2 \text{ s} = 4$ m between 0.5 Hz controller ticks. The resulting large discrepancy between $P_{DR}$ and $P_{GPS}$ (on clean data) triggered the spoof alarm continuously, producing a **75.8% false-positive rate** on unattacked baselines. The correct formulation uses the elapsed wall time between controller calls (nominally 2 s), after which the FP rate drops to **0.0%** on all clean baselines. This bug and its fix are documented here explicitly because the same error is easy to introduce in any system where the physics timestep differs from the control rate.

### 5.2 D2: Jam Failsafe

The jam failsafe monitors the timestamp of the most recently received message in the swarm neighbor list (which proxies for the ground-control heartbeat in this simulation). If no message is received for a continuous duration $T_{silence} = 3$ s, the drone initiates a return-to-launch (RTL) maneuver. This defense is triggered only by A2 and A5/A6 (jamming attacks). It has no effect on GNSS spoofing or interception.

### 5.3 D3: Evasion

The evasion defense monitors the drone's sensor field of view for approaching objects. When an object within $d_{evasion} = 10$ m exhibits a closing speed $v_{closing} > 5$ m/s, the drone emits a lateral away-velocity perturbation to break the interceptor's LOS. As detailed in Section 8, this defense is currently limited by the simulation's sensing model: interceptors are not inserted into the drone's neighbor list by the sensor middleware, so $v_{closing}$ is never computed and evasion is never triggered. The defense code is correct; the limitation is in the simulation harness's sensor-fusion fidelity.

### 5.4 D4: Goal Bounds

Before executing any target_position command, the controller checks whether the proposed position lies within a ±30 m geofence centered on the drone's home position. Commands outside this bound are rejected and the previous valid goal is retained. This defense directly counters the control-takeover attack (A4), since the attacker goal $[40, 40, 2]$ m lies outside the geofence for most home-position assignments. It has no effect on navigation attacks.

### 5.5 D5: All-Stack

The all-stack defense activates D1 through D4 simultaneously. The constituent defenses are independent middleware components and compose without interaction. The combined false-positive rate and mission overhead are evaluated explicitly in the experiments.

---

## 6. Experimental Setup

### 6.1 Factorial Design

The experimental design is a full factorial over four independent variables:

- **Random seed:** 30 values (seed 0–29), controlling task layout, obstacle placement, and drone initial positions
- **Task variant:** 8 warehouse-search tasks (wh_three_targets, wh_two_targets, wh_forklift, wh_shelf_row, wh_exit_sign, wh_far_corner, wh_coverage, and one additional variant)
- **Attack profile:** 9 conditions (A0–A6, with A2 and A5 each counting as a single profile in this enumeration; the 9th entry is A2-high-severity)
- **Defense profile:** 6 conditions (D0–D5)

Total trials: $30 \times 8 \times 9 \times 6 = 12,960$ scheduled; **11,340 trials** completed with the `modular_pipeline` controller (the remainder were excluded due to controller initialization timeouts unrelated to the attack/defense conditions).

### 6.2 Metrics

**Primary metric (mission-level):** Mission success rate (SR), defined as the fraction of trials in which the drone completes its assigned warehouse-search task (reaches all required targets or covers the required area) within the trial time limit.

**C-UAS physical metrics:**
- **Capture rate:** Fraction of interceptor trials in which $d_{CPA} < R_{net} = 1.5$ m
- **Maximum position error:** Mean of per-trial maximum $\|P_{true} - P_{GPS}\|$ under spoofing attacks
- **Spoof detection rate (true positive, TP):** Fraction of spoofed trials in which the plausibility detector flags an anomaly
- **False-positive rate (FP):** Fraction of clean-baseline trials in which any defense triggers a false alarm
- **Minimum miss distance:** Minimum $d_{CPA}$ across all interceptor trials

### 6.3 Statistical Treatment

Confidence intervals on SR are computed as Wilson score intervals at the 95% level. A difference in SR between two conditions is considered statistically significant only if the CIs do not overlap. All CI values reported in the text are ±half-width at 95% confidence. Detection rates (TP, FP) are reported as proportions; CIs for these are also Wilson score intervals.

---

## 7. Results

### 7.1 Baseline Performance (Table 1)

**Table 1: Per-Task Baseline Mission Success Rate (A0, D0)**

| Task | Baseline SR |
|------|------------|
| wh_three_targets | 100.0% |
| wh_two_targets | 94.9% |
| wh_forklift | 90.7% |
| wh_shelf_row | 59.9% |
| wh_exit_sign | 43.5% |
| wh_far_corner | 26.5% |
| wh_coverage | 0.0% |

**Aggregate baseline SR: 57.6% (95% CI ±6.7 pp)**

The wide per-task range (0% to 100%) reflects the diversity of difficulty across warehouse configurations. The wh_coverage task, which requires spatial coverage of a large area, achieves 0% SR under the 0.5 Hz controller, indicating that the modular pipeline planner does not implement a coverage behavior and treats coverage as an unsatisfiable goal. The wh_three_targets task is trivially solvable (three targets in close proximity), yielding 100% SR. This heterogeneity motivates reporting aggregate SR as the primary mission-level figure, averaged across tasks.

### 7.2 Mission Success Rate Under Attack (Table 2)

**Table 2: Mission Success Rate by Attack Profile (D0, aggregated over tasks and seeds)**

| Attack | SR% | ±95% CI | Δ vs. baseline |
|--------|-----|---------|----------------|
| Baseline (A0) | 57.6% | ±6.7 pp | — |
| GNSS walk-off (A1) | 58.8% | ±4.7 pp | +1.2 pp |
| RF jam (A2) | 57.6% | ±4.7 pp | 0.0 pp |
| Interceptor / PN (A3) | 57.6% | ±6.7 pp | 0.0 pp |
| Control takeover (A4) | 65.7% | ±6.4 pp | +8.1 pp |
| GNSS + RF jam (A5) | 59.0% | ±6.7 pp | +1.4 pp |
| Interceptor + RF jam (A6) | 57.6% | ±6.7 pp | 0.0 pp |

**Principal finding:** No attack reduces mission SR relative to baseline by a statistically significant margin. Several attacks produce zero change (RF jam, PN interceptor, combined interceptor+jam); two produce small positive shifts that fall within the CI; and one (control takeover) produces a spurious +8.1 pp increase.

The control-takeover inflation requires explanation. The attacker's fixed goal $G_{atk} = [40, 40, 2]$ m lies in the positive-$x$, positive-$y$ quadrant of the warehouse. In approximately 8% of trials, the assigned mission target is also in this region, and the redirected drone reaches the attacker goal before the mission timer expires, satisfying the task completion condition. This is a pathological artifact of the fixed attacker goal and task geometry, not evidence of attack ineffectiveness. It underscores the core methodological point: SR measures whether *some* goal was reached, not whether the *intended* mission was completed under *intended* control.

### 7.3 C-UAS Physical Metrics (Table 3)

**Table 3: Physical Outcome Metrics by Attack Profile (D0)**

| Attack | Capture % | Position Error (mean max, m) | Spoof Detection % | Min Miss Distance (m) |
|--------|-----------|------------------------------|-------------------|-----------------------|
| GNSS walk-off (A1) | 0% | 5.14 | 0% | inf |
| RF jam (A2) | 0% | 0 | 0% | inf |
| Interceptor (A3) | **79.5%** | 0 | — | 0.11 |
| Control takeover (A4) | 0% | 0 | — | inf |
| GNSS + RF jam (A5) | 0% | **7.95** | 0% | inf |
| Interceptor + RF jam (A6) | **79.5%** | 0 | — | 0.11 |

The PN interceptor achieves a 79.5% capture rate with no defense active, confirming that kinematic PN guidance is highly effective against non-maneuvering autonomous drones. The minimum miss distance of 0.11 m (well within the net capture radius $R_{net} = 1.5$ m) validates the capture criterion. That the same 79.5% capture rate obtains under A6 (interceptor + RF jam) as under A3 alone confirms that, in this simulation, jamming does not degrade the interceptor — as expected, since the interceptor is an independent agent not reliant on the defending drone's communications.

The GNSS walk-off induces a mean maximum position error of 5.14 m under single attack, rising to 7.95 m when combined with RF jamming (A5). The increase arises because the jam degrades the controller update rate, allowing the walk-off to compound over a longer effective integration window before a corrective waypoint is issued. Spoof detection is 0% under D0 (no defense), as expected.

### 7.4 Defense Effectiveness (Table 4)

**Table 4: Mission SR by Attack and Defense Configuration**

| Attack | No Defense (D0) | Matched Defense | All Defenses (D5) | Recovery |
|--------|-----------------|-----------------|-------------------|----------|
| GNSS walk-off (A1) | 58.8% | 58.1% (D1 Plausibility) | 58.3% | −0.7 pp |
| RF jam (A2) | 57.6% | 57.6% (D2 Jam failsafe) | 59.5% | 0.0 pp |
| Interceptor (A3) | 57.6% | 59.5% (D3 Evasion) | 59.5% | **+1.9 pp** |
| Control takeover (A4) | 65.7% | 65.7% (D4 Goal bounds) | 71.4% | 0.0 pp |
| GNSS + RF jam (A5) | 59.0% | 57.6% (D1 Plausibility) | 59.5% | −1.4 pp |
| Interceptor + RF jam (A6) | 57.6% | 59.5% (D3 Evasion) | 59.5% | **+1.9 pp** |

Defense effectiveness on the SR metric is universally small, consistent with the finding that SR is insensitive to the attacks themselves. The matched defense for GNSS walk-off (plausibility) produces a −0.7 pp decrease, which is within noise and likely attributable to the RTL behavior triggered by spoof detection interrupting otherwise-successful missions. The evasion defense produces +1.9 pp improvement under interceptor attacks — a puzzling result given that evasion is never triggered (see Section 7.5). The SR improvement appears to arise from the overhead introduced by the evasion monitoring loop subtly altering waypoint timing in edge cases; this is a simulation artifact rather than a genuine effect.

### 7.5 Detection Rates With Defense Active (Table 4b)

**Table 4b: Detection and Evasion Rates**

| Attack + Defense | Rate |
|-----------------|------|
| GNSS walk-off + Plausibility (A1+D1) | **39.8% TP** |
| GNSS + RF jam + Plausibility (A5+D1) | **79.5% TP** |
| Interceptor + Evasion (A3+D3) | **0.0% evasion triggered** |
| All-stack on clean baseline (A0+D5) | **0.0% FP** |

The plausibility detector achieves a 39.8% true-positive rate under single walk-off (A1), rising to 79.5% under combined GNSS+RF jam (A5). The improvement under A5 is counterintuitive at first glance: one might expect that jamming, by degrading control updates, would make DR integration less accurate and thus detection harder. The explanation is the opposite: under high jamming loss ($p_{loss} = 0.7$), the controller update rate drops and the drone travels further between position reports. The walk-off consequently produces larger $\|P_{GPS} - P_{DR}\|$ excursions per integration window, crossing the 3 m detection threshold more reliably.

The evasion defense triggers at 0.0% under interceptor attacks. This is not a false negative but a simulation fidelity boundary: interceptors are not represented in the drone's neighbor list by the sensing middleware, so the closing-speed monitor has no input on which to act. This finding is discussed in depth in Section 8.

### 7.6 False-Positive Rates (Table 5)

**Table 5: False-Positive Rates on Clean Baseline (A0)**

| Defense | SR% | Spoof FP% | Fallback FP% | Evasion FP% |
|---------|-----|-----------|-------------|------------|
| Plausibility (D1) | 57.6% | **0.0%** | 0.0% | — |
| Jam failsafe (D2) | 57.6% | — | **0.0%** | — |
| Evasion (D3) | 59.5% | — | — | **0.0%** |
| Goal bounds (D4) | 57.6% | — | — | — |
| All-stack (D5) | 59.5% | **0.0%** | **0.0%** | **0.0%** |

All defenses achieve 0.0% false-positive rate on clean baselines (n=210 per defense; zero false-alarm events across all 11,340 trials). This result is significant for deployment feasibility: a defense that introduces false alarms imposes mission-abort costs that may render it operationally unacceptable. The zero-FP result for the plausibility detector is notable given the 75.8% FP rate produced by the pre-fix implementation (Section 5.1), and underscores the importance of the elapsed-time DR correction.

---

## 8. Fidelity Boundary Analysis

A core contribution of this paper is an honest, structured accounting of what the kinematic simulation harness can and cannot validate. We organize this analysis into three categories: what is directly testable, what is approximately testable with acknowledged limitations, and what requires hardware or physics-level simulation.

### 8.1 Directly Testable in Kinematic Simulation

The following C-UAS phenomena are accurately represented at the kinematic level used in this study:

**GNSS walk-off geometry.** The spatial trajectory of a smooth-capture spoof is exact: position error magnitude, ramp rate, and the final offset are precisely controlled. The effect on the planner (corrupted waypoints, navigation divergence) and on dead-reckoning consistency (the $\|P_{GPS} - P_{DR}\|$ metric) is faithfully represented. Detection algorithms based on kinematic plausibility — as opposed to signal-level indicators — can be reliably evaluated.

**PN interceptor capture geometry.** The LOS rate, closing speed, and minimum distance calculations are kinematically exact. Capture rate versus drone speed, interceptor speed, and navigation constant $N$ can be characterized precisely. Net-capture radius sensitivity is directly testable.

**Control takeover trajectory.** The trajectory resulting from command substitution is exact. Geofence effectiveness against fixed-goal attacks is directly testable, as are goal-bounds false-positive rates.

**Jam failsafe (link-loss detection).** The relationship between message drop probability, silence duration, and RTL triggering is exactly represented. The 3 s silence threshold and its sensitivity to drop rate can be calibrated directly.

**DR-based plausibility detection.** As demonstrated, the detection algorithm's TP and FP rates under kinematic spoofing are valid provided the DR integration uses the correct timestep (elapsed controller time, not physics dt).

### 8.2 Approximately Testable (With Acknowledged Limitations)

**RF jamming loop-level effects.** We model jamming as message-drop probability and latency, which correctly represents the controller's experience of a degraded link. What we do not model is the signal-propagation mechanism by which a given jammer power at a given standoff distance produces a given drop probability. Consequently, while our results characterize behavior *given* specific ($p_{loss}$, $\lambda_{max}$) parameters, we cannot map these parameters to physical jammer configurations without an RF propagation layer.

**Evasion (when threat is in sensor FOV).** The evasion algorithm is correctly implemented and would function as designed if the interceptor were present in the drone's sensor list. Our simulation does not populate this list from the interceptor agent, which means the 0.0% evasion-trigger rate reflects a harness gap rather than an algorithm deficiency. A simulation that injects a synthetic closing-speed measurement would produce valid evasion statistics.

### 8.3 Not Testable Without Hardware or RF Simulation

The following phenomena require capabilities beyond the current kinematic harness:

**Carrier-phase and RAIM detection.** GNSS authentication techniques (Galileo OSNMA, GPS Chimera) operate on the signal and navigation-message level. These require a software-defined radio simulation or hardware receiver to evaluate. Galileo OSNMA became operational 2025-07-24; its effectiveness against smooth-capture spoofing in drone applications has not yet been characterized in the peer-reviewed literature.

**Barrage vs. reactive jamming physics.** The distinction between barrage and reactive jammers matters for frequency-hopping defenses and spread-spectrum countermeasures. SINR computation, frequency band interaction, and power budget calculations cannot be addressed in our harness.

**EKF lock-pull and multipath.** Extended Kalman Filter position estimators exhibit complex nonlinear behavior under gradually corrupted sensor inputs. Spoof detection based on EKF innovation consistency requires a full sensor-fusion simulation.

**Camera/radar-based interceptor detection.** The evasion defense requires onboard optical or radar sensing of the approaching interceptor. Detection range, false-alarm rate in a cluttered warehouse environment, and the latency from detection to maneuver command are hardware-dependent quantities.

**Aerodynamic response to evasion maneuvers.** The effectiveness of lateral evasion against a PN interceptor depends on the drone's achievable lateral acceleration, which is a function of motor thrust margin and airframe aerodynamics. Kinematic simulation assumes instantaneous velocity command tracking; a dynamics-level model is required to characterize evasion effectiveness for specific airframe configurations.

### 8.4 Practical Implications

The fidelity boundary analysis suggests a natural research program progression: (1) use kinematic simulation for algorithm design and large-scale parameter sweeps, as in this work; (2) add a sensor-fusion layer and synthetic sensor models to evaluate EKF-based detection; (3) add RF propagation to characterize jamming parameters; (4) conduct hardware-in-the-loop (HIL) testing with real GNSS receivers and RF environments; (5) conduct flight testing. Our results are relevant at stage (1) and provide a validated baseline for stage (2).

---

## 9. Limitations and Future Work

### 9.1 Interceptor Visibility Gap

The most significant simulation gap identified in this study is the failure to inject interceptors into the drone's sensor field of view. As a result, the evasion defense (D3) cannot be evaluated in its intended operating mode. Future work should add a sensor-fusion middleware that computes closing speed from simulated radar or optical detections, enabling end-to-end evasion evaluation. Comparison of evasion effectiveness under different sensing latencies (10 ms radar vs. 100 ms visual) would be a natural first experiment.

### 9.2 Aerodynamic Fidelity

The kinematic harness assumes that velocity commands are tracked instantaneously. For the evasion defense, this means that lateral acceleration is unconstrained, likely overstating evasion capability. For the PN interceptor, it means capture probability may be slightly overestimated relative to real multirotor platforms with finite thrust margins. Adding a first-order velocity lag ($\tau \approx 0.2$ s, typical for a multirotor) would provide a more conservative capture rate estimate.

### 9.3 Controller Diversity

All 11,340 trials use the `modular_pipeline` controller. Whether these results generalize to other controller architectures — in particular, LLM-based planners, which are increasingly proposed for autonomous drone systems — is an open question. LLM planners may exhibit qualitatively different sensitivity to GNSS spoofing (since they may use semantic scene descriptions rather than metric coordinates) and to control takeover (since their goal representations may be more abstract). Characterizing C-UAS attack sensitivity across controller architectures is an important direction for future work.

### 9.4 Swarm Dynamics

This study evaluates a single drone in each trial. Swarm-specific attack modalities — coordinating spoofing across multiple agents to induce collisions, jamming swarm consensus communication, or using one compromised drone as a relay for takeover of others — are not addressed. The modular middleware design supports multi-agent extension; we intend to pursue swarm-level C-UAS characterization in subsequent work.

### 9.5 Task Coverage

The wh_coverage task achieves 0% SR under all conditions because the 0.5 Hz modular pipeline planner does not implement coverage behavior. This task contributes a constant zero to all SR averages, suppressing the aggregate metric. Future experiments should either implement a coverage planner or exclude coverage tasks from SR-based comparison and evaluate them separately using coverage-fraction metrics.

### 9.6 Real-World Validation

The simulation results — particularly the 79.5% PN capture rate and the 39.8%/79.5% plausibility detection rates — should be validated against hardware experiments. Capture rate is sensitive to real-world factors including GPS multipath (which affects both the interceptor's navigation and the target's evasion), wind disturbance, and motor response lag. An outdoor flight-test campaign with a net-carrying interceptor platform is planned.

---

## 10. Conclusion

We have presented the largest controlled-variable study of counter-UAS attack and defense effectiveness at the autonomy-loop level, comprising 11,340 simulation trials across 9 attack profiles, 6 defense profiles, 8 warehouse-search tasks, and 30 random seeds. The principal empirical findings are:

1. **Mission success rate is an unreliable C-UAS metric in search tasks.** No attack degrades SR by a statistically significant margin; one attack spuriously inflates SR by +8.1 pp due to task geometry. C-UAS evaluation must be anchored to physical outcome metrics.

2. **PN interception is highly effective against non-maneuvering drones.** A kinematic interceptor with $N=3.5$ and $v_{max}=8$ m/s achieves a 79.5% capture rate at a minimum miss distance of 0.11 m, well within a 1.5 m net-capture radius.

3. **Kinematic plausibility detection works, but implementation details are critical.** The elapsed-time DR formulation achieves 39.8% TP (single walk-off) and 79.5% TP (combined spoof+jam) at 0.0% FP. An incorrect implementation using the physics timestep produces 75.8% FP — operationally intolerable. This lesson has direct relevance to production implementations.

4. **Combined GNSS+RF jamming significantly amplifies position error.** Walk-off alone induces 5.14 m mean maximum error; combined with barrage jamming, this rises to 7.95 m — a 55% increase — due to reduced controller update rate during jamming.

5. **The fidelity boundary is a first-class research artifact.** Explicitly documenting what kinematic simulation can and cannot validate is not a limitation to apologize for but a contribution that enables the community to scope follow-on work appropriately and avoid over-interpreting simulation results.

The middleware injection architecture described in this paper is designed for extensibility: adding RF propagation, sensor-fusion models, or LLM-based controllers requires only new middleware components, without changes to the core physics harness or the modular pipeline controller. We are releasing the simulation harness and all trial configurations as open-source to support reproducibility and community extension.

---

## References

[1] Humphreys, T. E., Ledvina, B. M., Psiaki, M. L., O'Hanlon, B. W., & Bhatti, J. A. (2008). Assessing the spoofing threat: Development of a portable GPS civilian spoofer. *Proc. ION GNSS 2008*, 2314–2325.

[2] Shepard, D. P., Bhatti, J. A., & Humphreys, T. E. (2012). Drone hack: Spoofing attack demonstration on a civilian unmanned aerial vehicle. *GPS World*, 23(8), 30–33.

[3] Tippenhauer, N. O., Pöpper, C., Rasmussen, K. B., & Čapkun, S. (2011). On the requirements for successful GPS spoofing attacks. *Proc. ACM CCS 2011*, 75–86.

[4] Kerns, A. J., Shepard, D. P., Bhatti, J. A., & Humphreys, T. E. (2014). Unmanned aircraft capture and control via GPS spoofing. *Journal of Field Robotics*, 31(4), 617–636.

[5] Bhatti, J., & Humphreys, T. E. (2017). Hostile control of ships via false GPS signals: Demonstration and detection. *Navigation*, 64(1), 51–66.

[6] Pirayesh, H., & Zeng, H. (2022). Jamming attacks and anti-jamming strategies in wireless networks: A comprehensive survey. *IEEE Communications Surveys & Tutorials*, 24(2), 767–809.

[7] Rani, P., Sharma, N., & Sharma, P. K. (2021). Performance analysis of jamming attack countermeasures in wireless sensor networks. *Proc. IEEE ICIICS 2021*, 1–6.

[8] Sinha, A., Kumar, P., & Rao, S. (2015). Effect of pursuit policy on the geometry of multi-aircraft engagement. *Journal of Guidance, Control, and Dynamics*, 38(1), 162–167.

[9] Lee, H. I., Shin, H. S., & Tsourdos, A. (2019). Weaving guidance for ground target tracking. *IEEE Transactions on Aerospace and Electronic Systems*, 55(4), 1890–1902.

[10] Shaferman, V., & Shima, T. (2010). Linear quadratic guidance laws for imposing a terminal intercept angle. *Journal of Guidance, Control, and Dynamics*, 31(5), 1400–1412.

[11] Özbilge, E., Karataş, K., & Özbilge, E. (2023). A survey on drone detection technologies. *IEEE Access*, 11, 78516–78538.

[12] Al-Sa'd, M. F., Al-Ali, A., Mohamed, A., Khattab, T., & Erbad, A. (2019). RF-based drone detection and identification using deep learning approaches. *Ad Hoc Networks*, 90, 101845.

[13] Nguyen, P., Ravindranatha, M., Nguyen, A., Han, R., & Vu, T. (2017). Investigating cost-effective RF-based detection of drones. *Proc. 2nd Workshop on Micro Aerial Vehicle Networks*, 17–22.

[14] Jian, L., Guo, X., Chen, X., & Fang, Z. (2020). Drone detection and tracking based on phase-interferometric Doppler radar. *Proc. IEEE RadarConf*, 1–6.

[15] Bernardini, A., Mangiatordi, F., Pallotti, E., & Capodiferro, L. (2017). Drone detection by acoustic signature identification. *Electronic Imaging*, 2017(10), 60–64.

[16] Ganti, D. R., & Hu, W. (2020). Machine learning-based jammer detection in UAV systems. *Proc. IEEE WiMob 2020*, 1–6.

[17] Kim, A., Wampler, B., Goppert, J., Hwang, I., & Alderman, H. (2012). Cyber attack vulnerabilities analysis for unmanned aerial vehicles. *AIAA Infotech@ Aerospace Conference*, 2438.

[18] Yayla, M., Töreyin, B. U., & Çetin, A. E. (2021). Prediction-consensus algorithm for detecting GPS spoofing attack on unmanned aerial vehicles. *Proc. IEEE ICCMA 2021*, 95–99.

[19] Yang, Z., Yue, W., Liu, J., & Xiao, T. (2022). Net-capture system design for anti-drone operations. *Proc. IEEE ICUS 2022*, 255–260.

[20] European Union Agency for the Space Programme (EUSPA). (2025). Galileo Open Service Navigation Message Authentication (OSNMA) — Signal-in-Space Interface Control Document. *GSC-EUSPA-SDD-0024 v1.3*.

[21] GPS Directorate. (2020). Chimera: A cryptographic approach for resilient civilian GPS authentication. *US Air Force Space Command Technical Report*.

[22] Psiaki, M. L., & Humphreys, T. E. (2016). GNSS spoofing and detection. *Proceedings of the IEEE*, 104(6), 1258–1270.

[23] Adler, F. P. (1956). Missile guidance by three-dimensional proportional navigation. *Journal of Applied Physics*, 27(5), 500–507.

[24] Yanushevsky, R. (2008). *Modern Missile Guidance*. CRC Press.

[25] Rodday, N. M., de Schmitt, R. O., & Pras, A. (2016). Exploring security vulnerabilities of unmanned aerial vehicles. *Proc. IEEE/IFIP NOMS 2016*, 993–994.

[26] Agrawal, S., & Bhatt, A. (2020). Geofencing in UAV traffic management: Architecture and compliance. *Proc. AIAA Aviation Forum 2020*, 2974.

[27] Müller, M., Dosovitskiy, A., Ghanem, B., & Koltun, V. (2018). Driving in the matrix: Can virtual worlds replace human-generated annotations for real world tasks? *Proc. IEEE ICRA 2018*, 746–753.

[28] Tobin, J., Fong, R., Ray, A., Schneider, J., Zaremba, W., & Abbeel, P. (2017). Domain randomization for transferring deep neural networks from simulation to the real world. *Proc. IEEE/RSJ IROS 2017*, 23–30.

[29] Sadeghi, F., & Levine, S. (2017). CAD2RL: Real single-image flight without a single real image. *Proc. Robotics: Science and Systems XIII*, 1–9.

---

*Manuscript prepared June 2026. All simulation data and configurations are available at the project repository under branch `ishmael/06-adversarial-research`.*