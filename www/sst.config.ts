/// <reference path="./.sst/platform/config.d.ts" />

export default $config({
  app(input) {
    return {
      name: "astral-website",
      removal: input?.stage === "production" ? "retain" : "remove",
      protect: ["production"].includes(input?.stage),
      home: "aws",
      providers: {
        aws: {
          region: "us-east-1",
        },
      },
    };
  },
  async run() {
    // Import stacks
    const { storage } = await import("./infra/storage");
    const { database } = await import("./infra/database");
    const { email } = await import("./infra/email");
    const { api } = await import("./infra/api");
    const { web } = await import("./infra/web");

    return {
      websiteUrl: web.url,
      bucketName: storage.bucket.name,
      ordersTableName: database.ordersTable.name,
    };
  },
});
