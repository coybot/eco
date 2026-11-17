import { bucket } from "./storage";

const isProd = $app.stage === "prod";
const domainName = isProd ? "coy.bot" : `${$app.stage}.coy.bot`;

// Next.js website deployed to AWS via OpenNext
export const web = new sst.aws.Nextjs("CoybotWebsite", {
  path: ".",

  link: [bucket],

  // Only CloudFront may call the server/image Lambdas. Needed since AWS began
  // requiring BOTH lambda:InvokeFunctionUrl and lambda:InvokeFunction on
  // function URLs: SST >= 3.19 grants both in "oac" mode, but only the first in
  // the default "none" mode, which made every page 403. OAC's caveat (POSTs need
  // x-amz-content-sha256) doesn't bite: the site only serves GETs; the operator
  // console POSTs go to the separate drone API.
  protection: "oac",

  domain: {
    name: domainName,
    redirects: isProd ? ["www.coy.bot"] : [],
    // The coy.bot zone already holds the ACM validation CNAMEs (same values ACM
    // asks for again) and, until webstack's placeholder site is retired, the
    // apex/www A records. Let SST take them over instead of failing on
    // "already exists". If webstack is torn down later, don't let it delete them.
    dns: sst.aws.dns({ override: true }),
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
