# Quickstart: bring your own AWS account

`control_plane: "aws"` is the other backend — real DynamoDB, IoT Core, and
S3 instead of the GCS's local stand-ins. If you don't need this, start with
[`quickstart-gcs.md`](quickstart-gcs.md) instead — it needs no AWS account.

## What ships here, and what doesn't

This repo ships the vendor-neutral Lambda handler code
(`aws/src/{handler,conversations,drones,groups,rover,llm}.py` and
`aws/src/backends/aws.py`) and the drone/app sides that talk to it. It does
**not** ship a deployable CloudFormation/SAM template — that's part of
Coybot's own private deployment, not published here. To run the AWS
backend yourself, you'll deploy your own infrastructure:

- **Lambda functions** wrapping `aws/src/`'s handler functions — see
  [`api-reference.md`](api-reference.md) for the full route table
  (method, path, handler function) to wire up in API Gateway.
- **DynamoDB tables** — 5 tables, keyed exactly as
  `aws/src/backends/local.py`'s `DEFAULT_TABLE_SCHEMAS` documents (that's
  the same shape the real tables use, since the local shim was built to
  match): `drone-registry` (`userId`, `droneId`), `drone-status`
  (`droneId`), `drone-logs` (`droneId`, `timestamp`),
  `drone-chat-conversations` (`PK`, `SK`), `drone-groups` (`userId`, `groupId`).
- **IoT Core**: a policy for your drones' MQTT pub/sub, plus Fleet
  Provisioning if you want self-provisioning devices — see
  [`certificates.md`](certificates.md).
- **S3 bucket** for uploaded photos.
- **Auth**: `aws/src/`'s handlers expect
  `event['requestContext']['authorizer']['userId']` — a Cognito authorizer
  (or your own) needs to populate that.

If this is more than you want to stand up yourself, GCS mode gets you the
same command/chat/registry functionality with a docker-compose-sized setup
instead — see [`quickstart-gcs.md`](quickstart-gcs.md).

## Drone-side config

```yaml
control_plane: "aws"
iot_endpoint: "YOUR_IOT_ENDPOINT.iot.YOUR_REGION.amazonaws.com"  # aws iot describe-endpoint --endpoint-type iot:Data-ATS
region: "YOUR_REGION"
images_bucket: "YOUR_IMAGES_BUCKET"
```

See [`config-reference.md`](config-reference.md) for every key, including
the video-streaming and S3-role-alias keys (both AWS-only).

## Certificates

AWS mode needs a claim certificate for Fleet Provisioning, or you can
provision device certificates manually. See [`certificates.md`](certificates.md).

## Trying it without any of this: SITL

None of the above is needed to try the raw flight SDK — see
[`simulation.md`](simulation.md) for running against ArduPilot SITL with no
backend deployed at all.

## App configuration

`client/ios/DroneOperator/DroneOperator/Config/AWSConfig.swift` ships with
placeholders — fill in your own API Gateway endpoint, Cognito pool IDs, IoT
endpoint, and S3 bucket from your deployment's outputs before building.
Xcode needs this file to exist to build at all, so it's tracked (not
gitignored) — be careful not to commit your real values back if you're
contributing changes upstream. `client/android/.../MainActivity.kt`'s
top-of-file constants work the same way.
