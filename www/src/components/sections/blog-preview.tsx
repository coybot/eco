"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight, Calendar } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { blogPosts } from "@/lib/blog-data";

// Latest 3 real posts, newest first — auto-syncs as posts are added.
const previewPosts = [...blogPosts]
  .sort((a, b) => b.dateIso.localeCompare(a.dateIso))
  .slice(0, 3)
  .map((p) => ({
    slug: p.slug,
    title: p.title,
    excerpt: p.description,
    date: p.date,
    category: p.category,
  }));

export function BlogPreviewSection() {
  return (
    <section className="py-24 bg-background">
      <div className="container mx-auto px-4">
        {/* Section Header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-16"
        >
          <h2 className="text-3xl sm:text-4xl font-bold">From the blog</h2>
          <p className="mt-4 text-lg text-muted-foreground max-w-2xl mx-auto">
            What we tried, what broke, and what we learned running thousands of
            closed-loop trials.
          </p>
        </motion.div>

        {/* Blog Grid */}
        <div className="grid md:grid-cols-2 gap-6 max-w-3xl mx-auto">
          {previewPosts.map((post, index) => (
            <motion.div
              key={post.slug}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: index * 0.1 }}
            >
              <Link href={`/blog/${post.slug}`}>
                <Card className="h-full bg-card border-border hover:border-amber-500/50 transition-all group cursor-pointer">
                  {/* Image Placeholder */}
                  <div className="aspect-video bg-secondary/50 flex items-center justify-center">
                    <div className="text-2xl font-bold text-muted-foreground/20">
                      {post.category}
                    </div>
                  </div>
                  
                  <CardContent className="p-6">
                    <div className="flex items-center space-x-2 mb-3">
                      <Badge variant="secondary" className="bg-amber-500/10 text-amber-500 text-xs">
                        {post.category}
                      </Badge>
                      <span className="text-xs text-muted-foreground flex items-center">
                        <Calendar className="h-3 w-3 mr-1" />
                        {post.date}
                      </span>
                    </div>
                    
                    <h3 className="text-lg font-semibold mb-2 group-hover:text-amber-500 transition-colors line-clamp-2">
                      {post.title}
                    </h3>
                    
                    <p className="text-sm text-muted-foreground line-clamp-2">
                      {post.excerpt}
                    </p>
                    
                    <span className="inline-flex items-center text-sm text-amber-500 mt-4 opacity-0 group-hover:opacity-100 transition-opacity">
                      Read more
                      <ArrowRight className="ml-1 h-3 w-3" />
                    </span>
                  </CardContent>
                </Card>
              </Link>
            </motion.div>
          ))}
        </div>

        {/* View All Link */}
        <motion.div
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.3 }}
          className="text-center mt-12"
        >
          <Link href="/blog">
            <Button variant="outline">
              View All
              <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </Link>
        </motion.div>
      </div>
    </section>
  );
}
