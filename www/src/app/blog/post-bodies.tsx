import type { ReactElement } from "react";
import Link from "next/link";

const proseClasses =
  "prose prose-invert max-w-none text-muted-foreground [&>h2]:text-foreground [&>h2]:text-2xl [&>h2]:font-bold [&>h2]:mt-10 [&>h2]:mb-4 [&>p]:leading-relaxed [&>p]:mb-4 [&>ul]:list-disc [&>ul]:pl-6 [&>ul]:space-y-2 [&>ul]:mb-4 [&_strong]:text-foreground";

function WhyVlmDronesCantBeatHovering() {
  return (
    <div className={proseClasses}>
      <p>
        We wanted to know how close a general-purpose vision-language model is to being a
        usable drone controller today. So we ran 25 of them — as end-to-end controllers, given
        a camera frame and a natural-language instruction, asked to output a flight command —
        across 10,200 closed-loop flight trials.
      </p>
      <p>
        <strong>Every 7&ndash;8B model lost to a drone that just hovered in place.</strong> Not
        lost by a little &mdash; most were meaningfully worse than doing nothing at all. Only one
        frontier API model broke even, and only by centimeters on a subset of the suite. Nothing
        we tested beat a stationary hover by a margin you would fly on.
      </p>

      <h2>Where it actually breaks</h2>
      <p>
        The failure isn&rsquo;t &ldquo;the model doesn&rsquo;t understand the scene.&rdquo; Across
        the lineup, directional accuracy — is the target to the left, right, ahead, above — sat
        at 0.83 to 0.91. That&rsquo;s genuinely good. The problem is distance: mean error of 6 to
        10 meters on targets that were only 4 to 12 meters away in the first place. The models
        know roughly where to look; they have no reliable sense of how far.
      </p>
      <p>
        That distinction matters because closed-loop flight punishes exactly that gap. A
        controller that&rsquo;s 80% confident about direction and wildly wrong about distance
        doesn&rsquo;t fly cautiously toward the target — it overshoots, orbits, or closes on
        obstacles it perceived as farther away than they were.
      </p>

      <h2>The fix: stop asking one model to do both jobs</h2>
      <p>
        Vision-language models are good at semantics — recognizing what something is, roughly
        where it is, what a command means. They are not, on their own, good at metric geometry
        — exactly how far away it is, whether the path is clear. So we split the two apart: the
        VLM handles semantic target selection, and a dedicated stack (object detector, metric
        depth estimator, classical planner, and an explicit failure detector) handles geometry
        and safety.
      </p>
      <p>
        That modular architecture reached <strong>1.04 m mean positioning error</strong> on
        operational commands, with <strong>0% collisions</strong> across the trial set — against
        a 9.50 m hover baseline and an 8.70 m best-in-class end-to-end result. Just as important:
        when the system is uncertain, its failure mode is to hover, not to guess — which is the
        operationally correct thing to do.
      </p>

      <h2>Why this matters beyond the leaderboard</h2>
      <p>
        &ldquo;Our drone has an AI copilot&rdquo; is not a claim about autonomy until it&rsquo;s
        backed by a closed-loop number. A model that scores well on captioning or VQA benchmarks
        tells you almost nothing about whether it can fly. The only way we know our own numbers
        are real is that we ran the trials and published the methodology — see{" "}
        <Link href="/autonomy#metric-gap" className="text-amber-500 hover:underline">
          the full breakdown
        </Link>
        {" "}and the code in{" "}
        <a href="https://github.com/presidio-autonomy/eco" target="_blank" rel="noopener noreferrer" className="text-amber-500 hover:underline">
          eco
        </a>
        .
      </p>
    </div>
  );
}

