import type { MetadataRoute } from "next";
import { blogPosts } from "@/lib/blog-data";

const baseUrl = process.env.NEXT_PUBLIC_BASE_URL ?? "https://presidioautonomy.com";

export default function sitemap(): MetadataRoute.Sitemap {
  const staticRoutes: MetadataRoute.Sitemap = [
    { url: `${baseUrl}/`, changeFrequency: "weekly", priority: 1.0 },
    { url: `${baseUrl}/autonomy`, changeFrequency: "monthly", priority: 0.9 },
    { url: `${baseUrl}/stack`, changeFrequency: "monthly", priority: 0.8 },
    { url: `${baseUrl}/get-started`, changeFrequency: "monthly", priority: 0.8 },
    { url: `${baseUrl}/blog`, changeFrequency: "weekly", priority: 0.6 },
    { url: `${baseUrl}/about`, changeFrequency: "yearly", priority: 0.5 },
  ];

  const blogRoutes: MetadataRoute.Sitemap = blogPosts.map((post) => ({
    url: `${baseUrl}/blog/${post.slug}`,
    lastModified: new Date(post.dateIso),
    changeFrequency: "monthly",
    priority: 0.6,
  }));

  return [...staticRoutes, ...blogRoutes];
}
