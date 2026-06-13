import type { ReactNode } from "react";
import Link from "next/link";
import { SITE } from "@/lib/site";

function Prose({ children }: { children: ReactNode }) {
  return (
    <div className="prose prose-invert max-w-none space-y-6 text-muted-foreground [&_h2]:mt-12 [&_h2]:mb-4 [&_h2]:text-2xl [&_h2]:font-semibold [&_h2]:text-foreground [&_h3]:mt-8 [&_h3]:mb-3 [&_h3]:text-lg [&_h3]:font-semibold [&_h3]:text-foreground [&_strong]:text-foreground [&_a]:text-amber-500 [&_a]:underline [&_ul]:list-disc [&_ul]:pl-6 [&_ul]:space-y-2 [&_ol]:list-decimal [&_ol]:pl-6 [&_ol]:space-y-2">
      {children}
    </div>
  );
}

export function BlogPostBody({ slug }: { slug: string }) {
  switch (slug) {
    case "how-to-make-autonomous-drones-smarter":
      return (
        <Prose>
          <p>
            If your goal is genuinely smarter autonomous drones, the fastest way
            to waste months is to treat a vision-language model (VLM) like a
            magic autopilot. In closed-loop flight, small grounding errors
            compound every time you replan. The result is often worse than doing
            nothing, which is why we benchmark end-to-end VLMs against a hover
            baseline and publish the tooling to reproduce those results.
          </p>
          <h2>Start in simulation, but validate in closed loop</h2>
          <p>
            Simulation is the right place to iterate quickly on APIs, frame
            conventions, planner edge cases, and perception plumbing. Astral
            documents two practical paths: lightweight ArduPilot SITL for
            SDK-level work, and Isaac Sim for perception-in-the-loop autonomy.{" "}
            <Link href="/docs/simulation">Run in simulation</Link> walks through
            both.
          </p>
          <p>
            The critical discipline is to separate &quot;runs in my notebook&quot;
            from &quot;flies reliably under physics.&quot; Closed-loop evaluation is
            non-negotiable for autonomy claims.
          </p>
          <h2>Use modular autonomy: semantics, geometry, safety</h2>
          <p>
            A pragmatic pattern for AI drone software is modular: let the VLM
            (or another semantic module) answer &quot;what&quot; the operator means,
            use metric depth and camera geometry to answer &quot;where&quot; in meters,
            and keep classical planning and safety layers responsible for
            &quot;how&quot; motion is executed. We call this the separation principle,
            and we document why it matters in our research notes on{" "}
            <Link href="/research">the metric gap</Link>.
          </p>
          <h2>Train perception on data that matches your evaluation domain</h2>
          <p>
            If you fine-tune detectors on one simulator&apos;s imagery and evaluate
            in another, you can get large offline improvements that do not move
            closed-loop success. That failure mode is exactly what the{" "}
            <Link href="/datasets/yonder">Yonder</Link> dataset and benchmark are
            designed to surface, alongside the public release on Hugging Face (
            <a href={SITE.yonderDataset}>astralhf/yonder</a>).
          </p>
          <h2>Ship software like a platform, not a demo</h2>
          <ul>
            <li>
              <strong>Open source core:</strong>{" "}
              <a href={SITE.astralSdk}>astral-sdk</a> and related repos on{" "}
              <a href={SITE.githubOrg}>GitHub</a>.
            </li>
            <li>
              <strong>Operator tooling:</strong> the Astral mobile apps for iOS
              and Android (links on the homepage).
            </li>
            <li>
              <strong>Documentation:</strong> product docs are in the open{" "}
              <a href={SITE.docs}>astral-docs</a> repository on GitHub.
            </li>
          </ul>
          <p>
            Astral is aiming to be the open autonomous drone software stack: credible
            benchmarks, datasets, and runnable code, not slideware. If you want
            the shortest path from reading to doing, install the SDK, run SITL,
            then turn on closed-loop trials in Isaac when you are ready to stress
            perception and planning together.
          </p>
        </Prose>
      );

    case "metric-gap-vision-language-drone-navigation":
      return (
        <Prose>
          <p>
            Vision-language models are excellent at parsing operator intent:
            object names, relations, negation, and visually grounded phrases.
            Drone navigation, however, requires something VLMs are not trained to
            deliver reliably: metric spatial grounding from a moving camera under
            physical dynamics.
          </p>
          <h2>What we measured</h2>
          <p>
            In our benchmark work (&quot;Closing the Metric Gap&quot;), we ran thousands
            of closed-loop quadrotor trials in NVIDIA Isaac Sim across many VLM
            architectures. The headline empirical pattern is a metric gap:
            direction can look good while distance to the target is wrong by
            meters. Under replanning, those errors diverge.
          </p>
          <h2>Why &quot;end-to-end VLM pilot&quot; fails first</h2>
          <p>
            Internet-scale image-text training does not give a model stable
            pixel-to-meter mapping for a drone camera at operational ranges. That
            is not a moral failure of VLMs; it is a supervision mismatch. Treating
            a VLM as a direct coordinate generator therefore stacks the hardest
            problem on the least appropriate module.
          </p>
          <h2>The separation principle</h2>
          <p>
            A better engineering contract is: VLMs for semantic target selection,
            dedicated depth and detection for metric localization, classical
            motion planning for feasible, collision-aware motion. The follow-on
            engineering paper documents how far that modular stack can be pushed,
            and where the next bottlenecks appear (exploration, multi-step plans,
            occlusions).
          </p>
          <h2>A clean single-model illustration</h2>
          <p>
            Our Gemma 4 pilot report shows the same weights deployed two ways:
            end-to-end goal prediction versus modular use as a target identifier.
            The gap is enormous, because the interface between &quot;language&quot; and
            &quot;geometry&quot; is doing real work.
          </p>
          <p>
            Read the full paper summaries on{" "}
            <Link href="/research">Research</Link>, and see runnable simulation
            setup on <Link href="/docs/simulation">Simulation</Link>.
          </p>
        </Prose>
      );

    case "yonder-drone-navigation-dataset":
      return (
        <Prose>
          <p>
            <a href={SITE.yonderDataset}>Yonder</a> is a large drone-perspective
            dataset for indoor navigation research. It is built to support serious
            perception training (detection, depth, semantics) and to make a
            specific evaluation failure mode obvious:{" "}
            <strong>offline metrics on one simulator can mis-rank models for
            closed-loop flight in another.</strong>
          </p>
          <h2>What is inside</h2>
          <p>
            The public release includes millions of frames across many indoor
            environments, with rich sensor arrays per waypoint (stereo RGB, depth,
            LiDAR-style sweeps, semantics, pose). Full details and layout are on
            the Hugging Face dataset card; start with the smoke subset{" "}
            <a href={SITE.yonderSample}>astralhf/yonder-sample</a> if you want a
            small download before committing to large transfers.
          </p>
          <h2>What Yonder is for (and not for)</h2>
          <ul>
            <li>
              <strong>Great for:</strong> training and studying drone-perspective
              perception, and diagnosing cross-simulator generalization when paired
              with a closed-loop evaluator.
            </li>
            <li>
              <strong>Not a substitute for:</strong> end-to-end policy training
              from expert trajectories; it is not packaged as behavior cloning
              data with full closed-loop rollouts.
            </li>
          </ul>
          <h2>Why this matters for AI drone software</h2>
          <p>
            The field has a habit of celebrating offline detection gains. Yonder
            includes the ingredients to show when those gains are real for flight
            and when they are an artifact of simulator-specific geometry and
            rendering conventions. If you care about trustworthy autonomy,
            publish both: offline metrics and closed-loop outcomes.
          </p>
          <p>
            Dataset hub: <a href={SITE.yonderDataset}>{SITE.yonderDataset}</a>
          </p>
        </Prose>
      );

    case "why-vlm-drones-cant-beat-hovering":
      return (
        <Prose>
          <p>
            We ran 10,200 closed-loop flight trials across 25 vision-language model architectures, and the result was
            uncomfortable: in aggregate, no end-to-end 7–8B parameter VLM reliably outperforms a drone that simply
            hovers in place. Not occasionally loses. Reliably loses. The hover baseline — zero motion, zero intelligence
            — sits at 9.50 m mean error. Our best end-to-end VLM result was 8.70 m. That is not a success story.
          </p>
          <p>
            This post explains why that happened, what the data shows about the root cause, and how a change in
            architecture — not model scale — closes the gap to 1.04 m on operational commands.
          </p>

          <h2>The Benchmark</h2>
          <p>
            The evaluation covered 153 distinct task trials organized into tiers by difficulty: stationary targets,
            semantic identification, visual grounding, occluded objects, and multi-step reasoning. Each trial ran
            closed-loop — the model received camera frames, issued waypoints, and the flight controller executed them
            in simulation. We measured final position error, step-1 prediction error, directional accuracy, and
            collision rate.
          </p>
          <p>
            The hover baseline exists because it represents the correct null hypothesis for drone AI. If your model
            cannot beat standing still, it is not ready for deployment. A hovering drone risks nothing, damages
            nothing, and stays at a known position. Any system that moves must justify that motion with accuracy
            gains. Across our 25 tested architectures, most could not.
          </p>
          <p>
            The models tested spanned the current frontier: Gemini 3 Flash, Qwen 2.5 VL, Gemma 4, LLaVA variants,
            InternVL, and others in the 2B–8B active-parameter range. All were evaluated as direct image-plus-text
            to waypoint controllers — the standard end-to-end framing where the VLM receives a frame and a natural
            language goal, then outputs a 3D target position for the flight controller to reach.
          </p>

          <h2>The Metric Gap</h2>
          <p>
            The failure has a specific name and a specific cause. We call it the metric gap, and understanding it
            requires separating two distinct capabilities: directional reasoning and distance estimation.
          </p>
          <p>
            VLMs are surprisingly good at direction. Across the benchmark, models achieved 0.83–0.91 directional
            cosine accuracy — meaning when asked to fly toward a forklift, they correctly identify which way to turn
            roughly 85–90% of the time. That is genuine capability built from internet-scale visual pretraining.
          </p>
          <p>
            VLMs are catastrophically bad at distance. The same models produce 6–10 m distance errors on targets
            that are 4–12 m away. The drone understands it needs to go left and forward, but has no reliable sense
            of whether the target is 3 m away or 15 m away. It guesses, and the guess is often wrong by a factor of two or three.
          </p>
          <p>
            The root cause is training data. VLMs are trained on internet images, which contain no metric depth
            supervision. Models learn relative scale but not absolute metric mapping from pixel geometry to
            real-world distances. When asked to produce a waypoint in meters, they are extrapolating from a signal
            they never directly learned.
          </p>

          <h2>What the Numbers Look Like</h2>
          <p>
            Hover baseline: 9.50 m mean error, 100% collision-free. Best end-to-end VLM: Gemini 3 Flash at 8.70 m —
            barely clearing hover by 0.80 m. Other frontier models range 8.70–13.69 m. Several are meaningfully
            worse than hovering.
          </p>
          <p>
            Closed-loop degradation compounds the problem. Qwen 2.5 VL&apos;s first prediction error is 8.11 m —
            marginal, but not catastrophic. By the end of a closed-loop trial, cumulative error reaches 38.64 m.
            The model anchors on its initial spatial estimate and compounds the error with each replanning step.
          </p>

          <h2>Gemma 4 Confirms the Pattern</h2>
          <p>
            Gemma 4 E2B as end-to-end controller: 47.78 m final error, 17.8 s/step latency, 67% collision rate.
            The same Gemma 4 weights inside the modular pipeline as a target-selector: 9.35 m — beating hover.
            The end-to-end versus modular gap for the identical model is 38.4 m. The model is not the problem.
            The architecture is. Full results at <Link href="/research#gemma4-pilot">research: Gemma 4 pilot</Link>.
          </p>

          <h2>What Fixed It: The Separation Principle</h2>
          <p>
            The solution is not a better VLM. It is a different question asked of the VLM. The modular Track A
            architecture separates responsibilities explicitly:
          </p>
          <ul>
            <li>
              <strong>VLM handles semantics only.</strong> Given a natural language goal, the VLM outputs a target
              label or bounding box selection — no coordinates, no distances.
            </li>
            <li>
              <strong>Depth Anything V2 handles geometry.</strong> A dedicated monocular depth model maps pixel
              distances to real-world meters — the supervision the VLM never received.
            </li>
            <li>
              <strong>Geometric planning handles navigation.</strong> Given the semantic selection and the metric
              depth estimate, a classical geometry solver computes the safe waypoint.
            </li>
          </ul>
          <p>
            Track A achieves 1.04 m mean error on operational commands. Across the full 153-trial benchmark: 9.98 m
            vs. hover&apos;s 9.50 m — a deliberate 0.48 m tradeoff to maintain 100% collision-free flight. Runs on a
            Jetson Orin Nano at ~$1,350 sensor cost.
          </p>

          <h2>What This Means for Operators</h2>
          <p>
            Ask any vendor for closed-loop trial data, not demo videos. Ask for collision rates on the full test set.
            Ask whether the architecture separates semantics from geometry. Ask what the system does on unsolved task
            types — no current system handles occluded target search or multi-step reasoning reliably, and any
            system claiming otherwise should provide per-tier data.
          </p>
          <p>
            Full benchmark methodology, per-tier results, and architecture specifications are at{" "}
            <Link href="/research#metric-gap">research: metric gap</Link>.
          </p>
        </Prose>
      );

    case "engineering-drone-autonomy-18-iterations":
      return (
        <Prose>
          <p>
            We trained a drone navigation model on 6.7 million frames of drone-perspective footage, fine-tuned two
            state-of-the-art detectors, and achieved a 9.7× improvement in detection accuracy. Then we deployed it —
            and performance was identical to the untrained baseline. Every single time.
          </p>
          <p>
            That result, replicated across four separate fine-tuning attempts, is the most important thing we learned
            in eighteen iterations of autonomous drone navigation research. This post documents the full arc — what we
            built, what broke, what the data actually said, and what we would do differently.
          </p>

          <h2>Where We Started</h2>
          <p>
            The initial modular pipeline result: 9.98 m aggregate error. Hover baseline: 9.50 m. We had built a
            complex system that performed worse than doing nothing. The pipeline had to beat hover. Until it did,
            nothing else mattered.
          </p>

          <h2>Phase I: Pipeline Engineering (Iterations 3–11)</h2>
          <p>
            Six targeted fixes moved aggregate error from 9.98 m to 8.15 m with &gt;90% collision-free flight:
            instruction decomposition, domain-specific detection priming, failure detection, spatial semantic memory,
            active yaw-based perception, and environment-specific safety profiles.
          </p>
          <p>
            One diagnostic finding from this phase: mock evaluation overestimated performance by 6.5× compared to
            closed-loop results. If you are benchmarking drone navigation against replayed data rather than live
            closed-loop runs, your numbers are not real.
          </p>

          <h2>The ZeroClaw Dataset</h2>
          <p>
            We built ZeroClaw: 6.7 million frames from drone-perspective viewpoints across 275 indoor environments
            with 32 million COCO-format bounding box annotations. Fine-tuning on ZeroClaw produced a 9.7×
            detection mAP improvement: 4.8% → 46.7%. By any offline measure, this solved detection.
          </p>
          <p>
            Except it did not. The dataset is available at <Link href="/datasets/yonder">astral.us/datasets/yonder</Link>.
          </p>

          <h2>The Domain Gap Trap</h2>
          <p>
            Four fine-tuning runs. Four different hyperparameter configurations. One result each time: 23–25%
            navigation success rate, statistically indistinguishable from the zero-shot baseline. The 9.7× mAP
            improvement produced exactly zero improvement in closed-loop navigation.
          </p>
          <p>
            Root cause: cross-simulator domain gap. ZeroClaw was generated in Habitat-Sim. Evaluation runs in Isaac
            Sim. These simulators render the same scenes differently, and detectors learned the Habitat-Sim
            distribution, not the underlying world. The diagnostic failure mode: the drone produced negative-Z goal
            predictions — interpreting its target as below the floor — and immediately pitched into the ground.
            This happened consistently across all four fine-tuned checkpoints. Zero-shot models never did this.
          </p>
          <p>
            The implication: training data generated in one simulation platform cannot transfer to a different
            simulation platform for closed-loop navigation, even when scenes and categories are identical.
            Offline mAP measured in a different renderer is not a proxy for navigation performance.
          </p>

          <h2>Phase III: Exploration Was the Real Bottleneck</h2>
          <p>
            75% of target objects were not visible from the drone&apos;s spawn position. Detection quality was
            irrelevant until visibility was established. A learned exploration policy achieved 6.9% validation
            accuracy versus 8.3% random chance — it learned the wrong thing. A depth-based heuristic produced
            +5.2 pp improvement, p=0.168. Not significant.
          </p>
          <p>
            Across all configurations, success rate converged to 23–25%. The tasks that produced 0% success
            shared one property: they required reasoning, not perception. Spatial relational queries, negation,
            multi-step planning, and occluded target inference are architectural limitations, not data limitations.
          </p>

          <h2>What Actually Worked</h2>
          <p>
            At n=4 drones cooperatively, the modular pipeline achieved 70–72.5% success versus 5% for zero-shot
            VLM baseline (p=0.002). Multi-agent coverage, not shared perception, is the source of that gain.
            That result is robust and does not depend on the single-agent ceiling being solved.
          </p>

          <h2>What We Would Do Differently</h2>
          <ul>
            <li>Validate synthetic data in the evaluation environment, not a different one.</li>
            <li>Run closed-loop evaluation early and often — it is the only number that is real.</li>
            <li>Profile your benchmark before optimizing perception: if 75% of targets are invisible from spawn,
              improving detector mAP by 10× will not move your success rate.</li>
          </ul>
          <p>
            Full technical detail: <Link href="/research#engineering-separation">research documentation</Link>.
            ZeroClaw dataset: <Link href="/datasets/yonder">astral.us/datasets/yonder</Link>.
          </p>
        </Prose>
      );

    case "drone-swarm-sensing-1000-drones":
      return (
        <Prose>
          <p>
            Scale a drone swarm from 32 to 1,000 units and something breaks. Not gradually — it breaks hard.
            Camera-only coordination falls 15.8 percentage points below the omniscient baseline at 1,000 drones
            and produces <strong>8× more collisions</strong>: 3,620 versus 452 in the same mission. That is not
            a tuning problem. It is an architectural one. UWB ranging is non-negotiable above roughly 100 drones.
          </p>

          <h2>Four Sensing Configurations</h2>
          <ul>
            <li><strong>Camera-only:</strong> onboard vision for neighbor detection and obstacle avoidance.</li>
            <li><strong>UWB-only:</strong> ultra-wideband radio ranging — precise distances, independent of lighting or occlusion.</li>
            <li><strong>Hybrid (camera + UWB):</strong> both modalities combined.</li>
            <li><strong>Omniscient baseline:</strong> perfect ground-truth position knowledge — the ceiling.</li>
          </ul>
          <p>
            At 32 drones the differences are negligible. Camera-only achieves 57.5% coverage, nearly matching the
            57.6% omniscient ceiling. This is the regime where most drone swarm demos live. The problem is not
            visible yet.
          </p>
          <p>
            By 200 drones, camera-only has opened a 19.2 percentage-point gap (40.0% vs. 59.2%, p&lt;0.001). UWB
            and Hybrid stay within 1–2 pp of omniscient at every scale. The separation is structural: cameras cannot
            maintain reliable neighbor awareness when mutual occlusion becomes the norm. UWB ranges through everything.
          </p>

          <h2>The Urban Penalty</h2>
          <p>
            Urban environments impose a 14–32 percentage-point coverage penalty versus open forest across all swarm
            sizes and sensing configurations. Two effects compound: visual range is effectively halved (30 m → 15 m
            in city clutter), and 22% of the map is physically occupied by buildings — coverage saturates at ~28%
            regardless of mission time.
          </p>
          <p>
            At 1,000 drones, city environments produce 8–11× more collisions than forest under identical sensing.
            Camera-only in a city at scale is not just suboptimal — it is operationally unacceptable.
          </p>

          <h2>Hybrid Squad Coordination: Centralized Performance at 3.3× Less Overhead</h2>
          <p>
            In 16-drone, 8-target experiments:
          </p>
          <ul>
            <li><strong>Centralized:</strong> 95.0±6.1% detection, 3.15 s reaction, 192 msg/s.</li>
            <li><strong>Decentralized:</strong> 85.0±5.0% detection, 0.44 s reaction.</li>
            <li><strong>Hybrid squad-based:</strong> 95.0±6.1% detection (matching centralized), 1.08 s reaction, 58 msg/s — 3.3× less communication overhead.</li>
          </ul>
          <p>
            For a 1,000-drone fleet: centralized at 192 msg/s per node generates ~192,000 messages/s fleet-wide;
            hybrid brings that to ~58,000 msg/s. At 200 bytes per message, that is the difference between 38 Mbps
            and 11 Mbps of coordination traffic before any payload data.
          </p>

          <h2>Resilience</h2>
          <p>
            <strong>GPS denial:</strong> with UWB present, coverage drops only 1.3 pp in city. Fleets carrying
            only GPS lose this fallback entirely.
          </p>
          <p>
            <strong>Communications denial:</strong> any architecture that relies on shared state collapses to
            camera-only performance. UWB hardware provides no benefit when the ranging data cannot be shared.
          </p>
          <p>
            <strong>75% drone attrition:</strong> forest coverage holds at 87.7%; city drops to 63.8%.
            Urban operations require 30–40% more drones to maintain equivalent resilience under attrition.
          </p>

          <h2>VLMs on Edge Hardware</h2>
          <p>
            SmolVLM-2B on Orin Nano (210 ms latency, 5.06 GB VRAM) provides a 25 pp detection advantage under total
            comms denial in forest (62.5% vs. 37.5%, p&lt;0.05). In city environments, this advantage disappears:
            building occlusion prevents the scene context VLM reasoning depends on. Spend those resources on UWB
            hardware and better mesh radio for urban operations.
          </p>

          <h2>Requirements Summary</h2>
          <p><strong>100-drone fleet:</strong> UWB on every airframe (~$20–50/unit), hybrid squad organization
            (squads of 8–16), mesh radio for 58+ msg/s per drone, GPS primary / UWB fallback.</p>
          <p><strong>1,000-drone fleet:</strong> UWB without exception, tiered coordination hierarchy (squad →
            sector → global), 30–40% reserve capacity for urban attrition, comms-denial firmware on every unit.
          </p>
          <p>
            Full methodology and per-configuration data: <Link href="/research#scaling-separation">Scaling the
            Separation Principle</Link>.
          </p>
        </Prose>
      );

    case "counter-uas-drone-attack-defense-simulation":
      return (
        <Prose>
          <p>
            None of the attacks worked — not the way you&apos;d expect.
          </p>
          <p>
            Over several weeks, we ran 11,340 simulation trials against our autonomous warehouse-search drone stack.
            We hit it with GNSS spoofing, RF jamming, proportional-navigation interceptors, and direct control
            takeover. Varied seeds, tasks, defense configurations. Tracked every trial.
          </p>
          <p>
            The headline result: <strong>no attack degraded mission success rate beyond the 95% confidence
            interval.</strong> One attack even inflated it. If you stopped reading here, you&apos;d conclude our
            drones are basically impervious to adversarial interference. That conclusion would be wrong — and the
            gap between what the numbers say and what they mean is exactly why we&apos;re writing this.
          </p>

          <h2>Mission Success Rate Is the Wrong Metric for C-UAS</h2>
          <p>
            This is the central finding. Every attack profile we tested returned a mission success rate within
            the confidence interval of baseline (57.6% ±6.7 pp). GNSS walk-off: +1.2 pp. RF jamming: 0.0 pp.
            PN interceptor: 0.0 pp.
          </p>
          <p>
            Control takeover <em>improved</em> success rate by +8.1 pp. The attacker&apos;s redirect goal happened
            to sit closer to the mission target in ~8% of trials. The drone got hijacked and accidentally completed
            more tasks.
          </p>
          <p>
            None of this means the attacks did nothing. It means mission success rate doesn&apos;t capture what
            C-UAS threats actually do. The right metrics are physical: capture percentage, maximum position error,
            detection latency, minimum miss distance.
          </p>

          <h2>What the Four Attack Types Actually Do</h2>
          <h3>GNSS Walk-Off</h3>
          <p>
            Smooth-capture spoofing: position blends from true toward attacker-controlled offset over 5 seconds.
            Velocity is left untouched — preserving the IMU signal that enables dead-reckoning detection. Physical
            result: <strong>5.14 m mean maximum position error</strong> under single-axis walk-off.
            Combined with RF jamming: <strong>7.95 m</strong>.
          </p>
          <h3>RF Jamming</h3>
          <p>
            Packet loss (0.5–0.9) and latency (0.2–0.8 s) injected into the communication channel. Produces no
            measurable change in single-drone mission success. The jam failsafe — RTL on 3 s of neighbor silence —
            returns 0.0% false positives on clean baseline.
          </p>
          <h3>Control Takeover</h3>
          <p>
            Direct override of <code>action.target_position</code>. The goal-bounds defense (reject targets
            outside ±30 m geofence) stops this completely. False positives on clean baseline: 0.0%.
          </p>

          <h2>Why PN Interceptors Are the Real Threat</h2>
          <p>
            Proportional-navigation guidance (N=3.5, v_max=8 m/s, net capture radius 1.5 m) achieves a{" "}
            <strong>79.5% capture rate</strong> with no defense active. Minimum miss distance: 0.11 m.
            When RF jamming is layered on: capture rate still 79.5%. Physical interception geometry is not
            sensitive to communication degradation.
          </p>
          <p>
            This is the metric that matters. A 79.5% probability your drone gets physically removed from the
            air in a single engagement is operationally decisive. No mission success rate figure captures that.
          </p>

          <h2>Defenses: What Works</h2>
          <p>
            <strong>Plausibility detection:</strong> dead-reckoning vs. GPS position; flag when divergence &gt;3 m.
            Under single walk-off: <strong>39.8% TP</strong>. Under combined GNSS+RF jam: <strong>79.5% TP</strong>
            (the jam forces faster spoofing, making detection easier). False positives on clean baseline:{" "}
            <strong>0.0%</strong>.
          </p>
          <p>
            Critical implementation lesson: the DR integrator must use elapsed wall time between controller calls
            (2 s), not the physics timestep (0.05 s). Using 0.05 s produced a <strong>75.8% false-positive
            rate</strong>. This is an easy bug to introduce in any system where physics dt differs from control rate.
          </p>
          <p>
            <strong>All defenses stacked:</strong> 0.0% FP on clean baseline, +1.9 pp mission success overhead.
            Properly tuned defenses impose no operational cost.
          </p>

          <h2>The Fidelity Boundary</h2>
          <p>
            <strong>Reliably testable in kinematic sim:</strong> GNSS position error, PN capture geometry, control
            takeover, jam failsafe, plausibility detection, goal-bounds enforcement.
          </p>
          <p>
            <strong>Not testable without hardware or RF simulation:</strong> carrier-phase GPS, RAIM, Galileo OSNMA
            authentication (operational since July 2025), barrage vs. reactive jamming physics, EKF lock-pull,
            camera/radar interception detection, aerodynamic evasion effectiveness.
          </p>
          <p>
            The evasion defense triggered at 0% — not because evasion is ineffective, but because interceptors are
            not inserted into the drone&apos;s onboard sensor list in the current harness. That is a simulation
            fidelity gap, not a finding about evasion.
          </p>

          <h2>Practical Takeaways</h2>
          <ul>
            <li>Don&apos;t use mission success rate as your primary C-UAS metric. Track capture rate and position error separately.</li>
            <li>PN interceptors are your highest-priority threat — 79.5% capture against non-maneuvering drones.</li>
            <li>Plausibility detection is deployable today at 0% FP if you use the right clock.</li>
            <li>Goal-bounds enforcement stops control takeover with zero performance cost.</li>
            <li>Jam failsafe (RTL on link silence) should be standard firmware on every drone.</li>
            <li>Update your GNSS threat model for OSNMA — capable adversaries now layer RF denial on top of spoofing.</li>
          </ul>
          <p>
            Full paper with methodology and all tables: <Link href="/research#counter-uas">research: C-UAS study</Link>.
          </p>
        </Prose>
      );

    case "human-in-loop-drone-autonomy-94-percent":
      return (
        <Prose>
          <p>
            In a recent series of experiments across 28 test configurations, we measured something drone autonomy
            researchers rarely publish plainly: the gap between what an AI system can do alone and what it can do
            with a human in the loop. For autonomous drone operations, that gap was 36.8 percentage points — the
            difference between 57.6% and 94.4% mission success rate.
          </p>
          <p>
            That number deserves unpacking. Not because it argues against autonomy — it doesn&apos;t — but because
            it tells you exactly where to put the human.
          </p>

          <h2>The Experiment: Human-in-the-Loop Split (S14)</h2>
          <p>
            36 trials comparing a standalone LLM coordinator against a human-in-the-loop split coordinator that
            retained the LLM for planning and execution but escalated ambiguous decisions to a human operator:
          </p>
          <ul>
            <li><strong>Autonomous LLM:</strong> 16.7% success rate, 0.0% all-target completion</li>
            <li><strong>HIL split:</strong> 94.4% success rate, 61.1% all-target completion, 7.261 s wall time</li>
          </ul>
          <p>
            The all-target metric requires completing every objective in a multi-target task. The jump from 0% to
            61.1% shows the autonomous system was failing at task planning in uncertain scenarios — not just being
            slow or imprecise. Human judgment fixed that structurally.
          </p>
          <p>
            The HIL coordinator did not give the human continuous control. It surfaced decisions at branch points
            where sensor data was ambiguous or model confidence dropped below threshold. Total human engagement
            per trial was measured in seconds, not minutes.
          </p>

          <h2>When Full Autonomy Works: Rover Operations (S16)</h2>
          <p>
            Ground vehicle navigation on known terrain: 100% success, 100% all-target, 1.976 m mean error, 0.427 s
            wall time. No human in the loop. Perfect performance.
          </p>
          <p>
            The difference from S14 is task structure, not model quality. Constrained 2D navigation in a known
            environment matches the LLM&apos;s training distribution. Aerial swarm coordination in uncertain
            environments does not. The right level of human oversight is determined by task novelty and
            environmental ambiguity, not by a global policy.
          </p>

          <h2>When AI Teams Outperform: Team Training (S19)</h2>
          <p>
            Fine-tuned team-level models in 45 trials:
          </p>
          <ul>
            <li><strong>sector_search_coordinator, 3 drones:</strong> 100% SR, 2.790 m, 0.712 s</li>
            <li><strong>parallel_llm_coordinator, 3 drones:</strong> 100% SR, 2.811 m, 0.240 s</li>
            <li><strong>Gemma (smaller model):</strong> 20% SR, 15.7 s latency</li>
          </ul>
          <p>
            The near-identical accuracy at 3× speed difference (0.712 s vs. 0.240 s) shows parallel inference as
            an architectural choice, not an optimization. The Gemma comparison is the warning: deploying an
            undersized model on cost grounds produces a 5× latency penalty and 80% failure rate on complex tasks.
          </p>
          <p>
            S19 also shows that team training closes the gap that required HIL in S14. When the model has been
            trained on the distribution of problems it will encounter, it doesn&apos;t need the human at the branch
            point anymore. Same task class, different model preparation, 83.3 pp difference in success rate.
          </p>

          <h2>Parallel Inference as Architecture (S20)</h2>
          <p>
            45 trials isolating parallel inference vs. sequential:
          </p>
          <ul>
            <li><strong>Sequential:</strong> 40% SR, 4.483 m, 5.573 s</li>
            <li><strong>Parallel n=2:</strong> 100% SR, 1.890 m, 1.610 s</li>
            <li><strong>Parallel n=3:</strong> 100% SR, 1.849 m, 0.392 s</li>
          </ul>
          <p>
            14× speedup from sequential to n=3 parallel, with accuracy improving by half. Sequential and parallel
            coordination are not on the same performance curve — they are in different capability tiers. Sub-400 ms
            end-to-end inference enables dynamic obstacle response and live target tracking. 5.5 s does not.
          </p>

          <h2>When to Trust the Drone, When to Keep a Human</h2>
          <p>
            <strong>Full autonomy is appropriate when:</strong> the task is routine and well-characterized; the
            model has been fine-tuned on that specific task class; failure modes are bounded and recoverable;
            you have empirical success rate data from real trials.
          </p>
          <p>
            <strong>Human oversight is non-negotiable when:</strong> conditions are novel or sensors are returning
            ambiguous readings; multi-target completion is required in uncertain environments; you are operating
            at the edge of the training distribution; consequences of failure are asymmetric.
          </p>
          <p>
            The transition should be dynamic, not static. A system that applies fixed human-oversight policy will
            either over-rely on humans for routine tasks or under-rely on them for novel ones. The architecture
            that works surfaces only the decisions that need human judgment — briefly, at the right moment.
          </p>
          <p>
            The 94.4% number is real. So is the 0% all-target success rate for the baseline autonomous system on
            the same tasks. Both numbers are from the same experimental series. Together they define the design
            space.
          </p>
          <p>
            Full methodology: <Link href="/research">research section</Link>. HIL and parallel inference
            implementation guidance: <Link href="/docs">documentation</Link>.
          </p>
        </Prose>
      );

    case "droneport-atc-tower-vs-selforg":
      return (
        <Prose>
          <p>
            Every urban air mobility operator faces the same question before they build their first vertiport: do you put a centralized tower in charge, or let drones negotiate landing slots peer-to-peer? The answer determines infrastructure cost, safety margins, and how the system degrades when traffic spikes.
          </p>
          <p>
            We ran a nine-cell factorial study to find out. Three factors, nine combinations, 30 seeds per cell across five traffic loads. The headline: <strong>self-organized coordination with ADS-B matches tower throughput below roughly 20–25 ops/hour — then falls behind sharply as load increases.</strong> And drones flying silent (no ADS-B, no tower) exceed safe separation thresholds at just 12 ops/hour.
          </p>
          <h2>The three factors</h2>
          <p>
            The study crosses coordination <strong>authority</strong> (centralized tower vs. fully decentralized self-organization), <strong>communications regime</strong> (continuous telemetry vs. terminal-only — silent during cruise), and <strong>observation modality</strong> (ADS-B cooperative broadcast, camera-only, sensor fusion, or nothing). Nine of the possible sixteen combinations are operationally meaningful; those are the cells we ran.
          </p>
          <p>
            E2 — tower, continuous, ADS-B — is the control. E9 — self-organized, terminal-only, no broadcast — is the stress floor. Everything else lives between them on the throughput–safety Pareto frontier.
          </p>
          <h2>Reference cell: tower + ADS-B + continuous</h2>
          <p>
            The E2 reference cell at light load (5 ops/hour) achieves <strong>140 ± 28 ops/hour completed throughput, zero loss-of-separation events, and 12.6 m minimum pairwise separation</strong> at 0.078 messages/second. The tower consistently clears arriving drones before any queuing builds. This is the ceiling.
          </p>
          <h2>Where self-organization works (and where it doesn't)</h2>
          <p>
            Self-organized coordination with ADS-B (E6) matches E2 at light load. Drones broadcast intent, negotiate pad claims by priority, and clear the holding circuit without central authority. The distributed protocol handles low-density traffic well.
          </p>
          <p>
            Above the crossover load (~20–25 ops/hour for a six-pad configuration), the gap opens. The tower's global slot optimizer prevents approach conflicts that the distributed back-off protocol can only resolve by delaying one drone — compounding under sustained load. Self-org without broadcast (E7, E9) is worse: without visibility of neighbors mid-route, separation events rise steeply past 12 ops/hour.
          </p>
          <h2>The silent-cruise trap</h2>
          <p>
            Terminal-only communications (drones broadcast only in the approach and departure zones, silent during cruise) is attractive for bandwidth. At 5 ops/hour the cost is small. At 40 ops/hour, the absence of mid-air deconfliction triggers measurable LoS increases. Self-org + terminal + no broadcast (E9) — the configuration that costs the least to deploy — is also the most dangerous at commercial load targets.
          </p>
          <h2>What this means for vertiport design</h2>
          <p>
            The Pareto-efficient designs for high-density droneports all share two properties: centralized pad scheduling and cooperative surveillance (ADS-B or fusion). Self-organization is the right architecture for low-density corridors where infrastructure cost dominates. The crossover point — roughly 20 ops/hour for six pads — is the design threshold operators should use when choosing between the two architectures.
          </p>
          <p>
            The broadcast necessity threshold (LoS exceeds 0.01 events/op around 12 ops/hour in no-broadcast cells) maps directly to a regulatory question: at what traffic density does Remote ID / ADS-B become mandatory? This study argues the answer is well below commercial delivery density, which is where most UAM operators intend to operate.
          </p>
          <p>
            Full experimental design, metrics, and simulation methodology: <Link href="/research#droneport-atc">research section</Link>. The kinematic harness and all nine cells are available at the{" "}
            <a href={SITE.githubOrg} target="_blank" rel="noopener noreferrer">Astral GitHub</a>.
          </p>
        </Prose>
      );

    case "domain-detector-aerial-autonomy":
      return (
        <Prose>
          <p>
            COCO-80 has no class for drone. No class for person_aerial. No class
            for landing_pad or powerline. That is not a gap we can paper over with
            prompt engineering — it is a hard limit of the label set the weights
            were trained on. So we replaced it.
          </p>
          <p>
            Over three training rounds we built a 9-class domain detector
            (drone, person_aerial, vehicle, bicycle_motorcycle, landing_pad,
            powerline_pole, person, animal, boat) tuned for the aerial autonomy
            use case. We trained on 48,000 images, used{" "}
            <a href="https://github.com/ultralytics/ultralytics" target="_blank" rel="noopener noreferrer">
              YOLOv8n
            </a>{" "}
            as the base, and learned something important along the way: the
            biggest failure mode is not the architecture, the learning rate, or
            the number of epochs. It is class imbalance. One of our classes
            collapsed to near-zero during training because we did not catch it.
          </p>
          <h2>The setup: why aerial perception is different</h2>
          <p>
            A drone camera sees the world at angles and scales COCO was not
            designed for. People become small blobs at 30 m altitude. Vehicles
            look like rectangles with no distinguishing features. A drone seen
            by another drone is a 15-pixel smear. Standard ImageNet-pretrained
            detectors handle the common cases (full-frame people, large cars from
            the side) but fail on the overhead/oblique aerial regime.
          </p>
          <p>
            We needed a detector that could fire on exactly those cases: distant
            people from altitude, vehicles from above, and — critically — other
            drones in the frame for counter-UAS and deconfliction.
          </p>
          <h2>Round 1: 18,000 sim frames from Isaac</h2>
          <p>
            We started with pure simulation data. Using NVIDIA Isaac Sim we
            generated 18,000 labeled frames across three indoor environments
            (office, warehouse, hospital), scripting drone trajectories and
            projecting known object poses through the camera model to produce
            perfect bounding boxes with no human labeling.
          </p>
          <p>
            The results looked great: mAP50 = 0.471, vehicle AP50 = 0.895,
            person AP50 = 0.894. We ran at 55 FPS on an RTX 5090 (18 ms per
            frame) with an ONNX-exported model. We were cautious about the
            numbers — sim val is easy; there is no sensor noise, no compression
            artifacts, no lighting variation beyond what we scripted.
          </p>
          <p>
            But person_aerial AP50 was 0.047 and drone AP50 was 0.047. The sim
            scenes did not have enough examples of those two classes at aerial
            angles to learn them well.
          </p>
          <h2>Round 2: 343,000 free labels from VisDrone — and the collapse</h2>
          <p>
            <a href="https://github.com/VisDrone/VisDrone-Dataset" target="_blank" rel="noopener noreferrer">
              VisDrone2019-DET
            </a>{" "}
            is a public aerial-footage dataset with 6,471 train and 548 val
            images, 382,000 annotated boxes shot from commercial drones over
            urban scenes. Person_aerial, vehicle, bicycle, motorcycle — all
            labeled. Free real-world distribution. We merged it in.
          </p>
          <p>
            Our merged training set grew to 24,500 images and 364,000 boxes.
            Person_aerial jumped from 0.047 to 0.377 AP50. Vehicle held at
            0.755. Bicycle_motorcycle appeared at 0.365. These were genuinely
            impressive gains.
          </p>
          <p>
            Drone AP50 dropped from 0.047 to 0.010. The class had not just
            stagnated — it had nearly vanished.
          </p>
          <h2>The diagnosis: 3.5% is not enough</h2>
          <p>
            VisDrone has no drone-as-target labels. The dataset is shot by
            drones looking down at streets; there are no other drones in the
            frame. When we merged our 12,000 drone boxes from sim into a
            343,000-box VisDrone dataset, drones became 3.5% of the total box
            count. The model learned to ignore the class.
          </p>
          <p>
            The fix was oversampling. We duplicated every training image
            containing a drone box 4× so that drone-containing images accounted
            for roughly 10% of training samples. The training set grew from
            21,471 to 48,717 images.
          </p>
          <h2>Round 3: the oversampled result</h2>
          <p>
            mAP50 on the real-world VisDrone val set: 0.384. Person_aerial: 0.360.
            Vehicle: 0.752. Bicycle_motorcycle: 0.339. Drone: 0.087.
          </p>
          <p>
            That 0.087 drone AP50 needs context. The VisDrone val set has zero
            drone-as-target images; every drone detection at validation time
            comes from our sim val frames only. So 0.087 is precision when the
            model fires on drone boxes — not coverage across a large val set. It
            is meaningful: the v2 model had precision near zero. The v3 model
            fires correctly when it sees a drone.
          </p>
          <h2>What the numbers actually mean</h2>
          <p>
            The headline mAP50 trajectory — 0.471 (v1) → 0.376 (v2) → 0.384
            (v3) — looks like a small net improvement from pure sim to merged
            training. It obscures the real story:
          </p>
          <ul>
            <li>
              <strong>V1&apos;s 0.471 was on an easy sim-only val set.</strong> The
              model had never seen a real image. Those numbers do not transfer.
            </li>
            <li>
              <strong>V2&apos;s 0.376 is on a hard real-world val set</strong> with
              38,000 boxes from actual drone footage. That is a genuine gain,
              despite looking like a regression in absolute mAP.
            </li>
            <li>
              <strong>V3&apos;s 0.384 adds drone recovery</strong> without sacrificing
              the real-world aerial classes. We got drone back without breaking
              person_aerial or vehicle.
            </li>
          </ul>
          <h2>Three things we learned</h2>
          <p>
            <strong>Sim mAP lies at small sample counts.</strong> A 0.471 on
            18,000 sim frames with a clean sim val set is not a 0.471 in the
            wild. Evaluate on out-of-distribution data before claiming any
            number.
          </p>
          <p>
            <strong>Free labels are worth the class-imbalance fight.</strong>{" "}
            VisDrone gave us 343,000 aerial-domain boxes at zero labeling cost.
            The collapse it caused was real but diagnosable and fixable. The
            net result — a model that has seen real aerial footage — is
            dramatically better than sim-only for the classes VisDrone covers.
          </p>
          <p>
            <strong>Class imbalance at 3.5% is a hard floor.</strong> We do not
            know exactly where the threshold is, but below about 5% of boxes the
            model treats a class as noise. The fix — oversampling the
            underrepresented images — is simple and effective. Future work: add
            synthetic augmentation (mosaic, copy-paste) specifically for the rare
            aerial classes (landing_pad, powerline_pole, animal, boat) which
            currently have zero training data.
          </p>
          <h2>Where the model runs</h2>
          <p>
            The ONNX is 12 MB. It runs at 55 FPS on an RTX 5090 and is
            targeting a TensorRT int8 export for the Jetson Orin Nano where it
            will replace the stock COCO-80 model in the perception pipeline.
            The model is loaded automatically by the perception layer if a
            domain detector ONNX is present — no inference code changes required.
          </p>
          <p>
            Technical report with all metrics, training curves, and confusion
            matrices:{" "}
            <Link href="/blog/domain-detector-aerial-autonomy-paper">
              Domain-Specific Object Detection for Aerial Autonomy
            </Link>
            .
          </p>
        </Prose>
      );

    case "domain-detector-aerial-autonomy-paper":
      return (
        <Prose>
          <p>
            <strong>Abstract.</strong> We present a domain-specific object
            detector for aerial autonomy applications, trained on a combined
            corpus of simulation-generated frames and the VisDrone2019-DET
            public dataset. Starting from a YOLOv8n base, we define a 9-class
            schema suited to autonomous drone operations (drone, person_aerial,
            vehicle, bicycle_motorcycle, landing_pad, powerline_pole, person,
            animal, boat) and evaluate three successive training configurations.
            The sim-only baseline (v1, 18K frames) reaches mAP50 = 0.471 on a
            held-out simulation val set. Merging VisDrone real-world footage (v2,
            21K images, 364K boxes) improves real-domain person_aerial from 0.047
            to 0.377 but causes near-complete drone class collapse (AP50 0.047 →
            0.010) due to a 3.5% box-fraction imbalance. A 4× oversampling of
            drone-containing images (v3, 49K images) recovers drone AP50 to 0.087
            while maintaining real-world gains: mAP50 = 0.384, person_aerial =
            0.360, vehicle = 0.752, bicycle_motorcycle = 0.339. The primary
            finding is that class imbalance — not architecture or augmentation —
            is the dominant failure mode for rare aerial classes in mixed-corpus
            training.
          </p>

          <h2>1. Introduction</h2>
          <p>
            Standard object detection models trained on COCO-80 perform well on
            common ground-level objects but fail systematically in the aerial
            autonomy domain for three reasons: (1) the label set has no drone,
            person_aerial, landing_pad, or powerline class; (2) COCO training
            images are primarily ground-level with standard aspect ratios, while
            drone cameras produce oblique and overhead views at varying altitudes;
            and (3) the objects of primary interest in aerial operations —
            distant targets, thin structures, other aircraft — are consistently
            underrepresented in internet-scale training corpora.
          </p>
          <p>
            We address this by fine-tuning YOLOv8n on a domain-specific corpus
            assembled from two sources: (i) Isaac Sim procedural simulation with
            camera-model ground truth projection, providing perfect labels at low
            cost, and (ii) VisDrone2019-DET, a large-scale public real-world
            aerial dataset providing aerial-perspective vehicle and pedestrian
            boxes. The goal is a detector that runs on-device at 55+ FPS on
            an RTX-class GPU and, after TensorRT int8 export, at viable frame
            rates on the Jetson Orin Nano edge platform.
          </p>

          <h2>2. Related work</h2>
          <p>
            <strong>Aerial object detection.</strong> VisDrone{" "}
            <a href="https://github.com/VisDrone/VisDrone-Dataset" target="_blank" rel="noopener noreferrer">
              (Zhu et al., 2018)
            </a>{" "}
            established a large-scale benchmark for drone-captured footage and
            demonstrated that ImageNet-pretrained detectors transfer poorly to
            the aerial regime. DOTA and xView extend coverage to satellite and
            high-altitude platforms. Anti-UAV and DroneVehicle datasets target
            the drone-as-target detection problem specifically.
          </p>
          <p>
            <strong>Sim-to-real for perception.</strong> NVIDIA Isaac Sim and
            similar photorealistic simulators have been used for generating
            labeled training data when real-world annotation is expensive.
            Domain randomization (DR) over lighting, texture, and object
            placement is standard practice. Our setup uses a fixed scene set
            without DR in v1 and v2; domain gap from sim to VisDrone is the
            expected cost.
          </p>
          <p>
            <strong>Class imbalance in detection.</strong> Class imbalance is
            a known problem in detection training. Focal loss, class-balanced
            sampling, and copy-paste augmentation are common mitigations.
            We apply the simplest effective remedy — image-level oversampling —
            and find it sufficient to recover a collapsed class.
          </p>

          <h2>3. Class schema</h2>
          <p>The 9-class schema and VisDrone mapping:</p>
          <ul>
            <li>
              <strong>0 — drone</strong>: unmanned aircraft in frame (sim only; absent from VisDrone)
            </li>
            <li>
              <strong>1 — person_aerial</strong>: person seen from above (VisDrone: pedestrian, people)
            </li>
            <li>
              <strong>2 — vehicle</strong>: car, van, truck, bus (VisDrone: car, van, truck, bus, tricycle)
            </li>
            <li>
              <strong>3 — bicycle_motorcycle</strong>: two-wheelers (VisDrone: bicycle, motor)
            </li>
            <li>
              <strong>4 — landing_pad</strong>: ground marker (sim only; no public label source)
            </li>
            <li>
              <strong>5 — powerline_pole</strong>: thin vertical / linear structure (sim only)
            </li>
            <li>
              <strong>6 — person</strong>: person at ground level / normal angle (sim only)
            </li>
            <li>
              <strong>7 — animal</strong>: generic animal (no training data in v1–v3)
            </li>
            <li>
              <strong>8 — boat</strong>: watercraft (no training data in v1–v3)
            </li>
          </ul>
          <p>
            Classes 4–8 have no VisDrone source and limited or zero sim coverage.
            They are present in the schema for forward compatibility but produce
            no meaningful AP in current models.
          </p>

          <h2>4. Datasets</h2>
          <h3>4.1 Simulation data (v1 corpus)</h3>
          <p>
            We drove NVIDIA Isaac Sim headlessly over three scene types (office,
            warehouse, hospital) with scripted randomized drone trajectories.
            Per-frame ground truth was produced by projecting known world-space
            object poses through the camera intrinsic model to COCO-format
            bounding boxes, giving perfect labels at zero human annotation cost.
          </p>
          <ul>
            <li>Train: 16,200 frames, 10,800 boxes</li>
            <li>Val: 1,800 frames, 1,200 boxes</li>
            <li>Box distribution: vehicle (class 2) ~60%, drone (class 0) ~40%</li>
            <li>Person / person_aerial: near-zero (scenes lacked overhead person views)</li>
          </ul>

          <h3>4.2 VisDrone2019-DET</h3>
          <p>
            VisDrone is a large-scale benchmark of drone-captured real-world
            footage over urban areas. We use the DET (detection) split:
          </p>
          <ul>
            <li>Train: 6,471 images, 343,204 boxes</li>
            <li>Val: 548 images, 38,759 boxes</li>
            <li>
              Classes mapped to schema: pedestrian → person_aerial (1),
              people → person_aerial (1), car/van/truck/bus/tricycle → vehicle (2),
              bicycle → bicycle_motorcycle (3), motor → bicycle_motorcycle (3)
            </li>
            <li>Ignored: awning-tricycle, others (no schema mapping)</li>
          </ul>
          <p>
            VisDrone contains no drone-as-target boxes. All drone class data
            in the merged corpus comes from simulation.
          </p>

          <h2>5. Training configuration</h2>
          <p>
            All runs: YOLOv8n base, Ultralytics 8.4.66, batch=64, 50 epochs,
            imgsz=640, AdamW optimizer, 2× RTX 5090 (hoopoe GPU server).
            No domain randomization, no mosaic augmentation for rare classes.
            ONNX export: 12 MB, FP32, opset 11.
          </p>
          <ul>
            <li>
              <strong>V1:</strong> sim-only corpus (18K images). Val: sim val set.
            </li>
            <li>
              <strong>V2:</strong> sim + VisDrone (21,471 images, 364K boxes). Val: VisDrone val set.
            </li>
            <li>
              <strong>V3:</strong> sim + VisDrone + 4× oversampled drone images
              (48,717 images). Val: VisDrone val set.
            </li>
          </ul>

          <h2>6. Results</h2>
          <h3>6.1 V1 — simulation baseline</h3>
          <ul>
            <li>mAP50 = 0.471, mAP50-95 = 0.341</li>
            <li>Precision = 0.79, Recall = 0.41</li>
            <li>Vehicle AP50 = 0.895</li>
            <li>Drone AP50 = 0.047</li>
            <li>Person AP50 = 0.894</li>
            <li>Person_aerial AP50 = 0.000 (no aerial person views in sim)</li>
            <li>Inference: 18 ms / 55 FPS on RTX 5090</li>
          </ul>
          <p>
            The high per-class AP on vehicle and person reflects easy sim
            geometry. The 0.471 headline is an artifact of evaluating on the
            same simulator distribution used for training.
          </p>

          <h3>6.2 V2 — VisDrone merge</h3>
          <ul>
            <li>mAP50 = 0.376 (VisDrone val), mAP50-95 = 0.215</li>
            <li>Precision = 0.54, Recall = 0.36</li>
            <li>Person_aerial AP50 = 0.377 (+0.377 vs v1)</li>
            <li>Vehicle AP50 = 0.755 (−0.140 vs v1)</li>
            <li>Bicycle_motorcycle AP50 = 0.365 (first non-zero result)</li>
            <li>Drone AP50 = 0.010 (−0.037 vs v1; near-collapse)</li>
          </ul>
          <p>
            The mAP50 drop from 0.471 to 0.376 is not a regression — it
            reflects a harder, real-world val set. The drone collapse is a
            genuine failure: 343K VisDrone boxes reduced drone boxes to 3.5%
            of total training signal.
          </p>

          <h3>6.3 V3 — 4× drone oversampling</h3>
          <ul>
            <li>mAP50 = 0.384 (VisDrone val), mAP50-95 = 0.219</li>
            <li>Precision = 0.55, Recall = 0.37</li>
            <li>Person_aerial AP50 = 0.360 (−0.017 vs v2; stable)</li>
            <li>Vehicle AP50 = 0.752 (−0.003 vs v2; stable)</li>
            <li>Bicycle_motorcycle AP50 = 0.339 (−0.026 vs v2; small regression)</li>
            <li>Drone AP50 = 0.087 (+0.077 vs v2; recovered)</li>
          </ul>
          <p>
            Drone fraction in training images rose from ~3.5% to ~10% after
            4× oversampling. The class recovered without materially harming
            the VisDrone classes. The drone AP50 is evaluated against the sim
            val set (VisDrone val contains no drone targets); it represents
            precision when the model fires on a drone, not recall across a
            large drone val set.
          </p>

          <h2>7. Discussion</h2>
          <h3>7.1 Sim-to-real gap in mAP reporting</h3>
          <p>
            V1&apos;s mAP50 = 0.471 on a sim val set is not comparable to v2/v3&apos;s
            mAP50 on a real-world val set. Treating the v1 number as a baseline
            and the v2 number as a regression would be a measurement error.
            The correct interpretation: v2 and v3 provide the first meaningful
            out-of-distribution evaluation because they use VisDrone val, which
            the model has not seen during training.
          </p>
          <h3>7.2 Class imbalance as the dominant failure mode</h3>
          <p>
            The drone collapse in v2 was caused entirely by class imbalance.
            At 3.5% of total box count, the gradient signal for the drone class
            was insufficient to maintain the v1 representations acquired from
            sim. The threshold is approximately 5% — below that, a class in a
            mixed-corpus training run is at risk of collapse without explicit
            rebalancing.
          </p>
          <p>
            The fix — 4× image-level oversampling of drone-containing images —
            is effective and simple. More principled alternatives (copy-paste
            augmentation, class-weighted focal loss, synthetic drone compositing)
            remain for future work.
          </p>
          <h3>7.3 Missing classes</h3>
          <p>
            Landing_pad, powerline_pole, animal, and boat have zero to near-zero
            training data across all three versions. These classes require either
            additional sim generation (for landing_pad and powerline_pole) or
            public dataset sourcing (for animal and boat at altitude). They are
            included in the schema now so inference code does not need to change
            when data becomes available.
          </p>

          <h2>8. Deployment</h2>
          <p>
            The v3 ONNX (12 MB, FP32) is uploaded to S3 and downloaded by
            the drone setup script. The perception layer auto-selects the domain
            detector over the COCO model when it is present, using the same
            ONNX inference path. TensorRT int8 export targeting the Jetson Orin
            Nano is the next step; preliminary benchmarks suggest 8–12 FPS at
            int8 on the Nano&apos;s integrated GPU, which is within budget for the
            perception pipeline.
          </p>

          <h2>9. Conclusion</h2>
          <p>
            We trained a 9-class domain object detector for aerial autonomy
            across three rounds of training. The main result is not the final
            mAP number — it is the failure mode: class imbalance at the 3–5%
            box-fraction level causes rare classes to collapse in mixed-corpus
            training. Simple image-level oversampling recovers the class.
            The next phase is expanding rare-class coverage (landing_pad,
            powerline_pole) via sim data generation and running the model on
            the Jetson Orin Nano in hardware-in-the-loop evaluation.
          </p>
          <p>
            See the blog post for an accessible walkthrough:{" "}
            <Link href="/blog/domain-detector-aerial-autonomy">
              We Trained a Domain Detector for Drones. One Class Collapsed to Zero.
            </Link>
          </p>
        </Prose>
      );

    default:
      return null;
  }
}
