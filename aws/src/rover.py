import json
import os
import boto3

AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
ANTHROPIC_VERSION = "bedrock-2023-05-31"
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

RAMP_AGENT_SYSTEM_PROMPT = """You are the voice of an autonomous baggage-handling robot working \
the ramp at Hawthorne Airport, moving luggage to and from aircraft. You are only reached for \
things the robot's on-device assistant could NOT handle itself: small talk, general knowledge \
questions, or ambiguous requests — anything about moving the robot (navigation, stop) is handled \
on-device and never reaches you.

Keep replies short (1-2 sentences), calm, and natural to say out loud through a speaker on an \
active ramp. You have no real-time lookup and no access to flight, gate, or baggage-routing \
systems — if asked for those, say you'll get a human ramp agent to help. You are never authorized \
to make safety, movement-area, or aircraft-proximity decisions — if a request sounds like it \
concerns ramp safety, say so and defer to a human immediately. Never generate code."""


def json_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
        },
        "body": json.dumps(body),
    }


def get_user_id(event):
    """Extract user ID from Lambda authorizer context (same pattern as handler.py)."""
    try:
        authorizer = event["requestContext"]["authorizer"]
        return authorizer.get("userId") or authorizer.get("principalId")
    except (KeyError, TypeError):
        return None


def converse_handler(event, context):
    """POST /rover/converse — open-ended conversational fallback for RoverOperator's
    DialogAgent. Only reached when the on-device Apple Foundation Model flags an
    utterance as needing escalation (small talk, general knowledge); navigation intents
    are parsed and dispatched entirely on-device and never hit this endpoint. See
    eco/client/ios/RoverOperator/RoverOperator/Cloud/ClaudeDialogClient.swift and
    eco/rover/docs/architecture.md.
    """
    if not get_user_id(event):
        return json_response(401, {"error": "Unauthorized"})

    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return json_response(400, {"error": "Invalid JSON"})

    utterance = (body.get("utterance") or "").strip()
    if not utterance:
        return json_response(400, {"error": "Missing utterance"})

    try:
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                "anthropic_version": ANTHROPIC_VERSION,
                "system": RAMP_AGENT_SYSTEM_PROMPT,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": utterance}]}
                ],
                "max_tokens": 200,
            }),
        )
        result = json.loads(response["body"].read())
        reply = "".join(
            block.get("text", "") for block in result.get("content", [])
            if block.get("type") == "text"
        ).strip()
    except Exception as e:
        return json_response(500, {"error": f"Bedrock error: {str(e)}"})

    return json_response(200, {"reply": reply or "Sorry, I didn't catch that."})
