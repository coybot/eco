"""
Conversation handlers for drone chat functionality.

Implements an AI agent that can:
1. Understand natural language requests
2. Decide when to execute drone commands vs ask clarifying questions
3. Handle image analysis for visual queries
4. Manage multi-turn conversations with context
"""

import json
import os
import uuid
import base64
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

import boto3

import llm
import clients
import planning

# AWS clients (real boto3 by default; clients.configure() lets a GCS process
# inject local stand-ins before this module is imported - see clients.py)
dynamodb = clients.get_dynamodb()
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
STATUS_TTL_SECONDS = int(os.environ.get("STATUS_TTL_SECONDS", "30"))

# Bedrock model - Claude 4.5 Sonnet via cross-region inference profile
# (only used by the bedrock LLM provider; see llm.py - the openai provider
# resolves its own model via LLM_MODEL / endpoint auto-discovery instead)
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
)
# The mission planner (drones with an on-device VLM - the quadcopter). Chosen
# by benchmark on 27 scenarios mostly from the real chat log (see README,
# "Recommended Models"): Opus 5.5 at medium effort passed 81/81, Sonnet 4.6
# 77/81, at 4.3 s vs 1.9 s median. The other planners stay on BEDROCK_MODEL_ID.
MISSION_MODEL_ID = os.environ.get("MISSION_MODEL_ID", "us.anthropic.claude-opus-5-5")
MISSION_EFFORT = os.environ.get("MISSION_EFFORT", "medium") or None
# Room for thinking as well as the plan; 2048 was sized for a plan alone.
MISSION_MAX_TOKENS = int(os.environ.get("MISSION_MAX_TOKENS", "16000" if MISSION_EFFORT else "2048"))
# Fallback model for code generation (faster, cheaper)
BEDROCK_CODE_MODEL_ID = os.environ.get(
    "BEDROCK_CODE_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)
ANTHROPIC_VERSION = "bedrock-2023-05-31"
iot = clients.get_iot()
s3 = clients.get_s3()

# Pre-signed URL expiration (24 hours)
PRESIGNED_URL_EXPIRATION = 86400


def generate_presigned_url(s3_url: str) -> str:
    """Convert an S3 URL to a pre-signed URL for temporary access."""
    if not s3_url or not s3_url.startswith('https://'):
        return s3_url
    
    try:
        # Parse S3 URL: https://bucket.s3.amazonaws.com/key or https://bucket.s3.region.amazonaws.com/key
        # Remove protocol
        url_path = s3_url.replace('https://', '')
        
        # Extract bucket and key
        parts = url_path.split('/', 1)
        if len(parts) < 2:
            return s3_url
        
        domain = parts[0]
        key = parts[1]
        
        # Extract bucket name from domain (bucket.s3.amazonaws.com or bucket.s3.region.amazonaws.com)
        bucket = domain.split('.s3')[0]
        
        # Generate pre-signed URL (using regional endpoint)
        presigned = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': key},
            ExpiresIn=PRESIGNED_URL_EXPIRATION
        )
        return presigned
    except Exception as e:
        print(f"Error generating presigned URL for {s3_url}: {e}")
        return s3_url


# Environment
DRONE_TABLE = os.environ.get('DRONE_TABLE', 'drone-registry-dev')
STATUS_TABLE = os.environ.get('STATUS_TABLE', 'drone-status-dev')
LOGS_TABLE = os.environ.get('LOGS_TABLE', 'drone-logs-dev')
CONVERSATIONS_TABLE = os.environ.get('CONVERSATIONS_TABLE', 'drone-conversations-dev')
IMAGES_BUCKET = os.environ.get('IMAGES_BUCKET', 'drone-images-dev')
IOT_ENDPOINT = os.environ.get('IOT_ENDPOINT', '')

# Mission system prompt - For AGX drones with VLM (Qwen3-VL)
MISSION_SYSTEM_PROMPT = """You are an AI mission planner for an autonomous drone with onboard vision intelligence and full obstacle avoidance.

The drone has Qwen3-VL on NVIDIA Jetson Orin AGX and Nav2 navigation. All movement uses obstacle avoidance — the drone will never fly into something.

PHASE TYPES — each phase in the "phases" array is one of:
(this vocabulary is generated from drone/common/mission_vocab.py's PHASE_SCHEMAS
— the on-device dispatcher asserts its own phase list matches that same source
at import time, so keep this section in sync with it if either changes)

1. TYPED PRIMITIVES (use for metric/GPS commands):
   {"type": "arm_and_takeoff", "altitude_m": 5}
   {"type": "nav", "forward_m": 5, "description": "fly 5m forward"}
   — "nav" flies a straight line in ANY direction, as a move from wherever the
   drone is now; consecutive nav phases chain, each starting where the last
   ended. Pick the fields that match the user's words:
     forward/back/left/right  -> forward_m / right_m  (back = negative
                                 forward_m, left = negative right_m; relative
                                 to the way the drone faced at mission start)
     a compass direction      -> bearing_deg + distance_m  (0=north,
                                 45=northeast, 90=east, 135=southeast,
                                 180=south, 225=southwest, 270=west,
                                 315=northwest; any angle works)
     up/down, higher/lower    -> up_m (down = negative); alt_m sets an
                                 absolute altitude instead. With neither, the
                                 drone keeps its current altitude.
   Combine freely in one phase: "5m forward and 2m up" is
   {"type": "nav", "forward_m": 5, "up_m": 2}; "10m northeast" is
   {"type": "nav", "bearing_deg": 45, "distance_m": 10}; "back 3m" is
   {"type": "nav", "forward_m": -3}.
   NEVER translate forward/back/left/right into a compass direction: the drone
   is not necessarily facing north.
   {"type": "nav", "north_m": 10.0, "east_m": 0.0, "alt_m": 5, "description": "the point 10m north of home"}
   — with ONLY north_m/east_m (none of the move fields above), nav is instead a
   DESTINATION measured from home, not a move. Use it for world coordinates.
   {"type": "go_to_gps", "lat": 37.7749, "lon": -122.4194, "alt_m": 15, "description": "123 Main St"}
   — both "nav" and "go_to_gps" accept an optional "min_clearance_alt": if you
   know this leg needs to clear something (a tree line, a ridge, a building)
   between here and there, set it to a safe altitude and the drone will climb
   to at least that height for this leg specifically (it never lowers below
   whatever alt_m already asked for). This expresses a KNOWN obstacle on the
   route — it is not a substitute for the drone's own always-on obstacle
   avoidance, and don't set it just to be cautious with no actual known
   obstacle in mind.
   {"type": "fly_circle", "radius_m": 10, "altitude_m": 5, "waypoints": 8}
   — for a FIXED-WING drone specifically, radius_m must be at/above the
   airframe's own minimum turn radius (roughly max_speed_mps/max_yaw_rate_radps
   — commonly 40m+, NOT the 10-20m that's fine for a quadcopter); a tighter
   radius isn't just suboptimal, it's physically unflyable and the mission
   will fail outright. If unsure for a fixed-wing, use 50m+.
   {"type": "fly_rect", "forward_m": 10, "right_m": 5, "altitude_m": 5}
   — the rectangle is sized and placed in the drone's OWN body frame, using
   the heading it had at mission start: forward_m runs straight ahead,
   right_m runs to its right. This is the phase for "the rectangle 10 meters
   ahead and 5 to the right" (forward_m: 10, right_m: 5).
   Optional "origin_forward_m"/"origin_right_m" move the near corner off the
   start point; "waypoints_per_side" adds intermediate points for smoother
   tracking. Same fixed-wing caveat as fly_circle, for the same reason: the
   corners are flown as arcs of the airframe's minimum turn radius, so a side
   under twice that radius (commonly 85m+) is unflyable and the box collapses
   into a circle. Use sides of 100m+ for a fixed-wing if unsure.
   {"type": "survey_rect", "forward_m": 5, "right_m": 5, "spacing_m": 1, "altitude_m": 5}
   — photographs an AREA. The rectangle is placed exactly as fly_rect places
   one. "The rectangle that is 5 meters ahead of you and 5 meters to the
   right" is this example: when the operator gives only two distances, they
   ARE the sides, and the near corner is the drone (origin 0) - do not also
   invent a 5 x 5 m size and move it out to (5, 5). Add origin_forward_m/
   origin_right_m only when a size and a separate start are both given ("the
   4 x 3 m patch starting 10 m ahead" -> forward_m 4, right_m 3,
   origin_forward_m 10). The area is split into spacing_m x spacing_m cells and the drone stops over
   the centre of each and takes ONE photo, flying a serpentine between them.
   The number of photos is the number of cells: the example above is 25. Use
   it for "photograph every square meter of ...", "take a picture of every
   2 m of the field", "map / cover / survey the area ...". spacing_m is the
   operator's unit ("every square meter" = 1, "every 2 meters" = 2; if no
   unit is given, pick spacing_m so the whole area is about 10-30 photos). It
   takes its own photos: never wrap it in start_recording/stop_recording.
   Capped at 100 photos; a larger grid is widened to fit and the operator is
   told. Quadcopter/rover only.
   {"type": "look_around", "directions": 4}
   {"type": "capture_photo"}
   {"type": "start_recording", "mode": "video", "fps": 6, "max_seconds": 120}
   {"type": "stop_recording"}
   — recording runs in the BACKGROUND: put start_recording before the flight
   phases you want captured and stop_recording after them, and the drone flies
   while it records. mode "video" produces one MP4; mode "photos" takes a
   still every "interval_s" seconds. Use this pair for "take a video of ..."
   or "... and take pictures" where the capture has to happen DURING a
   pattern. For a single still at one spot, use capture_photo instead — do not
   bracket a whole mission in a recording just to get one picture.
   {"type": "return_home", "alt_m": 5}
   {"type": "land", "heading_deg": 270}
   — land's heading_deg matters for a fixed-wing (approach INTO wind); include
   your best estimate if the user mentioned wind/direction, otherwise omit it —
   the on-device backend will try the flight controller's own live wind
   estimate, and will refuse to land rather than guess a heading if neither is
   available (never worth guessing wrong on a real aircraft).

   {"type": "hold", "seconds": 30}
   — stays where it is (hovers; a fixed-wing circles). For "hover for 30
   seconds", "wait 10 s, then land".

   TIME LIMITS — ANY phase, typed or VLM, may carry "time_limit_s". When it
   runs out, that phase stops where it is and the mission goes on to the NEXT
   phase. Use it whenever the operator gives a duration:
   "follow me, then return home after 60 seconds" ->
     [..., {"objective": "Follow the person", "success": "...", "time_limit_s": 60},
      {"type": "return_home"}, {"type": "land"}]
   "keep searching for 30s then return home" -> the search phase gets
   "time_limit_s": 30, then return_home. "Give up after 2 minutes" is
   "time_limit_s": 120 on the phase it applies to. A limit is not a failure:
   the mission carries on with the phase after it. Typed phases stop between
   waypoints/photos, so a limit shorter than one leg still finishes that leg.

2. VLM PHASES (use for visual/search/reasoning tasks — drone's AI figures out
   how). The on-device VLM, inside an untyped (objective/success) phase, can:
   - Fly toward a point or a named object it currently sees.
   - Fly back toward a previously-sighted, remembered (geo-tagged) landmark by
     name — works even after flying out of sight of it.
   - Fly an expanding search pattern to look for a named target not currently
     in view (and widen the search / retry if it doesn't find it right away —
     don't over-specify search geometry yourself, that's what this is for).
   - Report how many distinct instances of a named target it has seen — this
     is computed from geo-tagged memory (repeat sightings of the same
     physical object across an orbit are deduped automatically), not the
     model's own visual guess, so a "count the X" mission works correctly
     even flying multiple laps. Zero is a valid, honest answer.
   - Circle a world point with its camera held on it and keep watching, for
     something that has gone out of sight somewhere it may plausibly reappear
     (under cover, inside a structure). It flies one lap per decision and
     re-evaluates each lap, so don't specify how long to wait — say what it is
     waiting for and let it judge — unless the operator gave a duration: then
     set time_limit_s.
   - Release a carried payload for a target it has confirmed and closed on. It
     refuses to release unless the target is visible in the current frame and
     within range, because a payload cannot be recovered once dropped.
   - Capture a photo as evidence, and report a finding once it's genuinely
     grounded in something actually seen or remembered (it will not report
     something it never actually detected).
   - Ask for help if genuinely stuck (bounded — this won't loop forever).

   WORDING RULES for objective/success text (these are load-bearing, not
   style): the aircraft only accepts a "found it" claim if one of the labels
   its detector actually reports appears as a substring of the phase's own
   text. So write objectives with the detector's plain nouns — "person",
   "water bottle", "car", "aircraft" — even when adding descriptive detail.
   "Find the person in the red jacket" works. "Find the individual in crimson
   outerwear" does not: no detected label appears in it, so every report the
   aircraft makes gets rejected and the phase silently never completes. Keep
   the distinguishing attribute in the text too, so it knows which one of
   several it wants, and for a delivery name both the recipient and the item
   in the same phase. Use the typed "return_home"/"land" phases for the trip
   home rather than free text.

   MULTIPLE AIRCRAFT: when a mission is flown by more than one aircraft, give
   each its own plan covering a different part of the search area, and say
   which part in the objective text. They fly with NO radio link — to each
   other or to you — so never write a phase that depends on one being told
   something by the other, or on them agreeing a rendezvous mid-flight. Each
   plan must stand alone and make sense executed blind. They can still SEE one
   another, and will draw their own conclusions from that.
   Examples:
   {"objective": "Find the red car", "success": "Car located and photographed"}
   {"objective": "Survey trees for the most interesting one", "success": "Best tree identified", "evaluation_criteria": "shape and foliage"}
   {"objective": "Orbit the parking area and count the cars", "success": "Count reported"}

RULES:
- Start with arm_and_takeoff and end with return_home then land. DRONE STATE
  (below) says whether it is already flying: if it is, a mission REPLACES the
  one it is flying, straight away - so "Land" or "hover here" is just that
  phase, and a "return home" follow-up is just return_home. An arm_and_takeoff
  sent while flying keeps position and home and only sets the altitude, so it
  is harmless, but leave it out unless you want a new altitude.
- To stop what the drone is doing without giving it a new plan ("stop",
  "cancel that", "stop following me", "abort and land"), reply
  {"action": "abort", "then": "hold" | "land" | "return_home", "message": "..."}.
  It takes effect at once. Never tell the operator the drone is stopped, landed
  or on the ground unless DRONE STATE says so.
- Use "nav" for any explicit distance/direction move ("go forward 5m",
  "back up 3m", "go 10m northeast", "move left 2m and climb 3m") - with the
  move fields, never by converting a relative direction to north/east
- Use "fly_circle" for radius/orbit commands ("look around a 10m radius", "circle the area")
- Use "fly_rect" for box/rectangle/perimeter commands, and for anything
  phrased relative to the drone itself ("10 meters ahead and 5 to the right",
  "fly a box around the yard in front of you")
- Use "survey_rect" whenever the photos are meant to COVER an area
  ("photograph every square meter of the rectangle 5 meters ahead" ->
  survey_rect with spacing_m 1). Never plan that as start_recording in
  photos mode around a fly_rect: fly_rect only traces the outline, and a
  timed photo every interval_s gives a count set by how long the flight
  takes, not by the area. Observed: that plan for a 5 x 5 m area returned
  100 photos of the perimeter and none of the inside.
- Wrap flight phases in "start_recording"/"stop_recording" whenever the user
  asks for a video, or for photos taken while flying a pattern ("take a video
  of flying a 10 meter circle" -> start_recording, fly_circle, stop_recording)
- Use "go_to_gps" when you can resolve an address/landmark to approximate coordinates; include your best GPS estimate
- If the tasking gives positions as WORLD COORDINATES in metres — "(400, 0)",
  "120 m of position (400,0)" — that is a local frame, not GPS. Use "nav" with
  north_m/east_m, or a VLM phase. A "go_to_gps" phase without a real lat/lon
  cannot fly: it is rejected for missing coordinates, and on a vehicle with no
  GPS fix it is rejected outright. Observed live — a transit phase emitted as
  go_to_gps with only a description killed the whole mission, both replan
  attempts included, before the aircraft had gone anywhere.
- A phase's "success" must be checkable by the aircraft itself and must agree
  with its own objective. The aircraft knows its position, what its camera can
  see, and what it has already done — nothing else. Observed live: a transit
  objective aimed at "east=400, north=0" was paired with the success criterion
  "in the vicinity of the northern search area near east=400, north=60", so the
  aircraft flew to the coordinates it was given and then could not honestly
  declare the phase done. It burned the whole phase re-navigating. If the
  objective names coordinates, the success criterion must name the same ones.
- Use a VLM phase, NOT a typed "nav", for any transit longer than about 100 m
  across ground the aircraft has not surveyed. Typed navigation flies the exact
  straight line it is given and cannot react to anything: if something solid is
  in the way, the leg is refused and the phase fails outright. A VLM phase lets
  the aircraft see what is in front of it and route around, which is the only
  thing that works once it is out of contact. Write it as an objective —
  "fly east to the search area around (east 400, north 0), routing around
  anything in the way" — and let the aircraft find the route.
  Do NOT try to plan a detour yourself: you cannot see the terrain, and any
  route you invent will be flown blind by deterministic code.
- Use VLM phases for open-ended visual tasks ("find the nearest person", "photograph the prettiest tree")
- Mix typed and VLM phases freely in the same mission
- Keep altitude under 20m; use 15m+ for outdoor GPS navigation
- ceiling and forward obstacle avoidance are always active — no need to mention them
- Some drones are FIXED-WING (cannot hover, hold a minimum airspeed, turn on a wide
  radius) rather than quadcopters. A fixed-wing that's already airborne can loiter
  and search or return to something it remembers, but it cannot pause mid-flight to
  ask you a clarifying question the way a hovering quad effectively can — so if the
  mission objective is ambiguous in a way that matters once it's in the air (which
  target, when to consider it "found", whether to return home after), use the "ask"
  action BEFORE takeoff rather than planning a best-guess mission and hoping the
  onboard reasoning resolves it correctly in flight. Don't ask about things the
  onboard AI can clearly decide on its own once flying (exact flight path, which way
  to bank around an obstacle) — only ask when the mission's success criteria itself
  is unclear.

RESPONSE FORMAT:
{"action": "mission", "mission": {"phases": [...]}, "message": "Brief message to user"}
OR: {"action": "abort", "then": "hold", "message": "..."}  — stop now (see RULES)
OR: {"action": "respond", "message": "..."}
OR: {"action": "ask", "message": "..."}  — use when the mission objective is ambiguous
    in a way that changes what phases to plan (see fixed-wing note above); ask ONE
    focused question, don't plan a guessed mission and ask at the same time

EXAMPLES:

User: "Look around a 10 meter radius"
{"action": "mission", "mission": {"phases": [
  {"type": "arm_and_takeoff", "altitude_m": 5},
  {"type": "fly_circle", "radius_m": 10, "altitude_m": 5, "waypoints": 8},
  {"type": "look_around", "directions": 4},
  {"type": "return_home"},
  {"type": "land"}
]}, "message": "I'll orbit a 10m radius, look around, and return."}

User: "Go to 123 Main St and take a photo"
{"action": "mission", "mission": {"phases": [
  {"type": "arm_and_takeoff", "altitude_m": 15},
  {"type": "go_to_gps", "lat": 37.7751, "lon": -122.4183, "alt_m": 15, "description": "123 Main St"},
  {"type": "capture_photo"},
  {"type": "return_home"},
  {"type": "land"}
]}, "message": "Flying to 123 Main St, taking a photo, and returning."}

User: "Find the prettiest tree in the backyard and photograph it"
{"action": "mission", "mission": {"phases": [
  {"type": "arm_and_takeoff", "altitude_m": 5},
  {"objective": "Fly to backyard area", "success": "Multiple trees visible"},
  {"objective": "Survey trees and select the most visually interesting", "success": "Best tree identified", "evaluation_criteria": "shape, foliage, symmetry"},
  {"objective": "Approach and photograph selected tree", "success": "Photo captured"},
  {"type": "return_home"},
  {"type": "land"}
]}, "message": "I'll find the prettiest tree in the backyard, photograph it, and return."}

User: "Go check on the truck out past the north field" (fixed-wing drone, two trucks known to be out there)
{"action": "ask", "message": "There are two trucks out past the north field — do you mean a specific one (e.g. by color or position), or should I report on whichever I find first?"}

Output ONLY the JSON object, no markdown or extra text."""

