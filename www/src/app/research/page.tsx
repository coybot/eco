import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";
import { researchPapers } from "@/lib/research-data";
import { blogPosts } from "@/lib/blog-data";

const RESEARCH_DESCRIPTION =
  "Astral publishes rigorous autonomy research on vision-language navigation for aerial and ground robots, modular architectures, swarm sensing, and large-scale datasets including Yonder.";

export const metadata: Metadata = {
  title: "Research",
  description: RESEARCH_DESCRIPTION,
  ...socialMeta("/research", "Research | Astral", RESEARCH_DESCRIPTION),
};

// Build a unified list of papers + standalone blog posts, newest first.
// Papers that have a companion post are shown once (as a paper entry).
// Blog posts that are a companion to a paper are excluded (the paper card covers them).
const companionSlugs = new Set(
  researchPapers.map((p) => p.companionPostSlug).filter(Boolean)
);

type PaperEntry = {
  kind: "paper";
  dateIso: string;
  slug: string;
  title: string;
  venue: string;
  summary: string;
  href: string;
  companionPostSlug?: string;
};

type PostEntry = {
  kind: "post";
  dateIso: string;
  slug: string;
  title: string;
  description: string;
  category: string;
  href: string;
};

type Entry = PaperEntry | PostEntry;

const paperEntries: PaperEntry[] = researchPapers.map((p) => ({
  kind: "paper",
  dateIso: p.dateIso,
  slug: p.slug,
  title: p.title,
  venue: p.venue,
  summary: p.summary,
  href: p.companionPostSlug
    ? `/blog/${p.companionPostSlug}#paper`
    : `/research/${p.slug}`,
  companionPostSlug: p.companionPostSlug,
}));

const postEntries: PostEntry[] = blogPosts
  .filter((p) => !companionSlugs.has(p.slug))
  .map((p) => ({
    kind: "post",
    dateIso: p.dateIso,
    slug: p.slug,
    title: p.title,
    description: p.description,
    category: p.category,
    href: `/blog/${p.slug}`,
  }));

const entries: Entry[] = [...paperEntries, ...postEntries].sort((a, b) =>
  b.dateIso.localeCompare(a.dateIso)
);

