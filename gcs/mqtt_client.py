"""
Thin paho-mqtt wrapper shared by LocalIoTData (publish) and rules.py (subscribe)
- the GCS process holds exactly one MQTT connection to the local mosquitto
broker for all of it. Uses paho's v2 callback API (paho-mqtt>=2.0).
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger("gcs.mqtt")


class MqttClient:
    def __init__(self, host: str, port: int, username: Optional[str] = None,
                 password: Optional[str] = None, tls: bool = False,
                 ca_cert: Optional[str] = None, client_id: str = "gcs-server"):
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id, protocol=mqtt.MQTTv311,
        )
        if username:
            self._client.username_pw_set(username, password)
        if tls:
            if ca_cert:
                self._client.tls_set(ca_certs=ca_cert)
            else:
                self._client.tls_set()

        self._host = host
        self._port = port
        self._connected = threading.Event()
        self._subscriptions: dict = {}  # topic filter -> callback(topic, payload_bytes)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def connect(self, timeout: float = 10.0):
        self._client.connect(self._host, self._port, keepalive=60)
        self._client.loop_start()
        if not self._connected.wait(timeout):
            self._client.loop_stop()
            raise TimeoutError(f"could not connect to mosquitto at {self._host}:{self._port}")

    def disconnect(self):
        self._client.loop_stop()
        self._client.disconnect()

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        self._client.publish(topic, payload, qos=qos)

    def subscribe(self, topic_filter: str, callback: Callable[[str, bytes], None], qos: int = 1):
        """callback(topic, payload_bytes) fires for every message whose actual
        topic matches this filter (which may contain MQTT wildcards, e.g.
        'drone/+/status')."""
        self._subscriptions[topic_filter] = callback
        if self._connected.is_set():
            self._client.subscribe(topic_filter, qos=qos)

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties=None):
        if reason_code == 0:
            logger.info("connected to mosquitto at %s:%s", self._host, self._port)
            self._connected.set()
            for topic_filter in self._subscriptions:
                client.subscribe(topic_filter)
        else:
            logger.error("mosquitto connect failed: %s", reason_code)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        logger.warning("disconnected from mosquitto (%s) - paho will auto-reconnect", reason_code)
        self._connected.clear()

    def _on_message(self, client, userdata, message):
        for topic_filter, callback in list(self._subscriptions.items()):
            if mqtt.topic_matches_sub(topic_filter, message.topic):
                try:
                    callback(message.topic, message.payload)
                except Exception:
                    logger.exception("error handling message on topic %s", message.topic)
                return
