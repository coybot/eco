# Control plane: AWS vs GCS

The drone daemon, the iOS/Android app, and the `aws/src/` package all read
one config key — `control_plane: "aws"` (default) or `control_plane: "gcs"`
— and agree on which backend to use. Same MQTT topics and REST routes either
way; only the transport, auth, and where the LLM lives differ.

## Architecture: one control plane, two backends

`aws/src/clients.py` is a backend-selection seam. `aws/src/handler.py`,
`conversations.py`, `drones.py`, `groups.py`, and `rover.py` are vendor-neutral
— they call `clients.get_dynamodb()` / `get_iot()` / `get_s3()` and never talk
to AWS or a local store directly. Two backends implement those:

- **`aws/src/backends/aws.py`** — real, lazily-constructed boto3 clients:
  DynamoDB, IoT Core (`iot-data`), S3. This is the default; nothing has to be
  configured for it.
- **`aws/src/backends/local.py`** — `LocalDynamo` (SQLite), `LocalIoTData`
  (forwards to a local mosquitto broker), `LocalS3` (writes to a folder,
  serves images back over the GCS's own HTTP API). `gcs/server.py` builds
  these and calls `clients.select_backend(LocalBackend(...))`.

Backend selection is **not import-order-sensitive**: `get_dynamodb()` etc.
return stable proxies whose concrete backend resolves lazily, at first real
attribute access — so `select_backend()` can run before or after
`control.handler`/etc. are imported, as long as it happens before the first
real request.

## GCS mode: what runs where

```
iOS/Android app --HTTP--> gcs/http_api.py --calls unmodified--> aws/src/{handler,rover,drones,conversations,groups}.py
                                                                        |
                                                            aws/src/llm.py (LLM_PROVIDER=openai)
                                                                        |
                                                          your local Ollama / vLLM server
                                                                        |
                                                        aws/src/clients.py (LocalDynamo/LocalIoTData/LocalS3)
                                                                        |
                                                              aws/src/backends/local.py + mosquitto
                                                                        |
                drone/common/daemon.py (control_plane: gcs) <--MQTT-- mosquitto --MQTT--> app (WebSocket)
```

No AWS account, no certificates, no Coybot-hosted infrastructure. See
[`gcs/README.md`](../gcs/README.md) for the full picture and a working
docker-compose setup.

## What degrades in GCS mode

Storage and messaging have local equivalents; a few cloud-only features don't:

| Feature | AWS mode | GCS mode |
|---|---|---|
| Photo/image storage | S3 | Folder on the GCS box, served over its own HTTP API |
| Drone state, conversations | DynamoDB | SQLite |
| Pub/sub | AWS IoT Core | mosquitto |
| Device Shadow | Live shadow, subscribe-delivered | Store-only — a REST read sees the last-desired state, but there's no live push to the drone |
| `/auth/iot-policy` | Serves Cognito-IoT policy attach/detach | Not served — no local equivalent, no GCS route for it |
| Video streaming | Kinesis Video Streams WebRTC | **Unavailable.** `video.py` is Kinesis-only with no local equivalent; it stays a private, cloud-only module |
| Auth | Cognito (Google/Apple Sign-In) | A per-operator pairing token, printed to the GCS console on first run |

## Choosing a backend for storage explicitly

The backend choice is driven entirely by `control_plane` in `config.yaml` —
there's no separate storage-only switch. Choose `gcs` and images go to a
local folder; choose `aws` and they go to your S3 bucket. See
[`config-reference.md`](config-reference.md) for every key involved and
[`quickstart-gcs.md`](quickstart-gcs.md) / [`quickstart-aws.md`](quickstart-aws.md)
for the two setup paths end to end.
