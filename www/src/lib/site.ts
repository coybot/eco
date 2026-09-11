/** Default meta description (OG, Twitter, root layout). */
export const SITE_DEFAULT_DESCRIPTION =
  "Presidio Autonomy is an open-source stack for building — and honestly measuring — real drone autonomy. Not waypoint following: what happens when GPS drops, comms die, and the mission changes mid-flight.";

/** Canonical external URLs used across the site. */
export const SITE = {
  origin: "https://presidioautonomy.com",
  email: "hello@presidioautonomy.com",
  githubOrg: "https://github.com/presidio-autonomy",
  presidioSdk: "https://github.com/presidio-autonomy/presidio-sdk",
  presidioDocs: "https://github.com/presidio-autonomy/presidio-docs",
  eco: "https://github.com/presidio-autonomy/eco",
  /** HuggingFace org migration hasn't happened yet — see repo NOTICE files. */
  yonderDataset: "https://huggingface.co/datasets/astralhf/yonder",
  yonderSample: "https://huggingface.co/datasets/astralhf/yonder-sample",
} as const;
