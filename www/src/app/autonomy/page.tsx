import type { Metadata } from "next";
import Link from "next/link";
import { JsonLd } from "@/components/json-ld";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const TITLE = "What Drone Autonomy Actually Means (and Why Waypoints Don't Count)";
const DESCRIPTION =
  "Autonomy levels you can measure, the metric gap that sinks vision-language models in closed-loop flight, the separation principle that closes it, and why sim-first evaluation is the only honest way to test any of it.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  ...socialMeta("/autonomy", TITLE, DESCRIPTION, { type: "article" }),
};

const faqs = [
  {
    q: "Is waypoint following autonomy?",
    a: "No — it's automation. A flight plan that executes the same way regardless of what happens around it isn't making decisions; it's running a script. Autonomy is what a system does when the plan stops matching reality: GPS degrades, wind pushes it off course, the target moves, an obstacle isn't on the map.",
  },
  {
    q: "What are the levels of drone autonomy?",
    a: "The useful definition isn't a marketing tier — it's intervention rate and mission success on a fixed adversarial benchmark. L5 means zero human interventions and 100% mission success across the full scenario suite, including collision-free flight. That makes \"how autonomous is it?\" an empirical question with a number attached, not an adjective. Our own L5 result is scoped: 16 adversarial scenarios in kinematic simulation with deterministic sensing, interventions 20 → 0. Under randomized sensing (±5 cm range noise, 3% dropout; 50 seeds × 16 scenarios = 800 runs) it holds at 99.50% collision-free and 97.4% success, and 16/16 collision-free through ArduPilot SITL on both vehicle classes. There is no real-flight data behind it yet.",
  },
  {
    q: "Can an LLM or VLM fly a drone end-to-end?",
    a: "In our testing, no — not reliably. Across 25 vision-language models and 10,200 closed-loop trials, every 7–8B model lost to a drone that simply hovered in place, and a frontier API model barely broke even. VLMs are good at recognizing what something is; they're bad at knowing exactly how far away it is, and that gap is fatal in closed-loop control.",
  },
  {
    q: "What is GPS-denied navigation?",
    a: "Operating without reliable GPS — because it's jammed, spoofed, or simply unavailable indoors or under dense cover. Any system whose \"autonomy\" is really just GPS waypoint-following has no fallback once GPS is gone. A real autonomy stack degrades gracefully instead of failing outright.",
  },
  {
    q: "How do you actually test drone autonomy?",
    a: "In closed-loop simulation, repeatedly, before anything touches hardware. Offline metrics (like detection mAP) can improve dramatically while closed-loop navigation performance doesn't move at all — we've measured this directly. Only full closed-loop trials tell you what a system will actually do in the air.",
  },
  {
    q: "Is this stack NDAA-compliant?",
    a: "We don't make blanket compliance claims — the stack is modular specifically so you can choose NDAA-compliant components where your program requires them, and swap in what you need for the rest.",
  },
];

const jsonLd = [
  {
    "@context": "https://schema.org",
    "@type": "TechArticle",
    headline: TITLE,
    description: DESCRIPTION,
    url: `${SITE.origin}/autonomy`,
    author: { "@type": "Organization", name: "Presidio Autonomy" },
    publisher: { "@type": "Organization", name: "Presidio Autonomy", url: SITE.origin },
  },
  {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqs.map((f) => ({
      "@type": "Question",
      name: f.q,
      acceptedAnswer: { "@type": "Answer", text: f.a },
    })),
  },
  {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "Home", item: SITE.origin },
      { "@type": "ListItem", position: 2, name: "Autonomy", item: `${SITE.origin}/autonomy` },
    ],
  },
];

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="py-12 border-t border-border first:border-t-0 first:pt-0">
      <h2 className="text-2xl sm:text-3xl font-bold mb-4">{title}</h2>
      <div className="prose prose-invert max-w-none text-muted-foreground space-y-4 leading-relaxed">
        {children}
      </div>
    </section>
  );
}

