"""Sim session orchestrator — bridges the website to the on-demand EC2 sim host.

Routes (all Cognito-authorized via the shared TokenAuthorizer):
  POST /sim/prewarm           -> start the (stopped) EC2 sim host; return immediately.
  POST /sim/session           -> ensure host healthy, forward the reconfigure
                                 request, return {wsUrl, sessionId, drones[]}.
  POST /sim/session/{id}/end  -> tell the host to tear the session down.

The EC2 host runs sim_session_server.py. It boots Godot (empty world) + a daemon
pool on start, so by the time the user finishes configuring (pre-warm overlap) a
session start is just a fast reconfigure. The host self-stops on idle as a cost
backstop; this orchestrator also stops it on /end.

Env vars: SIM_INSTANCE_ID, SIM_HOST_BASE (e.g. https://sim.presidioautonomy.com),
SIM_SHARED_SECRET, SIM_PUBLIC_WSS (e.g. wss://sim.presidioautonomy.com).
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error

import boto3

INSTANCE_ID = os.environ.get("SIM_INSTANCE_ID", "")
HOST_BASE = os.environ.get("SIM_HOST_BASE", "https://sim.presidioautonomy.com").rstrip("/")
SHARED_SECRET = os.environ.get("SIM_SHARED_SECRET", "")
PUBLIC_WSS = os.environ.get("SIM_PUBLIC_WSS", "wss://sim.presidioautonomy.com")
REGION = os.environ.get("AWS_REGION", "us-west-2")

ec2 = boto3.client("ec2", region_name=REGION)


def _user_id(event):
    try:
        a = event["requestContext"]["authorizer"]
        return a.get("userId") or a.get("principalId")
    except (KeyError, TypeError):
        return None


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
        },
        "body": json.dumps(body, default=str),
    }


def _instance_state() -> str:
    if not INSTANCE_ID:
        return "unknown"
    r = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    return r["Reservations"][0]["Instances"][0]["State"]["Name"]


def _start_instance():
    state = _instance_state()
    if state in ("stopped", "stopping"):
        ec2.start_instances(InstanceIds=[INSTANCE_ID])
    return state


def _http(method, path, payload=None, timeout=8):
    url = f"{HOST_BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if SHARED_SECRET:
        req.add_header("X-Sim-Secret", SHARED_SECRET)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"{}")


# -- handlers ---------------------------------------------------------------
def prewarm_handler(event, context):
    if not _user_id(event):
        return _resp(401, {"error": "unauthorized"})
    if not INSTANCE_ID:
        return _resp(500, {"error": "SIM_INSTANCE_ID not configured"})
    state = _start_instance()
    return _resp(200, {"ok": True, "state": state})


def session_handler(event, context):
    user_id = _user_id(event)
    if not user_id:
        return _resp(401, {"error": "unauthorized"})
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "bad json"})

    fleet = body.get("fleet", {})
    env_name = body.get("env", "office")

    # Ensure the host is starting, but DON'T block — API Gateway caps the
    # integration at 29s and a cold boot can take longer. If the host isn't
    # healthy yet, tell the client to retry (it polls); pre-warm usually means
    # the very first call already finds it healthy.
    _start_instance()
    try:
        hstatus, _ = _http("GET", "/healthz", timeout=4)
        healthy = hstatus == 200
    except (urllib.error.URLError, OSError, ValueError):
        healthy = False
    if not healthy:
        return _resp(503, {"error": "warming", "retry": True})

    try:
        status, result = _http("POST", "/session",
                               {"fleet": fleet, "env": env_name, "userId": user_id},
                               timeout=30)
    except urllib.error.HTTPError as e:
        # Surface 409 "busy" / 4xx from the host verbatim.
        return _resp(e.code, {"error": e.reason})
    except (urllib.error.URLError, OSError) as e:
        return _resp(502, {"error": f"sim host error: {e}"})

    if status != 200:
        return _resp(status, result)
    result["wsUrl"] = f"{PUBLIC_WSS}/stream?session={result.get('sessionId')}"
    return _resp(200, result)


def end_handler(event, context):
    if not _user_id(event):
        return _resp(401, {"error": "unauthorized"})
    sid = (event.get("pathParameters") or {}).get("id")
    if not sid:
        return _resp(400, {"error": "session id required"})
    try:
        _http("POST", f"/session/{sid}/end", {}, timeout=10)
    except (urllib.error.URLError, OSError):
        pass  # host may already be stopping; best-effort
    # Stop the instance to cap cost (host also self-stops on idle).
    if INSTANCE_ID and _instance_state() == "running":
        try:
            ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        except Exception:
            pass
    return _resp(200, {"ok": True})


def handler(event, context):
    """Single entrypoint — routes by API Gateway resource path."""
    resource = event.get("resource", "")
    if resource == "/sim/prewarm":
        return prewarm_handler(event, context)
    if resource == "/sim/session":
        return session_handler(event, context)
    if resource == "/sim/session/{id}/end":
        return end_handler(event, context)
    return _resp(404, {"error": f"unknown route {resource}"})
