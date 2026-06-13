import * as aws from "@pulumi/aws";

// S3 bucket for product images and static assets, served via CloudFront
export const storage = {
  bucket: new sst.aws.Bucket("AssetsBucket", {
    access: "cloudfront",
  }),
};

export const { bucket } = storage;

// DynamoDB table for API rate limiting — PAY_PER_REQUEST, TTL auto-expires entries
export const rateLimitTable = new aws.dynamodb.Table("RateLimitTable", {
  hashKey: "pk",
  attributes: [{ name: "pk", type: "S" }],
  billingMode: "PAY_PER_REQUEST",
  ttl: { attributeName: "ttl", enabled: true },
});
