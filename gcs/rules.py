"""
MQTT subscriptions that replace the AWS IoT Topic Rules in aws/template.yaml
(DroneStatusRule, DroneLogsRule, DroneChatResponseRule) - each just calls the
same production handler function the rule's Lambda calls, with `event` built
the same way the rule's SQL builds it.

Not replicated (dormant even in AWS - see aws/template.yaml, no `Advice` rule
exists there either): the drone/{id}/advice/request -> conversations.advice_handler
path.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

from mqtt_client import MqttClient


def _topic_segment(topic: str, index: int) -> str:
    """1-indexed, matching AWS IoT SQL's topic(n) function."""
    parts = topic.split("/")
    return parts[index - 1] if 0 < index <= len(parts) else ""


def register(mqtt: MqttClient, drones_mod, conversations_mod, executor: ThreadPoolExecutor):
    """Subscribes the three live rules. conversations.response_handler may call
    the configured LLM (llm.invoke) for image analysis, so it runs on the
    executor rather than paho's own network thread."""

    def on_status(topic: str, payload: bytes):
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return
        event["droneId"] = _topic_segment(topic, 2)
        event["receivedAt"] = int(time.time())
        drones_mod.store_status_handler(event, None)

    def on_logs(topic: str, payload: bytes):
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return
        event["droneId"] = _topic_segment(topic, 2)
        event["receivedAt"] = int(time.time())
        drones_mod.store_logs_handler(event, None)

    def on_chat_response(topic: str, payload: bytes):
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return
        executor.submit(conversations_mod.response_handler, event, None)

    mqtt.subscribe("drone/+/status", on_status, qos=1)
    mqtt.subscribe("drone/+/logs", on_logs, qos=1)
    mqtt.subscribe("drone/+/chat/+/response", on_chat_response, qos=1)
