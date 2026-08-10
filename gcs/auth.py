"""
Pairing-token auth for the GCS HTTP API and MQTT broker - no Cognito, no Google/
Apple sign-in, no cloud dependency. Modeled on drone/common/local_control_api.py's
pairing-token pattern (STATE_DIR/pairing_token.json, 0600 perms, generated on
first run).

Two kinds of tokens, one store:
- operator tokens: one per named operator in config.yaml (`operators:`). The
  iOS/Android app authenticates HTTP requests with `Authorization: Bearer
  <token>`; http_api.py maps that to a stable synthetic userId ("gcs-<name>")
  so aws/src's existing per-user ownership checks (verify_ownership, etc.) work
  unchanged.
- drone tokens: one per drone id, generated on first registration. Used as the
  MQTT password for that drone's mosquitto account (see gen_mosquitto_auth.sh).

Both live in the same JSON file so `gen_mosquitto_auth.sh` has one place to read
from when building mosquitto's password file.
"""
from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path
from typing import Optional


class AuthStore:
    def __init__(self, path: Path, operator_names: Optional[list] = None):
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()
        changed = False
        for name in (operator_names or ["operator1"]):
            changed = self._ensure_operator(name) or changed
        if changed:
            self._save()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                data.setdefault("operators", {})
                data.setdefault("drones", {})
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"operators": {}, "drones": {}}

    def _save(self):
        self._path.write_text(json.dumps(self._data, indent=2))
        try:
            self._path.chmod(0o600)
        except OSError:
            pass  # best-effort on platforms without POSIX perms

    def _ensure_operator(self, name: str) -> bool:
        if name in self._data["operators"]:
            return False
        token = secrets.token_urlsafe(32)
        self._data["operators"][name] = token
        print(f"[gcs] generated pairing token for operator '{name}': {token}")
        return True

    # --- operators (HTTP / app auth) --------------------------------------

    def operator_user_id_for_token(self, token: str) -> Optional[str]:
        if not token:
            return None
        for name, tok in self._data["operators"].items():
            if secrets.compare_digest(tok, token):
                return f"gcs-{name}"
        return None

    def operator_tokens(self) -> dict:
        return dict(self._data["operators"])

    # --- drones (MQTT auth) ------------------------------------------------

    def ensure_drone_token(self, drone_id: str) -> str:
        with self._lock:
            if drone_id not in self._data["drones"]:
                self._data["drones"][drone_id] = secrets.token_urlsafe(32)
                self._save()
            return self._data["drones"][drone_id]

    def drone_id_for_token(self, token: str) -> Optional[str]:
        if not token:
            return None
        for did, tok in self._data["drones"].items():
            if secrets.compare_digest(tok, token):
                return did
        return None

    def drone_tokens(self) -> dict:
        return dict(self._data["drones"])


def user_id_from_authorization_header(auth_store: AuthStore, header_value: Optional[str]) -> Optional[str]:
    """Parses an `Authorization: Bearer <token>` header into the synthetic
    userId aws/src's get_user_id(event) expects to find at
    event['requestContext']['authorizer']['userId']."""
    if not header_value or not header_value.startswith("Bearer "):
        return None
    token = header_value.split(" ", 1)[1].strip()
    return auth_store.operator_user_id_for_token(token)
