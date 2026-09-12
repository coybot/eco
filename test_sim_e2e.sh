#!/usr/bin/env bash
# End-to-end test for sim drone flow.
# Usage: ./test_sim_e2e.sh <cloudflare-tunnel-url>
# Example: ./test_sim_e2e.sh https://abc-def-123.trycloudflare.com
#
# Prerequisites:
#   - sim_bridge.py running on hoopoe: cd ~/code/ishmael/benchmark && ~/isaac-sim-env/bin/python3 sim_bridge.py --env office
#   - cloudflared tunnel: ~/bin/cloudflared tunnel --url http://localhost:8765
#   - AWS profile 'coybot' configured

set -e

TUNNEL_URL="${1:?Usage: $0 <tunnel-url>}"
USER_ID="28a1f320-4051-70cd-1c82-9f5b07b8a5d9"
DRONE_ID="sim-quadcopter-e2e"
CONV_ID="e2e-conv-$(date +%s)"
REGION="us-west-2"

echo "=== Step 1: Register sim drone with tunnel URL ==="
AWS_PROFILE=coybot aws dynamodb put-item \
  --table-name drone-registry-dev \
  --region $REGION \
  --item "{
    \"userId\": {\"S\": \"$USER_ID\"},
    \"droneId\": {\"S\": \"$DRONE_ID\"},
    \"name\": {\"S\": \"E2E Test Sim\"},
    \"registeredAt\": {\"S\": \"$(date -u +%Y-%m-%dT%H:%M:%S)\"},
    \"status\": {\"S\": \"registered\"},
    \"droneType\": {\"S\": \"sim\"},
    \"vehicleType\": {\"S\": \"quadcopter\"},
    \"simEnvironment\": {\"S\": \"office\"},
    \"isaacHost\": {\"S\": \"$TUNNEL_URL\"}
  }"
echo "✅ Registered $DRONE_ID with isaacHost=$TUNNEL_URL"

echo ""
echo "=== Step 2: Test bridge health via tunnel ==="
curl -sf "$TUNNEL_URL/health" | python3 -m json.tool
echo ""

echo "=== Step 3: Send chat message via Lambda ==="
PAYLOAD=$(cat <<PAYLOAD
{
  "requestContext": {"authorizer": {"userId": "$USER_ID"}},
  "pathParameters": {"droneId": "$DRONE_ID", "conversationId": "$CONV_ID"},
  "body": "{\"message\": \"go up 2 meters and tell me what you see then land\"}"
}
PAYLOAD
)

echo "$PAYLOAD" > /tmp/e2e_event.json

AWS_PROFILE=coybot aws lambda invoke \
  --function-name drone-chat-message-dev \
  --region $REGION \
  --invocation-type RequestResponse \
  --payload file:///tmp/e2e_event.json \
  --cli-binary-format raw-in-base64-out \
  /tmp/e2e_response.json

echo "Lambda response:"
cat /tmp/e2e_response.json | python3 -m json.tool

echo ""
echo "=== Step 4: Wait for async execution and poll conversation ==="
echo "Waiting 45s for sim execution + VLM..."
sleep 45

echo ""
echo "=== Conversation messages: ==="
AWS_PROFILE=coybot aws dynamodb query \
  --table-name drone-conversations-dev \
  --region $REGION \
  --key-condition-expression 'PK = :pk AND begins_with(SK, :sk)' \
  --expression-attribute-values "{\":pk\": {\"S\": \"CONV#${DRONE_ID}#${CONV_ID}\"}, \":sk\": {\"S\": \"MSG#\"}}" \
  --query 'Items[*].{content:content.S, sender:sender.S, type:contentType.S, images:imageUrls.L[0].S}' \
  --output json | python3 -m json.tool

echo ""
echo "=== Done ==="
