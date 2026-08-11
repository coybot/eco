# GCS (Ground Control Station)

Runs the same drone orchestration logic as the AWS Lambda stack in `control/`,
locally on a PC, Mac, or NVIDIA Thor with **no internet connection and no AWS
account** - for use as a Ground Control Station that talks to local models
(Ollama, vLLM, or anything else speaking the OpenAI `/v1/chat/completions`
API) instead of Bedrock.

The iOS/Android apps and the drones themselves get a config switch between
**cloud** (AWS, unchanged default) and **GCS** (this). Nothing here changes
cloud behavior - `control`'s handler modules run completely unmodified in
GCS mode; only their AWS client construction is swapped out (see
`control/clients.py` and `control/llm.py`).

## How it works

```
iOS/Android app --HTTP--> gcs/http_api.py --calls unmodified--> control/{handler,rover,drones,conversations,groups}.py
                                                                        |
                                                            control/llm.py (LLM_PROVIDER=openai)
                                                                        |
                                                          your local Ollama / vLLM server
                                                                        |
                                                        control/clients.py (LocalDynamo/LocalIoTData/LocalS3)
                                                                        |
                                                              control/backends/local.py + mosquitto
                                                                        |
                drone/common/daemon.py (control_plane: gcs) <--MQTT-- mosquitto --MQTT--> app (WebSocket)
```

`gcs/server.py` is the only thing that's GCS-specific about the request path:
it builds the local stand-ins and calls `control/clients.py`'s
`select_backend()`, then imports and calls the exact same Lambda handler
functions the cloud deploy uses, via an
HTTP-to-Lambda-event adapter (`gcs/http_api.py`, the same pattern as
`e2e/harness/live_rover_act_bridge.py`) and an MQTT-to-IoT-Rule adapter
(`gcs/rules.py`).

## Quickstart (Mac / PC / NVIDIA Thor)

1. **Install dependencies** (Python 3.9+):
   ```bash
   cd eco/gcs
   pip3 install -r requirements.txt
   brew install mosquitto   # or your platform's package manager
   ```

2. **Start a local OpenAI-compatible model server.** Any of these work:
   - Ollama: `ollama serve` (default `http://localhost:11434/v1`)
   - vLLM: `vllm serve <model> --enable-auto-tool-choice --tool-call-parser <parser>`
     (the `--enable-auto-tool-choice` flag matters for `/rover/act`'s forced
     tool call - see "Known limitations" below)

3. **Start the GCS server once** to generate pairing tokens and the mosquitto
   password file:
   ```bash
   cp config.yaml.example config.yaml   # edit llm.base_url/model if not using Ollama's default
   python3 -m server --config config.yaml
   ```
   It prints an operator pairing token - copy it, then Ctrl+C.

4. **Generate mosquitto's password file** from those tokens:
   ```bash
   ./gen_mosquitto_auth.sh --data-dir ~/gcs-data
   ```
   This prints a `gcs-server` MQTT password - paste it into `config.yaml`'s
   `mqtt.password`.

5. **Start mosquitto**, then the GCS server for real:
   ```bash
   mosquitto -c mosquitto/mosquitto.conf &
   python3 -m server --config config.yaml
   ```

6. **Point a drone or the sim at it**: see `drone/common/config.yaml.example`'s
   `control_plane: gcs` section, or `drone/sim/sim_drone_daemon.py
   --control-plane gcs --mqtt-host <this box's LAN IP>`.

7. **Point the app at it**: in the app's Settings, choose "Ground Control
   Station", enter this box's LAN IP + port 8080, and paste the pairing token
   from step 3/4.

## Verifying it's working

```bash
curl http://localhost:8080/healthz
# {"status": "ok"}

curl -H "Authorization: Bearer <operator token>" http://localhost:8080/drones
# {"drones": []}
```

`gcs/tests/test_local_stack.py` runs a full no-hardware smoke test (mosquitto +
GCS server + a simulated drone + a stub OpenAI server) - see that file's
docstring, or just run it:

```bash
python3 -m pytest tests/test_local_stack.py -v
```

## What's degraded vs. cloud mode

- **Video** (Kinesis Video Streams WebRTC) has no local equivalent and is not
  served in GCS mode - the app should hide/disable live video when connected
  to a GCS.
- **Device Shadow features** (wifi push, battery-config push, factory-reset
  ack) are store-only: `control/backends/local.py`'s `LocalIoTData.update_thing_shadow`
  persists the desired state to `<data_dir>/shadows/<droneId>.json`, but
  nothing in GCS mode delivers it live - a drone in GCS mode currently only
  gets these via its own local config, not a live shadow push. Fine for
  store-and-poll workflows; not a substitute for shadow's real offline-safe
  delivery guarantee.
- **`/auth/iot-policy`, `/sim/*`** are cloud-only (Cognito IoT policies,
  EC2-hosted Godot sim sessions) and are not served here at all.
- **Local-model tool-calling for `/rover/act`** (a forced `tool_choice`) is
  only as reliable as your model server's function-calling support. vLLM with
  `--enable-auto-tool-choice` works well; some Ollama models ignore forced
  tool choice entirely. `control/llm.py`'s `OpenAICompatProvider` includes a
  JSON-parsing fallback for exactly this case, but decision quality on small
  local models is unproven versus Claude.
- **DynamoDB shim fidelity**: `control/backends/local.py`'s `LocalDynamoTable` only
  implements the exact `put_item`/`get_item`/`delete_item`/`update_item`/
  `query`/`scan` call patterns found in `control` today. An unrecognized
  expression raises `NotImplementedError` rather than silently doing the
  wrong thing - if you extend `control` with a new DynamoDB access pattern,
  extend the shim's evaluator in `backends/local.py` to match.

## Files

| File | Purpose |
|---|---|
| `server.py` | Entrypoint - wires everything together, run via `python3 -m gcs.server` |
| `config.py` / `config.yaml.example` | Config loading |
| `auth.py` | Pairing-token auth (operators + drones), no Cognito |
| `signing.py` | HMAC-signed local image URLs (stands in for S3 presigned URLs) |
| `backends/local.py` | `LocalDynamo` (SQLite), `LocalIoTData` (paho-mqtt), `LocalS3` (filesystem) |
| `mqtt_client.py` | Thin paho-mqtt wrapper shared by publish (`LocalIoTData`) and subscribe (`rules.py`) |
| `http_api.py` | REST route table + Lambda-event adapter, mirrors `aws/template.yaml` |
| `rules.py` | MQTT subscriptions replacing AWS IoT Topic Rules |
| `mosquitto/mosquitto.conf`, `gen_mosquitto_auth.sh` | Local broker config + password-file generator |
| `docker-compose.yml` | Optional containerized mosquitto (secondary path, see file) |
| `tests/` | Unit tests for every module above + one full local-stack smoke test |
