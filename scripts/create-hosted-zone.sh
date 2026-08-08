#!/usr/bin/env bash
# Create the Route53 hosted zone for presidioautonomy.com and print the
# nameservers to paste into GoDaddy. Run this once, before any `sst deploy`
# for the presidio-website app — SST's ACM cert validation needs the zone
# (and, once you've updated GoDaddy, the delegation) to already exist.
#
# Usage: ./create-hosted-zone.sh
set -euo pipefail

DOMAIN="presidioautonomy.com"
PROFILE="${AWS_PROFILE:-presidio}"

echo "Using AWS profile: $PROFILE"
aws sts get-caller-identity --profile "$PROFILE" >/dev/null || {
  echo "Could not authenticate with profile '$PROFILE'. Check ~/.aws/config and ~/.aws/credentials." >&2
  exit 1
}

existing=$(aws route53 list-hosted-zones-by-name \
  --dns-name "$DOMAIN" \
  --profile "$PROFILE" \
  --query "HostedZones[?Name=='${DOMAIN}.'].Id" \
  --output text)

if [ -n "$existing" ]; then
  echo "Hosted zone for $DOMAIN already exists: $existing"
  zone_id="${existing#/hostedzone/}"
else
  echo "Creating hosted zone for $DOMAIN ..."
  caller_ref="presidio-autonomy-$(date +%s)"
  result=$(aws route53 create-hosted-zone \
    --name "$DOMAIN" \
    --caller-reference "$caller_ref" \
    --profile "$PROFILE" \
    --output json)
  zone_id=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)['HostedZone']['Id'].split('/')[-1])")
  echo "Created hosted zone: $zone_id"
fi

echo
echo "=== Nameservers — paste these into GoDaddy (Domain Settings -> Nameservers -> Custom) ==="
aws route53 get-hosted-zone \
  --id "$zone_id" \
  --profile "$PROFILE" \
  --query "DelegationSet.NameServers" \
  --output table

echo
echo "Next steps:"
echo "  1. Log into GoDaddy, open presidioautonomy.com DNS settings, switch to these 4 custom nameservers."
echo "  2. In the same sitting, recreate your GoDaddy email-forwarding MX records inside this Route53"
echo "     zone (aws route53 change-resource-record-sets) so hello@presidioautonomy.com keeps working —"
echo "     forwarding breaks the moment GoDaddy stops being authoritative for the domain."
echo "  3. Wait for propagation, then verify with: dig NS $DOMAIN @1.1.1.1"
echo "     Do not run 'sst deploy' before that dig returns these Route53 nameservers — ACM certificate"
echo "     validation will hang."
