"""
Local stand-ins for the boto3 surfaces control/*.py actually uses: DynamoDB
(put_item/get_item/delete_item/update_item/query/scan), IoT Data
(publish/update_thing_shadow), and S3 (generate_presigned_url/upload_file/
put_object). These are duck-typed, not full boto3 emulations - each method
implements exactly the call patterns observed in control/{conversations,
handler,drones,groups}.py. An unrecognized call pattern raises NotImplementedError
rather than silently doing the wrong thing.

gcs/server.py builds LocalDynamo/LocalIoTData/LocalS3, wraps them in a
LocalBackend (below), and hands that to control.clients.select_backend().
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

try:
    from boto3.dynamodb.conditions import ConditionExpressionBuilder, ConditionBase
except ImportError:  # pragma: no cover - boto3 not installed on the GCS box
    ConditionExpressionBuilder = None
    ConditionBase = ()


# --- shared helpers ------------------------------------------------------------

def _decimal_to_native(value):
    """Recursively convert Decimal -> int/float. control's _to_dynamodb_types()
    does the reverse (float -> Decimal) before some put_item calls; we don't need
    to preserve Decimal identity in local storage, just the numeric value."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _decimal_to_native(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decimal_to_native(v) for v in value]
    return value


def _item_key_id(key: dict) -> str:
    """Canonical id for an item: DynamoDB item identity is exactly its primary
    key attribute(s), and every get/put/delete/update_item call supplies the
    FULL primary key - so this is schema-free and correct for 1-attr and
    2-attr (composite) keys alike."""
    return json.dumps(sorted(key.items()), sort_keys=True, default=str)


_NAME_RE = re.compile(r"^[#\w]+$")


def _resolve_name(token: str, names: dict) -> str:
    token = token.strip()
    if token.startswith("#"):
        if token not in names:
            raise NotImplementedError(f"ExpressionAttributeNames missing alias {token!r}")
        return names[token]
    return token


_CLAUSE_RE = re.compile(
    r"begins_with\(\s*(?P<bw_name>[#\w]+)\s*,\s*(?P<bw_val>:\w+)\s*\)"
    r"|(?P<name>[#\w]+)\s*(?P<op>=|<>|>=|<=|>|<)\s*(?P<val>:\w+)"
)


def _condition_to_string(condition, names: dict, values: dict):
    """Normalize a KeyConditionExpression/FilterExpression, which call sites pass
    either as a plain string (with ExpressionAttributeNames/Values) or as a
    boto3.dynamodb.conditions Key/Attr object, into (expr_string, names, values)
    - reusing boto3's own ConditionExpressionBuilder for the latter so both forms
    flow through one evaluator below."""
    if isinstance(condition, str) or condition is None:
        return condition, dict(names), dict(values)
    if ConditionExpressionBuilder is not None and isinstance(condition, ConditionBase):
        expr, built_names, built_values = ConditionExpressionBuilder().build_expression(
            condition, is_key_condition=True
        )
        merged_names = {**built_names, **names}
        merged_values = {**built_values, **values}
        return expr, merged_names, merged_values
    raise NotImplementedError(f"unsupported condition expression type: {type(condition)!r}")


def _eval_condition(item: dict, expr: Optional[str], names: dict, values: dict) -> bool:
    if not expr:
        return True
    ok = True
    pos = 0
    for m in _CLAUSE_RE.finditer(expr):
        if m.group("bw_name") is not None:
            attr = _resolve_name(m.group("bw_name"), names)
            prefix = values[m.group("bw_val")]
            actual = item.get(attr)
            ok = ok and isinstance(actual, str) and actual.startswith(prefix)
        else:
            attr = _resolve_name(m.group("name"), names)
            op = m.group("op")
            expected = values[m.group("val")]
            actual = item.get(attr)
            if actual is None:
                ok = False
            elif op == "=":
                ok = ok and actual == expected
            elif op == "<>":
                ok = ok and actual != expected
            elif op == ">":
                ok = ok and actual > expected
            elif op == "<":
                ok = ok and actual < expected
            elif op == ">=":
                ok = ok and actual >= expected
            elif op == "<=":
                ok = ok and actual <= expected
        pos = m.end()
    if pos == 0:
        raise NotImplementedError(f"unsupported condition expression syntax: {expr!r}")
    return ok


