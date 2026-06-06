# Ishmael — natural-language sim test director

Turn one English sentence into a running, observed simulated test on Hoopoe.

```
"2 rovers and 3 quadcopters search the office for a chair and send a picture"
  → fleet spawned in Isaac (office scene)
  → mission dispatched through the cloud (Bedrock plans, drones execute)
  → the picture arrives at the mobile client (the test's "iPhone")
```

## Pieces
- **`nlp.py`** — `parse_test_spec(text) -> TestSpec`. Local vLLM on Hoopoe
  (`ISHMAEL_VLLM_URL`, the served Qwen) with a deterministic rule-based fallback.
- **`director.py`** — `run(spec)`: roster → register → launch fleet
  (`../launch_fleet.py`) → wait Online → dispatch via the mobile client → collect
  images/videos. Reuses `fleet.parse_roster` / `fleet.register_fleet`.
- **`cli.py`** — `python -m ishmael.cli "<sentence>"`.
- **mobile client** — `eco/sim/mobile/app_client.py` (headless, CI) and
  `drive_ios_sim.sh` (real iOS Simulator, Mac).

## Usage (on Hoopoe, from `eco/drone/sim`)
```bash
# parse only — works anywhere, no Isaac/AWS needed
python -m ishmael.cli --parse-only "1 rover and 1 quadcopter search an office for a chair"

# full run (Isaac venv python; certs + API token configured)
ISHMAEL_API_BASE=https://03bnj3wwef.execute-api.us-west-2.amazonaws.com/prod \
ISHMAEL_API_TOKEN="<cognito-id-token>" \
ISHMAEL_USER_SUB="<cognito-sub>" \
/home/yusuf/isaac-sim-env/bin/python3 -m ishmael.cli \
  "1 rover and 1 quadcopter search an office for a chair and send a picture"
```

## Environment
| var | purpose | default |
|-----|---------|---------|
| `ISHMAEL_VLLM_URL` | OpenAI-compatible vLLM base | `http://localhost:8000/v1` |
| `ISHMAEL_VLLM_MODEL` | model id (else first from `/v1/models`) | — |
| `ISHMAEL_API_BASE` | cloud API base for mission dispatch | — (required to run) |
| `ISHMAEL_API_TOKEN` | bearer token for the API authorizer | — |
| `ISHMAEL_USER_SUB` | Cognito sub to register the fleet under | — |
| `ISHMAEL_IOT_ENDPOINT` | IoT ATS data endpoint | dev endpoint |
| `ISHMAEL_MOBILE_CERTS` | certs dir for the app client's MQTT | `~/eco-certs` |
| `ISHMAEL_SIM_PYTHON` | Isaac venv python for the fleet launch | `/home/yusuf/isaac-sim-env/bin/python3` |

The director never talks to Isaac or Bedrock directly — it composes the existing
sim host and the mobile contract, so a sim drone stays IRL-identical end-to-end.
