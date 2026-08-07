"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Trophy, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

const miniResults = [
  {
    rank: 1,
    system: "Presidio (modular stack)",
    meanError: "1.04 m",
    collisionRate: "0%",
    highlight: true,
  },
  {
    rank: 2,
    system: "Hover baseline (no motion)",
    meanError: "9.50 m",
    collisionRate: "0%",
    highlight: false,
  },
  {
    rank: 3,
    system: "Best end-to-end VLM (Gemini 3 Flash)",
    meanError: "8.70 m",
    collisionRate: "—",
    highlight: false,
  },
];

export function BenchmarkTeaserSection() {
  return (
    <section className="py-24 bg-card border-y border-border">
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
                  Open Benchmark · 25 VLMs · 10,200 trials
                </p>
                <h2 className="text-3xl sm:text-4xl font-bold leading-tight">
                  Most end-to-end models couldn't beat a hovering drone.
                </h2>
                <p className="mt-4 text-lg text-muted-foreground">
                  We tested 25 vision-language models in closed-loop flight. Most couldn't beat a stationary hover — and the best only beat it by 0.8 m. It's why we build a modular stack, and why we publish the full methodology and dataset.
                </p>
              </div>
              <Button asChild size="lg" className="glow w-fit">
                <Link href="/benchmark">
                  See the full leaderboard
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
                      <th className="text-left px-4 py-3 text-muted-foreground font-medium">#</th>
                      <th className="text-left px-4 py-3 text-muted-foreground font-medium">System</th>
                      <th className="text-right px-4 py-3 text-muted-foreground font-medium">Pos. error</th>
                      <th className="text-right px-4 py-3 text-muted-foreground font-medium">Collisions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {miniResults.map((row) => (
                      <tr
                        key={row.rank}
                        className={`border-b border-border last:border-0 ${
                          row.highlight ? "bg-amber-500/5" : ""
                        }`}
                      >
                        <td className="px-4 py-3 font-mono text-muted-foreground">{row.rank}</td>
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
                          {row.collisionRate}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="px-4 py-3 bg-background/30 border-t border-border">
                  <Link href="/benchmark" className="text-xs text-amber-500 hover:underline flex items-center gap-1">
                    Full results: 25 models, methodology, submission form
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
