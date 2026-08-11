"""Drone groups — WhatsApp-style fleets for IRL *and* sim drones.

A group is a named set of drone ids owned by a user. A group message is split by
one Bedrock call into a per-drone order ("everyone do X except rover-002 hold"),
then each order is dispatched over the **existing per-drone path**
(conversations.publish_to_drone). Each drone executes with its own autonomy, so
this works identically for real and simulated drones — the group concept never
reaches the drone.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

# Dual import: packaged (control.conversations/etc.) in the repo, flat
# (conversations/etc.) in a Lambda zip whose root is this directory's contents.
try:
    from . import conversations as C  # type: ignore  # reuse get_user_id, json_response, generate_code, publish_*
    from . import llm, clients  # type: ignore
except ImportError:  # pragma: no cover - flat Lambda zip install
    import conversations as C  # type: ignore
    import llm  # type: ignore
    import clients  # type: ignore

dynamodb = clients.get_dynamodb()
GROUPS_TABLE = os.environ.get("GROUPS_TABLE", "drone-groups-dev")
DRONE_TABLE = os.environ.get("DRONE_TABLE", "drone-registry-dev")

SPLIT_SYSTEM_PROMPT = """You are a drone-fleet dispatcher. You receive a group \
instruction and a roster of drones, each with an id and a type (quadcopter or \
rover). Output STRICT JSON: an object mapping every drone id to a short, concrete \
natural-language order for THAT specific drone.

