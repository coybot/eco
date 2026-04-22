"""Parse a natural-language prompt into a target object noun.

V1: regex-based. Handles phrasings like:
  - "go to the nearest person and come back"
  - "find the nearest chair"
  - "fly to a bottle and return"
The VLM target-selector from the papers (Qwen/SmolVLM) can replace this later.
"""

import re
from dataclasses import dataclass
from typing import Optional

_VERBS = r"(?:go\s+to|navigate\s+to|fly\s+to|find|locate|visit)"
_RETURN = r"(?:come|go|fly|return)\s+back|rtl|return\s+to\s+(?:launch|home|start)"

_PATTERN = re.compile(
    rf"{_VERBS}\s+(?:the\s+|a\s+|an\s+)?(?:nearest\s+|closest\s+)?"
    rf"(?P<target>[A-Za-z][A-Za-z\s\-]*?)"
    rf"(?:\s+and\s+(?:{_RETURN})|\s*$)",
    re.IGNORECASE,
)


@dataclass
class ParsedPrompt:
    target: str            # noun phrase to look for, e.g. "person"
    return_home: bool      # whether the prompt asks for come-back
    raw: str


def parse(prompt: str) -> ParsedPrompt:
    """Extract target noun + return flag from a natural-language prompt.

    Raises ValueError if no target can be extracted.
    """
    m = _PATTERN.search(prompt.strip())
    if not m:
        raise ValueError(
            f"Could not parse a target from prompt: {prompt!r}. "
            f"Expected something like 'go to the nearest X and come back'."
        )
    target = m.group("target").strip().lower()
    # Normalize multi-space
    target = re.sub(r"\s+", " ", target)
    return ParsedPrompt(
        target=target,
        return_home=bool(re.search(_RETURN, prompt, re.IGNORECASE)),
        raw=prompt,
    )


if __name__ == "__main__":
    for p in [
        "go to the nearest person and come back",
        "find the nearest chair",
        "fly to a red chair and return",
        "navigate to the closest dining table and go back",
    ]:
        pp = parse(p)
        print(f"{p!r} -> target={pp.target!r} return={pp.return_home}")
