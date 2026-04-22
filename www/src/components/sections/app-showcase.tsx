"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Apple, Play } from "lucide-react";
import { Button } from "@/components/ui/button";

export function AppShowcaseSection() {
  return (
    <section className="py-24 bg-card overflow-hidden">
      <div className="container mx-auto px-4">
        <div className="grid lg:grid-cols-2 gap-12 items-center max-w-6xl mx-auto">
          {/* Left Column - Content */}
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5 }}
          >
            <h2 className="text-3xl sm:text-4xl font-bold mb-4">
              Mission Control in Your Pocket
            </h2>
            <p className="text-lg text-muted-foreground mb-8">
              Configure, deploy, monitor and control drone fleets with the Astral 
              app. Build plans and missions confidently. Use our Simulator to run 
              and confirm scenarios before takeoff.
            </p>

            <div className="space-y-4 mb-8">
              <div className="flex items-start space-x-3">
                <div className="w-6 h-6 rounded-full bg-amber-500/10 flex items-center justify-center shrink-0 mt-0.5">
                  <span className="text-amber-500 text-sm font-bold">1</span>
                </div>
                <div>
                  <h3 className="font-semibold">Plan Your Mission</h3>
                  <p className="text-sm text-muted-foreground">
                    Design flight paths, set waypoints, and configure autonomous behaviors.
                  </p>
                </div>
              </div>
              <div className="flex items-start space-x-3">
                <div className="w-6 h-6 rounded-full bg-amber-500/10 flex items-center justify-center shrink-0 mt-0.5">
                  <span className="text-amber-500 text-sm font-bold">2</span>
                </div>
                <div>
                  <h3 className="font-semibold">Simulate & Validate</h3>
                  <p className="text-sm text-muted-foreground">
                    Test your mission in our simulator before deploying to real hardware.
                  </p>
                </div>
              </div>
              <div className="flex items-start space-x-3">
                <div className="w-6 h-6 rounded-full bg-amber-500/10 flex items-center justify-center shrink-0 mt-0.5">
                  <span className="text-amber-500 text-sm font-bold">3</span>
                </div>
                <div>
                  <h3 className="font-semibold">Deploy & Monitor</h3>
                  <p className="text-sm text-muted-foreground">
                    Launch your fleet and monitor real-time telemetry and video feeds.
                  </p>
                </div>
              </div>
            </div>

            {/* App Store Buttons */}
            <div className="flex flex-wrap gap-4">
              <Link href="https://apps.apple.com/us/app/astral-us-drone-operator/id6471107516" target="_blank">
                <Button variant="outline" size="lg" className="h-14 px-6">
                  <Apple className="mr-2 h-6 w-6" />
                  <div className="text-left">
                    <div className="text-xs text-muted-foreground">Download on the</div>
                    <div className="text-sm font-semibold">App Store</div>
                  </div>
                </Button>
              </Link>
              <Link href="https://play.google.com/store/apps/details?id=us.astral.mobile" target="_blank">
                <Button variant="outline" size="lg" className="h-14 px-6">
                  <Play className="mr-2 h-6 w-6" />
                  <div className="text-left">
                    <div className="text-xs text-muted-foreground">Get it on</div>
                    <div className="text-sm font-semibold">Google Play</div>
                  </div>
                </Button>
              </Link>
            </div>
          </motion.div>

          {/* Right Column - App Preview */}
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5 }}
            className="relative"
          >
            {/* Phone Mockup */}
            <div className="relative mx-auto max-w-[300px]">
              {/* Subtle glow effect */}
              <div className="absolute -inset-4 bg-white/5 rounded-[3rem] blur-2xl" />
              
              {/* Phone frame */}
              <div className="relative bg-card border-4 border-secondary rounded-[2.5rem] p-2 shadow-2xl">
                <div className="bg-background rounded-[2rem] overflow-hidden aspect-[9/19]">
                  {/* Status bar */}
                  <div className="h-6 bg-secondary/50 flex items-center justify-center">
                    <div className="w-20 h-1 bg-foreground/20 rounded-full" />
                  </div>
                  
                  {/* App content placeholder */}
                  <div className="p-4 space-y-4">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold">Mission Control</span>
                      <div className="w-8 h-8 rounded-full bg-amber-500/20" />
                    </div>
                    
                    {/* Map placeholder */}
                    <div className="aspect-square bg-secondary/50 rounded-lg flex items-center justify-center">
                      <div className="text-center">
                        <div className="w-12 h-12 mx-auto mb-2 rounded-full bg-amber-500/20 flex items-center justify-center">
                          <div className="w-3 h-3 rounded-full bg-amber-500 animate-pulse" />
                        </div>
                        <span className="text-xs text-muted-foreground">Live Map</span>
                      </div>
                    </div>
                    
                    {/* Stats */}
                    <div className="grid grid-cols-3 gap-2">
                      {["Alt", "Speed", "Battery"].map((stat) => (
                        <div key={stat} className="bg-secondary/30 rounded-md p-2 text-center">
                          <div className="text-xs text-muted-foreground">{stat}</div>
                          <div className="text-sm font-mono font-bold">--</div>
                        </div>
                      ))}
                    </div>
                    
                    {/* Action button */}
                    <div className="bg-amber-500/20 rounded-lg p-3 text-center">
                      <span className="text-sm font-semibold text-amber-500">Launch Mission</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
