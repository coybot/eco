# Quickstart: Ground Control Station (no AWS account needed)

This is the zero-dependency path, and the app's default. No AWS account, no
certificates, no Presidio-hosted infrastructure — just this repo, a local
LLM, and mosquitto.

## 1. Install the drone SDK

```bash
git clone https://github.com/presidio-autonomy/presidio-platform.git
cd presidio-platform
pip install -e .
```

## 2. Install and start the GCS server

```bash
cd gcs
pip3 install -r requirements.txt
brew install mosquitto   # or your platform's package manager
```

Start a local OpenAI-compatible model server — Ollama (`ollama serve`,
default `http://localhost:11434/v1`) is the easiest. vLLM works too; pass
`--enable-auto-tool-choice --tool-call-parser <parser>` (needed for
`/rover/act`'s forced tool call).

```bash
cp config.yaml.example config.yaml   # edit llm.base_url/model if not using Ollama's default
python3 -m server --config config.yaml   # prints an operator pairing token — copy it, then Ctrl+C
./gen_mosquitto_auth.sh --data-dir ~/gcs-data   # prints a gcs-server MQTT password — paste into config.yaml's mqtt.password
mosquitto -c mosquitto/mosquitto.conf &
python3 -m server --config config.yaml
```

Verify:
```bash
curl http://localhost:8080/healthz
# {"status": "ok"}
```

## 3. Point a drone at it

In `drone/common/config.yaml` (copy from `config.yaml.example`):

```yaml
control_plane: "gcs"
gcs:
  mqtt_host: "192.168.1.50"        # this box's LAN IP
  mqtt_port: 1883
  auth_token: "PASTE_DRONE_PAIRING_TOKEN_HERE"
  images_base_url: "http://192.168.1.50:8080"
```

The drone's pairing token comes from registering it — `POST /drones` (see
[`api-reference.md`](api-reference.md)) — not the *operator* token from
step 2. No certificates needed in this mode.

No hardware yet? `gcs/tests/test_local_stack.py::test_full_flow_register_status_message_response`
is a real, runnable no-hardware round trip — a plain MQTT client heartbeats
as a drone and gets a chat response back, against a real GCS server and
mosquitto. Read it for the exact request shapes, or run it directly:
```bash
cd gcs && python3 -m pytest tests/test_local_stack.py -v
```

## 4. Point the app at it

In the app's Settings → Ground Control Station (this is the app's default
control plane): enter this box's LAN IP and port 8080, and paste the
*operator* pairing token from step 2.

## What you get, and what's degraded

Full command chat, drone registry, groups, rover mission agent — all the
same code paths as the cloud deployment (see
[`command-flow.md`](command-flow.md)). Video streaming and Cognito auth are
cloud-only with no local equivalent; see
[`control-plane.md`](control-plane.md) for the full list.

## Next steps

- [`config-reference.md`](config-reference.md) — every `config.yaml` key
- [`control-plane.md`](control-plane.md) — how backend selection works under the hood
- [`gcs/README.md`](../gcs/README.md) — the full picture, including
  `gcs/tests/test_local_stack.py`'s no-hardware smoke test
