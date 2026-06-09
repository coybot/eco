"""KVS WebRTC master for a simulated vehicle.

Adapted from ``eco/drone/common/video_producer.py``: same signaling/SDP/ICE
machinery and the same channel-naming contract (``drone-{drone_id}-{env}``,
matching ``eco/aws/src/video.py:get_channel_name``), but the video source is a
``FrameBus`` fed by the Isaac Sim worker instead of a physical camera, and AWS
credentials come from the standard boto3 chain on the sim host (env/profile)
rather than the IoT credential provider (sim drones have no device cert).

Runs in its own thread; the app connects as viewer via the existing
``GET /drones/{id}/video/viewer`` endpoint, so no app/cloud change is needed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import ssl
from datetime import datetime, timezone
from urllib.parse import urlparse, quote

from pathlib import Path

import boto3
import cv2
import numpy as np
import requests
import websockets
from aiortc import (RTCPeerConnection, RTCSessionDescription, VideoStreamTrack,
                    RTCConfiguration, RTCIceServer)
from aiortc.sdp import candidate_from_sdp
from av import VideoFrame

DEFAULT_REGION = "us-west-2"

# Credential source (set once by run_video_producer). A sim drone authenticates
# to KVS exactly like an IRL drone: present its IoT device cert to the IoT
# credential provider and receive temporary AWS creds — no static keys, no
# inbound tunnel (the master connects outbound to the signaling WebSocket).
_CRED_CONF: dict | None = None


def _iot_credentials(conf: dict) -> dict:
    """Mint temporary AWS creds via the IoT credential provider (cert auth).

    Mirrors eco/drone/common/video_producer.py:get_iot_credentials. conf keys:
    certs_dir, credentials_endpoint, video_role_alias, iot_thing_name.
    """
    certs = Path(conf["certs_dir"])
    url = (f"https://{conf['credentials_endpoint']}"
           f"/role-aliases/{conf['video_role_alias']}/credentials")
    resp = requests.get(
        url,
        cert=(str(certs / "device.pem"), str(certs / "private.key")),
        verify=str(certs / "root-ca.pem"),
        headers={"x-amzn-iot-thingname": conf["iot_thing_name"]},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"IoT credential provider failed: {resp.text}")
    c = resp.json()["credentials"]
    return {"access_key": c["accessKeyId"], "secret_key": c["secretAccessKey"],
            "session_token": c["sessionToken"]}


def _resolve_credentials(region: str) -> dict:
    """Cert-based IoT creds when configured, else the boto3 default chain."""
    if _CRED_CONF and _CRED_CONF.get("certs_dir"):
        return _iot_credentials(_CRED_CONF)
    creds = boto3.Session(region_name=region).get_credentials()
    if creds is None:
        raise RuntimeError("No AWS credentials: provide an IoT cert (certs_dir) "
                           "or set AWS_PROFILE/env vars")
    f = creds.get_frozen_credentials()
    return {"access_key": f.access_key, "secret_key": f.secret_key,
            "session_token": f.token}


def ensure_channel_and_signaling(channel_name: str, credentials: dict, region: str) -> dict:
    """Resolve master endpoints/ICE for the channel.

    Like IRL drones, the master only *describes* the channel — the channel is
    created by the cloud (eco/aws/src/video.py) when the app requests a viewer.
    If it doesn't exist yet, this raises and the caller retries until the app
    connects and the cloud creates it.
    """
    kvs = boto3.client(
        "kinesisvideo", region_name=region,
        aws_access_key_id=credentials["access_key"],
        aws_secret_access_key=credentials["secret_key"],
        aws_session_token=credentials["session_token"],
    )
    resp = kvs.describe_signaling_channel(ChannelName=channel_name)
    channel_arn = resp["ChannelInfo"]["ChannelARN"]
    print(f"  Channel: {channel_name}", flush=True)

    endpoints = kvs.get_signaling_channel_endpoint(
        ChannelARN=channel_arn,
        SingleMasterChannelEndpointConfiguration={"Protocols": ["WSS", "HTTPS"],
                                                  "Role": "MASTER"},
    )
    eps = {ep["Protocol"]: ep["ResourceEndpoint"] for ep in endpoints["ResourceEndpointList"]}

    signaling = boto3.client(
        "kinesis-video-signaling", region_name=region, endpoint_url=eps["HTTPS"],
        aws_access_key_id=credentials["access_key"],
        aws_secret_access_key=credentials["secret_key"],
        aws_session_token=credentials["session_token"],
    )
    ice_resp = signaling.get_ice_server_config(
        ChannelARN=channel_arn, ClientId="drone-master", Service="TURN")
    ice_servers = [RTCIceServer(urls=c["Uris"], username=c.get("Username"),
                                credential=c.get("Password"))
                   for c in ice_resp["IceServerList"]]
    return {"channel_arn": channel_arn, "wss_endpoint": eps["WSS"],
            "ice_servers": ice_servers}


def create_presigned_url(wss_endpoint: str, channel_arn: str, credentials: dict,
                         region: str) -> str:
    host = urlparse(wss_endpoint).netloc
    t = datetime.now(timezone.utc)
    amz_date = t.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = t.strftime("%Y%m%d")
    service = "kinesisvideo"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    q = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-ChannelARN": channel_arn,
        "X-Amz-Credential": f"{credentials['access_key']}/{credential_scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": "299",
        "X-Amz-SignedHeaders": "host",
    }
    if credentials.get("session_token"):
        q["X-Amz-Security-Token"] = credentials["session_token"]

    def canon(params):
        return "&".join(f"{quote(k, safe='')}={quote(v, safe='~')}"
                        for k, v in sorted(params.items()))

    canonical_request = (f"GET\n/\n{canon(q)}\nhost:{host}\n\nhost\n"
                         f"{hashlib.sha256(b'').hexdigest()}")
    string_to_sign = (f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n"
                      f"{hashlib.sha256(canonical_request.encode()).hexdigest()}")

    def sign(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = sign(f"AWS4{credentials['secret_key']}".encode(), date_stamp)
    k_signing = sign(sign(sign(k_date, region), service), "aws4_request")
    q["X-Amz-Signature"] = hmac.new(k_signing, string_to_sign.encode(),
                                    hashlib.sha256).hexdigest()
    return f"{wss_endpoint}?{canon(q)}"


class SimFrameTrack(VideoStreamTrack):
    """Video track sourced from the sim worker's FrameBus (RGB ndarray)."""

    kind = "video"

    def __init__(self, frame_bus):
        super().__init__()
        self._bus = frame_bus
        self._last = np.zeros((720, 1280, 3), dtype=np.uint8)

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        rgb = self._bus.get()
        if rgb is not None:
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            if bgr.shape[:2] != (720, 1280):
                bgr = cv2.resize(bgr, (1280, 720))
            self._last = bgr
        frame = VideoFrame.from_ndarray(self._last, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        return frame


async def _run_signaling(channel_name: str, frame_bus, region: str):
    credentials = _resolve_credentials(region)
    info = ensure_channel_and_signaling(channel_name, credentials, region)
    signed_url = create_presigned_url(info["wss_endpoint"], info["channel_arn"],
                                      credentials, region)
    ice_servers = info["ice_servers"]
    pcs: dict = {}
    print("Connecting to signaling (master)…", flush=True)

    async with websockets.connect(signed_url, ssl=ssl.create_default_context()) as ws:
        print("✓ Connected; waiting for viewer…", flush=True)
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except websockets.ConnectionClosed:
                print("Signaling disconnected", flush=True)
                break
            if not msg or not msg.strip():
                continue
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue
            mtype = data.get("messageType")
            sender = data.get("senderClientId", "viewer")
            payload_b64 = data.get("messagePayload", "")

            if mtype == "SDP_OFFER":
                print(f"  Viewer {sender[:12]} connected", flush=True)
                for old in list(pcs.values()):
                    try:
                        await old.close()
                    except Exception:
                        pass
                pcs.clear()
                all_ice = list(ice_servers) + [
                    RTCIceServer(urls="stun:stun.l.google.com:19302"),
                    RTCIceServer(urls="stun:stun1.l.google.com:19302"),
                ]
                pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=all_ice))
                pcs[sender] = pc

                @pc.on("connectionstatechange")
                async def _on_state():
                    print(f"  Connection: {pc.connectionState}", flush=True)

                pc.addTrack(SimFrameTrack(frame_bus))
                sdp = base64.b64decode(payload_b64).decode()
                await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                await ws.send(json.dumps({
                    "action": "SDP_ANSWER", "recipientClientId": sender,
                    "messagePayload": base64.b64encode(answer.sdp.encode()).decode(),
                }))
            elif mtype == "ICE_CANDIDATE" and sender in pcs:
                try:
                    cj = json.loads(base64.b64decode(payload_b64).decode())
                    cstr = cj.get("candidate", "")
                    if cstr and "candidate:" in cstr:
                        cand = candidate_from_sdp(cstr)
                        cand.sdpMid = cj.get("sdpMid")
                        cand.sdpMLineIndex = cj.get("sdpMLineIndex")
                        await pcs[sender].addIceCandidate(cand)
                except Exception as e:
                    print(f"  ICE error: {e}", flush=True)


def run_video_producer(channel_name: str, frame_bus, region: str = DEFAULT_REGION,
                       cred_conf: dict | None = None):
    """Blocking entry point — call from a dedicated thread. Auto-reconnects.

    cred_conf (optional): {certs_dir, credentials_endpoint, video_role_alias,
    iot_thing_name} to authenticate via the IoT credential provider like an IRL
    drone. If omitted, falls back to the boto3 default credential chain.
    """
    global _CRED_CONF
    _CRED_CONF = cred_conf
    while True:
        try:
            asyncio.run(_run_signaling(channel_name, frame_bus, region))
        except Exception as e:
            print(f"[video] signaling error, retrying in 5s: {e}", flush=True)
        import time
        time.sleep(5)
