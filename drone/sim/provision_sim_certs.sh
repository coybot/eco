#!/usr/bin/env bash
# Provision a real AWS IoT device cert + thing for one or more SIM drones, so a
# sim drone authenticates to AWS exactly like an IRL drone (mTLS, drone-policy-dev).
#
# Idempotent: skips any id that already has certs/<id>/device.pem locally.
# Certs land in ~/eco-certs-fleet/<id>/{device.pem,private.key,root-ca.pem} —
# the layout sim_drone_daemon.py --certs-dir expects.
#
#   AWS_PROFILE=astral bash provision_sim_certs.sh sim-quadcopter-001 [sim-rover-004 ...]
#
# Region is pinned: the `presidio` profile defaults to us-east-1 but the IoT stack
# lives in us-west-2.
set -euo pipefail

REGION="${REGION:-us-west-2}"
POLICY="${POLICY:-drone-policy-dev}"
CERTS_BASE="${CERTS_BASE:-$HOME/eco-certs-fleet}"
ROOT_CA_URL="https://www.amazontrust.com/repository/AmazonRootCA1.pem"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <drone-id> [<drone-id> ...]" >&2
  exit 2
fi

aws_iot() { aws iot --region "$REGION" "$@"; }

for ID in "$@"; do
  DIR="$CERTS_BASE/$ID"
  if [[ -f "$DIR/device.pem" && -f "$DIR/private.key" ]]; then
    echo "[$ID] certs already present at $DIR — skipping"
    continue
  fi
  mkdir -p "$DIR"

  # Thing (no-op if it already exists).
  aws_iot create-thing --thing-name "$ID" >/dev/null 2>&1 \
    || echo "[$ID] thing exists (ok)"

  echo "[$ID] creating keys + certificate…"
  ARN=$(aws_iot create-keys-and-certificate --set-as-active \
    --certificate-pem-outfile "$DIR/device.pem" \
    --private-key-outfile "$DIR/private.key" \
    --query certificateArn --output text)
  echo "[$ID] cert ARN: $ARN"

  aws_iot attach-policy --policy-name "$POLICY" --target "$ARN"
  aws_iot attach-thing-principal --thing-name "$ID" --principal "$ARN"

  if [[ ! -f "$DIR/root-ca.pem" ]]; then
    curl -fsSL "$ROOT_CA_URL" -o "$DIR/root-ca.pem"
  fi
  echo "[$ID] provisioned -> $DIR"
done

echo "Done. certs base: $CERTS_BASE"
