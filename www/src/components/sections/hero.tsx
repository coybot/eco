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
            The Autonomous Drone Platform.
          </motion.h1>

        </div>
      </div>
    </section>
  );
}