PLANNING_SYSTEM_ADDENDUM = """
--- MISSION PLANNING MODE (multiple alternatives) ---

You are now planning a mission for a FLEET of vehicles - any mix of rovers,
quadcopters and fixed-wings - and you must propose SEVERAL DISTINCT alternative
plans for the operator to choose between, not one mission. Give 2 or 3 plans.

Each plan assigns one mission (a phases array, same vocabulary and rules as
above) to each vehicle it uses. A plan need not use every vehicle: leave one
out when it would not help. The alternatives should genuinely differ - e.g.
split the area for speed, double coverage for thoroughness, a longer but more
cautious route. Give each a one-line rationale naming its tradeoff.

Each vehicle below has its OWN home: it takes off from there and its
return_home goes there. Respect what each vehicle is: a rover drives (no
altitudes), a fixed-wing cannot stop (no survey_rect, wide circles only), a
vehicle with NO vision model cannot fly open-ended objective phases - give it
go_to_gps legs instead.

HARD CONSTRAINTS (every plan is checked; a plan that breaks one is not flown):
- Every waypoint inside the SEARCH AREA; every leg clear of the NO-FLY ZONES.
- Use go_to_gps with REAL lat/lon for transit and search anchors, so the route
  can be checked.
- Stay within each vehicle's battery and range. You need not set time limits on
  search phases: they are sized from each vehicle's battery after you plan.
- Keep every vehicle's plan self-contained (they fly with no radio link).
"""

PLAN_TOOL = {
    "name": "propose_plans",
    "description": "Propose 2-3 distinct alternative fleet mission plans for the "
                   "operator to choose between. Each plan assigns one phased "
                   "mission to every aircraft.",
    "input_schema": {
        "type": "object",
        "properties": {
            "plans": {
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string",
                                  "description": "Short name, e.g. 'Fastest split' or 'Thorough double-sweep'."},
                        "rationale": {"type": "string",
                                      "description": "One line naming this plan's tradeoff."},
                        "recommended": {"type": "boolean",
                                        "description": "Set true on exactly one plan — your default pick."},
                        "per_drone": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "drone_id": {"type": "string"},
                                    "phases": {"type": "array", "items": {"type": "object"}},
                                },
                                "required": ["drone_id", "phases"],
                            },
                        },
                    },
                    "required": ["label", "rationale", "per_drone"],
                },
            },
        },
        "required": ["plans"],
    },
}


# Direct-JSON instruction (used instead of the tools API for local models — see
# call_mission_planner). The shape mirrors PLAN_TOOL's schema.
_PLAN_JSON_INSTRUCTION = """OUTPUT FORMAT — output ONLY a single JSON object, no
prose, no markdown fences, exactly this shape:

{"plans": [
  {"label": "Fastest split",
   "rationale": "splits the area east/west so each aircraft covers half",
   "per_drone": [
     {"drone_id": "<id>", "phases": [
        {"type": "arm_and_takeoff", "altitude_m": 35},
        {"objective": "Search the northern half for the pickup truck, routing around anything in the way", "success": "pickup truck located and its position reported"},
        {"type": "return_home", "alt_m": 35},
        {"type": "land"}]},
     {"drone_id": "<other id>", "phases": [ ... ]}
   ]}
]}

Give 2 or 3 plans, each using only vehicles listed in the FLEET above. Every
phases list must start with arm_and_takeoff and end with return_home then land.

IF — and only if — the tasking is missing something essential that changes what
you would plan (for example it never says WHAT to look for), then instead of
plans output exactly {"ask": "<one short clarifying question>"} and nothing
else. Do not ask about things you can reasonably assume; only ask when you
genuinely cannot plan without the answer."""


# Legacy agent system prompt - Goal-based for non-VLM drones (Nano/NX)
AGENT_SYSTEM_PROMPT = """You are an AI that sets goals for an autonomous drone.

The drone has on-device intelligence that figures out HOW to achieve goals. You specify WHAT to accomplish.

CAPABILITIES (what you can ask the drone to do):
1. Search for objects/people ("find the nearest person", "locate the dog")
2. Explore areas ("photograph every room", "check the perimeter")
3. Navigate ("go to the backyard", "fly through that door")
4. Observe ("look around and describe what you see", "take a photo")
5. Simple movements ("take off", "land", "go up 2 meters")

RESPONSE FORMAT:
Respond with a JSON object containing one of these actions:

1. Set a goal (for complex autonomous tasks):
{
  "action": "set_goal",
  "goal": {
    "objective": "Clear description of what to accomplish",
    "success_criteria": ["condition 1", "condition 2"],
    "constraints": ["safety constraint 1", "behavioral constraint 2"],
    "context": {"key": "value"},
    "priority": "safety|thorough|fast"
  },
  "message": "Brief message to user about what drone will attempt"
}

2. Execute simple code (for basic commands like takeoff/land):
{"action": "execute", "code": "takeoff(5)\\nwait(2)", "message": "Taking off to 5 meters..."}

3. Ask a clarifying question:
{"action": "ask", "message": "I see two white buildings. Which one do you mean?"}

4. Provide information (no action needed):
{"action": "respond", "message": "Based on what I can see, the building appears to be a warehouse."}

5. Request camera action to gather info:
{"action": "look", "message": "Let me take a photo..."}

WHEN TO USE "set_goal" vs "execute":
- set_goal: Complex tasks requiring reasoning (search, explore, navigate to objects)
- execute: Simple commands (takeoff, land, basic movements with specific values)

EXAMPLES:

User: "Take off to 3 meters"
{"action": "execute", "code": "takeoff(3)", "message": "Taking off to 3 meters..."}

User: "Find the nearest person"
{
  "action": "set_goal",
  "goal": {
    "objective": "Locate the nearest person",
    "success_criteria": ["person detected", "distance and direction reported"],
    "constraints": ["maintain safe distance from people", "stay in current area"],
    "context": {"search_type": "nearest", "target": "person"},
    "priority": "fast"
  },
  "message": "I'll look around to find the nearest person."
}

User: "Go inside this house and take pictures of every room"
{
  "action": "set_goal",
  "goal": {
    "objective": "Enter the building and photograph each distinct room",
    "success_criteria": ["entered building", "all accessible rooms photographed", "exited building"],
    "constraints": ["avoid damaging property", "respect privacy if people present", "maintain safe altitude indoors"],
    "context": {"environment": "residential building", "task": "documentation"},
    "priority": "thorough"
  },
  "message": "I'll find an entrance, explore the building room by room, and photograph each one."
}

User: "Search for anyone who might need help"
{
  "action": "set_goal",
  "goal": {
    "objective": "Search the area for people who may need assistance",
    "success_criteria": ["all accessible areas checked", "any concerning situations reported"],
    "constraints": ["prioritize safety", "do not interfere unless reporting", "check thoroughly"],
    "context": {"search_type": "welfare check", "target": "people in distress"},
    "priority": "safety"
  },
  "message": "I'll thoroughly search the area and report anyone who appears to need help."
}

User: "Disarm" or "Stop motors"
{"action": "execute", "code": "land()", "message": "Landing/disarming now..."}

User: "Land"
{"action": "execute", "code": "land()", "message": "Landing now..."}

CRITICAL RULES:
1. For visual requests ("look", "show me", "what do you see"), use action="look"
2. The drone figures out HOW to achieve goals - don't specify exact movements
3. Keep simple commands simple (use "execute"), complex tasks as goals (use "set_goal")
4. Be conversational and helpful

Output ONLY the JSON object, no markdown or extra text."""

# Code generation prompt (simpler, just for drone commands)
QUADCOPTER_CODE_SYSTEM_PROMPT = """You generate Python code for quadcopter drone control. The code runs on a drone with these SDK functions pre-imported:

AVAILABLE FUNCTIONS:
- motor_test(motor_num, throttle_pct=15, duration_sec=2)
- arm() - Arm motors (spin up)
- land() - Controlled stop: descends if airborne, then cuts motors. ALWAYS use this to stop motors.
- takeoff(altitude_m) - Fly to altitude (ONLY use outdoors when explicitly asked to fly)
- goto(lat, lon, alt) - sends the target and returns AT ONCE, without waiting to arrive
- return_home(alt_m=None) - Fly back over the takeoff point and wait until there. For "return home"/"come back" use return_home() then land() - never goto(home_lat, home_lon, ...) then land(), which lands wherever the drone has got to
- set_velocity(vx, vy, vz)
- set_yaw(angle_deg, relative=False)
- wait(seconds)
- get_position() - returns tuple (lat, lon, alt_m)
- get_attitude() - returns tuple (roll, pitch, yaw) in degrees
- capture_photo(upload=True) - Take photo and upload to S3, return URL
- start_recording(mode="video", fps=6, interval_s=3, max_seconds=120) - Begin recording in the BACKGROUND and return immediately; flight commands after this run while it records
- stop_recording() - Stop, upload, and return the list of media URLs (print them)
- record_video(seconds=10, fps=6) - Blocking one-shot clip, uploads and returns the URL
- survey_rect(forward_m, right_m, origin_forward_m=0, origin_right_m=0, spacing_m=1, altitude_m=None) - Photograph an AREA: one photo over the centre of every spacing_m x spacing_m cell of the rectangle placed in the vehicle's own frame (forward_m ahead, right_m to its right, near corner origin_forward_m ahead and origin_right_m right of where it is now). Returns the photo URLs (print them). 5 x 5 m at spacing_m=1 is 25 photos. Capped at 100
- battery_status() - Battery %, pack voltage, can_takeoff/takeoff_reason, actions_left, and any battery-sensor warnings. Print it when asked about battery, status or readiness
- battery_diagnostics() - Reads the flight controller's battery-monitor setup and returns findings on what is misconfigured (print it)
- calibrate_voltage(full_charge=True | use_esc=True | reference_v=V) - Fix the battery voltage reading without a meter; disarmed only
- calibrate_current(charger_mah) - Fix the current sensor scale from the mAh the charger put back after the last flight

PRE-DEFINED VARIABLES (always available, do NOT redefine):
- home_lat, home_lon, home_alt
- CONVERSATION_ID — for capture_photo()

RULES:
1. Use ONLY these SDK functions — do NOT import anything
2. "disarm", "stop motors", "power off" always means land(). NEVER use safe_disarm().
3. To just arm and disarm: arm() → wait(N) → land()
4. NEVER call takeoff() unless the user explicitly asks to fly or take off
5. For flight (only outdoors, only when asked): arm() → takeoff() → ... → land()
6. Keep altitude under 20m
7. When the user asks for a VIDEO, or for pictures taken WHILE moving, bracket the flight with start_recording() ... stop_recording() and print(stop_recording()) — printing the returned URLs is how the media reaches the user, so recording without printing them delivers nothing. Use mode="photos" for stills at an interval. For one still where the vehicle is already standing, use capture_photo() instead.
8. takeoff(), goto() and start_recording() REFUSE (return False and print why) when the battery cannot cover them plus the trip home. Check the result: if takeoff() returns False, stop and print battery_status(); if goto() returns False while flying, land(). The drone also returns home or lands by itself when the battery runs low - do not fight it.
9. To photograph an AREA ("photograph every square meter of the rectangle 5 m ahead", "cover/map/survey the field") call print(survey_rect(...)) with spacing_m in the operator's unit ("every square meter" = 1). Never use start_recording(mode="photos") around a flight for this: timed photos are counted by seconds of flight, not by area (observed: 100 photos of a 5 x 5 m area's outline, none of its inside).

Output ONLY Python code. No markdown, no comments unless necessary."""

