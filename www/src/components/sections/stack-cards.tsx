"use client";

import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";
import { SITE } from "@/lib/site";

const cards = [
  {
    name: "presidio-sdk",
    tagline: "Drones that finish the mission when the network doesn't.",
    body: "Python for ArduPilot drones, plus Swift packages for a phone-brained rover. MAVLink primitives, camera drivers, and the on-device reasoning loop.",
    href: SITE.presidioSdk,
  },
  {
    name: "eco",
    tagline: "Simulation and a 16-scenario adversarial benchmark.",
    body: "Sensor dropout, GPS spoofing, dynamic intruders, chokepoints. SITL and Isaac Sim paths, automated intervention grading, published scorecards.",
    href: SITE.eco,
  },
  {
    name: "presidio-docs",
    tagline: "Getting-started guides and reference docs.",
    body: "Quickstart, SITL simulation, Isaac Sim perception-in-the-loop, and the autonomy-levels writeup — Mintlify sources, open for PRs.",
    href: SITE.presidioDocs,
  },
];

export function StackCardsSection() {
  return (
    <section className="py-24 bg-background">
      <div className="container mx-auto px-4">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-16"
        >
          <h2 className="text-3xl sm:text-4xl font-bold">What&rsquo;s in the stack</h2>
          <p className="mt-4 text-lg text-muted-foreground max-w-2xl mx-auto">
            Three repos, all open source. No cloud dependency to try any of it.
          </p>
        </motion.div>

        <div className="grid md:grid-cols-3 gap-6 max-w-5xl mx-auto">
          {cards.map((card, i) => (
            <motion.a
              key={card.name}
              href={card.href}
              target="_blank"
              rel="noopener noreferrer"
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: i * 0.1 }}
              className="group rounded-xl border border-border bg-card p-6 hover:border-amber-500/50 transition-all"
            >
              <p className="font-mono text-sm text-amber-500">{card.name}</p>
              <h3 className="mt-2 text-lg font-semibold">{card.tagline}</h3>
              <p className="mt-3 text-sm text-muted-foreground">{card.body}</p>
              <span className="mt-4 inline-flex items-center text-sm text-amber-500 opacity-0 group-hover:opacity-100 transition-opacity">
                View on GitHub
                <ArrowRight className="ml-1 h-3 w-3" />
              </span>
            </motion.a>
          ))}
        </div>
      </div>
    </section>
  );
}
