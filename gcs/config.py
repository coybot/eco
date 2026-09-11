"""
Configuration loading for the GCS server: gcs/config.yaml (see
config.yaml.example), with sensible defaults so a bare `python3 -m gcs.server`
against a local mosquitto + Ollama/vLLM works with zero config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class HttpConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    # Address other machines should use to reach this server, e.g.
    # "http://192.0.2.1:8080". Bound to 0.0.0.0 the server cannot infer its own
    # reachable address, so presigned image URLs fall back to 127.0.0.1 — which a
    # remote drone resolves to itself, and its photo upload is refused. Set this
    # whenever a drone or app runs on another host.
    public_base_url: Optional[str] = None


@dataclass
class MqttConfig:
    host: str = "127.0.0.1"
    port: int = 1883
    username: str = "gcs-server"
    password: Optional[str] = None
    tls: bool = False
    ca_cert: Optional[str] = None


@dataclass
class LlmConfig:
    provider: str = "openai"
    base_url: str = "http://localhost:11434/v1"
    model: Optional[str] = None
    api_key: Optional[str] = None
    timeout_s: float = 60.0


@dataclass
class GCSConfig:
    data_dir: Path = field(default_factory=lambda: Path.home() / "gcs-data")
    http: HttpConfig = field(default_factory=HttpConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    operators: list = field(default_factory=lambda: ["operator1"])
    # Optional table-name overrides (env var name -> value), merged over
    # control's own env-var defaults (DRONE_TABLE, STATUS_TABLE, etc.) - only
    # needed if you want the local DB file to use different logical table
    # names than the cloud defaults.
    tables: dict = field(default_factory=dict)


def load_config(path: Optional[str] = None) -> GCSConfig:
    raw = {}
    if path and Path(path).expanduser().exists():
        raw = yaml.safe_load(Path(path).expanduser().read_text()) or {}

    cfg = GCSConfig()
    cfg.data_dir = Path(os.path.expanduser(raw.get("data_dir", str(cfg.data_dir))))

    http_raw = raw.get("http") or {}
    cfg.http = HttpConfig(
        host=http_raw.get("host", cfg.http.host),
        port=int(http_raw.get("port", cfg.http.port)),
        public_base_url=http_raw.get("public_base_url", cfg.http.public_base_url),
    )

    mqtt_raw = raw.get("mqtt") or {}
    cfg.mqtt = MqttConfig(
        host=mqtt_raw.get("host", cfg.mqtt.host),
        port=int(mqtt_raw.get("port", cfg.mqtt.port)),
        username=mqtt_raw.get("username", cfg.mqtt.username),
        password=mqtt_raw.get("password", cfg.mqtt.password),
        tls=bool(mqtt_raw.get("tls", cfg.mqtt.tls)),
        ca_cert=mqtt_raw.get("ca_cert", cfg.mqtt.ca_cert),
    )

    llm_raw = raw.get("llm") or {}
    cfg.llm = LlmConfig(
        provider=llm_raw.get("provider", cfg.llm.provider),
        base_url=llm_raw.get("base_url", cfg.llm.base_url),
        model=llm_raw.get("model"),
        api_key=llm_raw.get("api_key"),
        timeout_s=float(llm_raw.get("timeout_s", cfg.llm.timeout_s)),
    )

    cfg.operators = raw.get("operators") or cfg.operators
    cfg.tables = raw.get("tables") or {}
    return cfg
