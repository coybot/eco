"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";

const columns = [
  {
    title: "You know aerospace or hardware",
    body: "You can build and fly the airframe. The gap is the software layer that decides what to do when the plan breaks.",
  },
  {
    title: "You know embedded software",
    body: "You can get code running on the flight computer. The gap is what that code should actually be doing — perception, planning, safety, not just a scripted route.",
  },
  {
    title: "You know AI",
    body: "You can train and deploy a model. The gap is knowing why a model that scores well offline still crashes into a forklift in the field.",
  },
];

export function AudienceSection() {
  return (
    <section className="py-24 bg-card border-y border-border">
      <div className="container mx-auto px-4">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-16"
        >
          <h2 className="text-3xl sm:text-4xl font-bold">
            You know one piece. Autonomy is the part between them.
          </h2>
        </motion.div>

        <div className="grid md:grid-cols-3 gap-6 max-w-5xl mx-auto">
          {columns.map((col, i) => (
            <motion.div
              key={col.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: i * 0.1 }}
              className="text-center"
            >
              <h3 className="text-lg font-semibold">{col.title}</h3>
              <p className="mt-3 text-sm text-muted-foreground">{col.body}</p>
            </motion.div>
          ))}
        </div>

        <div className="text-center mt-12">
          <Link
            href="/autonomy"
            className="inline-flex items-center text-amber-500 font-medium hover:underline"
          >
            Start here
            <ArrowRight className="ml-1 h-4 w-4" />
          </Link>
        </div>
      </div>
    </section>
  );
}
