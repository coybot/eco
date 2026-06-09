export type BlogPostMeta = {
  slug: string;
  title: string;
  description: string;
  date: string;
  /** ISO date for JSON-LD */
  dateIso: string;
  category: string;
  readTime: string;
};

export const blogPosts: BlogPostMeta[] = [
  {
    slug: "how-to-make-autonomous-drones-smarter",
    title: "How to Make Autonomous Drones Smarter (Without Wishful Thinking)",
    description:
      "A practical stack for AI drone autonomy: simulation-first iteration, metric grounding, modular perception and planning, and closed-loop evaluation.",
    date: "May 2, 2026",
    dateIso: "2026-05-02",
    category: "Guide",
    readTime: "12 min read",
  },
  {
    slug: "metric-gap-vision-language-drone-navigation",
    title:
      "The Metric Gap in Vision-Language Drone Navigation: What Actually Breaks",
    description:
      "Why general-purpose VLMs struggle as end-to-end drone controllers, what fails in closed loop, and why separating semantics from geometry is the pragmatic path.",
    date: "May 2, 2026",
    dateIso: "2026-05-02",
    category: "Research",
    readTime: "14 min read",
  },
  {
    slug: "yonder-drone-navigation-dataset",
    title:
      "Yonder: A Large-Scale Drone Navigation Dataset and Why Offline mAP Lies to You",
    description:
      "What Yonder contains, who it is for, and how cross-simulator evaluation prevents false confidence when training perception for embodied flight.",
    date: "May 2, 2026",
    dateIso: "2026-05-02",
    category: "Dataset",
    readTime: "11 min read",
  },
];

export function getBlogPost(slug: string): BlogPostMeta | undefined {
  return blogPosts.find((p) => p.slug === slug);
}
