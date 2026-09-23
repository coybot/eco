"""Real-AWS backend: boto3 DynamoDB, IoT Core (iot-data), and S3.

Lazily builds each client on first use (identical to what handler.py/
conversations.py/drones.py/groups.py used to build directly at import time,
before clients.py's seam existed), and caches it. This is the default backend
when nothing else is selected via control.clients.select_backend() - matching
the deployed Lambda stack's zero-configuration behavior.
"""
from __future__ import annotations

import os

import boto3


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"


class AwsBackend:
    def __init__(self):
        self._dynamodb = None
        self._iot = None
        self._s3 = None

    @property
    def dynamodb(self):
        if self._dynamodb is None:
            self._dynamodb = boto3.resource("dynamodb")
        return self._dynamodb

    @property
    def iot(self):
        """The iot-data client used to publish() to drones/apps - not the
        control-plane `iot` client (policy attach/detach), which has no local
        equivalent and stays a direct boto3 call in drones.py."""
        if self._iot is None:
            self._iot = boto3.client("iot-data", region_name=_region())
        return self._iot

    @property
    def s3(self):
        if self._s3 is None:
            from botocore.config import Config as BotoConfig
            self._s3 = boto3.client("s3", region_name=_region(),
                                     config=BotoConfig(signature_version="s3v4"))
        return self._s3

    def image_url_fn(self, key: str) -> str:
        bucket = os.environ.get("IMAGES_BUCKET", "drone-images-dev")
        return f"https://{bucket}.s3.amazonaws.com/{key}"