_SORT_HINT_ATTRS = ("timestamp", "createdAt", "SK")


def _sort_items(items: list, expr: Optional[str], names: dict, ascending: bool):
    """Best-effort sort-key ordering. Real DynamoDB sorts Query results by the
    table's sort key attribute; this shim has no schema to consult, so it looks
    for a sort-ish attribute named in the condition (begins_with/>/< clauses)
    first, then falls back to common conventions used in this codebase. Falls
    back to insertion order (stable) if nothing matches - degraded ordering, not
    a crash, since this only affects local/offline dev ergonomics."""
    sort_attr = None
    if expr:
        m = _CLAUSE_RE.search(expr)
        while m:
            name_token = m.group("bw_name") or m.group("name")
            resolved = _resolve_name(name_token, names)
            if resolved not in ("PK",) and not resolved.lower().endswith("id"):
                sort_attr = resolved
                break
            m = _CLAUSE_RE.search(expr, m.end())
    if sort_attr is None:
        for candidate in _SORT_HINT_ATTRS:
            if items and candidate in items[0]:
                sort_attr = candidate
                break
    if sort_attr is None:
        return items
    return sorted(items, key=lambda it: it.get(sort_attr, ""), reverse=not ascending)


# --- LocalDynamo -----------------------------------------------------------------

# The 5 tables control actually uses, by their cloud DEFAULT env-var value (see
# conversations.py/drones.py/handler.py/groups.py's `os.environ.get('X_TABLE',
# '<default>')` calls). gcs/server.py builds the real schema map from whatever
# table names its own config/env actually resolves to, falling back to this
# dict unchanged for any table it doesn't override - so a GCS install that
# doesn't rename tables gets correct behavior with zero config.
DEFAULT_TABLE_SCHEMAS = {
    "drone-registry-dev": ("userId", "droneId"),
    "drone-status-dev": ("droneId",),
    "drone-logs-dev": ("droneId", "timestamp"),
    "drone-chat-conversations-dev": ("PK", "SK"),
    "drone-groups-dev": ("userId", "groupId"),
}


class LocalDynamoTable:
    def __init__(self, store: "_SqliteStore", name: str, key_attrs: Optional[tuple] = None):
        self._store = store
        self._name = name
        self._key_attrs = key_attrs  # None => infer from first put_item (best-effort fallback)

    def put_item(self, Item: dict):
        item = _decimal_to_native(Item)
        # Every put_item observed in control writes a full item that already
        # contains its own primary key attribute(s) inline.
        key_attrs = self._key_attrs or self._store.infer_key_attrs(self._name, item)
        key = {k: item[k] for k in key_attrs if k in item}
        self._store.put(self._name, _item_key_id(key) if key else str(uuid.uuid4()), item)
        return {}

    def get_item(self, Key: dict):
        item = self._store.get(self._name, _item_key_id(Key))
        return {"Item": item} if item is not None else {}

    def delete_item(self, Key: dict):
        self._store.delete(self._name, _item_key_id(Key))
        return {}

    def update_item(self, Key: dict, UpdateExpression: str, ExpressionAttributeNames: dict = None,
                     ExpressionAttributeValues: dict = None, ReturnValues: str = None, **_):
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        item = self._store.get(self._name, _item_key_id(Key)) or dict(Key)

        expr = UpdateExpression.strip()
        if not expr.upper().startswith("SET "):
            raise NotImplementedError(
                f"only SET update expressions are supported by the local shim, got: {expr!r}"
            )
        for clause in expr[4:].split(","):
            lhs, _, rhs = clause.strip().partition("=")
            attr = _resolve_name(lhs.strip(), names)
            val_token = rhs.strip()
            if val_token not in values:
                raise NotImplementedError(f"missing ExpressionAttributeValues for {val_token!r}")
            item[attr] = _decimal_to_native(values[val_token])

        self._store.put(self._name, _item_key_id(Key), item)
        if ReturnValues == "ALL_NEW":
            return {"Attributes": item}
        return {}

    def query(self, KeyConditionExpression=None, ExpressionAttributeValues: dict = None,
              ExpressionAttributeNames: dict = None, ScanIndexForward: bool = None,
              Limit: int = None, IndexName: str = None, **_):
        expr, names, values = _condition_to_string(
            KeyConditionExpression, ExpressionAttributeNames or {}, ExpressionAttributeValues or {}
        )
        items = [it for it in self._store.all(self._name) if _eval_condition(it, expr, names, values)]
        if ScanIndexForward is not None:
            items = _sort_items(items, expr, names, ascending=ScanIndexForward)
        if Limit is not None:
            items = items[:Limit]
        return {"Items": items, "Count": len(items)}

    def scan(self, FilterExpression: str = None, ExpressionAttributeValues: dict = None,
             ExpressionAttributeNames: dict = None, **_):
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        items = [it for it in self._store.all(self._name)
                 if _eval_condition(it, FilterExpression, names, values)]
        return {"Items": items, "Count": len(items)}


