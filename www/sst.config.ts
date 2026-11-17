/// <reference path="./.sst/platform/config.d.ts" />

export default $config({
  app(input) {
    return {
      name: "coybot-website",
      removal: input?.stage === "prod" ? "retain" : "remove",
      protect: ["prod"].includes(input?.stage),
      home: "aws",
      providers: {
        aws: {
          // us-east-2: the coybot AWS org's region-restriction SCP only allows
          // regional services (Lambda, DynamoDB, SQS, S3) in us-east-2. The
          // CloudFront distribution and its ACM cert are created in us-east-1
          // by SST itself, which that policy allows.
          region: "us-east-2",
        },
      },
    };
  },
  async run() {
    const { storage } = await import("./infra/storage");
    const { web } = await import("./infra/web");

    return {
      websiteUrl: web.url,
      bucketName: storage.bucket.name,
    };
  },
});
