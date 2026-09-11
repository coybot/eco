import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { JsonLd } from "@/components/json-ld";
import { blogPosts, getBlogPost } from "@/lib/blog-data";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";
import { BlogPostBody } from "../post-bodies";

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

  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "BlogPosting",
      headline: post.title,
      description: post.description,
      datePublished: post.dateIso,
      author: { "@type": "Organization", name: "Presidio Autonomy" },
      publisher: { "@type": "Organization", name: "Presidio Autonomy", url: SITE.origin },
      mainEntityOfPage: { "@type": "WebPage", "@id": `${SITE.origin}/blog/${slug}` },
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "Home", item: SITE.origin },
        { "@type": "ListItem", position: 2, name: "Blog", item: `${SITE.origin}/blog` },
        { "@type": "ListItem", position: 3, name: post.title, item: `${SITE.origin}/blog/${slug}` },
      ],
    },
  ];

  return (
    <>
      <JsonLd data={jsonLd} />
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
          <h1 className="text-4xl font-bold tracking-tight mb-4">{post.title}</h1>
          <p className="text-muted-foreground">{post.date}</p>

          <div className="mt-10 border-t border-border pt-10">
            <BlogPostBody slug={slug} />
          </div>
        </div>
      </article>
    </>
  );
}
