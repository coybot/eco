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

/** Public contact details (Contact page, footer, structured data). */
export const CONTACT = {
  legalName: "Coybot LLC",
  email: "hello@coy.bot",
  phoneDisplay: "+1 408-908-8273",
  phoneE164: "+14089088273",
  address: {
    street: "2150 N First St",
    city: "San Jose",
    region: "CA",
    postalCode: "95131",
    country: "US",
  },
  mapsUrl:
    "https://www.google.com/maps/search/?api=1&query=2150+N+First+St%2C+San+Jose%2C+CA+95131",
} as const;
