import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";
import { researchPapers } from "@/lib/research-data";
import { getBlogPost } from "@/lib/blog-data";

const RESEARCH_DESCRIPTION =
  "Astral publishes rigorous autonomy research on vision-language navigation for aerial and ground robots, modular architectures, swarm sensing, and large-scale datasets including Yonder.";

export const metadata: Metadata = {
  title: "Research",
  description: RESEARCH_DESCRIPTION,
  ...socialMeta("/research", "Research | Astral", RESEARCH_DESCRIPTION),
};

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
              Astral treats autonomous uncrewed systems as a systems problem:
              perception, geometry, planning, safety, simulation fidelity, and honest
              evaluation — whether the robot flies, drives, or does both. Each paper
              below links to the full write-up on the blog, where the technical
              detail is available alongside the accessible narrative.
            </p>
            <div className="flex flex-wrap gap-3">
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
          </div>
        </section>

        <section className="py-12 bg-background border-t border-border">
          <div className="container mx-auto px-4 max-w-4xl space-y-8">
            {researchPapers.map((paper) => {
              const companion = paper.companionPostSlug
                ? getBlogPost(paper.companionPostSlug)
                : null;
              const href = companion
                ? `/blog/${paper.companionPostSlug}#paper`
                : `/research/${paper.slug}`;

              return (
                <Card
                  key={paper.slug}
                  id={paper.slug}
                  className="bg-card border-border scroll-mt-24"
                >
                  <CardHeader>
                    <CardTitle className="text-xl leading-snug">
                      <Link
                        href={href}
                        className="hover:text-amber-500 transition-colors"
                      >
                        {paper.title}
                      </Link>
                    </CardTitle>
                    <p className="text-sm text-muted-foreground">{paper.venue}</p>
                  </CardHeader>
                  <CardContent className="space-y-4 text-muted-foreground">
                    <p>{paper.summary}</p>
                    <div className="flex flex-wrap gap-3">
                      <Button size="sm" asChild>
                        <Link href={href}>Read the paper</Link>
                      </Button>
                      {companion && (
                        <Button variant="secondary" size="sm" asChild>
                          <Link href={`/blog/${paper.companionPostSlug}`}>
                            {companion.title}
                          </Link>
                        </Button>
                      )}
                      {paper.externalLinks.map((l) =>
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
              );
            })}
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
