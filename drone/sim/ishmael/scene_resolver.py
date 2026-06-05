"""Resolve a described scene ("a forest", "stadium", "village") to a loadable USD.

Backends behind one interface (``resolve_scene``):
  1. **Library** (ships now) — a keyword catalog (``scenes.yaml``) mapping words to
     Isaac built-in interiors and Yonder urban/wilderness USDs. Exact and fuzzy
     keyword matching, plus an optional vLLM classifier (reuses ``nlp``) that snaps
     an unseen description onto the nearest known keyword.
  2. **Generative hook** (interface only) — ``GenerativeSceneBackend.generate`` is an
     unimplemented extension point selected when library confidence is low. The stub
     raises ``NotImplementedError``; until it's wired, low-confidence scenes degrade to
     the nearest library stand-in (logged), so a run never blocks.

Yonder (urban/wilderness) USDs resolve against ``ISHMAEL_R2_BASE`` (a public r2.dev
base URL or a local mirror dir). With it unset, the resolver returns the keyword's
fallback built-in; ``IsaacVehicleBridge.setup`` itself further falls back to a ground
plane if any load fails.

The result is a ``SceneRef`` whose ``usd_url`` can be handed straight to the existing
``IsaacVehicleBridge`` / ``launch_fleet.py`` (which today take a scene *name*; the
director passes ``SceneRef.launch_value()`` — a URL when resolved, else the name).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_CATALOG_PATH = Path(__file__).with_name("scenes.yaml")


@dataclass
class SceneRef:
    name: str                      # the described scene, normalized
    category: str                  # builtin | urban | wilderness | generative
    sid: str                       # asset id within the category
    usd_url: Optional[str]         # resolvable URL, or None -> use fallback name
    fallback_builtin: str = "warehouse"
    confidence: str = "high"       # high | low
    backend: str = "library"       # library | generative | builtin
    notes: str = ""

    def launch_value(self) -> str:
        """What to pass to launch_fleet/IsaacVehicleBridge.

        A full USD URL when we resolved one; otherwise the fallback built-in *name*
        (office/warehouse/hospital), which the existing ``_ENV_USD_MAP`` understands.
        """
        return self.usd_url or self.fallback_builtin


# --- catalog loading ----------------------------------------------------------

def _load_catalog() -> dict:
    """Load scenes.yaml when PyYAML is available; else the baked-in default.

    The YAML file is the human-editable source of truth on deployments (the drone/
    Hoopoe envs ship PyYAML for config.yaml). _DEFAULT_CATALOG mirrors it so the
    resolver also works dependency-free (laptops, CI) without drifting silently —
    keep the two in sync when editing scenes.yaml.
    """
    try:
        import yaml
        with open(_CATALOG_PATH) as f:
            return yaml.safe_load(f)
    except Exception:
        return _DEFAULT_CATALOG


_OFFICE = ("https://omniverse-content-production.s3-us-west-2.amazonaws.com/"
           "Assets/Isaac/5.1/Isaac/Environments/Office/office.usd")
_WAREHOUSE = ("https://omniverse-content-production.s3-us-west-2.amazonaws.com/"
              "Assets/Isaac/5.1/Isaac/Environments/Simple_Warehouse/"
              "warehouse_with_forklifts.usd")
_HOSPITAL = ("https://omniverse-content-production.s3-us-west-2.amazonaws.com/"
             "Assets/Isaac/5.1/Isaac/Environments/Hospital/hospital.usd")

_DEFAULT_CATALOG = {
    "builtins": {"office": _OFFICE, "warehouse": _WAREHOUSE, "hospital": _HOSPITAL},
    "r2_prefix": {"urban": "simulator-environments/urban-envs",
                  "wilderness": "simulator-environments/wilderness-envs"},
    "keywords": {
        "office": {"category": "builtin", "sid": "office"},
        "workplace": {"category": "builtin", "sid": "office"},
        "room": {"category": "builtin", "sid": "office"},
        "indoor": {"category": "builtin", "sid": "office"},
        "warehouse": {"category": "builtin", "sid": "warehouse"},
        "hangar": {"category": "builtin", "sid": "warehouse"},
        "factory": {"category": "builtin", "sid": "warehouse"},
        "depot": {"category": "builtin", "sid": "warehouse"},
        "hospital": {"category": "builtin", "sid": "hospital"},
        "clinic": {"category": "builtin", "sid": "hospital"},
        "forest": {"category": "wilderness", "sid": "forest-seed00", "fallback": "warehouse"},
        "woods": {"category": "wilderness", "sid": "forest-seed00", "fallback": "warehouse"},
        "jungle": {"category": "wilderness", "sid": "forest-seed00", "fallback": "warehouse"},
        "desert": {"category": "wilderness", "sid": "desert-seed00", "fallback": "warehouse"},
        "mountain": {"category": "wilderness", "sid": "mountain-seed00", "fallback": "warehouse"},
        "hill": {"category": "wilderness", "sid": "mountain-seed00", "fallback": "warehouse"},
        "coast": {"category": "wilderness", "sid": "coast-seed00", "fallback": "warehouse"},
        "beach": {"category": "wilderness", "sid": "coast-seed00", "fallback": "warehouse"},
        "canyon": {"category": "wilderness", "sid": "canyon-seed00", "fallback": "warehouse"},
        "valley": {"category": "wilderness", "sid": "canyon-seed00", "fallback": "warehouse"},
        "outdoor": {"category": "wilderness", "sid": "forest-seed00", "fallback": "warehouse"},
        "city": {"category": "urban", "sid": "nyc-wallst", "fallback": "warehouse"},
        "downtown": {"category": "urban", "sid": "chicago-loop", "fallback": "warehouse"},
        "street": {"category": "urban", "sid": "paris-marais", "fallback": "warehouse"},
        "town": {"category": "urban", "sid": "naperville-il", "fallback": "warehouse"},
        "village": {"category": "urban", "sid": "levittown-ny", "fallback": "warehouse"},
        "suburb": {"category": "urban", "sid": "levittown-ny", "fallback": "warehouse"},
        "neighborhood": {"category": "urban", "sid": "naperville-il", "fallback": "warehouse"},
        "castle": {"category": "urban", "sid": "rome-centro", "fallback": "warehouse", "confidence": "low"},
        "palace": {"category": "urban", "sid": "rome-centro", "fallback": "warehouse", "confidence": "low"},
        "stadium": {"category": "urban", "sid": "chicago-loop", "fallback": "warehouse", "confidence": "low"},
        "arena": {"category": "urban", "sid": "chicago-loop", "fallback": "warehouse", "confidence": "low"},
        "cafe": {"category": "builtin", "sid": "office", "fallback": "office", "confidence": "low"},
        "restaurant": {"category": "builtin", "sid": "office", "fallback": "office", "confidence": "low"},
        "shop": {"category": "builtin", "sid": "office", "fallback": "office", "confidence": "low"},
    },
}


def _r2_url(prefix: str, sid: str) -> Optional[str]:
    base = os.environ.get("ISHMAEL_R2_BASE", "").rstrip("/")
    if not base:
        return None
    return f"{base}/{prefix}/{sid}/scene.usd"


# --- generative hook (extension point) ----------------------------------------

class GenerativeSceneBackend(ABC):
    """Pluggable backend that synthesizes a USD scene from a free-text description.

    Wire a concrete implementation and pass it to ``resolve_scene(generative=...)``
    to take over low-confidence scenes (e.g. 'castle', 'stadium') that have no exact
    library asset.
    """

    @abstractmethod
    def generate(self, description: str) -> str:
        """Return a path/URL to a generated ``scene.usd`` for ``description``."""
        raise NotImplementedError


class _NullGenerative(GenerativeSceneBackend):
    def generate(self, description: str) -> str:  # pragma: no cover - stub
        raise NotImplementedError(
            "No generative scene backend wired; low-confidence scene "
            f"'{description}' fell back to the nearest library asset.")


# --- matching -----------------------------------------------------------------

def _match_keyword(description: str, catalog: dict) -> Optional[tuple[str, dict]]:
    d = (description or "").strip().lower()
    kws = catalog["keywords"]
    if d in kws:
        return d, kws[d]
    # word-level / substring match
    for word in d.replace(",", " ").split():
        if word in kws:
            return word, kws[word]
    for k in kws:
        if k in d:
            return k, kws[k]
    return None


def _classify_with_vllm(description: str, catalog: dict) -> Optional[tuple[str, dict]]:
    """Ask the local vLLM to pick the closest known keyword for an unseen scene."""
    from . import nlp
    choices = sorted(catalog["keywords"].keys())
    prompt = ("Pick the single best match for this scene description from the list. "
              "Reply with ONLY one word from the list.\n"
              f"Description: {description}\nList: {', '.join(choices)}")
    base = nlp._vllm_url()
    model = nlp._vllm_model(base)
    if not model:
        return None
    import json
    import urllib.request
    body = json.dumps({"model": model, "temperature": 0.0, "max_tokens": 8,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.load(r)
        word = out["choices"][0]["message"]["content"].strip().lower().split()[0]
    except Exception:
        return None
    word = word.strip(".,")
    if word in catalog["keywords"]:
        return word, catalog["keywords"][word]
    return None


# --- public entry point -------------------------------------------------------

def resolve_scene(description: str, use_llm: bool = True,
                  generative: Optional[GenerativeSceneBackend] = None) -> SceneRef:
    catalog = _load_catalog()
    builtins = catalog["builtins"]
    prefixes = catalog["r2_prefix"]

    matched = _match_keyword(description, catalog)
    if matched is None and use_llm:
        matched = _classify_with_vllm(description, catalog)

    if matched is None:
        # total miss — default to office interior
        return SceneRef(name=description or "office", category="builtin", sid="office",
                        usd_url=builtins["office"], fallback_builtin="office",
                        confidence="low", backend="builtin",
                        notes="no keyword match; defaulted to office")

    key, entry = matched
    category = entry["category"]
    sid = entry["sid"]
    confidence = entry.get("confidence", "high")
    fallback = entry.get("fallback", "warehouse")

    # low-confidence + a real generative backend -> let it synthesize
    gen = generative
    if confidence == "low" and gen is not None and not isinstance(gen, _NullGenerative):
        try:
            usd = gen.generate(description)
            return SceneRef(name=key, category="generative", sid=sid, usd_url=usd,
                            fallback_builtin=fallback, confidence="high",
                            backend="generative", notes="synthesized")
        except NotImplementedError:
            pass  # fall through to library stand-in

    if category == "builtin":
        return SceneRef(name=key, category="builtin", sid=sid,
                        usd_url=builtins.get(sid, builtins["office"]),
                        fallback_builtin=sid if sid in builtins else fallback,
                        confidence=confidence, backend="builtin")

    # urban / wilderness via R2
    url = _r2_url(prefixes[category], sid)
    notes = "" if url else f"ISHMAEL_R2_BASE unset; using fallback '{fallback}'"
    return SceneRef(name=key, category=category, sid=sid, usd_url=url,
                    fallback_builtin=fallback, confidence=confidence,
                    backend="library", notes=notes)
