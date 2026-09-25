"""
Mission vocabulary — single source of truth for the phase-type and VLM-action
vocabulary shared between the cloud mission planner (aws/src/conversations.py)
and the on-device dispatcher (reasoning_loop.py).

Why this exists: before this module, the typed-phase list was hand-duplicated
as prose in conversations.py's MISSION_SYSTEM_PROMPT and as an if/elif chain in
reasoning_loop.py._execute_phase, with no mechanism to catch drift between the
two. The on-device VLM's own action vocabulary (vlm.py's ActionType) was worse
— entirely invisible to the cloud planner, which could only describe VLM
phases as generic "objective/success" text without knowing what the on-device
loop can actually do (orbit, search, remember, ask). See the fixed-wing
autonomy plan's "change B".

Deployment topology note: the cloud planner (AWS Lambda, aws/src/) and the
on-device dispatcher (drone/common/) are genuinely separate deployables with
no shared Python import path (see eco/CLAUDE.md's "Three targets, two deploy
steps") — Lambda cannot `import mission_vocab` at runtime. So this module is
the canonical *source*; conversations.py's prompt text is regenerated from it
via render_phase_prompt_section()/render_vlm_capabilities_section() and pasted
in as a clearly-marked generated block (see conversations.py), rather than a
live shared import. On-device, reasoning_loop.py imports this module directly
and asserts its dispatch table's keys match PHASE_SCHEMAS at import time, so
drift there is a hard failure, not a silent gap.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class FieldSpec:
    type: str  # "float" | "int" | "str" | "float|None" etc, for prompt rendering only
    default: Any = None
    required: bool = False
    description: str = ""


@dataclass
class PhaseSpec:
    description: str
    fields: Dict[str, FieldSpec] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Typed phases — deterministic geometry/actuation, executed by
# reasoning_loop.py's _execute_phase dispatch table (backends.py Backend
# seam under the hood — see change A). Every key here MUST have a matching
# executor in reasoning_loop.py's dispatch table; _execute_phase asserts this
# at import time.
# ---------------------------------------------------------------------------
PHASE_SCHEMAS: Dict[str, PhaseSpec] = {
    "arm_and_takeoff": PhaseSpec(
        description="Arm and take off to an altitude. Fixed-wing: a real "
                     "hand-launch (waits for the throw to be detected), not "
                     "a vertical climb.",
        fields={"altitude_m": FieldSpec("float", 5.0, description="Target altitude, meters AGL")},
    ),
    "nav": PhaseSpec(
        description="Fly in a straight line, in any direction. As a MOVE from "
                     "where the aircraft is now: forward_m/right_m (negative = "
                     "back/left, relative to the heading at mission start), "
                     "bearing_deg+distance_m for any compass direction (0=N, "
                     "45=NE, 90=E, 180=S, 270=W), and up_m (negative = down; "
                     "altitude is kept if neither up_m nor alt_m is given). "
                     "Moves chain: each starts where the last ended. Without "
                     "any of those fields, north_m/east_m is instead a "
                     "DESTINATION measured from home.",
        fields={
            "forward_m": FieldSpec("float", None, description="Move ahead (negative = back), meters"),
            "right_m": FieldSpec("float", None, description="Move right (negative = left), meters"),
            "bearing_deg": FieldSpec("float", None, description="Compass direction of the move, degrees (0=N, 90=E)"),
            "distance_m": FieldSpec("float", None, description="Length of a bearing_deg move, meters"),
            "up_m": FieldSpec("float", None, description="Climb (negative = descend), meters"),
            "north_m": FieldSpec("float", 0.0),
            "east_m": FieldSpec("float", 0.0),
            "alt_m": FieldSpec("float", 5.0, description="Absolute altitude, meters; overrides up_m"),
            "description": FieldSpec("str", None, description="Human-readable label for progress reporting"),
            "min_clearance_alt": FieldSpec("float", None, description="Climb-to-clear: never fly this leg below this altitude (e.g. a known tree line/obstacle on the route). Only ever raises alt_m, never lowers it — the real guarantee is still the FC-enforced altitude floor set once at mission start, this just lets a mission express a known obstacle explicitly."),
        },
    ),
    "go_to_gps": PhaseSpec(
        description="Fly to an absolute GPS coordinate.",
        fields={
            "lat": FieldSpec("float", required=True),
            "lon": FieldSpec("float", required=True),
            "alt_m": FieldSpec("float", 15.0),
            "description": FieldSpec("str", None),
            "min_clearance_alt": FieldSpec("float", None, description="Same climb-to-clear semantics as the nav phase's field."),
        },
    ),
    "fly_circle": PhaseSpec(
        description="Fly N waypoints around a circle centered on home, at a "
                     "given radius and altitude — a bounded, finite pattern "
                     "(not an open-ended loiter). IMPORTANT for a fixed-wing: "
                     "radius_m must be at or above the airframe's own minimum "
                     "turn radius (roughly max_speed_mps / max_yaw_rate_radps "
                     "— for a typical small fixed-wing this is 40m+, not the "
                     "10-20m that's fine for a quadcopter); a tighter radius "
                     "is not just suboptimal, it is physically unflyable and "
                     "the mission will fail (the on-device code clamps it up "
                     "as a backstop, but don't rely on that — pick a real, "
                     "flyable radius, 50m+ if unsure for a fixed-wing).",
        fields={
            "radius_m": FieldSpec("float", 10.0),
            "altitude_m": FieldSpec("float", 5.0),
            "waypoints": FieldSpec("int", 8, description="Points around the circle"),
        },
    ),
    "fly_rect": PhaseSpec(
        description="Fly the perimeter of a rectangle, sized and placed in the "
                     "aircraft's own body frame: forward_m runs along the "
                     "heading the aircraft had at mission start, right_m runs "
                     "90 deg clockwise from it. This is what makes an operator "
                     "phrase like \"the rectangle 10 meters ahead and 5 to the "
                     "right\" expressible (forward_m=10, right_m=5). A bounded, finite pattern, not a loiter. "
                     "IMPORTANT for a fixed-wing, for the same reason fly_circle "
                     "has a radius floor: the corners are flown as arcs of the "
                     "airframe's own minimum turn radius (roughly max_speed_mps "
                     "/ max_yaw_rate_radps — 40m+ for a typical small "
                     "fixed-wing), so a side shorter than twice that radius is "
                     "not merely suboptimal, it is unflyable and degenerates "
                     "into a circle. Pick sides of 100m+ for a fixed-wing if "
                     "unsure; the on-device code clamps as a backstop but do "
                     "not rely on it.",
        fields={
            "forward_m": FieldSpec("float", 10.0, description="Rectangle extent along the start heading, meters"),
            "right_m": FieldSpec("float", 10.0, description="Rectangle extent 90 deg right of the start heading, meters"),
            "origin_forward_m": FieldSpec("float", 0.0, description="Near corner's forward offset from home, meters"),
            "origin_right_m": FieldSpec("float", 0.0, description="Near corner's rightward offset from home, meters"),
            "altitude_m": FieldSpec("float", 5.0),
            "waypoints_per_side": FieldSpec("int", 1, description="Intermediate points along each side, for smoother tracking"),
            "min_clearance_alt": FieldSpec("float", None, description="Same climb-to-clear semantics as the nav phase's field."),
        },
    ),
    "look_around": PhaseSpec(
        description="Pan and capture photos in N directions (quad/rover — "
                     "no fixed-wing equivalent yet).",
        fields={"directions": FieldSpec("int", 4)},
    ),
    "capture_photo": PhaseSpec(description="Capture and upload a single photo.", fields={}),
    "start_recording": PhaseSpec(
        description="Start capturing media in the background and return "
                     "immediately — the phases that follow fly while it "
                     "records. Pair with a later stop_recording, which is what "
                     "actually encodes and uploads. Use this for \"take a video "
                     "of ...\" or \"... and take pictures\" where the capture "
                     "has to happen DURING a pattern; use capture_photo "
                     "instead for a single still at one spot.",
        fields={
            "mode": FieldSpec("str", "video", description='"video" for one MP4, "photos" for a still every interval_s'),
            "fps": FieldSpec("float", 6.0, description="Video mode only: frames per second"),
            "interval_s": FieldSpec("float", 3.0, description="Photos mode only: seconds between stills"),
            "max_seconds": FieldSpec("float", 120.0, description="Hard stop, whatever the following phases do"),
        },
    ),
    "stop_recording": PhaseSpec(
        description="Stop the recording started by start_recording, encode it "
                     "and upload. Harmless if nothing is recording.",
        fields={},
    ),
    "return_home": PhaseSpec(
        description="Return toward the launch point (RTL). Fixed-wing: loiters "
                     "at RTL altitude near home — does not land by itself "
                     "unless the airframe's RTL_AUTOLAND is separately "
                     "configured; use a subsequent land phase for a "
                     "controlled approach.",
        fields={"alt_m": FieldSpec("float", 5.0)},
    ),
    "land": PhaseSpec(
        description="Land. Fixed-wing: flies a real approach-and-touchdown, "
                     "INTO WIND — heading_deg should be supplied if known; "
                     "if omitted, the on-device backend will try the flight "
                     "controller's own live wind estimate, and will refuse "
                     "to land (fails safe) rather than guess a heading if "
                     "neither is available. Quad: unaffected by heading_deg.",
        fields={"heading_deg": FieldSpec("float", None, description="Fixed-wing only; approach heading, into wind")},
    ),
}


# ---------------------------------------------------------------------------
# VLM action capabilities — what the on-device perceive→decide→act loop
# (reasoning_loop.py._exec_vlm_phase) can actually do inside an untyped
# ("objective"/"success") phase. Mirrors vlm.py's real ActionType enum
# (kept in sync there manually — vlm.py owns the enum since it's also the
# literal parse target for the model's JSON output; this module describes
# capabilities in cloud-planner-relevant terms, not the wire schema).
# ---------------------------------------------------------------------------
VLM_CAPABILITIES: Dict[str, str] = {
    "navigate_to_point": "Fly toward a point it currently sees in-frame (pixel-located).",
    "navigate_to_world": "Fly to an explicit world coordinate — how it acts on "
                          "anywhere it knows about but cannot currently see, "
                          "including routing around an obstacle to a clear point "
                          "beyond it.",
    "navigate_to_object": "Fly toward a named object it currently sees.",
    "return_to_landmark": "Fly back toward a previously-sighted, remembered "
                           "(geo-tagged) landmark by name — works even after "
                           "flying out of sight of it.",
    "search_area": "Fly an expanding search pattern to look for a named "
                    "target that isn't currently in view.",
    "count": "Report how many distinct instances of a named target it has "
             "seen — computed from geo-tagged memory (dedups repeat "
             "sightings of the same physical object across an orbit), not "
             "the model's own visual arithmetic. Zero is a valid answer.",
    "orbit_point": "Circle a world point with its camera held on it and keep "
                    "watching — for something that has gone out of sight "
                    "somewhere it may reappear (under cover, inside a "
                    "structure). One lap per decision, so it re-evaluates "
                    "each lap rather than committing to a fixed wait.",
    "follow_target": "Keep station on something that is MOVING (a vehicle driving "
                      "away, a person walking) — a stand-off behind it for a rover or "
                      "multirotor, a continuous circle around it for a fixed-wing, which "
                      "cannot hover. Distinct from orbit_point (a FIXED point) and from "
                      "navigate (which ends on arrival at a point that is already stale "
                      "by the time it gets there). Runs between decisions rather than "
                      "inside one, so re-issuing it does not restart it.",
    "drop_payload": "Release a carried payload for a target it has confirmed "
                     "and is close to. Refuses unless the target is visible "
                     "in the current frame and within range — a payload "
                     "cannot be recovered once released.",
    "capture_photo": "Capture a photo as evidence.",
    "report": "Report a grounded finding (backed by a real detection or "
              "memory match — cannot report something never actually seen).",
    "ask_cloud": "Ask for help if genuinely stuck (bounded — repeated "
                 "failures become a real phase failure, not an infinite ask loop).",
}


# ---------------------------------------------------------------------------
# Rules the planner's free-text phases must obey to survive the on-device
# grounding guard. These are not style preferences: the guard checks whether
# any DETECTED LABEL appears as a substring of the phase's objective/success
# text, one-directionally. A phase that says "locate the individual in crimson
# outerwear" therefore contains no detectable label, so the guard rejects every
# report the aircraft makes about it and the phase can never complete — with no
# error anywhere, just a mission that quietly runs out of actions.
# ---------------------------------------------------------------------------
PHASE_WORDING_RULES: list = [
    'Write objectives using the detector\'s own plain nouns — "person", '
    '"water bottle", "car", "aircraft" — even when adding descriptive detail. '
    '"Find the person in the red jacket" works; "find the individual in '
    'crimson outerwear" does not, because no detected label appears in it.',
    "Keep the distinguishing attribute in the text as well as the noun, so the "
    "aircraft knows which one of several it is looking for.",
    'For a delivery, name both the recipient and the item in the same phase, '
    'e.g. "deliver the water bottle to the person in the red jacket".',
    'Use the typed "return_home"/"land" phases for the trip home rather than '
    "free text — they bypass the grounding guard, which has nothing to ground "
    "a return against.",
]

# The wording rule exists because of how the guard compares; if that comparison
# is ever made bidirectional, revisit this list rather than leaving it as
# unexplained superstition.
GUARD_MATCH_IS_ONE_DIRECTIONAL = True


def render_phase_wording_rules() -> str:
    lines = ["When writing an untyped phase's objective/success text:"]
    for rule in PHASE_WORDING_RULES:
        lines.append(f"- {rule}")
    return "\n".join(lines)


def render_phase_prompt_section() -> str:
    """Render PHASE_SCHEMAS as the prose block conversations.py's
    MISSION_SYSTEM_PROMPT pastes in (regenerate + re-paste by hand when the
    vocab changes — see this module's docstring for why it can't be a live
    shared import across the Lambda/on-device deploy boundary)."""
    lines = []
    for name, spec in PHASE_SCHEMAS.items():
        field_bits = []
        for fname, fspec in spec.fields.items():
            tag = "required" if fspec.required else f"default={fspec.default!r}"
            field_bits.append(f"{fname} ({fspec.type}, {tag})")
        fields_str = ", ".join(field_bits) if field_bits else "no fields"
        lines.append(f'- "{name}": {spec.description} [{fields_str}]')
    return "\n".join(lines)


def render_vlm_capabilities_section() -> str:
    """Render VLM_CAPABILITIES as prose so the cloud planner can compose an
    objective/success string knowing what the on-device loop can actually do,
    instead of writing free text blind to the real action set."""
    lines = ["The on-device VLM, inside an untyped (objective/success) phase, can:"]
    for name, desc in VLM_CAPABILITIES.items():
        lines.append(f"- {desc}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_phase_prompt_section())
    print()
    print(render_vlm_capabilities_section())
