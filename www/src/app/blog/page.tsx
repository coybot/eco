import { Metadata } from "next";
import Link from "next/link";
import { Calendar, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export const metadata: Metadata = {
  title: "Blog",
  description:
    "Stay informed with the latest news, updates, and industry insights from Astral.",
};

// Sample blog posts (would come from Sanity CMS in production)
const blogPosts = [
  {
    slug: "top-defense-use-cases-of-autonomous-drones-in-2025",
    title: "Top Defense Use Cases of Autonomous Drones in 2025",
    excerpt:
      "How AI-Driven, NDAA-Compliant Aircraft Are Reshaping Modern Military Capability. On Jun 1, 2025 Ukraine launched a drone attack on a Russian military base...",
    date: "December 10, 2025",
    category: "Defense",
    readTime: "8 min read",
  },
  {
    slug: "ndaa-compliant-drones-explained",
    title: "NDAA-Compliant Drones Explained: What Operators Need to Know",
    excerpt:
      "Trusted Hardware for Trusted Autonomy. As AI-powered drones move into critical infrastructure, public safety, and defense-adjacent missions...",
    date: "November 25, 2025",
    category: "Compliance",
    readTime: "6 min read",
  },
  {
    slug: "how-autonomous-drones-work",
    title: "How Autonomous Drones Work: AI, Sensors, and Fleet Management",
    excerpt:
      "Learn how autonomous drones use AI, sensors, and fleet management systems to navigate, avoid obstacles, and perform complex missions.",
    date: "November 6, 2025",
    category: "Technology",
    readTime: "10 min read",
  },
  {
    slug: "introducing-mothership-jetson-orin",
    title: "Introducing M1-A and M1-G: Powered by NVIDIA Jetson Orin Nano",
    excerpt:
      "We're excited to announce the M1-A quadcopter and M1-G rover, our autonomous platforms featuring the NVIDIA Jetson Orin Nano with 67 TOPS of AI performance.",
    date: "October 15, 2025",
    category: "Product",
    readTime: "5 min read",
  },
  {
    slug: "agricultural-drone-automation-guide",
    title: "The Complete Guide to Agricultural Drone Automation",
    excerpt:
      "From crop monitoring to precision spraying, discover how autonomous drones are transforming modern agriculture and increasing yields.",
    date: "September 28, 2025",
    category: "Agriculture",
    readTime: "12 min read",
  },
  {
    slug: "building-with-astral-sdk",
    title: "Building Your First App with the Astral SDK",
    excerpt:
      "A step-by-step tutorial on creating custom autonomous drone applications using our open source SDK and APIs.",
    date: "September 10, 2025",
    category: "Developer",
    readTime: "15 min read",
  },
];

const categories = [
  "All",
  "Defense",
  "Technology",
  "Product",
  "Compliance",
  "Agriculture",
  "Developer",
];

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

        {/* Categories */}
        <section className="py-8 bg-card border-b border-border">
          <div className="container mx-auto px-4">
            <div className="flex flex-wrap justify-center gap-2">
              {categories.map((category) => (
                <Button
                  key={category}
                  variant={category === "All" ? "default" : "outline"}
                  size="sm"
                  className={category === "All" ? "" : ""}
                >
                  {category}
                </Button>
              ))}
            </div>
          </div>
        </section>

        {/* Blog Posts */}
        <section className="py-16 bg-background">
          <div className="container mx-auto px-4">
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6 max-w-6xl mx-auto">
              {blogPosts.map((post) => (
                <Link key={post.slug} href={`/blog/${post.slug}`}>
                  <Card className="h-full bg-card border-border hover:border-amber-500/50 transition-all group cursor-pointer">
                    {/* Image Placeholder */}
                    <div className="aspect-video bg-secondary/50 flex items-center justify-center">
                      <div className="text-2xl font-bold text-muted-foreground/20">
                        {post.category}
                      </div>
                    </div>

                    <CardContent className="p-6">
                      <div className="flex items-center gap-2 mb-3">
                        <Badge
                          variant="secondary"
                          className="bg-amber-500/10 text-amber-500 text-xs"
                        >
                          {post.category}
                        </Badge>
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

        {/* Newsletter CTA */}
        <section className="py-16 bg-card">
          <div className="container mx-auto px-4">
            <div className="max-w-xl mx-auto text-center">
              <h2 className="text-2xl font-bold mb-4">Stay Updated</h2>
              <p className="text-muted-foreground mb-6">
                Subscribe to our newsletter for the latest updates on autonomous
                drone technology.
              </p>
              <form className="flex gap-2 max-w-md mx-auto">
                <input
                  type="email"
                  placeholder="Enter your email"
                  className="flex-1 h-10 px-4 rounded-md border border-input bg-background text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
                <Button type="submit">Subscribe</Button>
              </form>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
