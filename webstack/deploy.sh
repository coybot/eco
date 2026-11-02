#!/usr/bin/env bash
# Usage: AWS_PROFILE=newproj DOMAIN=example.com ZONE_ID=Z123... ./deploy.sh
# Refuses to run without an explicit profile so it can't hit the wrong account.
#
# The account is an AWS "new experience" project: its SCP denies everything in
# us-east-1 except global services (ACM, CloudFront, IAM, Route 53, ...). So the
# stack runs in the project's home Region, and only the CloudFront certificate,
# which must live in us-east-1, is requested there directly through ACM.
set -euo pipefail
: "${AWS_PROFILE:?set AWS_PROFILE to the profile of the NEW account}"
: "${DOMAIN:?set DOMAIN, e.g. example.com}"
: "${ZONE_ID:?set ZONE_ID to the Route 53 hosted zone id}"
STACK="${STACK:-webstack}"
REGION="${REGION:-us-east-2}"
cd "$(dirname "$0")"

echo "Deploying to account: $(aws sts get-caller-identity --query Account --output text) (profile $AWS_PROFILE, region $REGION)"
read -r -p "Continue? [y/N] " ok
[ "$ok" = "y" ] || exit 1

# CloudFront certificate (us-east-1): reuse an existing one for the domain, else request and validate it.
CF_CERT=$(aws acm list-certificates --region us-east-1 \
  --query "CertificateSummaryList[?DomainName=='$DOMAIN'].CertificateArn | [0]" --output text)
if [ -z "$CF_CERT" ] || [ "$CF_CERT" = "None" ]; then
  CF_CERT=$(aws acm request-certificate --region us-east-1 --domain-name "$DOMAIN" \
    --subject-alternative-names "www.$DOMAIN" --validation-method DNS \
    --query CertificateArn --output text)
  echo "Requested CloudFront certificate $CF_CERT"
fi
STATUS=$(aws acm describe-certificate --region us-east-1 --certificate-arn "$CF_CERT" \
  --query Certificate.Status --output text)
if [ "$STATUS" != "ISSUED" ]; then
  # Validation records show up a few seconds after the request.
  until RECORDS=$(aws acm describe-certificate --region us-east-1 --certificate-arn "$CF_CERT" \
      --query "Certificate.DomainValidationOptions[].ResourceRecord.[Name,Value]" --output text) \
      && [ -n "$RECORDS" ] && ! echo "$RECORDS" | grep -q None; do sleep 5; done
  CHANGES=""
  while read -r NAME VALUE; do
    CHANGES="$CHANGES{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"$NAME\",\"Type\":\"CNAME\",\"TTL\":300,\"ResourceRecords\":[{\"Value\":\"$VALUE\"}]}},"
  done <<< "$(echo "$RECORDS" | sort -u)"
  aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" \
    --change-batch "{\"Changes\":[${CHANGES%,}]}" >/dev/null
  echo "Waiting for the CloudFront certificate to validate (usually a few minutes)..."
  aws acm wait certificate-validated --region us-east-1 --certificate-arn "$CF_CERT"
fi
echo "CloudFront certificate: $CF_CERT"

sam build
sam deploy --stack-name "$STACK" --region "$REGION" --resolve-s3 \
  --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset \
  --parameter-overrides "DomainName=$DOMAIN" "HostedZoneId=$ZONE_ID" "CloudFrontCertificateArn=$CF_CERT"

out() { aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
BUCKET=$(out SiteBucketName); DIST=$(out DistributionId)

aws s3 sync site/ "s3://$BUCKET" --delete
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/*" >/dev/null
echo "Site: https://$DOMAIN   API: https://api.$DOMAIN/health"
