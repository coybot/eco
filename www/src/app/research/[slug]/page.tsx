import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { Button } from "@/components/ui/button";
import { researchPapers, getResearchPaper } from "@/lib/research-data";
import { ResearchPaperBody } from "../paper-bodies";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

type Props = { params: Promise<{ slug: string }> };

export function generateStaticParams() {
  return researchPapers.map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const paper = getResearchPaper(slug);
  if (!paper) return {};
  return {
    title: paper.title,
    description: paper.summary,
    ...socialMeta(`/research/${paper.slug}`, `${paper.title} | Presidio`, paper.summary, {
      type: "article",
      publishedTime: paper.dateIso,
    }),
  };
}

export default async function ResearchPaperPage({ params }: Props) {
  const { slug } = await params;
  const paper = getResearchPaper(slug);
  if (!paper) notFound();

  const url = `${SITE.origin}/research/${paper.slug}`;
  const citations = [
    ...(paper.companionPostSlug
      ? [`${SITE.origin}/blog/${paper.companionPostSlug}`]
      : []),
    ...paper.externalLinks.filter((l) => l.external).map((l) => l.href),
  ];

  const jsonLd =
    paper.schemaType === "Dataset"
      ? {
          "@context": "https://schema.org",
          "@type": "Dataset",
          name: paper.title,
          description: paper.summary,
          url,
          datePublished: paper.dateIso,
          license: "https://creativecommons.org/licenses/by-nc/4.0/",
          creator: { "@type": "Organization", name: "Presidio", url: SITE.origin },
          isAccessibleForFree: true,
          distribution: {
            "@type": "DataDownload",
            contentUrl: SITE.yonderDataset,
            encodingFormat: "application/zip",
          },
          sameAs: SITE.yonderDataset,
        }
      : {
          "@context": "https://schema.org",
          "@type": ["Report", "ScholarlyArticle"],
          headline: paper.title,
          name: paper.title,
          abstract: paper.summary,
          description: paper.summary,
          datePublished: paper.dateIso,
          author: { "@type": "Organization", name: "Presidio", url: SITE.origin },
          publisher: { "@type": "Organization", name: "Presidio", url: SITE.origin },
          mainEntityOfPage: { "@type": "WebPage", "@id": url },
          ...(citations.length > 0 ? { citation: citations } : {}),
        };

  return (
    <div className="min-h-screen flex flex-col">
      <JsonLd data={jsonLd} />
      <Header />
      <main className="flex-1">
        <article className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-3xl">
            <Link
              href="/research"
              className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground mb-8"
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to research
            </Link>
            <p className="text-sm text-amber-500 mb-2">{paper.venue}</p>
            <h1 className="text-3xl sm:text-4xl font-bold tracking-tight mb-6">
              {paper.title}
            </h1>

            {/* TL;DR — first thing crawlers and LLMs read */}
            <div className="rounded-lg border border-border bg-card p-6 mb-8">
              <p className="text-xs uppercase tracking-widest text-muted-foreground font-mono mb-3">
                TL;DR
              </p>
              <ul className="list-disc pl-5 space-y-2 text-foreground/90">
                {paper.tldr.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            </div>

            {/* Key stats as a real HTML table — highly citable */}
            {paper.keyStats && paper.keyStats.length > 0 && (
              <div className="mb-8 overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <tbody>
                    {paper.keyStats.map((row) => (
                      <tr key={row.label} className="border-b border-border">
                        <th className="py-2 pr-4 font-medium text-muted-foreground align-top w-1/2">
                          {row.label}
                        </th>
                        <td className="py-2 text-foreground/90">{row.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Abstract */}
            <h2 className="text-xl font-semibold mb-3">Abstract</h2>
            <p className="text-muted-foreground leading-relaxed mb-8">{paper.summary}</p>

            {/* Full per-paper body */}
            <ResearchPaperBody slug={paper.slug} />

            <div className="flex flex-wrap gap-3 border-t border-border pt-8 mt-12">
              {paper.companionPostSlug && (
                <Button asChild>
                  <Link href={`/blog/${paper.companionPostSlug}`}>
                    Read the full write-up
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
          </div>
        </article>
      </main>
      <Footer />
    </div>
  );
}
