"""Parse an English test description into a structured ``TestSpec``.

    "2 rovers and 3 quadcopters search the office for a chair and send a
     picture to the app"
        ->
    TestSpec(vehicles=[VehicleReq('rover',2), VehicleReq('quadcopter',3)],
             scene='office', objective='search for a chair', target='chair',
             mobile_action='send_picture')

Two backends, tried in order:
  1. **Local vLLM** on Hoopoe (OpenAI-compatible chat completion) — the Qwen model
     already served under ``/opt/ml/llm-serve``. Endpoint from ``ISHMAEL_VLLM_URL``
     (default ``http://localhost:8000/v1``); model from ``ISHMAEL_VLLM_MODEL`` or the
     first entry of ``/v1/models``. Returns strict JSON.
  2. **Rule-based fallback** — deterministic regex/keyword parser over the known
     vocabulary. Always available, no network, used when the vLLM is unreachable or
     ``use_llm=False``.

The rule parser also *validates* LLM output: anything the LLM returns is normalized
through the same vocabulary, so a hallucinated vehicle type or scene degrades to the
nearest known value rather than crashing the director.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

# --- vocabulary ---------------------------------------------------------------

_VEHICLE_ALIASES = {
    "quad": "quadcopter", "quads": "quadcopter", "quadcopter": "quadcopter",
    "quadcopters": "quadcopter", "drone": "quadcopter", "drones": "quadcopter",
    "copter": "quadcopter", "uav": "quadcopter", "uavs": "quadcopter",
    "rover": "rover", "rovers": "rover", "carter": "rover", "carters": "rover",
    "ground": "rover", "car": "rover", "cars": "rover", "robot": "rover",
    "robots": "rover",
}

# Scenes the resolver understands today (branch 2 extends this list). Kept here so
# the parser can snap a described scene to a known keyword.
_KNOWN_SCENES = [
    "office", "warehouse", "hospital", "hangar", "outdoor",
    "stadium", "forest", "cafe", "castle", "village", "city", "desert",
    "mountain", "coast", "canyon",
]

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "single": 1, "pair": 2, "couple": 2,
}

_MOBILE_ACTIONS = ("send_picture", "send_video", "none")


@dataclass
class VehicleReq:
    type: str          # "quadcopter" | "rover"
    count: int

    def normalized(self) -> "VehicleReq":
        vt = _VEHICLE_ALIASES.get(self.type.strip().lower(), self.type.strip().lower())
        if vt not in ("quadcopter", "rover"):
            vt = "quadcopter"
        return VehicleReq(type=vt, count=max(1, int(self.count)))


@dataclass
class TestSpec:
    vehicles: list[VehicleReq] = field(default_factory=list)
    scene: str = "office"
    objective: str = ""
    target: Optional[str] = None
    mobile_action: str = "send_picture"
    vantage: bool = False        # also record an overhead vantage video
    photoreal: bool = False      # request photorealistic rendering (branch 4)
    raw: str = ""

    # -- helpers used by the director ------------------------------------------
    def fleet_arg(self) -> str:
        """Compose the ``--fleet`` spec fleet.parse_roster expects: 'quad:2,rover:3'."""
        parts = []
        for v in self.vehicles:
            kind = "quad" if v.type == "quadcopter" else "rover"
            parts.append(f"{kind}:{v.count}")
        return ",".join(parts) or "quad:1"

    def total_vehicles(self) -> int:
        return sum(v.count for v in self.vehicles)

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2)

    def normalized(self) -> "TestSpec":
        vehicles = [v.normalized() for v in self.vehicles] or [VehicleReq("quadcopter", 1)]
        # merge duplicate types
        merged: dict[str, int] = {}
        for v in vehicles:
            merged[v.type] = merged.get(v.type, 0) + v.count
        vehicles = [VehicleReq(t, c) for t, c in merged.items()]
        scene = _snap_scene(self.scene)
        action = self.mobile_action if self.mobile_action in _MOBILE_ACTIONS else "send_picture"
        return TestSpec(vehicles=vehicles, scene=scene,
                        objective=self.objective.strip(),
                        target=(self.target or None),
                        mobile_action=action,
                        vantage=self.vantage,
                        photoreal=self.photoreal,
                        raw=self.raw)


def _snap_scene(scene: str) -> str:
    s = (scene or "").strip().lower()
    if not s:
        return "office"
    if s in _KNOWN_SCENES:
        return s
    for k in _KNOWN_SCENES:
        if k in s or s in k:
            return k
    return s  # leave it; the scene resolver (branch 2) does fuzzy/LLM mapping


# --- rule-based parser --------------------------------------------------------

def _parse_rule_based(text: str) -> TestSpec:
    t = text.strip()
    low = t.lower()

    # vehicles: "<number> <vehicle-word>" — number may be a digit or a number word
    vehicles: list[VehicleReq] = []
    num = r"(\d+|" + "|".join(re.escape(w) for w in _NUMBER_WORDS) + r")"
    veh = r"(" + "|".join(re.escape(w) for w in _VEHICLE_ALIASES) + r")"
    for m in re.finditer(num + r"\s+" + veh, low):
        count_tok, veh_tok = m.group(1), m.group(2)
        count = int(count_tok) if count_tok.isdigit() else _NUMBER_WORDS.get(count_tok, 1)
        vehicles.append(VehicleReq(_VEHICLE_ALIASES[veh_tok], count))
    # bare vehicle word with no number (e.g. "a rover and a quadcopter" handled above
    # via number words; "rovers search" with no count -> 1)
    if not vehicles:
        for m in re.finditer(veh, low):
            vehicles.append(VehicleReq(_VEHICLE_ALIASES[m.group(1)], 1))

    # scene: prefer "in/at/the <scene>" but accept a bare known scene word
    scene = "office"
    for k in _KNOWN_SCENES:
        if re.search(r"\b" + re.escape(k) + r"\b", low):
            scene = k
            break

    # target: noun after "for a/an/the" (e.g. "search ... for a chair")
    target = None
    mt = re.search(r"\bfor\s+(?:a|an|the)?\s*([a-z][a-z\s]*?)(?:\s+and\b|[.,]|$| and )", low)
    if mt:
        target = mt.group(1).strip().split(" and ")[0].strip() or None

    # objective verb phrase
    objective = ""
    mo = re.search(r"\b(search|find|look\s+for|inspect|patrol|explore|scan|map|survey)"
                   r"(?:es|s|ing)?\b", low)
    if mo:
        verb = mo.group(1)
        if target and verb not in ("look for",):
            objective = f"{verb} for {target}"
        elif target:
            objective = f"{verb} {target}"
        else:
            objective = f"{verb} the {scene}"

    # record_vantage intent: "from above", "from a vantage", "overhead shot"
    vantage = bool(re.search(
        r"\b(vantage|overhead|bird.?s.?eye|from above|wide.?shot|exterior shot)\b", low))

    # mobile action
    if re.search(r"\b(video|footage|recording|record)\b", low):
        mobile_action = "send_video"
    elif re.search(r"\b(picture|photo|image|pic|snapshot|still)\b", low):
        mobile_action = "send_picture"
    else:
        mobile_action = "send_picture"

    photoreal = bool(re.search(r"\b(photorealistic|photoreal|realistic|lifelike|cinematic)\b",
                               low))

    return TestSpec(vehicles=vehicles, scene=scene, objective=objective,
                    target=target, mobile_action=mobile_action,
                    vantage=vantage, photoreal=photoreal, raw=text).normalized()


# --- vLLM backend -------------------------------------------------------------

_LLM_SYSTEM = (
    "You convert a natural-language drone simulation test description into JSON. "
    "Output ONLY a JSON object, no prose, no markdown fences. Schema:\n"
    "{\n"
    '  "vehicles": [{"type": "quadcopter"|"rover", "count": <int>}],\n'
    '  "scene": "<one or two words, e.g. office, forest, stadium, castle, village, cafe>",\n'
    '  "objective": "<short verb phrase, e.g. search for a chair>",\n'
    '  "target": "<object to find, or null>",\n'
    '  "mobile_action": "send_picture"|"send_video"|"none"\n'
    "}\n"
    "Map 'drone' to 'quadcopter' and 'ground robot'/'carter' to 'rover'. "
    "If a count is missing, use 1."
)

_LLM_EXAMPLES = [
    ("2 rovers and 3 quadcopters search the office for a chair and send a picture to the app",
     {"vehicles": [{"type": "rover", "count": 2}, {"type": "quadcopter", "count": 3}],
      "scene": "office", "objective": "search for a chair", "target": "chair",
      "mobile_action": "send_picture"}),
    ("a single drone explores a forest and records the video",
     {"vehicles": [{"type": "quadcopter", "count": 1}], "scene": "forest",
      "objective": "explore the forest", "target": None, "mobile_action": "send_video"}),
]


def _vllm_url() -> str:
    return os.environ.get("ISHMAEL_VLLM_URL", "http://localhost:8000/v1").rstrip("/")


def _vllm_model(base: str) -> Optional[str]:
    if os.environ.get("ISHMAEL_VLLM_MODEL"):
        return os.environ["ISHMAEL_VLLM_MODEL"]
    try:
        import urllib.request
        with urllib.request.urlopen(base + "/models", timeout=5) as r:
            data = json.load(r)
        models = data.get("data") or []
        return models[0]["id"] if models else None
    except Exception:
        return None


def _parse_with_vllm(text: str) -> Optional[TestSpec]:
    import urllib.request
    base = _vllm_url()
    model = _vllm_model(base)
    if not model:
        return None
    messages = [{"role": "system", "content": _LLM_SYSTEM}]
    for ex_text, ex_json in _LLM_EXAMPLES:
        messages.append({"role": "user", "content": ex_text})
        messages.append({"role": "assistant", "content": json.dumps(ex_json)})
    messages.append({"role": "user", "content": text})
    body = json.dumps({
        "model": model, "messages": messages, "temperature": 0.0, "max_tokens": 512,
    }).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.load(r)
        content = out["choices"][0]["message"]["content"]
    except Exception:
        return None
    obj = _extract_json(content)
    if obj is None:
        return None
    try:
        vehicles = [VehicleReq(v.get("type", "quadcopter"), int(v.get("count", 1)))
                    for v in obj.get("vehicles", [])]
        spec = TestSpec(
            vehicles=vehicles,
            scene=obj.get("scene", "office"),
            objective=obj.get("objective", ""),
            target=obj.get("target"),
            mobile_action=obj.get("mobile_action", "send_picture"),
            raw=text,
        )
        return spec.normalized()
    except Exception:
        return None


def _extract_json(content: str) -> Optional[dict]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-z]*\n?", "", content)
        content = re.sub(r"\n?```$", "", content).strip()
    try:
        return json.loads(content)
    except Exception:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


# --- public entry point -------------------------------------------------------

def parse_test_spec(text: str, use_llm: bool = True) -> TestSpec:
    """Parse ``text`` into a normalized ``TestSpec``.

    Tries the local vLLM first (when ``use_llm`` and reachable), else falls back to
    the deterministic rule parser. The result is always normalized so the director
    can trust it.
    """
    if use_llm:
        spec = _parse_with_vllm(text)
        if spec is not None and spec.vehicles:
            return spec
    return _parse_rule_based(text)
