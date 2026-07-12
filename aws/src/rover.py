import json
import os
import boto3

AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-5")
ANTHROPIC_VERSION = "bedrock-2023-05-31"
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

RAMP_AGENT_SYSTEM_PROMPT = """You are the voice of an autonomous baggage-handling robot working \
the ramp at Hawthorne Airport, moving luggage to and from aircraft. You are only reached for \
things the robot's on-device assistant could NOT handle itself: small talk, general knowledge \
questions, or ambiguous requests — anything about moving the robot (navigation, stop) is handled \
on-device and never reaches you.

Keep replies short (1-2 sentences), calm, and natural to say out loud through a speaker on an \
active ramp. You have no real-time lookup and no access to flight, gate, or baggage-routing \
systems — if asked for those, say you'll get a human ramp agent to help. You are never authorized \
to make safety, movement-area, or aircraft-proximity decisions — if a request sounds like it \
concerns ramp safety, say so and defer to a human immediately. Never generate code."""


def json_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
        },
        "body": json.dumps(body),
    }


def get_user_id(event):
    """Extract user ID from Lambda authorizer context (same pattern as handler.py)."""
    try:
        authorizer = event["requestContext"]["authorizer"]
        return authorizer.get("userId") or authorizer.get("principalId")
    except (KeyError, TypeError):
        return None


def converse_handler(event, context):
    """POST /rover/converse — open-ended conversational fallback for PhroverOperator's
    DialogAgent (PhroverKit). Only reached when the on-device Apple Foundation Model flags
    an utterance as needing escalation (small talk, general knowledge); navigation intents
    are parsed and dispatched entirely on-device and never hit this endpoint. See
    ClaudeDialogClient.swift in the sibling astral-sdk repo (swift/Sources/PhroverCloud)
    and eco/rover/docs/architecture.md.
    """
    if not get_user_id(event):
        return json_response(401, {"error": "Unauthorized"})

    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return json_response(400, {"error": "Invalid JSON"})

    utterance = (body.get("utterance") or "").strip()
    if not utterance:
        return json_response(400, {"error": "Missing utterance"})

    try:
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                "anthropic_version": ANTHROPIC_VERSION,
                "system": RAMP_AGENT_SYSTEM_PROMPT,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": utterance}]}
                ],
                "max_tokens": 200,
            }),
        )
        result = json.loads(response["body"].read())
        reply = "".join(
            block.get("text", "") for block in result.get("content", [])
            if block.get("type") == "text"
        ).strip()
    except Exception as e:
        return json_response(500, {"error": f"Bedrock error: {str(e)}"})

    return json_response(200, {"reply": reply or "Sorry, I didn't catch that."})


