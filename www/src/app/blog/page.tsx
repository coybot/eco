import { Metadata } from "next";
import Link from "next/link";
import { Calendar, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Card, CardContent } from "@/components/ui/card";
import { JsonLd } from "@/components/json-ld";
import { blogPosts } from "@/lib/blog-data";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const BLOG_DESCRIPTION =
  "Astral's drone autonomy research blog: closed-loop benchmarks, the metric gap in vision-language navigation, swarm sensing at scale, counter-UAS, and the Yonder dataset.";

export const metadata: Metadata = {
  title: "Blog",
  description: BLOG_DESCRIPTION,
  ...socialMeta("/blog", "Blog | Astral", BLOG_DESCRIPTION),
};

const blogJsonLd = {
  "@context": "https://schema.org",
  "@type": "Blog",
  name: "Astral Blog",
  description: BLOG_DESCRIPTION,
  url: `${SITE.origin}/blog`,
  publisher: { "@type": "Organization", name: "Astral", url: SITE.origin },
  blogPost: blogPosts.map((p) => ({
    "@type": "BlogPosting",
    headline: p.title,
    description: p.description,
    datePublished: p.dateIso,
    url: `${SITE.origin}/blog/${p.slug}`,
  })),
};

// Map blog-data entries to the shape this page uses
const posts = blogPosts.map((p) => ({
  slug: p.slug,
  title: p.title,
  excerpt: p.description,
  date: p.date,
  category: p.category,
  readTime: p.readTime,
}));

export default function BlogPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <JsonLd data={blogJsonLd} />
      <Header />
      <main className="flex-1">
        {/* Hero */}
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-4xl sm:text-5xl font-bold mb-6">Blog</h1>
              <p className="text-lg text-muted-foreground">
                Stay informed with the latest news, updates, and industry
                insights from Astral.
              </p>
            </div>
          </div>
        </section>

        {/* Blog Posts */}
        <section className="py-16 bg-background">
          <div className="container mx-auto px-4">
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6 max-w-6xl mx-auto">
              {posts.map((post) => (
                <Link key={post.slug} href={`/blog/${post.slug}`}>
                  <Card className="h-full bg-card border-border hover:border-amber-500/50 transition-all group cursor-pointer">
                    <CardContent className="p-6">
                      <div className="flex items-center gap-2 mb-3">
                        <span className="text-xs text-muted-foreground flex items-center">
                          <Calendar className="h-3 w-3 mr-1" />
                          {post.date}
                        </span>
                      </div>

                      <h2 className="text-lg font-semibold mb-2 group-hover:text-amber-500 transition-colors line-clamp-2">
                        {post.title}
                      </h2>

                      <p className="text-sm text-muted-foreground line-clamp-2 mb-4">
                        {post.excerpt}
                      </p>

                      <div className="flex items-center justify-between">
                        <span className="text-xs text-muted-foreground">
                          {post.readTime}
                        </span>
                        <span className="inline-flex items-center text-sm text-amber-500 opacity-0 group-hover:opacity-100 transition-opacity">
                          Read more
                          <ArrowRight className="ml-1 h-3 w-3" />
                        </span>
                      </div>
                    </CardContent>
                  </Card>
                </Link>
              ))}
            </div>
          </div>
        </section>


      </main>
      <Footer />
    </div>
  );
}
