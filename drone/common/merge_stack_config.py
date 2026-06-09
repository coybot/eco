#!/usr/bin/env python3
"""Merge stack-level keys from config.stack-dev.yaml into an existing config.yaml."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=Path, help="config.yaml to update in place")
    ap.add_argument("stack", type=Path, help="config.stack-*.yaml with backend URLs")
    args = ap.parse_args()

    if not args.stack.is_file():
        print(f"Stack file not found: {args.stack}", file=sys.stderr)
        sys.exit(1)

    with open(args.stack, "r", encoding="utf-8") as f:
        stack = yaml.safe_load(f) or {}

    if args.target.is_file():
        with open(args.target, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}

    for k, v in stack.items():
        if v is not None:
            cfg[k] = v

    args.target.parent.mkdir(parents=True, exist_ok=True)
    with open(args.target, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
