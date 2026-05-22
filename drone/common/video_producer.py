#!/usr/bin/env python3
"""
Kinesis Video Streams WebRTC Producer for OAK-D Lite camera.
Streams live video to iOS app viewers via WebRTC signaling.
"""

import asyncio
import json
import os
import yaml
import hashlib
import hmac
import requests
import boto3
import ssl
import cv2
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, quote
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, RTCConfiguration, RTCIceServer
from aiortc.sdp import candidate_from_sdp
from av import VideoFrame
import base64

# Configuration
SCRIPT_DIR = Path(__file__).parent.absolute()
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
CERT_DIR = SCRIPT_DIR / "certs"
DEFAULT_REGION = "us-west-2"

# Global state
_streaming = False


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}

def get_region(config: dict) -> str:
    """Resolve AWS region for KVS/WebRTC resources."""
    return (
        config.get("region")
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or DEFAULT_REGION
    )


def get_iot_credentials(config):
    """Get AWS credentials via IoT credential provider."""
    drone_id = config.get("drone_id")
    # iot_thing_name is the IoT Thing the device certs are provisioned for.
    # It may differ from drone_id (which is used for channel/topic naming).
    thing_name = config.get("iot_thing_name", drone_id)
    role_alias = config.get("video_role_alias", "drone-video-role-alias-dev")
    credentials_endpoint = config.get("credentials_endpoint")
    
    cert_path = CERT_DIR / "device.pem"
    key_path = CERT_DIR / "private.key"
    ca_path = CERT_DIR / "root-ca.pem"
    
    url = f"https://{credentials_endpoint}/role-aliases/{role_alias}/credentials"
    response = requests.get(
        url,
        cert=(str(cert_path), str(key_path)),
        verify=str(ca_path),
        headers={"x-amzn-iot-thingname": thing_name}
    )
    
    if response.status_code != 200:
        raise Exception(f"Failed to get credentials: {response.text}")
    
    creds = response.json()["credentials"]
    return {
        "access_key": creds["accessKeyId"],
        "secret_key": creds["secretAccessKey"],
        "session_token": creds["sessionToken"],
    }


def get_signaling_info(config, credentials):
    """Get signaling channel info from Kinesis Video."""
    drone_id = config.get("drone_id")
    environment = config.get("environment", "dev")
    channel_name = f"drone-{drone_id}-{environment}"
    region = get_region(config)
    
    kvs = boto3.client(
        "kinesisvideo",
        region_name=region,
        aws_access_key_id=credentials["access_key"],
        aws_secret_access_key=credentials["secret_key"],
        aws_session_token=credentials["session_token"]
    )
    
    # Describe the channel (created by the cloud API before this runs)
    response = kvs.describe_signaling_channel(ChannelName=channel_name)
    channel_arn = response["ChannelInfo"]["ChannelARN"]
    print(f"  Channel: {channel_name}")
    
    # Get endpoints
    endpoints = kvs.get_signaling_channel_endpoint(
        ChannelARN=channel_arn,
        SingleMasterChannelEndpointConfiguration={
            "Protocols": ["WSS", "HTTPS"],
            "Role": "MASTER"
        }
    )
    
    eps = {ep["Protocol"]: ep["ResourceEndpoint"] for ep in endpoints["ResourceEndpointList"]}
    
    # Get ICE servers
    signaling_client = boto3.client(
        "kinesis-video-signaling",
        region_name=region,
        endpoint_url=eps["HTTPS"],
        aws_access_key_id=credentials["access_key"],
        aws_secret_access_key=credentials["secret_key"],
        aws_session_token=credentials["session_token"]
    )
    
    ice_response = signaling_client.get_ice_server_config(
        ChannelARN=channel_arn,
        ClientId="drone-master",
        Service="TURN"
    )
    
    ice_servers = []
    for cfg in ice_response["IceServerList"]:
        ice_servers.append(RTCIceServer(
            urls=cfg["Uris"],
            username=cfg.get("Username"),
            credential=cfg.get("Password")
        ))
    
    return {
        "channel_arn": channel_arn,
        "channel_name": channel_name,
        "wss_endpoint": eps["WSS"],
        "https_endpoint": eps["HTTPS"],
        "ice_servers": ice_servers,
        "region": region,
    }


