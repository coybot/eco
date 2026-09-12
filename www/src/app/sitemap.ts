import type { MetadataRoute } from "next";
import { products } from "@/lib/products";
import { blogPosts } from "@/lib/blog-data";
import { researchPapers } from "@/lib/research-data";

const baseUrl = process.env.NEXT_PUBLIC_BASE_URL ?? "https://coy.bot";

export default function sitemap(): MetadataRoute.Sitemap {
  const staticRoutes: MetadataRoute.Sitemap = [
    { url: `${baseUrl}/`, lastModified: new Date("2026-04-03"), changeFrequency: "weekly", priority: 1.0 },
    { url: `${baseUrl}/products`, lastModified: new Date("2025-11-01"), changeFrequency: "weekly", priority: 0.9 },
    { url: `${baseUrl}/pricing`, lastModified: new Date("2025-11-01"), changeFrequency: "weekly", priority: 0.9 },
    { url: `${baseUrl}/compare`, lastModified: new Date("2025-11-01"), changeFrequency: "monthly", priority: 0.8 },
    { url: `${baseUrl}/enterprise`, lastModified: new Date("2025-06-01"), changeFrequency: "monthly", priority: 0.8 },
    { url: `${baseUrl}/about`, lastModified: new Date("2025-06-01"), changeFrequency: "monthly", priority: 0.8 },
    { url: `${baseUrl}/research`, lastModified: new Date("2026-01-17"), changeFrequency: "weekly", priority: 0.8 },
    { url: `${baseUrl}/benchmark`, lastModified: new Date("2025-03-07"), changeFrequency: "weekly", priority: 0.9 },
    { url: `${baseUrl}/faq`, lastModified: new Date("2025-06-01"), changeFrequency: "monthly", priority: 0.7 },
    { url: `${baseUrl}/build`, lastModified: new Date("2025-06-01"), changeFrequency: "weekly", priority: 0.7 },
    { url: `${baseUrl}/docs`, lastModified: new Date("2025-06-01"), changeFrequency: "weekly", priority: 0.7 },
    { url: `${baseUrl}/docs/simulation`, lastModified: new Date("2025-06-01"), changeFrequency: "weekly", priority: 0.7 },
    { url: `${baseUrl}/docs/mobile-app`, lastModified: new Date("2025-06-01"), changeFrequency: "monthly", priority: 0.7 },
    { url: `${baseUrl}/privacy`, lastModified: new Date("2026-06-06"), changeFrequency: "yearly", priority: 0.3 },
    { url: `${baseUrl}/terms`, lastModified: new Date("2026-06-06"), changeFrequency: "yearly", priority: 0.3 },
    { url: `${baseUrl}/datasets/yonder`, lastModified: new Date("2026-05-01"), changeFrequency: "monthly", priority: 0.7 },
    { url: `${baseUrl}/apps`, lastModified: new Date("2025-06-01"), changeFrequency: "monthly", priority: 0.7 },
    { url: `${baseUrl}/blog`, lastModified: new Date("2026-04-03"), changeFrequency: "weekly", priority: 0.6 },
  ];

  const productRoutes: MetadataRoute.Sitemap = products.map((product) => ({
    url: `${baseUrl}/products/${product.id}`,
    lastModified: new Date("2025-11-01"),
    changeFrequency: "monthly",
    priority: 0.8,
  }));

  const blogRoutes: MetadataRoute.Sitemap = blogPosts.map((post) => ({
    url: `${baseUrl}/blog/${post.slug}`,
    lastModified: new Date(post.dateIso),
    changeFrequency: "monthly",
    priority: 0.6,
  }));

  const researchRoutes: MetadataRoute.Sitemap = researchPapers.map((paper) => ({
    url: `${baseUrl}/research/${paper.slug}`,
    lastModified: new Date(paper.dateIso),
    changeFrequency: "monthly",
    priority: 0.7,
  }));

  return [...staticRoutes, ...productRoutes, ...blogRoutes, ...researchRoutes];
}