MISSION_AGENT_SYSTEM_PROMPT = """You are the cloud reasoning brain of a small autonomous \
ground rover (Phrover). You are called once per "think" step of an ongoing mission, not \
once per video frame. Each call may include a photo from the rover's forward camera, a \
list of objects an on-device detector currently recognizes (closed-set, e.g. "chair", \
"person"), the operator's most recent words, the rover's current pose, and the rover's \
memory of the mission so far (past utterances and the pose the rover was at when each was \
said, plus the pose where the mission started).

You must always call the `decide` tool with exactly one next action:
- navigate: drive toward a target. Use target_kind="imagePoint" with (x, y) normalized \
  image coordinates (0,0 = bottom-left, 1,1 = top-right) to point at ANYTHING you can see \
  in the photo, including open-vocabulary references the on-device detector can't name \
  (colors, "my backpack", "the plant by the window") — you do the visual grounding; the \
  rover's phone converts your point into a real-world location using its own depth sensor. \
  Use target_kind="worldPoint" with (x, y) taken directly from a remembered pose or a \
  remembered object in memory when the operator refers to somewhere already visited or \
  something already seen (e.g. "go home", "back to where we started", "the chair we \
  passed") — do not guess a worldPoint you have not actually seen in memory.
- explore: the target is probably in a part of the space you haven't seen. Pick an opening \
  id from the unexplored-openings list to drive there and look. Prefer unexplored openings; \
  if one you checked turned out to be a dead end (e.g. just a hallway), try the next.
- lookAround: rotate in place to search from where you are — use a smaller angle (around \
  1.57 radians / 90 degrees) for a partial scan and up to 6.28 radians / 360 degrees for a \
  full look-around.
- ask: ask a short clarifying question ONLY when you genuinely cannot proceed (multiple \
  plausible matches, or nothing matches and exploring/looking already failed). Do not ask \
  again immediately after an unanswered question — proceed best-effort instead.
- say: speak a short acknowledgement or progress update with no expectation of a reply.
- stop: the operator wants the rover to stop moving right now.
- done: the operator's request has been fully satisfied — including any later steps of \
  your plan, like returning after fetching something.
- claimRoom: ONLY when a team_context section is present below (a multi-rover mission). \
  Announce to your teammates that you're taking a specific room/area, using candidate_id \
  for the room's id. Claim rooms no one else has claimed yet; prefer ones closest to you. \
  If a teammate is marked no longer responding, their unclaimed rooms are up for grabs — \
  claim one instead of waiting. This is real shared-intent coordination: reason about it \
  yourself each tick from the team_context you're given, there is no separate allocation \
  system doing this for you.

Keep a short running mission plan for multi-step requests ("go to X, then come back"): \
whenever the plan changes, write the whole updated plan into updated_plan and mark \
finished steps done; omit updated_plan to keep the current plan. The plan you wrote is \
echoed back to you every step — trust it over re-deriving intent from scratch.

Never invent an object you cannot actually see in the photo. If no photo is attached, you \
cannot ground new visual references — rely on the on-device detector's list, memory, \
explore, or ask/lookAround instead. Never generate code.

Avoid repeating actions that aren't working: never repeat a full-circle lookAround from a \
pose where you already looked around and your pose hasn't changed since — a repeat scan \
from the same spot yields nothing new; explore an unexplored opening or navigate somewhere \
new instead. If you narrated an intention (e.g. "moving to opening_12 next"), issue that \
action on your very next call — check your recent-actions list to make sure you actually \
did what you said, instead of re-deciding from scratch. Report each anomaly exactly once: \
before flagging something you've spotted, check your plan and recent actions for an \
existing report of it. Watch your battery: when it's low relative to the distance back to \
the mission start pose, return and report before stranding yourself. When there are no \
unexplored openings left and nothing new is appearing, give a final report and choose done \
— do not keep rescanning."""

DECIDE_TOOL = {
    "name": "decide",
    "description": "Choose the rover's single next action.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["navigate", "explore", "lookAround", "ask", "say", "stop", "done", "claimRoom"],
            },
            "candidate_id": {
                "type": "string",
                "description": "Required when action is explore (the id of the opening to check, from the unexplored-openings list) or claimRoom (the id of the room/area to claim, from team_context.rooms).",
            },
            "updated_plan": {
                "type": "string",
                "description": "Your full rewritten mission plan whenever it changes (mark finished steps done). Omit to keep the current plan.",
            },
            "target_kind": {
                "type": "string",
                "enum": ["imagePoint", "worldPoint"],
                "description": "Required when action is navigate.",
            },
            "x": {
                "type": "number",
                "description": "imagePoint: normalized x in [0,1]. worldPoint: world-plane x copied from a remembered pose.",
            },
            "y": {
                "type": "number",
                "description": "imagePoint: normalized y in [0,1], bottom=0 top=1. worldPoint: world-plane y copied from a remembered pose.",
            },
            "angle": {
                "type": "number",
                "description": "Radians to rotate, required when action is lookAround. Counterclockwise positive.",
            },
            "text": {
                "type": "string",
                "description": "Required when action is ask (the question) or say (what to say).",
            },
            "reasoning": {
                "type": "string",
                "description": "One short sentence explaining the choice, for logging.",
            },
        },
        "required": ["action"],
    },
}


