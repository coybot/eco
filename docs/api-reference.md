# API reference (`control/` REST endpoints)

Every route below is implemented once, in `control/{handler,rover,drones,
conversations,groups}.py`, and served two ways:

- **AWS mode:** through API Gateway + these Lambda functions, per
  `aws/template.yaml` (private — see [`control-plane.md`](control-plane.md)).
  Auth: `Authorization: Bearer <Cognito idToken>`.
- **GCS mode:** through `gcs/http_api.py`'s local HTTP server, calling the
  exact same handler functions directly (no Lambda event translation
  differences you'd notice). Auth: `Authorization: Bearer <operator pairing
  token>`, printed to the GCS console on first run.

`{droneId}`/`{groupId}`/`{conversationId}` are path parameters throughout.

| Method | Path | Handler | Notes |
|---|---|---|---|
| POST | `/command` | `handler.lambda_handler` | Direct natural-language command, no conversation history |
| POST | `/rover/converse` | `rover.converse_handler` | Open-ended chat escalation for the rover mission agent |
| POST | `/rover/act` | `rover.act_handler` | Rover mission-agent action call |
| POST | `/drones` | `drones.register_handler` | Register a new drone |
| GET | `/drones` | `drones.list_handler` | List your drones |
| DELETE | `/drones/{droneId}` | `drones.delete_handler` | Remove a drone |
| PATCH | `/drones/{droneId}` | `drones.update_handler` | Rename, etc. |
| GET | `/drones/{droneId}/wifi` | `drones.wifi_get_handler` | |
| PUT | `/drones/{droneId}/wifi` | `drones.wifi_put_handler` | |
| GET | `/drones/{droneId}/battery-config` | `drones.battery_get_handler` | |
| PUT | `/drones/{droneId}/battery-config` | `drones.battery_put_handler` | |
| GET | `/drones/{droneId}/status` | `drones.status_handler` | Heartbeat-derived online/offline + last telemetry |
| GET | `/drones/{droneId}/logs` | `drones.get_logs_handler` | |
| POST | `/drones/{droneId}/conversations` | `conversations.create_handler` | Start a chat conversation with a drone |
| GET | `/drones/{droneId}/conversations/{conversationId}` | `conversations.get_handler` | |
| POST | `/drones/{droneId}/conversations/{conversationId}/messages` | `conversations.message_handler` | Send a message — see [`command-flow.md`](command-flow.md) for the full path to MAVLink |
| POST | `/drones/{droneId}/conversations/{conversationId}/select` | `conversations.select_handler` | Select among multiple candidate targets (e.g. "which chair") |
| POST | `/drones/{droneId}/conversations/{conversationId}/upload-url` | `conversations.upload_url_handler` | Get a presigned upload URL (S3) or the GCS's local upload URL |
| POST | `/groups` | `groups.create_handler` | Multi-drone groups |
| GET | `/groups` | `groups.list_handler` | |
| DELETE | `/groups/{groupId}` | `groups.delete_handler` | |
| POST | `/groups/{groupId}/conversations/{conversationId}/messages` | `groups.message_handler` | Broadcast a message to every drone in the group |
| GET | `/groups/{groupId}/conversations/{conversationId}` | `groups.history_handler` | |

## AWS-only routes (no GCS equivalent)

These exist in `aws/template.yaml` but aren't in `gcs/http_api.py`'s route
table — no local backend implements them (see
[`control-plane.md`](control-plane.md)'s degradation table):

| Method | Path | Why it's AWS-only |
|---|---|---|
| POST | `/auth/iot-policy` | Cognito-identity ↔ IoT-policy attach/detach; GCS auth is a flat pairing token, not Cognito |
| GET | `/drones/{droneId}/video/signaling` | Kinesis Video Streams WebRTC — no local video path |
| GET | `/drones/{droneId}/video/viewer` | same |
| POST | `/drones/{droneId}/video/start` | same |
| POST/GET/POST | `/sim/prewarm`, `/sim/session`, `/sim/session/{id}/end` | Web simulator orchestration — internal tooling, not part of the drone/app data path |

## Provisioning (not on this API — served by the drone itself)

`GET /info`, `POST /configure` etc. are served by the *drone*, not the
backend — see [`provisioning.md`](provisioning.md).
