import { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Badge } from "@/components/ui/badge";

const blogPosts = [
  {
    slug: "top-defense-use-cases-of-autonomous-drones-in-2025",
    title: "Top Defense Use Cases of Autonomous Drones in 2025",
    excerpt:
      "How AI-driven, NDAA-compliant aircraft are reshaping modern military capability.",
    category: "Defense",
    date: "December 10, 2025",
  },
  {
    slug: "ndaa-compliant-drones-explained",
    title: "NDAA-Compliant Drones Explained: What Operators Need to Know",
    excerpt:
      "Trusted hardware for trusted autonomy in defense and critical infrastructure.",
    category: "Compliance",
    date: "November 25, 2025",
  },
  {
    slug: "how-autonomous-drones-work",
    title: "How Autonomous Drones Work: AI, Sensors, and Fleet Management",
    excerpt:
      "A practical walkthrough of autonomy stacks, sensing, and mission orchestration.",
    category: "Technology",
    date: "November 6, 2025",
  },
  {
    slug: "introducing-mothership-jetson-orin",
    title: "Introducing M1-A and M1-G: Powered by NVIDIA Jetson Orin Nano",
    excerpt:
      "Product update on the latest Astral platform architecture and capabilities.",
    category: "Product",
    date: "October 15, 2025",
  },
  {
    slug: "agricultural-drone-automation-guide",
    title: "The Complete Guide to Agricultural Drone Automation",
    excerpt:
      "How autonomous missions improve crop scouting, inspection cadence, and field decisions.",
    category: "Agriculture",
    date: "September 28, 2025",
  },
  {
    slug: "building-with-astral-sdk",
    title: "Building Your First App with the Astral SDK",
    excerpt:
      "A quick guide for building mission-enabled apps with the Astral SDK and APIs.",
    category: "Developer",
    date: "September 10, 2025",
  },
] as const;

export function generateStaticParams() {
  return blogPosts.map((post) => ({ slug: post.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const post = blogPosts.find((entry) => entry.slug === slug);
  if (!post) return { title: "Post Not Found" };
  return { title: post.title, description: post.excerpt };
}

export default async function BlogPostPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const post = blogPosts.find((entry) => entry.slug === slug);
  if (!post) notFound();

  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-3xl">
            <Link
              href="/blog"
              className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground mb-6"
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Blog
            </Link>
            <div className="flex items-center gap-3 mb-4">
              <Badge variant="secondary" className="bg-amber-500/10 text-amber-500">
                {post.category}
              </Badge>
              <span className="text-sm text-muted-foreground">{post.date}</span>
            </div>
            <h1 className="text-4xl font-bold mb-4">{post.title}</h1>
            <p className="text-lg text-muted-foreground">{post.excerpt}</p>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
