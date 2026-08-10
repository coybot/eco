"""
Storage / pub-sub client seam, shared by conversations.py, handler.py, drones.py,
and groups.py.

Cloud (Lambda) default: get_dynamodb()/get_iot()/get_s3() return real, lazily
constructed boto3 clients/resources - identical to what those modules used to
build directly at import time. Nothing changes for the deployed stack.

GCS (local ground control station) mode: gcs/server.py calls configure() with
local stand-ins (SQLite-backed DynamoDB, a paho-mqtt-backed IoT publisher, a
filesystem-backed S3) *before* importing conversations/handler/drones/groups -
those modules capture `dynamodb = clients.get_dynamodb()` etc. at their own
import time, so import order matters. Lambda never calls configure(); it always
gets the real AWS clients.

Not covered by this seam (cloud-only, untouched): drones.py's control-plane
`iot` client (`boto3.client('iot')`, used for Cognito IoT-policy attach/detach)
and video.py's Kinesis Video Streams client - neither has a GCS equivalent.
"""
import os

import boto3

_dynamodb = None
_iot = None
_s3 = None
_image_url_fn = None


def configure(dynamodb=None, iot=None, s3=None, image_url_fn=None):
    """Inject local stand-ins. Call this before importing any aws/src handler
    module. Passing None for an argument leaves that client's default (lazy
    real-AWS) behavior in place."""
    global _dynamodb, _iot, _s3, _image_url_fn
    if dynamodb is not None:
        _dynamodb = dynamodb
    if iot is not None:
        _iot = iot
    if s3 is not None:
        _s3 = s3
    if image_url_fn is not None:
        _image_url_fn = image_url_fn


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"


def get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb


def get_iot():
    """The iot-data client used to publish() to drones/apps - not the
    control-plane `iot` client (policy attach/detach), which is cloud-only."""
    global _iot
    if _iot is None:
        _iot = boto3.client("iot-data", region_name=_region())
    return _iot


def get_s3():
    global _s3
    if _s3 is None:
        from botocore.config import Config as BotoConfig
        _s3 = boto3.client("s3", region_name=_region(), config=BotoConfig(signature_version="s3v4"))
    return _s3


def public_image_url(key: str) -> str:
    """Public (viewing) URL for an S3 object key. GCS mode overrides this via
    configure(image_url_fn=...) to point at its own local image server."""
    if _image_url_fn is not None:
        return _image_url_fn(key)
    bucket = os.environ.get("IMAGES_BUCKET", "drone-images-dev")
    return f"https://{bucket}.s3.amazonaws.com/{key}"
