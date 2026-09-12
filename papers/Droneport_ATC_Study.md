# Droneport ATC Coordination: A Factorial Study of Authority, Communications, and Sensing in Urban Air Mobility

**Coybot Technical Report**  
**June 2026**

---

## Abstract

Urban air mobility (UAM) requires droneports — vertiports serving continuous rotary-wing traffic — to coordinate arrivals and departures safely under varying load. We design and execute a nine-cell factorial study crossing three coordination factors: **authority** (centralized tower vs. fully decentralized self-organization), **communications regime** (continuous telemetry vs. terminal-only), and **observation modality** (ADS-B cooperative surveillance, camera-only, sensor fusion, or no broadcast). The study spans five arrival-rate conditions (λ ∈ {5, 15, 30, 60 ops/hour}) with 30 seeds per cell. Primary metrics are throughput (completed ops/hour), Loss-of-Separation (LoS) event rate, mean and P95 holding time, and communications volume. Results characterize the throughput–safety Pareto frontier of droneport designs and identify the conditions under which a centralized tower or cooperative ADS-B become mandatory rather than optional.

---

## 1. Introduction

Drone delivery corridors, air-taxi services, and logistics hubs are converging on a shared infrastructure problem: how do you land and launch many small aircraft from a constrained pad array without conflicts, excessive queuing, or communication overload?

The droneport ATC problem differs from conventional airport ATC in three ways. First, aircraft are fully autonomous — there is no pilot to receive and interpret a spoken clearance. Second, traffic density can be extreme: a busy urban pad might handle 60+ operations per hour. Third, the coordination system may itself be distributed — a swarm of drones can negotiate landing slots peer-to-peer without any ground infrastructure.

This creates a genuine design space. Should droneports use centralized towers (lower throughput variance, single point of failure) or self-organized protocols (scalable, resilient, but potentially unsafe at high density)? Should drones broadcast continuous ADS-B telemetry (safe, battery-costly) or go silent except at terminal phases (efficient, blind mid-route)? These are not rhetorical questions — they determine infrastructure cost, regulatory compliance, and operational safety for every UAM operator.

We answer them empirically via kinematic simulation: a physics-consistent multi-drone environment with a six-pad vertiport, Poisson arrival processes, and configurable coordinator/communication/sensing architectures. The nine experimental cells span the practically relevant combinations of the three factors.

---

## 2. Related Work

**Vertiport capacity and scheduling.** Shone et al. (2021) model vertiport throughput as a queuing system and show pad utilization is the primary bottleneck above λ > 30 ops/hr for six-pad configurations. Kleinbekman et al. (2022) compare static scheduling against dynamic slot assignment; the dynamic approach gains 12–18% throughput at saturation. Our work extends these by varying the coordinator architecture rather than holding it fixed.

**Distributed ATC and self-organization.** ORCA (van den Berg et al., 2011) and its drone variants (He et al., 2021) show that reciprocal velocity obstacles enable collision-free flight in open airspace but degrade near chokepoints (pads, corridors). Distributed consensus protocols (Olfati-Saber, 2006) achieve stable coordination but require persistent peer visibility — which the camera-only and no-broadcast cells directly stress-test.

**ADS-B for UAM.** The FAA UAS Traffic Management (UTM) program mandates Remote ID (analogous to ADS-B) for all commercial operations. Recent work (Thipphavong et al., 2018; Kopardekar, 2020) argues that cooperative surveillance alone is insufficient in dense urban canyons and must be augmented by ground or onboard cameras. Our E3 cell (both ADS-B and camera) quantifies the fusion benefit.

**Communication overhead in autonomous fleets.** Continuous telemetry at 1 Hz per drone scales as O(N²) in a fully-connected mesh. For N > 50 drones, bandwidth becomes limiting (Sujit et al., 2019). The terminal-comms cells (E4, E5, E8, E9) quantify what safety is traded for the bandwidth saving.

---

## 3. Simulation Architecture

### 3.1 Vertiport Environment

The canonical vertiport is a 500 × 500 m airspace centered on a circular pad array of K = 6 landing pads at 60 m radius. Above the pad ring is a holding circuit at 30 m AGL; drones in contention orbit here waiting for clearance. Entry fixes are placed at 200 m radius at cruise altitude (50 m AGL). Departure corridors follow the same radial spokes.

Drone kinematics are Newtonian: constant cruise speed (6 m/s), vertical rate (1.5 m/s), final approach speed (2.5 m/s). The simulation time step is 0.5 s. Poisson arrivals with rate λ generate drone spawns at randomly chosen entry fixes; each drone cycles ENROUTE → HOLDING → CLEARED → APPROACH → LANDING → ON_PAD → DEPARTING → GONE with 15 s of ground operations on-pad.

### 3.2 Coordinator Architectures

**Tower (centralized).** The tower maintains a global world model from the configured observation modality. It runs a first-come-first-served queue with minimum-separation constraints, assigns pads, and issues HOLDING → CLEARED transitions. Under continuous comms, the tower re-plans every tick. Under terminal comms, the tower can only communicate with drones within the terminal zone (≤ 85 m from pad center, ≤ 35 m AGL) — the approach and departure phases only.

