/// <reference path="./.sst/platform/config.d.ts" />

/**
 * Deploy the drone API (AWS SAM / CloudFormation stack `drone-api`), triggered
 * through SST so ops matches `eco/www` (`npx sst deploy`).
 *
 *   export DRONE_API_PARAMETERS="Environment=dev DroneId=... GoogleClientId=... \
 *     SimHostBase=... SimPublicWss=... SimInstanceId=..."
 *   AWS_PROFILE=<profile> npx sst deploy
 *
 * DRONE_API_PARAMETERS carries the stack's non-secret parameters, read them
 * from the live stack with
 *   aws cloudformation describe-stacks --stack-name drone-api --query 'Stacks[0].Parameters'
 * They are not in this repo on purpose: it is public, and the values include
 * the sim host's address. Parameters left out (the NoEcho secrets, empty
 * AppleServicesId) keep their current values. Without the variable this
 * refuses to run: a deploy on the template's placeholder defaults replaces the
 * live Google client id and sim host.
 *
 * Requires: AWS credentials, SAM CLI (`sam`), Python 3.13 for `sam build`.
 */
export default $config({
  app(input) {
    return {
      name: "coybot-drone-api",
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
    const parameters = process.env.DRONE_API_PARAMETERS?.trim();
    if (!parameters) {
      throw new Error(
        "DRONE_API_PARAMETERS is not set - see the comment at the top of aws/sst.config.ts",
      );
    }

    new command.local.Command("SamDeploy", {
      dir: samRoot,
      create:
        "sam build && sam deploy --stack-name drone-api --no-confirm-changeset " +
        "--no-fail-on-empty-changeset --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM " +
        "--parameter-overrides $DRONE_API_PARAMETERS",
      environment: { DRONE_API_PARAMETERS: parameters },
      // A Command only reruns when its inputs change, and these never do, so
      // without this every `sst deploy` after the first silently skipped SAM.
      triggers: [Date.now()],
      // Do not tear down the CloudFormation stack when `sst remove` runs.
      delete: ":",
    });

    return {
      message: "SAM built and updated stack `drone-api`.",
    };
  },
});