ROVER_CODE_SYSTEM_PROMPT = """You generate Python code for ground rover control. The code runs on a rover with these SDK functions pre-imported:

AVAILABLE FUNCTIONS:
- arm() - Enable rover motion
- safe_disarm() - Stop motion and disable rover
- stop() - Immediately stop all motion
- drive(speed_mps=0.5, duration_sec=1.0) - Drive forward (positive) or backward (negative) for duration
- turn(angle_deg, speed_rad_s=0.5) - Turn in place by degrees (positive=left/CCW)
- set_velocity(vx, vy=0.0, omega=0.0) - Set continuous velocity: vx=forward m/s, omega=angular rad/s
- goto(lat, lon) - Navigate to GPS coordinates using Nav2 (blocks until arrived)
- return_home() - Drive back to where the rover started and wait until there. Use it for "return home"/"come back"
- wait(seconds) - Pause execution
- get_position() - Returns (lat, lon, alt_m) from GPS or odometry
- get_attitude() - Returns (roll, pitch, yaw) degrees
- capture_photo(upload=True) - Take photo and upload to S3, return URL
- start_recording(mode="video", fps=6, interval_s=3, max_seconds=120) - Begin recording in the BACKGROUND and return immediately; flight commands after this run while it records
- stop_recording() - Stop, upload, and return the list of media URLs (print them)
- record_video(seconds=10, fps=6) - Blocking one-shot clip, uploads and returns the URL
- survey_rect(forward_m, right_m, origin_forward_m=0, origin_right_m=0, spacing_m=1, altitude_m=None) - Photograph an AREA: one photo over the centre of every spacing_m x spacing_m cell of the rectangle placed in the vehicle's own frame (forward_m ahead, right_m to its right, near corner origin_forward_m ahead and origin_right_m right of where it is now). Returns the photo URLs (print them). 5 x 5 m at spacing_m=1 is 25 photos. Capped at 100

PRE-DEFINED VARIABLES (always available, do NOT redefine):
- home_lat, home_lon, home_alt — GPS position at command time
- CONVERSATION_ID — for capture_photo()

RULES:
1. Use ONLY these SDK functions — do NOT import anything
2. Max speed 1.0 m/s, max angular speed 1.57 rad/s
3. For motion: arm() first, then drive()/turn()/set_velocity()/goto(), then safe_disarm() when done
4. Do NOT use takeoff(), land(), set_yaw(), motor_test(), disarm() — those are either quadcopter-only or blocked
5. "stop" or "halt" means stop(). "disarm" always means safe_disarm() — never use disarm() directly
6. When the user asks for a VIDEO, or for pictures taken WHILE moving, bracket the flight with start_recording() ... stop_recording() and print(stop_recording()) — printing the returned URLs is how the media reaches the user, so recording without printing them delivers nothing. Use mode="photos" for stills at an interval. For one still where the vehicle is already standing, use capture_photo() instead.
7. To photograph an AREA ("photograph every square meter of the rectangle 5 m ahead", "cover/map/survey the field") call print(survey_rect(...)) with spacing_m in the operator's unit ("every square meter" = 1). Never use start_recording(mode="photos") around a flight for this: timed photos are counted by seconds of flight, not by area (observed: 100 photos of a 5 x 5 m area's outline, none of its inside).

Output ONLY Python code. No markdown, no comments unless necessary."""

# Keep legacy name pointing to quadcopter prompt for backward compat
CODE_SYSTEM_PROMPT = QUADCOPTER_CODE_SYSTEM_PROMPT


def get_vehicle_type(drone_id):
    """The vehicleType the drone was registered with ('quadcopter', 'rover',
    'fixedwing'), or None when the registry has none."""
    table = dynamodb.Table(os.environ.get('DRONE_TABLE', 'drone-registry-dev'))
    try:
        resp = table.scan(
            FilterExpression='droneId = :d',
            ExpressionAttributeValues={':d': drone_id}
        )
        items = resp.get('Items', [])
        if items:
            return items[0].get('vehicleType')
    except Exception:
        pass
    return None


def get_code_system_prompt(drone_id):
    """Return the appropriate code generation prompt based on vehicle type."""
    if get_vehicle_type(drone_id) == 'rover':
        return ROVER_CODE_SYSTEM_PROMPT
    return QUADCOPTER_CODE_SYSTEM_PROMPT


# Appended to MISSION_SYSTEM_PROMPT when the registry knows the vehicle: the
# mission planner is otherwise told nothing about it, and planned a rover's
# area survey as "take off to 5 m ... land".
MISSION_VEHICLE_NOTES = {
    'rover': ("THIS DRONE IS A GROUND ROVER. It drives and cannot fly: give no "
              "altitude_m, alt_m or min_clearance_alt, and never tell the operator "
              "it will take off, fly or land. Keep the usual first and last phases "
              "(on a rover they arm and stop it)."),
    'fixedwing': ("THIS DRONE IS A FIXED-WING. It cannot stop in the air, so it "
                  "cannot fly survey_rect. Asked to photograph every part of an "
                  "area, do not plan a mission: respond that it cannot stop over "
                  "each spot, and offer to photograph the area while flying over "
                  "it instead. Apply every fixed-wing size limit above."),
    'quadcopter': "THIS DRONE IS A QUADCOPTER.",
}


def describe_drone_state(item):
    """The planner's view of the aircraft right now, from its last heartbeat.
    Without it the planner could only guess from the chat, and answered a
    mid-flight "Stop" with "the drone is on the ground"."""
    import time as _time
    if not item:
        return "DRONE STATE: unknown (no heartbeat)."
    try:
        age = _time.time() - float(item.get('lastUpdate', 0)) / 1000.0
    except (TypeError, ValueError):
        age = None
    if age is not None and age > 60:
        return f"DRONE STATE: unknown (last heartbeat {age:.0f} s ago)."
    armed = bool(item.get('armed'))
    agl = item.get('altitudeAgl')
    try:
        agl = float(agl) if agl is not None else None
    except (TypeError, ValueError):
        agl = None
    if armed and agl is not None and agl > 1.0:
        where = f"FLYING at {agl:.1f} m above the takeoff point"
    elif armed:
        where = "armed, on or near the ground"
    else:
        where = "on the ground, disarmed"
    m = item.get('mission') or {}
    if m.get('running'):
        doing = f"mission running, on step {m.get('phase')} of {m.get('of')} ({m.get('step')})"
    else:
        doing = "no mission running"
    extra = f"; battery {item['battery']}%" if item.get('battery') is not None else ""
    if item.get('pilotControl'):
        extra += ("; THE PILOT HAS CONTROL from the transmitter, so the drone refuses every "
                  "command until it lands or the transmitter selects GUIDED")
    return f"DRONE STATE (live): {where}; {doing}{extra}."


def get_drone_state(drone_id):
    try:
        return dynamodb.Table(STATUS_TABLE).get_item(Key={'droneId': drone_id}).get('Item')
    except Exception as e:
        print(f"Error reading drone state: {e}")
        return None


def mission_system_prompt(vehicle_type=None, drone_state=None):
    note = MISSION_VEHICLE_NOTES.get(vehicle_type)
    out = MISSION_SYSTEM_PROMPT + ("\n\n" + note if note else "")
    if drone_state:
        out += "\n\n" + drone_state
    return out


def get_user_id(event):
    """Extract user ID from Lambda authorizer context."""
    try:
        authorizer = event['requestContext']['authorizer']
        return authorizer.get('userId') or authorizer.get('principalId')
    except (KeyError, TypeError):
        return None


def verify_ownership(user_id, drone_id):
    """Verify that the user owns the specified drone."""
    table = dynamodb.Table(DRONE_TABLE)
    try:
        response = table.get_item(Key={'userId': user_id, 'droneId': drone_id})
        return 'Item' in response
    except Exception:
        return False


def json_response(status_code, body):
    """Create API Gateway response with CORS headers."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        },
        'body': json.dumps(body, default=str)
    }


def save_message(conversation_id, drone_id, sender, content_type, content, image_urls=None, media=None,
                 sent=None):
    """Save a message to the conversation history."""
    table = dynamodb.Table(CONVERSATIONS_TABLE)
    
    timestamp = datetime.now(timezone.utc).isoformat()
    message_id = str(uuid.uuid4())
    
    item = {
        'PK': f'CONV#{drone_id}#{conversation_id}',
        'SK': f'MSG#{timestamp}#{message_id}',
        'messageId': message_id,
        'conversationId': conversation_id,
        'droneId': drone_id,
        'sender': sender,
        'contentType': content_type,
        'content': content,
        'timestamp': timestamp,
        'ttl': int(datetime.now(timezone.utc).timestamp()) + (7 * 24 * 60 * 60)  # 7 days
    }
    
    if image_urls:
        item['imageUrls'] = image_urls
    if media:
        # Stored UNSIGNED, like imageUrls: presigned URLs expire in 24h but the
        # message lives for 7 days, so history has to re-sign on read.
        item['media'] = media
    if sent:
        # What actually went to the drone, for the planner's history (not shown in the app).
        item['sent'] = sent

    table.put_item(Item=item)
    
    return {
        'id': message_id,
        'conversationId': conversation_id,
        'sender': sender,
        'content': {'type': content_type, 'text': content},
        'timestamp': timestamp
    }


def get_conversation_history(drone_id, conversation_id, limit=20):
    """Get the most recent `limit` messages of a conversation, oldest first.

    Queried newest-first and reversed: an oldest-first query with a Limit
    returns the conversation's FIRST messages. In a ~90-message conversation
    the planner never saw its own question "what should the drone do if it
    loses sight of you?", so the operator's answer, "keep searching for 30s
    then return home", came back as "what would you like me to search for?".
    """
    table = dynamodb.Table(CONVERSATIONS_TABLE)

    response = table.query(
        KeyConditionExpression='PK = :pk AND begins_with(SK, :sk_prefix)',
        ExpressionAttributeValues={
            ':pk': f'CONV#{drone_id}#{conversation_id}',
            ':sk_prefix': 'MSG#'
        },
        ScanIndexForward=False,  # newest first, so Limit keeps the latest
        Limit=limit
    )

    messages = []
    for item in reversed(response.get('Items', [])):
        content = item['content']
        if item.get('sent'):
            # Without this the planner saw only its own words and could not
            # tell a mission that went out from one that did not.
            content = f"{content}\n[Sent to the drone: {item['sent']}]"
        msg = {
            'role': 'user' if item['sender'] == 'user' else 'assistant',
            'content': content
        }
        if item.get('imageUrls'):
            msg['images'] = item['imageUrls']
        messages.append(msg)
    
    return messages


def summarize_phases(phases):
    """One line per mission for the history: what the drone was told to do."""
    out = []
    for p in phases or []:
        extra = {k: v for k, v in p.items() if k not in ('type', 'objective', 'success', 'description')}
        name = p.get('type') or p.get('objective', 'vision phase')
        args = ", ".join(f"{k}={v}" for k, v in extra.items())
        out.append(f"{name}({args})" if args else name)
    return "mission: " + " -> ".join(out)


# Plain stop/land/home words skip the planner: they are the ones that must not
# wait on a model, and they mean one thing. The drone checks its own state, so
# "stop" on the ground does nothing.
_FAST_ABORT = {
    'hold': {'stop', 'halt', 'abort', 'freeze', 'hold', 'pause', 'cancel', 'stop now',
             'emergency stop', 'hold position', 'stay there', 'stop stop', 'hover here'},
    'land': {'land', 'land now', 'land here', 'land immediately'},
    'return_home': {'return home', 'come back', 'come home', 'go home', 'rtl',
                    'return to home', 'return to launch', 'come back home'},
}


def fast_abort(message):
    import re as _re
    words = _re.sub(r"[^a-z ]", " ", (message or "").lower())
    words = " ".join(w for w in words.split() if w not in ('please', 'now', 'drone', 'the'))
    if not words:
        words = (message or "").strip().lower()
    for then, phrases in _FAST_ABORT.items():
        if words in phrases or words + " now" in phrases:
            return then
    return None


def send_abort(drone_id, conversation_id, then, message, reply):
    """Stop the drone's current mission, then hold / land / go home."""
    loading = save_message(conversation_id, drone_id, 'drone', 'loading', reply,
                           sent=f"STOP, then {then}")
    publish_to_app(drone_id, conversation_id, 'ack', reply)
    publish_to_drone(drone_id, conversation_id, {
        'action': 'abort', 'then': then, 'conversation_id': conversation_id,
        'original_message': message,
    })
    publish_log(drone_id, "INFO", f"Abort sent (then {then})")
    return json_response(200, {'status': 'sent', 'message_id': loading['id'],
                               'immediate_response': loading})


def fetch_image_as_base64(url: str) -> tuple[str, str]:
    """Fetch an image from URL and return (base64_data, media_type)."""
    try:
        print(f"📸 Fetching image: {url[:100]}...")
        
        # Handle S3 URLs - need to use pre-signed URL or fetch directly
        req = urllib.request.Request(url, headers={'User-Agent': 'DroneAgent/1.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            image_data = response.read()
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            
            # Map content type to Claude format
            if 'png' in content_type:
                media_type = 'image/png'
            elif 'gif' in content_type:
                media_type = 'image/gif'
            elif 'webp' in content_type:
                media_type = 'image/webp'
            else:
                media_type = 'image/jpeg'
            
            encoded = base64.b64encode(image_data).decode('utf-8')
            print(f"✅ Image fetched: {len(image_data)} bytes, {media_type}")
            return encoded, media_type
            
    except Exception as e:
        print(f"❌ Failed to fetch image: {e}")
        return None, None


def call_agent(conversation_history, user_message, pending_images=None):
    """Call the AI agent to decide on action."""
    
    # Build messages for Bedrock (Claude format)
    messages = []
    
    # Add conversation history
    for msg in conversation_history:
        messages.append({
            'role': msg['role'],
            'content': [{'type': 'text', 'text': msg['content']}]
        })
    
    # Add current user message with images if available
    user_content = [{'type': 'text', 'text': user_message}]
    
    # Add any pending images (from drone) - actually encode and include them
    if pending_images:
        for img_url in pending_images:
            # Generate pre-signed URL first if it's an S3 URL
            presigned_url = generate_presigned_url(img_url)
            
            # Fetch and encode the image
            img_base64, media_type = fetch_image_as_base64(presigned_url)
            
            if img_base64:
                # Add image to the message content for Claude vision
                user_content.append({
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': media_type,
                        'data': img_base64
                    }
                })
            else:
                # Fallback to text reference if fetch failed
                user_content[0]['text'] += f"\n[Image could not be loaded: {img_url}]"
    
    messages.append({'role': 'user', 'content': user_content})
    
    try:
        result = llm.invoke(AGENT_SYSTEM_PROMPT, messages, max_tokens=1024, model=BEDROCK_MODEL_ID)
        response_text = ''.join(
            block.get('text', '') for block in result.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        
        # Parse JSON response
        try:
            # Handle potential markdown wrapping
            if response_text.startswith('```'):
                lines = response_text.split('\n')
                response_text = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])
            
            return json.loads(response_text)
        except json.JSONDecodeError:
            # If not valid JSON, treat as simple response
            return {'action': 'respond', 'message': response_text}
            
    except Exception as e:
        print(f"❌ Agent error: {e}")
        return {'action': 'respond', 'message': f'Error processing request: {str(e)}'}


