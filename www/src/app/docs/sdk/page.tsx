import { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Check } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "SDK Overview",
  description: "Core concepts and first steps for the Astral SDK.",
};

const capabilities = [
  "Mission creation and scheduling",
  "Fleet deployment orchestration",
  "Live telemetry ingestion",
  "Simulation-first workflows",
];

export default function SDKOverviewPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <Link
              href="/docs"
              className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground mb-6"
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Documentation
            </Link>
            <h1 className="text-4xl font-bold mb-4">SDK Overview</h1>
            <p className="text-muted-foreground">
              Build autonomous workflows with the Astral Python SDK.
            </p>
          </div>
        </section>

        <section className="py-12 bg-background">
          <div className="container mx-auto px-4 max-w-4xl space-y-6">
            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>What you can do</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-2">
                  {capabilities.map((capability) => (
                    <li key={capability} className="flex items-center text-sm">
                      <Check className="h-4 w-4 text-amber-500 mr-2" />
                      <span className="text-muted-foreground">{capability}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>

            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Authentication</CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="rounded-md border border-border bg-background p-4 text-sm overflow-x-auto">
{`from astral import AstralClient
import os

client = AstralClient(api_key=os.environ["ASTRAL_API_KEY"])`}
                </pre>
              </CardContent>
            </Card>

            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Mission lifecycle</CardTitle>
              </CardHeader>
              <CardContent className="text-muted-foreground text-sm space-y-2">
                <p>1. Create mission plan and waypoints.</p>
                <p>2. Validate mission in simulator.</p>
                <p>3. Deploy to one or more drones.</p>
                <p>4. Observe telemetry and update behavior as needed.</p>
              </CardContent>
            </Card>

            <div className="flex flex-wrap gap-3">
              <Link href="/docs/api">
                <Button>
                  Next: API Reference
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