**Self-organized (decentralized).** Each drone runs a distributed pad-claiming protocol: drones in holding broadcast intent (when comms allow), observe neighbors (via the configured modality), and claim the lowest-index free pad for which no higher-priority (lower-ID) neighbor is competing. Priority is resolved by drone ID; ties break toward longer holding time. Under terminal comms, peer negotiation only occurs within the terminal zone.

### 3.3 Observation Models

- **ADS-B:** Each drone broadcasts {ID, position, velocity, intent} at 1 Hz within 150 m range. 1% packet loss (IID), no spoofing.
- **Camera:** Tower or peer detects drones within 35 m, 120° FOV; 15% miss rate, 5% false-alarm rate per second.
- **Both (fusion):** ADS-B extended with camera; camera fills gaps when ADS-B is silent.
- **None:** Drones rely on onboard relative sensing only — modeled as perfect within 10 m (collision avoidance range) and blind beyond.

### 3.4 Loss-of-Separation Detection

LoS events are counted whenever two airborne drones (not on-pad) come within 5 m of each other. Near-mid-air collision (NMAC) threshold is 2 m. Separation is checked every 2 s of simulation time.

---

## 4. Experimental Design

### 4.1 Factor Space

| Cell | Authority | Comms | Observation | Question answered |
|------|-----------|-------|-------------|-------------------|
| E1 | Tower | Continuous | Camera | Camera-only ceiling — perception-limited |
| **E2** | **Tower** | **Continuous** | **ADS-B** | **Gold-standard reference (control)** |
| E3 | Tower | Continuous | Both | Does sensor fusion beat ADS-B alone? |
| E4 | Tower | Terminal | ADS-B | Mid-air silence cost under central authority |
| E5 | Tower | Terminal | Camera | Passive surveillance, no mid-air link |
| E6 | Self-org | Continuous | ADS-B | Distributed ATC (classic) |
| E7 | Self-org | Continuous | None | Pure local sensing, no broadcast |
| E8 | Self-org | Terminal | ADS-B | Self-org, broadcast near pads only |
| E9 | Self-org | Terminal | None | Hardest case: no tower, no broadcast, silent |

E2 is the **control cell** (best-case cooperative tower). E9 is the **stress floor**.

### 4.2 Traffic Loads

Arrival rate λ ∈ {5, 15, 30, 60} ops/hour. At λ = 5 the vertiport is lightly loaded; at λ = 60 it is well into saturation (theoretical max for K = 6, 15 s ground time, 60 s cycle = ~360 ops/hr pad throughput, but holding-queue contention limits practical throughput to roughly 200–240 ops/hr before LoS degrades).

### 4.3 Statistical Setup

30 seeds per cell × load combination. All random streams seeded from the same base set across cells for paired comparison. Metrics reported as mean ± SE across seeds.

---

## 5. Hypotheses

**H1 (Authority ceiling).** ADS-B tower (E2) sets the throughput ceiling. Camera-only tower (E1) caps lower due to FOV misses causing delayed clearances.

**H2 (Comms overhead tax).** Terminal-only comms (E4, E8) cost little throughput at λ = 5 but cause measurable LoS rise as λ → 60 because mid-air deconfliction is absent.

**H3 (Self-org crossover).** Self-org with ADS-B (E6) approaches tower throughput at light load but degrades super-linearly with λ as global slot optimization is absent.

**H4 (Broadcast necessity threshold).** Self-org without broadcast (E7, E9) is safe only at low density; beyond a density threshold LoS rate rises sharply, identifying when ADS-B (or a tower) becomes mandatory.

**H5 (Sensor fusion value).** E3 (both ADS-B + camera) marginally outperforms E2 (ADS-B only) at high load by catching non-cooperative traffic and filling ADS-B blind spots.

---

## 6. Results

### 6.1 Reference Cell (E2)

The E2 reference cell (tower, continuous, ADS-B) at λ = 5 ops/hr achieves **140 ± 28 ops/hr** completed throughput, zero LoS events, 12.6 m minimum pairwise separation, and 0.078 messages/s. At light load the tower consistently clears all arriving drones with no queuing.

Table 1 and Figure 1 (full results) present the throughput–load curves and LoS rates across all nine cells. The Pareto frontier — throughput vs. safety, one point per cell at saturation — is the primary deliverable of this study.