def create_presigned_url(wss_endpoint, channel_arn, credentials, region):
    """Create a presigned URL for Kinesis Video Signaling WebSocket."""
    parsed = urlparse(wss_endpoint)
    host = parsed.netloc
    
    t = datetime.now(timezone.utc)
    amz_date = t.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = t.strftime("%Y%m%d")
    
    service = "kinesisvideo"
    method = "GET"
    canonical_uri = "/"
    
    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    
    query_params = {
        "X-Amz-Algorithm": algorithm,
        "X-Amz-ChannelARN": channel_arn,
        "X-Amz-Credential": f"{credentials['access_key']}/{credential_scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": "299",
        "X-Amz-Security-Token": credentials["session_token"],
        "X-Amz-SignedHeaders": "host",
    }
    
    canonical_querystring = "&".join([
        f"{quote(k, safe='')}={quote(v, safe='~')}" 
        for k, v in sorted(query_params.items())
    ])
    
    canonical_headers = f"host:{host}\n"
    signed_headers = "host"
    payload_hash = hashlib.sha256(b"").hexdigest()
    
    canonical_request = f"{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    
    string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    
    def sign(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()
    
    k_date = sign(f"AWS4{credentials['secret_key']}".encode(), date_stamp)
    k_region = sign(k_date, region)
    k_service = sign(k_region, service)
    k_signing = sign(k_service, "aws4_request")
    
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    query_params["X-Amz-Signature"] = signature
    
    final_querystring = "&".join([
        f"{quote(k, safe='')}={quote(v, safe='~')}" 
        for k, v in sorted(query_params.items())
    ])
    
    return f"{wss_endpoint}?{final_querystring}"


class CameraStreamTrack(VideoStreamTrack):
    """Video track that captures from auto-detected camera (RealSense, OAK-D, etc.)."""

    kind = "video"

    def __init__(self, camera=None, v4l2_cap=None):
        super().__init__()
        self._camera = camera      # Auto-detected camera instance (pyrealsense2/depthai)
        self._v4l2_cap = v4l2_cap  # OpenCV VideoCapture fallback (V4L2 loopback)
        self._frame_count = 0
        self._last_frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        if self._camera:
            try:
                frame_data = self._camera.get_frame(timeout_ms=100)
                if frame_data and frame_data.rgb is not None:
                    bgr_frame = cv2.cvtColor(frame_data.rgb, cv2.COLOR_RGB2BGR)
                    if bgr_frame.shape[:2] != (720, 1280):
                        bgr_frame = cv2.resize(bgr_frame, (1280, 720))
                    self._last_frame = bgr_frame
            except Exception as e:
                if self._frame_count % 30 == 0:
                    print(f"    Frame error: {e}")
        elif self._v4l2_cap:
            try:
                ret, bgr_frame = self._v4l2_cap.read()
                if ret and bgr_frame is not None:
                    if bgr_frame.shape[:2] != (720, 1280):
                        bgr_frame = cv2.resize(bgr_frame, (1280, 720))
                    self._last_frame = bgr_frame
            except Exception as e:
                if self._frame_count % 30 == 0:
                    print(f"    V4L2 frame error: {e}")
        else:
            # No camera - show test pattern
            cv2.putText(self._last_frame, f"Frame {self._frame_count}", (50, 360),
                       cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 255, 0), 3)
        
        frame = VideoFrame.from_ndarray(self._last_frame, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        self._frame_count += 1
        
        if self._frame_count % 30 == 0:
            print(f"    Sent {self._frame_count} frames")
        
        return frame


async def run_signaling(signaling_info, credentials, ice_servers, region: str):
    """Run WebRTC signaling as master."""
    global _streaming
    
    wss_endpoint = signaling_info["wss_endpoint"]
    channel_arn = signaling_info["channel_arn"]
    
    signed_url = create_presigned_url(wss_endpoint, channel_arn, credentials, region)
    
    print("Connecting to signaling...")
    
    pcs = {}
    camera_tracks = {}  # Track per connection
    
    # Initialize camera using auto-detection (RealSense, OAK-D, etc.)
    camera = None
    v4l2_cap = None
    try:
        import sys
        sys.path.insert(0, str(SCRIPT_DIR))
        from camera import get_camera, list_available_cameras

        available = list_available_cameras()
        if available:
            print(f"  Available cameras: {[c['name'] for c in available]}")
            try:
                cam = get_camera(rgb_fps=30, enable_depth=False)
                if cam:
                    cam.start()
                    print("  Warming up camera...")
                    for _ in range(15):
                        cam.get_frame(timeout_ms=500)
                    print(f"  ✓ Camera ready ({cam.CAMERA_TYPE})")
                    camera = cam
                else:
                    print("  Warning: Failed to initialize camera via SDK")
            except Exception as e:
                print(f"  Warning: Camera SDK start failed: {e} — will try V4L2")
                camera = None  # ensure V4L2 fallback runs
        else:
            print("  Warning: No cameras detected via SDK")
    except Exception as e:
        print(f"  Warning: Camera SDK init failed: {e}")
        camera = None

    # If SDK camera unavailable, fall back to V4L2 direct capture
    # Priority: color streams first (video2/video5 on RealSense D435I), then loopbacks
    if camera is None:
        for dev_idx in [2, 5, 10, 11, 0, 1]:
            try:
                cap = cv2.VideoCapture(dev_idx)
                if not cap.isOpened():
                    continue
                # Flush stale frames
                for _ in range(5):
                    cap.read()
                ret, frame = cap.read()
                if ret and frame is not None and frame.mean() > 5:
                    v4l2_cap = cap
                    print(f"  ✓ Using V4L2 /dev/video{dev_idx} ({frame.shape[1]}x{frame.shape[0]} mean={frame.mean():.0f})")
                    break
                cap.release()
            except Exception as e:
                print(f"  video{dev_idx}: {e}")
        if v4l2_cap is None:
            print("  Warning: No usable V4L2 device found, using test pattern")
    
    try:
        async with websockets.connect(signed_url, ssl=ssl.create_default_context()) as ws:
            print("✓ Connected to signaling channel")
            print("Waiting for iOS app to connect...")
            _streaming = True
            
            while _streaming:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    
                    # Skip empty or non-JSON messages
                    if not msg or not msg.strip():
                        continue
                    
                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        print(f"  Skipping non-JSON message")
                        continue
                    
                    msg_type = data.get("messageType")
                    sender_id = data.get("senderClientId", "viewer")
                    payload_b64 = data.get("messagePayload", "")
                    
                    if msg_type == "SDP_OFFER":
                        print(f"\n  Viewer connected: {sender_id[:12]}...")
                        
                        sdp = base64.b64decode(payload_b64).decode()
                        
                        # Create RTCConfiguration with ICE servers
                        # Add Google STUN so ICE can find a direct path — KVS TURN
                        # sometimes gives 403 on CHANNEL_BIND for NATed hosts
                        all_ice = list(ice_servers) + [
                            RTCIceServer(urls="stun:stun.l.google.com:19302"),
                            RTCIceServer(urls="stun:stun1.l.google.com:19302"),
                        ]
                        config = RTCConfiguration(iceServers=all_ice)
                        pc = RTCPeerConnection(configuration=config)
                        pcs[sender_id] = pc
                        
                        @pc.on("connectionstatechange")
                        async def on_state():
                            state = pc.connectionState
                            print(f"  Connection: {state}")
                            if state == "connected":
                                print("  ✓ STREAMING VIDEO!")
                        
                        # Create fresh video track for this connection (using shared camera)
                        camera_track = CameraStreamTrack(camera=camera, v4l2_cap=v4l2_cap)
                        camera_tracks[sender_id] = camera_track
                        
                        # Add video track
                        pc.addTrack(camera_track)
                        
                        # Set remote offer
                        await pc.setRemoteDescription(
                            RTCSessionDescription(sdp=sdp, type="offer")
                        )
                        
                        # Create and send answer
                        answer = await pc.createAnswer()
                        await pc.setLocalDescription(answer)
                        
                        answer_b64 = base64.b64encode(answer.sdp.encode()).decode()
                        await ws.send(json.dumps({
                            "action": "SDP_ANSWER",
                            "recipientClientId": sender_id,
                            "messagePayload": answer_b64
                        }))
                        print(f"  Sent answer, waiting for ICE connection...")
                        
                    elif msg_type == "ICE_CANDIDATE":
                        if sender_id in pcs:
                            try:
                                candidate_json = base64.b64decode(payload_b64).decode()
                                candidate_data = json.loads(candidate_json)
                                candidate_str = candidate_data.get("candidate", "")
                                
                                if candidate_str and "candidate:" in candidate_str:
                                    candidate = candidate_from_sdp(candidate_str)
                                    candidate.sdpMid = candidate_data.get("sdpMid")
                                    candidate.sdpMLineIndex = candidate_data.get("sdpMLineIndex")
                                    await pcs[sender_id].addIceCandidate(candidate)
                                    print(f"  Added ICE candidate")
                            except Exception as e:
                                print(f"  ICE error: {e}")
                            
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed:
                    print("\nSignaling disconnected")
                    break
                except Exception as e:
                    print(f"  Error in loop: {e}")
                    continue
                    
    except Exception as e:
        print(f"Signaling error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        for pc in pcs.values():
            await pc.close()


async def main():
    global _streaming
    
    print("=" * 50)
    print("KINESIS VIDEO WEBRTC PRODUCER")
    print("OAK-D Lite Camera -> iOS App")
    print("=" * 50)
    
    config = load_config()
    print(f"Drone ID: {config.get('drone_id')}")
    region = get_region(config)
    print(f"AWS Region: {region}")
    
    print("\n1. Getting AWS credentials...")
    credentials = get_iot_credentials(config)
    print("   ✓ Got credentials")
    
    print("\n2. Getting signaling channel info...")
    signaling_info = get_signaling_info(config, credentials)
    print(f"   ✓ Channel: {signaling_info['channel_name']}")
    print(f"   ✓ ICE servers: {len(signaling_info['ice_servers'])}")
    
    print("\n3. Starting WebRTC producer...")
    try:
        await run_signaling(signaling_info, credentials, signaling_info["ice_servers"], region)
    except KeyboardInterrupt:
        pass
    finally:
        _streaming = False
    
    print("\nProducer stopped.")


if __name__ == "__main__":
    asyncio.run(main())
