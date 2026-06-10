import { Metadata } from "next";
import Link from "next/link";
import { Calendar, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Card, CardContent } from "@/components/ui/card";
import { blogPosts } from "@/lib/blog-data";

export const metadata: Metadata = {
  title: "Blog",
  description:
    "Stay informed with the latest news, updates, and industry insights from Astral.",
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
