#!/usr/bin/env python3
"""One-off: make the OLD AWS account's CloudFront distributions give up the
astral.us / www.astral.us names, so the new account's redirect distribution
can claim them (CloudFront alternate domain names are unique across all AWS
accounts).

For each distribution: remove its alternate domain names, switch it to the
default *.cloudfront.net certificate, and disable it. Nothing is deleted.

Usage:  AWS_PROFILE=astral python3 scripts/release-old-astral-aliases.py
"""
import json
import os
import subprocess
import sys

IDS = ["E2WBQEXLJHR0L9", "E1OSVG72FXQSWF"]  # astral.us, www.astral.us


def aws(*args):
    p = subprocess.run(["aws", *args, "--output", "json"],
                       capture_output=True, text=True,
                       env={**os.environ, "AWS_PAGER": ""})
    if p.returncode != 0:
        sys.exit(f"aws {args[0]} {args[1]} failed:\n{p.stderr.strip()}")
    return json.loads(p.stdout) if p.stdout.strip() else None


if not os.environ.get("AWS_PROFILE"):
    sys.exit("Set AWS_PROFILE to the OLD account's profile (astral).")

print("Account:", aws("sts", "get-caller-identity")["Account"], "profile", os.environ["AWS_PROFILE"])
configs = {}
for i in IDS:
    r = aws("cloudfront", "get-distribution-config", "--id", i)
    configs[i] = r
    c = r["DistributionConfig"]
    print(f"  {i}: {c.get('Comment')!r} aliases={c['Aliases'].get('Items', [])} enabled={c['Enabled']}")

if input("Remove these names and disable both distributions? [y/N] ").strip() != "y":
    sys.exit("Aborted, nothing changed.")

for i, r in configs.items():
    c = r["DistributionConfig"]
    c["Aliases"] = {"Quantity": 0}
    c["ViewerCertificate"] = {"CloudFrontDefaultCertificate": True,
                              "MinimumProtocolVersion": "TLSv1",
                              "CertificateSource": "cloudfront"}
    c["Enabled"] = False
    res = aws("cloudfront", "update-distribution", "--id", i, "--if-match", r["ETag"],
              "--distribution-config", json.dumps(c))
    d = res["Distribution"]
    print(f"  {i}: status={d['Status']} enabled={d['DistributionConfig']['Enabled']} "
          f"aliases={d['DistributionConfig']['Aliases']['Quantity']}")

print("Done. The names are free once the update is accepted (status may show InProgress for a few minutes).")
