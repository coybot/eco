import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const RESEARCH_DESCRIPTION =
  "Astral publishes rigorous autonomy research on vision-language navigation for aerial and ground robots, modular architectures, swarm sensing, and large-scale datasets including Yonder.";

export const metadata: Metadata = {
  title: "Research",
  description: RESEARCH_DESCRIPTION,
  ...socialMeta("/research", "Research | Astral", RESEARCH_DESCRIPTION),
};

type PaperLink =
  | { label: string; href: string; external?: false }
  | { label: string; href: string; external: true };

const papers: Array<{
  id: string;
  title: string;
  venue: string;
  summary: string;
  links: PaperLink[];
}> = [
  {
    id: "yonder",
    title:
      "Yonder: A 4.65M-Frame Drone Navigation Dataset and the Cross-Simulator Generalization Gap",
    venue: "NeurIPS 2026 Datasets & Benchmarks track (submission)",
    summary:
      "Introduces Yonder, a multi-million-frame drone-perspective indoor dataset with rich sensing, and shows why offline detection gains can fail to translate to closed-loop navigation when training and evaluation simulators disagree geometrically.",
    links: [{ label: "Yonder on Hugging Face", href: SITE.yonderDataset, external: true }],
  },
  {
    id: "metric-gap",
    title:
      "Closing the Metric Gap: From Diagnosis to Solution in Vision-Language Drone Navigation",
    venue: "Technical report",
    summary:
      "Large-scale closed-loop benchmark across many VLMs, decomposing failures into semantic understanding versus metric spatial grounding, and a modular architecture that closes the gap on operational commands while prioritizing collision-free flight.",
    links: [
      { label: "Simulation docs", href: "/docs/simulation" },
      { label: "GitHub", href: SITE.githubOrg, external: true },
    ],
  },
  {
    id: "engineering-separation",
    title:
      "Engineering the Separation Principle: From Modular Architecture to Deployable Drone Navigation",
    venue: "Technical report",
    summary:
      "An eighteen-iteration engineering log: improving a modular autonomy stack in aggregate, scaling detector fine-tuning with large synthetic data, diagnosing a cross-simulator localization gap, and characterizing exploration and planning as the next bottlenecks.",
    links: [{ label: "Yonder dataset", href: "/datasets/yonder" }],
  },
  {
    id: "scaling-separation",
    title:
      "Scaling the Separation Principle: Sensing Requirements for 1000-Drone Swarms in Urban and Natural Environments",
    venue: "Technical report",
    summary:
      "Controlled swarm simulations up to 1,000 agents comparing sensing stacks and coordination architectures, with a focus on when ultra-wideband ranging becomes necessary as fleet scale and environment difficulty increase.",
    links: [{ label: "GitHub", href: SITE.githubOrg, external: true }],
  },
  {
    id: "gemma4-pilot",
    title:
      "Gemma 4 E2B as an End-to-End Drone Navigation Controller: A Pilot Trial in the 25-VLM Lineup",
    venue: "Technical note",
    summary:
      "Adds Gemma 4 to the same Isaac Sim closed-loop benchmark and compares end-to-end goal prediction against modular deployment of the same weights as a semantic target selector, illustrating the leverage of the separation principle.",
    links: [{ label: "Metric gap (context)", href: "/blog/metric-gap-vision-language-drone-navigation" }],
  },
];

export default function ResearchPage() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    name: "Astral Research",
    description: RESEARCH_DESCRIPTION,
    url: `${SITE.origin}/research`,
    isPartOf: { "@type": "WebSite", name: "Astral", url: SITE.origin },
  };

  return (
    <div className="min-h-screen flex flex-col">
      <JsonLd data={jsonLd} />
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <h1 className="text-4xl sm:text-5xl font-bold mb-6">Research</h1>
            <p className="text-lg text-muted-foreground mb-8">
              Astral treats autonomous uncrewed systems as a systems problem:
              perception, geometry, planning, safety, simulation fidelity, and honest
              evaluation—whether the robot flies, drives, or does both. These papers
              and notes are the scientific backbone behind our open software,
              datasets, benchmarks, and vehicle programs.
            </p>
            <div className="flex flex-wrap gap-3">
              <Button asChild>
                <Link href="/datasets/yonder">Yonder dataset</Link>
              </Button>
              <Button variant="outline" asChild>
                <a href={SITE.githubOrg}>GitHub</a>
              </Button>
              <Button variant="outline" asChild>
                <a href={SITE.docs}>Documentation</a>
              </Button>
            </div>
          </div>
        </section>

        <section className="py-12 bg-background border-t border-border">
          <div className="container mx-auto px-4 max-w-4xl space-y-8">
            {papers.map((paper) => (
              <Card
                key={paper.id}
                id={paper.id}
                className="bg-card border-border scroll-mt-24"
              >
                <CardHeader>
                  <CardTitle className="text-xl leading-snug">{paper.title}</CardTitle>
                  <p className="text-sm text-muted-foreground">{paper.venue}</p>
                </CardHeader>
                <CardContent className="space-y-4 text-muted-foreground">
                  <p>{paper.summary}</p>
                  <div className="flex flex-wrap gap-3">
                    {paper.links.map((l) =>
                      l.external ? (
                        <Button key={l.href} variant="secondary" size="sm" asChild>
                          <a href={l.href} target="_blank" rel="noopener noreferrer">
                            {l.label}
                          </a>
                        </Button>
                      ) : (
                        <Button key={l.href} variant="secondary" size="sm" asChild>
                          <Link href={l.href}>{l.label}</Link>
                        </Button>
                      )
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
