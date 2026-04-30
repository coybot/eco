import { Metadata } from "next";
import Link from "next/link";
import {
  Rocket,
  Code,
  Terminal,
  BookOpen,
  Cpu,
  ArrowRight,
} from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Documentation",
  description:
    "Comprehensive documentation for Astral drones, SDK, and Mission Control platform.",
};

const quickLinks = [
  {
    title: "Quickstart",
    description: "Get your drone flying in under 10 minutes",
    icon: Rocket,
    href: "/docs/quickstart",
  },
  {
    title: "SDK Overview",
    description: "Build custom applications with our SDK",
    icon: Code,
    href: "/docs/sdk",
  },
  {
    title: "API Reference",
    description: "Complete REST API documentation",
    icon: Terminal,
    href: "/docs/api",
  },
  {
    title: "Hardware Setup",
    description: "Unbox and configure your drone",
    icon: Cpu,
    href: "/docs/hardware",
  },
];

const sections = [
  {
    title: "Getting Started",
    items: [
      { name: "Introduction", href: "/docs/introduction" },
      { name: "Quickstart Guide", href: "/docs/quickstart" },
      { name: "Installation", href: "/docs/installation" },
    ],
  },
  {
    title: "Hardware",
    items: [
      { name: "M1-A Quadcopter", href: "/docs/hardware/m1a" },
      { name: "Setup Guide", href: "/docs/hardware/setup" },
      { name: "Maintenance", href: "/docs/hardware/maintenance" },
    ],
  },
  {
    title: "SDK",
    items: [
      { name: "Overview", href: "/docs/sdk/overview" },
      { name: "Authentication", href: "/docs/sdk/authentication" },
      { name: "Missions", href: "/docs/sdk/missions" },
      { name: "Telemetry", href: "/docs/sdk/telemetry" },
      { name: "Fleet Management", href: "/docs/sdk/fleet" },
    ],
  },
  {
    title: "Mission Control",
    items: [
      { name: "Overview", href: "/docs/mission-control" },
      { name: "Mission Planning", href: "/docs/mission-control/planning" },
      { name: "Simulation", href: "/docs/simulation" },
      { name: "Deployment", href: "/docs/mission-control/deployment" },
    ],
  },
];

export default function DocsPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        {/* Hero */}
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-4xl sm:text-5xl font-bold mb-6">
                Documentation
              </h1>
              <p className="text-lg text-muted-foreground mb-8">
                Everything you need to build with Astral. From getting started
                to advanced fleet management.
              </p>

              {/* Search placeholder */}
              <div className="max-w-xl mx-auto">
                <div className="relative">
                  <input
                    type="text"
                    placeholder="Search documentation..."
                    className="w-full h-12 px-4 pr-12 rounded-lg border border-border bg-card text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                  />
                  <kbd className="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none hidden sm:inline-flex h-6 items-center gap-1 rounded border border-border bg-muted px-2 font-mono text-xs text-muted-foreground">
                    ⌘K
                  </kbd>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Quick Links */}
        <section className="py-12 bg-card">
          <div className="container mx-auto px-4">
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6 max-w-6xl mx-auto">
              {quickLinks.map((link) => (
                <Link key={link.title} href={link.href}>
                  <Card className="h-full bg-background border-border hover:border-amber-500/50 transition-colors cursor-pointer group">
                    <CardContent className="p-6">
                      <div className="inline-flex p-3 rounded-lg bg-amber-500/10 mb-4">
                        <link.icon className="h-6 w-6 text-amber-500" />
                      </div>
                      <h3 className="font-semibold mb-2 group-hover:text-amber-500 transition-colors">
                        {link.title}
                      </h3>
                      <p className="text-sm text-muted-foreground">
                        {link.description}
                      </p>
                    </CardContent>
                  </Card>
                </Link>
              ))}
            </div>
          </div>
        </section>

        {/* Documentation Sections */}
        <section className="py-16 bg-background">
          <div className="container mx-auto px-4">
            <div className="max-w-6xl mx-auto">
              <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-8">
                {sections.map((section) => (
                  <div key={section.title}>
                    <h2 className="font-semibold text-lg mb-4">
                      {section.title}
                    </h2>
                    <ul className="space-y-2">
                      {section.items.map((item) => (
                        <li key={item.name}>
                          <Link
                            href={item.href}
                            className="text-muted-foreground hover:text-foreground transition-colors text-sm flex items-center group"
                          >
                            <BookOpen className="h-3 w-3 mr-2 opacity-0 group-hover:opacity-100 transition-opacity" />
                            {item.name}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* Code Example */}
        <section className="py-16 bg-card">
          <div className="container mx-auto px-4">
            <div className="max-w-4xl mx-auto">
              <h2 className="text-2xl font-bold mb-8 text-center">
                Start Building in Minutes
              </h2>

              <div className="bg-background rounded-lg border border-border overflow-hidden">
                <div className="flex items-center space-x-2 px-4 py-2 border-b border-border bg-secondary/30">
                  <div className="flex space-x-1.5">
                    <div className="w-3 h-3 rounded-full bg-red-500/50" />
                    <div className="w-3 h-3 rounded-full bg-yellow-500/50" />
                    <div className="w-3 h-3 rounded-full bg-green-500/50" />
                  </div>
                  <span className="text-xs text-muted-foreground font-mono">
                    Terminal
                  </span>
                </div>
                <pre className="p-4 overflow-x-auto text-sm font-mono">
                  <code className="text-muted-foreground">
{`# Install uv (if you haven't already)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create a new project
uv init my-drone-app && cd my-drone-app

# Add the Astral SDK
uv add astral-sdk

# Run your first script
uv run python main.py`}
                  </code>
                </pre>
              </div>

              <div className="text-center mt-8">
                <Link href="/docs/quickstart">
                  <Button className="glow">
                    Read the Quickstart
                    <ArrowRight className="ml-2 h-4 w-4" />
                  </Button>
                </Link>
              </div>
            </div>
          </div>
        </section>

      </main>
      <Footer />
    </div>
  );
}
