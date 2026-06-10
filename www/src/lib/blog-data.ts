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
    slug: "droneport-atc-tower-vs-selforg",
    title: "Tower vs. Self-Organized Droneport ATC: What a 9-Cell Factorial Study Found",
    description:
      "We ran 405 simulations across nine coordination architectures. Self-org with ADS-B matches tower throughput below 20 ops/hour — then falls apart. Silent-cruise drones exceed safe separation thresholds at just 12 ops/hour.",
    date: "June 9, 2026",
    dateIso: "2026-06-09",
    category: "Research",
    readTime: "10 min read",
  },
  {
    slug: "why-vlm-drones-cant-beat-hovering",
    title: "Why Every Drone AI We Tested Lost to Doing Nothing (And What Fixed It)",
    description:
      "We ran 10,200 closed-loop flight trials across 25 vision-language models. Every single one lost to a drone that just hovered. Here's the metric gap — and the architecture that finally closed it.",
    date: "June 9, 2026",
    dateIso: "2026-06-09",
    category: "Research",
    readTime: "14 min read",
  },
  {
    slug: "engineering-drone-autonomy-18-iterations",
    title: "18 Iterations to Beat Hover: What We Learned Engineering Drone Autonomy",
    description:
      "We trained on 6.7 million frames. Detection improved 9.7×. Closed-loop navigation didn't budge. The story of the domain gap trap — and what actually limits autonomous drones today.",
    date: "June 9, 2026",
    dateIso: "2026-06-09",
    category: "Research",
    readTime: "15 min read",
  },
  {
    slug: "drone-swarm-sensing-1000-drones",
    title: "What 1,000 Drones Need to Coordinate: UWB Is Non-Negotiable Above 100",
    description:
      "Camera-only swarms drop 15.8 percentage points in coverage and have 8× the collision rate at 1,000 drones. Here's what the sensing infrastructure for large-scale drone fleets actually requires.",
    date: "June 9, 2026",
    dateIso: "2026-06-09",
    category: "Research",
    readTime: "13 min read",
  },
  {
    slug: "counter-uas-drone-attack-defense-simulation",
    title: "We Attacked Our Own Drones 11,340 Times. Here's What We Learned.",
    description:
      "GNSS spoofing. RF jamming. PN interceptors. Control takeover. We ran every major counter-UAS attack against our autonomous drone swarm. None of them degraded mission success — and that's the problem.",
    date: "June 9, 2026",
    dateIso: "2026-06-09",
    category: "Security",
    readTime: "16 min read",
  },
  {
    slug: "human-in-loop-drone-autonomy-94-percent",
    title: "94% Success Rate: What Happens When You Add a Human to the Loop",
    description:
      "Full autonomy gets 57.6% on hard warehouse tasks. Add a human for novel situations, and it jumps to 94.4%. The right architecture isn't fully autonomous — it's autonomy-aware.",
    date: "June 9, 2026",
    dateIso: "2026-06-09",
    category: "Operations",
    readTime: "11 min read",
  },
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
