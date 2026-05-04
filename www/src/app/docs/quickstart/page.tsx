import { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Quickstart",
  description: "Get your Astral drone flying in under 10 minutes.",
};

export default function QuickstartPage() {
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
            <h1 className="text-4xl font-bold mb-4">Quickstart</h1>
            <p className="text-muted-foreground">
              Get your drone connected, armed, and safely airborne in a few
              minutes.
            </p>
          </div>
        </section>

        <section className="py-12 bg-background">
          <div className="container mx-auto px-4 max-w-4xl space-y-6">
            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Prerequisites</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="list-disc pl-5 text-muted-foreground space-y-2">
                  <li>Astral M1-A drone with charged batteries</li>
                  <li>Controller powered on and paired</li>
                  <li>
                    Astral mobile app (iOS/Android) installed on your phone
                  </li>
                  <li>Open test area with clear line of sight</li>
                </ul>
              </CardContent>
            </Card>

            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Install SDK</CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="rounded-md border border-border bg-background p-4 text-sm overflow-x-auto">
{`# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create project
uv init my-drone-app && cd my-drone-app

# Add SDK
uv add astral-sdk`}
                </pre>
              </CardContent>
            </Card>

            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>First Mission Script</CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="rounded-md border border-border bg-background p-4 text-sm overflow-x-auto">
{`from astral import AstralClient
import os

client = AstralClient(api_key=os.environ["ASTRAL_API_KEY"])

mission = client.missions.create(
    name="Quickstart Mission",
    waypoints=[
        {"lat": 37.7749, "lng": -122.4194, "alt": 50},
        {"lat": 37.7751, "lng": -122.4180, "alt": 50},
    ],
)

client.fleet.deploy(mission_id=mission.id, drone_ids=["drone-001"])`}
                </pre>
              </CardContent>
            </Card>

            <div className="flex flex-wrap gap-3">
              <Link href="/docs/sdk">
                <Button>
                  Next: SDK Overview
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
