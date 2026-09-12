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
          region: "us-east-1",
        },
      },
    };
  },
  async run() {
    const { storage, rateLimitTable } = await import("./infra/storage");
    const { web } = await import("./infra/web");

    return {
      websiteUrl: web.url,
      bucketName: storage.bucket.name,
      rateLimitTable: rateLimitTable.name,
    };
  },
});
