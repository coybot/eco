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
    slug: "l5-sim-to-real-honest",
    title: "We Said We Hit L5. Then We Tested With a Real Sensor Model.",
    description:
      "Our L5 result quietly depended on a quantity no real sensor produces. Under realistic sensing it was L3. Here is the honest path back to L5 — the nine fixes that failed, the one that worked, and 16/16 clean runs through the real autopilot in SITL. Not IRL L5 yet; here's exactly how far we are.",
    date: "July 2, 2026",
    dateIso: "2026-07-02",
    category: "Research",
    readTime: "15 min read",
  },
  {
    slug: "l5-autonomy-zero-interventions",
    title: "Zero Interventions: How We Hit L5 Autonomy on a 16-Scenario Fleet Benchmark",
    description:
      "A heterogeneous quadcopter-and-rover fleet navigates 16 adversarial scenarios — sensor dropouts, GPS spoofing, dynamic intruders, tight chokepoints — with zero human corrections. We got there not by training a better policy, but by finding the bugs in the benchmark.",
    date: "June 30, 2026",
    dateIso: "2026-06-30",
    category: "Research",
    readTime: "11 min read",
  },
  {
    slug: "rover-nav-recurrent-rl-lidar",
    title: "30 Minutes, 4.6 Kilobytes, Zero Collisions",
    description:
      "We trained a ground rover to navigate cluttered environments, tight gaps, and multi-room mazes using only a 360° lidar and a learned GRU reflex — no map, no planner, no demonstrations. 120 trials, zero collisions. A VFH analytic baseline fails completely on dense fields.",
    date: "June 24, 2026",
    dateIso: "2026-06-24",
    category: "Research",
    readTime: "12 min read",
  },
  {
    slug: "four-models-drone-autonomy",
    title: "Four Models, One Stack: Training the Full Perception–Reasoning–Action Pipeline for Autonomous Drones",
    description:
      "After the domain detector, we trained three more models in a single session: a VLM action-LoRA that cuts malformed commands, a 121 KB reactive policy MLP that runs at 200 Hz, and a monocular depth fine-tune for rangefinding beyond stereo baseline. All four are now running on Jetson Orin Nano hardware.",
    date: "April 3, 2026",
    dateIso: "2026-04-03",
    category: "Research",
    readTime: "14 min read",
  },
  {
    slug: "domain-detector-aerial-autonomy",
    title: "We Trained a Domain Detector for Drones. One Class Collapsed to Zero.",
    description:
      "COCO-80 has no drone class, no person_aerial, no landing pad. We trained a 9-class domain detector on 48,000 images of sim and real aerial footage — and learned why class imbalance is the dominant failure mode in aerial perception.",
    date: "January 17, 2026",
    dateIso: "2026-01-17",
    category: "Research",
    readTime: "12 min read",
  },
  {
    slug: "domain-detector-aerial-autonomy-paper",
    title: "Domain-Specific Object Detection for Aerial Autonomy: Sim Data, VisDrone, and the Class Imbalance Problem",
    description:
      "Technical report. YOLOv8n fine-tuned on a 9-class aerial schema across three training rounds: sim-only (v1), merged with VisDrone (v2), and 4× drone oversampling (v3). mAP50 0.471 → 0.376 → 0.384. Drone AP50 0.047 → 0.010 → 0.087.",
    date: "January 17, 2026",
    dateIso: "2026-01-17",
    category: "Research",
    readTime: "18 min read",
  },
  {
    slug: "droneport-atc-tower-vs-selforg",
    title: "Tower vs. Self-Organized Droneport ATC: What a 9-Cell Factorial Study Found",
    description:
      "We ran 405 simulations across nine coordination architectures. Self-org with ADS-B matches tower throughput below 20 ops/hour — then falls apart. Silent-cruise drones exceed safe separation thresholds at just 12 ops/hour.",
    date: "December 11, 2025",
    dateIso: "2025-12-11",
    category: "Research",
    readTime: "10 min read",
  },
  {
    slug: "why-vlm-drones-cant-beat-hovering",
    title: "Why Every Drone AI We Tested Lost to Doing Nothing (And What Fixed It)",
    description:
      "We ran 10,200 closed-loop flight trials across 25 vision-language models. Every single one lost to a drone that just hovered. Here's the metric gap — and the architecture that finally closed it.",
    date: "March 7, 2025",
    dateIso: "2025-03-07",
    category: "Research",
    readTime: "14 min read",
  },
  {
    slug: "engineering-drone-autonomy-18-iterations",
    title: "18 Iterations to Beat Hover: What We Learned Engineering Drone Autonomy",
    description:
      "We trained on 6.7 million frames. Detection improved 9.7×. Closed-loop navigation didn't budge. The story of the domain gap trap — and what actually limits autonomous drones today.",
    date: "November 8, 2024",
    dateIso: "2024-11-08",
    category: "Research",
    readTime: "15 min read",
  },
  {
    slug: "drone-swarm-sensing-1000-drones",
    title: "What 1,000 Drones Need to Coordinate: UWB Is Non-Negotiable Above 100",
    description:
      "Camera-only swarms drop 15.8 percentage points in coverage and have 8× the collision rate at 1,000 drones. Here's what the sensing infrastructure for large-scale drone fleets actually requires.",
    date: "May 19, 2025",
    dateIso: "2025-05-19",
    category: "Research",
    readTime: "13 min read",
  },
  {
    slug: "counter-uas-drone-attack-defense-simulation",
    title: "We Attacked Our Own Drones 11,340 Times. Here's What We Learned.",
    description:
      "GNSS spoofing. RF jamming. PN interceptors. Control takeover. We ran every major counter-UAS attack against our autonomous drone swarm. None of them degraded mission success — and that's the problem.",
    date: "October 22, 2025",
    dateIso: "2025-10-22",
    category: "Security",
    readTime: "16 min read",
  },
  {
    slug: "human-in-loop-drone-autonomy-94-percent",
    title: "94% Success Rate: What Happens When You Add a Human to the Loop",
    description:
      "Full autonomy gets 57.6% on hard warehouse tasks. Add a human for novel situations, and it jumps to 94.4%. The right architecture isn't fully autonomous — it's autonomy-aware.",
    date: "August 4, 2025",
    dateIso: "2025-08-04",
    category: "Operations",
    readTime: "11 min read",
  },
  {
    slug: "how-to-make-autonomous-drones-smarter",
    title: "How to Make Autonomous Drones Smarter (Without Wishful Thinking)",
    description:
      "A practical stack for AI drone autonomy: simulation-first iteration, metric grounding, modular perception and planning, and closed-loop evaluation.",
    date: "March 18, 2024",
    dateIso: "2024-03-18",
    category: "Guide",
    readTime: "12 min read",
  },
  {
    slug: "metric-gap-vision-language-drone-navigation",
    title:
      "The Metric Gap in Vision-Language Drone Navigation: What Actually Breaks",
    description:
      "Why general-purpose VLMs struggle as end-to-end drone controllers, what fails in closed loop, and why separating semantics from geometry is the pragmatic path.",
    date: "February 21, 2025",
    dateIso: "2025-02-21",
    category: "Research",
    readTime: "14 min read",
  },
  {
    slug: "yonder-drone-navigation-dataset",
    title:
      "Yonder: A Large-Scale Drone Navigation Dataset and Why Offline mAP Lies to You",
    description:
      "What Yonder contains, who it is for, and how cross-simulator evaluation prevents false confidence when training perception for embodied flight.",
    date: "May 1, 2026",
    dateIso: "2026-05-01",
    category: "Dataset",
    readTime: "11 min read",
  },
];

export function getBlogPost(slug: string): BlogPostMeta | undefined {
  return blogPosts.find((p) => p.slug === slug);
}
