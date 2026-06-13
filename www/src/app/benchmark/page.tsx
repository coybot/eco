import type { Metadata } from "next";
import Link from "next/link";
import { ExternalLink, Trophy, FlaskConical, GitFork, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { Button } from "@/components/ui/button";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const DESCRIPTION =
  "The closed-loop AI drone navigation benchmark. 25 VLMs, 10,200 flight trials, one surprising result: every end-to-end model lost to a hovering baseline. Submit your own architecture — we want to be beaten.";

export const metadata: Metadata = {
  title: "Drone AI Benchmark",
  description: DESCRIPTION,
  ...socialMeta("/benchmark", "Drone AI Benchmark | Astral", DESCRIPTION),
};

const jsonLd = {
  "@context": "https://schema.org",
  "@type": "Dataset",
  name: "Astral Closed-Loop Drone AI Navigation Benchmark",
  description: DESCRIPTION,
  url: "https://astral.us/benchmark",
  creator: { "@type": "Organization", name: "Astral", url: "https://astral.us" },
  license: "https://creativecommons.org/licenses/by/4.0/",
  measurementTechnique: "Closed-loop Isaac Sim flight trials with collision and position-error scoring",
  variableMeasured: "Mean position error (m), collision rate (%), directional accuracy, step-1 prediction error",
  isAccessibleForFree: true,
  datePublished: "2026-05-02",
  keywords: [
    "drone navigation",
    "vision-language models",
    "autonomous drones",
    "closed-loop evaluation",
    "AI benchmark",
    "aerial robotics",
  ],
};

// Results table data
// Sources: why-vlm-drones-cant-beat-hovering, metric-gap paper, gemma4-pilot paper, engineering-separation
const results = [
  {
    rank: 1,
    system: "Astral Track A (modular)",
    type: "Modular stack",
    org: "Astral",
    meanError: "1.04 m",
    meanErrorVal: 1.04,
    collisionRate: "0%",
    directionalAcc: "—",
    notes: "Operational commands only. VLM as semantic selector + depth model for metric grounding.",
    link: "/blog/why-vlm-drones-cant-beat-hovering#paper",
    highlight: true,
  },
  {
    rank: 2,
    system: "Astral Track A (full benchmark)",
    type: "Modular stack",
    org: "Astral",
    meanError: "9.98 m",
    meanErrorVal: 9.98,
    collisionRate: "0%",
    directionalAcc: "—",
    notes: "All 153 trials including unsolved task types (occluded, multi-step). Tradeoff vs. hover to maintain 0% collisions.",
    link: "/blog/engineering-drone-autonomy-18-iterations",
    highlight: true,
  },
  {
    rank: 3,
    system: "Hover baseline",
    type: "Null baseline",
    org: "—",
    meanError: "9.50 m",
    meanErrorVal: 9.50,
    collisionRate: "0%",
    directionalAcc: "—",
    notes: "Zero motion, zero intelligence. Correct null hypothesis: any system that moves must justify it with accuracy gains.",
    link: null,
    highlight: false,
  },
  {
    rank: 4,
    system: "Gemma 4 E2B (modular)",
    type: "VLM — modular",
    org: "Google",
    meanError: "9.35 m",
    meanErrorVal: 9.35,
    collisionRate: "—",
    directionalAcc: "—",
    notes: "Same Gemma 4 weights as semantic target selector inside the modular pipeline. Beats hover.",
    link: "/blog/why-vlm-drones-cant-beat-hovering#paper",
    highlight: false,
  },
  {
    rank: 5,
    system: "Gemini 3 Flash (E2E)",
    type: "VLM — end-to-end",
    org: "Google",
    meanError: "8.70 m",
    meanErrorVal: 8.70,
    collisionRate: "—",
    directionalAcc: "0.89",
    notes: "Best end-to-end result in the 25-VLM lineup. Beats hover by 0.80 m — marginal.",
    link: "/blog/metric-gap-vision-language-drone-navigation#paper",
    highlight: false,
  },
  {
    rank: 6,
    system: "Qwen 2.5 VL (E2E)",
    type: "VLM — end-to-end",
    org: "Alibaba",
    meanError: "38.64 m*",
    meanErrorVal: 38.64,
    collisionRate: "—",
    directionalAcc: "0.83",
    notes: "*Closed-loop final error. Step-1 error is 8.11 m, but cumulative error reaches 38.64 m — model anchors on initial spatial estimate and compounds.",
    link: "/blog/metric-gap-vision-language-drone-navigation#paper",
    highlight: false,
  },
  {
    rank: 7,
    system: "Gemma 4 E2B (E2E)",
    type: "VLM — end-to-end",
    org: "Google",
    meanError: "47.78 m",
    meanErrorVal: 47.78,
    collisionRate: "67%",
    directionalAcc: "—",
    notes: "17.8 s/step latency. Same weights as rank 4 above — architecture is everything.",
    link: "/blog/why-vlm-drones-cant-beat-hovering#paper",
    highlight: false,
  },
  {
    rank: 8,
    system: "Frontier VLMs (E2E, median)",
    type: "VLM — end-to-end",
    org: "Various",
    meanError: "~11–14 m",
    meanErrorVal: 12.5,
    collisionRate: "—",
    directionalAcc: "0.83–0.91",
    notes: "Median result across remaining 22 models in the 25-VLM lineup. All underperform hover on the full benchmark.",
    link: "/blog/metric-gap-vision-language-drone-navigation#paper",
    highlight: false,
  },
];

export default function BenchmarkPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <JsonLd data={jsonLd} />
      <Header />
      <main className="flex-1">

        {/* Hero */}
        <section className="py-16 bg-gradient-to-b from-background to-card border-b border-border">
          <div className="container mx-auto px-4 max-w-4xl">
            <div className="flex items-center gap-3 mb-4">
              <div className="p-2 rounded-lg bg-amber-500/10">
                <FlaskConical className="h-5 w-5 text-amber-500" />
              </div>
              <span className="text-sm text-amber-500 font-mono uppercase tracking-widest">
                Open benchmark
              </span>
            </div>
            <h1 className="text-4xl sm:text-5xl font-bold tracking-tight mb-6">
              Closed-Loop Drone AI<br />Navigation Benchmark
            </h1>
            <p className="text-lg text-muted-foreground max-w-2xl mb-4">
              We ran 10,200 closed-loop flight trials across 25 vision-language models
              in Isaac Sim. Every end-to-end model lost to a drone that just hovered.
              We published the results, the methodology, the dataset, and the models.
            </p>
            <p className="text-lg text-muted-foreground max-w-2xl mb-8">
              <strong className="text-foreground">We want to be beaten.</strong>{" "}
              Submit your architecture. If you outperform our modular stack,
              we will put your result at the top of this table and write about it.
              The goal is an honest leaderboard for the whole community — not a trophy case.
            </p>
            <div className="flex flex-wrap gap-3">
              <Button asChild size="lg">
                <a href="mailto:hello@astral.us">
                  Submit a result
                  <ArrowRight className="ml-2 h-4 w-4" />
                </a>
              </Button>
              <Button variant="outline" size="lg" asChild>
                <Link href="#run-it-yourself">Run it yourself</Link>
              </Button>
              <Button variant="outline" size="lg" asChild>
                <a href={SITE.yonderDataset} target="_blank" rel="noopener noreferrer">
                  Yonder dataset
                  <ExternalLink className="ml-2 h-4 w-4" />
                </a>
              </Button>
            </div>
          </div>
        </section>

        {/* Key finding callout */}
        <section className="py-10 bg-amber-500/5 border-b border-amber-500/20">
          <div className="container mx-auto px-4 max-w-4xl">
            <div className="grid sm:grid-cols-3 gap-8 text-center">
              <div>
                <div className="text-4xl font-bold text-amber-500 mb-1">10,200</div>
                <div className="text-sm text-muted-foreground">Closed-loop flight trials</div>
              </div>
              <div>
                <div className="text-4xl font-bold text-amber-500 mb-1">25</div>
                <div className="text-sm text-muted-foreground">VLM architectures tested</div>
              </div>
              <div>
                <div className="text-4xl font-bold text-amber-500 mb-1">1.04 m</div>
                <div className="text-sm text-muted-foreground">Best result (modular, operational commands)</div>
              </div>
            </div>
          </div>
        </section>

        {/* Results table */}
        <section className="py-16 bg-background border-b border-border">
          <div className="container mx-auto px-4 max-w-5xl">
            <h2 className="text-2xl font-bold mb-2">Results</h2>
            <p className="text-muted-foreground mb-8 text-sm">
              Ranked by mean position error (lower is better). Hover baseline is 9.50 m — the null
              hypothesis every system must beat to justify moving.{" "}
              <Link href="#methodology" className="text-amber-500 underline">
                Full methodology below.
              </Link>
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b border-border text-left">
                    <th className="py-3 pr-4 font-semibold text-muted-foreground w-8">#</th>
                    <th className="py-3 pr-4 font-semibold text-muted-foreground">System</th>
                    <th className="py-3 pr-4 font-semibold text-muted-foreground">Type</th>
                    <th className="py-3 pr-4 font-semibold text-muted-foreground">Mean error</th>
                    <th className="py-3 pr-4 font-semibold text-muted-foreground">Collision rate</th>
                    <th className="py-3 pr-4 font-semibold text-muted-foreground hidden lg:table-cell">Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r) => (
                    <tr
                      key={r.rank}
                      className={`border-b border-border ${
                        r.highlight
                          ? "bg-amber-500/5"
                          : r.system.includes("Hover")
                          ? "bg-card/50 italic"
                          : ""
                      }`}
                    >
                      <td className="py-3 pr-4 text-muted-foreground">{r.rank}</td>
                      <td className="py-3 pr-4 font-medium">
                        {r.link ? (
                          <Link href={r.link} className="hover:text-amber-500 transition-colors">
                            {r.system}
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">{r.system}</span>
                        )}
                        {r.highlight && (
                          <span className="ml-2 text-xs bg-amber-500/20 text-amber-500 px-1.5 py-0.5 rounded font-normal">
                            Astral
                          </span>
                        )}
                      </td>
                      <td className="py-3 pr-4 text-muted-foreground">{r.type}</td>
                      <td className="py-3 pr-4 font-mono font-semibold">
                        {r.meanError}
                      </td>
                      <td className="py-3 pr-4 font-mono">{r.collisionRate}</td>
                      <td className="py-3 pr-4 text-muted-foreground text-xs hidden lg:table-cell max-w-xs">
                        {r.notes}
                      </td>
                    </tr>
                  ))}
                  {/* Submit row */}
                  <tr className="border-b border-dashed border-border/50">
                    <td className="py-3 pr-4 text-muted-foreground/40">—</td>
                    <td className="py-3 pr-4" colSpan={5}>
                      <a
                        href="mailto:hello@astral.us"
                        className="text-amber-500 hover:underline text-sm"
                      >
                        + Submit your system
                      </a>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-xs text-muted-foreground mt-4">
              — = not measured or not applicable for this system. * = closed-loop cumulative error (see notes).
              All trials run in Isaac Sim unless noted in submission.
            </p>
          </div>
        </section>

        {/* Methodology */}
        <section id="methodology" className="py-16 bg-card border-b border-border">
          <div className="container mx-auto px-4 max-w-4xl">
            <h2 className="text-2xl font-bold mb-6">Methodology</h2>
            <div className="grid md:grid-cols-2 gap-10 text-muted-foreground">
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground">What we measure</h3>
                <p>
                  <strong className="text-foreground">Mean position error (m)</strong> — Euclidean distance
                  between the drone's final position and the target object centroid at trial end.
                  Primary metric. Lower is better.
                </p>
                <p>
                  <strong className="text-foreground">Collision rate (%)</strong> — Fraction of trials
                  ending in a physics collision with any environment object.
                </p>
                <p>
                  <strong className="text-foreground">Directional accuracy</strong> — Cosine similarity
                  between predicted heading and ground-truth heading to target on the first step.
                  Separates semantic understanding from metric grounding.
                </p>
                <p>
                  <strong className="text-foreground">Step-1 prediction error</strong> — Position error
                  on the first waypoint output only, before closed-loop compounding.
                </p>
              </div>
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground">Trial structure</h3>
                <p>
                  153 distinct task trials, each repeated across evaluation runs. Tasks are organized
                  into five tiers by difficulty:
                </p>
                <ul className="list-disc pl-5 space-y-1 text-sm">
                  <li>Tier 1 — Stationary targets, unambiguous category name</li>
                  <li>Tier 2 — Semantic identification (color, size, type)</li>
                  <li>Tier 3 — Visual grounding (relative position)</li>
                  <li>Tier 4 — Occluded objects</li>
                  <li>Tier 5 — Multi-step reasoning and negation</li>
                </ul>
                <p className="text-sm">
                  75% of target objects are not visible from the drone's spawn position — exploration
                  is required. The environment is an indoor warehouse in Isaac Sim. Drone platform:
                  simulated Jetson Orin Nano compute budget.
                </p>
              </div>
            </div>

            <div className="mt-10 space-y-4 text-muted-foreground">
              <h3 className="text-lg font-semibold text-foreground">The hover baseline</h3>
              <p>
                The hover baseline outputs zero velocity at every step. It achieves 9.50 m mean error
                (the average distance from spawn to target across all trials) and 0% collision rate.
                It is the correct null hypothesis: any system that moves must justify that motion with
                a reduction in position error. Beating hover is the minimum bar for deployment readiness.
                In our 25-VLM lineup, the best end-to-end result beat hover by 0.80 m. Most did not beat
                it at all.
              </p>
              <p>
                We report hover as rank 3 rather than rank 1 because the Astral modular stack and the
                Gemma 4 modular result both beat it. Hover is not the goal — it is the floor.
              </p>
            </div>

            <div className="mt-8 flex flex-wrap gap-3">
              <Button asChild>
                <Link href="/blog/metric-gap-vision-language-drone-navigation#paper">
                  Full methodology paper
                </Link>
              </Button>
              <Button variant="outline" asChild>
                <Link href="/blog/why-vlm-drones-cant-beat-hovering">
                  Detailed results write-up
                </Link>
              </Button>
            </div>
          </div>
        </section>

        {/* Run it yourself */}
        <section id="run-it-yourself" className="py-16 bg-background border-b border-border">
          <div className="container mx-auto px-4 max-w-4xl">
            <h2 className="text-2xl font-bold mb-2">Run it yourself</h2>
            <p className="text-muted-foreground mb-8">
              The benchmark is fully reproducible. You need Isaac Sim, the Astral SDK, and the Yonder
              evaluation split. Everything else is open source.
            </p>

            <div className="space-y-10">
              <div>
                <h3 className="text-lg font-semibold mb-3">1. Prerequisites</h3>
                <ul className="list-disc pl-5 space-y-2 text-muted-foreground text-sm">
                  <li>
                    <strong className="text-foreground">Isaac Sim 4.x</strong> — the evaluation
                    environment. Free for research.{" "}
                    <a
                      href="https://developer.nvidia.com/isaac-sim"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-amber-500 underline"
                    >
                      Download from NVIDIA
                    </a>
                    .
                  </li>
                  <li>
                    <strong className="text-foreground">Astral SDK</strong> — the evaluation harness,
                    task definitions, and scoring scripts.{" "}
                    <a
                      href={SITE.astralSdk}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-amber-500 underline"
                    >
                      github.com/astral-us/astral-sdk
                    </a>
                    .
                  </li>
                  <li>
                    <strong className="text-foreground">Yonder evaluation split</strong> — the
                    held-out evaluation scenes.{" "}
                    <a
                      href={SITE.yonderDataset}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-amber-500 underline"
                    >
                      astralhf/yonder on Hugging Face
                    </a>
                    .
                  </li>
                  <li>
                    Python 3.10+, CUDA 12.x, 16 GB VRAM minimum (for running VLMs locally).
                  </li>
                </ul>
              </div>

              <div>
                <h3 className="text-lg font-semibold mb-3">2. Install</h3>
                <div className="bg-card rounded-lg border border-border p-4 font-mono text-sm space-y-1 text-muted-foreground overflow-x-auto">
                  <div><span className="text-muted-foreground/50"># Clone the SDK</span></div>
                  <div>git clone https://github.com/astral-us/astral-sdk.git && cd astral-sdk</div>
                  <div className="mt-2"><span className="text-muted-foreground/50"># Install dependencies</span></div>
                  <div>pip install -e ".[benchmark]"</div>
                  <div className="mt-2"><span className="text-muted-foreground/50"># Download Yonder eval split (~2 GB)</span></div>
                  <div>python scripts/download_yonder.py --split eval</div>
                </div>
              </div>

              <div>
                <h3 className="text-lg font-semibold mb-3">3. Run the baseline</h3>
                <p className="text-muted-foreground text-sm mb-3">
                  Reproduce our hover baseline and Track A results first to verify your setup matches ours:
                </p>
                <div className="bg-card rounded-lg border border-border p-4 font-mono text-sm space-y-1 text-muted-foreground overflow-x-auto">
                  <div><span className="text-muted-foreground/50"># Hover baseline (should give ~9.50 m mean error)</span></div>
                  <div>python benchmark/run.py --policy hover --trials 153 --output results/hover.json</div>
                  <div className="mt-2"><span className="text-muted-foreground/50"># Astral Track A modular stack</span></div>
                  <div>python benchmark/run.py --policy astral_track_a --trials 153 --output results/track_a.json</div>
                  <div className="mt-2"><span className="text-muted-foreground/50"># Score and compare</span></div>
                  <div>python benchmark/score.py results/hover.json results/track_a.json</div>
                </div>
              </div>

              <div>
                <h3 className="text-lg font-semibold mb-3">4. Plug in your own model</h3>
                <p className="text-muted-foreground text-sm mb-3">
                  Implement the <code className="text-amber-500">DronePolicy</code> interface and pass it
                  to the runner. The interface is intentionally minimal — receive a frame and goal string,
                  return a 3D waypoint:
                </p>
                <div className="bg-card rounded-lg border border-border p-4 font-mono text-sm text-muted-foreground overflow-x-auto">
                  <div><span className="text-muted-foreground/50"># benchmark/policies/my_policy.py</span></div>
                  <div className="mt-1">from astral.benchmark import DronePolicy, Observation</div>
                  <div>import numpy as np</div>
                  <div className="mt-2">class MyPolicy(DronePolicy):</div>
                  <div>&nbsp;&nbsp;&nbsp;&nbsp;def predict(self, obs: Observation) -&gt; np.ndarray:</div>
                  <div>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span className="text-muted-foreground/50"># obs.image: (H, W, 3) uint8 RGB</span></div>
                  <div>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span className="text-muted-foreground/50"># obs.goal: str ("fly to the red crate")</span></div>
                  <div>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span className="text-muted-foreground/50"># obs.depth: (H, W) float32, metres (if available)</span></div>
                  <div>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span className="text-muted-foreground/50"># return: [x, y, z] waypoint in drone frame, metres</span></div>
                  <div>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;waypoint = your_model(obs.image, obs.goal)</div>
                  <div>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return waypoint</div>
                  <div className="mt-2">python benchmark/run.py --policy my_policy.MyPolicy --trials 153</div>
                </div>
              </div>

              <div>
                <h3 className="text-lg font-semibold mb-3">5. Compare against published results</h3>
                <div className="bg-card rounded-lg border border-border p-4 font-mono text-sm text-muted-foreground overflow-x-auto">
                  <div><span className="text-muted-foreground/50"># Download published result files for comparison</span></div>
                  <div>python benchmark/score.py results/my_policy.json --compare published</div>
                  <div className="mt-2"><span className="text-muted-foreground/50"># This prints: mean error, collision rate, per-tier breakdown,</span></div>
                  <div><span className="text-muted-foreground/50"># directional accuracy, and comparison against hover + Track A.</span></div>
                </div>
              </div>
            </div>

            <div className="mt-10 p-6 rounded-lg border border-border bg-card space-y-3">
              <h3 className="font-semibold">Known limitations to disclose in submissions</h3>
              <ul className="list-disc pl-5 space-y-2 text-sm text-muted-foreground">
                <li>
                  All trials are in Isaac Sim. Real-world transfer performance is not measured here
                  and sim-to-real gap varies by architecture.
                </li>
                <li>
                  Mock evaluation overstates performance by ~6.5× vs. closed-loop (validated in{" "}
                  <Link href="/blog/engineering-drone-autonomy-18-iterations" className="text-amber-500 underline">
                    engineering iteration log
                  </Link>
                  ). Submissions based on replayed data will not be accepted.
                </li>
                <li>
                  Tiers 4 and 5 (occluded, multi-step) are unsolved by all current systems including
                  ours. Per-tier breakdown is required in submissions.
                </li>
              </ul>
            </div>
          </div>
        </section>

        {/* Submit */}
        <section className="py-16 bg-card border-b border-border">
          <div className="container mx-auto px-4 max-w-4xl">
            <div className="flex items-start gap-4 mb-8">
              <div className="mt-1 p-2 rounded-lg bg-amber-500/10 shrink-0">
                <Trophy className="h-5 w-5 text-amber-500" />
              </div>
              <div>
                <h2 className="text-2xl font-bold mb-2">Submit your result</h2>
                <p className="text-muted-foreground">
                  We genuinely want to be beaten. If your architecture outperforms ours, we will put
                  your result at the top of the table, link to your paper or repo, and write about
                  what you did differently. The point of this benchmark is to find out what actually
                  works on closed-loop drone navigation — not to defend our own numbers.
                </p>
              </div>
            </div>

            <div className="grid md:grid-cols-2 gap-8 mb-8">
              <div>
                <h3 className="font-semibold mb-3">Required in your submission</h3>
                <ul className="list-disc pl-5 space-y-2 text-sm text-muted-foreground">
                  <li>Results file from <code className="text-amber-500">benchmark/run.py</code> (JSON)</li>
                  <li>Mean position error, collision rate, per-tier breakdown</li>
                  <li>System description: architecture type (E2E / modular / other), model(s) used, compute budget</li>
                  <li>Confirmation that trials ran closed-loop in Isaac Sim, not replayed data</li>
                  <li>Link to code, paper, or write-up (preprint is fine)</li>
                </ul>
              </div>
              <div>
                <h3 className="font-semibold mb-3">What we do with it</h3>
                <ul className="list-disc pl-5 space-y-2 text-sm text-muted-foreground">
                  <li>Verify the results file is consistent with the reported numbers</li>
                  <li>Add your result to the table above, credited to your org/team</li>
                  <li>If you beat our best result, we write a post about it</li>
                  <li>We do not gatekeep negative results — if you tried something and it failed, that is as useful as a win</li>
                </ul>
              </div>
            </div>

            <div className="flex flex-wrap gap-3">
              <Button asChild size="lg">
                <a href="mailto:hello@astral.us">
                  Email hello@astral.us
                  <ArrowRight className="ml-2 h-4 w-4" />
                </a>
              </Button>
              <Button variant="outline" size="lg" asChild>
                <a href={SITE.githubOrg} target="_blank" rel="noopener noreferrer">
                  <GitFork className="mr-2 h-4 w-4" />
                  Open an issue on GitHub
                </a>
              </Button>
            </div>

            <p className="text-xs text-muted-foreground mt-6">
              You keep all rights to your work. We ask only for permission to list your result on this page with a link back to you.
            </p>
          </div>
        </section>

        {/* Related */}
        <section className="py-12 bg-background">
          <div className="container mx-auto px-4 max-w-4xl">
            <h2 className="text-lg font-semibold mb-5">Related</h2>
            <div className="grid sm:grid-cols-3 gap-4 text-sm">
              <Link href="/blog/why-vlm-drones-cant-beat-hovering" className="p-4 rounded-lg border border-border hover:border-amber-500/40 transition-colors">
                <div className="font-medium mb-1">Full results write-up</div>
                <div className="text-muted-foreground">Why every VLM lost to hovering and the architecture that fixed it</div>
              </Link>
              <Link href="/datasets/yonder" className="p-4 rounded-lg border border-border hover:border-amber-500/40 transition-colors">
                <div className="font-medium mb-1">Yonder dataset</div>
                <div className="text-muted-foreground">4.65M-frame drone navigation dataset used for evaluation</div>
              </Link>
              <Link href="/blog/engineering-drone-autonomy-18-iterations" className="p-4 rounded-lg border border-border hover:border-amber-500/40 transition-colors">
                <div className="font-medium mb-1">18-iteration engineering log</div>
                <div className="text-muted-foreground">How the Track A architecture was built, iteration by iteration</div>
              </Link>
            </div>
          </div>
        </section>

      </main>
      <Footer />
    </div>
  );
}
