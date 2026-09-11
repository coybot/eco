import type { Metadata } from "next";
import Link from "next/link";
import { Github, ArrowRight } from "lucide-react";
import { JsonLd } from "@/components/json-ld";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const TITLE = "The Open-Source Presidio Autonomy Stack";
const DESCRIPTION =
  "presidio-sdk, eco, and presidio-docs: an open-source Python/Swift SDK for ArduPilot drones and phone-brained rovers, a 16-scenario adversarial simulation benchmark, and the documentation to run it all yourself.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  ...socialMeta("/stack", TITLE, DESCRIPTION),
};

const repos = [
  {
    name: "presidio-sdk",
    href: SITE.presidioSdk,
    tagline: "Drones that finish the mission when the network doesn't.",
    body: "Python package for ArduPilot-based drones (MAVLink primitives, optional OAK-D/RealSense camera drivers), plus Swift packages for Phrover, a phone-brained rover. Everything that runs on the drone or the phone is open, including the on-device reasoning loop — only the cloud fleet backend is closed.",
    install: "pip install git+https://github.com/presidio-autonomy/presidio-sdk",
  },
  {
    name: "eco",
    href: SITE.eco,
    tagline: "Simulation and the 16-scenario adversarial benchmark.",
    body: "Sensor dropout, GPS spoofing, dynamic intruders, and tight chokepoints, with automated intervention grading. SITL and Isaac Sim paths, and the on-device autonomy daemon. The scorecards committed here are baseline runs, not the L5 result: they top out at L2 with 62% success and 40 interventions, and the learned policies sit at L1 with 6–12% success.",
    install: null,
  },
  {
    name: "presidio-docs",
    href: SITE.presidioDocs,
    tagline: "Guides and reference documentation.",
    body: "Mintlify sources: introduction, quickstart, autonomy levels, SITL simulation, and Isaac Sim perception-in-the-loop.",
    install: null,
  },
];

const jsonLd = {
  "@context": "https://schema.org",
  "@graph": [
    ...repos.map((r) => ({
      "@type": "SoftwareSourceCode",
      name: r.name,
      description: r.tagline,
      codeRepository: r.href,
      license: "https://www.apache.org/licenses/LICENSE-2.0",
    })),
    {
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "Home", item: SITE.origin },
        { "@type": "ListItem", position: 2, name: "Stack", item: `${SITE.origin}/stack` },
      ],
    },
  ],
};

export default function StackPage() {
  return (
    <>
      <JsonLd data={jsonLd} />
      <section className="py-16 bg-gradient-to-b from-background to-card">
        <div className="container mx-auto px-4 max-w-4xl">
          <h1 className="text-4xl sm:text-5xl font-bold mb-6">{TITLE}</h1>
          <p className="text-lg text-muted-foreground mb-10">{DESCRIPTION}</p>

          <div className="space-y-6">
            {repos.map((r) => (
              <Card key={r.name} className="bg-card border-border">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between flex-wrap gap-2">
                    <span className="font-mono">{r.name}</span>
                    <Button asChild variant="secondary" size="sm">
                      <a href={r.href} target="_blank" rel="noopener noreferrer">
                        <Github className="mr-2 h-4 w-4" />
                        View on GitHub
                      </a>
                    </Button>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="font-medium text-foreground">{r.tagline}</p>
                  <p className="mt-2 text-muted-foreground">{r.body}</p>
                  {r.install && (
                    <pre className="mt-4 rounded-md bg-background border border-border p-3 text-sm overflow-x-auto">
                      <code>{r.install}</code>
                    </pre>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>

          <div className="mt-12 border-t border-border pt-8">
            <h2 className="text-xl font-semibold mb-3">Datasets and models</h2>
            <p className="text-muted-foreground">
              The Yonder navigation dataset and pretrained models are hosted on HuggingFace
              under a legacy namespace (a real org migration hasn&rsquo;t happened yet):{" "}
              <a href={SITE.yonderDataset} target="_blank" rel="noopener noreferrer" className="text-amber-500 hover:underline">
                {SITE.yonderDataset}
              </a>
              . A ~500 MB sample is available at{" "}
              <a href={SITE.yonderSample} target="_blank" rel="noopener noreferrer" className="text-amber-500 hover:underline">
                {SITE.yonderSample}
              </a>
              .
            </p>
          </div>

          <div className="mt-8 border-t border-border pt-8">
            <h2 className="text-xl font-semibold mb-3">Licensing and contributing</h2>
            <p className="text-muted-foreground">
              Each repo carries its own LICENSE/NOTICE (MIT or Apache-2.0). The Yonder dataset
              is not under those terms — it is CC-BY-NC-4.0, non-commercial use only. PRs are welcome —
              open an issue first for anything substantive so we can align on scope.
            </p>
          </div>

          <div className="mt-12">
            <Button asChild size="lg">
              <Link href="/get-started">
                Get started
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </div>
        </div>
      </section>
    </>
  );
}