class _SqliteStore:
    """One SQLite file backs every table: (table_name, item_id) -> json blob.
    A single global lock keeps this correct under the GCS's threaded HTTP
    server + MQTT rules subscriber without needing per-table locking."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS items ("
            " table_name TEXT NOT NULL,"
            " item_id TEXT NOT NULL,"
            " item_json TEXT NOT NULL,"
            " PRIMARY KEY (table_name, item_id)"
            ")"
        )
        self._conn.commit()
        self._key_attr_cache: dict[str, set] = {}

    def infer_key_attrs(self, table_name: str, item: dict) -> set:
        """Fallback ONLY for tables with no entry in DEFAULT_TABLE_SCHEMAS /
        server.py's schema map. Guessing from item contents is inherently
        fragile (e.g. a sort key like "timestamp" that isn't a conventional
        id-looking name would silently collapse rows - this exact bug was
        caught by control/backends/test_local.py), so this path exists only for
        forwards-compat with a table this shim doesn't know about yet, and
        callers should prefer passing an explicit key_attrs tuple."""
        if table_name in self._key_attr_cache:
            return self._key_attr_cache[table_name] & set(item)
        candidates = {k for k in item if k in (
            "userId", "droneId", "groupId", "PK", "SK", "conversationId", "timestamp",
        )}
        if not candidates:
            candidates = set(item)  # fall back to the whole item (still correct, just verbose)
        self._key_attr_cache[table_name] = candidates
        return candidates

    def put(self, table_name: str, item_id: str, item: dict):
        with self._lock:
            self._conn.execute(
                "INSERT INTO items (table_name, item_id, item_json) VALUES (?, ?, ?) "
                "ON CONFLICT(table_name, item_id) DO UPDATE SET item_json = excluded.item_json",
                (table_name, item_id, json.dumps(item)),
            )
            self._conn.commit()

    def get(self, table_name: str, item_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT item_json FROM items WHERE table_name = ? AND item_id = ?",
                (table_name, item_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def delete(self, table_name: str, item_id: str):
        with self._lock:
            self._conn.execute(
                "DELETE FROM items WHERE table_name = ? AND item_id = ?", (table_name, item_id)
            )
            self._conn.commit()

    def all(self, table_name: str):
        with self._lock:
            rows = self._conn.execute(
                "SELECT item_json FROM items WHERE table_name = ?", (table_name,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]


class LocalDynamo:
    """Drop-in for `boto3.resource('dynamodb')` - only `.Table(name)` is used
    anywhere in control.

    `schemas` maps table_name -> primary key attribute names, e.g.
    {"drone-registry-dev": ("userId", "droneId")}. Defaults to
    DEFAULT_TABLE_SCHEMAS; gcs/server.py passes the real map built from
    whatever DRONE_TABLE/STATUS_TABLE/etc. env vars it actually set, so a
    renamed table still gets correct (non-guessed) key handling."""

    def __init__(self, db_path: Path, schemas: Optional[dict] = None):
        self._store = _SqliteStore(db_path)
        self._tables: dict[str, LocalDynamoTable] = {}
        self._schemas = {**DEFAULT_TABLE_SCHEMAS, **(schemas or {})}

    def Table(self, name: str) -> LocalDynamoTable:
        if name not in self._tables:
            key_attrs = self._schemas.get(name)
            self._tables[name] = LocalDynamoTable(self._store, name, key_attrs)
        return self._tables[name]


# --- LocalIoTData ----------------------------------------------------------------

class LocalIoTData:
    """Drop-in for `boto3.client('iot-data')` - only .publish() and
    .update_thing_shadow() are used anywhere in control. Publishing forwards to
    the local mosquitto broker; shadow updates are stored (store-only - there is
    no live shadow-subscribe delivery path to the drone in GCS mode, see
    gcs/README.md's "degraded features" section) so a later REST read can still
    see the last-desired state."""

    def __init__(self, mqtt_client, shadow_dir: Path):
        self._mqtt = mqtt_client
        self._shadow_dir = shadow_dir
        self._shadow_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def publish(self, topic: str, payload, qos: int = 0, **_):
        data = payload if isinstance(payload, (bytes, bytearray)) else str(payload).encode("utf-8")
        self._mqtt.publish(topic, data, qos=qos)

    def update_thing_shadow(self, thingName: str, payload):
        data = json.loads(payload if isinstance(payload, str) else payload.decode("utf-8"))
        path = self._shadow_dir / f"{thingName}.json"
        with self._lock:
            existing = json.loads(path.read_text()) if path.exists() else {"state": {"desired": {}}}
            desired = data.get("state", {}).get("desired", {})
            existing.setdefault("state", {}).setdefault("desired", {}).update(desired)
            path.write_text(json.dumps(existing))
        return {"payload": json.dumps(existing).encode("utf-8")}


# --- LocalS3 -----------------------------------------------------------------------

class LocalS3:
    """Drop-in for the S3 client control builds - only .generate_presigned_url()
    is used. Presigned PUT/GET urls point back at the GCS's own HTTP server
    (see gcs/http_api.py's /images/{key} route), carrying a short-lived HMAC
    token (gcs/signing.py) instead of a real SigV4 signature."""

    def __init__(self, base_url: str, images_dir: Path, sign_fn):
        self._base_url = base_url.rstrip("/")
        self._images_dir = images_dir
        self._images_dir.mkdir(parents=True, exist_ok=True)
        self._sign_fn = sign_fn  # (key: str, expires_in: int) -> opaque token str

    def generate_presigned_url(self, ClientMethod: str, Params: dict, ExpiresIn: int = 3600, **_):
        key = Params["Key"]
        token = self._sign_fn(key, ExpiresIn)
        return f"{self._base_url}/images/{key}?token={token}"

    def public_image_url(self, key: str) -> str:
        return f"{self._base_url}/images/{key}"

    # Used by drone-side HTTP-PUT image upload (see drone/common/drone_sdk.py's
    # GCS branch) indirectly - http_api.py writes the file directly to
    # self._images_dir, this helper exists for any future direct-write caller.
    def path_for(self, key: str) -> Path:
        return self._images_dir / key


# --- LocalBackend ------------------------------------------------------------------

class LocalBackend:
    """Adapts a LocalDynamo/LocalIoTData/LocalS3 trio to the backend interface
    control.clients.select_backend() expects (.dynamodb, .iot, .s3,
    .image_url_fn(key)). gcs/server.py constructs the three (they need
    cfg-derived paths, an mqtt client, etc. that this module has no access to)
    and wraps them in one of these."""

    def __init__(self, dynamodb: LocalDynamo, iot: LocalIoTData, s3: LocalS3):
        self.dynamodb = dynamodb
        self.iot = iot
        self.s3 = s3

    def image_url_fn(self, key: str) -> str:
        return self.s3.public_image_url(key)
