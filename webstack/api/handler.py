import json
import os

import boto3

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    route = event.get("rawPath", "/")
    if route == "/health":
        return _resp(200, {"ok": True})
    return _resp(404, {"error": "not found", "path": route})
