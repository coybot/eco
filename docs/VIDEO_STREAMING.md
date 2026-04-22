# Live Video Streaming Setup

This document explains how to set up live video streaming from the drone's OAK-D Lite camera to the iOS app using **Amazon Kinesis Video Streams WebRTC**.

## Architecture

```
┌─────────────────┐     WebRTC      ┌─────────────────┐
│  Drone (Orin)   │ ◄────────────► │   iOS App       │
│  OAK-D Lite     │    Signaling   │   (Viewer)      │
│  (Master)       │    via KVS     │                 │
└────────┬────────┘                └────────┬────────┘
         │                                  │
         │ IoT Credentials                  │ Cognito Auth
         │                                  │
         ▼                                  ▼
┌─────────────────────────────────────────────────────┐
│              AWS Cloud                              │
│  ┌─────────────────┐  ┌──────────────────────────┐ │
│  │ IoT Core        │  │ Kinesis Video Streams    │ │
│  │ (Credentials)   │  │ - Signaling Channel      │ │
│  └─────────────────┘  │ - STUN/TURN Servers      │ │
│                       └──────────────────────────┘ │
│  ┌─────────────────┐  ┌──────────────────────────┐ │
│  │ API Gateway     │  │ Lambda Functions         │ │
│  │ /video/signaling│  │ - Create/Get Channels    │ │
│  │ /video/viewer   │  │ - Get ICE Servers        │ │
│  └─────────────────┘  └──────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

## Prerequisites

### AWS Infrastructure

1. Deploy the SAM template:
   ```bash
   cd aws
   sam build
   sam deploy
   ```

2. Note the outputs:
   - `DroneVideoRoleAlias` - IoT role alias for drone video streaming
   - `ApiEndpoint` - API Gateway URL

### Drone Setup

1. **Install dependencies on Orin:**
   ```bash
   # SSH into drone
   ssh orin-admin@<drone-ip>
   
   # Install Python dependencies
   pip install boto3 pyyaml requests
   
   # For WebRTC (if using aiortc):
   pip install aiortc aiohttp av
   
   # For GStreamer (recommended for production):
   sudo apt-get install -y gstreamer1.0-plugins-bad gstreamer1.0-plugins-good \
       gstreamer1.0-tools gstreamer1.0-libav
   ```

2. **Update drone config** (`drone/common/config.yaml`):
   ```yaml
   drone_id: "your-drone-id"
   
   # Video streaming
   video_role_alias: "drone-video-role-alias-dev"
  credentials_endpoint: "xxxx.credentials.iot.us-west-2.amazonaws.com"
   
   # Get credentials endpoint with:
   # aws iot describe-endpoint --endpoint-type iot:CredentialProvider
   ```

3. **Verify IoT certificates exist:**
   ```bash
   ls -la drone/common/certs/
   # Should have: device.pem, private.key, root-ca.pem
   ```

### iOS App Setup

1. **Add WebRTC dependency:**
   
   In Xcode, go to **File > Add Package Dependencies** and add:
   ```
   https://github.com/AWSChimeSDK/WebRTC-IOS-SDK
   ```
   
   Or use CocoaPods:
   ```ruby
   pod 'AmazonChimeSDK-iOS-SDK'
   ```

2. **Add camera/microphone permissions** to `Info.plist`:
   ```xml
   <key>NSCameraUsageDescription</key>
   <string>Camera access for video calls</string>
   <key>NSMicrophoneUsageDescription</key>
   <string>Microphone access for audio</string>
   ```

## Usage

### Starting the Stream

1. **On the iOS app:**
   - Navigate to a drone's detail page
   - Go to the "General" tab
   - Tap the **Play** button on the "Live Camera" card
   - The app will:
     - Call `GET /drones/{droneId}/video/viewer` to get signaling channel info
     - Connect to Kinesis Video Signaling as a viewer
     - Display the video when the drone connects

2. **On the drone:**
   - Start the video streaming service:
     ```bash
     cd drone
     python -m common.video_stream
     ```
   - Or run as a systemd service (see below)

### Stopping the Stream

- **iOS:** Tap the **Stop** button
- **Drone:** Press `Ctrl+C` or send `SIGTERM`

## Running as a Service (Production)

Create `/etc/systemd/system/drone-video.service`:

```ini
[Unit]
Description=Drone Video Streaming Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=orin-admin
WorkingDirectory=/home/orin-admin/eco/drone
ExecStart=/usr/bin/python3 -m common.video_stream
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable drone-video
sudo systemctl start drone-video
```

## API Endpoints

### GET /drones/{droneId}/video/signaling
Returns signaling channel info for the drone (master role).

**Response:**
```json
{
  "success": true,
  "role": "MASTER",
  "channelName": "drone-xyz-dev",
  "channelARN": "arn:aws:kinesisvideo:...",
  "region": "us-west-2",
  "endpoints": {
    "WSS": "wss://...",
    "HTTPS": "https://..."
  }
}
```

### GET /drones/{droneId}/video/viewer
Returns viewer credentials with ICE servers for the iOS app.

**Response:**
```json
{
  "success": true,
  "role": "VIEWER",
  "channelName": "drone-xyz-dev",
  "channelARN": "arn:aws:kinesisvideo:...",
  "region": "us-west-2",
  "endpoints": {
    "WSS": "wss://...",
    "HTTPS": "https://..."
  },
  "iceServers": [
    {
      "urls": ["turn:..."],
      "username": "...",
      "credential": "...",
      "ttl": 300
    }
  ]
}
```

## Troubleshooting

### "Signaling channel not found"
- Ensure the drone is online and streaming
- Check that `video_role_alias` is configured correctly
- Verify IoT credentials are valid

### "Failed to get credentials"
- Check that `credentials_endpoint` is set in config
- Verify IoT certificates exist and are valid
- Ensure the drone's IoT policy includes `iot:AssumeRoleWithCertificate`

### No video appearing on iOS
- Check drone logs for camera errors
- Verify OAK-D Lite is connected (`depthai-viewer`)
- Check ICE connection state in Xcode console

### High latency
- This is a WebRTC P2P connection, so latency should be <500ms
- If latency is high, check if TURN relay is being used (indicates NAT issues)
- Ensure both drone and iOS have good network connectivity

## Cost Estimation

Kinesis Video Streams WebRTC pricing (us-west-2):
- Signaling: $0.03 per million signaling messages
- TURN relay: $0.12 per GB relayed
- No cost for direct P2P connections

Typical usage (1 hour streaming):
- ~$0.01 signaling + $0 if P2P works
- ~$0.50 if TURN relay is needed (poor network)

## Alternative: GStreamer KVS Producer

For production, you may want to use the official GStreamer kvssink plugin:

```bash
# Install KVS producer SDK
# See: https://github.com/awslabs/amazon-kinesis-video-streams-producer-sdk-cpp

# GStreamer pipeline example
gst-launch-1.0 v4l2src ! videoconvert ! x264enc ! kvssink stream-name=drone-xyz
```

This is more reliable for continuous streaming but requires C++ SDK compilation.

