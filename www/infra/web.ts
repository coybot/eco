import { bucket } from "./storage";
import * as aws from "@pulumi/aws";

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

// Grant the SSR Lambda role permission to invoke Bedrock models
const bedrockPolicy = new aws.iam.RolePolicy("AstralWebsiteBedrockPolicy", {
  role: web.nodes.server.nodes.role.name,
  policy: JSON.stringify({
    Version: "2012-10-17",
    Statement: [
      {
        Effect: "Allow",
        Action: ["bedrock:InvokeModel"],
        Resource: "*",
      },
    ],
  }),
});
