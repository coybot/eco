import { bucket, rateLimitTable } from "./storage";
import * as aws from "@pulumi/aws";

const isProd = $app.stage === "prod";
const domainName = isProd ? "presidioautonomy.com" : `${$app.stage}.presidioautonomy.com`;

// Next.js website deployed to AWS via OpenNext
export const web = new sst.aws.Nextjs("PresidioWebsite", {
  path: ".",

  link: [bucket],

  domain: {
    name: domainName,
    redirects: isProd ? ["www.presidioautonomy.com"] : [],
  },

  environment: {
    NEXT_PUBLIC_BASE_URL: `https://${domainName}`,
    RATE_LIMIT_TABLE: rateLimitTable.name,
  },

  warm: 1,

  memory: "1024 MB",

  transform: {
    cdn: (args) => {
      args.comment = `Presidio Website - ${$app.stage}`;
    },
  },
});

// Grant the SSR Lambda role permission to invoke Bedrock models (for the
// plan-mission natural-language simulation demo) and use the rate-limit table
const lambdaPolicy = new aws.iam.RolePolicy("PresidioWebsiteLambdaPolicy", {
  role: web.nodes.server.nodes.role.name,
  policy: $jsonStringify({
    Version: "2012-10-17",
    Statement: [
      { Effect: "Allow", Action: ["bedrock:InvokeModel"], Resource: "*" },
      { Effect: "Allow", Action: ["dynamodb:UpdateItem", "dynamodb:GetItem"], Resource: rateLimitTable.arn },
    ],
  }),
});
