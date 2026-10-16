"""
A paho-mqtt-backed connection adapter that exposes the same surface daemon.py
already codes against (the AWS IoT Device SDK v2's mqtt_connection object):

    .connect() -> Future
    .disconnect() -> Future
    .subscribe(topic=, qos=, callback=) -> (Future, packet_id)
    .publish(topic=, payload=, qos=) -> (Future, packet_id)

callback(topic, payload, **kwargs) matches the AWS IoT SDK's own callback
signature, so daemon.py's on_command/on_chat_command/on_video_command/etc.
run completely unmodified against either transport.

Used when config.yaml has `control_plane: gcs` - talks to a local Ground
Control Station's mosquitto broker (see gcs/README.md) over plain TCP or TLS
with username/password auth, instead of AWS IoT Core over mTLS with device
certs. Same topics either way.

Modeled on drone/sim/sim_drone_daemon.py's existing paho-mqtt usage, factored
out into a reusable adapter for the real on-device daemon.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from concurrent.futures import Future
from typing import Callable, Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger("drone.gcs_mqtt")


class PahoMqttAdapter:
    def __init__(self, host: str, port: int, client_id: str,
                 username: Optional[str] = None, password: Optional[str] = None,
                 tls: bool = False, ca_cert: Optional[str] = None,
                 on_connection_interrupted: Optional[Callable] = None,
                 on_connection_resumed: Optional[Callable] = None):
        self._host = host
        self._port = port
        self._on_interrupted = on_connection_interrupted
        self._on_resumed = on_connection_resumed
        self._connect_future: Optional[Future] = None
        self._disconnect_future: Optional[Future] = None
        self._ever_connected = False
        self._lock = threading.Lock()
        self._subscriptions: dict = {}       # topic filter -> (qos_int, callback)
        self._pending_subscribes: dict = {}  # mid -> Future
        # QoS 1 publishes awaiting the broker's PUBACK: mid -> Future. Acks that
        # arrive before publish() has registered its mid wait in _early_acks.
        # Never hold _pub_lock while calling into paho: paho runs on_publish
        # with its own _out_message_mutex held, and publish() takes that same
        # mutex - holding ours across it would deadlock against an ack.
        self._pub_lock = threading.Lock()
        self._pending_publishes: dict = {}
        self._early_acks: "OrderedDict[int, None]" = OrderedDict()

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

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_message = self._on_message
        self._client.on_publish = self._on_publish

    # --- surface daemon.py calls against (mirrors awsiot's mqtt_connection) ---

    def connect(self) -> Future:
        self._connect_future = Future()
        self._client.connect_async(self._host, self._port, keepalive=120)
        self._client.loop_start()
        return self._connect_future

    def disconnect(self) -> Future:
        self._disconnect_future = Future()
        self._client.disconnect()
        return self._disconnect_future

    def subscribe(self, topic: str, qos, callback: Callable) -> tuple:
        qos_int = int(qos)
        with self._lock:
            self._subscriptions[topic] = (qos_int, callback)
        result, mid = self._client.subscribe(topic, qos=qos_int)
        future = Future()
        if result == mqtt.MQTT_ERR_SUCCESS:
            with self._lock:
                self._pending_subscribes[mid] = future
        else:
            future.set_exception(RuntimeError(f"subscribe to {topic} failed: {result}"))
        return future, mid

    def publish(self, topic: str, payload, qos=0) -> tuple:
        """Never blocks. For QoS 0 the future is already resolved. For QoS 1 it
        resolves when the broker acknowledges the message - which is what the
        outbox waits for before it forgets a reply. paho keeps a QoS 1 message
        published while disconnected and sends it after reconnecting, so the
        future can resolve long after this returns (as awscrt's does)."""
        qos_int = int(qos)
        data = payload if isinstance(payload, (bytes, bytearray)) else str(payload).encode("utf-8")
        future = Future()
        info = self._client.publish(topic, data, qos=qos_int)
        if qos_int == 0:
            future.set_result(info.mid)
        elif info.rc not in (mqtt.MQTT_ERR_SUCCESS, mqtt.MQTT_ERR_NO_CONN):
            future.set_exception(RuntimeError(f"publish to {topic} failed: {info.rc}"))
        else:
            with self._pub_lock:
                acked = self._early_acks.pop(info.mid, False) is None
                if not acked:
                    self._pending_publishes[info.mid] = future
            if acked:
                future.set_result(info.mid)
        return future, info.mid

    def is_connected(self) -> bool:
        return bool(self._client.is_connected())

    # --- paho v2 callbacks -------------------------------------------------

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties=None):
        if reason_code == 0:
            logger.info("connected to GCS mosquitto broker at %s:%s", self._host, self._port)
            was_ever_connected = self._ever_connected
            self._ever_connected = True
            if self._connect_future and not self._connect_future.done():
                self._connect_future.set_result(None)

            # Re-subscribe to everything on every (re)connect - mosquitto
            # session persistence isn't guaranteed the same way AWS IoT's
            # clean_session=False is, so this is simpler and always correct.
            with self._lock:
                subs = dict(self._subscriptions)
            for sub_topic, (qos_int, _cb) in subs.items():
                client.subscribe(sub_topic, qos=qos_int)

            if was_ever_connected and self._on_resumed:
                self._on_resumed(self, return_code=reason_code, session_present=False)
        else:
            logger.error("GCS mosquitto connect failed: %s", reason_code)
            if self._connect_future and not self._connect_future.done():
                self._connect_future.set_exception(RuntimeError(f"connect failed: {reason_code}"))

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        logger.warning("disconnected from GCS mosquitto broker (%s)", reason_code)
        if self._disconnect_future and not self._disconnect_future.done():
            self._disconnect_future.set_result(None)
        elif self._on_interrupted:
            self._on_interrupted(self, error=RuntimeError(f"disconnected: {reason_code}"))

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties=None):
        with self._lock:
            future = self._pending_subscribes.pop(mid, None)
        if future and not future.done():
            future.set_result(None)

    def _on_publish(self, client, userdata, mid, reason_code=None, properties=None):
        with self._pub_lock:
            future = self._pending_publishes.pop(mid, None)
            if future is None:
                # Not registered yet (or a QoS 0 send nobody waits on). Bounded
                # far below the 65535 mid space, so an old entry is evicted long
                # before its mid can be reused.
                self._early_acks[mid] = None
                while len(self._early_acks) > 4096:
                    self._early_acks.popitem(last=False)
                return
        if not future.done():
            future.set_result(mid)

    def _on_message(self, client, userdata, message):
        with self._lock:
            subs = dict(self._subscriptions)
        for topic_filter, (_qos, callback) in subs.items():
            if mqtt.topic_matches_sub(topic_filter, message.topic):
                try:
                    callback(message.topic, message.payload)
                except Exception:
                    logger.exception("error in MQTT callback for topic %s", message.topic)
                return
