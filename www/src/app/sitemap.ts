import type { MetadataRoute } from "next";
import { products } from "@/lib/products";
import { blogPosts } from "@/lib/blog-data";
import { researchPapers } from "@/lib/research-data";

const baseUrl = process.env.NEXT_PUBLIC_BASE_URL ?? "https://astral.us";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();

  const staticRoutes: MetadataRoute.Sitemap = [
    { url: `${baseUrl}/`, lastModified: now, changeFrequency: "weekly", priority: 1.0 },
    { url: `${baseUrl}/products`, lastModified: now, changeFrequency: "weekly", priority: 0.9 },
    { url: `${baseUrl}/pricing`, lastModified: now, changeFrequency: "weekly", priority: 0.9 },
    { url: `${baseUrl}/compare`, lastModified: now, changeFrequency: "monthly", priority: 0.8 },
    { url: `${baseUrl}/enterprise`, lastModified: now, changeFrequency: "monthly", priority: 0.8 },
    { url: `${baseUrl}/about`, lastModified: now, changeFrequency: "monthly", priority: 0.8 },
    { url: `${baseUrl}/research`, lastModified: now, changeFrequency: "weekly", priority: 0.8 },
    { url: `${baseUrl}/benchmark`, lastModified: now, changeFrequency: "weekly", priority: 0.9 },
    { url: `${baseUrl}/faq`, lastModified: now, changeFrequency: "monthly", priority: 0.7 },
    { url: `${baseUrl}/build`, lastModified: now, changeFrequency: "weekly", priority: 0.7 },
    { url: `${baseUrl}/docs`, lastModified: now, changeFrequency: "weekly", priority: 0.7 },
    { url: `${baseUrl}/docs/simulation`, lastModified: now, changeFrequency: "weekly", priority: 0.7 },
    { url: `${baseUrl}/datasets/yonder`, lastModified: now, changeFrequency: "monthly", priority: 0.7 },
    { url: `${baseUrl}/apps`, lastModified: now, changeFrequency: "monthly", priority: 0.7 },
    { url: `${baseUrl}/blog`, lastModified: now, changeFrequency: "weekly", priority: 0.6 },
  ];

  const productRoutes: MetadataRoute.Sitemap = products.map((product) => ({
    url: `${baseUrl}/products/${product.id}`,
    lastModified: now,
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
