"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Shield, Tractor, Building2, Siren, ArrowRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

const solutions = [
  {
    id: "defense",
    name: "Defense",
    description: "NDAA-compliant autonomous systems for ISR, perimeter security, and tactical operations.",
    icon: Shield,
    color: "text-white",
    bgColor: "bg-white/10",
  },
  {
    id: "agriculture",
    name: "Agriculture",
    description: "Precision farming with crop health monitoring, spraying, and yield optimization.",
    icon: Tractor,
    color: "text-white",
    bgColor: "bg-white/10",
  },
  {
    id: "infrastructure",
    name: "Infrastructure",
    description: "Automated inspection of power lines, pipelines, bridges, and critical assets.",
    icon: Building2,
    color: "text-white",
    bgColor: "bg-white/10",
  },
  {
    id: "public-safety",
    name: "Public Safety",
    description: "Search and rescue, emergency response, crowd monitoring, and law enforcement support.",
    icon: Siren,
    color: "text-white",
    bgColor: "bg-white/10",
  },
];

export function SolutionsSection() {
  return (
    <section className="py-24 bg-card">
      <div className="container mx-auto px-4">
        {/* Section Header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-16"
        >
          <h2 className="text-3xl sm:text-4xl font-bold">Solutions</h2>
          <p className="mt-4 text-lg text-muted-foreground max-w-2xl mx-auto">
            Mission-tailored solutions that unlock the full capabilities of 
            autonomous drone fleets. Then customize to your needs.
          </p>
        </motion.div>

        {/* Solutions Grid */}
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6 max-w-6xl mx-auto">
          {solutions.map((solution, index) => (
            <motion.div
              key={solution.id}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: index * 0.1 }}
            >
              <Link href={`/solutions/${solution.id}`}>
                <Card className="h-full bg-background border-border hover:border-amber-500/50 transition-all group cursor-pointer">
                  <CardContent className="p-6">
                    <div className={`inline-flex p-3 rounded-lg ${solution.bgColor} mb-4`}>
                      <solution.icon className={`h-6 w-6 ${solution.color}`} />
                    </div>
                    <h3 className="text-xl font-semibold mb-2 group-hover:text-amber-500 transition-colors">
                      {solution.name}
                    </h3>
                    <p className="text-sm text-muted-foreground mb-4">
                      {solution.description}
                    </p>
                    <span className="inline-flex items-center text-sm text-amber-500 opacity-0 group-hover:opacity-100 transition-opacity">
                      Learn more
                      <ArrowRight className="ml-1 h-3 w-3" />
                    </span>
                  </CardContent>
                </Card>
              </Link>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
