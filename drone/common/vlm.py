"""
VLM Service - Vision-Language Model for drone perception and reasoning.

Uses Qwen3-VL to:
1. Understand what the drone sees (scene description)
2. Make decisions about what to do next (action selection)
3. Handle subjective judgments (e.g., "prettiest tree")

The VLM outputs structured actions that integrate with Nav2 for execution.
"""

import os
import re
import sys
import time
import json
import base64
import threading
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

# Models directory (install: ~/drone-api/models; repo: drone/models)
_script_dir = Path(__file__).parent.resolve()
MODELS_DIR = _script_dir / "models" if (_script_dir / "models").exists() else _script_dir.parent / "models"

# Symlinks created by setup_models.py
VLM_MODEL_PATH = MODELS_DIR / "vlm.gguf"
VLM_MMPROJ_PATH = MODELS_DIR / "vlm_mmproj.gguf"


class ActionType(str, Enum):
    """Types of actions the VLM can output."""
    NAVIGATE_TO_POINT = "navigate_to_point"  # Point on image -> Nav2 goal
    NAVIGATE_TO_OBJECT = "navigate_to_object"  # Named object -> Nav2 goal
    RETURN_TO_LANDMARK = "return_to_landmark"  # Named label -> remembered world position (memory.nearest)
    SEARCH_AREA = "search_area"  # Fly an expanding-orbit search pattern for a named target
    COUNT = "count"  # Report a distinct-object count for a named target, computed
                      # from SpatialMemory (not the model's own visual arithmetic —
                      # see reasoning_loop.py's COUNT handler)
    NAVIGATE_TO_WORLD = "navigate_to_world"  # Fly to an explicit world coordinate.
                                              # Needed because most of the spatial
                                              # information in the prompt (memory,
                                              # obstacle ends, detections) is given
                                              # as world coordinates, and a pixel
                                              # cannot name a place off-camera.
    ORBIT_POINT = "orbit_point"  # Circle a world point, sensor held on it, and keep
                                  # watching — for when something you care about is
                                  # temporarily hidden and may reappear
    FOLLOW_TARGET = "follow_target"  # Keep station on something that is MOVING. Distinct
                                      # from ORBIT_POINT (which watches a FIXED point) and
                                      # from navigate (which ends on arrival): the goal is
                                      # re-estimated every tick and there is no arrival.
    DROP_PAYLOAD = "drop_payload"  # Release the carried payload near a confirmed target
    AVOID = "avoid"  # Obstruction filling the camera: break left/right, or climb OVER it.
                      # Deliberately RELATIVE (no coordinates), because it is the one
                      # action meant to be answerable from the image alone — every other
                      # spatial action here is phrased in world coordinates that only
                      # exist because the sim handed them over. See reasoning_loop's
                      # AVOID handler and the "over or around" cue in SYSTEM_PROMPT.
    CAPTURE_PHOTO = "capture_photo"
    REPORT = "report"  # Send message to user
    PHASE_COMPLETE = "phase_complete"
    MISSION_COMPLETE = "mission_complete"
    MISSION_FAILED = "mission_failed"
    ASK_CLOUD = "ask_cloud"  # Need help from cloud


@dataclass
class VLMAction:
    """Structured action output from VLM."""
    action_type: ActionType
    
    # For navigate actions
    point_x: Optional[int] = None  # Pixel x coordinate
    point_y: Optional[int] = None  # Pixel y coordinate
    target_object: Optional[str] = None  # Object name to navigate to
    
    # For orbit_point: which world point to circle, and how.
    world_x: Optional[float] = None
    world_y: Optional[float] = None
    radius_m: Optional[float] = None
    alt_m: Optional[float] = None

    # For avoid: which way to break. "left" / "right" / "over".
    direction: Optional[str] = None
    magnitude_deg: Optional[float] = None

    # For report/complete actions
    message: Optional[str] = None
    
    # VLM's reasoning (for debugging/display)
    reasoning: Optional[str] = None

    # Confidence (0-1)
    confidence: float = 1.0

    # True only when the model's raw output was genuinely unusable (empty
    # after retries, or no action_type recoverable by any parse strategy) —
    # lets callers (MissionLoop) tell "the model decided X" apart from "the
    # model failed to decide anything", instead of silently treating both the
    # same way. See _parse_response's last-resort branch.
    parse_failed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "point_x": self.point_x,
            "point_y": self.point_y,
            "target_object": self.target_object,
            "world_x": self.world_x,
            "world_y": self.world_y,
            "radius_m": self.radius_m,
            "alt_m": self.alt_m,
            "direction": self.direction,
            "magnitude_deg": self.magnitude_deg,
            "message": self.message,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "parse_failed": self.parse_failed,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VLMAction':
        return cls(
            action_type=ActionType(data.get("action_type", "report")),
            point_x=data.get("point_x"),
            point_y=data.get("point_y"),
            target_object=data.get("target_object"),
            world_x=data.get("world_x"),
            world_y=data.get("world_y"),
            radius_m=data.get("radius_m"),
            alt_m=data.get("alt_m"),
            direction=data.get("direction"),
            magnitude_deg=data.get("magnitude_deg"),
            message=data.get("message"),
            reasoning=data.get("reasoning"),
            confidence=data.get("confidence", 1.0),
        )


