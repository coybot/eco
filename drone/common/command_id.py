"""Stable identity for an inbound command, used to drop MQTT QoS 1 redeliveries.

Lives outside daemon.py so it can be tested: importing daemon pulls in awscrt and
generates a drone ID as a side effect of module import.

The rule is deliberately strict about what counts as "the same command". Dropping
a real command is a silent failure - the operator sees nothing happen and the app
sits in progress forever - so anything that differs at all must produce a
different id.
"""

import hashlib
import json


def compute_command_id(data: dict) -> str:
    """Return the dedup key for one command payload.

    An explicit message_id from the publisher wins. Otherwise hash the whole
    payload canonically.

    The previous fallback hashed only f"{conversation_id}:{code}". Commands
    carrying no code - action=mission and action=set_goal - therefore all
    collapsed onto md5("<conversation_id>:"), so the second mission in a
    conversation was always discarded as a duplicate of the first.
    """
    explicit = data.get("message_id")
    if explicit:
        return str(explicit)

    # sort_keys so a redelivery of the same payload hashes identically
    # regardless of key order; default=str so an odd value cannot raise here
    # and take the command handler down with it.
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.md5(canonical.encode()).hexdigest()