def generate_code(instruction, conversation_id, drone_id=None, vehicle_type=None):
    """Generate Python code for a drone command."""
    if vehicle_type == 'rover':
        system_prompt = ROVER_CODE_SYSTEM_PROMPT
    elif vehicle_type == 'quadcopter':
        system_prompt = QUADCOPTER_CODE_SYSTEM_PROMPT
    else:
        system_prompt = get_code_system_prompt(drone_id) if drone_id else CODE_SYSTEM_PROMPT
    try:
        messages = [
            {
                'role': 'user',
                'content': [{
                    'type': 'text',
                    'text': f"CONVERSATION_ID = '{conversation_id}'\n\nCommand: {instruction}"
                }]
            }
        ]
        result = llm.invoke(system_prompt, messages, max_tokens=1024, model=BEDROCK_MODEL_ID)
        code = ''.join(
            block.get('text', '') for block in result.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        
        # Clean up code - extract from markdown code block if present
        if '```' in code:
            # Find code between ``` markers
            import re
            # Match ```python or ``` followed by code and closing ```
            match = re.search(r'```(?:python)?\s*\n(.*?)```', code, re.DOTALL)
            if match:
                code = match.group(1).strip()
            else:
                # Fallback: just strip leading ``` line
                lines = code.split('\n')
                start = 0
                end = len(lines)
                for i, line in enumerate(lines):
                    if line.strip().startswith('```'):
                        start = i + 1
                        break
                for i in range(len(lines) - 1, -1, -1):
                    if lines[i].strip() == '```':
                        end = i
                        break
                code = '\n'.join(lines[start:end]).strip()
        
        # Drop any prose before the code: the first line from which the rest
        # parses as Python. This used to cut at the first line starting with a
        # known SDK call, which also cut real code - `urls = survey_rect(...)`,
        # `if ...:` and `try:` start no such line - and sent a survey as just
        # `land()`.
        import ast as _ast
        lines = code.split('\n')
        for i in range(len(lines)):
            candidate = '\n'.join(lines[i:]).strip()
            try:
                _ast.parse(candidate)
            except SyntaxError:
                continue
            code = candidate
            break

        # If the user said "disarm" (but not "land"), replace any land() the LLM
        # generated with safe_disarm() — the LLM stubbornly maps disarm→land().
        import re as _re
        if 'disarm' in instruction.lower() and 'land' not in instruction.lower():
            code = _re.sub(r'\bland\s*\(\s*\)', 'safe_disarm()', code)

        return code
        
    except Exception as e:
        error_msg = str(e)
        # Surface the error to the user instead of sending a comment as "code"
        raise RuntimeError(f"Failed to generate code: {error_msg}") from e


def failure_message(result):
    """Describe a failed drone result to the operator.

    Two result shapes reach the app. Code execution carries stderr/error; a
    mission carries failure_reason/summary plus a phase count. Only the first
    pair used to be checked, so every failed mission rendered "Unknown error" -
    the cause was discarded at the last hop before the person who needed it,
    and five identical flights produced five identical useless messages.
    """
    reason = (
        result.get('stderr')
        or result.get('error')
        or result.get('failure_reason')
        or result.get('summary')
        or 'Unknown error'
    )
    reason = str(reason).strip() or 'Unknown error'
    total_phases = result.get('total_phases')
    if total_phases:
        reason = f"{reason} ({result.get('phases_completed', 0)}/{total_phases} phases completed)"
    return reason


def media_items(image_urls, video_urls=None):
    """Combine photo and clip URLs into one ordered list of typed media items.

    Photos first, then clips, both in capture order. The `kind` is carried
    explicitly rather than inferred from the file extension client-side,
    because the URLs the app receives are presigned and carry a query string —
    every "does it end in .mp4" check downstream would have to strip it first,
    and one of them eventually would not.
    """
    items = [{'url': u, 'kind': 'photo'} for u in (image_urls or [])]
    items += [{'url': u, 'kind': 'video'} for u in (video_urls or [])]
    return items


def publish_to_drone(drone_id, conversation_id, payload):
    """Send command to drone via IoT Core.

    Stamps a message_id unless the caller set one. The drone dedups on this
    field and only falls back to hashing the payload when it is absent - and
    that fallback used to key on conversation_id + code, so two code-less
    commands in one conversation (action=mission, action=set_goal) hashed
    identically and the second was dropped as a redelivery. Giving every
    command its own id removes the ambiguity at the source.

    Safe against MQTT QoS 1 redelivery: the broker replays these exact bytes,
    id included, so a replay still dedups. These handlers are invoked
    synchronously by API Gateway, which Lambda does not auto-retry, so one
    user action stays one id.
    """
    payload = dict(payload)
    payload.setdefault('message_id', str(uuid.uuid4()))

    topic = f'drone/{drone_id}/chat/{conversation_id}/command'
    print(f"📤 Publishing to drone topic: {topic}")
    print(f"📤 Payload: {json.dumps(payload)[:500]}")
    
    iot.publish(
        topic=topic,
        qos=1,
        payload=json.dumps(payload)
    )
    print(f"✅ Published to drone")


def publish_to_app(drone_id, conversation_id, message_type, text, image_urls=None, image_options=None, media=None):
    """Send message to app via IoT Core."""
    topic = f'drone/{drone_id}/chat/{conversation_id}'
    
    payload = {
        'droneId': drone_id,
        'conversation_id': conversation_id,
        'message_type': message_type,
        'text': text,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    if image_urls:
        # Generate pre-signed URLs for images
        payload['image_urls'] = [generate_presigned_url(url) for url in image_urls]
    if image_options:
        # Generate pre-signed URLs for image options too
        payload['image_options'] = [
            {**opt, 'url': generate_presigned_url(opt['url'])}
            for opt in image_options
        ]
    if media:
        payload['media'] = [
            {**m, 'url': generate_presigned_url(m['url'])} for m in media
        ]
    
    print(f"📤 Publishing to app topic: {topic}, type: {message_type}")
    iot.publish(
        topic=topic,
        qos=1,
        payload=json.dumps(payload)
    )


def publish_log(drone_id, level, message, code=None, source="aws"):
    """Publish a log entry to the drone logs topic for UI visibility."""
    topic = f"drone/{drone_id}/logs"
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "source": source,
        "message": message
    }
    if code:
        log_entry["code"] = code
    try:
        iot.publish(
            topic=topic,
            qos=1,
            payload=json.dumps(log_entry)
        )
    except Exception as e:
        print(f"⚠️ Failed to publish log: {e}")


def get_drone_capabilities(drone_id):
    """
    Get drone capabilities from status table.
    
    Returns dict with:
    - has_vlm: bool - True if drone has Qwen3-VL
    - variant: str - 'nano', 'nx', 'agx32', 'agx64', or 'unknown'
    - nav2_available: bool - True if Nav2 is running
    """
    table = dynamodb.Table(STATUS_TABLE)
    try:
        response = table.get_item(Key={'droneId': drone_id})
        if 'Item' not in response:
            return {'has_vlm': False, 'variant': 'unknown', 'nav2_available': False}
        
        item = response['Item']
        
        # Check capabilities field (populated by drone heartbeat)
        capabilities = item.get('capabilities', {})
        
        # Determine if drone has VLM based on variant
        variant = capabilities.get('variant', item.get('variant', 'unknown'))
        has_vlm = variant in ('agx32', 'agx64')
        
        # Check if VLM is explicitly reported
        if capabilities.get('vlm_available'):
            has_vlm = True
        
        nav2_available = capabilities.get('nav2_available', has_vlm)  # AGX implies Nav2
        
        return {
            'has_vlm': has_vlm,
            'variant': variant,
            'nav2_available': nav2_available,
        }
        
    except Exception as e:
        print(f"Error getting drone capabilities: {e}")
        return {'has_vlm': False, 'variant': 'unknown', 'nav2_available': False}


_KNOWN_ACTIONS = {'mission', 'respond', 'ask', 'set_goal', 'execute', 'look', 'abort'}


def _extract_last_json_action(text: str):
    """Scan `text` for every top-level {...} object (brace-depth counting,
    not regex, so nested braces in a mission's phases don't confuse it) and
    return the LAST one that both parses and has a recognized "action" key.
    Last, not first: a self-correcting model's final blob is the one it
    intends as the real answer (see call_mission_agent's caller for the real
    example this was written against). Returns a dict, or None if nothing
    recoverable — no return-type annotation since this file targets Lambda's
    Python runtime and doesn't use PEP604 `X | None` syntax anywhere else,
    so this avoids assuming a Python version newer than what's deployed.
    """
    candidates = []
    depth = 0
    start = None
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(text[start:i + 1])
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get('action') in _KNOWN_ACTIONS:
            return parsed
    return None


def call_mission_agent(conversation_history, user_message, pending_images=None, vehicle_type=None,
                       drone_state=None, mission_context=None):
    """
    Call the AI agent for mission planning (AGX drones with VLM).
    
    This uses the mission-based approach for drones with on-device VLM.
    mission_context: optional pre-rendered block (see _render_mission_context)
    of what earlier missions in this conversation found, appended to the
    system prompt so follow-ups ("did you also see a car?", "fly back to it")
    have real data to answer and anchor from.
    """
    messages = []
    
    # Add conversation history
    for msg in conversation_history:
        messages.append({
            'role': msg['role'],
            'content': [{'type': 'text', 'text': msg['content']}]
        })
    
    # Add current user message with images if available
    user_content = [{'type': 'text', 'text': user_message}]
    
    if pending_images:
        for img_url in pending_images:
            presigned_url = generate_presigned_url(img_url)
            img_base64, media_type = fetch_image_as_base64(presigned_url)
            
            if img_base64:
                user_content.append({
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': media_type,
                        'data': img_base64
                    }
                })
    
    messages.append({'role': 'user', 'content': user_content})
    
    try:
        system = mission_system_prompt(vehicle_type, drone_state)
        if mission_context:
            system = f"{system}\n\n{mission_context}"
        result = llm.invoke(system, messages,
                            max_tokens=MISSION_MAX_TOKENS, model=MISSION_MODEL_ID, effort=MISSION_EFFORT)
        response_text = ''.join(
            block.get('text', '') for block in result.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        
        # Parse JSON response
        if response_text.startswith('```'):
            lines = response_text.split('\n')
            response_text = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])

        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            # The model can self-correct mid-generation (confirmed live: one
            # real response produced a full valid mission JSON, then "Wait,
            # I'm missing return_home and land. Let me correct:", then a
            # second, corrected JSON blob) — a single whole-string json.loads
            # then fails (extra prose + two concatenated objects) and used to
            # fall straight through to dumping the entire raw mess as a
            # 'respond' message, which a real user would see verbatim.
            # Recover the LAST well-formed top-level JSON object with a
            # recognized "action" instead of giving up immediately — mirrors
            # vlm.py's _parse_response() outermost-brace-span fallback for
            # the same class of prose-wrapped-JSON problem.
            recovered = _extract_last_json_action(response_text)
            if recovered is not None:
                return recovered
            return {'action': 'respond', 'message': response_text}
            
    except Exception as e:
        print(f"❌ Mission agent error: {e}")
        return {'action': 'respond', 'message': f'Error processing request: {str(e)}'}


# --- Fleet mission planner ---------------------------------------------------
# The model proposes plans; nothing it writes is trusted. Every plan, the
# model's and the ones built here from geometry (planning.plan_split), is
# assessed per vehicle against the drawn search area, the no-fly zones, the
# vehicle's own battery model, range and wind, and what the vehicle physically
# is - a rover, a quadcopter, a fixed-wing, anything with a planning.Platform.
# A plan with a blocking issue cannot be dispatched; one with warnings only
# after the operator acknowledges them (plan_select_handler).

HEARTBEAT_FRESH_S = 60