# System prompt for the VLM
VLM_SYSTEM_PROMPT = """You are a drone pilot AI with vision. You see through the drone's camera and decide what to do next.

Your job is to accomplish the given mission by analyzing what you see and selecting the best action.

OUTPUT FORMAT:
You must respond with a JSON object containing:
{
  "reasoning": "Your brief reasoning about what you see and why you chose this action",
  "action_type": "one of: navigate_to_point, navigate_to_world, navigate_to_object, return_to_landmark, search_area, orbit_point, follow_target, drop_payload, count, capture_photo, report, phase_complete, mission_complete, mission_failed, ask_cloud",
  "point_x": <pixel x if navigate_to_point, 0 to IMAGE WIDTH>,
  "point_y": <pixel y if navigate_to_point, 0 to IMAGE HEIGHT, measured DOWN from the top>,
  "target_object": "<object name if navigate_to_object, return_to_landmark, search_area, count, follow_target, or drop_payload>",
  "world_x": <world east coordinate if navigate_to_world or orbit_point>,
  "world_y": <world north coordinate if navigate_to_world or orbit_point>,
  "radius_m": <orbit radius in metres if orbit_point, at least 45>,
  "alt_m": <altitude in metres if orbit_point or follow_target>,
  "message": "<message content if report/complete/failed>"
}

ACTION TYPES:
- navigate_to_point: Point to where the drone should fly, as pixel coordinates
  on the image you were just shown. The frame size is stated under CAMERA below
  — coordinates outside it cannot be resolved and the action is wasted. y is
  measured downward from the top, so the ground is in the LOWER half of the
  frame and the horizon runs across the middle; a point near the top is sky and
  resolves to nothing. If the place you want is not visible in the frame at all,
  use navigate_to_world with coordinates instead.
- navigate_to_world: Fly to an explicit world coordinate (world_x east, world_y
  north, optional alt_m). Use this whenever you want to go somewhere named by
  coordinates rather than by something visible in frame — a point from MEMORY,
  the end of an obstacle listed in OBSTACLES AHEAD, or a place you were told
  about in the objective. navigate_to_point can only aim at what is currently
  on camera, so it cannot take you around something that is in your way.
- avoid: Something solid is in your way and close. Break "left" or "right", or go
  "over" it, in the "direction" field. This action takes NO coordinates on
  purpose — it is the one action you are meant to answer from the picture alone,
  so decide it from what you see and nothing else.
  HOW TO CHOOSE OVER OR AROUND, from the image: if you can see the TOP EDGE of
  the obstruction with sky or open air above it, you can out-climb it — answer
  "over". If it rises past the top of the frame, you cannot climb over it in
  time; pick "left" or "right", whichever side shows more open space or sky.
  When both sides look equally blocked, still choose one — holding course into it
  is the one answer that is certainly wrong. Optional "magnitude_deg" sets how
  hard to break (default 35).
- navigate_to_object: Navigate toward a detected object by name
- return_to_landmark: Fly back to a REMEMBERED LANDMARK by label (see MEMORY below) —
  use this when a target you've already seen is no longer visible (out of range/FOV,
  a fast vehicle flew past it) instead of re-searching from scratch
- search_area: Fly an expanding-orbit search pattern for a named target that is
  NOT in MEMORY (never seen) or whose remembered position turned out to be empty —
  each decision advances one leg of the pattern, so keep issuing search_area while
  the target remains unfound and re-evaluate once it appears in CURRENT DETECTIONS
- count: Report how many distinct TARGET_OBJECTs have been seen so far this mission.
  You do NOT need to count them yourself by eye — the system already tracks every
  distinct sighting in MEMORY (it merges repeat sightings of the same physical
  object, so flying past the same car twice doesn't double-count it) and reports
  the real number. Your job is only to decide WHEN you're confident you've covered
  enough of the area to call count for <target_object> — e.g. after orbiting the
  whole area at least once, not after a single glimpse. Zero is a completely valid
  and honest count if you've covered the area and genuinely seen none.
- orbit_point: Fly a circle around a world point with the camera held on it, and
  keep watching. Each decision flies one lap. Use this when something you care
  about has gone out of sight somewhere it could plausibly reappear — it went
  under cover, into a structure, behind terrain — and leaving would mean losing
  it. Circling keeps eyes on the place while you wait, which searching elsewhere
  does not. Give world_x/world_y for the point to watch (world coordinates, e.g.
  from MEMORY or CURRENT DETECTIONS), radius_m (45 or more; this aircraft cannot
  hover and stalls in too tight a turn) and alt_m.
- follow_target: Keep station on something that is MOVING — a vehicle driving away, a
  person walking. Set target_object to what to follow, and alt_m if you want a specific
  height. Use this instead of navigate_to_object whenever the thing will not still be
  there by the time you arrive: navigate ends when it reaches a point, and the point is
  already stale. Use it instead of orbit_point whenever the thing itself is moving —
  orbit_point circles a FIXED place. Following runs on its own between your decisions and
  reports back, so do not re-issue it every turn; report, or choose another action, and
  issue a stop when you want to break off. This aircraft cannot hover, so for a fixed-wing
  "following" means flying a continuous circle around the target as it moves.
- drop_payload: Release the carried payload for a target you have CONFIRMED and
  are close to. Set target_object to what you are delivering to. You carry a
  limited number (see PAYLOAD) and cannot pick one back up, so releasing on the
  wrong person wastes it. Only release once the distinguishing attribute named
  in the objective is actually visible in the current frame — being near "a
  person" is not the same as being near the RIGHT person.
- capture_photo: Take a photo of what's currently in view
- report: Send a message/observation to the user
- phase_complete: Current mission phase is done, move to next
- mission_complete: Entire mission accomplished successfully
- mission_failed: Cannot complete mission (explain why)
- ask_cloud: Need help from cloud AI (complex decision)

IMPORTANT:
- NEVER report/phase_complete/mission_complete a target as found unless it
  actually appears in CURRENT DETECTIONS this turn OR MEMORY from earlier —
  if you have not actually detected it, you have not found it, no matter how
  confident the image looks. Use search_area or navigate_to_point/navigate_to_object
  to actually go look, and only report once a real detection backs the claim.
- If RECENT ACTIONS already shows you reporting the same finding, DO NOT report
  it again — the finding is already recorded. Call phase_complete (or
  mission_complete if this was the whole mission) instead of repeating yourself.
- Same for count: if RECENT ACTIONS already shows a count for this target_object,
  don't call count again for it — call phase_complete instead.
- Be concise in reasoning
- For navigation, prefer pointing to specific locations on the image
- Consider obstacles and safety
- Report interesting findings
- Complete phases systematically before moving on
- A vehicle that cannot hover (e.g. fixed-wing) flies past what it sees — check
  MEMORY for landmarks already spotted before deciding to search again

IDENTIFYING THE RIGHT ONE:
- When the objective describes a target by an ATTRIBUTE (what they are wearing,
  what colour something is), a detection of the general class is not a match.
  "A person is visible" does not mean "the person in the red jacket is visible".
  Close in until you can actually see the attribute, and say in your reasoning
  what you can and cannot make out yet.
- Bystanders who are not the target may be present. Rejecting one is real
  progress, not a failure — say so and keep looking.

OPERATING WITHOUT COMMUNICATIONS:
- You have NO radio link: not to base, and not to any other aircraft. Nobody
  will tell you what a teammate has found. You cannot ask, and you cannot be
  told. Everything you know comes from your own sensors.
- You may still be able to SEE a teammate (they appear as "aircraft" in
  CURRENT DETECTIONS, and PEER OBSERVATIONS summarises what they have been
  doing). What a teammate does is evidence. An aircraft that has descended and
  is circling low over one spot has very likely found something worth looking
  at — that is a reason to go and see for yourself, and confirm it with your
  own eyes rather than assume.
- Objects left in the world are also evidence. Equipment on the ground where
  there was none before means someone has already been there and acted."""


