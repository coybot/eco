"""Account-specific AWS endpoints, resolved at runtime instead of tracked.

The IoT data and credentials endpoints embed the AWS account they belong to, so
this repository carries placeholders rather than real ones. First hit wins:

1. an explicit --iot-endpoint / --credentials-endpoint on the command line
2. IOT_ENDPOINT / CREDENTIALS_ENDPOINT in the environment
3. ~/.config/presidio/secrets.env (override the path with PRESIDIO_SECRETS)
4. the placeholders below, which are not working endpoints

secrets.env is the same plain KEY=VALUE file the shell launchers source via
drone/sim/secrets_env.sh; see secrets.env.example at the repo root.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

IOT_PLACEHOLDER = "YOUR_IOT_ENDPOINT.iot.us-west-2.amazonaws.com"
CREDENTIALS_PLACEHOLDER = "YOUR_CREDENTIALS_ENDPOINT.credentials.iot.us-west-2.amazonaws.com"

_cache: Optional[Dict[str, str]] = None


def secrets_path() -> Path:
    return Path(os.environ.get("PRESIDIO_SECRETS", "~/.config/presidio/secrets.env")).expanduser()


def _load() -> Dict[str, str]:
    """Parse secrets.env once. A missing or unreadable file is not an error --
    the placeholders are a usable answer for anyone without an AWS account."""
    global _cache
    if _cache is not None:
        return _cache

    values: Dict[str, str] = {}
    path = secrets_path()
    try:
        text = path.read_text()
    except OSError:
        _cache = values
        return values

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value

    _cache = values
    return values


def resolve(name: str, placeholder: str) -> str:
    """Environment first, then secrets.env, then the placeholder."""
    from_env = os.environ.get(name)
    if from_env:
        return from_env
    return _load().get(name) or placeholder


def iot_endpoint() -> str:
    return resolve("IOT_ENDPOINT", IOT_PLACEHOLDER)


def credentials_endpoint() -> str:
    return resolve("CREDENTIALS_ENDPOINT", CREDENTIALS_PLACEHOLDER)


def is_placeholder(value: str) -> bool:
    """True when the caller is about to connect to nothing. Worth checking before
    a long-running job that would otherwise fail deep inside the MQTT client."""
    return value in (IOT_PLACEHOLDER, CREDENTIALS_PLACEHOLDER) or value.startswith("YOUR_")