def _fnum(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _latlon(d):
    """{'lat','lon'} from either key style, or None."""
    if not isinstance(d, dict):
        return None
    lat = _fnum(d.get('lat', d.get('latitude')))
    lon = _fnum(d.get('lon', d.get('longitude')))
    return {'lat': lat, 'lon': lon} if lat is not None and lon is not None else None


def _heartbeat_age_s(item):
    import time as _time
    ts = _fnum((item or {}).get('lastUpdate'))
    if ts is None:
        return None
    # Epoch ms from the daemons; tolerate seconds.
    return _time.time() - (ts / 1000.0 if ts > 1e11 else ts)


def _owned_drone_ids(user_id):
    try:
        resp = dynamodb.Table(DRONE_TABLE).query(
            KeyConditionExpression='userId = :uid',
            ExpressionAttributeValues={':uid': user_id})
        return [it['droneId'] for it in resp.get('Items', []) if it.get('droneId')]
    except Exception as e:
        print(f"⚠️ could not list the operator's drones: {e}")
        return []


def build_vehicle(drone_id, homes=None, home=None, limits=None, limits_by_drone=None):
    """A planning.Vehicle from the drone's last heartbeat and registry entry.
    Home is, in order: the request's per-drone `homes`, the request's single
    `home`, the home the drone reports, its last position. Returns
    (vehicle, warnings) - vehicle is None when there is nothing to plan from."""
    item = get_drone_state(drone_id) or {}
    caps = item.get('capabilities') or {}
    vtype = get_vehicle_type(drone_id) or caps.get('vehicle_type') or item.get('vehicleType')
    lim = dict(limits or {})
    lim.update((limits_by_drone or {}).get(drone_id) or {})
    platform = planning.Platform.for_vehicle(vtype, item.get('energyModel'), lim)
    notes = []
    if 'vlm' not in lim:
        if 'vlm_available' in caps:
            platform.vlm = bool(caps.get('vlm_available'))
        else:
            notes.append({'kind': 'capability', 'severity': 'warn',
                          'detail': 'has not reported whether it has a vision model'})
    pos = _latlon(item.get('position'))
    h = _latlon((homes or {}).get(drone_id)) or _latlon(home) or _latlon(item.get('home')) or pos
    if h is None:
        return None, [{'kind': 'home', 'severity': 'block',
                       'detail': 'no home: it has reported no position and none was given'}]
    age = _heartbeat_age_s(item)
    pre = item.get('preflight') or {}
    mission = item.get('mission') or {}
    v = planning.Vehicle(
        drone_id=drone_id, platform=platform, home=h,
        battery_pct=_fnum(item.get('battery')),
        position=pos, altitude_m=_fnum(item.get('altitudeAgl')) or 0.0,
        online=age is not None and age <= HEARTBEAT_FRESH_S,
        can_takeoff=pre.get('canTakeoff') if 'canTakeoff' in pre else None,
        takeoff_reason=pre.get('reason'),
        pilot_control=item.get('pilotControl') or None,
        mission_running=mission.get('id') if mission.get('running') else None)
    return v, notes


_SWEEP_WORDS = ('photograph', 'photo', 'map ', 'mapping', 'survey', 'cover', 'scan',
                'sweep', 'record', 'film', 'video', 'inspect every', 'whole area')


def _plan_style(message):
    """"sweep" for coverage taskings (photograph/map/survey the area), else
    "search" (find something)."""
    low = f" {(message or '').lower()} "
    return 'sweep' if any(w in low for w in _SWEEP_WORDS) else 'search'


def _fmt_polygon(poly):
    """Render a lat/lon ring as a compact human/model-readable vertex list."""
    return "[" + ", ".join(
        f"({p['lat']:.6f}, {p['lon']:.6f})" for p in poly) + "]"


def _render_planning_context(operating_area, no_fly_zones, fleet, wind=None,
                             suggested=None, excluded=None):
    """The situational block the planner reads alongside the operator's
    tasking: each vehicle (what it is, its own home, its battery and what that
    buys), the search area, the no-fly zones, the wind, and the sector split
    the geometry suggests - all in lat/lon, the frame the plans are checked in."""
    lines = [PLANNING_SYSTEM_ADDENDUM, "", "FLEET (plan one mission for each):"]
    for v in fleet:
        p = v.platform
        batt = f"{v.battery_pct:.0f}%" if v.battery_pct is not None else "unknown"
        state = (f"airborne at {v.altitude_m:.0f} m" if v.airborne else
                 "on the ground")
        lines.append(f"  {v.drone_id}: {p.vehicle_type} ({p.describe()}; "
                     f"{'has' if p.vlm else 'NO'} vision model), battery {batt}, {state}, "
                     f"home ({v.home['lat']:.6f}, {v.home['lon']:.6f}), "
                     f"range limit {p.max_range_m:.0f} m from home")
    if excluded:
        lines.append("NOT AVAILABLE (do not plan for these): " + "; ".join(
            f"{did} - {why}" for did, why in excluded.items()))
    lines.append("")
    if operating_area:
        lines.append("SEARCH AREA boundary (lat, lon vertices) - every waypoint must be "
                     "inside this:")
        lines.append("  " + _fmt_polygon(operating_area))
    else:
        lines.append("SEARCH AREA: not specified.")
    lines.append("")
    if no_fly_zones:
        lines.append(f"NO-FLY ZONES ({len(no_fly_zones)}) - every leg must stay "
                     "clear of these:")
        for i, z in enumerate(no_fly_zones):
            lines.append(f"  zone {i}: {_fmt_polygon(z)}")
    else:
        lines.append("NO-FLY ZONES: none.")
    lines.append("")
    lines.append(f"WIND: from {wind.from_deg:.0f} deg at {wind.speed_mps:g} m/s." if wind
                 else "WIND: not given.")
    if suggested:
        lines.append("")
        lines.append("SUGGESTED SPLIT (sectors sized so all finish together; use or improve):")
        for d in suggested:
            lines.append(f"  {d['drone_id']}: {_fmt_polygon(d['sector'])}")
    return "\n".join(lines)


def _coerce_list(value):
    """Return `value` as a list, JSON-decoding it first if a weak model
    double-encoded it as a string (observed live with llama3.2 via Ollama:
    "plans" came back as a stringified JSON array, not an array)."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _extract_tool_input(result, tool_name, list_key=None):
    """Pull a forced tool call's argument dict out of an llm.invoke result.

    Handles three shapes: (1) a native tool_use block; (2) the whole call
    emitted as TEXT (small local models that ignore forced tool_choice) wrapped
    under any of the common arg keys — `input`, `parameters`, `arguments`; and
    (3) a `list_key` whose value the model double-encoded as a JSON string.
    Returns the arguments dict (with `list_key` coerced to a real list when
    given), or {} if nothing recoverable."""
    def _unwrap(d):
        if not isinstance(d, dict):
            return {}
        # If the model wrapped args under name/input|parameters|arguments,
        # descend into the args object.
        for wrap in ('input', 'parameters', 'arguments'):
            if wrap in d and isinstance(d[wrap], (dict, str)):
                inner = d[wrap]
                if isinstance(inner, str):
                    try:
                        inner = json.loads(inner)
                    except json.JSONDecodeError:
                        inner = {}
                if isinstance(inner, dict):
                    d = inner
                break
        if list_key and list_key in d:
            d = {**d, list_key: _coerce_list(d[list_key])}
        return d

    for block in result.get('content', []):
        if block.get('type') == 'tool_use' and block.get('name') == tool_name:
            return _unwrap(block.get('input') or {})
    text = ''.join(b.get('text', '') for b in result.get('content', [])
                   if b.get('type') == 'text')
    if text:
        recovered = llm._try_parse_tool_json(text)
        if isinstance(recovered, dict):
            return _unwrap(recovered)
    return {}


_DETECTOR_NOUNS = ("pickup truck", "truck", "car", "person", "aircraft",
                   "water bottle")


def _target_noun(message):
    """The detector noun the mission is about, taken from the operator's text
    so a synthesized search phase grounds correctly (see mission_vocab wording
    rules). Defaults to 'target' if none of the known nouns appear."""
    low = (message or "").lower()
    for noun in _DETECTOR_NOUNS:
        if noun in low:
            return noun
    return "target"


def _plan_context(body, defaults=None):
    """The planning inputs a request carries, with `defaults` (the running
    plan's stored context, for a retask) filling anything it leaves out. Stored
    with the options so /plan/select re-checks a plan against the same area,
    zones, wind and limits it was made for."""
    d = defaults or {}

    def pick(name, *request_keys, default=None):
        for k in request_keys or (name,):
            if body.get(k) not in (None, [], {}):
                return body[k]
        return d.get(name, default)

    max_aircraft = pick('max_aircraft')
    return {
        'area': pick('area', 'operating_area', 'search_area', default=[]) or [],
        'no_fly': pick('no_fly', 'no_fly_zones', default=[]) or [],
        'wind': pick('wind'),
        'altitude_m': _fnum(pick('altitude_m')),
        'spacing_m': _fnum(pick('spacing_m')),
        'max_aircraft': int(max_aircraft) if max_aircraft else None,
        'margin_m': _fnum(pick('margin_m')) or 10.0,
        'homes': pick('homes', default={}) or {},
        'home': pick('home'),
        'limits': pick('limits', default={}) or {},
        'limits_by_drone': pick('limits_by_drone', default={}) or {},
    }


def _assemble_fleet(pool, ctx, retask=False):
    """Vehicles from `pool` that can be sent, and why each other one cannot.
    Returns (fleet, excluded {id: [detail]}, notes {id: [warn issues]})."""
    wind = planning.Wind.parse(ctx.get('wind'))
    fleet, excluded, notes = [], {}, {}
    for did in pool:
        v, extra = build_vehicle(did, ctx.get('homes'), ctx.get('home'),
                                 ctx.get('limits'), ctx.get('limits_by_drone'))
        issues = list(extra)
        if v is not None:
            issues += planning.eligibility(v, wind, ctx.get('area') or None)
        if retask:
            issues = [i for i in issues if i['kind'] != 'busy']
        blocks = planning.blocking(issues)
        if v is None or blocks:
            excluded[did] = [i['detail'] for i in blocks] or ['unavailable']
            continue
        fleet.append(v)
        notes[did] = planning.warnings(issues)
    return fleet, excluded, notes


def _assess_entry(v, phases, ctx):
    return planning.assess_mission(
        phases, v.platform, v.home, battery_pct=v.battery_pct,
        area=ctx.get('area') or None, no_fly=ctx.get('no_fly') or [],
        wind=planning.Wind.parse(ctx.get('wind')),
        start=v.position if v.airborne else None,
        start_alt_m=v.altitude_m if v.airborne else 0.0,
        margin_m=ctx.get('margin_m') or 10.0)


def _plan_record(label, rationale, per_drone, ctx, *, source, notes=None,
                 extra_issues=None, coverage=None):
    """One option as the app renders it and /plan/select dispatches it.
    `per_drone` items carry drone_id, phases, assessment and optionally sector
    and style. Keeps the keys the app already reads (est_minutes, nfz_clear,
    nfz_violations, per_drone) and adds dispatchable/blocking/warnings."""
    issues = list(extra_issues or [])
    out = []
    for d in per_drone:
        a = d['assessment']
        did = d['drone_id']
        issues += [dict(i, drone_id=did) for i in a['issues']]
        issues += [dict(i, drone_id=did) for i in (notes or {}).get(did, [])]
        out.append({
            'drone_id': did,
            'phases': a['phases'],
            'style': d.get('style'),
            'sector': d.get('sector'),
            'coverage': d.get('coverage'),
            'costing': a['costing'],
            'adjustments': a['adjustments'],
            'geofence': {'keep_in': d.get('sector') or ctx.get('area') or [],
                         'no_fly': ctx.get('no_fly') or []},
        })
    blocks = planning.blocking(issues)
    geo = [i for i in blocks if i['kind'] in ('nfz', 'outside_area', 'leaves_area')]
    return {
        'label': label,
        'rationale': rationale,
        'source': source,
        'est_minutes': max((d['costing']['est_minutes'] for d in out), default=0.0),
        'nfz_clear': not geo,
        'nfz_violations': len(geo),
        'dispatchable': bool(out) and not blocks,
        'blocking': blocks,
        'warnings': planning.warnings(issues),
        'coverage': coverage,
        'per_drone': out,
        'recommended': False,
    }


def _not_used(why):
    return ("; ".join(f"{did} not used: {r}" for did, r in why.items())) if why else ""


def _generated_plans(fleet, ctx, target, style, notes):
    """Plans built from geometry alone: the area split between the vehicles
    choose_fleet picks, in the tasking's style, plus the other style and a
    single-vehicle option. A sweep the batteries cannot finish is replaced by
    its partial version, which says how much it covers."""
    area = ctx.get('area')
    if not fleet or not area or len(area) < 3:
        return []
    wind = planning.Wind.parse(ctx.get('wind'))
    chosen, why = planning.choose_fleet(fleet, area, ctx.get('spacing_m'),
                                        max_vehicles=ctx.get('max_aircraft'))
    combos = [(chosen, why)]
    if len(chosen) > 1:
        single, why1 = planning.choose_fleet(fleet, area, ctx.get('spacing_m'), max_vehicles=1)
        combos.append((single, why1))
    styles = [style] + (['sweep'] if style == 'search' else ['search'])
    plans = []
    for k, (vs, skipped) in enumerate(combos):
        for st in (styles if k == 0 else styles[:1]):
            if st == 'search' and not any(v.platform.vlm for v in vs):
                continue
            r = planning.plan_split(vs, area, st, target=target, no_fly=ctx.get('no_fly') or [],
                                    wind=wind, altitude_m=ctx.get('altitude_m'),
                                    spacing_m=ctx.get('spacing_m'),
                                    margin_m=ctx.get('margin_m') or 10.0)
            coverage = None
            if st == 'sweep' and any(any(i['kind'] == 'energy' for i in
                                         planning.blocking(d['assessment']['issues']))
                                     for d in r['per_drone']):
                r = planning.plan_split(vs, area, st, target=target,
                                        no_fly=ctx.get('no_fly') or [], wind=wind,
                                        altitude_m=ctx.get('altitude_m'),
                                        spacing_m=ctx.get('spacing_m'),
                                        margin_m=ctx.get('margin_m') or 10.0, partial=True)
                coverage = r.get('coverage')
            n = len(vs)
            who = vs[0].drone_id if n == 1 else f"{n} vehicles"
            if st == 'search':
                label = f"Split search, {who}" if n > 1 else f"Search with {who}"
                rationale = (f"each searches its own sector of the area for the {target}, "
                             f"sectors sized so all finish together; every search is "
                             f"time-limited to what its battery leaves")
            elif coverage is not None and coverage < 1:
                label = f"Partial sweep, {who} ({coverage * 100:.0f}% of the area)"
                rationale = ("sweeps lanes over as much of the area as the batteries "
                             "cover, and reports exactly how much that is")
            else:
                label = f"Split sweep, {who}" if n > 1 else f"Sweep with {who}"
                rationale = ("flies every lane of the area while recording: full "
                             "coverage, no vision model needed")
            extra = _not_used(skipped)
            if extra:
                rationale = f"{rationale}. {extra}"
            plans.append(_plan_record(label, rationale, r['per_drone'], ctx, source='planner',
                                      notes=notes, coverage=coverage))
    return plans


def _model_plans(raw_plans, fleet, ctx, notes):
    """The model's plans, every vehicle's mission assessed and repaired. A
    plan that names a vehicle that is not available keeps the rest but is
    blocked, and says which."""
    by_id = {v.drone_id: v for v in fleet}
    plans = []
    for i, plan in enumerate(raw_plans or []):
        if not isinstance(plan, dict):
            continue
        per, extra, seen = [], [], set()
        for e in plan.get('per_drone') or []:
            did = (e or {}).get('drone_id')
            phases = (e or {}).get('phases') or []
            if did in seen or not phases:
                continue
            seen.add(did)
            v = by_id.get(did)
            if v is None:
                extra.append({'kind': 'vehicle', 'severity': 'block', 'drone_id': did,
                              'detail': f"plans for {did}, which is not available"})
                continue
            per.append({'drone_id': did, 'phases': phases, 'assessment': _assess_entry(v, phases, ctx)})
        if not per and not extra:
            continue
        plans.append(_plan_record(plan.get('label') or f"Plan {i + 1}",
                                  plan.get('rationale') or '', per, ctx, source='model',
                                  notes=notes, extra_issues=extra))
    return plans


def _rank(plans):
    """Number the options and recommend one: dispatchable first, then full
    coverage over partial, then fewest warnings, then soonest finished. A plan
    that cannot be dispatched is never recommended."""
    def key(p):
        return (not p['dispatchable'], (p.get('coverage') or 1.0) < 1.0,
                len(p['warnings']), p['est_minutes'])
    plans = sorted(plans, key=key)
    for i, p in enumerate(plans):
        p['plan_id'] = f'plan-{i + 1}'
        p['recommended'] = False
    if plans and plans[0]['dispatchable']:
        plans[0]['recommended'] = True
    return plans


def call_mission_planner(conversation_history, user_message, fleet, ctx,
                         excluded=None, notes=None):
    """Turn an operator's tasking plus the drawn area and no-fly zones into
    ranked fleet plans: the model's (repaired and checked) and the
    geometry-built split plans (always offered when there is an area, so a
    model that fails or plans badly still leaves the operator something
    sound). Returns {'plans', 'ask'}.

    Output is requested as a plain JSON object rather than via the tools API:
    small local models served through Ollama/vLLM handle a forced tool_choice
    unreliably (observed live: empty completions or empty argument objects),
    whereas a direct "emit this JSON" instruction is parsed robustly by
    _extract_tool_input's text path."""
    target = _target_noun(user_message)
    style = _plan_style(user_message)
    generated = _generated_plans(fleet, ctx, target, style, notes or {})
    suggested = next((p['per_drone'] for p in generated if p['source'] == 'planner'), None)
    system = (MISSION_SYSTEM_PROMPT + "\n\n"
              + _render_planning_context(ctx.get('area'), ctx.get('no_fly'), fleet,
                                         planning.Wind.parse(ctx.get('wind')),
                                         suggested, excluded)
              + "\n\n" + _PLAN_JSON_INSTRUCTION)
    messages = []
    for msg in conversation_history:
        content = msg['content']
        text = content.get('text') if isinstance(content, dict) else content
        messages.append({'role': msg['role'],
                         'content': [{'type': 'text', 'text': text or ''}]})
    messages.append({'role': 'user', 'content': [{'type': 'text', 'text': user_message}]})

    raw_plans = []
    try:
        # Local models occasionally return an unparseable / empty completion for
        # this structured ask; a plain retry usually lands (confirmed live with
        # qwen2.5 via Ollama). Bounded so a truly broken endpoint still returns.
        for attempt in range(3):
            result = llm.invoke(system, messages, max_tokens=4096, model=BEDROCK_MODEL_ID)
            tool_input = _extract_tool_input(result, 'propose_plans', list_key='plans')
            raw_plans = tool_input.get('plans', [])
            ask = tool_input.get('ask') if isinstance(tool_input, dict) else None
            if isinstance(ask, str) and ask.strip() and not raw_plans:
                return {'plans': [], 'ask': ask.strip()}
            if raw_plans:
                break
            print(f"⚠️ planner produced no plans (attempt {attempt + 1}/3), retrying")
    except Exception as e:
        # The geometry-built plans still stand on their own.
        print(f"❌ Mission planner model error: {e}")
    plans = _model_plans(raw_plans[:3], fleet, ctx, notes or {}) + generated
    return {'plans': _rank(plans), 'ask': None}


def _plan_options_key(drone_id, conversation_id):
    return {'PK': f'CONV#{drone_id}#{conversation_id}', 'SK': 'PLANOPTS'}


def _store_plan_options(drone_id, conversation_id, plans, context=None):
    """Persist the last-offered plan set - and the inputs it was planned from -
    so /plan/select can re-check and dispatch the chosen plan's phases
    server-side rather than trusting the client to echo them back."""
    table = dynamodb.Table(CONVERSATIONS_TABLE)
    item = dict(_plan_options_key(drone_id, conversation_id))
    item.update({
        'plans': json.dumps(plans),
        'context': json.dumps(context or {}),
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'ttl': int(datetime.now(timezone.utc).timestamp()) + (24 * 60 * 60),
    })
    table.put_item(Item=item)


def _load_plan_options(drone_id, conversation_id, with_context=False):
    table = dynamodb.Table(CONVERSATIONS_TABLE)
    plans, ctx = [], {}
    try:
        resp = table.get_item(Key=_plan_options_key(drone_id, conversation_id))
        item = resp.get('Item')
        if item:
            plans = json.loads(item.get('plans', '[]'))
            ctx = json.loads(item.get('context', '{}'))
    except Exception as e:
        print(f"⚠️ Failed to load plan options: {e}")
    return {'plans': plans, 'context': ctx} if with_context else plans


def _mission_records_key(drone_id, conversation_id):
    return {'PK': f'CONV#{drone_id}#{conversation_id}', 'SK': 'MISSIONS'}


def _build_mission_record(payload, result):
    """Slim, structured record of one completed mission — the durable
    counterpart to the prose summary save_message() stores, and what makes an
    operator follow-up ("did you also see a car?") answerable at all: the
    drone's raw `result` (findings/landmarks/photos) is otherwise gone once
    response_handler has turned it into a chat message.

    `payload` is the drone's full /response payload (build_response_payload
    in fw_gcs_daemon.py); `result` is payload['result']. Caps mirror
    _render_mission_context's own budget so a record built here never grows
    unboundedly even if a future caller renders it without re-capping."""
    summary = (result.get('summary') or '')[:300]
    landmarks = result.get('landmarks') or []
    return {
        'mission_id': payload.get('mission_id') or result.get('mission_id'),
        'completed_at': payload.get('timestamp') or datetime.now(timezone.utc).isoformat(),
        'success': bool(result.get('success')),
        'summary': summary,
        'target_location': result.get('target_location'),
        'landmarks': landmarks[:15],
        'photos': (payload.get('image_urls') or result.get('photos') or [])[:5],
    }


def _append_mission_record(drone_id, conversation_id, record, keep=3):
    """Append one mission record, keeping only the most recent `keep` (newest
    last) — same single-item/JSON-string/TTL shape as _store_plan_options, so
    the local Dynamo shim needs no new indexing to support this."""
    table = dynamodb.Table(CONVERSATIONS_TABLE)
    key = _mission_records_key(drone_id, conversation_id)
    try:
        resp = table.get_item(Key=key)
        existing = json.loads(resp.get('Item', {}).get('missions', '[]'))
    except Exception as e:
        print(f"⚠️ Failed to load prior mission records: {e}")
        existing = []
    existing.append(record)
    existing = existing[-keep:]
    item = dict(key)
    item.update({
        'missions': json.dumps(existing),
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'ttl': int(datetime.now(timezone.utc).timestamp()) + (7 * 24 * 60 * 60),
    })
    table.put_item(Item=item)


def _persist_mission_record(drone_id, conversation_id, payload, result):
    """Record a completed (or failed) MISSION's structured result for later
    follow-ups. Plain code executions carry none of the mission keys and are
    skipped. Never raises: losing a record must not lose the reply."""
    if not any(k in (result or {}) for k in
               ('phases_completed', 'phasesCompleted', 'mission_id')):
        return
    try:
        _append_mission_record(drone_id, conversation_id,
                               _build_mission_record(payload, result))
    except Exception as e:
        print(f"⚠️ failed to persist mission record: {e}")


def _load_mission_records(drone_id, conversation_id):
    table = dynamodb.Table(CONVERSATIONS_TABLE)
    try:
        resp = table.get_item(Key=_mission_records_key(drone_id, conversation_id))
        item = resp.get('Item')
        if not item:
            return []
        return json.loads(item.get('missions', '[]'))
    except Exception as e:
        print(f"⚠️ Failed to load mission records: {e}")
        return []


def _render_mission_context(records, max_chars=4000):
    """Render persisted mission records into a MISSION_SYSTEM_PROMPT addendum
    telling the model how to use them. Hard-budgeted: this rides on top of an
    already-substantial system prompt into a local 7B model served through
    Ollama's OpenAI-compatible endpoint with no explicit num_ctx set, so an
    unbounded dump risks silently truncating the FRONT of the prompt (the
    phase vocabulary) rather than erroring. Oldest records are dropped first
    if still over budget after the per-record caps already applied in
    _build_mission_record.

    The "forbid go_to_gps" rule matters even though these records carry
    lat/lon: go_to_gps is unflyable in the sim's self-contained ENU sandbox
    (MISSION_SYSTEM_PROMPT already documents this as a real failure mode), so
    a fly-back phase must be anchored in the exact "east=X, north=Y (metres,
    local frame)" wording _anchor_phases_to_sim's explicit-coordinate gate
    looks for — otherwise the daemon's own search-area anchor would silently
    overwrite whatever the model wrote.
    """
    if not records:
        return None
    lines = ["COMPLETED MISSION DATA (authoritative, most recent last):"]
    rendered = []
    for rec in records:
        line = json.dumps(rec, default=str)
        rendered.append(line)
    # Drop oldest first if over budget (records list is oldest-first already).
    while rendered and sum(len(l) for l in rendered) > max_chars:
        rendered.pop(0)
    lines.extend(rendered)
    lines.append("")
    lines.append(
        "Using this data:\n"
        "- Questions about what was seen (\"did you see a car?\", \"how many "
        "trucks?\", \"where was X?\"): answer with action \"respond\", strictly "
        "from the landmarks above. Coordinates are local frame (east/north "
        "metres) plus lat/lon. If a label is not in the landmarks, it was not "
        "seen — say so plainly. If a landmark has an image_url, include the "
        "URL in your message.\n"
        "- Requests to fly back to / re-photograph a listed object: emit "
        "action \"mission\" with a fresh phase plan. The objective text MUST "
        "name the stored coordinates in the exact wording \"east=<E>, "
        "north=<N> (metres, local frame)\". Do NOT use go_to_gps for these — "
        "it cannot be flown in this environment; do not invent coordinates.\n"
        "  Example for \"get more angles of the car\":\n"
        "  {\"action\": \"mission\", \"mission\": {\"phases\": [\n"
        "    {\"type\": \"arm_and_takeoff\", \"altitude_m\": 35},\n"
        "    {\"objective\": \"Fly to the car at east=388, north=13 (metres, "
        "local frame) and photograph it from multiple angles\",\n"
        "     \"success\": \"car at east=388, north=13 photographed from "
        "multiple angles\"},\n"
        "    {\"type\": \"return_home\", \"alt_m\": 35},\n"
        "    {\"type\": \"land\"}\n"
        "  ]}, \"message\": \"Returning to the car for more angle shots.\"}"
    )
    return "\n".join(lines)


def is_drone_online(drone_id):
    """Check if drone is online by querying recent heartbeat from status table."""
    import time
    table = dynamodb.Table(STATUS_TABLE)
    try:
        # Get the status record for this drone (written by IoT Rule from heartbeats)
        response = table.get_item(Key={'droneId': drone_id})
        
        if 'Item' not in response:
            print(f"No status record found for drone {drone_id}")
            return False
        
        item = response['Item']
        
        # Prefer TTL when present (seconds since epoch)
        ttl = item.get('ttl')
        current_time = int(time.time())
        if ttl:
            try:
                ttl_int = int(ttl)
                is_online = ttl_int > current_time
                print(f"Drone {drone_id} status check: ttl={ttl_int}, current={current_time}, online={is_online}")
                return is_online
            except (ValueError, TypeError):
                pass

        # Fallback: lastUpdate epoch ms within STATUS_TTL_SECONDS
        last_update = item.get('lastUpdate')
        if last_update:
            try:
                last_update_ms = float(last_update)
                now_ms = time.time() * 1000
                age_seconds = (now_ms - last_update_ms) / 1000
                is_online = age_seconds < STATUS_TTL_SECONDS
                print(f"Drone {drone_id} status check: age={age_seconds:.1f}s, online={is_online}")
                return is_online
            except (ValueError, TypeError):
                pass

        print(f"Drone {drone_id} status check: missing ttl/lastUpdate, checking logs table")
        logs_table = dynamodb.Table(LOGS_TABLE)
        logs_resp = logs_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key('droneId').eq(drone_id),
            ScanIndexForward=False,
            Limit=1
        )
        items = logs_resp.get('Items') or []
        if items:
            last_ts = items[0].get('timestamp', '')
            # Timestamp suffix is millis since epoch (see store_logs_handler)
            if isinstance(last_ts, str) and "_" in last_ts:
                try:
                    last_ms = int(last_ts.split("_")[-1])
                    age_seconds = (time.time() * 1000 - last_ms) / 1000
                    is_online = age_seconds < STATUS_TTL_SECONDS
                    print(f"Drone {drone_id} logs check: age={age_seconds:.1f}s, online={is_online}")
                    return is_online
                except Exception:
                    pass
        return False
    except Exception as e:
        print(f"Error checking drone status: {e}")
        # If we can't check, assume online and let the timeout handle it
        return True


# ============================================
# Lambda Handlers
# ============================================

def create_handler(event, context):
    """Create a new conversation."""
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event['pathParameters']['droneId']
    
    if not verify_ownership(user_id, drone_id):
        return json_response(403, {'error': 'You do not own this drone'})
    
    # Create conversation
    conversation_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    
    table = dynamodb.Table(CONVERSATIONS_TABLE)
    table.put_item(Item={
        'PK': f'CONV#{drone_id}#{conversation_id}',
        'SK': 'META',
        'conversationId': conversation_id,
        'droneId': drone_id,
        'userId': user_id,
        'createdAt': timestamp,
        'ttl': int(datetime.now(timezone.utc).timestamp()) + (7 * 24 * 60 * 60)
    })
    
    return json_response(200, {
        'conversation_id': conversation_id,
        'drone_id': drone_id,
        'created_at': timestamp
    })


def get_handler(event, context):
    """Get conversation history."""
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event['pathParameters']['droneId']
    conversation_id = event['pathParameters']['conversationId']
    
    if not verify_ownership(user_id, drone_id):
        return json_response(403, {'error': 'You do not own this drone'})
    
    # Get messages
    table = dynamodb.Table(CONVERSATIONS_TABLE)
    
    response = table.query(
        KeyConditionExpression='PK = :pk AND begins_with(SK, :sk_prefix)',
        ExpressionAttributeValues={
            ':pk': f'CONV#{drone_id}#{conversation_id}',
            ':sk_prefix': 'MSG#'
        },
        ScanIndexForward=True
    )
    
    messages = []
    for item in response.get('Items', []):
        # Skip "loading" messages - they're temporary placeholders
        if item['contentType'] == 'loading':
            continue
            
        msg = {
            'id': item['messageId'],
            'conversationId': conversation_id,
            'sender': item['sender'],
            'content': {
                'type': item['contentType'],
                'text': item['content']
            },
            'timestamp': item['timestamp']
        }
        
        if item.get('media'):
            # Re-sign on read: what is stored is the permanent S3 URL, and the
            # presigned form handed out at publish time expired after 24h while
            # the message itself lives 7 days. Without this, replayed history
            # shows dead links rather than the media it actually captured.
            msg['content']['media'] = [
                {**m, 'url': generate_presigned_url(m['url'])}
                for m in item['media']
            ]

        if item.get('imageUrls'):
            # Generate pre-signed URLs for all images
            print(f"Processing message with {len(item['imageUrls'])} images")
            signed_urls = [generate_presigned_url(url) for url in item['imageUrls']]
            print(f"Generated {len(signed_urls)} signed URLs")
            
            if item['contentType'] == 'image_choice':
                msg['content']['options'] = [
                    {'id': i, 'url': url, 'description': None}
                    for i, url in enumerate(signed_urls)
                ]
                print(f"Added {len(msg['content']['options'])} options to message")
            else:
                msg['content']['url'] = signed_urls[0]
        
        messages.append(msg)
    
    print(f"Returning {len(messages)} messages")
    
    return json_response(200, {
        'conversation_id': conversation_id,
        'messages': messages
    })


def is_visual_request(message: str) -> bool:
    """Check if the message is asking for visual information (camera use)."""
    message_lower = message.lower()
    
    # Keywords that indicate the user wants to see something
    visual_keywords = [
        'what do you see', 'what can you see', 'tell me what you see',
        'show me', 'take a photo', 'take a picture', 'take photo', 'take picture',
        'capture', 'look around', 'look at', 'what is around', "what's around",
        'can you see', 'do you see', 'see anything', 'what is there',
        'describe what', 'what is in front', "what's in front",
        'look for', 'find the', 'spot the', 'locate',
        'snap a', 'grab a photo', 'get a picture',
    ]
    
    return any(keyword in message_lower for keyword in visual_keywords)


def is_movement_request(message: str) -> bool:
    """Check if the message includes explicit movement/flight instructions."""
    message_lower = message.lower()
    movement_keywords = [
        'take off', 'takeoff', 'lift off', 'lift-off',
        'go up', 'rise', 'climb', 'ascend', 'descend',
        'move', 'fly', 'hover', 'land', 'return', 'rtl',
        'motor test', 'test motor', 'test motors', 'motor check',
        'spin motor', 'spin motors',
        'meters', 'meter', 'feet', 'foot', 'ft', 'm ',
        'forward', 'backward', 'left', 'right',
        'north', 'south', 'east', 'west'
    ]
    return any(keyword in message_lower for keyword in movement_keywords)


def message_handler(event, context):
    """Handle a new chat message from user."""
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event['pathParameters']['droneId']
    conversation_id = event['pathParameters']['conversationId']
    
    if not verify_ownership(user_id, drone_id):
        return json_response(403, {'error': 'You do not own this drone'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})
    
    message = body.get('message', '')
    if not message:
        return json_response(400, {'error': 'Missing message'})
    
    # Save user message
    save_message(conversation_id, drone_id, 'user', 'text', message)

    then = fast_abort(message)
    if then is not None:
        replies = {'hold': "Stopping — holding position.", 'land': "Landing now.",
                   'return_home': "Coming back home."}
        return send_abort(drone_id, conversation_id, then, message, replies[then])
    
    # Get conversation history
    history = get_conversation_history(drone_id, conversation_id)
    
    # Check drone capabilities to decide which agent to use
    capabilities = get_drone_capabilities(drone_id)
    has_vlm = capabilities.get('has_vlm', False)
    variant = capabilities.get('variant', 'unknown')
    
    print(f"🤖 Drone {drone_id} capabilities: variant={variant}, has_vlm={has_vlm}")
    
    # Call appropriate agent based on drone capabilities
    if has_vlm:
        # AGX drone with VLM - use mission-based approach. Give it what past
        # missions in this conversation actually found (last 2 - see
        # _render_mission_context's budget note) so sighting follow-ups and
        # fly-back requests have something to answer from.
        records = _load_mission_records(drone_id, conversation_id)
        agent_response = call_mission_agent(history, message,
                                            vehicle_type=get_vehicle_type(drone_id),
                                            drone_state=describe_drone_state(get_drone_state(drone_id)),
                                            mission_context=_render_mission_context(records[-2:]) if records else None)
    else:
        # Legacy drone (Nano/NX) - use goal-based approach
        agent_response = call_agent(history, message)
    
    action = agent_response.get('action', 'respond')
    print(f"🤖 Agent response: action={action}, message={agent_response.get('message', '')[:100]}")
    publish_log(drone_id, "INFO", f"Agent action={action} for message: {message[:200]} (variant={variant})")

    movement_request = is_movement_request(message)
    visual_request = is_visual_request(message)
    
    # SAFETY NET: Override to 'look' for pure visual requests (no movement) on non-VLM drones
    if not has_vlm and action in ('respond', 'ask') and visual_request and not movement_request:
        print(f"⚠️ Overriding action from '{action}' to 'look' - detected visual request: {message}")
        action = 'look'
        agent_response['action'] = 'look'
        agent_response['message'] = 'Let me take a look...'
        publish_log(drone_id, "INFO", f"Overrode action from '{action}' to look (visual request without movement)")

    # SAFETY NET: Force execution for explicit movement requests on non-VLM drones only.
    # VLM/AGX drones use the mission planner which routes all movement through Nav2 obstacle avoidance.
    if movement_request and not has_vlm:
        if action in ('respond', 'look'):
            print(f"⚠️ Overriding action from '{action}' to 'execute' - detected movement request: {message}")
        else:
            print(f"⚠️ Forcing execute - detected movement request: {message}")
        action = 'execute'
        agent_response['action'] = 'execute'
        agent_response['message'] = 'Executing flight command...'
        publish_log(drone_id, "INFO", f"Forced action=execute (movement request, non-VLM), prev={action}")
    
    # Check if drone is online before sending commands
    drone_online = is_drone_online(drone_id)
    if not drone_online:
        publish_log(drone_id, "WARNING", "Drone appears offline (no recent heartbeat)")
    
    # Handle different agent actions
    if action == 'abort':
        then = agent_response.get('then') if agent_response.get('then') in ('hold', 'land', 'return_home') else 'hold'
        return send_abort(drone_id, conversation_id, then, message,
                          agent_response.get('message') or "Stopping.")

    if action == 'mission':
        # New mission-based approach for AGX drones with VLM
        if not drone_online:
            error_msg = save_message(
                conversation_id, drone_id, 'drone', 'text',
                'The drone appears to be offline. Please check the connection and try again.'
            )
            publish_to_app(drone_id, conversation_id, 'error', 
                'The drone appears to be offline. Please check the connection and try again.')
            publish_log(drone_id, "ERROR", "Rejected mission: drone offline")
            return json_response(200, {
                'status': 'error',
                'message_id': error_msg['id'],
                'immediate_response': error_msg
            })
        
        mission = agent_response.get('mission', {})
        phases = mission.get('phases', [])
        publish_log(drone_id, "INFO", f"Starting mission with {len(phases)} phases")
        
        # Save loading message
        loading_msg = save_message(
            conversation_id, drone_id, 'drone', 'loading',
            agent_response.get('message', 'Starting mission...'),
            sent=summarize_phases(phases)
        )
        
        # Send ack via MQTT so app knows request is being processed
        publish_to_app(drone_id, conversation_id, 'ack', 
            agent_response.get('message', 'Starting mission...'))
        
        # Generate mission ID
        import uuid
        mission_id = str(uuid.uuid4())[:8]
        
        # Send mission to drone
        publish_to_drone(drone_id, conversation_id, {
            'action': 'mission',
            'mission_id': mission_id,
            'phases': phases,
            'conversation_id': conversation_id,
            'original_message': message
        })
        
        return json_response(200, {
            'status': 'sent',
            'mission_id': mission_id,
            'message_id': loading_msg['id'],
            'immediate_response': loading_msg
        })
    
    elif action == 'set_goal':
        # New goal-based approach - send goal to drone's on-device intelligence
        if not drone_online:
            error_msg = save_message(
                conversation_id, drone_id, 'drone', 'text',
                'The drone appears to be offline. Please check the connection and try again.'
            )
            publish_to_app(drone_id, conversation_id, 'error', 
                'The drone appears to be offline. Please check the connection and try again.')
            publish_log(drone_id, "ERROR", "Rejected set_goal: drone offline")
            return json_response(200, {
                'status': 'error',
                'message_id': error_msg['id'],
                'immediate_response': error_msg
            })
        
        goal = agent_response.get('goal', {})
        publish_log(drone_id, "INFO", f"Setting goal: {goal.get('objective', 'unknown')}")
        
        # Save loading message
        loading_msg = save_message(
            conversation_id, drone_id, 'drone', 'loading',
            agent_response.get('message', 'Working on it...')
        )
        
        # Send ack via MQTT so app knows request is being processed
        publish_to_app(drone_id, conversation_id, 'ack', 
            agent_response.get('message', 'Working on it...'))
        
        # Send goal to drone
        publish_to_drone(drone_id, conversation_id, {
            'action': 'set_goal',
            'goal': goal,
            'conversation_id': conversation_id,
            'original_message': message
        })
        
        return json_response(200, {
            'status': 'sent',
            'message_id': loading_msg['id'],
            'immediate_response': loading_msg
        })
    
    elif action == 'execute':
        # Check if drone is online
        if not drone_online:
            error_msg = save_message(
                conversation_id, drone_id, 'drone', 'text',
                'The drone appears to be offline. Please check the connection and try again.'
            )
            publish_to_app(drone_id, conversation_id, 'error', 
                'The drone appears to be offline. Please check the connection and try again.')
            publish_log(drone_id, "ERROR", "Rejected execute: drone offline")
            return json_response(200, {
                'status': 'error',
                'message_id': error_msg['id'],
                'immediate_response': error_msg
            })
        
        # Generate and send code to drone
        # For rovers: always use generate_code() with the rover prompt — the
        # generic agent generates quadcopter inline code (takeoff/land) which
        # doesn't apply to rovers.
        vehicle_type = 'rover' if get_code_system_prompt(drone_id) is ROVER_CODE_SYSTEM_PROMPT else 'quadcopter'
        code = '' if vehicle_type == 'rover' else agent_response.get('code', '')
        if not code:
            # Use the original user message to preserve details
            try:
                code = generate_code(message, conversation_id, drone_id=drone_id)
            except Exception as e:
                error_text = f"Sorry, I couldn't process that command. AI service error: {str(e)}"
                error_msg = save_message(conversation_id, drone_id, 'drone', 'text', error_text)
                publish_to_app(drone_id, conversation_id, 'error', error_text)
                publish_log(drone_id, "ERROR", f"Code generation failed: {e}")
                return json_response(200, {
                    'status': 'error',
                    'message_id': error_msg['id'],
                    'immediate_response': error_msg
                })
        publish_log(drone_id, "INFO", "Dispatching execute command to drone", code=code)
        
        # Save loading message
        loading_msg = save_message(
            conversation_id, drone_id, 'drone', 'loading',
            agent_response.get('message', 'Working on it...')
        )
        
        # Send ack via MQTT so app knows request is being processed
        publish_to_app(drone_id, conversation_id, 'ack', 
            agent_response.get('message', 'Working on it...'))
        
        # Send to drone
        publish_to_drone(drone_id, conversation_id, {
            'action': 'execute',
            'code': code,
            'conversation_id': conversation_id,
            'original_message': message,
            'message_id': loading_msg['id']
        })
        
        return json_response(200, {
            'status': 'sent',
            'message_id': loading_msg['id'],
            'immediate_response': loading_msg
        })
    
    elif action == 'ask':
        # Agent needs clarification
        pending_images = agent_response.get('pending_images', [])
        
        if pending_images:
            # Save as image choice
            msg = save_message(
                conversation_id, drone_id, 'drone', 'image_choice',
                agent_response.get('message', 'Which one?'),
                image_urls=pending_images
            )
            msg['content'] = {
                'type': 'image_choice',
                'text': agent_response.get('message', 'Which one?'),
                'options': [
                    {'id': i, 'url': url, 'description': None}
                    for i, url in enumerate(pending_images)
                ]
            }
        else:
            msg = save_message(
                conversation_id, drone_id, 'drone', 'text',
                agent_response.get('message', 'Could you clarify?')
            )
        
        return json_response(200, {
            'status': 'sent',
            'message_id': msg['id'],
            'immediate_response': msg
        })
    
    elif action == 'look':
        # Check if drone is online
        if not drone_online:
            error_msg = save_message(
                conversation_id, drone_id, 'drone', 'text',
                'The drone appears to be offline. Please check the connection and try again.'
            )
            publish_to_app(drone_id, conversation_id, 'error', 
                'The drone appears to be offline. Please check the connection and try again.')
            publish_log(drone_id, "ERROR", "Rejected look: drone offline")
            return json_response(200, {
                'status': 'error',
                'message_id': error_msg['id'],
                'immediate_response': error_msg
            })
        
        # Agent wants to use camera - capture_photo uploads to S3 automatically
        code = 'url = capture_photo(upload=True)\nprint(f"Photo: {url}")'
        
        loading_msg = save_message(
            conversation_id, drone_id, 'drone', 'loading',
            agent_response.get('message', 'Looking around...')
        )
        
        # Send ack via MQTT so app knows request is being processed
        publish_to_app(drone_id, conversation_id, 'ack', 
            agent_response.get('message', 'Looking around...'))
        
        publish_to_drone(drone_id, conversation_id, {
            'action': 'look',
            'code': code,
            'conversation_id': conversation_id,
            'original_message': message,
            'follow_up': True  # Tell drone to send images back for analysis
        })
        publish_log(drone_id, "INFO", "Dispatching look command to drone", code=code)
        
        return json_response(200, {
            'status': 'sent',
            'message_id': loading_msg['id'],
            'immediate_response': loading_msg
        })
    
    else:  # 'respond'
        # Simple response, no drone action needed
        msg = save_message(
            conversation_id, drone_id, 'drone', 'text',
            agent_response.get('message', 'I understand.')
        )
        
        # Also publish via MQTT for real-time
        publish_to_app(drone_id, conversation_id, 'text', msg['content']['text'])
        
        return json_response(200, {
            'status': 'sent',
            'message_id': msg['id'],
            'immediate_response': msg
        })


def _active_plan_key(drone_id, conversation_id):
    return {'PK': f'CONV#{drone_id}#{conversation_id}', 'SK': 'ACTIVEPLAN'}


def _store_active_plan(drone_id, conversation_id, record):
    item = dict(_active_plan_key(drone_id, conversation_id))
    item.update({'plan': json.dumps(record),
                 'ttl': int(datetime.now(timezone.utc).timestamp()) + (7 * 24 * 60 * 60)})
    dynamodb.Table(CONVERSATIONS_TABLE).put_item(Item=item)


def _load_active_plan(drone_id, conversation_id):
    try:
        item = dynamodb.Table(CONVERSATIONS_TABLE).get_item(
            Key=_active_plan_key(drone_id, conversation_id)).get('Item')
        return json.loads(item['plan']) if item else None
    except Exception as e:
        print(f"⚠️ Failed to load the active plan: {e}")
        return None


def _parse_plan_request(event):
    """(user_id, drone_id, conversation_id, body) or a ready error response."""
    user_id = get_user_id(event)
    if not user_id:
        return None, json_response(401, {'error': 'Unauthorized'})
    drone_id = event['pathParameters']['droneId']
    conversation_id = event['pathParameters']['conversationId']
    if not verify_ownership(user_id, drone_id):
        return None, json_response(403, {'error': 'You do not own this drone'})
    try:
        body = json.loads(event.get('body') or '{}')
    except json.JSONDecodeError:
        return None, json_response(400, {'error': 'Invalid JSON'})
    return (user_id, drone_id, conversation_id, body), None


def _propose(user_id, drone_id, conversation_id, message, pool, ctx, retask_of=None):
    """Shared by /plan and /plan/retask: assemble the fleet, plan, store the
    options for /plan/select, and build the response."""
    for did in pool:
        if not verify_ownership(user_id, did):
            return json_response(403, {'error': f'You do not own drone {did}'})
    fleet, excluded, notes = _assemble_fleet(pool, ctx, retask=retask_of is not None)
    if not fleet:
        text = "No vehicle can fly this right now: " + "; ".join(
            f"{did}: {', '.join(r)}" for did, r in excluded.items())
        save_message(conversation_id, drone_id, 'drone', 'text', text)
        publish_to_app(drone_id, conversation_id, 'error', text)
        return json_response(200, {'status': 'unavailable', 'plans': [], 'excluded': excluded})

    save_message(conversation_id, drone_id, 'user', 'text', message)
    history = get_conversation_history(drone_id, conversation_id)
    planner = call_mission_planner(history, message, fleet, ctx,
                                   excluded={d: ', '.join(r) for d, r in excluded.items()},
                                   notes=notes)
    plans, ask = planner.get('plans', []), planner.get('ask')
    if ask:
        save_message(conversation_id, drone_id, 'drone', 'text', ask)
        publish_to_app(drone_id, conversation_id, 'text', ask)
        return json_response(200, {'status': 'ask', 'question': ask, 'plans': []})
    if not plans:
        publish_to_app(drone_id, conversation_id, 'error',
                       "I couldn't produce a plan for that. Try rephrasing the task, "
                       "or draw the search area.")
        return json_response(200, {'status': 'error', 'plans': [], 'excluded': excluded})
    homes = {v.drone_id: v.home for v in fleet}
    for p in plans:
        p['retask_of'] = retask_of
        for e in p['per_drone']:
            e['home'] = homes.get(e['drone_id'])
    _store_plan_options(drone_id, conversation_id, plans, ctx)
    recommended = next((p['plan_id'] for p in plans if p.get('recommended')), None)
    ok = sum(1 for p in plans if p['dispatchable'])
    save_message(conversation_id, drone_id, 'drone', 'text',
                 f"Proposed {len(plans)} plan option(s), {ok} ready to fly"
                 + (" (replacing the running plan once approved)." if retask_of else "."))
    return json_response(200, {'status': 'proposed' if retask_of else 'ok', 'plans': plans,
                               'recommended_plan_id': recommended, 'excluded': excluded,
                               'retask_of': retask_of})


def plan_handler(event, context):
    """POST /drones/{droneId}/conversations/{conversationId}/plan

    Take the operator's tasking plus the drawn search area and no-fly zones and
    return ranked fleet plans. Optional: drone_ids (the pool to choose from -
    default every drone this operator owns), homes {drone_id: {lat, lon}} or
    one home for all, wind {from_deg, speed_mps}, altitude_m, spacing_m,
    max_aircraft, limits / limits_by_drone (max_wind_mps, max_range_m,
    landing_reserve_pct, min_takeoff_pct, ceiling_m, vlm). Nothing is sent to
    any vehicle here - that happens on /plan/select."""
    parsed, err = _parse_plan_request(event)
    if err:
        return err
    user_id, drone_id, conversation_id, body = parsed
    message = body.get('message', '')
    if not message:
        return json_response(400, {'error': 'Missing message'})
    pool = body.get('drone_ids') or _owned_drone_ids(user_id) or [drone_id]
    return _propose(user_id, drone_id, conversation_id, message, pool, _plan_context(body))


def _recheck(plan, ctx):
    """Re-assess a stored plan against every vehicle's state now: batteries
    drain, aircraft go offline, pilots take over between planning and "go".
    Returns (blocking, warnings) - new ones only, each tagged with drone_id."""
    retask = plan.get('retask_of') is not None
    blocks, warns = [], []
    for e in plan.get('per_drone', []):
        did = e['drone_id']
        v, extra = build_vehicle(did, ctx.get('homes'), ctx.get('home'), ctx.get('limits'),
                                 ctx.get('limits_by_drone'))
        issues = list(extra)
        if v is not None:
            issues += planning.eligibility(v, planning.Wind.parse(ctx.get('wind')),
                                           ctx.get('area') or None)
            issues += _assess_entry(v, e['phases'], ctx)['issues']
        if retask:
            issues = [i for i in issues if i['kind'] != 'busy']
        blocks += [dict(i, drone_id=did) for i in planning.blocking(issues)]
        warns += [dict(i, drone_id=did) for i in planning.warnings(issues)]
    return blocks, warns


def plan_select_handler(event, context):
    """POST /drones/{droneId}/conversations/{conversationId}/plan/select

    The approval route. Dispatches the chosen option - a new plan, or a
    /plan/retask proposal, which replaces what those vehicles are flying -
    only if it is still sound: a plan with a blocking issue is refused (409
    "blocked"), the plan is re-checked against every vehicle's state now, and
    one with warnings goes only with `acknowledge_warnings: true` (409
    "needs_approval" lists them otherwise)."""
    parsed, err = _parse_plan_request(event)
    if err:
        return err
    user_id, drone_id, conversation_id, body = parsed
    plan_id = body.get('plan_id')
    if not plan_id:
        return json_response(400, {'error': 'Missing plan_id'})

    stored = _load_plan_options(drone_id, conversation_id, with_context=True)
    plans, ctx = stored['plans'], stored['context']
    plan = next((p for p in plans if p.get('plan_id') == plan_id), None)
    if plan is None:
        return json_response(404, {'error': f'Unknown plan_id {plan_id}'})
    for e in plan.get('per_drone', []):
        if not verify_ownership(user_id, e.get('drone_id')):
            return json_response(403, {'error': f"You do not own drone {e.get('drone_id')}"})
    if not plan.get('dispatchable', True):
        return json_response(409, {'status': 'blocked', 'plan_id': plan_id,
                                   'blocking': plan.get('blocking', [])})
    blocks, warns = _recheck(plan, ctx)
    if blocks:
        return json_response(409, {'status': 'blocked', 'plan_id': plan_id,
                                   'reason': 'changed since it was planned',
                                   'blocking': blocks})
    seen, all_warns = set(), []
    for w in (plan.get('warnings') or []) + warns:
        k = (w.get('drone_id'), w.get('kind'), w.get('detail'))
        if k not in seen:
            seen.add(k)
            all_warns.append(w)
    if all_warns and not body.get('acknowledge_warnings'):
        return json_response(409, {'status': 'needs_approval', 'plan_id': plan_id,
                                   'warnings': all_warns})

    save_message(conversation_id, drone_id, 'user', 'text',
                 f"Go with {plan.get('label', plan_id)}.")
    active = (_load_active_plan(drone_id, conversation_id) or {}) if plan.get('retask_of') else {}
    vehicles = dict(active.get('vehicles') or {})
    dispatched = []
    for entry in plan.get('per_drone', []):
        did = entry.get('drone_id')
        phases = entry.get('phases', [])
        if not did or not phases:
            continue
        mission_id = str(uuid.uuid4())[:8]
        publish_to_drone(did, conversation_id, {
            'action': 'mission',
            'mission_id': mission_id,
            'phases': phases,
            'geofence': entry.get('geofence'),
            'conversation_id': conversation_id,
            'original_message': f"selected {plan.get('label', plan_id)}",
        })
        publish_log(did, "INFO", f"Dispatched {len(phases)}-phase mission "
                    f"(plan {plan_id}, mission {mission_id})")
        vehicles[did] = {'mission_id': mission_id, 'status': 'dispatched', 'home': entry.get('home'),
                         'geofence': entry.get('geofence'), 'phases': len(phases)}
        dispatched.append(did)

    # A retask must plan each vehicle back to the home it left from, not to
    # wherever it happens to be when the retask comes in.
    ctx = dict(ctx, homes=dict(ctx.get('homes') or {},
                               **{d: v['home'] for d, v in vehicles.items() if v.get('home')}))
    _store_active_plan(drone_id, conversation_id, {
        'plan_id': plan_id, 'label': plan.get('label'),
        'started_at': datetime.now(timezone.utc).isoformat(),
        'retask_of': plan.get('retask_of'), 'vehicles': vehicles, 'context': ctx,
        'acknowledged_warnings': all_warns,
    })
    publish_to_app(drone_id, conversation_id, 'ack',
                   f"Executing {plan.get('label', plan_id)} on "
                   f"{len(dispatched)} vehicle(s).")
    return json_response(200, {'status': 'sent', 'dispatched': dispatched,
                              'plan_id': plan_id,
                              'mission_ids': {d: vehicles[d]['mission_id'] for d in dispatched}})


def plan_status_handler(event, context):
    """GET /drones/{droneId}/conversations/{conversationId}/plan

    The running plan and each vehicle's live state from its heartbeat."""
    parsed, err = _parse_plan_request(event)
    if err:
        return err
    _, drone_id, conversation_id, _ = parsed
    active = _load_active_plan(drone_id, conversation_id)
    if not active:
        return json_response(404, {'error': 'No plan has been dispatched in this conversation'})
    live = {}
    for did, info in (active.get('vehicles') or {}).items():
        item = get_drone_state(did) or {}
        age = _heartbeat_age_s(item)
        mission = item.get('mission') or {}
        live[did] = {
            'online': age is not None and age <= HEARTBEAT_FRESH_S,
            'battery_pct': _fnum(item.get('battery')),
            'position': _latlon(item.get('position')),
            'altitude_m': _fnum(item.get('altitudeAgl')),
            'pilot_control': item.get('pilotControl') or None,
            'mission': mission,
            'flying_this_plan': bool(mission.get('running')
                                     and mission.get('id') == info.get('mission_id')),
            'status': info.get('status'),
        }
    return json_response(200, {'plan_id': active.get('plan_id'), 'label': active.get('label'),
                              'started_at': active.get('started_at'), 'vehicles': live})


_ABORT_THEN = ('hold', 'land', 'return_home')


def plan_abort_handler(event, context):
    """POST /drones/{droneId}/conversations/{conversationId}/plan/abort

    Stop the running plan on every vehicle in it, or on `drone_ids`, then
    `then` (hold | land | return_home; default return_home). Never refused for
    want of a plan: a stop has to work even when the record is gone."""
    parsed, err = _parse_plan_request(event)
    if err:
        return err
    user_id, drone_id, conversation_id, body = parsed
    then = body.get('then') or 'return_home'
    if then not in _ABORT_THEN:
        return json_response(400, {'error': f"then must be one of {', '.join(_ABORT_THEN)}"})
    active = _load_active_plan(drone_id, conversation_id) or {}
    targets = body.get('drone_ids') or list((active.get('vehicles') or {}).keys()) or [drone_id]
    for did in targets:
        if not verify_ownership(user_id, did):
            return json_response(403, {'error': f'You do not own drone {did}'})
    for did in targets:
        publish_to_drone(did, conversation_id, {
            'action': 'abort', 'then': then, 'conversation_id': conversation_id,
            'original_message': f"abort plan, then {then}"})
        publish_log(did, "INFO", f"Plan abort sent (then {then})")
        if did in (active.get('vehicles') or {}):
            active['vehicles'][did]['status'] = f'aborted ({then})'
    if active:
        _store_active_plan(drone_id, conversation_id, active)
    text = f"Stopping {len(targets)} vehicle(s), then {then.replace('_', ' ')}."
    save_message(conversation_id, drone_id, 'drone', 'text', text)
    publish_to_app(drone_id, conversation_id, 'ack', text)
    return json_response(200, {'status': 'sent', 'aborted': targets, 'then': then})


def plan_retask_handler(event, context):
    """POST /drones/{droneId}/conversations/{conversationId}/plan/retask

    Re-plan the running mission from where the vehicles are now: `message` is
    the new tasking; drone_ids narrows (or add_drone_ids widens) who is
    re-planned; any of the /plan inputs overrides the running plan's. Returns
    proposals and flies nothing - approve one through /plan/select, which
    re-checks it and replaces what those vehicles are flying."""
    parsed, err = _parse_plan_request(event)
    if err:
        return err
    user_id, drone_id, conversation_id, body = parsed
    message = body.get('message', '')
    if not message:
        return json_response(400, {'error': 'Missing message'})
    active = _load_active_plan(drone_id, conversation_id)
    if not active:
        return json_response(409, {'error': 'No plan is running in this conversation; '
                                            'use /plan'})
    pool = body.get('drone_ids') or list((active.get('vehicles') or {}).keys())
    pool += [d for d in (body.get('add_drone_ids') or []) if d not in pool]
    ctx = _plan_context(body, active.get('context'))
    return _propose(user_id, drone_id, conversation_id, message, pool, ctx,
                    retask_of=active.get('plan_id'))


def plan_control_handler(event, context):
    """One Lambda for the after-launch routes: GET /plan (status), POST
    /plan/abort, POST /plan/retask. The GCS maps each route to its own
    handler directly."""
    path = (event.get('resource') or event.get('path') or '').rstrip('/')
    method = (event.get('httpMethod') or 'GET').upper()
    if method == 'GET':
        return plan_status_handler(event, context)
    if path.endswith('/abort'):
        return plan_abort_handler(event, context)
    if path.endswith('/retask'):
        return plan_retask_handler(event, context)
    return json_response(404, {'error': 'Unknown plan route'})


def select_handler(event, context):
    """Handle image selection from user."""
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event['pathParameters']['droneId']
    conversation_id = event['pathParameters']['conversationId']
    
    if not verify_ownership(user_id, drone_id):
        return json_response(403, {'error': 'You do not own this drone'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})
    
    option_id = body.get('option_id')
    if option_id is None:
        return json_response(400, {'error': 'Missing option_id'})
    
    # Save selection as user message
    save_message(conversation_id, drone_id, 'user', 'text', f'Option {option_id + 1}')
    
    # Get history to find the pending images/context
    history = get_conversation_history(drone_id, conversation_id)
    
    # Call agent with selection context
    selection_message = f"User selected option {option_id + 1}. Continue with the task."
    agent_response = call_agent(history, selection_message)
    
    action = agent_response.get('action', 'respond')
    
    if action == 'execute':
        vehicle_type = 'rover' if get_code_system_prompt(drone_id) is ROVER_CODE_SYSTEM_PROMPT else 'quadcopter'
        code = '' if vehicle_type == 'rover' else agent_response.get('code', '')
        if not code:
            try:
                code = generate_code(agent_response.get('message', ''), conversation_id, drone_id=drone_id)
            except Exception as e:
                error_text = f"Sorry, I couldn't process that command. AI service error: {str(e)}"
                error_msg = save_message(conversation_id, drone_id, 'drone', 'text', error_text)
                publish_to_app(drone_id, conversation_id, 'error', error_text)
                return json_response(200, {'status': 'error', 'message_id': error_msg['id'], 'immediate_response': error_msg})
        
        loading_msg = save_message(
            conversation_id, drone_id, 'drone', 'loading',
            agent_response.get('message', 'On it...')
        )
        
        publish_to_drone(drone_id, conversation_id, {
            'action': 'execute',
            'code': code,
            'conversation_id': conversation_id,
            'selected_option': option_id
        })
        
        return json_response(200, {
            'status': 'sent',
            'message_id': loading_msg['id'],
            'immediate_response': loading_msg
        })
    
    else:
        msg = save_message(
            conversation_id, drone_id, 'drone', 'text',
            agent_response.get('message', 'Understood.')
        )
        
        publish_to_app(drone_id, conversation_id, 'text', msg['content']['text'])
        
        return json_response(200, {
            'status': 'sent',
            'message_id': msg['id'],
            'immediate_response': msg
        })


def response_handler(event, context):
    """Handle response from drone (via IoT Rule)."""
    # This is triggered by IoT Rule when drone publishes to chat response topic
    
    # The payload comes directly from IoT
    payload = event
    print(f"📥 Drone response received: {json.dumps(payload)[:500]}")
    
    drone_id = payload.get('droneId')
    conversation_id = payload.get('conversation_id')
    
    if not drone_id or not conversation_id:
        print(f"❌ Missing drone_id or conversation_id in payload: {payload}")
        return
    
    result = payload.get('result', {})
    image_urls = payload.get('image_urls', [])
    video_urls = payload.get('video_urls', [])
    original_message = payload.get('original_message', '')
    follow_up = payload.get('follow_up', False)

    # One ordered list, photos then clips, is what decides "a file or a folder"
    # on the app: it renders a single item inline and several as an album, by
    # length alone. Building it here keeps that decision in one place instead
    # of asking the client to reconcile two parallel URL lists.
    media = media_items(image_urls, video_urls)

    print(f"📋 Processing: drone={drone_id}, conv={conversation_id}, "
          f"images={len(image_urls)}, videos={len(video_urls)}, follow_up={follow_up}")
    print(f"📋 Result: success={result.get('success')}, stdout={result.get('stdout', '')[:200]}")

    # Report the failure before anything else, including the history read. This is
    # what tells the operator why the aircraft did not do what they asked, so it
    # should not be able to fail because a DynamoDB read was slow.
    #
    # It also has to come before the image branches: those fire on image_urls
    # alone, and a failed mission still carries result.photos - so a mission that
    # failed after taking a picture used to be described to the operator as a
    # photo, with the reason dropped entirely. Failure wins; images ride along.
    if result and not result.get('success', False):
        msg = f"There was an issue: {failure_message(result)}"
        # 'media' whenever anything was captured — including video, which the
        # old 'image' type could not describe. Text only when nothing was.
        content_type = 'media' if media else 'text'
        save_message(conversation_id, drone_id, 'drone', content_type, msg,
                     image_urls=image_urls if image_urls else None,
                     media=media if media else None)
        publish_to_app(drone_id, conversation_id, content_type, msg,
                       image_urls=image_urls if image_urls else None,
                       media=media if media else None)
        print(f"✅ Failure reported to app: {msg[:120]}")
        # A failed mission can still have found things before it stopped.
        _persist_mission_record(drone_id, conversation_id, payload, result)
        return

    _persist_mission_record(drone_id, conversation_id, payload, result)

    # Get conversation history
    history = get_conversation_history(drone_id, conversation_id)
    
    if follow_up and image_urls:
        # Drone sent images for analysis - call agent to decide what to show user
        analysis_prompt = f"""The drone has captured these images while looking around:
{json.dumps(image_urls)}

Original user request: {original_message}
Execution result: {json.dumps(result)}

Analyze the situation and respond appropriately. If multiple options match what the user was looking for, present them as choices."""
        
        agent_response = call_agent(history, analysis_prompt, pending_images=image_urls)
        action = agent_response.get('action', 'respond')
        
        if action == 'ask' or len(image_urls) > 1:
            # Show options to user
            save_message(
                conversation_id, drone_id, 'drone', 'image_choice',
                agent_response.get('message', 'I found these. Which one?'),
                image_urls=image_urls
            )
            
            publish_to_app(
                drone_id, conversation_id, 'image_choice',
                agent_response.get('message', 'I found these. Which one?'),
                image_urls=image_urls,
                image_options=[
                    {'id': i, 'url': url, 'description': None}
                    for i, url in enumerate(image_urls)
                ]
            )
        else:
            # Single result or just text response
            msg_content = agent_response.get('message', 'Here\'s what I found.')
            content_type = 'media' if media else 'text'

            save_message(
                conversation_id, drone_id, 'drone', content_type,
                msg_content,
                image_urls=image_urls if image_urls else None,
                media=media if media else None
            )

            publish_to_app(
                drone_id, conversation_id, content_type,
                msg_content,
                image_urls=image_urls if image_urls else None,
                media=media if media else None
            )
    
    elif media:
        # The drone captured something. Keyed on `media`, not image_urls,
        # because a recorded clip with no stills is a perfectly normal result
        # of "take a video of ..." and used to fall through to the text-only
        # branch below, silently dropping the video the operator asked for.
        if image_urls:
            # Only stills can go to the vision model; an MP4 cannot. So a
            # video-only result gets a plain acknowledgement rather than a
            # description, instead of a failed or hallucinated analysis.
            analysis_prompt = f"""The drone has captured this image: {image_urls[0]}
Original user request: {original_message}
Execution result: {json.dumps(result)}

Describe what you see in the image and respond to the user's request."""
            agent_response = call_agent(history, analysis_prompt, pending_images=image_urls)
            default_msg = "Here's the photo."
        else:
            agent_response = {}
            default_msg = (f"Here's the recording ({len(video_urls)} clip"
                           f"{'s' if len(video_urls) != 1 else ''}).")

        msg_content = agent_response.get('message') or default_msg
        save_message(
            conversation_id, drone_id, 'drone', 'media',
            msg_content,
            image_urls=image_urls if image_urls else None,
            media=media
        )

        publish_to_app(
            drone_id, conversation_id, 'media',
            msg_content,
            image_urls=image_urls if image_urls else None,
            media=media
        )
    
    else:
        # No images, just execution result
        if result.get('success'):
            msg = f"Done! {result.get('stdout', '')}"
        else:
            msg = f"There was an issue: {failure_message(result)}"
        
        print(f"💬 Saving text message: {msg[:100]}")
        save_message(conversation_id, drone_id, 'drone', 'text', msg)
        publish_to_app(drone_id, conversation_id, 'text', msg)
        print(f"✅ Message saved and published to app")


def advice_handler(event, context):
    """Handle advice request from drone's on-device LLM (via IoT Rule).
    
    When the drone's on-device intelligence is stuck and asks for help,
    this handler processes the request and returns advice.
    """
    payload = event
    print(f"📥 Advice request received: {json.dumps(payload)[:500]}")
    
    drone_id = payload.get('droneId') or payload.get('drone_id')
    conversation_id = payload.get('conversation_id')
    question = payload.get('question', '')
    current_state = payload.get('current_state', '')
    goal = payload.get('goal', {})
    
    if not drone_id or not conversation_id:
        print(f"❌ Missing drone_id or conversation_id in advice request")
        return
    
    publish_log(drone_id, "INFO", f"On-device LLM asking for advice: {question[:200]}")
    
    # Build prompt for Claude to provide advice
    advice_prompt = f"""The drone's on-device AI is executing a goal and needs your advice.

GOAL:
Objective: {goal.get('objective', 'unknown')}
Success criteria: {goal.get('success_criteria', [])}
Constraints: {goal.get('constraints', [])}
Priority: {goal.get('priority', 'safety')}

CURRENT STATE:
{current_state}

THE DRONE IS ASKING:
{question}

Provide brief, actionable advice (1-3 sentences). Don't give specific code - give guidance the drone can use to figure out what to do next."""

    try:
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': advice_prompt}]}]
        result = llm.invoke(
            'You are helping a drone that is stuck while executing an autonomous task. Provide brief, practical advice.',
            messages, max_tokens=256, model=BEDROCK_MODEL_ID
        )
        advice = ''.join(
            block.get('text', '') for block in result.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        
        print(f"💬 Generated advice: {advice[:200]}")
        
    except Exception as e:
        print(f"❌ Error generating advice: {e}")
        advice = "Continue with your best judgment based on the goal and current state."
    
    # Send advice back to drone
    response_topic = f'drone/{drone_id}/advice/response'
    advice_payload = {
        'droneId': drone_id,
        'conversation_id': conversation_id,
        'advice': advice,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    iot.publish(
        topic=response_topic,
        qos=1,
        payload=json.dumps(advice_payload)
    )
    
    publish_log(drone_id, "INFO", f"Advice sent: {advice[:100]}")


def upload_url_handler(event, context):
    """Generate pre-signed URL for drone to upload images."""
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event['pathParameters']['droneId']
    conversation_id = event['pathParameters']['conversationId']
    
    if not verify_ownership(user_id, drone_id):
        return json_response(403, {'error': 'You do not own this drone'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})
    
    filename = body.get('filename', f'{uuid.uuid4()}.jpg')
    
    # Generate S3 key
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    s3_key = f'drones/{drone_id}/conversations/{conversation_id}/{timestamp}_{filename}'
    
    # Generate pre-signed upload URL
    upload_url = s3.generate_presigned_url(
        'put_object',
        Params={
            'Bucket': IMAGES_BUCKET,
            'Key': s3_key,
            'ContentType': 'image/jpeg'
        },
        ExpiresIn=300  # 5 minutes
    )
    
    # Public URL for viewing
    image_url = clients.public_image_url(s3_key)
    
    return json_response(200, {
        'upload_url': upload_url,
        'image_url': image_url
    })

