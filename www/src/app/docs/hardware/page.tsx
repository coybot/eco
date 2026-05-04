import { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export const metadata: Metadata = {
  title: "Hardware Setup",
  description: "Unbox, inspect, and prepare your Astral M1-A hardware.",
};

const checklist = [
  "Inspect frame, props, and payload mounts",
  "Charge and insert intelligent battery packs",
  "Power controller first, then drone",
  "Verify camera, telemetry, and control link",
  "Perform hover test in an open area",
];

export default function HardwareSetupPage() {
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
            <Badge className="mb-4 bg-amber-500/10 text-amber-500">M1-A</Badge>
            <h1 className="text-4xl font-bold mb-4">Hardware Setup</h1>
            <p className="text-muted-foreground">
              Bring up your M1-A safely and consistently before your first
              autonomous mission.
            </p>
          </div>
        </section>

        <section className="py-12 bg-background">
          <div className="container mx-auto px-4 max-w-4xl space-y-6">
            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Setup checklist</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {checklist.map((item) => (
                  <div key={item} className="flex items-center text-sm">
                    <CheckCircle2 className="h-4 w-4 text-amber-500 mr-2" />
                    <span className="text-muted-foreground">{item}</span>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Field readiness checks</CardTitle>
              </CardHeader>
              <CardContent className="text-muted-foreground text-sm space-y-2">
                <p>Confirm GNSS lock and stable IMU readings before takeoff.</p>
                <p>Validate return-to-home behavior in a controlled test area.</p>
                <p>Keep spare props and batteries available for mission continuity.</p>
              </CardContent>
            </Card>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
