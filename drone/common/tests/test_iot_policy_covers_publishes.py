"""Every topic the drone publishes to must be allowed by its IoT policy.

AWS IoT answers a publish the policy does not allow by dropping the
connection. Mission progress went to drone/{id}/chat/{cid}/progress, which
drone-policy-dev did not list: in a cloud test the aircraft was disconnected
six times in one survey, and the mission result, published during a
reconnect, never reached the chat. Nothing errors on the drone -- the only
symptom is "Connection interrupted: AWS_ERROR_MQTT_UNEXPECTED_HANGUP".

Also pins that the result the operator waits for is published at QoS 1.

Run: cd eco && python3 -m pytest drone/common/tests/test_iot_policy_covers_publishes.py -v
"""

import fnmatch
import re
from pathlib import Path

ECO = Path(__file__).resolve().parents[3]
COMMON = ECO / "drone" / "common"
TEMPLATE = ECO / "aws" / "template.yaml"


def _policy_publish_patterns():
    """topic/... resources of the iot:Publish statement in DroneIoTPolicy."""
    text = TEMPLATE.read_text()
    block = text[text.index("\n  DroneIoTPolicy:"):]
    pub = block.index("- iot:Publish")
    patterns = []
    for line in block[pub:].splitlines()[1:]:
        m = re.search(r":topic/([^']+)'", line)
        if m:
            patterns.append(m.group(1))
        elif "Effect:" in line:
            break
    return patterns


def _published_topics():
    """drone/... topic templates in f-strings the drone code publishes to."""
    topics = set()
    for name in ("daemon.py", "reasoning_loop.py"):
        for m in re.finditer(r'f"(drone/\{[^}]+\}/[^"]+)"', (COMMON / name).read_text()):
            topics.add(re.sub(r"\{[^}]+\}", "X", m.group(1)))
    return topics


def test_the_policy_block_is_found():
    patterns = _policy_publish_patterns()
    assert "drone/*/status" in patterns and "drone/*/chat/*/response" in patterns


def test_every_drone_publish_topic_is_allowed():
    patterns = _policy_publish_patterns()
    subscribed_only = {"command"}  # topics the drone receives, not publishes
    missing = sorted(
        t for t in _published_topics()
        if t.rsplit("/", 1)[-1] not in subscribed_only
        and not any(fnmatch.fnmatchcase(t, p) for p in patterns)
    )
    assert not missing, f"drone publishes to topics its IoT policy forbids: {missing}"


def test_mission_results_are_published_at_qos_1():
    src = (COMMON / "daemon.py").read_text()
    calls = re.findall(r"topic=response_topic,\s*payload=[^\n]+\n\s*qos=mqtt\.QoS\.(\w+)", src)
    assert calls and set(calls) == {"AT_LEAST_ONCE"}, calls
