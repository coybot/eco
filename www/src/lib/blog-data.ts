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
    slug: "why-vlm-drones-cant-beat-hovering",
    title: "10,200 Flights, 25 Vision-Language Models, and the Drone That Beat Them by Doing Nothing",
    description:
      "We ran 10,200 closed-loop flight trials across 25 vision-language models. Every 7-8B model lost to a drone that just hovered, and a frontier API model barely broke even. Here's the metric gap — and the architecture that finally closed it.",
    date: "March 7, 2025",
    dateIso: "2025-03-07",
    category: "Research",
    readTime: "14 min read",
  },
  {
    slug: "l5-autonomy-zero-interventions",
    title: "Zero Interventions: Hitting L5 on a 16-Scenario Benchmark — by Fixing the Benchmark",
    description:
      "In simulation, a heterogeneous quadcopter-and-rover fleet navigates 16 adversarial scenarios — sensor dropouts, GPS spoofing, dynamic intruders, tight chokepoints — with zero human corrections. We got there not by training a better policy, but by finding the bugs in the benchmark.",
    date: "June 30, 2026",
    dateIso: "2026-06-30",
    category: "Research",
    readTime: "11 min read",
  },
];

export function getBlogPost(slug: string): BlogPostMeta | undefined {
  return blogPosts.find((p) => p.slug === slug);
}
