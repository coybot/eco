import { bucket } from "./storage";

const isProd = $app.stage === "prod";
const domainName = isProd ? "coy.bot" : `${$app.stage}.coy.bot`;

// Next.js website deployed to AWS via OpenNext
export const web = new sst.aws.Nextjs("CoybotWebsite", {
  path: ".",

  link: [bucket],

  domain: {
    name: domainName,
    redirects: isProd ? ["www.coy.bot"] : [],
  },

  environment: {
    NEXT_PUBLIC_BASE_URL: `https://${domainName}`,
  },

  warm: isProd ? 5 : 1,

  memory: "1024 MB",

  transform: {
    cdn: (args) => {
      args.comment = `Coybot Website - ${$app.stage}`;
    },
  },
});
