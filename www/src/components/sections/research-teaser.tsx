"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { FlaskConical, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ResearchTeaserSection() {
  return (
    <section className="py-24 bg-card border-y border-border">
      <div className="container mx-auto px-4 max-w-4xl">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="flex flex-col md:flex-row md:items-center gap-8"
        >
          <div className="inline-flex p-4 rounded-2xl bg-amber-500/10 w-fit">
            <FlaskConical className="h-10 w-10 text-amber-500" />
          </div>
          <div className="flex-1 space-y-4">
            <h2 className="text-3xl sm:text-4xl font-bold">Research-grade autonomy</h2>
            <p className="text-lg text-muted-foreground">
              We benchmarked 25 vision-language models across 10,200 closed-loop
              flight trials — most couldn't beat a drone that just hovered, and the best only beat it by 0.8 m. That
              result is why we build the way we do: we publish on the metric gap in
              vision-language navigation, modular architectures that separate
              semantics from geometry, swarm sensing requirements at scale, and
              Yonder — a public dataset designed to expose when offline perception
              metrics mislead you in closed loop.
            </p>
            <Button asChild size="lg" className="w-fit">
              <Link href="/research">
                Read our research
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
