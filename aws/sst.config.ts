/// <reference path="./.sst/platform/config.d.ts" />

/**
 * Deploy the drone API the same way as before (AWS SAM / CloudFormation),
 * but triggered through SST so ops matches `eco/www` (`npx sst deploy`).
 *
 * Requires: AWS credentials, SAM CLI (`sam`), Python 3.13 for `sam build`.
 * Region and stack name come from `samconfig.toml` and template parameters.
 */
export default $config({
  app(input) {
    return {
      name: "astral-drone-api",
      removal: input?.stage === "prod" ? "retain" : "remove",
      home: "aws",
      providers: {
        aws: {
          region: "us-west-2",
        },
      },
    };
  },
  async run() {
    const command = await import("@pulumi/command");
    const samRoot = process.cwd();

    new command.local.Command("SamDeploy", {
      dir: samRoot,
      create:
        "sam build && sam deploy --no-confirm-changeset --no-fail-on-empty-changeset --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM",
      // Do not tear down the CloudFormation stack when `sst remove` runs.
      delete: ":",
    });

    return {
      message:
        "SAM deploy command registered. On `sst deploy`, SAM builds and updates stack `drone-api` (see samconfig.toml).",
    };
  },
});
