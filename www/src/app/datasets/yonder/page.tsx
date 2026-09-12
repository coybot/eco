import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const PAGE_DESCRIPTION =
  "Yonder is a large-scale drone-perspective indoor navigation dataset on Hugging Face, designed for serious perception research and cross-simulator closed-loop evaluation.";

export const metadata: Metadata = {
  title: "Yonder dataset",
  description: PAGE_DESCRIPTION,
  ...socialMeta(
    "/datasets/yonder",
    "Yonder dataset | Coybot",
    PAGE_DESCRIPTION
  ),
};

export default function YonderDatasetPage() {
  const datasetLd = {
    "@context": "https://schema.org",
    "@type": "Dataset",
    name: "Yonder",
    description: PAGE_DESCRIPTION,
    url: SITE.yonderDataset,
    license: "https://creativecommons.org/licenses/by-nc/4.0/",
    creator: { "@type": "Organization", name: "Coybot", url: SITE.origin },
    isAccessibleForFree: true,
    distribution: {
      "@type": "DataDownload",
      contentUrl: SITE.yonderDataset,
      encodingFormat: "application/zip",
    },
  };

  const webPageLd = {
    "@context": "https://schema.org",
    "@type": "WebPage",
    name: "Yonder — Drone navigation dataset",
    url: `${SITE.origin}/datasets/yonder`,
    description: PAGE_DESCRIPTION,
    about: { "@type": "Dataset", name: "Yonder", url: SITE.yonderDataset },
  };

  return (
    <div className="min-h-screen flex flex-col">
      <JsonLd data={datasetLd} />
      <JsonLd data={webPageLd} />
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <p className="text-sm text-amber-500 mb-2">Dataset</p>
            <h1 className="text-4xl sm:text-5xl font-bold mb-6">
              Yonder — drone-perspective indoor navigation at scale
            </h1>
            <p className="text-lg text-muted-foreground mb-8">
              Yonder pairs a very large drone-view indoor corpus with a clear
              evaluation story:{" "}
              <strong className="text-foreground">
                offline perception metrics are not a substitute for closed-loop
                flight tests
              </strong>
              , especially when training and evaluation simulators differ.
            </p>
            <div className="flex flex-wrap gap-3">
              <Button asChild>
                <a href={SITE.yonderDataset} target="_blank" rel="noopener noreferrer">
                  Open on Hugging Face
                </a>
              </Button>
              <Button variant="outline" asChild>
                <a href={SITE.yonderSample} target="_blank" rel="noopener noreferrer">
                  Small sample (~500 MB)
                </a>
              </Button>
              <Button variant="outline" asChild>
                <Link href="/research#yonder">Research summary</Link>
              </Button>
            </div>
          </div>
        </section>

        <section className="py-12 bg-background">
          <div className="container mx-auto px-4 max-w-4xl space-y-8">
            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Headline facts</CardTitle>
              </CardHeader>
              <CardContent className="text-muted-foreground space-y-3">
                <ul className="list-disc pl-6 space-y-2">
                  <li>Millions of drone-perspective frames across many indoor scenes</li>
                  <li>
                    Rich sensing per waypoint (stereo RGB, depth, IR, panoramic
                    LiDAR-style data, semantic segmentation, pose)
                  </li>
                  <li>
                    Public host:{" "}
                    <a
                      className="text-amber-500 underline"
                      href={SITE.yonderDataset}
                    >
                      {SITE.yonderDataset}
                    </a>
                  </li>
                  <li>
                    License inherits HSSD non-commercial terms (CC-BY-NC-4.0);
                    check the dataset card before commercial use
                  </li>
                </ul>
              </CardContent>
            </Card>

            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Who this is for</CardTitle>
              </CardHeader>
              <CardContent className="text-muted-foreground space-y-4">
                <p>
                  Robotics and ML teams training open-vocabulary detectors,
                  depth estimators, semantic models, and other perception modules
                  for drone-view indoor navigation.
                </p>
                <p>
                  Researchers studying sim-to-sim transfer: the dataset is
                  explicitly motivated by cross-simulator pitfalls that are easy
                  to miss if you only watch offline mAP move.
                </p>
              </CardContent>
            </Card>

            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Quick start</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 text-muted-foreground">
                <p>
                  The Hugging Face README includes a minimal download snippet
                  (single-scene smoke test) and notes on repository layout. For a
                  tiny local download before multi-terabyte transfers, start with{" "}
                  <a
                    className="text-amber-500 underline"
                    href={SITE.yonderSample}
                  >
                    coybothf/yonder-sample
                  </a>
                  .
                </p>
                <pre className="rounded-md border border-border bg-background p-4 text-sm overflow-x-auto text-foreground/90">
{`from huggingface_hub import snapshot_download

path = snapshot_download(
    repo_id="coybothf/yonder",
    repo_type="dataset",
    allow_patterns="indoor/drone-data/augmented/hssd-102343992/*.npz",
)`}
                </pre>
              </CardContent>
            </Card>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
