"""Live-model probe for MissionAgent's cloud brain — the real thing, not the scripted
brain in sdk's MissionCognitionTests.swift.

Walks the identical scenario as testGreenChairInOtherRoomAndBack (start room, two
doorways, an empty hallway behind one, a chair behind the other, then a return leg) but
drives it with REAL calls to the deployed Bedrock model, using this repo's actual
production prompt/tool schema (imported directly from aws/src/rover.py, not a paraphrase
of it). Between calls, this script plays "the world" and "MissionAgent" — updating pose,
visited candidates, visible/remembered objects, and echoing back the plan exactly as
MissionAgent's real update loop would — so what the model sees each tick matches what it
would see in production.

This is a diagnostic, not a test: it makes real (billed) Bedrock calls, so it does not run
in CI or the e2e gate. Run manually:

    cd eco && AWS_PROFILE=astral python3 rover/models/probe_mission_cognition.py
    cd eco && AWS_PROFILE=astral python3 rover/models/probe_mission_cognition.py --with-photo

Grade the transcript against: did it ask when genuinely ambiguous, explore sensibly,
recognize a dead end and switch doors, ground the chair, remember to return.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aws" / "src"))
import rover  # noqa: E402  (path insert must precede this import)

MAX_TICKS = 12

# Ground truth for the scripted "world" — matches MissionCognitionTests.swift exactly.
START = (0.0, 0.0)
DOORWAY_1 = (3.0, 2.0)   # hallway — nothing behind it
DOORWAY_2 = (3.0, -2.0)  # the chair's room
CHAIR = (6.0, -3.0)
CHAIR_VISIBLE_RADIUS = 3.5

# A real stock photo with a chair in it, for the --with-photo run (open-vocabulary
# grounding needs an actual image; there's no synthetic multi-room photo set for this
# scenario, so this only exercises "can it point at a chair", not the full room layout).
# URL resolved via Wikimedia's API (commons.wikimedia.org/w/api.php?action=query&...
# prop=imageinfo&iiprop=url), not guessed — Commons file URLs aren't a stable pattern.
STOCK_CHAIR_PHOTO_URL = "https://upload.wikimedia.org/wikipedia/commons/b/b9/Cast_iron_garden_chair_at_Boreham%2C_Essex%2C_England.jpg"


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


class World:
    """Plays both "the house" and MissionAgent's per-tick world-model update."""

    def __init__(self, with_photo: bool):
        self.pose = START
        self.candidates = {
            "opening_1": {"id": "opening_1", "x": DOORWAY_1[0], "y": DOORWAY_1[1],
                          "widthMeters": 1.0, "status": "unexplored"},
            "opening_2": {"id": "opening_2", "x": DOORWAY_2[0], "y": DOORWAY_2[1],
                          "widthMeters": 0.9, "status": "unexplored"},
        }
        self.remembered: dict[str, dict] = {}
        self.turns: list[dict] = []
        self.plan: str | None = None
        self.photo_b64 = self._load_photo() if with_photo else None

    @staticmethod
    def _load_photo() -> str:
        # Mirrors FrameEncoder.jpeg() (sdk's PhroverKit/Perception/FrameEncoder.swift):
        # downscale to a 512px longest side, JPEG quality ~0.6 — the real pipeline never
        # sends a full-resolution frame, and Bedrock rejects images over 5MB base64 anyway.
        print(f"Fetching stock photo: {STOCK_CHAIR_PHOTO_URL}", file=sys.stderr)
        req = Request(STOCK_CHAIR_PHOTO_URL, headers={"User-Agent": "Mozilla/5.0 (probe script)"})
        with urlopen(req, timeout=20) as r:
            raw = r.read()
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        longest = max(image.size)
        if longest > 512:
            scale = 512 / longest
            image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode()

    def chair_visible(self) -> bool:
        return distance(self.pose, CHAIR) < CHAIR_VISIBLE_RADIUS

    def record_turn(self, utterance: str) -> None:
        self.turns.append({"utterance": utterance, "pose": {"x": self.pose[0], "y": self.pose[1], "yaw": 0}})

    def visible_objects(self) -> list[dict]:
        if self.chair_visible():
            return [{"label": "chair", "confidence": 0.9, "x": 0.5, "y": 0.5}]
        return []

    def update_after(self, action: dict) -> None:
        """What the real MissionAgent would do to the world for this decision, before the
        next think-tick: move the rover, mark candidates visited, remember what's seen."""
        kind = action.get("action")
        if kind == "explore":
            cid = action.get("candidateId")
            if cid in self.candidates:
                self.candidates[cid]["status"] = "visited"
                self.pose = (self.candidates[cid]["x"], self.candidates[cid]["y"])
        elif kind == "navigate" and action.get("targetKind") == "worldPoint":
            if action.get("x") is not None and action.get("y") is not None:
                self.pose = (action["x"], action["y"])
        elif kind == "navigate" and action.get("targetKind") != "worldPoint":
            # imagePoint navigate with no real depth sensor here — approximate "arrived
            # near what it pointed at" only when the chair is actually visible/plausible,
            # otherwise leave pose unchanged and flag it (see main loop's warnings).
            if self.chair_visible():
                self.pose = CHAIR

        # Object permanence, mirroring MissionAgent.updateWorldModel(): whatever's visible
        # now gets pinned to world memory.
        if self.chair_visible():
            self.remembered["chair"] = {"label": "chair", "x": CHAIR[0], "y": CHAIR[1],
                                        "timesSeen": self.remembered.get("chair", {}).get("timesSeen", 0) + 1}
        # Being at (or having driven to) a candidate counts as visited.
        for c in self.candidates.values():
            if distance((c["x"], c["y"]), self.pose) < 1.0:
                c["status"] = "visited"

    def request_body(self, utterance: str | None) -> dict:
        return {
            "utterance": utterance,
            "frameJPEGBase64": self.photo_b64 if self.chair_visible() else None,
            "visibleObjects": self.visible_objects(),
            "pose": {"x": self.pose[0], "y": self.pose[1], "yaw": 0},
            "navState": "idle",
            "memory": {
                "turns": self.turns[-5:],
                "missionStartPose": {"x": START[0], "y": START[1], "yaw": 0},
                "rememberedObjects": list(self.remembered.values()),
            },
            "explorationCandidates": list(self.candidates.values()),
            "plan": self.plan,
            "lastAnswerWasInconclusive": False,
        }