export default function AutonomyPage() {
  return (
    <>
      <JsonLd data={jsonLd} />
      <article className="py-16 bg-gradient-to-b from-background to-card">
        <div className="container mx-auto px-4 max-w-3xl">
          <h1 className="text-4xl sm:text-5xl font-bold tracking-tight mb-6">{TITLE}</h1>
          <p className="text-lg text-muted-foreground mb-4">{DESCRIPTION}</p>

          <Section id="waypoint-fallacy" title="The waypoint fallacy">
            <p>
              A flight plan is a script, not a decision-maker. Punch in a sequence of GPS
              coordinates and the aircraft will fly to each in turn — right up until something
              about the mission changes. GPS degrades under a bridge or near a jammer. Wind
              pushes the aircraft off the planned track. The target you were sent to observe has
              moved. An obstacle that wasn&rsquo;t on the map is now in the flight path.
            </p>
            <p>
              None of that is handled by a waypoint list. Everything interesting about autonomy
              is exception handling — what the system does when the plan stops matching the
              world. If your &ldquo;autonomy&rdquo; evaporates the moment the mission deviates
              from plan, you bought a very fancy timer.
            </p>
          </Section>

          <Section id="levels" title="Autonomy levels you can measure">
            <p>
              Instead of a marketing tier (&ldquo;full autonomy!&rdquo;), we define autonomy
              levels by two numbers on a fixed adversarial benchmark: intervention rate and
              mission success. L5 means zero human interventions and 100% mission success
              across the full scenario suite — sensor dropout, GPS spoofing, dynamic intruders,
              tight chokepoints — including zero collisions.
            </p>
            <p>
              That framing turns &ldquo;how autonomous is it?&rdquo; into an empirical question
              with a number attached, which is what a program office actually needs to make a
              decision. We&rsquo;ve hit L5 on our own 16-scenario adversarial{" "}
              <em>simulation</em> benchmark — interventions went from 20 to zero — and we
              published how — see the <Link href="/blog/l5-autonomy-zero-interventions" className="text-amber-500 hover:underline">write-up</Link>.
            </p>
            <p>
              The scope of that number matters, so here it is in full. L5 holds under
              deterministic sensing in kinematic simulation. An earlier configuration that hit
              L5 lost it entirely once we introduced a more realistic sensor model (see{" "}
              <Link href="#sim-first" className="text-amber-500 hover:underline">sim-first evaluation</Link>{" "}
              below); we fixed that, and the current stack now holds up under randomized
              sensing too — &plusmn;5 cm range noise and 3% dropout, 50 seeds &times; 16
              scenarios (800 runs), at 99.50% collision-free and 97.4% mission success. Through
              ArduPilot SITL it is 16/16 collision-free on both vehicle classes.
            </p>
            <p className="text-sm border border-border rounded-lg p-4 not-prose">
              <strong className="text-foreground">Maturity.</strong> Every result on this page
              is <em>simulation-only</em> and <em>indoor-only</em> — the first two limitations
              listed in both papers. There is no real-flight data behind the L5 result or the
              navigation benchmarks. Real-world transfer is analyzed but unvalidated, and
              outdoor generalization is unknown.
            </p>
          </Section>

          <Section id="metric-gap" title="Why AI alone isn't autonomy — the metric gap">
            <p>
              We ran 25 vision-language models as end-to-end drone controllers across 10,200
              closed-loop flight trials. Every 7&ndash;8B model lost to a drone that simply
              hovered in place; a frontier API model barely broke even. The diagnosis: these models get <em>direction</em> mostly right (0.83–0.91
              directional accuracy) but get <em>distance</em> catastrophically wrong — 6 to 10
              meters of error on targets that were only 4 to 12 meters away.
            </p>
            <p>
              Semantic intelligence without metric grounding is a drone that correctly identifies
              a forklift and flies straight into it. Knowing <em>what</em> something is isn&rsquo;t
              the same as knowing <em>exactly how far away</em> it is — and in closed-loop
              control, that second number is the one that matters.
            </p>
          </Section>

          <Section id="separation-principle" title="The separation principle">
            <p>
              Our fix: use vision-language models for what they&rsquo;re actually good at —
              semantics — and hand geometry and safety to dedicated modules: an object detector,
              a metric depth estimator, a classical planner, and an explicit failure detector.
            </p>
            <p>
              That architecture reached 1.04 m mean error [95% CI 0.84&ndash;1.23] on
              operational commands against a 0.15 m oracle, at 100% collision-free flight. Just
              as important: when the system is uncertain, its failure mode is to <em>hover</em>{" "}
              — which is the operationally correct thing to do, not a crash.
            </p>
            <p>
              We should be precise about where that does and doesn&rsquo;t win. On the full
              67-task benchmark the same stack aggregates to 9.98 m against hover&rsquo;s
              9.50 m — the paper states this as a limitation. What it buys for that 0.48 m is
              100% collision-free flight across all 153 trials, making it the only active
              navigation system in the study that matches hover&rsquo;s perfect safety record;
              it also wins 21 of 51 individual tasks and hits 25.5% success-at-5m against
              hover&rsquo;s 17.6%. Later engineering work reached 8.15 m aggregate at &ge;90%
              collision-free — beating hover outright for the first time.
            </p>
            <p>
              The modularity has a second benefit for regulated buyers: components can be
              selected for NDAA compliance and verified independently, and a heterogeneous
              fleet keeps working with per-drone independence under communications denial. That
              multi-agent work is earlier-stage than the single-drone results: the 4-drone
              extension achieves 70% mission success, and its swarm collision-free rate is only
              12%, because each drone&rsquo;s safety profile accounts for static obstacles but
              not for the other drones.
            </p>
          </Section>

          <Section id="sim-first" title="Sim-first evaluation, or how offline metrics lie">
            <p>
              We&rsquo;ve measured this three separate times: a 9.7&times; improvement in
              detection accuracy produced <em>zero</em> improvement in closed-loop navigation
              (a cross-simulator domain gap — the model was optimizing for a distribution that
              didn&rsquo;t match deployment). Mock evaluations ran roughly 6.5&times; more
              optimistic than the real closed-loop numbers. And an earlier configuration that
              hit L5 lost it entirely under a more realistic sensor model — which is exactly
              why the current L5 result is reported with its sensing model attached, and why we
              re-ran it under randomized sensing (99.50% collision-free, 97.4% success over 800
              runs) and through ArduPilot SITL (16/16) before publishing it.
            </p>
            <p>
              The lesson: never trust an offline or component-level metric on its own. Only
              closed-loop trials tell you what a system will actually do in the air — and
              simulation is the only place you can afford to run thousands of them.
            </p>
          </Section>

          <Section id="unsolved" title="What's still unsolved">
            <p>
              Spatial reasoning, negation (&ldquo;the object that is <em>not</em> the red one&rdquo;),
              occluded targets, and multi-step planning are all still weak points across every
              model we&rsquo;ve tested. We&rsquo;d rather say that plainly than fabricate a
              &ldquo;full autonomy&rdquo; claim — the ceiling is real, and knowing where it is
              matters more than pretending it isn&rsquo;t there.
            </p>
          </Section>

          <section id="faq" className="py-12 border-t border-border">
            <h2 className="text-2xl sm:text-3xl font-bold mb-6">FAQ</h2>
            <Accordion type="single" collapsible className="w-full">
              {faqs.map((f, i) => (
                <AccordionItem key={f.q} value={`item-${i}`}>
                  <AccordionTrigger className="text-left">{f.q}</AccordionTrigger>
                  <AccordionContent className="text-muted-foreground">{f.a}</AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          </section>

          <div className="pt-8 flex flex-wrap gap-3">
            <Button asChild size="lg">
              <Link href="/get-started">Run the benchmark yourself</Link>
            </Button>
            <Button asChild size="lg" variant="outline">
              <Link href="/blog">Read the engineering history</Link>
            </Button>
          </div>
        </div>
      </article>
    </>
  );
}