Rules:
- Honor exceptions and per-drone callouts (e.g. "everyone search the area except \
rover-002, you stay put" → rover-002 gets "hold position", others get a search order).
- Quadcopters fly: phrase orders with takeoff/altitude/photos. Rovers drive on the \
ground: phrase with drive/turn/forward. Never tell a rover to take off.
- If the group is told to cover/search an area, give each drone a slightly \
different sub-area or heading so they spread out.
- Keep each order one sentence. Output ONLY the JSON object. Keys MUST be the exact \
drone ids from the roster."""


def _table(name):
    return dynamodb.Table(name)


# --- CRUD --------------------------------------------------------------------
def create_handler(event, context):
    user_id = C.get_user_id(event)
    if not user_id:
        return C.json_response(401, {"error": "Unauthorized"})
    body = json.loads(event.get("body", "{}") or "{}")
    name = body.get("name", "Group")
    members = body.get("members", [])
    if not isinstance(members, list) or not members:
        return C.json_response(400, {"error": "members[] required"})
    group_id = body.get("groupId") or f"grp-{uuid.uuid4().hex[:8]}"
    item = {"userId": user_id, "groupId": group_id, "name": name,
            "members": members, "createdAt": datetime.now(timezone.utc).isoformat()}
    _table(GROUPS_TABLE).put_item(Item=item)
    return C.json_response(201, {"group": item})


def list_handler(event, context):
    user_id = C.get_user_id(event)
    if not user_id:
        return C.json_response(401, {"error": "Unauthorized"})
    resp = _table(GROUPS_TABLE).query(
        KeyConditionExpression=Key("userId").eq(user_id))
    return C.json_response(200, {"groups": resp.get("Items", [])})


def delete_handler(event, context):
    user_id = C.get_user_id(event)
    group_id = event.get("pathParameters", {}).get("groupId")
    if not (user_id and group_id):
        return C.json_response(400, {"error": "missing group"})
    _table(GROUPS_TABLE).delete_item(Key={"userId": user_id, "groupId": group_id})
    return C.json_response(200, {"deleted": group_id})


def _get_group(user_id, group_id):
    return _table(GROUPS_TABLE).get_item(
        Key={"userId": user_id, "groupId": group_id}).get("Item")


def _member_types(user_id, members):
    """Map drone_id -> vehicleType from the registry (default quadcopter)."""
    table = _table(DRONE_TABLE)
    out = {}
    for did in members:
        try:
            it = table.get_item(Key={"userId": user_id, "droneId": did}).get("Item", {})
            out[did] = it.get("vehicleType", "quadcopter")
        except Exception:
            out[did] = "quadcopter"
    return out


# --- group message: split + fan out ------------------------------------------
def split_orders(message: str, member_types: dict) -> dict:
    """One LLM call (Bedrock by default, or a local GCS model) → {drone_id: per-drone order}."""
    roster = "\n".join(f"- {did} ({vt})" for did, vt in member_types.items())
    messages = [{"role": "user", "content": [{"type": "text",
        "text": f"Roster:\n{roster}\n\nGroup instruction: {message}"}]}]
    result = llm.invoke(SPLIT_SYSTEM_PROMPT, messages, max_tokens=1024, model=C.BEDROCK_MODEL_ID)
    text = "".join(b.get("text", "") for b in result.get("content", [])
                   if b.get("type") == "text").strip()
    if "```" in text:
        import re
        m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    try:
        orders = json.loads(text)
    except Exception:
        # fallback: everyone gets the raw message
        orders = {did: message for did in member_types}
    # only keep known members
    return {d: orders[d] for d in member_types if d in orders}


def history_handler(event, context):
    """Group conversation history. Like conversations.get_handler but ownership is
    verified against the group (a groupId is not a drone, so verify_ownership fails)."""
    user_id = C.get_user_id(event)
    if not user_id:
        return C.json_response(401, {"error": "Unauthorized"})
    group_id = event["pathParameters"]["groupId"]
    conversation_id = event["pathParameters"]["conversationId"]
    if not _get_group(user_id, group_id):
        return C.json_response(404, {"error": "group not found"})

    table = C.dynamodb.Table(C.CONVERSATIONS_TABLE)
    resp = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": f"CONV#{group_id}#{conversation_id}", ":sk": "MSG#"},
        ScanIndexForward=True)
    messages = []
    for item in resp.get("Items", []):
        if item.get("contentType") == "loading":
            continue
        msg = {"id": item["messageId"], "conversationId": conversation_id,
               "sender": item["sender"],
               "content": {"type": item["contentType"], "text": item["content"]},
               "timestamp": item["timestamp"]}
        if item.get("imageUrls"):
            signed = [C.generate_presigned_url(u) for u in item["imageUrls"]]
            if item["contentType"] == "image_choice":
                msg["content"]["options"] = [
                    {"id": i, "url": u, "description": None} for i, u in enumerate(signed)]
            else:
                msg["content"]["url"] = signed[0]
        messages.append(msg)
    return C.json_response(200, {"conversation_id": conversation_id, "messages": messages})


def message_handler(event, context):
    user_id = C.get_user_id(event)
    if not user_id:
        return C.json_response(401, {"error": "Unauthorized"})
    group_id = event["pathParameters"]["groupId"]
    conversation_id = event["pathParameters"]["conversationId"]
    body = json.loads(event.get("body", "{}") or "{}")
    message = body.get("message", "")
    if not message:
        return C.json_response(400, {"error": "Missing message"})

    group = _get_group(user_id, group_id)
    if not group:
        return C.json_response(404, {"error": "group not found"})
    members = group.get("members", [])
    member_types = _member_types(user_id, members)

    # record the user's group message
    C.save_message(conversation_id, group_id, "user", "text", message)

    orders = split_orders(message, member_types)
    print(f"[group {group_id}] orders: {json.dumps(orders)[:600]}")

    # Generate ALL per-drone code first, THEN publish back-to-back. Interleaving
    # slow Bedrock calls between publishes left long gaps that dropped delivery to
    # later drones; a tight publish loop (like independent publishers) is reliable.
    codes = {}
    for drone_id, order in orders.items():
        try:
            codes[drone_id] = C.generate_code(order, conversation_id, vehicle_type=member_types.get(drone_id, 'quadcopter'))
        except Exception as e:
            print(f"[group {group_id}] codegen {drone_id} failed: {e}")
    dispatched = {}
    for drone_id, code in codes.items():
        try:
            C.publish_to_drone(drone_id, conversation_id, {
                "action": "execute", "code": code,
                "original_message": orders[drone_id], "conversation_id": conversation_id,
                "groupId": group_id})
            dispatched[drone_id] = orders[drone_id]
        except Exception as e:
            print(f"[group {group_id}] dispatch {drone_id} failed: {e}")
            dispatched[drone_id] = f"(failed: {e})"

    # ack into the group thread; per-drone responses arrive on each member's
    # drone/{id}/chat/{conversation_id} topic (app subscribes per member).
    C.publish_to_app(group_id, conversation_id, "ack",
                     f"Dispatched to {len(dispatched)} drones.")
    return C.json_response(200, {"groupId": group_id, "orders": dispatched})