def call_bedrock_directly(body: dict) -> dict:
    """Exactly what aws/src/rover.py's act_handler does, minus the Lambda/API-Gateway
    wrapper — same system prompt, same forced tool-use, same model."""
    content = []
    if body.get("frameJPEGBase64"):
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": body["frameJPEGBase64"]},
        })
    content.append({"type": "text", "text": rover._describe_mission(body)})

    response = rover.bedrock.invoke_model(
        modelId=rover.BEDROCK_MODEL_ID,
        body=json.dumps({
            "anthropic_version": rover.ANTHROPIC_VERSION,
            "system": rover.MISSION_AGENT_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": content}],
            "tools": [rover.DECIDE_TOOL],
            "tool_choice": {"type": "tool", "name": "decide"},
            "max_tokens": 500,
        }),
    )
    result = json.loads(response["body"].read())
    decision = next(
        (b["input"] for b in result.get("content", []) if b.get("type") == "tool_use" and b.get("name") == "decide"),
        None,
    )
    return decision or {"action": "say", "reasoning": "(no tool call — malformed response)"}


def run(with_photo: bool) -> None:
    world = World(with_photo=with_photo)
    utterance = "go to the green chair in the other room, then come back"
    world.record_turn(utterance)

    print(f"=== Mission: \"{utterance}\" (photo={'yes' if with_photo else 'no'}) ===\n")

    for tick in range(MAX_TICKS):
        body = world.request_body(utterance)
        print(f"--- tick {tick} --- pose={world.pose} "
              f"candidates={ {k: v['status'] for k, v in world.candidates.items()} } "
              f"visible={[o['label'] for o in body['visibleObjects']]}")

        decision = call_bedrock_directly(body)
        print(f"  -> {json.dumps(decision)}")
        if decision.get("reasoning"):
            print(f"     reasoning: {decision['reasoning']}")

        if decision.get("updated_plan"):
            world.plan = decision["updated_plan"]

        action = {
            "action": decision.get("action"),
            "candidateId": decision.get("candidate_id"),
            "targetKind": decision.get("target_kind"),
            "x": decision.get("x"),
            "y": decision.get("y"),
        }

        if decision.get("action") in ("done", "stop"):
            world.update_after(action)
            print(f"\n=== finished: {decision.get('action')} at tick {tick}, final pose={world.pose} ===")
            break

        if decision.get("action") == "ask":
            reply = "I'm not sure, sorry — try either one."
            world.record_turn(reply)
            utterance = reply
            world.update_after(action)
            continue

        world.update_after(action)
        utterance = None
    else:
        print(f"\n=== hit MAX_TICKS={MAX_TICKS} without done/stop — treat as a failure mode ===")

    print(f"\nFinal plan: {world.plan}")
    print(f"Final remembered objects: {world.remembered}")
    if distance(world.pose, START) < 0.5:
        print("Returned to start: YES")
    else:
        print(f"Returned to start: NO (pose={world.pose})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--with-photo", action="store_true",
                    help="Attach a real stock photo of a chair once the chair 'room' is reached, "
                         "to test actual open-vocabulary visual grounding.")
    args = ap.parse_args()
    run(with_photo=args.with_photo)
