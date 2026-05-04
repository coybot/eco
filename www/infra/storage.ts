// S3 bucket for product images and assets
export const storage = {
  bucket: new sst.aws.Bucket("AssetsBucket", {
    access: "cloudfront", // Serve via CloudFront for better performance
    transform: {
      bucket: {
        lifecycleRules: [
          {
            enabled: true,
            // Clean up incomplete multipart uploads after 7 days
            abortIncompleteMultipartUpload: {
              daysAfterInitiation: 7,
            },
          },
        ],
      },
    },
  }),
};

// Export for use in other stacks
export const { bucket } = storage;
