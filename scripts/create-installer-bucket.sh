#!/usr/bin/env bash
# Create the presidio-drone-installer S3 bucket and copy the existing
# astral-drone-installer bucket's contents (installer script + fleet
# provisioning claim certs) into it. Run this before anyone follows the
# renamed drone/installer/install.sh or README one-liners — they now point
# at presidio-drone-installer, which doesn't exist until this runs.
#
# Usage: ./create-installer-bucket.sh
set -euo pipefail

SRC_BUCKET="astral-drone-installer"
DST_BUCKET="presidio-drone-installer"
PROFILE="${AWS_PROFILE:-presidio}"

echo "Using AWS profile: $PROFILE"
aws sts get-caller-identity --profile "$PROFILE" >/dev/null || {
  echo "Could not authenticate with profile '$PROFILE'. Check ~/.aws/config and ~/.aws/credentials." >&2
  exit 1
}

if ! aws s3api head-bucket --bucket "$SRC_BUCKET" --profile "$PROFILE" 2>/dev/null; then
  echo "Source bucket $SRC_BUCKET not found or not accessible with profile $PROFILE." >&2
  exit 1
fi

region=$(aws s3api get-bucket-location --bucket "$SRC_BUCKET" --profile "$PROFILE" --query "LocationConstraint" --output text)
[ "$region" = "None" ] && region="us-east-1"
echo "Source bucket region: $region"

if aws s3api head-bucket --bucket "$DST_BUCKET" --profile "$PROFILE" 2>/dev/null; then
  echo "Destination bucket $DST_BUCKET already exists — skipping creation."
else
  echo "Creating $DST_BUCKET in $region ..."
  if [ "$region" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$DST_BUCKET" --profile "$PROFILE" --region "$region"
  else
    aws s3api create-bucket --bucket "$DST_BUCKET" --profile "$PROFILE" --region "$region" \
      --create-bucket-configuration LocationConstraint="$region"
  fi
fi

echo "Copying objects from $SRC_BUCKET to $DST_BUCKET ..."
aws s3 sync "s3://$SRC_BUCKET" "s3://$DST_BUCKET" --profile "$PROFILE"

echo
echo "Checking $SRC_BUCKET's bucket policy for public-read grants to mirror onto $DST_BUCKET ..."
if aws s3api get-bucket-policy --bucket "$SRC_BUCKET" --profile "$PROFILE" --query Policy --output text > /tmp/astral-installer-policy.json 2>/dev/null; then
  echo "Source policy saved to /tmp/astral-installer-policy.json — review it, then apply the equivalent"
  echo "(with the bucket name swapped) to $DST_BUCKET:"
  echo "  aws s3api put-bucket-policy --bucket $DST_BUCKET --profile $PROFILE --policy file://<edited-policy>.json"
else
  echo "No bucket policy on $SRC_BUCKET (or not permitted to read it) — nothing to mirror."
fi

echo
echo "Done. Verify with:"
echo "  aws s3 ls s3://$DST_BUCKET --profile $PROFILE"
echo "  curl -fsSL https://$DST_BUCKET.s3.amazonaws.com/install.sh | head"
