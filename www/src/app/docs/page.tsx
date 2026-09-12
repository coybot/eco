import { Metadata } from "next";
import Link from "next/link";
import { FlaskConical, Smartphone, Github, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { SITE } from "@/lib/site";

export const metadata: Metadata = {
  title: "Documentation",
  description:
    "Comprehensive documentation for Coybot drones, SDK, and Mission Control platform.",
};

const quickLinks = [
  {
    title: "Run in Simulation",
    description: "Test your drone logic in ArduPilot SITL or Isaac Sim",
    icon: FlaskConical,
    href: "/docs/simulation",
    external: false,
  },
  {
    title: "Mobile App",
    description: "iOS operator app for real-time control and monitoring",
    icon: Smartphone,
    href: "/docs/mobile-app",
    external: false,
  },
  {
    title: "SDK & API on GitHub",
    description: "Full source, quickstart, and API reference",
    icon: Github,
    href: SITE.coybotSdk,
    external: true,
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
                SDK guides, simulation setup, and API reference. Full documentation lives on GitHub.
              </p>
            </div>
          </div>
        </section>

        {/* Quick Links */}
        <section className="py-12 bg-card">
          <div className="container mx-auto px-4">
            <div className="grid sm:grid-cols-3 gap-6 max-w-4xl mx-auto">
              {quickLinks.map((link) => {
                const inner = (
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
                );
                return link.external ? (
                  <a key={link.title} href={link.href} target="_blank" rel="noopener noreferrer">
                    {inner}
                  </a>
                ) : (
                  <Link key={link.title} href={link.href}>{inner}</Link>
                );
              })}
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

# Add the Coybot SDK
uv add coybot-sdk

# Run your first script
uv run python main.py`}
                  </code>
                </pre>
              </div>

              <div className="text-center mt-8">
                <a href={SITE.coybotSdk} target="_blank" rel="noopener noreferrer">
                  <Button className="glow">
                    View docs on GitHub
                    <ArrowRight className="ml-2 h-4 w-4" />
                  </Button>
                </a>
              </div>
            </div>
          </div>
        </section>

      </main>
      <Footer />
    </div>
  );
}
