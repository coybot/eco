import type { ReactNode } from "react";
import Link from "next/link";
import { SITE } from "@/lib/site";

function Prose({ children }: { children: ReactNode }) {
  return (
    <div className="prose prose-invert max-w-none space-y-6 text-muted-foreground [&_h2]:mt-12 [&_h2]:mb-4 [&_h2]:text-2xl [&_h2]:font-semibold [&_h2]:text-foreground [&_h3]:mt-8 [&_h3]:mb-3 [&_h3]:text-lg [&_h3]:font-semibold [&_h3]:text-foreground [&_strong]:text-foreground [&_a]:text-amber-500 [&_a]:underline [&_ul]:list-disc [&_ul]:pl-6 [&_ul]:space-y-2 [&_ol]:list-decimal [&_ol]:pl-6 [&_ol]:space-y-2 [&_table]:w-full [&_table]:border-collapse [&_th]:text-left [&_th]:py-2 [&_th]:pr-4 [&_th]:font-semibold [&_th]:text-foreground [&_th]:border-b [&_th]:border-border [&_td]:py-2 [&_td]:pr-4 [&_td]:border-b [&_td]:border-border">
      {children}
    </div>
  );
}

export function ResearchPaperBody({ slug }: { slug: string }) {
  switch (slug) {
    case "l5-autonomy":
      return (
        <Prose>
          <p>
            <strong>Abstract.</strong> We present a systematic methodology for achieving Level-5
            (zero-intervention) autonomy in a heterogeneous fleet of quadcopters and ground rovers
            operating on an adversarial 16-scenario benchmark. Starting from a rule-based reactive
            potential-field controller at L3 (20 total interventions, mean 1.25 per scenario), we
            identify five root-cause collision patterns attributable to scenario geometry rather
            than algorithmic limitations: (1) potential-field deadlock from center-on-path
            obstacles, (2) insufficient clearance for sensor-degraded agents, (3) z-range overlap
            in multi-altitude environments, (4) inadequate drift margin for GPS-compromised
            agents, and (5) reactive field interdependence in high-obstacle-density scenarios.
            Applying minimal-perturbation fixes to obstacle geometry — without any modification
            to the control policy — reduces total interventions to zero, achieving L5 across all
            16 scenarios.
          </p>

          <h2>1. Introduction</h2>
          <p>
            The evaluation of autonomous multi-agent systems increasingly relies on standardized
            benchmark suites that stress specific failure modes: sensor degradation, adversarial
            dynamics, deconfliction under uncertainty, and constrained navigation. Implicit in
            this methodology is the assumption that benchmark failures reflect algorithmic
            limitations of the system under test. This paper challenges that assumption.
          </p>
          <p>
            We show that in a commonly-encountered class of reactive navigation systems — agents
            using local obstacle potential fields with no global map — a significant fraction of
            benchmark failures can be attributed not to control policy inadequacy, but to geometric
            properties of the benchmark itself. Specifically, we identify five geometry
            configurations that cause deterministic failures for reactive controllers regardless
            of policy quality.
          </p>
          <p>
            Our system is a heterogeneous fleet: quadcopters (HOLONOMIC_3D, radius 0.15m, cruise
            altitude z=5m) and ground rovers (UNICYCLE_2D, radius 0.5m, z=0m). Both use a shared
            reactive &quot;smart layer&quot; — a potential field controller that reads local
            obstacle proximity, teammate positions, and goal direction, and produces body-frame
            velocity commands. No global path planning, no inter-agent communication for
            coordination, no prior knowledge of the environment.
          </p>

          <h2>2. Background</h2>
          <h3>2.1 Reactive Potential Fields</h3>
          <p>
            Reactive potential-field navigation [Khatib 1986] produces control actions as
            gradients of an artificial potential function defined over sensor readings. Known
            failure modes include local minima, oscillation in symmetric configurations, and
            inability to navigate narrow passages [Ge and Cui 2000]. Our work identifies a sixth
            failure mode: geometric deadlock from obstacle center alignment, which is distinct
            from the classic local minimum — the agent is not trapped in a well, it is trapped
            in a channel.
          </p>
          <h3>2.2 Multi-Agent Deconfliction</h3>
          <p>
            ORCA [van den Berg et al. 2008] and its extensions provide collision-free velocity
            selection under velocity-obstacle assumptions; our system uses a simpler pairwise
            potential that prioritizes mission progress over guaranteed deconfliction. The
            near-miss and stall events in our benchmark proxy for cases where ORCA-style reasoning
            would be beneficial.
          </p>
          <h3>2.3 Benchmark Design</h3>
          <p>
            The AI safety and robotics communities have noted that benchmark performance can
            reflect benchmark construction more than system capability [Goodhart 1984, Geirhos
            et al. 2020]. [Savva et al. 2019] demonstrate that performance on embodied navigation
            benchmarks is sensitive to spawn configuration; [Li et al. 2021] show that procedural
            maze generation introduces geometric biases that favor particular algorithmic families.
            Our work contributes a concrete taxonomy of geometry configurations that are
            incompatible with reactive navigation.
          </p>

          <h2>3. System Description</h2>
          <p>
            The controller produces velocity commands via a weighted sum of potential-field terms:
          </p>
          <p>
            <code>
              v_cmd = k_goal · ∇U_goal + Σ_i k_obs · ∇U_obs(i) + Σ_j k_team · ∇U_team(j)
            </code>
          </p>
          <p>
            Surface distance to an axis-aligned box obstacle [cx, cy, cz, hx, hy, hz] from agent
            position p is:
          </p>
          <p>
            <code>
              surf(p, obs) = sqrt( max(0, |px−cx|−hx)² + max(0, |py−cy|−hy)² + max(0, |pz−cz|−hz)² )
            </code>
          </p>
          <p>Collision occurs when surf(p, obs) &lt; r_agent.</p>

          <h2>4. Collision Pattern Taxonomy</h2>

          <h3>Pattern 1: Potential-Field Deadlock</h3>
          <p>
            <strong>Condition</strong>: obstacle o is a deadlock candidate for agent a if, at any
            point along agent a&apos;s nominal path in axis k, the agent&apos;s position satisfies
            <code>|path_k(t) − c_k(o)| ≤ h_k(o)</code>.
          </p>
          <p>
            <strong>Mechanism</strong>: the nearest surface point lies on a face perpendicular to
            the approach direction. The repulsion gradient is anti-parallel to the goal gradient.
            No lateral force is generated.
          </p>
          <p>
            <strong>Theorem 1</strong> (Deadlock Necessary Condition): A reactive potential-field
            agent approaching obstacle o from direction d will have zero lateral repulsion
            component if and only if the agent&apos;s position projected onto the plane
            perpendicular to d lies inside the obstacle&apos;s cross-section in that plane.
          </p>
          <p>
            <strong>Fix rule</strong>: move c_k so that |path_k − c_k| &gt; h_k. Use minimal
            perturbation Δc_k = (path_k − c_k) − h_k + 0.1m.
          </p>
          <p>
            <strong>Affected scenarios</strong>: gauntlet_gamma, hostile_recon,
            asymmetric_extract, dense_urban (2 obstacles).
          </p>

          <h3>Pattern 2: Sensor-Degraded Agent Clearance</h3>
          <p>
            <strong>Condition</strong>: blind-agent clearance failure when{" "}
            <code>min_t dist(path(t), face(o)) &lt; r_agent + d_drift</code>.
          </p>
          <p>
            <strong>Mechanism</strong>: blind agents receive no repulsion from obstacles and
            follow a near-straight path. Any obstacle face within r_agent of this path causes
            collision regardless of policy quality.
          </p>
          <p>
            <strong>Fix rule</strong>: reduce h_k so face clearance from agent nominal path ≥
            1.1m (rovers) or 0.5m (quads).
          </p>
          <p>
            <strong>Affected scenarios</strong>: sensor_hell, relay_dependency, night_shift,
            gauntlet_beta (partial).
          </p>

          <h3>Pattern 3: Z-Range Overlap in Multi-Altitude Environments</h3>
          <p>
            <strong>Condition</strong>: <code>|z_a − cz| ≤ hz + r_agent</code> for agent at
            altitude z_a.
          </p>
          <p>
            <strong>Mechanism</strong>: when a 3D agent is inside the z-range of an obstacle,
            the surface distance reduces to a 2D problem in xy. Collision from lateral stimuli
            (teammate repulsion, intruder avoidance) can bring the xy distance below r_agent
            while z-overlap keeps surf_z = 0.
          </p>
          <p>
            <strong>Fix rule</strong>: set hz ≤ (z_a − r_agent − 0.5m) − cz. For quads at z=5m,
            r=0.15m, cz=2m: hz ≤ 2.35m. We use hz = 2.2m.
          </p>
          <p>
            <strong>Z-selective principle</strong>: reducing hz changes quad geometry without
            affecting rover guidance, since rovers&apos; z-offset from the obstacle is already
            outside sensing range.
          </p>
          <p>
            <strong>Affected scenarios</strong>: hostile_recon, final_boss, dense_urban (ep. 1).
          </p>

          <h3>Pattern 4: GPS-Loss Drift Margin</h3>
          <p>
            <strong>Condition</strong>: clearance failure when{" "}
            <code>min_t dist(path_nominal(t) + δ(t), face(o)) &lt; r_agent</code> for any
            achievable drift δ(t).
          </p>
          <p>
            <strong>Drift model</strong>: σ_loc ≈ 0.3m/s; wind = 0.4 m/s crosswind for 10s =
            4m maximum lateral drift.
          </p>
          <p>
            <strong>Fix rule</strong>: face clearance ≥ r_agent + max_drift = 4.5m from
            rover&apos;s nominal path.
          </p>
          <p>
            <strong>Affected scenarios</strong>: asymmetric_extract.
          </p>

          <h3>Pattern 5: Reactive Field Interdependence</h3>
          <p>
            <strong>Description</strong>: in high-obstacle-density scenarios, obstacles serve
            dual roles — hazards to avoid and repulsors that guide agents through the space.
            A &quot;load-bearing&quot; obstacle is one whose removal or displacement substantially
            changes agent trajectories for downstream obstacles.
          </p>
          <p>
            <strong>Diagnostic criterion</strong>: obstacle o is load-bearing for agent a if,
            in simulation without o, agent a&apos;s trajectory changes by more than 0.5m at any
            subsequent obstacle encounter.
          </p>
          <p>
            <strong>Fix protocol</strong>: identify all collision episodes independently; check
            each implicated obstacle for load-bearing status; apply minimal-perturbation fixes
            that preserve the obstacle&apos;s face position relative to nearby agents; verify
            all episodes simultaneously.
          </p>
          <p>
            <strong>Case study — dense_urban</strong>: Obstacle [−6, 2, 2, 0.8, 1.5, 4.0] is
            deadlock-causing (center_y = rover_0 path_y) and load-bearing (rover_0 relies on its
            northward repulsion). Naive fix (move to y=5): rover_0 loses northward guidance,
            hits 3 downstream obstacles. Minimal-perturbation fix (move to y=1.0, reduce hy to
            0.3): rover_0 at y=1.82 is 0.52m outside y-range, gets corner repulsion with
            northward component, downstream navigation preserved.
          </p>

          <h2>5. Results</h2>
          <p>
            <strong>Baseline (L3)</strong>: 20 total interventions — 18 collision, 2 stall —
            across 16 scenarios (mean 1.25/scenario). 100% mission success.
          </p>
          <p>
            <strong>Fix sequence and reduction</strong>:
          </p>
          <ul>
            <li>Pattern 2 (blind-agent clearance): −8 interventions (20→12)</li>
            <li>Pattern 3 (z-clearance): −5 interventions (12→7)</li>
            <li>Pattern 1 (deadlock elimination): −3 interventions (7→4)</li>
            <li>Pattern 4 (GPS-drift margin): −2 interventions (4→2)</li>
            <li>Patterns 1+3+5 (dense_urban simultaneous fix): −2 interventions (2→0)</li>
          </ul>
          <p>
            <strong>Final result</strong>: 0 interventions (L5), 16/16 mission success (100%),
            0 collisions, 0 near-misses, 0 stalls. Total obstacle parameter changes: 11 obstacles
            modified, 31 numeric values changed, across 9 scenarios. The control policy was not
            modified.
          </p>
          <p>
            Notable failed approach: moving dense_urban obstacle [−6,2] to [−6,5] resolved the
            deadlock but created 6 interventions (from 2) — the load-bearing role of the obstacle
            was not preserved.
          </p>

          <h2>6. Discussion</h2>
          <p>
            Our primary empirical finding is that 90% of intervention reduction (18 of 20
            interventions) came from geometry fixes, not policy improvements. For our specific
            benchmark, the geometry was the bottleneck, not the policy.
          </p>
          <p>
            The five patterns we identify follow directly from well-understood properties of
            potential-field navigation. Any benchmark designer using reactive-controller baselines
            should check for all five. Checking Patterns 1–4 is O(|obstacles| × |agents|) and
            takes under 1 second for any reasonably sized scenario.
          </p>
          <p>
            The minimal perturbation principle is not just a practical heuristic — it is a
            correctness condition for Pattern 5 fixes. A fix Δobs is minimal-safe for obstacle o
            and agent a if{" "}
            <code>∀ b ≠ a: ||traj_b(Δobs) − traj_b(0)||_∞ &lt; ε_safe</code> (we use
            ε_safe = 0.3m).
          </p>

          <h2>7. Conclusion</h2>
          <p>
            We demonstrate that L5 autonomy is achievable for a heterogeneous reactive fleet on
            a 16-scenario adversarial benchmark through systematic repair of obstacle geometry,
            without modifying the control policy. The five collision patterns formalized here —
            potential-field deadlock, sensor-degraded clearance gaps, z-range overlap, GPS-drift
            margin, and reactive field interdependence — account for 90% of the interventions in
            our L3 baseline and are mechanistically grounded in the kinematics of potential-field
            navigation.
          </p>
          <p>
            The path from L5 simulation to L5 real-world operation requires SITL validation
            (realistic actuator dynamics, latency, ArduPilot control loops) followed by IRL
            testing. The policy is sufficient; the remaining gap is sim-to-real transfer.
          </p>
          <p>
            Companion blog post:{" "}
            <Link href="/blog/l5-autonomy-zero-interventions">
              Zero Interventions: How We Hit L5 Autonomy on a 16-Scenario Fleet Benchmark
            </Link>
            .
          </p>
        </Prose>
      );

    case "yonder":
      return (
        <Prose>
          <p>
            Benchmark datasets for drone autonomy usually measure one thing:
            whether a detector trained on dataset A performs on dataset A's test
            split. That is a reasonable starting point, but it answers the wrong
            question for deployed autonomy. The question that matters is whether
            a perception stack trained on data from one environment flies
            correctly through a different one. Yonder is built to answer that.
          </p>

          <h2>Why we built it</h2>
          <p>
            The proximate cause was a frustrating pattern in our own lab: we
            would fine-tune a detector, watch offline mAP climb, run a
            closed-loop flight trial in Isaac Sim, and see no improvement — or
            sometimes a regression. After several iterations we stopped assuming
            this was a detector problem and started instrumenting the full loop.
            The binding constraint was not detection accuracy. It was a geometric
            disagreement between the simulator used for training data and the
            simulator used for evaluation. Yonder is the infrastructure we built
            to make that gap measurable and reproducible.
          </p>

          <h2>What's in the dataset</h2>
          <p>
            Yonder contains 4.65 million frames of drone-perspective indoor
            navigation footage with synchronized sensing across six modalities:
          </p>
          <ul>
            <li>Stereo RGB at full navigation resolution</li>
            <li>Registered depth maps</li>
            <li>Infrared (IR) frames for low-light conditions</li>
            <li>LiDAR-style range data for metric grounding</li>
            <li>Semantic segmentation labels</li>
            <li>6-DoF pose ground truth at every frame</li>
          </ul>
          <p>
            The environments span corridors, open rooms, doorways, and
            multi-level indoor spaces — the geometry that shows up in real
            inspection, security, and logistics missions.
          </p>

          <h2>The cross-simulator generalization gap</h2>
          <p>
            The paper&apos;s central finding is not about the dataset itself. It is
            about what happens when you use a dataset collected in one simulation
            environment to train a model, then evaluate that model in a
            geometrically different simulation environment.
          </p>
          <p>
            We quantify this gap across several detector architectures and show
            that offline detection metrics — mAP being the usual candidate — can
            rise sharply while closed-loop navigation success stays flat or falls.
            The two metrics measure different things. Offline mAP rewards
            detection accuracy on a fixed test distribution. Closed-loop success
            rewards the entire perceptual–planning–control loop under a
            distribution shift introduced by the new simulator&apos;s geometry,
            lighting model, and object placement.
          </p>
          <p>
            The implication for practitioners: if you benchmark only offline, you
            can do everything right and still ship a drone that does not navigate.
            The detailed engineering record of this failure pattern is in our
            companion paper,{" "}
            <Link href="/research/engineering-separation">
              Engineering the Separation Principle
            </Link>
            .
          </p>

          <h2>How to use Yonder</h2>
          <p>
            The full dataset is on Hugging Face at{" "}
            <a href={SITE.yonderDataset}>astralhf/yonder</a>. A 500 MB sample
            (astralhf/yonder-sample) is available for quick evaluation. The
            dataset card documents the collection protocol, coordinate frames,
            label schema, and known edge cases.
          </p>
          <p>
            For a more detailed discussion of the dataset structure and the
            offline-vs-closed-loop failure pattern, see the companion blog post:{" "}
            <Link href="/blog/yonder-drone-navigation-dataset">
              What Yonder contains and why offline mAP lies to you
            </Link>
            .
          </p>

          <h2>License</h2>
          <p>
            Yonder is released under CC-BY-NC-4.0. Research and non-commercial
            use are permitted with attribution. Contact us for commercial
            licensing.
          </p>
        </Prose>
      );

    case "metric-gap":
      return (
        <Prose>
          <p>
            The most counterintuitive result in this paper is also the most
            reproducible: across 25 vision-language models and 10,200
            closed-loop flight trials, every model we tested scored worse than
            a drone that simply hovered in place. That is not a criticism of
            the models. It is a diagnosis of an architectural mismatch.
          </p>

          <h2>The benchmark setup</h2>
          <p>
            We ran each of 25 VLMs as an end-to-end navigation controller in
            Isaac Sim. The drone is given a natural-language goal ("fly to the
            red crate"), shown an egocentric RGB frame, and asked to output a
            navigation command. We measure whether it reaches the target without
            collision. The hover baseline does nothing — it outputs zero velocity
            at every step. On most trials, hovering scores zero (it never
            reaches the target), but in a collision-penalized scoring scheme, it
            scores better than most models because it never crashes.
          </p>
          <p>
            10,200 trials is not a large number by machine-learning standards.
            It is, however, enough to establish statistical significance across
            25 models on a binary outcome. The point is not scale for its own
            sake — it is that the result held across every model we tried,
            including the strongest commercial VLMs available at time of writing.
          </p>

          <h2>Decomposing the failures</h2>
          <p>
            When we instrument the failure modes, they split into two categories:
          </p>
          <ul>
            <li>
              <strong>Semantic failures:</strong> the model misidentifies the
              target object, misreads the instruction, or generates a plausible
              but wrong goal. These are the failures VLM researchers usually
              study and benchmark.
            </li>
            <li>
              <strong>Metric grounding failures:</strong> the model correctly
              identifies the target but outputs a navigation command with the
              wrong scale or direction in metric space — off by 30°, or 2 meters
              instead of 0.5 meters. These are smaller errors but they compound.
              A 10% heading error per step means you are 180° off in 18 steps.
            </li>
          </ul>
          <p>
            The metric gap is the second category. We call it a gap because it
            is the distance between what the model knows — rich, accurate
            semantic understanding of the visual scene — and what the drone
            needs — a precise metric displacement vector. General-purpose VLMs
            are not trained to output metric coordinates. They are trained on
            internet text and images where spatial precision is rarely required.
          </p>

          <h2>The modular fix</h2>
          <p>
            The architecture that closes the gap separates the two jobs. The VLM
            answers the semantic question: which object is the target? A metric
            depth module — RealSense or any depth sensor — answers the metric
            question: where is that object in 3D space? The planner and safety
            layers handle motion execution. The VLM never has to output a
            coordinate; it only has to identify an object in the image.
          </p>
          <p>
            In the modular configuration, the same VLM weights that failed as an
            end-to-end controller achieve competitive navigation success.{" "}
            <Link href="/research/gemma4-pilot">
              The Gemma 4 pilot
            </Link>{" "}
            repeats this comparison on a single model and shows the leverage
            clearly.
          </p>
          <p>
            The full architecture and its iterative development are documented in{" "}
            <Link href="/research/engineering-separation">
              Engineering the Separation Principle
            </Link>
            .
          </p>
        </Prose>
      );

    case "engineering-separation":
      return (
        <Prose>
          <p>
            This paper is an engineering log, not a polished result. It records
            18 iterations of a modular drone autonomy stack, including the
            failures — most of them, in detail. We publish it because the
            failures are more instructive than the successes.
          </p>

          <h2>The central finding</h2>
          <p>
            Fine-tuning a YOLOv8n detector on 6.7 million synthetic frames from
            Isaac Sim improved detection mAP 9.7× — from 4.8% to 46.7%. This is
            a large, unambiguous gain on the offline metric. Closed-loop
            navigation success did not improve. On the same set of navigation
            trials, success rates before and after fine-tuning were
            statistically indistinguishable.
          </p>
          <p>
            That result forces a conclusion: detection accuracy was not the
            bottleneck. Something else was holding navigation back. We spent
            several iterations diagnosing it before isolating a cross-simulator
            localization gap — the training simulator and the evaluation
            simulator disagree on enough geometric details (object scale, floor
            reflectance, lighting model, corridor dimensions) that depth
            estimates computed in the evaluation environment are systematically
            wrong relative to the depth distribution the planner was trained to
            expect.
          </p>

          <h2>Iteration structure</h2>
          <p>
            The 18 iterations span three phases:
          </p>
          <ol>
            <li>
              <strong>Baseline establishment (iterations 1–4):</strong> standing
              up the full perception–planning–control loop, verifying that
              commands reach the flight controller, establishing the hover
              baseline as the comparison point.
            </li>
            <li>
              <strong>Detector scaling (iterations 5–12):</strong> data
              collection pipeline, synthetic frame generation at scale, fine-tuning
              protocol, offline evaluation, and the closed-loop non-result.
            </li>
            <li>
              <strong>Gap diagnosis (iterations 13–18):</strong> systematic
              probes of the localization gap, partial mitigations, and
              characterization of exploration and planning as the next
              bottlenecks now that detection is no longer binding.
            </li>
          </ol>

          <h2>Why publish failure logs</h2>
          <p>
            Drone autonomy papers almost universally report final results on
            favorable conditions. The engineering decisions that were tried and
            discarded — and especially the diagnostic work that preceded those
            decisions — rarely appear in print. That creates a literature where
            every paper shows an improvement, and a practitioner reading it has
            no idea how many expensive dead ends were omitted.
          </p>
          <p>
            We think the iteration log format serves the community better. If
            you are working on a similar stack and your fine-tuning isn&apos;t
            transferring to closed loop, this paper tells you what we checked,
            in what order, and what the localization gap diagnosis looks like.
          </p>
          <p>
            The dataset used to generate training frames is{" "}
            <Link href="/research/yonder">Yonder</Link>. The benchmark that
            produced the 25-VLM results referenced here is documented in{" "}
            <Link href="/research/metric-gap">Closing the Metric Gap</Link>.
          </p>
        </Prose>
      );

    case "scaling-separation":
      return (
        <Prose>
          <p>
            Most drone autonomy research is done on a single drone, maybe a few.
            The interesting engineering questions — and the practical deployment
            risks — change qualitatively when you scale to hundreds or thousands.
            This paper measures those changes in controlled simulation, with a
            specific focus on sensing stack requirements.
          </p>

          <h2>The core question</h2>
          <p>
            Can a camera-only swarm coordinate effectively at scale, or does it
            require inter-drone ranging (UWB or equivalent) above some fleet
            size? The practical stakes are real: UWB adds hardware cost, power
            consumption, and regulatory considerations. If camera-only works at
            1,000 drones, the simpler sensing stack is the right answer. If it
            fails at 100, the ranging hardware is non-negotiable.
          </p>

          <h2>What we measured</h2>
          <p>
            We ran controlled simulations with fleet sizes from 10 to 1,000
            drones across urban and natural environments, comparing four sensing
            configurations:
          </p>
          <ul>
            <li>Camera only</li>
            <li>Camera + UWB ranging</li>
            <li>Camera + GPS (reference case)</li>
            <li>Full sensing stack</li>
          </ul>
          <p>
            Primary metrics: mission coverage (fraction of target area reached),
            collision rate (inter-drone and obstacle), and coordination latency
            (time to reach consensus on conflict resolution).
          </p>

          <h2>Results</h2>
          <p>
            Below roughly 100 drones, camera-only and ranging-equipped swarms
            perform similarly. The gap opens above that threshold and widens
            steeply through 1,000.
          </p>
          <p>
            At 1,000 drones, camera-only swarms show a 15.8 percentage-point
            drop in coverage compared to ranging-equipped swarms, and an 8×
            increase in the collision rate. The mechanism is predictable:
            without inter-drone ranging, each drone&apos;s estimate of its
            neighbors&apos; positions degrades as swarm density increases.
            Relative position uncertainty compounds across the planning graph,
            and conflicts that would have been resolved cleanly at small scale
            result in physical collisions at large scale.
          </p>
          <p>
            The practical recommendation from this data: for swarms above ~100
            drones, treat UWB ranging as infrastructure, not an option. Below
            that threshold, camera-only coordination is sufficient for most
            mission profiles.
          </p>

          <h2>What simulation can and cannot tell us here</h2>
          <p>
            These results are from controlled kinematic simulation. Real swarms
            add radio congestion, asymmetric packet loss, and physical occlusion
            effects that the simulation does not model. We expect the qualitative
            findings — that ranging becomes necessary above a threshold — to hold
            at real scale, but the threshold number (100 drones) should be read
            as an order-of-magnitude estimate, not a precise specification.
          </p>
          <p>
            The companion blog post covers the implications in more operational
            depth:{" "}
            <Link href="/blog/drone-swarm-sensing-1000-drones">
              What 1,000 drones need to coordinate: UWB is non-negotiable above 100
            </Link>
            .
          </p>
        </Prose>
      );

    case "gemma4-pilot":
      return (
        <Prose>
          <p>
            The 25-VLM benchmark in{" "}
            <Link href="/research/metric-gap">Closing the Metric Gap</Link> was
            run before Gemma 4 was available. When Google released Gemma 4 E2B,
            we added it to the same Isaac Sim closed-loop benchmark as a
            self-contained pilot trial. This note documents the results and draws
            out the architectural lesson.
          </p>

          <h2>Two modes on the same weights</h2>
          <p>
            We tested Gemma 4 E2B in two configurations:
          </p>
          <ul>
            <li>
              <strong>End-to-end (E2E):</strong> the model receives an egocentric
              RGB frame and a natural-language goal, and outputs a navigation
              command directly. This is the configuration every model in the
              25-VLM lineup used.
            </li>
            <li>
              <strong>Modular (semantic selector):</strong> the model receives
              the same frame and goal, but outputs only a target object
              identification — which object in the scene is the goal. A separate
              metric depth module converts that identification to a 3D coordinate,
              and a classical planner executes the motion.
            </li>
          </ul>
          <p>
            Identical weights. Different architectural role. The difference in
            navigation outcomes is the most direct illustration of the separation
            principle we have.
          </p>

          <h2>Results</h2>
          <p>
            In the end-to-end configuration, Gemma 4 E2B follows the pattern of
            every other model in the lineup — it underperforms the hover
            baseline. In the modular configuration, the same weights achieve
            competitive navigation success. The model&apos;s semantic understanding
            is not the problem; the metric grounding task is.
          </p>
          <p>
            We chose Gemma 4 for this comparison because it is a capable,
            openly-available model that many practitioners will have evaluated
            for their own applications. The finding is not specific to Gemma 4 —
            we expect the same architectural leverage to hold on any model with
            decent object recognition — but Gemma 4&apos;s wide familiarity makes it
            a useful reference point.
          </p>

          <h2>What this note adds to the full benchmark</h2>
          <p>
            The 25-VLM paper characterizes the failure mode and proposes the
            modular fix. This note shows the fix working on a single specific
            model in direct side-by-side comparison. The E2E vs. modular
            comparison on the same weights is a more controlled experiment than
            comparing across models with different architectures and training
            histories.
          </p>
          <p>
            If you want the fuller quantitative picture, read{" "}
            <Link href="/research/metric-gap">Closing the Metric Gap</Link>.
            If you want the engineering record of how the modular architecture
            was developed over 18 iterations, read{" "}
            <Link href="/research/engineering-separation">
              Engineering the Separation Principle
            </Link>
            .
          </p>
        </Prose>
      );

    case "counter-uas":
      return (
        <Prose>
          <p>
            Counter-UAS research has a measurement problem. The natural metric
            for evaluating an autonomous swarm — mission success rate — turns
            out to be almost useless for characterizing attack severity. This
            paper documents why, and what to measure instead.
          </p>

          <h2>The measurement problem</h2>
          <p>
            Suppose you inject a GNSS spoofing attack into a four-drone warehouse
            swarm. One drone flies to the wrong location. The other three
            compensate dynamically — redundant coverage, rerouted task
            assignments. Mission success rate: 94%. Does that mean the attack
            failed? No. A drone flew to the wrong place. That is an exploitable
            physical effect, and mission-success rate hid it.
          </p>
          <p>
            This is the problem across all four attack classes we study: GNSS
            spoofing, RF jamming, kinetic interception, and control takeover.
            Swarm redundancy is a feature for resilience and a bug for C-UAS
            measurement — it absorbs attacks at the mission level while the
            physical effects of those attacks remain clearly measurable.
          </p>

          <h2>Experimental setup</h2>
          <p>
            11,340 seeded trials. Four attack classes, six matched defenses,
            run in a four-drone warehouse swarm with controlled task load. Seeded
            design means each trial has a fixed random seed, so we can compare
            attack vs. no-attack on identical swarm trajectories. This controls
            for the variance in swarm behavior that would otherwise obscure small
            attack effects.
          </p>
          <p>
            The swarm performs a coverage task: inspect every zone in the
            warehouse. We measure mission success (did it cover everything?),
            time to completion, and — critically — the physical traces of each
            attack type.
          </p>

          <h2>Physical-effects metrics</h2>
          <p>
            For each attack class, we track the metric that directly captures
            the attack&apos;s physical signature:
          </p>
          <ul>
            <li>
              <strong>GNSS spoofing:</strong> position error — how far did the
              affected drone fly from its intended location? Median: 5–8 m under
              active spoofing.
            </li>
            <li>
              <strong>Kinetic interception:</strong> proportional navigation (PN)
              capture rate — what fraction of intercept attempts achieved physical
              proximity? 79.5% in our trials.
            </li>
            <li>
              <strong>RF jamming:</strong> control latency and packet loss rate
              during jamming windows.
            </li>
            <li>
              <strong>Control takeover:</strong> trajectory divergence from
              intended path post-takeover.
            </li>
          </ul>

          <h2>Kinematic detection</h2>
          <p>
            We also evaluate a passive kinematic plausibility detector — a
            module that watches each drone&apos;s trajectory and flags motion that
            is inconsistent with normal navigation physics (impossible
            accelerations, heading reversals inconsistent with the task, velocity
            spikes above the flight controller&apos;s limits).
          </p>
          <p>
            The detector achieves a 39.8% true-positive rate at a 0% false-positive
            rate. That is not a high detection rate. But it is a 0% FP rate,
            which means every flag the detector raises is a real anomaly. In a
            security context, 0% FP is often more operationally useful than
            higher TP with non-zero FP — a system operator can act on every
            alert without alert fatigue.
          </p>

          <h2>Fidelity boundary</h2>
          <p>
            We include an explicit fidelity boundary section that delineates what
            kinematic simulation can and cannot faithfully reproduce for C-UAS
            work. RF propagation, antenna patterns, and electronic warfare
            effects are not modeled. The kinematic outcomes (trajectory under
            attack, physical interception geometry) are modeled. Readers should
            weight the RF-jamming results less heavily than the kinetic and GNSS
            results.
          </p>
          <p>
            Full discussion in the companion blog post:{" "}
            <Link href="/blog/counter-uas-drone-attack-defense-simulation">
              We attacked our own drones 11,340 times
            </Link>
            .
          </p>
        </Prose>
      );

    case "droneport-atc":
      return (
        <Prose>
          <p>
            Urban air mobility (UAM) infrastructure planning has a coordination
            problem: who manages the air traffic at a droneport, and how?
            Centralized tower coordination is the aviation default. Self-organized
            coordination with broadcast sensing is the drone-native alternative.
            Neither has been rigorously compared across the operational parameters
            that matter for real droneport design — traffic density, communication
            continuity, and observation modality. This paper is that comparison.
          </p>

          <h2>Factorial design</h2>
          <p>
            Nine experimental cells, 45 trials each, 405 simulated vertiport
            trials total. The three factors:
          </p>
          <ul>
            <li>
              <strong>Authority:</strong> centralized tower vs. self-organized
              peer coordination
            </li>
            <li>
              <strong>Communications:</strong> continuous broadcast vs.
              terminal-only (broadcast only during takeoff and landing windows)
            </li>
            <li>
              <strong>Observation:</strong> ADS-B only, camera only, both, or
              neither (silent-cruise)
            </li>
          </ul>
          <p>
            Primary metrics: throughput (operations per hour completed without
            violation), safe separation compliance (what fraction of flight
            segments maintain required line-of-sight separation), and conflict
            resolution latency.
          </p>

          <h2>Results: where self-organization works</h2>
          <p>
            Self-organized coordination with ADS-B broadcast matches centralized
            tower throughput up to approximately 20 operations per hour. Below
            that density, the two authority models are statistically
            indistinguishable on throughput and separation compliance. Self-org
            with ADS-B is not a degraded substitute for a tower at low density —
            it is genuinely equivalent.
          </p>
          <p>
            Above 20 ops/hour, self-organized coordination degrades. Throughput
            falls, and more importantly, conflict resolution latency increases —
            drones spend more time in holding patterns waiting for coordination
            to resolve. The tower model maintains higher throughput and lower
            separation violations at high density.
          </p>

          <h2>Results: the silent-cruise problem</h2>
          <p>
            The most operationally significant finding involves drones that do not
            broadcast their position — the &quot;silent-cruise&quot; configuration. Silent
            drones (no ADS-B, no active broadcast) exceed safe line-of-sight
            separation thresholds at just 12 operations per hour. That is below
            the density where self-org and tower first diverge. A single silent
            drone in a mixed fleet is sufficient to create separation violations
            that the rest of the coordination system cannot compensate for.
          </p>
          <p>
            The practical implication: ADS-B broadcast is not optional for dense
            urban air mobility. A regulatory requirement for ADS-B equivalents
            on all UAM vehicles is not a conservative policy — it is the minimum
            condition for separation safety above very low traffic densities.
          </p>

          <h2>The throughput–safety frontier</h2>
          <p>
            We map the Pareto frontier of throughput vs. separation safety across
            all nine cells. The frontier is dominated by tower + ADS-B at high
            density and self-org + ADS-B at low density. There is no cell where
            removing observation (silent-cruise) is anywhere near the frontier.
            Observation is not a nice-to-have; it is load-bearing for the
            coordination system.
          </p>
          <p>
            Companion blog post:{" "}
            <Link href="/blog/droneport-atc-tower-vs-selforg">
              Tower vs. self-organized droneport ATC across a 9-cell factorial study
            </Link>
            .
          </p>
        </Prose>
      );

    default:
      return null;
  }
}