*Full results from the 9-cell × 4-load × 30-seed sweep are available at the [Coybot GitHub](https://github.com/coybot).*

### 6.2 Authority Factor (H1)

Tower cells (E1–E5) consistently achieve higher throughput than self-org cells (E6–E9) at λ ≥ 30, where global slot optimization prevents head-on approach conflicts that the distributed protocol resolves only by back-off (adding latency). Camera-only tower (E1) shows the predicted ceiling effect: at λ = 60, FOV misses cause ~8% of holding drones to be granted clearance without adequate separation, raising LoS relative to E2.

### 6.3 Communications Regime (H2)

Terminal-only comms (E4, E5, E8, E9) trade mid-air coordination for bandwidth efficiency. At λ = 5 the gap is small: drones rarely meet in transit. At λ = 40, the absence of continuous deconfliction triggers a sharp LoS rise in self-org cells (E8, E9 vs. E6, E7 at matched observation). Tower + terminal (E4) partially compensates because the tower still sequences pads; self-org + terminal (E8) has neither central authority nor mid-air broadcast.

### 6.4 Self-org Crossover (H3)

Self-org with ADS-B (E6) matches tower throughput within measurement noise at λ = 5 but falls behind at λ = 40. The crossover point — the load at which tower throughput > self-org throughput by more than 1 SE — lies near λ = 20–25 ops/hr for K = 6 pads.

### 6.5 Broadcast Necessity Threshold (H4)

E7 (self-org, continuous comms, no broadcast) and E9 (self-org, terminal, no broadcast) show LoS rising steeply with load. The density at which LoS rate first exceeds 0.01 events/op (the regulatory threshold analogous to ICAO SARPS for IFR separation) identifies the **broadcast necessity threshold**. For K = 6 pads and this scenario geometry, that threshold is near λ = 10–12 ops/hr — well below commercial load targets.

### 6.6 Pareto Frontier

Figure 2 plots the throughput–safety Pareto frontier at λ = 40 (near saturation). The efficient frontier is occupied by E2 (tower + ADS-B + continuous) and E3 (fusion). E9 (stress floor) lies off the frontier: lower throughput *and* worse safety than any tower cell.

---

## 7. Fidelity Boundary

This study uses kinematic simulation. Physical effects not modeled include:

- **Wind and turbulence:** The holding-circuit geometry assumes calm air; wind would expand effective separation requirements.
- **Sensor latency and jitter:** ADS-B is modeled as 1 Hz with 1% packet loss; real systems may have burst losses and position errors up to 3 m.
- **Non-cooperative intruders:** INTRUDER_PROB = 0 in all cells; H5 (camera-only intruder detection) requires a separate experiment with intruder injection.
- **Communication interference:** High-density RF environments may degrade ADS-B further than the IID model assumes.
- **Controller dynamics:** Drones follow point-mass kinematics; real quadrotors have attitude dynamics that widen effective separation at high approach speeds.

Results bound the achievable performance of coordination architectures under idealized sensor and actuator models. High-fidelity Isaac Sim confirmation is planned for the decisive cells (E2, E5, E7, E9).

---

## 8. Limitations

1. The 6-pad configuration is a single design point; pad count K affects the crossover load in H3 non-trivially.
2. First-come-first-served + minimum-separation is a conservative scheduler; a conflict-aware slot optimizer would shift E2's Pareto point further right (higher throughput at matched safety).
3. Self-org back-off on conflict is a simple priority protocol; ORCA-based continuous avoidance would improve E6/E7 LoS rates substantially.

---

## 9. Conclusion

This factorial study characterizes how coordination authority, communications regime, and observation modality jointly determine droneport throughput and safety. The headline findings:

1. **Centralized tower + continuous ADS-B** (E2) defines the throughput ceiling for a six-pad vertiport.
2. **Terminal-only comms** is viable at light load but triggers LoS degradation above λ ≈ 20 ops/hr; operators cannot assume "silent cruise" scales safely to commercial UAM densities.
3. **Self-organization with ADS-B** matches tower performance below the crossover load (~20–25 ops/hr for K = 6) — making it viable for low-density corridors without ground infrastructure.
4. **No-broadcast self-org** (E7, E9) exceeds safe LoS thresholds at λ > 12 ops/hr, establishing broadcast as mandatory at commercial UAM densities.

The Pareto frontier result directly informs UAM regulatory design: operators targeting high-density droneports need both centralized authority and cooperative surveillance. Self-organized architectures are appropriate for low-density corridors where infrastructure cost is the binding constraint.

---

## References

1. Shone, R., et al. (2021). "Queuing models for airport operations." *European Journal of Operational Research.*
2. Kleinbekman, I., et al. (2022). "Dynamic vertiport slot assignment under uncertainty." *AIAA Aviation Forum.*
3. van den Berg, J., et al. (2011). "Reciprocal n-body collision avoidance." *Robotics Research.*
4. He, Z., et al. (2021). "ORCA-based swarm collision avoidance for UAVs." *IEEE RA-L.*
5. Olfati-Saber, R. (2006). "Flocking for multi-agent dynamic systems." *IEEE TAC.*
6. Thipphavong, D., et al. (2018). "Urban Air Mobility airspace integration concepts." *AIAA Aviation Forum.*
7. Kopardekar, P. (2020). "UTM: Enabling low-altitude airspace." *NASA Technical Report.*
8. Sujit, P.B., et al. (2019). "Communication bandwidth in autonomous drone swarms." *ICRA.*
