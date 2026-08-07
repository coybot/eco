import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { Button } from "@/components/ui/button";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const FAQ_DESCRIPTION =
  "Answers to common questions about autonomous drone AI: why vision-language models fail at navigation, the metric gap, autonomous drone SDKs, swarm sensing, and counter-UAS — grounded in Presidio's published research.";

export const metadata: Metadata = {
  title: "FAQ — Autonomous Drone AI",
  description: FAQ_DESCRIPTION,
  ...socialMeta("/faq", "FAQ — Autonomous Drone AI | Presidio", FAQ_DESCRIPTION),
};

const faqs: { q: string; a: string }[] = [
  {
    q: "Why do AI drones fail at navigation?",
    a: "In Presidio's 10,200-trial closed-loop benchmark across 25 vision-language models, every model scored worse than a drone that simply hovered. Failures decompose into two parts — semantic understanding (what is that object?) and metric spatial grounding (where is it, in meters?) — and the metric gap dominates. Models that name objects correctly still misplace them in 3D, and small grounding errors compound on every replan.",
  },
  {
    q: "Can a vision-language model fly a drone end-to-end?",
    a: "Not reliably today. Used as an end-to-end controller, even strong VLMs lose to a hovering baseline because metric errors accumulate over a closed-loop flight. A modular architecture that separates semantic target selection from metric geometry and control closes the gap on operational commands while prioritizing collision-free flight.",
  },
  {
    q: "What is the metric gap in drone navigation?",
    a: "The metric gap is the disconnect between offline perception accuracy and closed-loop flight performance. In one Presidio study, fine-tuning a detector on 6.7 million frames improved detection mAP 9.7× (4.8% → 46.7%), yet closed-loop navigation success did not improve at all. Offline metrics measured the wrong thing; metric spatial grounding, not detection, was the binding constraint.",
  },
  {
    q: "Why does offline mAP mislead drone perception research?",
    a: "Offline detection metrics (like mAP) can rise sharply while real navigation does not, especially when the training and evaluation simulators disagree geometrically. Presidio built the Yonder dataset specifically to expose this cross-simulator generalization gap and to make closed-loop evaluation, not offline scores, the deciding metric.",
  },
  {
    q: "What is an autonomous drone SDK?",
    a: "An autonomous drone SDK is the software layer that lets developers build, test, and deploy autonomy — perception, planning, and control — on a drone. Presidio's SDK is open source and pairs with high-fidelity simulation (ArduPilot SITL for fast iteration, Isaac-class sim for full perception loops), so autonomy code is evaluated in closed loop before it reaches hardware. It runs on your own platform or on Presidio's NDAA-compliant Quadcopter, Rover, and Fixed-Wing.",
  },
  {
    q: "What sensing do large drone swarms need?",
    a: "Camera-only swarms degrade sharply at scale: in Presidio's simulations up to 1,000 agents, coverage dropped 15.8 percentage points and the collision rate rose 8× compared with ranging-equipped swarms. Ultra-wideband (UWB) ranging becomes effectively non-negotiable above roughly 100 drones.",
  },
  {
    q: "Are counter-UAS attacks detectable in autonomous swarms?",
    a: "Mission-success rate is the wrong primary metric for counter-UAS — across 11,340 seeded trials, GNSS spoofing, RF jamming, kinetic interception, and control takeover often left aggregate task completion unchanged. The physical effects are still clearly measurable (e.g. a 79.5% proportional-navigation capture rate, 5–8 m position error), and a kinematic plausibility detector reached a 39.8% true-positive rate at a 0% false-positive rate.",
  },
  {
    q: "Tower vs. self-organized droneport ATC — which scales?",
    a: "In a 9-cell factorial study across 405 simulated vertiport trials, self-organized coordination with ADS-B broadcast matched centralized tower throughput below about 20 operations per hour, then degraded. Silent-cruise drones (no broadcast) exceeded safe line-of-sight separation thresholds at just 12 operations per hour — making broadcast a practical necessity for dense urban air mobility.",
  },
];

const faqJsonLd = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: faqs.map((faq) => ({
    "@type": "Question",
    name: faq.q,
    acceptedAnswer: { "@type": "Answer", text: faq.a },
  })),
};

export default function FaqPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <JsonLd data={faqJsonLd} />
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-3xl">
            <h1 className="text-4xl sm:text-5xl font-bold mb-6">
              Autonomous drone AI — FAQ
            </h1>
            <p className="text-lg text-muted-foreground">
              Straight answers to the questions people ask about autonomous drone
              AI — each grounded in Presidio's{" "}
              <Link href="/research" className="text-amber-500 underline">
                published research
              </Link>
              .
            </p>
          </div>
        </section>

        <section className="py-12 bg-background border-t border-border">
          <div className="container mx-auto px-4 max-w-3xl">
            <div className="space-y-10">
              {faqs.map((faq) => (
                <div key={faq.q} className="space-y-3">
                  <h2 className="text-xl font-semibold">{faq.q}</h2>
                  <p className="text-muted-foreground leading-relaxed">{faq.a}</p>
                </div>
              ))}
            </div>

            <div className="mt-16 border-t border-border pt-10 flex flex-wrap gap-3">
              <Button asChild>
                <Link href="/research">Read the research</Link>
              </Button>
              <Button variant="outline" asChild>
                <Link href="/blog">Browse the blog</Link>
              </Button>
              <Button variant="outline" asChild>
                <a href={SITE.githubOrg} target="_blank" rel="noopener noreferrer">
                  Open SDK on GitHub
                </a>
              </Button>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
