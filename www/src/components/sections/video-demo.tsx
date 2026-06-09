"use client";

import { motion } from "framer-motion";

export function VideoDemoSection() {
  return (
    <section className="py-20 bg-background">
      <div className="container mx-auto px-4">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="max-w-5xl mx-auto"
        >
          <div className="text-center mb-12">
            <h2 className="text-3xl sm:text-4xl font-bold mb-4">See it in action</h2>
            <p className="text-lg text-muted-foreground">
              Astral autonomous platforms operating with real-time perception and decision-making
            </p>
          </div>

          <div className="aspect-video bg-card rounded-lg border border-border overflow-hidden shadow-xl">
            <iframe
              width="100%"
              height="100%"
              src="https://www.youtube.com/embed/zlBUv7T1Yyc"
              title="Astral Demo"
              frameBorder="0"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              allowFullScreen
            ></iframe>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
