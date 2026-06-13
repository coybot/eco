import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, FlaskConical } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { Button } from "@/components/ui/button";
import { blogPosts, getBlogPost } from "@/lib/blog-data";
import { researchPapers } from "@/lib/research-data";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";
import { BlogPostBody } from "../post-bodies";
import { ResearchPaperBody } from "../../research/paper-bodies";

type Props = { params: Promise<{ slug: string }> };

export function generateStaticParams() {
  return blogPosts.map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const post = getBlogPost(slug);
  if (!post) return {};
  return {
    title: post.title,
    description: post.description,
    ...socialMeta(`/blog/${post.slug}`, post.title, post.description, {
      type: "article",
      publishedTime: post.dateIso,
    }),
  };
}

export default async function BlogPostPage({ params }: Props) {
  const { slug } = await params;
  const post = getBlogPost(slug);
  if (!post) notFound();

  const paper = researchPapers.find((p) => p.companionPostSlug === slug);

  const jsonLdTypes = paper
    ? ["BlogPosting", "ScholarlyArticle"]
    : "BlogPosting";

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": jsonLdTypes,
    headline: post.title,
    description: post.description,
    datePublished: post.dateIso,
    author: { "@type": "Organization", name: "Astral" },
    publisher: { "@type": "Organization", name: "Astral", url: SITE.origin },
    mainEntityOfPage: {
      "@type": "WebPage",
      "@id": `${SITE.origin}/blog/${slug}`,
    },
    ...(paper
      ? {
          abstract: paper.summary,
          name: paper.title,
        }
      : {}),
  };

  return (
    <div className="min-h-screen flex flex-col">
      <JsonLd data={jsonLd} />
      <Header />
      <main className="flex-1">
        <article className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-3xl">
            <Link
              href="/blog"
              className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground mb-8"
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to blog
            </Link>
            <p className="text-sm text-amber-500 mb-2">
              {post.category} · {post.readTime}
            </p>
            <h1 className="text-4xl font-bold tracking-tight mb-4">
              {post.title}
            </h1>
            <p className="text-muted-foreground">{post.date}</p>

            <div className="mt-10 border-t border-border pt-10">
              <BlogPostBody slug={slug} />
            </div>

            {/* Technical paper section — shown when a companion paper exists */}
            {paper && (
              <div
                id="paper"
                className="mt-16 border-t border-border pt-12 space-y-8"
              >
                {/* Header */}
                <div className="flex items-start gap-4">
                  <div className="mt-1 p-2 rounded-lg bg-amber-500/10 shrink-0">
                    <FlaskConical className="h-5 w-5 text-amber-500" />
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-widest text-amber-500 font-mono mb-1">
                      Technical paper
                    </p>
                    <h2 className="text-2xl font-bold">{paper.title}</h2>
                    <p className="text-sm text-muted-foreground mt-1">
                      {paper.venue}
                    </p>
                  </div>
                </div>

                {/* TL;DR */}
                <div className="rounded-lg border border-border bg-card p-6">
                  <p className="text-xs uppercase tracking-widest text-muted-foreground font-mono mb-3">
                    TL;DR
                  </p>
                  <ul className="list-disc pl-5 space-y-2 text-foreground/90">
                    {paper.tldr.map((point) => (
                      <li key={point}>{point}</li>
                    ))}
                  </ul>
                </div>

                {/* Key stats */}
                {paper.keyStats && paper.keyStats.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left border-collapse">
                      <tbody>
                        {paper.keyStats.map((row) => (
                          <tr key={row.label} className="border-b border-border">
                            <th className="py-2 pr-4 font-medium text-muted-foreground align-top w-1/2">
                              {row.label}
                            </th>
                            <td className="py-2 text-foreground/90">
                              {row.value}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* Abstract */}
                <div>
                  <h3 className="text-lg font-semibold mb-3">Abstract</h3>
                  <p className="text-muted-foreground leading-relaxed">
                    {paper.summary}
                  </p>
                </div>

                {/* Full paper body */}
                <ResearchPaperBody slug={paper.slug} />

                {/* External links */}
                {paper.externalLinks.length > 0 && (
                  <div className="flex flex-wrap gap-3 pt-4">
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
                )}
              </div>
            )}
          </div>
        </article>
      </main>
      <Footer />
    </div>
  );
}
