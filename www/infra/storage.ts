// S3 bucket for product images and static assets, served via CloudFront
export const storage = {
  bucket: new sst.aws.Bucket("AssetsBucket", {
    access: "cloudfront",
  }),
};

export const { bucket } = storage;
