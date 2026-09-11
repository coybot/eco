"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Trophy, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

// Numbers from different benchmark subsets are not comparable, so each row states
// its own scope rather than sitting in a single ranked leaderboard.
const miniResults = [
  {
    scope: "Full 67-task benchmark",
    system: "Presidio (modular stack)",
    meanError: "9.98 m",
    collisionFree: "100%",
    highlight: true,
  },
  {
    scope: "Full 67-task benchmark",
    system: "Hover baseline (no motion)",
    meanError: "9.50 m",
    collisionFree: "100%",
    highlight: false,
  },
  {
    scope: "Operational commands only",
    system: "Presidio (modular stack)",
    meanError: "1.04 m",
    collisionFree: "100%",
    highlight: true,
  },
  {
    scope: "Full benchmark, later engineering",
    system: "Presidio (current build)",
    meanError: "8.15 m",
    collisionFree: "≥90%",
    highlight: true,
  },
];

export function EvidenceSection() {
  return (
    <section id="metric-gap" className="py-24 bg-card border-y border-border">
      <div className="container mx-auto px-4 max-w-5xl">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
        >
          <div className="flex flex-col lg:flex-row gap-12 items-start">
            {/* Left — headline */}
            <div className="flex-1 space-y-6">
              <div className="inline-flex p-4 rounded-2xl bg-amber-500/10">
                <Trophy className="h-10 w-10 text-amber-500" />
              </div>
              <div>
                <p className="text-sm font-medium text-amber-500 uppercase tracking-wider mb-2">
                  25 vision-language models · 10,200 closed-loop trials
                </p>
                <h2 className="text-3xl sm:text-4xl font-bold leading-tight">
                  The 7&ndash;8B models all lost to a drone that just hovered.
                </h2>
                <p className="mt-4 text-lg text-muted-foreground">
                  We ran 25 vision-language models as end-to-end drone controllers in
                  closed-loop flight. Every 7&ndash;8B model lost to a stationary hover; a
                  frontier API model barely broke even. A modular stack that separates
                  semantic understanding from metric geometry reaches 1.04 m on operational
                  commands, collision-free. On the full 67-task benchmark it aggregates to
                  9.98 m against hover&rsquo;s 9.50 m — 0.48 m of accuracy traded for
                  100% collision-free flight across all 153 trials, the only active navigator
                  that matches hover&rsquo;s perfect safety record. Later engineering work
                  reached 8.15 m aggregate at &ge;90% collision-free, beating hover outright
                  for the first time.
                </p>
                <p className="mt-4 text-sm text-muted-foreground">
                  What&rsquo;s open: the 16-scenario L5 benchmark, its scenario definitions
                  and its scorecard are in the <code>eco</code> repo and runnable today. What
                  isn&rsquo;t: the closed-loop VLM navigation harness that produced the
                  10,200-trial results is in a private repo. The Yonder dataset is
                  CC-BY-NC-4.0 — non-commercial use only, unlike the MIT/Apache-2.0 code.
                </p>
                <p className="text-sm text-muted-foreground">
                  Maturity: these results are simulation-only and indoor-only. Real-world
                  transfer is analyzed but not yet validated in flight, and outdoor
                  generalization is unknown.
                </p>
              </div>
              <Button asChild size="lg" className="glow w-fit">
                <Link href="/autonomy#metric-gap">
                  Read the full breakdown
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Link>
              </Button>
            </div>

            {/* Right — mini results table */}
            <div className="w-full lg:w-auto lg:min-w-[400px]">
              <div className="rounded-xl border border-border overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-background/50">
                      <th className="text-left px-4 py-3 text-muted-foreground font-medium">Benchmark scope</th>
                      <th className="text-left px-4 py-3 text-muted-foreground font-medium">System</th>
                      <th className="text-right px-4 py-3 text-muted-foreground font-medium">Pos. error</th>
                      <th className="text-right px-4 py-3 text-muted-foreground font-medium">Collision-free</th>
                    </tr>
                  </thead>
                  <tbody>
                    {miniResults.map((row) => (
                      <tr
                        key={`${row.scope}-${row.system}`}
                        className={`border-b border-border last:border-0 ${
                          row.highlight ? "bg-amber-500/5" : ""
                        }`}
                      >
                        <td className="px-4 py-3 text-xs text-muted-foreground">{row.scope}</td>
                        <td className="px-4 py-3 font-medium">
                          {row.highlight && (
                            <span className="inline-block w-2 h-2 rounded-full bg-amber-500 mr-2 align-middle" />
                          )}
                          {row.system}
                        </td>
                        <td className={`px-4 py-3 text-right font-mono ${row.highlight ? "text-amber-500 font-bold" : "text-muted-foreground"}`}>
                          {row.meanError}
                        </td>
                        <td className={`px-4 py-3 text-right font-mono ${row.highlight ? "text-green-500 font-bold" : "text-muted-foreground"}`}>
                          {row.collisionFree}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="px-4 py-3 bg-background/30 border-t border-border space-y-2">
                  <p className="text-xs text-muted-foreground">
                    Not a ranking: rows are only comparable within the same benchmark scope.
                    Operational commands are an easier subset (0.15 m oracle) than the full
                    67-task suite. Simulation results.
                  </p>
                  <Link href="/autonomy#metric-gap" className="text-xs text-amber-500 hover:underline flex items-center gap-1">
                    Full results and methodology
                    <ArrowRight className="h-3 w-3" />
                  </Link>
                </div>
              </div>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
