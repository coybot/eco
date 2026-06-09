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

    default:
      return null;
  }
}
