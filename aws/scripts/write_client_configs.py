#!/usr/bin/env python3
"""Write the drone-api stack's endpoints into every client that hard-codes them.

Read-only against AWS: it calls `cloudformation describe-stacks` and
`iot describe-endpoint`, then rewrites the files below in place. Run it after
each deploy that replaces the stack (new account, new region, new user pool):

    python3 aws/scripts/write_client_configs.py            # profile coybot, us-east-2
    python3 aws/scripts/write_client_configs.py --check    # report, change nothing

Every pattern must match at least once; a miss fails the run, so a file that
was restructured gets noticed instead of silently keeping old values.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ECO = Path(__file__).resolve().parents[2]


def aws(profile: str, region: str, *args: str) -> dict:
    out = subprocess.run(
        ["aws", *args, "--profile", profile, "--region", region, "--output", "json"],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def stack_values(profile: str, region: str, stack: str) -> dict:
    outs = aws(profile, region, "cloudformation", "describe-stacks", "--stack-name", stack)
    o = {x["OutputKey"]: x["OutputValue"] for x in outs["Stacks"][0].get("Outputs", [])}
    creds = aws(profile, region, "iot", "describe-endpoint",
                "--endpoint-type", "iot:CredentialProvider")["endpointAddress"]
    return {
        "region": region,
        "api": o["ApiEndpoint"],
        "iot": o["IoTEndpoint"],
        "creds": creds,
        "user_pool": o["UserPoolId"],
        "client": o["UserPoolClientId"],
        "identity_pool": o["IdentityPoolId"],
        "hosted_ui": o["UserPoolDomain"],
        "bucket": o["ImagesBucketName"],
        "s3_alias": o["DroneS3RoleAlias"],
        "video_alias": o["DroneVideoRoleAlias"],
    }


def q(s: str) -> str:
    """Replacement text for a quoted value (escape backslashes for re.sub)."""
    return s.replace("\\", "\\\\")


def rules(v: dict) -> dict[str, list[tuple[str, str]]]:
    swift = lambda k, val: (rf'(static let {k} = )"[^"]*"', rf'\g<1>"{q(val)}"')
    kt = lambda k, val: (rf'(private const val {k}\s*= )"[^"]*"', rf'\g<1>"{q(val)}"')
    yml = lambda k, val: (rf'(?m)^({k}: )"[^"]*"', rf'\g<1>"{q(val)}"')
    ts = lambda k, val: (rf'(NEXT_PUBLIC_OPERATOR_{k} \?\?\s*)"[^"]*"', rf'\g<1>"{q(val)}"')
    return {
        "client/ios/DroneOperator/DroneOperator/Config/AWSConfig.swift": [
            swift("region", v["region"]),
            swift("apiEndpoint", v["api"]),
            swift("identityPoolId", v["identity_pool"]),
            swift("userPoolId", v["user_pool"]),
            swift("iotEndpoint", v["iot"]),
            swift("imagesBucket", v["bucket"]),
            swift("cognitoClientId", v["client"]),
            swift("cognitoHostedUIDomain", v["hosted_ui"]),
        ],
        "client/android/app/src/main/java/com/coybot/drone/MainActivity.kt": [
            kt("API_BASE", v["api"]),
            kt("COGNITO_REGION", v["region"]),
            kt("COGNITO_CLIENT_ID", v["client"]),
            kt("COGNITO_HOSTED_UI", v["hosted_ui"]),
        ],
        "client/android/BUILD.md": [
            kt("API_BASE", v["api"]),
            kt("COGNITO_REGION", v["region"]),
            kt("COGNITO_CLIENT_ID", v["client"]),
            (r"(--user-pool-id )\S+", rf"\g<1>{q(v['user_pool'])}"),
        ],
        "drone/common/config.stack-dev.yaml": [
            yml("iot_endpoint", v["iot"]),
            yml("region", v["region"]),
            yml("credentials_endpoint", v["creds"]),
            yml("images_bucket", v["bucket"]),
            yml("s3_role_alias", v["s3_alias"]),
            yml("video_role_alias", v["video_alias"]),
        ],
        "drone/sim/BRINGUP_MAC.md": [
            (r"POOL=\S+; CLIENT=\S+", f"POOL={q(v['user_pool'])}; CLIENT={q(v['client'])}"),
        ],
        "www/src/lib/operator/config.ts": [
            ts("API_ENDPOINT", v["api"]),
            ts("REGION", v["region"]),
            ts("IDENTITY_POOL_ID", v["identity_pool"]),
            ts("IOT_ENDPOINT", v["iot"]),
            ts("COGNITO_USER_POOL_ID", v["user_pool"]),
            ts("COGNITO_APP_CLIENT_ID", v["client"]),
            ts("COGNITO_REGION", v["region"]),
            ts("COGNITO_HOSTED_UI_URL", v["hosted_ui"]),
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", default="coybot")
    ap.add_argument("--region", default="us-east-2")
    ap.add_argument("--stack", default="drone-api")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    v = stack_values(args.profile, args.region, args.stack)
    failed = False
    for rel, subs in rules(v).items():
        path = ECO / rel
        text = path.read_text()
        new = text
        for pat, rep in subs:
            new, n = re.subn(pat, rep, new)
            if n == 0:
                print(f"MISS  {rel}: {pat}", file=sys.stderr)
                failed = True
        changed = new != text
        if changed and not args.check:
            path.write_text(new)
        print(f"{'would update' if args.check and changed else 'updated' if changed else 'unchanged':12} {rel}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
