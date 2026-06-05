"""Shared AWS credential + S3 helpers for the eco sim host.

A sim drone authenticates to AWS the same way an IRL drone does: it presents its
IoT device certificate to the IoT credential provider and receives temporary AWS
credentials scoped by a role alias (video or S3). No static keys.

Mirrors eco/drone/common/drone_sdk.py:_get_iot_credentials / upload_photo and
video_producer.py:get_iot_credentials.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import requests


def iot_credentials(certs_dir: str, credentials_endpoint: str, role_alias: str,
                    thing_name: str) -> dict:
    """Mint temporary AWS creds via the IoT credential provider (mTLS, cert auth)."""
    certs = Path(certs_dir)
    url = f"https://{credentials_endpoint}/role-aliases/{role_alias}/credentials"
    resp = requests.get(
        url,
        cert=(str(certs / "device.pem"), str(certs / "private.key")),
        verify=str(certs / "root-ca.pem"),
        headers={"x-amzn-iot-thingname": thing_name},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"IoT credential provider failed ({resp.status_code}): {resp.text}")
    c = resp.json()["credentials"]
    return {"access_key": c["accessKeyId"], "secret_key": c["secretAccessKey"],
            "session_token": c["sessionToken"]}


def upload_jpeg_to_s3(creds: dict, bucket: str, region: str, drone_id: str,
                      conversation_id: str, jpeg_bytes: bytes) -> str:
    """Upload a JPEG to the images bucket and return its public URL.

    Key layout matches the IRL drone: drones/{id}/conversations/{cid}/{ts}.jpg
    """
    import boto3
    s3 = boto3.client(
        "s3", region_name=region,
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
        aws_session_token=creds["session_token"],
    )
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    key = f"drones/{drone_id}/conversations/{conversation_id}/{ts}.jpg"
    s3.put_object(Bucket=bucket, Key=key, Body=jpeg_bytes, ContentType="image/jpeg")
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def upload_mp4_to_s3(creds: dict, bucket: str, region: str, drone_id: str,
                     conversation_id: str, mp4_bytes: bytes,
                     label: str = "clip") -> str:
    """Upload an MP4 to the images bucket and return its public URL.

    Same key layout as photos so the app's media handling is uniform:
    drones/{id}/conversations/{cid}/{ts}_{label}.mp4
    """
    import boto3
    s3 = boto3.client(
        "s3", region_name=region,
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
        aws_session_token=creds["session_token"],
    )
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    key = f"drones/{drone_id}/conversations/{conversation_id}/{ts}_{label}.mp4"
    s3.put_object(Bucket=bucket, Key=key, Body=mp4_bytes, ContentType="video/mp4")
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