def _say(msg: str) -> None:
    """Print progress without ever letting it break a flight.

    A print raised OSError: Bad file descriptor mid-mission — stdout had gone
    from under a long-running background run — and it did more than lose a log
    line: the exception propagated out of decide(), the handler's own print
    raised again, and the phase, the mission and the whole four-take batch died
    with it. Whatever closed the descriptor is worth finding, but a telemetry
    line must never be able to abort the aircraft.
    """
    try:
        print(msg, flush=True)
    except Exception:
        pass


class VLMService:
    """
    Vision-Language Model service for drone perception and reasoning.
    
    Uses llama.cpp with Qwen3-VL for multimodal inference.
    """
    
    def __init__(self, model_path: Path = None, mmproj_path: Path = None):
        """
        Initialize VLM service.
        
        Args:
            model_path: Path to VLM GGUF file (default: models/vlm.gguf)
            mmproj_path: Path to vision encoder GGUF file
        """
        self.model_path = model_path or VLM_MODEL_PATH
        self.mmproj_path = mmproj_path or VLM_MMPROJ_PATH

        # Inference backend. Default is local llama.cpp with the Qwen3-VL GGUF
        # (the on-aircraft configuration). Set VLM_BASE_URL to route the SAME
        # perception prompts to an OpenAI-compatible vision endpoint instead
        # (e.g. Ollama serving qwen2.5vl / llava) — used where the GGUF/llama.cpp
        # isn't installed but a real vision MODEL is still wanted in the loop.
        # decide()'s messages are already OpenAI-shaped (system + image_url +
        # text), so only the transport changes; every decision is still the
        # model's.
        self._base_url = (os.environ.get("VLM_BASE_URL") or "").rstrip("/")
        self._backend = "openai" if self._base_url else "llama"
        self._model = os.environ.get("VLM_MODEL", "")
        self._api_key = os.environ.get("VLM_API_KEY")
        # Qwen3-generation models reason before answering. At decide()'s 500-token
        # budget the reasoning alone can consume the whole completion, returning
        # empty content and stalling the mission ("VLM produced no usable output").
        # Set to "none" against Ollama to suppress it. Only sent when set, so
        # endpoints that reject the field are unaffected.
        self._reasoning_effort = os.environ.get("VLM_REASONING_EFFORT")

        self._llm = None
        self._available = False
        # Serialises inference. Two aircraft fly concurrently in the two-drone
        # demo, each with its own MissionLoop on its own thread, but they share
        # one loaded model because a second 5 GB instance will not fit alongside
        # the first. llama.cpp keeps KV-cache state across a call and is not
        # thread-safe, so overlapping calls corrupt each other's context —
        # producing plausible-looking answers to the wrong prompt, which is far
        # worse than waiting. Each create_chat_completion is self-contained
        # (full prompt every time), so per-call locking is sufficient.
        self._lock = threading.RLock()

        self._init_model()

    def _chat(self, **kwargs):
        """The single serialised entry point to the model. Every inference call
        in this class goes through here so no site can forget the lock."""
        with self._lock:
            if self._backend == "openai":
                return self._chat_openai(**kwargs)
            return self._llm.create_chat_completion(**kwargs)

    def _chat_openai(self, *, messages, max_tokens=500, temperature=0.1, **_ignored):
        """Route the perception call to an OpenAI-compatible vision endpoint
        (Ollama/vLLM). Returns the same {'choices':[{'message':{'content':...}}]}
        shape create_chat_completion gives, so decide()'s parsing is unchanged.
        `logit_bias` (a llama.cpp EOS workaround) is dropped — not portable."""
        import urllib.request
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self._base_url + "/chat/completions",
                                     data=body,
                                     headers={"Content-Type": "application/json"})
        if self._api_key:
            req.add_header("Authorization", f"Bearer {self._api_key}")
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)

    def _init_model(self):
        """Initialize the VLM model."""
        if self._backend == "openai":
            # No local weights to load — the model lives behind an HTTP endpoint.
            if not self._model:
                print("VLM_BASE_URL set but VLM_MODEL is empty; set the served "
                      "vision model name.")
                self._available = False
                return
            print(f"VLM via OpenAI endpoint: {self._model} @ {self._base_url}")
            self._available = True
            return
        if not self.model_path.exists():
            print(f"VLM model not found at {self.model_path}")
            print("Run setup_models.py to download Qwen3-VL")
            self._available = False
            return
        
        if not self.mmproj_path.exists():
            print(f"VLM vision encoder not found at {self.mmproj_path}")
            self._available = False
            return
        
        try:
            from llama_cpp import Llama
            from llama_cpp.llama_chat_format import Llava16ChatHandler
            
            print(f"Loading VLM: {self.model_path.name}...")
            
            # Create chat handler for vision
            chat_handler = Llava16ChatHandler(
                clip_model_path=str(self.mmproj_path),
                verbose=False,
            )
            
            # Load model
            self._llm = Llama(
                model_path=str(self.model_path),
                chat_handler=chat_handler,
                n_ctx=8192,  # Context window
                n_threads=4,
                n_gpu_layers=-1,  # Use all GPU layers
                verbose=False,
            )
            
            self._available = True
            print(f"VLM loaded successfully: {self.model_path.name}")
            
        except ImportError:
            print("llama-cpp-python not installed. Install with: pip install llama-cpp-python")
            self._available = False
        except Exception as e:
            print(f"Failed to load VLM: {e}")
            self._available = False
    
    def is_available(self) -> bool:
        """Check if VLM is available."""
        return self._available
    
    def _encode_image(self, image_path: str = None, image_bytes: bytes = None, 
                      image_array = None) -> str:
        """
        Encode image to base64 data URI.
        
        Args:
            image_path: Path to image file
            image_bytes: Raw image bytes
            image_array: NumPy array (RGB)
        
        Returns:
            Base64 data URI string
        """
        if image_array is not None:
            # Convert numpy array to JPEG bytes. cv2 is imported LAZILY here
            # (not at function top): the sim/hardware capture paths hand us JPEG
            # bytes directly and never reach this branch, so a box without
            # opencv-python (e.g. this dev laptop) must still encode those bytes.
            # Falls back to Pillow when cv2 is absent.
            try:
                import cv2
                if len(image_array.shape) == 3 and image_array.shape[2] == 3:
                    bgr = cv2.cvtColor(image_array, cv2.COLOR_RGB2BGR)  # RGB->BGR
                else:
                    bgr = image_array
                _, buffer = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                image_bytes = buffer.tobytes()
            except ImportError:
                import io
                from PIL import Image
                image_bytes = io.BytesIO()
                Image.fromarray(image_array).save(image_bytes, format="JPEG", quality=85)
                image_bytes = image_bytes.getvalue()
        elif image_path:
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
        
        if image_bytes is None:
            raise ValueError("No image provided")
        
        b64 = base64.b64encode(image_bytes).decode('utf-8')
        return f"data:image/jpeg;base64,{b64}"
    
    # Width the avoidance frame is downscaled to before it is sent.
    #
    # This is the single biggest latency win available and it costs nothing in
    # accuracy. Measured on an Orin Nano running qwen3.5:4b over an 18-frame
    # labelled set: native 640x480 took 23.4 s per call, 320 px took 1.0 s — 23x
    # faster — and scored marginally BETTER (9/18 vs 7/18). Nearly all the time
    # was the vision encoder chewing image tokens, not the language model.
    #
    # 23 s is not a latency, it is a different flight. At the 12 m/s minimum
    # airspeed the aircraft covers 280 m between decisions, further than the
    # 200 m it can see ahead; at 1.0 s it covers 12 m.
    AVOID_FRAME_PX = 320

    def check_path(self, image_bytes: bytes) -> Optional[str]:
        """Fast camera-only look ahead: "clear", "over", "around", or None.

        A single narrow question with a tiny token budget, deliberately separate
        from decide(). decide() carries the full mission prompt with a dozen
        action types and takes ~20 s; obstacle avoidance cannot run on a 20 s
        loop, and it does not need any of that context.

        MEASURED ACCURACY, so nobody has to guess: 9/18 on a labelled Manhattan
        set (chance is 6/18 for three classes), and the errors are not spread
        evenly — the model answers "over" for 13 of 18 frames regardless of what
        is in them, scoring 6/6 on over, 2/6 on clear and 1/6 on around. It is
        close to a constant function. Treat the result as ADVISORY ONLY and keep
        a geometric check authoritative for anything that must not hit a
        building. See drone/sim/guarded_backend.py.
        """
        try:
            payload_img = self._downscale(image_bytes, self.AVOID_FRAME_PX)
        except Exception:
            payload_img = image_bytes           # Pillow missing: send as-is
        prompt = (
            "You are the forward camera of a drone flying straight ahead through "
            "a city. Answer with ONE word only.\n"
            "CLEAR  - nothing blocks the way straight ahead.\n"
            "OVER   - a building blocks the way ahead, and you can see its ROOF "
            "with sky above it, so you could climb above it.\n"
            "AROUND - a building blocks the way ahead and rises past the TOP of "
            "the picture, so you cannot climb above it.\n"
            "Look only at the MIDDLE of the picture. Answer CLEAR, OVER or AROUND.")
        try:
            text = self._ask_raw(prompt, payload_img, max_tokens=6)
        except Exception as e:
            # Returning None rather than raising is deliberate: the Orin's vision
            # encoder crashes intermittently (HTTP 500, "encoding mtmd batch" in
            # the ollama log), and a look-ahead that occasionally cannot answer
            # must not take the flight down with it. The caller treats None as
            # "no opinion" and the geometric guard carries on regardless.
            print(f"[vlm] check_path failed: {e}")
            return None
        up = (text or "").strip().upper()
        for word in ("AROUND", "OVER", "CLEAR"):
            if word in up:
                return word.lower()
        return None

    @staticmethod
    def _downscale(image_bytes: bytes, width: int) -> bytes:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(image_bytes))
        if im.width <= width:
            return image_bytes
        im.thumbnail((width, width))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=82)
        return buf.getvalue()

    def _ask_raw(self, prompt: str, image_bytes: bytes, max_tokens: int = 16) -> str:
        """One image question, one short answer. No mission context, no history.

        Goes through _chat() like every other inference in this class, so it
        takes the same lock and inherits the API key and reasoning_effort
        handling — reasoning_effort in particular is load-bearing: without it the
        model spends the whole token budget thinking and returns an empty string.
        """
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": self._encode_image(image_bytes=image_bytes)}},
        ]}]
        out = self._chat(messages=messages, max_tokens=max_tokens, temperature=0.0)
        return (out["choices"][0]["message"].get("content") or "")

    def decide(
        self,
        image,  # Can be path, bytes, or numpy array
        mission_phase: Dict[str, Any],
        drone_state: Dict[str, Any] = None,
        history: list = None,
        detections: list = None,
        memory: list = None,
        extra_context: Optional[Dict[str, str]] = None,
    ) -> VLMAction:
        """
        Decide what action to take based on current view and mission.

        Args:
            image: Current camera frame (path, bytes, or numpy array)
            mission_phase: Current mission phase with objective, success criteria
            drone_state: Optional drone state (position, battery, etc.)
            history: Optional recent action history
            detections: Optional list of backends.Detection from this tick's
                perception pass (what's visible right now)
            memory: Optional list of spatial_memory.Landmark — persistent
                world-frame sightings from earlier in the flight (what's been
                seen before but may not be visible now)

        Returns:
            VLMAction describing what to do next
        """
        if not self._available:
            return VLMAction(
                action_type=ActionType.MISSION_FAILED,
                message="VLM not available",
                reasoning="VLM model not loaded"
            )
        
        # Encode image
        try:
            image_uri = self._encode_image(
                image_path=image if isinstance(image, str) else None,
                image_bytes=image if isinstance(image, bytes) else None,
                image_array=image if hasattr(image, 'shape') else None,
            )
        except Exception as e:
            return VLMAction(
                action_type=ActionType.MISSION_FAILED,
                message=f"Failed to encode image: {e}",
                reasoning="Image encoding error"
            )
        
        # Build prompt
        prompt = self._build_prompt(mission_phase, drone_state, history, detections,
                                    memory, extra_context)
        
        # Call VLM
        try:
            start_time = time.time()

            messages = [
                {"role": "system", "content": VLM_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_uri}},
                        {"type": "text", "text": prompt},
                    ],
                },
            ]

            # A real, observed failure mode of this local model: for certain
            # prompt states (confirmed live: longer MEMORY/RECENT ACTIONS
            # sections, reproduced 10/10) it emits EOS as the literal first
            # generated token — completion_tokens=0. This is NOT a sampling
            # hiccup: it reproduced identically at temperature 0.1, 0.4, and
            # 0.7 and with repeat_penalty up to 1.15, so a same-parameters
            # retry is a guaranteed no-op, not "try again and get lucky".
            # Directly confirmed the fix: banning the EOS token id via
            # logit_bias unlocks a completely normal, sensible response
            # underneath (verified against the exact failing prompt — model
            # went on to emit a full, well-formed JSON action and still
            # stopped naturally on its own after ~60 tokens, so this isn't
            # forcing it to ramble to max_tokens, just refusing to let it
            # quit before saying anything). Only applied from the 2nd
            # attempt on, so the common (non-degenerate) case is untouched.
            content = ""
            attempts = 3
            # The EOS-ban retry is a llama.cpp-specific workaround (see note
            # above) and needs a loaded local model to resolve the token id; the
            # OpenAI/Ollama backend has no such handle, so skip it there.
            eos_id = self._llm.token_eos() if self._backend == "llama" else None
            for attempt in range(attempts):
                kwargs = dict(messages=messages, max_tokens=500, temperature=0.1)
                if attempt > 0 and eos_id is not None:
                    kwargs["logit_bias"] = {eos_id: -100.0}
                response = self._chat(**kwargs)
                content = response['choices'][0]['message']['content'] or ""
                if content.strip():
                    break
                print(f"VLM returned empty output (attempt {attempt + 1}/{attempts}), retrying...")

            elapsed = time.time() - start_time

            # Parse response
            action = self._parse_response(content)

            _say(f"VLM decision in {elapsed:.2f}s: {action.action_type.value}"
                 + (" (parse_failed)" if action.parse_failed else ""))

            # Optional training-data capture (no-op unless a recorder is enabled).
            try:
                from data_recorder import get_default
                recorder = get_default()
                if recorder.enabled:
                    recorder.record_vlm(
                        rgb_frame=image if hasattr(image, 'shape') else None,
                        system_prompt=VLM_SYSTEM_PROMPT,
                        user_prompt=prompt,
                        response=content,
                        action=action.to_dict(),
                        inference_ms=elapsed * 1000.0,
                        drone_state=drone_state,
                    )
            except Exception:
                pass

            return action
            
        except Exception as e:
            _say(f"VLM inference error: {e}")
            return VLMAction(
                action_type=ActionType.ASK_CLOUD,
                message=f"VLM error: {e}",
                reasoning="Inference failed, need cloud assistance"
            )
    
    def _build_prompt(
        self,
        mission_phase: Dict[str, Any],
        drone_state: Dict[str, Any] = None,
        history: list = None,
        detections: list = None,
        memory: list = None,
        extra_context: Optional[Dict[str, str]] = None,
    ) -> str:
        """Build the prompt for the VLM."""
        lines = [
            "CURRENT MISSION PHASE:",
            f"  Objective: {mission_phase.get('objective', 'Unknown')}",
        ]

        if mission_phase.get('success'):
            lines.append(f"  Success when: {mission_phase.get('success')}")

        if mission_phase.get('evaluation_criteria'):
            lines.append(f"  Evaluation criteria: {mission_phase.get('evaluation_criteria')}")

        if drone_state:
            lines.append("")
            lines.append("DRONE STATE:")
            if drone_state.get('battery'):
                lines.append(f"  Battery: {drone_state['battery']}%")
            if drone_state.get('position'):
                lines.append(f"  Position: {drone_state['position']}")
            if drone_state.get('altitude'):
                lines.append(f"  Altitude: {drone_state['altitude']}m")

        if detections:
            lines.append("")
            lines.append("CURRENT DETECTIONS (this frame):")
            for d in detections:
                range_str = f", {d.range_m:.0f}m" if d.range_m is not None else ""
                lines.append(f"  - {d.label} (confidence {d.score:.0%}{range_str})")

        if memory:
            lines.append("")
            lines.append("MEMORY (world-frame landmarks seen earlier this flight, may not be visible now):")
            for lm in memory:
                lines.append(
                    f"  - {lm.label} at world ({lm.x:.0f}, {lm.y:.0f}, {lm.z:.0f}) "
                    f"[seen {lm.hits}x, last score {lm.score:.0%}]"
                )

        # Caller-supplied blocks (PEER OBSERVATIONS, OBSTACLES, PAYLOAD). These
        # carry things the single current frame cannot show — what a teammate has
        # been doing over the last minute, structure ahead sensed beyond visual
        # range, how many payloads are left — and are assembled by deterministic
        # geometry in the harness, not invented by the model. Placed before
        # RECENT ACTIONS so the situation reads before the history of responses
        # to it.
        if extra_context:
            for title, body in extra_context.items():
                if not body:
                    continue
                lines.append("")
                lines.append(f"{title}:")
                for row in str(body).strip().splitlines():
                    lines.append(f"  {row.strip()}")

        if history:
            lines.append("")
            lines.append("RECENT ACTIONS:")
            for h in history[-5:]:
                lines.append(f"  - {h}")

        lines.append("")
        lines.append("Based on what you see in the image and the mission objective, what should the drone do next?")
        lines.append("Respond with a JSON object. Output ONLY the JSON object, no other text before or after it.")

        return "\n".join(lines)

    def _parse_response(self, content: str) -> VLMAction:
        """Parse VLM response into structured action.

        Three layers, each a genuine attempt to recover what the model
        actually decided, not just a formatting workaround:
        1. Strict JSON (optionally fenced/prose-wrapped).
        2. Regex extraction of individual fields — recovers the real decision
           from JSON that's *almost* valid (trailing commas, smart quotes, an
           unterminated string after the fields that matter) instead of
           discarding it just because the whole blob doesn't parse.
        3. Last resort: nothing above found even an action_type. This is a
           real anomaly (a confirmed live cause: the model returning a
           genuinely empty completion even after decide()'s own retries) —
           NOT the same as "the model gave a normal report", so it must not
           be silently absorbed as one. Returns parse_failed=True and
           ActionType.ASK_CLOUD (never a bare, un-actioned REPORT) so
           MissionLoop takes a real, bounded, safe action instead of
           continuing on whatever it was already doing while treating this
           tick as a no-op.
        """
        original = content
        try:
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end]
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end]
            elif "{" in content and "}" in content:
                start = content.find("{")
                end = content.rfind("}") + 1
                content = content[start:end]

            data = json.loads(content.strip())
            return VLMAction.from_dict(data)

        except json.JSONDecodeError:
            pass

        # Layer 2: pull individual fields out by regex, tolerating broken
        # JSON around them — the model's intent is usually still legible even
        # when the full blob doesn't parse.
        action_type_match = re.search(r'"action_type"\s*:\s*"(\w+)"', original)
        if action_type_match:
            try:
                action_type = ActionType(action_type_match.group(1))
            except ValueError:
                action_type = None
            if action_type is not None:
                def _field(name: str) -> Optional[str]:
                    m = re.search(rf'"{name}"\s*:\s*"([^"]*)"', original)
                    return m.group(1) if m else None

                def _num_field(name: str) -> Optional[int]:
                    m = re.search(rf'"{name}"\s*:\s*(-?\d+)', original)
                    return int(m.group(1)) if m else None

                return VLMAction(
                    action_type=action_type,
                    target_object=_field("target_object"),
                    message=_field("message"),
                    reasoning=(_field("reasoning") or "Recovered from malformed JSON")[:500],
                    point_x=_num_field("point_x"),
                    point_y=_num_field("point_y"),
                )

        # Layer 3: bare text keyword match, only for the terminal actions —
        # these are the only ones safe to infer from prose alone (no
        # navigation target/coordinates to get wrong).
        content_lower = original.lower()
        if "mission_complete" in content_lower or "mission complete" in content_lower:
            return VLMAction(action_type=ActionType.MISSION_COMPLETE,
                             message=original[:200], reasoning="Parsed from text response")
        if "phase_complete" in content_lower or "phase complete" in content_lower:
            return VLMAction(action_type=ActionType.PHASE_COMPLETE,
                             message=original[:200], reasoning="Parsed from text response")
        if "failed" in content_lower or "cannot" in content_lower:
            return VLMAction(action_type=ActionType.MISSION_FAILED,
                             message=original[:200], reasoning="Parsed from text response")

        # Nothing recoverable — a real anomaly (empty/garbage output that
        # survived decide()'s own retries), not a normal decision. Escalate,
        # don't shrug.
        return VLMAction(
            action_type=ActionType.ASK_CLOUD,
            message=f"VLM produced no usable output ({len(original)} chars, no recoverable action_type)",
            reasoning="parse_failed",
            parse_failed=True,
        )
    
    def describe_scene(self, image) -> str:
        """
        Get a natural language description of what's in the image.
        
        Args:
            image: Camera frame (path, bytes, or numpy array)
        
        Returns:
            Scene description string
        """
        if not self._available:
            return "VLM not available"
        
        try:
            image_uri = self._encode_image(
                image_path=image if isinstance(image, str) else None,
                image_bytes=image if isinstance(image, bytes) else None,
                image_array=image if hasattr(image, 'shape') else None,
            )
            
            response = self._chat(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_uri}},
                            {"type": "text", "text": "Describe what you see in this image briefly. Focus on objects, distances, and spatial layout relevant for drone navigation."},
                        ],
                    },
                ],
                max_tokens=300,
                temperature=0.1,
            )
            
            return response['choices'][0]['message']['content']
            
        except Exception as e:
            return f"Error describing scene: {e}"
    
    def ask(self, image, question: str, max_tokens: int = 200) -> str:
        """
        Ask a free-form question about an image and return the raw reply.

        describe_scene() and evaluate() both hard-code their own prompt shape
        (a navigation summary, and a 0-10 score respectively). Perception
        gating needs neither: it asks a closed question ("is a person in a red
        jacket visible?") and scores the answer itself. This is the
        unopinionated path for that.

        Args:
            image: Camera frame (path, bytes, or numpy array)
            question: The prompt to ask about the image
            max_tokens: Reply length cap

        Returns:
            Raw model reply, or a string starting with "Error" on failure.
        """
        if not self._available:
            return "Error: VLM not available"

        try:
            image_uri = self._encode_image(
                image_path=image if isinstance(image, str) else None,
                image_bytes=image if isinstance(image, bytes) else None,
                image_array=image if hasattr(image, 'shape') else None,
            )

            response = self._chat(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_uri}},
                            {"type": "text", "text": question},
                        ],
                    },
                ],
                max_tokens=max_tokens,
                temperature=0.1,
            )
            return response['choices'][0]['message']['content']

        except Exception as e:
            return f"Error asking VLM: {e}"

    def evaluate(self, image, criteria: str) -> Tuple[float, str]:
        """
        Evaluate something in the image based on criteria.
        
        Args:
            image: Camera frame
            criteria: What to evaluate (e.g., "aesthetic beauty of the tree")
        
        Returns:
            Tuple of (score 0-10, explanation)
        """
        if not self._available:
            return 0.0, "VLM not available"
        
        try:
            image_uri = self._encode_image(
                image_path=image if isinstance(image, str) else None,
                image_bytes=image if isinstance(image, bytes) else None,
                image_array=image if hasattr(image, 'shape') else None,
            )
            
            response = self._chat(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_uri}},
                            {"type": "text", "text": f"Evaluate what you see based on: {criteria}\n\nRespond with JSON: {{\"score\": <0-10>, \"explanation\": \"<brief reason>\"}}"},
                        ],
                    },
                ],
                max_tokens=200,
                temperature=0.1,
            )
            
            content = response['choices'][0]['message']['content']
            
            # Parse response
            try:
                if "```" in content:
                    start = content.find("{")
                    end = content.rfind("}") + 1
                    content = content[start:end]
                data = json.loads(content)
                return float(data.get("score", 5)), data.get("explanation", "")
            except:
                return 5.0, content[:200]
                
        except Exception as e:
            return 0.0, f"Error evaluating: {e}"
    
    def get_info(self) -> Dict[str, Any]:
        """Get information about the loaded VLM."""
        return {
            "available": self._available,
            "model_path": str(self.model_path) if self.model_path.exists() else None,
            "mmproj_path": str(self.mmproj_path) if self.mmproj_path.exists() else None,
        }


# Singleton instance
_vlm_service = None
# Guards CONSTRUCTION, not inference (VLMService has its own lock for that).
# Two MissionLoop threads starting together would otherwise both see None and
# each load a separate 5 GB model — the second load fails or thrashes, and the
# two aircraft would end up on different model instances.
_vlm_service_lock = threading.Lock()


def get_vlm_service() -> VLMService:
    """Get or create the VLM service singleton."""
    global _vlm_service
    if _vlm_service is None:
        with _vlm_service_lock:
            if _vlm_service is None:
                _vlm_service = VLMService()
    return _vlm_service


# Test function
def test_vlm():
    """Test the VLM service."""
    print("Testing VLM Service...")
    
    vlm = get_vlm_service()
    print(f"Info: {vlm.get_info()}")
    
    if not vlm.is_available():
        print("VLM not available - skipping inference test")
        return
    
    # Test with a sample mission
    mission_phase = {
        "objective": "Find and photograph the most interesting object in view",
        "success": "Photo taken of selected object",
    }
    
    print("\nTest requires a camera frame - skipping inference test")
    print("VLM service initialized successfully")


if __name__ == "__main__":
    test_vlm()
