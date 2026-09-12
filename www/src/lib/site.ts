/** Default meta description (OG, Twitter, root layout). */
export const SITE_DEFAULT_DESCRIPTION =
  "Coybot builds autonomy for uncrewed aircraft, rovers, and other robots: open SDK, simulation, operator apps, and datasets—integrate your own platform and models, or work with us on custom quadcopters, fixed-wing, rovers, and beyond.";

/** Canonical external URLs used across the marketing site. */
export const SITE = {
  origin: "https://coy.bot",
  /** Public docs repo (Markdown + Mintlify sources); avoids dead web.coy.bot. */
  docs: "https://github.com/coybot/coybot-docs",
  githubOrg: "https://github.com/coybot",
  coybotSdk: "https://github.com/coybot/coybot-sdk",
  yonderDataset: "https://huggingface.co/datasets/coybothf/yonder",
  yonderSample: "https://huggingface.co/datasets/coybothf/yonder-sample",
  droneModels: "https://huggingface.co/coybothf/coybot-drone-models",
  youtube: "https://www.youtube.com/@coybot_us",
  linkedin: "https://www.linkedin.com/company/coybot",
  appStore:
    "https://apps.apple.com/us/app/coybot-drone-operator/id6471107516",
} as const;
