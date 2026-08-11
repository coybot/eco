# `config.yaml` reference

Copy [`drone/common/config.yaml.example`](../drone/common/config.yaml.example)
to `config.yaml` next to `drone_sdk.py`/`daemon.py` (or wherever
`PRESIDIO_SDK_CONFIG_DIR`/`set_config_path()` point — see
[`sdk-reference.md`](sdk-reference.md)) and fill in what you need. Nothing
here is required for the SDK alone (`drone_sdk.py` only reads `serial_port`/
`baud_rate`); the rest is read by `daemon.py` for the always-on command
daemon.

| Key | Default | Used by | Notes |
|---|---|---|---|
| `drone_id` | auto-generated | `daemon.py` | Persisted after first boot |
| `control_plane` | `"aws"` | `daemon.py` | `"aws"` or `"gcs"` — see [`control-plane.md`](control-plane.md) |
| `serial_port` | `/dev/ttyACM0` | `drone_sdk.py` | Prefer a stable `/dev/serial/by-id/...` path over `/dev/ttyACM*`, which can reorder. `tcp:`/`udp:`/`udpin:` URLs work too (SITL) |
| `baud_rate` | `115200` | `drone_sdk.py` | |
| `vehicle_type` | `"quadcopter"` | `daemon.py` | `"quadcopter"`, `"rover"`, or `"fixedwing"` |
| `iot_endpoint` | — | `daemon.py` (AWS mode) | `aws iot describe-endpoint --endpoint-type iot:Data-ATS` |
| `region` | `"us-west-2"` | `daemon.py` (AWS mode) | Match your deployed stack |
| `gcs.mqtt_host` | — | `daemon.py` (GCS mode) | The GCS box's LAN IP |
| `gcs.mqtt_port` | `1883` | `daemon.py` (GCS mode) | |
| `gcs.mqtt_tls` | `false` | `daemon.py` (GCS mode) | |
| `gcs.mqtt_ca_cert` | — | `daemon.py` (GCS mode) | Only used when `mqtt_tls: true` |
| `gcs.auth_token` | — | `daemon.py` (GCS mode) | Printed by the GCS on first `POST /drones` registration |
| `gcs.images_base_url` | — | `daemon.py` (GCS mode) | The GCS's own HTTP API, e.g. `http://192.168.1.50:8080` |
| `battery.cells` | `4` | `daemon.py` | 3S=3, 4S=4, 6S=6 |
| `battery.capacity_mah` | `5000` | `daemon.py` | |
| `battery.low_voltage_per_cell` | `3.5` | `daemon.py` | |
| `battery.critical_voltage_per_cell` | `3.3` | `daemon.py` | |
| `log_level` | `"INFO"` | `daemon.py` | |
| `user_id` | — | `daemon.py` | Set automatically during WiFi provisioning — see [`provisioning.md`](provisioning.md) |
| `provisioning_timeout` | `60` (seconds) | `drone/common/provisioning.py` | |
| `video_role_alias` | — | `daemon.py` (AWS mode, video only) | IoT role alias for Kinesis access. Unused in GCS mode — video streaming has no local equivalent |
| `credentials_endpoint` | — | `daemon.py` (AWS mode, video only) | `aws iot describe-endpoint --endpoint-type iot:CredentialProvider` |
| `s3_role_alias` | — | `daemon.py` (AWS mode) | IoT role alias for S3 access |
| `images_bucket` | — | `daemon.py` (AWS mode) | Your S3 bucket for uploaded photos |

## Where config.yaml lives

By default, next to `drone_sdk.py` itself — matching the on-device flat
install (`drone/common/*.py` copied into `~/drone-api/`, see
[`install-companion.md`](install-companion.md)). Running the SDK as an
installed package with config elsewhere? See `set_config_path()` and
`PRESIDIO_SDK_CONFIG_DIR` in [`sdk-reference.md`](sdk-reference.md).

## Deploy-time overlay: `config.stack-dev.yaml`

If you're running the full AWS backend (not just the SDK), backend URLs
(`iot_endpoint`, `region`, `video_role_alias`, `credentials_endpoint`,
`s3_role_alias`, `images_bucket`) can instead live in a separate
`config.stack-dev.yaml` (see the `.example` template next to it) and get
merged into `config.yaml` at install time by `merge_stack_config.py` — useful
if you manage per-drone fields and stack-wide fields separately. Neither
file is required; setting the same keys directly in `config.yaml` works too.
