import { SITE } from "./site";

export type PaperExternalLink = {
  label: string;
  href: string;
  external?: boolean;
};

export type ResearchPaperMeta = {
  /** = the existing anchor id on /research (metric-gap, counter-uas, yonder, …) */
  slug: string;
  title: string;
  venue: string;
  /** ISO date for sitemap lastModified + schema datePublished */
  dateIso: string;
  /** Drives per-page JSON-LD type */
  schemaType: "Dataset" | "Report";
  /** Short abstract shown on the hub card + paper page */
  summary: string;
  /** 2–4 numeric, extraction-friendly bullets (GEO) */
  tldr: string[];
  /** Optional data-table rows (GEO) */
  keyStats?: { label: string; value: string }[];
  /** Long-form companion blog post slug where one exists */
  companionPostSlug?: string;
  /** Non-blog links (GitHub, Hugging Face, docs, dataset page) */
  externalLinks: PaperExternalLink[];
};

export const researchPapers: ResearchPaperMeta[] = [
  {
    slug: "l5-autonomy",
    title:
      "Achieving L5 Autonomy in Heterogeneous Multi-Agent Fleet Navigation via Scenario Geometry Repair",
    venue: "Technical report",
    dateIso: "2026-06-30",
    schemaType: "Report",
    summary:
      "A systematic methodology for achieving zero-intervention autonomy in a heterogeneous quadcopter-and-rover fleet on a 16-scenario adversarial benchmark. Starting from L3 (20 total interventions), five collision patterns attributable to scenario geometry — not the control policy — are identified and resolved, reaching L5 (0 interventions, 100% success, 0 collisions) without modifying the reactive controller.",
    tldr: [
      "L5 (zero interventions, 100% mission success, zero collisions) achieved across 16 adversarial scenarios for a heterogeneous quadcopter-and-rover fleet.",
      "90% of intervention reduction came from obstacle geometry fixes, not policy changes.",
      "Five deterministic collision patterns are formalized with closed-form diagnosis criteria and fix rules.",
      "Minimal-perturbation principle: the smallest geometry change that resolves a collision pattern, preserving load-bearing obstacle navigation roles.",
    ],
    keyStats: [
      { label: "Scenarios", value: "16" },
      { label: "Starting level", value: "L3 (20 intv)" },
      { label: "Final level", value: "L5 (0 intv)" },
      { label: "Policy changes", value: "0" },
    ],
    companionPostSlug: "l5-autonomy-zero-interventions",
    externalLinks: [],
  },
  {
    slug: "yonder",
    title:
      "Yonder: A 4.65M-Frame Drone Navigation Dataset and the Cross-Simulator Generalization Gap",
    venue: "Technical report",
    dateIso: "2026-05-01",
    schemaType: "Dataset",
    summary:
      "Introduces Yonder, a multi-million-frame drone-perspective indoor dataset with rich sensing, and shows why offline detection gains can fail to translate to closed-loop navigation when training and evaluation simulators disagree geometrically.",
    tldr: [
      "Yonder is a 4.65M-frame drone-perspective indoor navigation dataset with stereo RGB, depth, IR, LiDAR-style, and semantic data.",
      "Offline detection gains do not reliably transfer to closed-loop navigation when training and evaluation simulators disagree geometrically.",
      "Released publicly on Hugging Face under CC-BY-NC-4.0.",
    ],
    keyStats: [
      { label: "Frames", value: "4.65M" },
      { label: "Sensing", value: "stereo RGB, depth, IR, LiDAR-style, segmentation, pose" },
      { label: "License", value: "CC-BY-NC-4.0" },
      { label: "Host", value: "Hugging Face" },
    ],
    companionPostSlug: "yonder-drone-navigation-dataset",
    externalLinks: [
      { label: "Yonder on Hugging Face", href: SITE.yonderDataset, external: true },
      { label: "Dataset page", href: "/datasets/yonder" },
    ],
  },
  {
    slug: "metric-gap",
    title:
      "Closing the Metric Gap: From Diagnosis to Solution in Vision-Language Drone Navigation",
    venue: "Technical report",
    dateIso: "2025-02-21",
    schemaType: "Report",
    summary:
      "Large-scale closed-loop benchmark across many VLMs, decomposing failures into semantic understanding versus metric spatial grounding, and a modular architecture that closes the gap on operational commands while prioritizing collision-free flight.",
    tldr: [
      "A large-scale closed-loop benchmark across 25 vision-language models — every one underperformed a hovering baseline.",
      "Failures decompose into semantic understanding vs. metric spatial grounding; the metric gap dominates.",
      "A modular architecture separating semantics from geometry closes the gap on operational commands.",
    ],
    keyStats: [
      { label: "VLMs tested", value: "25" },
      { label: "Closed-loop flight trials", value: "10,200" },
      { label: "Baseline that won", value: "hover (do nothing)" },
    ],
    companionPostSlug: "metric-gap-vision-language-drone-navigation",
    externalLinks: [
      { label: "Simulation docs", href: "/docs/simulation" },
      { label: "GitHub", href: SITE.githubOrg, external: true },
    ],
  },
  {
    slug: "engineering-separation",
    title:
      "Engineering the Separation Principle: From Modular Architecture to Deployable Drone Navigation",
    venue: "Technical report",
    dateIso: "2024-11-08",
    schemaType: "Report",
    summary:
      "An eighteen-iteration engineering log: improving a modular autonomy stack in aggregate, scaling detector fine-tuning with large synthetic data, diagnosing a cross-simulator localization gap, and characterizing exploration and planning as the next bottlenecks.",
    tldr: [
      "An 18-iteration engineering log of a modular drone autonomy stack.",
      "Detector fine-tuning on 6.7M synthetic frames improved detection mAP 9.7× (4.8% → 46.7%) — but closed-loop navigation did not improve.",
      "A cross-simulator localization gap, not detection accuracy, is the binding constraint.",
    ],
    keyStats: [
      { label: "Iterations", value: "18" },
      { label: "Training frames", value: "6.7M" },
      { label: "Detection mAP", value: "4.8% → 46.7% (9.7×)" },
    ],
    companionPostSlug: "engineering-drone-autonomy-18-iterations",
    externalLinks: [
      { label: "Yonder dataset", href: "/datasets/yonder" },
      { label: "GitHub", href: SITE.githubOrg, external: true },
    ],
  },
  {
    slug: "scaling-separation",
    title:
      "Scaling the Separation Principle: Sensing Requirements for 1000-Drone Swarms in Urban and Natural Environments",
    venue: "Technical report",
    dateIso: "2025-05-19",
    schemaType: "Report",
    summary:
      "Controlled swarm simulations up to 1,000 agents comparing sensing stacks and coordination architectures, with a focus on when ultra-wideband ranging becomes necessary as fleet scale and environment difficulty increase.",
    tldr: [
      "Controlled swarm simulations up to 1,000 agents comparing sensing stacks and coordination architectures.",
      "Camera-only swarms lose 15.8 percentage points of coverage and see 8× the collision rate at 1,000 drones.",
      "UWB ranging becomes non-negotiable above ~100 drones.",
    ],
    keyStats: [
      { label: "Max swarm size", value: "1,000" },
      { label: "Coverage drop (camera-only)", value: "15.8 pp" },
      { label: "Collision-rate increase", value: "8×" },
      { label: "UWB threshold", value: "~100 drones" },
    ],
    companionPostSlug: "drone-swarm-sensing-1000-drones",
    externalLinks: [{ label: "GitHub", href: SITE.githubOrg, external: true }],
  },
  {
    slug: "gemma4-pilot",
    title:
      "Gemma 4 E2B as an End-to-End Drone Navigation Controller: A Pilot Trial in the 25-VLM Lineup",
    venue: "Technical note",
    dateIso: "2025-03-07",
    schemaType: "Report",
    summary:
      "Adds Gemma 4 to the same Isaac Sim closed-loop benchmark and compares end-to-end goal prediction against modular deployment of the same weights as a semantic target selector, illustrating the leverage of the separation principle.",
    tldr: [
      "Adds Gemma 4 E2B to the 25-VLM closed-loop Isaac Sim benchmark.",
      "Compares end-to-end goal prediction against modular deployment of the same weights as a semantic target selector.",
      "Illustrates the leverage of the separation principle on a single model.",
    ],
    keyStats: [
      { label: "Model", value: "Gemma 4 E2B" },
      { label: "Benchmark", value: "Isaac Sim closed-loop" },
      { label: "Lineup", value: "25 VLMs" },
    ],
    companionPostSlug: "why-vlm-drones-cant-beat-hovering",
    externalLinks: [{ label: "GitHub", href: SITE.githubOrg, external: true }],
  },
  {
    slug: "counter-uas",
    title:
      "Counter-UAS Attack and Defense Characterization in Autonomous Drone Swarms: A Kinematic Simulation Study",
    venue: "Technical report",
    dateIso: "2025-10-22",
    schemaType: "Report",
    summary:
      "11,340 seeded trials across four attack classes (GNSS spoofing, RF jamming, kinetic interception, control takeover) and six matched defenses in a four-drone warehouse swarm. Central finding: mission success rate is the wrong primary metric for C-UAS — physical effects (79.5% PN capture rate, 5–8 m position error) are clearly measurable even when aggregate task completion is unaffected. A kinematic plausibility detector achieves 39.8% TP at 0% false-positive rate. Includes an explicit fidelity boundary analysis delineating what kinematic simulation can and cannot faithfully reproduce.",
    tldr: [
      "11,340 seeded trials across 4 attack classes and 6 defenses in a 4-drone warehouse swarm.",
      "Mission-success rate is the wrong primary C-UAS metric — physical effects are measurable even when task completion is unaffected (79.5% PN capture, 5–8 m position error).",
      "A kinematic plausibility detector achieves 39.8% true-positive rate at 0% false positives.",
    ],
    keyStats: [
      { label: "Seeded trials", value: "11,340" },
      { label: "Attack classes", value: "4" },
      { label: "Matched defenses", value: "6" },
      { label: "PN capture rate", value: "79.5%" },
      { label: "Detector", value: "39.8% TP @ 0% FP" },
    ],
    companionPostSlug: "counter-uas-drone-attack-defense-simulation",
    externalLinks: [{ label: "GitHub", href: SITE.githubOrg, external: true }],
  },
  {
    slug: "droneport-atc",
    title:
      "Droneport ATC Coordination: A Factorial Study of Authority, Communications, and Sensing in Urban Air Mobility",
    venue: "Technical report",
    dateIso: "2025-12-11",
    schemaType: "Report",
    summary:
      "Nine-cell factorial study comparing tower vs. self-organized coordination, continuous vs. terminal-only communications, and four observation modalities (ADS-B, camera, both, none) across 405 simulated vertiport trials. Self-org with ADS-B matches tower throughput below ~20 ops/hour then degrades; silent-cruise drones exceed safe LoS thresholds at 12 ops/hour. Characterizes the throughput–safety Pareto frontier and broadcast necessity threshold for UAM droneport designs.",
    tldr: [
      "A 9-cell factorial study across 405 simulated vertiport trials: tower vs. self-organized authority, continuous vs. terminal comms, 4 observation modalities.",
      "Self-org with ADS-B matches tower throughput below ~20 ops/hour, then degrades.",
      "Silent-cruise drones exceed safe line-of-sight separation thresholds at just 12 ops/hour.",
    ],
    keyStats: [
      { label: "Simulated trials", value: "405" },
      { label: "Factorial cells", value: "9" },
      { label: "Self-org parity", value: "<20 ops/hr" },
      { label: "Silent-cruise breach", value: "12 ops/hr" },
    ],
    companionPostSlug: "droneport-atc-tower-vs-selforg",
    externalLinks: [{ label: "GitHub", href: SITE.githubOrg, external: true }],
  },
];

export function getResearchPaper(slug: string): ResearchPaperMeta | undefined {
  return researchPapers.find((p) => p.slug === slug);
}
