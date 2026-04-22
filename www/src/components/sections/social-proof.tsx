"use client";

import { motion } from "framer-motion";

const pressLogos = [
  { name: "TechCrunch", logo: "TechCrunch" },
  { name: "Wired", logo: "WIRED" },
  { name: "New Atlas", logo: "New Atlas" },
  { name: "Lifewire", logo: "Lifewire" },
  { name: "ZDNet", logo: "ZDNet" },
  { name: "DroneDJ", logo: "DroneDJ" },
];

export function SocialProofSection() {
  return (
    <section className="py-12 border-y border-border bg-card/50">
      <div className="container mx-auto px-4">
        <motion.div
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="flex flex-col items-center"
        >
          <p className="text-sm text-muted-foreground mb-8">
            Featured in leading tech publications
          </p>
          <div className="flex flex-wrap items-center justify-center gap-x-12 gap-y-6">
            {pressLogos.map((press) => (
              <div
                key={press.name}
                className="text-muted-foreground/60 hover:text-muted-foreground transition-colors"
              >
                <span className="text-xl font-semibold tracking-tight">
                  {press.logo}
                </span>
              </div>
            ))}
          </div>
        </motion.div>
      </div>
    </section>
  );
}
