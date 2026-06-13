/** Default meta description (OG, Twitter, root layout). */
export const SITE_DEFAULT_DESCRIPTION =
  "Astral builds autonomy for uncrewed aircraft, rovers, and other robots: open SDK, simulation, operator apps, and datasets—integrate your own platform and models, or work with us on custom quadcopters, fixed-wing, rovers, and beyond.";

/** Canonical external URLs used across the marketing site. */
export const SITE = {
  origin: "https://astral.us",
  /** Public docs repo (Markdown + Mintlify sources); avoids dead web.astral.us. */
  docs: "https://github.com/astral-us/astral-docs",
  githubOrg: "https://github.com/astral-us",
  astralSdk: "https://github.com/astral-us/astral-sdk",
  yonderDataset: "https://huggingface.co/datasets/astralhf/yonder",
  yonderSample: "https://huggingface.co/datasets/astralhf/yonder-sample",
  youtube: "https://www.youtube.com/@astral_us",
  linkedin: "https://www.linkedin.com/company/astral-us",
  appStore:
    "https://apps.apple.com/us/app/astral-us-drone-operator/id6471107516",
} as const;
