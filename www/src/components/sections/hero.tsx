"use client";

import { motion } from "framer-motion";
import { Volume2, VolumeX } from "lucide-react";
import { useState, useRef } from "react";

export function HeroSection() {
  const [isMuted, setIsMuted] = useState(true);
  const videoRef = useRef<HTMLVideoElement>(null);

  const toggleMute = () => {
    if (videoRef.current) {
      videoRef.current.muted = !isMuted;
      setIsMuted(!isMuted);
    }
  };

  return (
    <section className="relative overflow-hidden">
      {/* Video Background */}
      <div className="absolute inset-0 z-0">
        <video
          ref={videoRef}
          autoPlay
          loop
          muted
          playsInline
          className="absolute inset-0 w-full h-full object-cover"
          poster="/hero-poster.jpg"
        >
          {/* Add your video source here */}
          {/* <source src="/hero-video.mp4" type="video/mp4" /> */}
        </video>
        {/* Dark overlay for text readability */}
        <div className="absolute inset-0 bg-black/60" />
      </div>

      {/* Fallback gradient if no video */}
      <div className="absolute inset-0 bg-gradient-to-b from-black via-black/95 to-black z-0" />
      
      {/* Subtle grid pattern */}
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff08_1px,transparent_1px),linear-gradient(to_bottom,#ffffff08_1px,transparent_1px)] bg-[size:4rem_4rem] [mask-image:radial-gradient(ellipse_60%_50%_at_50%_0%,#000_70%,transparent_110%)] z-0" />

      {/* Mute toggle (only show if video is present) */}
      {/* 
      <button
        onClick={toggleMute}
        className="absolute bottom-8 right-8 z-20 p-3 rounded-full bg-white/10 backdrop-blur-sm hover:bg-white/20 transition-colors"
      >
        {isMuted ? (
          <VolumeX className="h-5 w-5 text-white" />
        ) : (
          <Volume2 className="h-5 w-5 text-white" />
        )}
      </button>
      */}

      <div className="container relative z-10 mx-auto px-4 pt-32 pb-16">
        <div className="max-w-4xl mx-auto text-center">
          {/* Headline */}
          <motion.h1
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.1 }}
            className="text-4xl sm:text-5xl md:text-6xl lg:text-7xl font-bold tracking-tight text-white"
          >
            Most vision-language models can't out-fly a hovering drone.
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.2 }}
            className="mt-6 text-lg sm:text-xl text-white/70 max-w-2xl mx-auto"
          >
            We ran 10,200 closed-loop flight trials across 25 VLMs. The best
            end-to-end model beat a stationary hover by just 0.8 m; most were
            worse than doing nothing. Astral's modular stack reached 1.04 m —
            and the full benchmark, dataset, and code are open.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.3 }}
            className="mt-8 flex flex-wrap gap-4 justify-center"
          >
            <a
              href="/benchmark"
              className="inline-flex items-center px-6 py-3 rounded-lg bg-amber-500 text-black font-semibold hover:bg-amber-400 transition-colors"
            >
              See the benchmark
            </a>
            <a
              href="/docs"
              className="inline-flex items-center px-6 py-3 rounded-lg border border-white/30 text-white font-semibold hover:bg-white/10 transition-colors"
            >
              Start building
            </a>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
