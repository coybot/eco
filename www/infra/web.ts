import { bucket } from "./storage";
import { ordersTable } from "./database";

// Next.js website deployed to AWS via OpenNext
export const web = new sst.aws.Nextjs("AstralWebsite", {
  path: ".", // Root of the project contains Next.js app

  // Link AWS resources to Next.js (available as env vars)
  link: [bucket, ordersTable],
  
  // Environment variables
  environment: {
    NEXT_PUBLIC_BASE_URL: $app.stage === "production" 
      ? "https://astral.us" 
      : `https://${$app.stage}.astral.us`,
  },
  
  // Domain configuration (uncomment when ready)
  // domain: {
  //   name: $app.stage === "production" ? "astral.us" : `${$app.stage}.astral.us`,
  //   dns: sst.aws.dns({ zone: "astral.us" }),
  //   cert: "arn:aws:acm:us-east-1:...", // ACM certificate ARN
  // },
  
  // Build settings
  buildCommand: "npm run build",
  
  // Warm the Lambda functions to reduce cold starts
  warm: $app.stage === "production" ? 5 : 1,
  
  // Memory allocation for Lambda
  memory: "1024 MB",
  
  // OpenNext settings
  transform: {
    cdn: (args) => {
      // Custom CloudFront settings
      args.comment = `Astral Website - ${$app.stage}`;
    },
  },
});
