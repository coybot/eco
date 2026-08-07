"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import { Code2, Smartphone, Database, MonitorPlay, ArrowRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { SITE } from "@/lib/site";

type PillarBase = {
  title: string;
  description: string;
  icon: LucideIcon;
};

type PillarExternal = PillarBase & {
  external: true;
  href: string;
  cta: string;
  secondaryHref?: string;
  secondaryCta?: string;
};

type PillarInternal = PillarBase & {
  external: false;
  href: string;
  cta: string;
};

const pillars: (PillarExternal | PillarInternal)[] = [
  {
    title: "Open source stack",
    description:
      "Python SDK, examples, and repositories you can run today—ArduPilot SITL for fast iteration on multicopters, fixed-wing, rovers, and more; Isaac-class sim when you need full perception loops.",
    icon: Code2,
    href: SITE.presidioSdk,
    cta: "GitHub: presidio-sdk",
    external: true as const,
  },
  {
    title: "Mobile operator app",
    description:
      "iOS and Android apps for real-world aerial and ground operations alongside the same autonomy stack we develop in the open.",
    icon: Smartphone,
    href: SITE.appStore,
    cta: "App Store",
    external: true as const,
  },
  {
    title: "Yonder dataset",
    description:
      "A large drone-perspective indoor corpus on Hugging Face, built to train perception and to stress-test cross-simulator generalization.",
    icon: Database,
    href: "/datasets/yonder",
    cta: "Explore Yonder",
    external: false as const,
  },
  {
    title: "Simulation guides",
    description:
      "Concrete setup for SITL and Isaac-style workflows so your autonomy code is exercised the same way we evaluate research benchmarks.",
    icon: MonitorPlay,
    href: "/docs/simulation",
    cta: "Run in simulation",
    external: false as const,
  },
];

export function PlatformSection() {
  return (
    <section className="py-24 bg-background">
      <div className="container mx-auto px-4">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-16 max-w-3xl mx-auto"
        >
          <h2 className="text-3xl sm:text-4xl font-bold">What Presidio ships today</h2>
          <p className="mt-4 text-lg text-muted-foreground">
            Open software and honest benchmarks, plus operator tooling and
            simulation-first docs—backed by reference air and ground platforms and
            programs to integrate your fleet or design vehicles for your mission.
          </p>
        </motion.div>

        <div className="grid sm:grid-cols-2 gap-6 max-w-5xl mx-auto">
          {pillars.map((item, index) => (
            <motion.div
              key={item.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: index * 0.08 }}
            >
              <Card className="h-full bg-card border-border hover:border-amber-500/50 transition-colors group">
                <CardContent className="p-6 flex flex-col h-full">
                  <div className="inline-flex p-3 rounded-lg bg-amber-500/10 mb-4 w-fit">
                    <item.icon className="h-6 w-6 text-amber-500" />
                  </div>
                  <h3 className="text-xl font-semibold mb-2">{item.title}</h3>
                  <p className="text-sm text-muted-foreground flex-1 mb-6">
                    {item.description}
                  </p>
                  {item.external ? (
                    <div className="flex flex-wrap gap-4">
                      <a
                        href={item.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center text-sm font-medium text-amber-500 group-hover:underline"
                      >
                        {item.cta}
                        <ArrowRight className="ml-2 h-4 w-4" />
                      </a>
                      {item.secondaryHref && item.secondaryCta ? (
                        <a
                          href={item.secondaryHref}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center text-sm font-medium text-amber-500 group-hover:underline"
                        >
                          {item.secondaryCta}
                          <ArrowRight className="ml-2 h-4 w-4" />
                        </a>
                      ) : null}
                    </div>
                  ) : (
                    <Link
                      href={item.href}
                      className="inline-flex items-center text-sm font-medium text-amber-500 group-hover:underline"
                    >
                      {item.cta}
                      <ArrowRight className="ml-2 h-4 w-4" />
                    </Link>
                  )}
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>

        <p className="mt-12 text-center text-sm text-muted-foreground max-w-2xl mx-auto">
          <Link href="/products" className="text-amber-500 hover:underline">
            Reference platforms
          </Link>
          {" · "}
          <Link href="/enterprise" className="text-amber-500 hover:underline">
            Custom vehicles & fleet programs
          </Link>
        </p>
      </div>
    </section>
  );
}
