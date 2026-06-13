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
