import { bucket } from "./storage";

const isProd = $app.stage === "prod";
const domainName = isProd ? "astral.us" : `${$app.stage}.astral.us`;

// Next.js website deployed to AWS via OpenNext
export const web = new sst.aws.Nextjs("AstralWebsite", {
  path: ".",

  link: [bucket],

  domain: {
    name: domainName,
    redirects: isProd ? ["www.astral.us"] : [],
  },

  environment: {
    NEXT_PUBLIC_BASE_URL: `https://${domainName}`,
  },

  warm: isProd ? 5 : 1,

  memory: "1024 MB",

  transform: {
    cdn: (args) => {
      args.comment = `Astral Website - ${$app.stage}`;
    },
  },
});