export default function ResearchPage() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    name: "Astral Research",
    description: RESEARCH_DESCRIPTION,
    url: `${SITE.origin}/research`,
    isPartOf: { "@type": "WebSite", name: "Astral", url: SITE.origin },
    hasPart: researchPapers.map((p) => ({
      "@type": p.schemaType === "Dataset" ? "Dataset" : "Report",
      name: p.title,
      url: p.companionPostSlug
        ? `${SITE.origin}/blog/${p.companionPostSlug}#paper`
        : `${SITE.origin}/research/${p.slug}`,
    })),
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
              Papers, technical reports, and write-ups from the Astral autonomy
              team — ordered by date.
            </p>
            <div className="flex flex-wrap gap-3 mb-16">
              <Button asChild>
                <Link href="/datasets/yonder">Yonder dataset</Link>
              </Button>
              <Button variant="outline" asChild>
                <a href={SITE.githubOrg} target="_blank" rel="noopener noreferrer">GitHub</a>
              </Button>
              <Button variant="outline" asChild>
                <a href={SITE.droneModels} target="_blank" rel="noopener noreferrer">Models on HF</a>
              </Button>
            </div>

            {/* L5 callout */}
            <div className="mb-10">
              <div className="inline-flex items-center gap-2 bg-amber-500/10 border border-amber-500/20 rounded-full px-4 py-1.5 mb-6">
                <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
                <span className="text-sm font-medium text-amber-500">L5 achieved — June 2026</span>
              </div>
              <h2 className="text-2xl sm:text-3xl font-bold mb-4">
                What &ldquo;autonomy&rdquo; actually means
              </h2>
              <p className="text-muted-foreground mb-4">
                Most drone platforms advertise &ldquo;autonomous flight.&rdquo; What they mean is: follow a GPS waypoint list, and if the link drops, return-to-home or land in place. That&rsquo;s not autonomy — that&rsquo;s a fancy RC plane with a panic button.
              </p>
              <p className="text-muted-foreground mb-8">
                Real autonomy means the aircraft handles sensor failures, unexpected obstacles, GPS spoofing, dynamic adversaries, and comms blackout — without a human in the loop. We measure this on a five-level scale. As of June 2026, our heterogeneous fleet (quadcopters + ground rovers) achieves L5 across a 16-scenario adversarial benchmark: zero interventions, 100% mission success, zero collisions.
              </p>

              <div className="overflow-x-auto mb-8">
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left py-2 pr-6 font-semibold text-foreground w-16">Level</th>
                      <th className="text-left py-2 pr-6 font-semibold text-foreground">Intervention rate</th>
                      <th className="text-left py-2 font-semibold text-foreground">What it looks like in practice</th>
                    </tr>
                  </thead>
                  <tbody className="text-muted-foreground">
                    <tr className="border-b border-border/50">
                      <td className="py-2 pr-6 font-mono">L1</td>
                      <td className="py-2 pr-6">&gt;2 per scenario</td>
                      <td className="py-2">Operator corrects the aircraft constantly. &ldquo;Autonomous&rdquo; in name only.</td>
                    </tr>
                    <tr className="border-b border-border/50">
                      <td className="py-2 pr-6 font-mono">L2</td>
                      <td className="py-2 pr-6">1–2 per scenario</td>
                      <td className="py-2">GPS waypoint following. Fails on sensor dropout or unexpected obstacles.</td>
                    </tr>
                    <tr className="border-b border-border/50">
                      <td className="py-2 pr-6 font-mono">L3</td>
                      <td className="py-2 pr-6">0.5–1 per scenario</td>
                      <td className="py-2">Handles most cases. Stalls or collides on edge cases.</td>
                    </tr>
                    <tr className="border-b border-border/50">
                      <td className="py-2 pr-6 font-mono">L4</td>
                      <td className="py-2 pr-6">&lt;0.5, ≥90% success</td>
                      <td className="py-2">Rarely needs a human. Still fails under combined sensor + comms degradation.</td>
                    </tr>
                    <tr>
                      <td className="py-2 pr-6 font-mono text-amber-500 font-semibold">L5</td>
                      <td className="py-2 pr-6 text-amber-500 font-semibold">0 — 100% success</td>
                      <td className="py-2 text-foreground font-medium">Zero interventions. No collisions. Handles every adversarial inject across all 16 scenarios.</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <p className="text-sm text-muted-foreground mb-6">
                The 16-scenario benchmark covers: sensor dropout, GPS spoofing, RF jamming, dynamic intruders, communications blackout, tight chokepoints, time-critical extraction, cascading failures, and simultaneous multi-inject stress tests. The video below shows all 16 — in 2D simulation, 3D visualization, and photorealistic render.
              </p>

              <div className="relative w-full rounded-lg border border-border overflow-hidden" style={{ aspectRatio: "16/9" }}>
                <iframe
                  className="absolute inset-0 w-full h-full"
                  src="https://www.youtube.com/embed/hePElM49LFE?autoplay=1&rel=0"
                  title="Astral L5 Autonomy Demo Reel"
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  allowFullScreen
                />
              </div>
            </div>
          </div>
        </section>

        <section className="py-12 bg-background border-t border-border">
          <div className="container mx-auto px-4 max-w-4xl space-y-6">
            {entries.map((entry) =>
              entry.kind === "paper" ? (
                <Card
                  key={entry.slug}
                  id={entry.slug}
                  className="bg-card border-border scroll-mt-24"
                >
                  <CardHeader>
                    <div className="flex items-center gap-2 mb-1">
                      <Badge className="bg-amber-500/10 text-amber-500 border-0 text-xs">
                        Paper
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {entry.dateIso}
                      </span>
                    </div>
                    <CardTitle className="text-xl leading-snug">
                      <Link
                        href={entry.href}
                        className="hover:text-amber-500 transition-colors"
                      >
                        {entry.title}
                      </Link>
                    </CardTitle>
                    <p className="text-sm text-muted-foreground">{entry.venue}</p>
                  </CardHeader>
                  <CardContent className="space-y-4 text-muted-foreground">
                    <p>{entry.summary}</p>
                    <div className="flex flex-wrap gap-3">
                      <Button size="sm" asChild>
                        <Link href={entry.href}>Read the paper</Link>
                      </Button>
                      {entry.companionPostSlug && (
                        <Button variant="secondary" size="sm" asChild>
                          <Link href={`/blog/${entry.companionPostSlug}`}>
                            Blog write-up
                          </Link>
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>
              ) : (
                <Card
                  key={entry.slug}
                  className="bg-card border-border"
                >
                  <CardHeader>
                    <div className="flex items-center gap-2 mb-1">
                      <Badge variant="secondary" className="text-xs">
                        {entry.category}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {entry.dateIso}
                      </span>
                    </div>
                    <CardTitle className="text-xl leading-snug">
                      <Link
                        href={entry.href}
                        className="hover:text-amber-500 transition-colors"
                      >
                        {entry.title}
                      </Link>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4 text-muted-foreground">
                    <p>{entry.description}</p>
                    <Button size="sm" asChild>
                      <Link href={entry.href}>Read</Link>
                    </Button>
                  </CardContent>
                </Card>
              )
            )}
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
