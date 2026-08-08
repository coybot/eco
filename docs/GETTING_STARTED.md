# Presidio — Developer Setup

Welcome to the Presidio drone platform. This guide gets you from zero to building and deploying.

## Prerequisites

- macOS or Linux dev machine
- Git, Python 3.11+, Rust (for clearkeep)
- [uv](https://docs.presidio.sh/uv/) (Python package manager)
- AWS CLI v2
- GitHub CLI (`gh`)
- Xcode (if building the iOS app)

## 1. Clone the repos

All repos live under the [presidio-autonomy](https://github.com/presidio-autonomy) GitHub org. You should have received an invite — accept it first.

```bash
mkdir -p ~/code/presidio && cd ~/code/presidio

# Public
git clone https://github.com/presidio-autonomy/presidio-sdk.git sdk
git clone https://github.com/presidio-autonomy/presidio-docs.git docs

# Private — requires org membership
git clone https://github.com/presidio-autonomy/eco.git
git clone https://github.com/presidio-autonomy/clearkeep.git
```

### What's what

| Repo | Description |
|------|-------------|
| **eco** | Main platform: AWS backend (SAM), iOS app, drone daemon, website |
| **sdk** | Open-source Python SDK for ArduPilot drones (`pip install presidio-sdk`) |
| **docs** | Public docs site (Mintlify), rendered at presidioautonomy.com/docs |
| **clearkeep** | Encrypted messaging (Rust): QUIC + Noise + MLS, with iOS client support |

## 2. AWS access

You should have received an IAM invite for the `presidio` account (us-west-2). Configure your CLI:

```bash
aws configure --profile presidio
# Region: us-west-2
# Output: json
```

Verify access:

```bash
AWS_PROFILE=presidio aws sts get-caller-identity
```

The backend stack is called `drone-api`. To see its outputs (API endpoint, IoT endpoint, etc.):

```bash
AWS_PROFILE=presidio sam list stack-outputs --stack-name drone-api --region us-west-2
```

## 3. SDK quickstart

```bash
cd sdk
uv sync          # or: pip install -e ".[all]"
```

Copy `src/presidio_sdk/config_example.yaml` to `config.yaml` and edit `serial_port` for your flight controller. Run SITL examples without hardware:

```bash
python examples/sitl/fly_sitl.py
```

## 4. iOS app (eco)

The Xcode project is at `eco/client/ios/DroneOperator/DroneOperator.xcodeproj`.

- API endpoint, IoT endpoint, and Cognito IDs are already configured in `Config/AWSConfig.swift`
- Open in Xcode, select your development team under Signing & Capabilities, and build

## 5. Deploying the backend (eco)

If you need to redeploy the SAM stack:

```bash
cd eco/aws
AWS_PROFILE=presidio sam deploy \
  --region us-west-2 \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --stack-name drone-api
```

## 6. Drone onboarding (Orin Nano)

To provision a physical drone running an NVIDIA Orin Nano:

```bash
cd eco/drone/platforms/orin
./install.sh --remote user@<drone-host> --password <password> --start
```

This handles IoT certificate provisioning, model downloads (~6-8 GB), daemon startup, and WiFi hotspot creation for iOS pairing.

## 7. Clearkeep (encrypted messaging)

```bash
cd clearkeep
cargo build
```

For local dev with DynamoDB Local + MinIO:

```bash
cd infra
make up        # docker-compose
```

See `spec/` for protocol docs and `README.md` for architecture details.

## 8. Docs site

```bash
cd docs
npm install -g mintlify
mintlify dev   # http://localhost:3000
```

## Questions?

Ping in the team channel or open an issue on the relevant repo.
