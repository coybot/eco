"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight, Github } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SITE } from "@/lib/site";

export function CTASection() {
  return (
    <section className="py-24 bg-card relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-transparent via-white/[0.02] to-transparent" />

      <div className="container relative mx-auto px-4">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="max-w-3xl mx-auto text-center"
        >
          <h2 className="text-3xl sm:text-4xl md:text-5xl font-bold mb-6">
            Fly it in simulation before you trust it in the air.
          </h2>
          <p className="text-lg text-muted-foreground mb-10 max-w-xl mx-auto">
            No hardware required to start. Run the SDK against SITL, then the full
            16-scenario benchmark, then decide what you trust.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <Link href="/get-started">
              <Button size="lg" className="text-base px-8">
                Get started
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </Link>
            <a href={SITE.githubOrg} target="_blank" rel="noopener noreferrer">
              <Button size="lg" variant="outline" className="text-base px-8">
                <Github className="mr-2 h-4 w-4" />
                GitHub
              </Button>
            </a>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
