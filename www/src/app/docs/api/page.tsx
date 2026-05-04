import { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";

export const metadata: Metadata = {
  title: "API Reference",
  description: "Practical API reference for missions, fleet, and telemetry.",
};

const endpoints = [
  {
    method: "POST",
    path: "/v1/missions",
    description: "Create a new mission with waypoints and configuration.",
  },
  {
    method: "POST",
    path: "/v1/fleet/deploy",
    description: "Deploy an existing mission to one or more drones.",
  },
  {
    method: "GET",
    path: "/v1/telemetry/stream",
    description: "Stream live drone telemetry and mission state.",
  },
];

export default function ApiReferencePage() {
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
            <h1 className="text-4xl font-bold mb-4">API Reference</h1>
            <p className="text-muted-foreground">
              Core endpoints and request patterns used by the SDK and Mission
              Control.
            </p>
          </div>
        </section>

        <section className="py-12 bg-background">
          <div className="container mx-auto px-4 max-w-4xl space-y-6">
            {endpoints.map((endpoint) => (
              <Card key={endpoint.path} className="bg-card border-border">
                <CardHeader>
                  <CardTitle className="text-base">
                    <span className="text-amber-500 mr-2">{endpoint.method}</span>
                    {endpoint.path}
                  </CardTitle>
                  <CardDescription>{endpoint.description}</CardDescription>
                </CardHeader>
              </Card>
            ))}

            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Example request</CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="rounded-md border border-border bg-background p-4 text-sm overflow-x-auto">
{`curl -X POST https://api.astral.us/v1/missions \\
  -H "Authorization: Bearer $ASTRAL_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "Survey Mission",
    "waypoints": [
      {"lat": 37.7749, "lng": -122.4194, "alt": 50}
    ]
  }'`}
                </pre>
              </CardContent>
            </Card>

            <div className="flex flex-wrap gap-3">
              <Link href="/docs/hardware">
                <Button>
                  Next: Hardware Setup
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
