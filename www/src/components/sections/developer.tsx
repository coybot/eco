"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Github, BookOpen, Terminal, ArrowRight, Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useState } from "react";

const codeExample = `from astral import AstralClient

client = AstralClient(api_key=os.environ["ASTRAL_API_KEY"])

# Create a mission
mission = client.missions.create(
    name="Survey Mission",
    waypoints=[
        {"lat": 37.7749, "lng": -122.4194, "alt": 50},
        {"lat": 37.7751, "lng": -122.4180, "alt": 50},
    ],
    config={
        "return_to_home": True,
        "obstacle_avoidance": True,
    },
)

# Deploy to fleet
client.fleet.deploy(
    mission_id=mission.id,
    drone_ids=["drone-001", "drone-002"],
)`;

const resources = [
  {
    icon: BookOpen,
    title: "Documentation",
    description: "Comprehensive guides and tutorials",
    href: "/docs",
  },
  {
    icon: Terminal,
    title: "API Reference",
    description: "Complete API documentation",
    href: "/docs/api",
  },
  {
    icon: Github,
    title: "Open Source",
    description: "Contribute on GitHub",
    href: "https://github.com/astral-us",
  },
];

export function DeveloperSection() {
  const [copied, setCopied] = useState(false);

  const copyInstall = () => {
    navigator.clipboard.writeText("uv add astral-sdk");
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="py-24 bg-background">
      <div className="container mx-auto px-4">
        <div className="grid lg:grid-cols-2 gap-12 items-center max-w-6xl mx-auto">
          {/* Left Column - Code Example */}
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5 }}
          >
            {/* Install Command */}
            <div className="mb-4">
              <button
                onClick={copyInstall}
                className="inline-flex items-center space-x-2 bg-card border border-border rounded-lg px-4 py-2 font-mono text-sm hover:border-amber-500/50 transition-colors group"
              >
                <span className="text-muted-foreground">$</span>
                <span>uv add astral-sdk</span>
                {copied ? (
                  <Check className="h-4 w-4 text-success" />
                ) : (
                  <Copy className="h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors" />
                )}
              </button>
            </div>

            {/* Code Block */}
            <div className="relative bg-card border border-border rounded-lg overflow-hidden">
              <div className="flex items-center space-x-2 px-4 py-2 border-b border-border bg-secondary/30">
                <div className="flex space-x-1.5">
                  <div className="w-3 h-3 rounded-full bg-red-500/50" />
                  <div className="w-3 h-3 rounded-full bg-yellow-500/50" />
                  <div className="w-3 h-3 rounded-full bg-green-500/50" />
                </div>
                <span className="text-xs text-muted-foreground font-mono">
                  example.py
                </span>
              </div>
              <pre className="p-4 overflow-x-auto text-sm">
                <code className="font-mono text-muted-foreground">
                  {codeExample}
                </code>
              </pre>
            </div>
          </motion.div>

          {/* Right Column - Content */}
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5 }}
          >
            <h2 className="text-3xl sm:text-4xl font-bold mb-4">
              Built for Developers
            </h2>
            <p className="text-lg text-muted-foreground mb-8">
              Train pre-configured LLMs or bring your own. Rapidly develop using 
              proven, open source software. Sell your apps on the Astral App Store 
              or build for your specific needs.
            </p>

            {/* Resources Grid */}
            <div className="grid sm:grid-cols-2 gap-4 mb-8">
              {resources.map((resource) => (
                <Link
                  key={resource.title}
                  href={resource.href}
                  className="flex items-start space-x-3 p-3 rounded-lg hover:bg-card transition-colors group"
                >
                  <div className="p-2 rounded-md bg-amber-500/10">
                    <resource.icon className="h-4 w-4 text-amber-500" />
                  </div>
                  <div>
                    <h3 className="font-medium group-hover:text-amber-500 transition-colors">
                      {resource.title}
                    </h3>
                    <p className="text-sm text-muted-foreground">
                      {resource.description}
                    </p>
                  </div>
                </Link>
              ))}
            </div>

            <Link href="/docs/quickstart">
              <Button size="lg" className="glow">
                Start Building
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </Link>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
