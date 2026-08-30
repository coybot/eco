#!/usr/bin/env python3
"""
GCS (Ground Control Station) server entrypoint.

Wires local stand-ins for AWS (SQLite "DynamoDB", mosquitto-backed "IoT",
filesystem "S3") into aws/src's *unmodified* production Lambda handler
modules, then serves the same REST routes and MQTT topics the cloud stack
does - see gcs/README.md for the full picture and a quickstart.

Order matters: clients.configure() MUST run before aws/src's handler modules
are imported, since conversations.py/handler.py/drones.py/groups.py capture
`dynamodb = clients.get_dynamodb()` etc. at their own import time.

Run (from the eco/gcs directory, so this dir's modules are importable):
    cd eco/gcs && python3 -m server --config config.yaml
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from auth import AuthStore
from config import GCSConfig, load_config
from mqtt_client import MqttClient
from signing import ImageUrlSigner

AWS_SRC = Path(__file__).resolve().parent.parent / "aws" / "src"

# Table logical identity -> (env var aws/src reads, its cloud default, the
# local shim's key schema). Any config.yaml `tables:` override is honored by
# setting the same env var before aws/src is imported, so both sides agree on
# the table name - and the schema map below always matches whichever name
# ends up in effect.
_TABLE_SPECS = {
    "DRONE_TABLE": ("drone-registry-dev", ("userId", "droneId")),
    "STATUS_TABLE": ("drone-status-dev", ("droneId",)),
    "LOGS_TABLE": ("drone-logs-dev", ("droneId", "timestamp")),
    "CONVERSATIONS_TABLE": ("drone-chat-conversations-dev", ("PK", "SK")),
    "GROUPS_TABLE": ("drone-groups-dev", ("userId", "groupId")),
}


def _apply_table_env(cfg: GCSConfig) -> dict:
    """Sets DRONE_TABLE/STATUS_TABLE/etc. in os.environ (so aws/src resolves
    the same table names this process's LocalDynamo schema map uses), and
    returns {table_name: key_attrs} for LocalDynamo(schemas=...)."""
    schemas = {}
    for env_name, (default_name, key_attrs) in _TABLE_SPECS.items():
        name = cfg.tables.get(env_name) or os.environ.get(env_name) or default_name
        os.environ[env_name] = name
        schemas[name] = key_attrs
    return schemas


def _set_llm_env(cfg: GCSConfig):
    os.environ["LLM_PROVIDER"] = cfg.llm.provider
    os.environ["LLM_BASE_URL"] = cfg.llm.base_url
    if cfg.llm.model:
        os.environ["LLM_MODEL"] = cfg.llm.model
    elif "LLM_MODEL" in os.environ:
        del os.environ["LLM_MODEL"]
    if cfg.llm.api_key:
        os.environ["LLM_API_KEY"] = cfg.llm.api_key
    os.environ["LLM_TIMEOUT_S"] = str(cfg.llm.timeout_s)


class GCSHandle:
    """Handle to a running GCS instance - returned by start(), used by tests
    to shut everything down cleanly and to inspect the running components."""

    def __init__(self, http_server, mqtt, auth_store, signer, cfg: GCSConfig):
        self.http_server = http_server
        self.mqtt = mqtt
        self.auth_store = auth_store
        self.signer = signer
        self.cfg = cfg

    @property
    def base_url(self) -> str:
        return self.http_server.base_url

    def stop(self):
        self.http_server.stop()
        self.mqtt.disconnect()


def start(cfg: GCSConfig) -> GCSHandle:
    """Builds and starts every GCS component, then returns immediately (the
    HTTP server and MQTT client each run their own background threads). Split
    out from run() so tests can start/stop a real instance without blocking
    on the Ctrl+C loop."""
    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    _set_llm_env(cfg)
    schemas = _apply_table_env(cfg)

    auth_store = AuthStore(cfg.data_dir / "auth.json", operator_names=cfg.operators)
    signer_secret = auth_store.operator_tokens().get(cfg.operators[0], "gcs-default-secret")
    signer = ImageUrlSigner(signer_secret)

    mqtt = MqttClient(
        host=cfg.mqtt.host, port=cfg.mqtt.port,
        username=cfg.mqtt.username, password=cfg.mqtt.password,
        tls=cfg.mqtt.tls, ca_cert=cfg.mqtt.ca_cert, client_id="gcs-server",
    )
    print(f"[gcs] connecting to mosquitto at {cfg.mqtt.host}:{cfg.mqtt.port} ...")
    mqtt.connect()

    # Import the local shims and wire them in via clients.configure() BEFORE
    # aws/src's handler modules are imported - see module docstring.
    import local_aws
    dynamodb = local_aws.LocalDynamo(cfg.data_dir / "dynamo.sqlite3", schemas=schemas)
    iot = local_aws.LocalIoTData(mqtt, cfg.data_dir / "shadows")
    s3 = local_aws.LocalS3(
        base_url=cfg.http.public_base_url or
                 f"http://{cfg.http.host if cfg.http.host != '0.0.0.0' else '127.0.0.1'}:{cfg.http.port}",
        images_dir=cfg.data_dir / "images",
        sign_fn=signer.sign,
    )

    sys.path.insert(0, str(AWS_SRC))
    import clients
    clients.configure(dynamodb=dynamodb, iot=iot, s3=s3, image_url_fn=s3.public_image_url)

    import handler
    import rover
    import drones
    import conversations
    import groups

    import rules
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gcs-rule")
    rules.register(mqtt, drones, conversations, executor)

    import http_api
    routes = http_api.build_routes(handler, rover, drones, conversations, groups)
    http_server = http_api.GCSHttpServer(
        host=cfg.http.host, port=cfg.http.port, routes=routes,
        auth_store=auth_store, images_dir=cfg.data_dir / "images", sign_verify_fn=signer.verify,
    )
    http_server.start()

    print(f"[gcs] LLM provider: {cfg.llm.provider} @ {cfg.llm.base_url}")
    print(f"[gcs] HTTP API listening on {http_server.base_url} (bound {cfg.http.host}:{cfg.http.port})")
    print(f"[gcs] data dir: {cfg.data_dir}")
    print("[gcs] operator pairing tokens (paste into the app's GCS settings screen):")
    for name, token in auth_store.operator_tokens().items():
        print(f"       {name}: {token}")
    print("[gcs] ready.", flush=True)

    return GCSHandle(http_server, mqtt, auth_store, signer, cfg)


def run(cfg: GCSConfig):
    """CLI entrypoint: start() then block until Ctrl+C."""
    handle = start(cfg)
    print("[gcs] Ctrl+C to stop.")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        handle.stop()


def main():
    parser = argparse.ArgumentParser(description="GCS (Ground Control Station) server")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    args = parser.parse_args()
    run(load_config(args.config))


if __name__ == "__main__":
    main()