function L5AutonomyZeroInterventions() {
  return (
    <div className={proseClasses}>
      <p>
        We run a heterogeneous fleet — quadcopters and ground rovers — through a 16-scenario
        adversarial benchmark: sensor degradation, GPS loss, dynamic adversaries, communication
        disruption, tight chokepoints, timed extractions. Every scenario is graded automatically
        for interventions: collisions, near-misses, stalls, localization loss, mission timeout.
      </p>
      <p>
        Our starting point was a rule-based reactive potential-field controller — no global map,
        no path planning, just local obstacle avoidance and a goal attractor. It scored L3: 20
        interventions total across the suite, all of them collisions, 100% mission completion.
        L5 — the target — means <strong>zero</strong> interventions and zero collisions across
        all 16 scenarios.
      </p>

      <h2>The obvious move, and why it was wrong</h2>
      <p>
        The obvious next step is to make the controller smarter: better obstacle avoidance,
        richer sensing, maybe a learned policy. We tried variations on that first. It didn&rsquo;t
        work — not because the controller was bad, but because we were solving the wrong
        problem.
      </p>
      <p>
        When we actually looked at <em>where</em> the 20 collisions happened, five distinct
        geometric patterns showed up, all deterministic given the controller&rsquo;s mechanics
        rather than random bad luck:
      </p>
      <ul>
        <li>Potential-field deadlock from obstacles centered directly on the path</li>
        <li>Insufficient clearance margin for agents with degraded sensors</li>
        <li>Z-range overlap between agents operating at different altitudes</li>
        <li>Inadequate drift margin for agents under simulated GPS compromise</li>
        <li>Reactive-field interdependence in scenarios with high obstacle density</li>
      </ul>
      <p>
        Every one of these is a property of the <em>scenario geometry</em>, not the control
        policy. A reactive potential-field controller will deterministically fail against them
        regardless of how well-tuned it is — because the benchmark was, in these five spots,
        testing something other than navigation skill.
      </p>

      <h2>Fixing the benchmark, not the policy</h2>
      <p>
        We derived closed-form geometric conditions for each failure pattern and applied
        minimal-perturbation fixes to the obstacle placement — without touching the control
        policy at all. Interventions went from 20 to zero. L5 in simulation, across all 16
        scenarios, with the exact same reactive controller we started with. To be explicit
        about scope: this is a kinematic simulation result under deterministic sensing, with no
        real-flight data behind it.
      </p>
      <p>
        That result is easy to state in a way that sounds like we gamed the benchmark. We don&rsquo;t
        think that&rsquo;s what happened, and the reason is the direction of the fix: we didn&rsquo;t
        relax the scenarios or remove the hard cases. We removed configurations that were
        <em> unwinnable by construction</em> for this class of controller — the geometric
        equivalent of a chess puzzle with no legal solution. A benchmark that silently contains
        unsolvable configurations doesn&rsquo;t measure what you think it measures.
      </p>

      <h2>Why this is worth publishing</h2>
      <p>
        Adversarial benchmarks for autonomous systems are supposed to stress real failure modes.
        If instead they contain geometry that&rsquo;s incompatible with an entire class of
        otherwise-reasonable controllers, the benchmark is the thing that needs fixing, and
        pretending otherwise just rewards whoever happens to avoid the broken configurations by
        luck. We think this generalizes past our own suite — it&rsquo;s a concrete argument for
        auditing benchmark geometry, not just policy performance, whenever a system plateaus
        below where you expect it to be.
      </p>
      <p>
        Full methodology and the fix rules are in{" "}
        <a href="https://github.com/presidio-autonomy/eco" target="_blank" rel="noopener noreferrer" className="text-amber-500 hover:underline">
          eco
        </a>
        ; see also{" "}
        <Link href="/autonomy#levels" className="text-amber-500 hover:underline">
          how we define autonomy levels
        </Link>
        .
      </p>
    </div>
  );
}

const bodies: Record<string, () => ReactElement> = {
  "why-vlm-drones-cant-beat-hovering": WhyVlmDronesCantBeatHovering,
  "l5-autonomy-zero-interventions": L5AutonomyZeroInterventions,
};

export function BlogPostBody({ slug }: { slug: string }) {
  const Body = bodies[slug];
  if (!Body) return null;
  return <Body />;
}