def _describe_mission(body):
    """Render the JSON mission context (see CloudBrain.swift's ActRequest for the exact
    wire shape) into text the model reads alongside the attached photo, if any."""
    lines = []

    utterance = body.get("utterance")
    if utterance:
        lines.append(f'Operator just said: "{utterance}"')

    plan = body.get("plan")
    if plan:
        lines.append(f"Current plan (as you last wrote it): {plan}")

    visible = body.get("visibleObjects") or []
    if visible:
        objs = ", ".join(
            f"{o.get('label')} at image point ({o.get('x', 0):.2f}, {o.get('y', 0):.2f})"
            for o in visible
        )
        lines.append(f"On-device detector currently sees: {objs}")
    else:
        lines.append("On-device detector sees nothing recognized right now.")

    pose = body.get("pose")
    if pose:
        lines.append(f"Current pose: x={pose.get('x', 0):.2f}, y={pose.get('y', 0):.2f}, yaw={pose.get('yaw', 0):.2f}")

    battery = body.get("batteryPercent")
    if battery is not None:
        lines.append(f"Battery: {battery:.0f}%")

    lines.append(f"Navigation state: {body.get('navState', 'idle')}")

    recent_actions = body.get("recentActions") or []
    if recent_actions:
        lines.append(
            "Your recent actions, oldest→newest: " + " | ".join(recent_actions)
        )

    memory = body.get("memory") or {}
    turns = memory.get("turns") or []
    if turns:
        recent = "; ".join(
            f'"{t.get("utterance")}" at pose x={t["pose"]["x"]:.2f}, y={t["pose"]["y"]:.2f}'
            for t in turns[-5:]
        )
        lines.append(f"Recent memory: {recent}")

    remembered = memory.get("rememberedObjects") or []
    if remembered:
        objs = ", ".join(
            f"{o.get('label')} at world ({o.get('x', 0):.2f}, {o.get('y', 0):.2f})"
            for o in remembered
        )
        lines.append(f"Objects remembered from earlier (may not be visible now; usable as worldPoint): {objs}")

    candidates = body.get("explorationCandidates") or []
    if candidates:
        openings = "; ".join(
            f"{c.get('id')} at ({c.get('x', 0):.2f}, {c.get('y', 0):.2f}), "
            f"{c.get('widthMeters', 0):.1f}m wide [{c.get('status', 'unexplored')}]"
            for c in candidates
        )
        lines.append(f"Openings to unexplored space: {openings}")

    start_pose = memory.get("missionStartPose")
    if start_pose:
        lines.append(f"Mission start pose (use for 'home'/'back'): x={start_pose['x']:.2f}, y={start_pose['y']:.2f}")

    if body.get("lastAnswerWasInconclusive"):
        lines.append("Your last question went unanswered — proceed with your best guess instead of asking again.")

    team = body.get("teamContext")
    if team:
        rooms = team.get("rooms") or []
        rooms_str = "; ".join(
            f"{r.get('id')} at ({r.get('x', 0):.2f}, {r.get('y', 0):.2f})" for r in rooms
        )
        teammates = team.get("teammates") or []
        mates_str = "; ".join(
            f"{t.get('id')} ({'alive' if t.get('alive') else 'NOT RESPONDING'}, "
            f"claimed: {', '.join(t.get('claimedRoomIds') or []) or 'none yet'})"
            for t in teammates
        )
        lines.append(
            f"TEAM MISSION — you are rover '{team.get('myId')}'. "
            f"Claimable rooms/areas: {rooms_str or 'none'}. "
            f"Teammates: {mates_str or 'none known yet'}. "
            "Claim unclaimed rooms yourself (see claimRoom above); if a teammate stops "
            "responding, their unclaimed rooms are yours to pick up."
        )

    return "\n".join(lines)


def act_handler(event, context):
    """POST /rover/act — vision + tool-use mission brain for MissionAgent (PhroverKit).
    Sibling of /rover/converse: this route drives ACTIONS (navigate/lookAround/ask/say/
    stop/done), not just talk, and is the "cloud primary" half of the hybrid brain — see
    CloudBrain.swift / HybridBrain.swift in the sibling astral-sdk repo
    (swift/Sources/PhroverCloud/Cloud), which this request/response shape mirrors exactly.
    """
    if not get_user_id(event):
        return json_response(401, {"error": "Unauthorized"})

    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return json_response(400, {"error": "Invalid JSON"})

    content = []
    frame_b64 = body.get("frameJPEGBase64")
    if frame_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64},
        })
    content.append({"type": "text", "text": _describe_mission(body)})

    try:
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                "anthropic_version": ANTHROPIC_VERSION,
                "system": MISSION_AGENT_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": content}],
                "tools": [DECIDE_TOOL],
                "tool_choice": {"type": "tool", "name": "decide"},
                "max_tokens": 700,
            }),
        )
        result = json.loads(response["body"].read())
        decision = next(
            (block["input"] for block in result.get("content", [])
             if block.get("type") == "tool_use" and block.get("name") == "decide"),
            None,
        )
    except Exception as e:
        return json_response(500, {"error": f"Bedrock error: {str(e)}"})

    if not decision or "action" not in decision:
        return json_response(200, {"action": "say", "text": "Sorry, I'm not sure what to do next."})

    return json_response(200, {
        "action": decision.get("action"),
        "targetKind": decision.get("target_kind"),
        "x": decision.get("x"),
        "y": decision.get("y"),
        "angle": decision.get("angle"),
        "text": decision.get("text"),
        "candidateId": decision.get("candidate_id"),
        "updatedPlan": decision.get("updated_plan"),
    })
