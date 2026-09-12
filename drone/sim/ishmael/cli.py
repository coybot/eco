"""Ishmael command-line entry point.

    python -m ishmael.cli "2 rovers and 3 quads search the office for a chair \
        and send a picture to the app"

    # just parse and print the structured spec (no launch):
    python -m ishmael.cli --parse-only "a drone explores a forest and records video"

Run from ``eco/drone/sim`` (so the sibling ``fleet.py`` / ``launch_fleet.py`` import),
or with that directory on PYTHONPATH. On Hoopoe, invoke with the Isaac venv python
only for the launch step — parsing works with any python.
"""

from __future__ import annotations

import argparse
import sys

from .nlp import parse_test_spec
from .director import DirectorConfig, run as run_spec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ishmael", description=__doc__)
    ap.add_argument("description", help="natural-language test description")
    ap.add_argument("--parse-only", action="store_true",
                    help="print the parsed TestSpec and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve roster but do not launch Isaac or dispatch")
    ap.add_argument("--no-llm", action="store_true",
                    help="skip the vLLM, use the rule-based parser only")
    ap.add_argument("--no-register", action="store_true",
                    help="do not write DynamoDB registry rows")
    ap.add_argument("--engine", choices=["coybot_sim", "isaac"], default=None,
                    help="sim engine: coybot_sim = the project's Godot engine (default, "
                         "cross-platform); isaac = the optional photoreal engine on hoopoe")
    ap.add_argument("--certs-base", default=None)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)

    spec = parse_test_spec(args.description, use_llm=not args.no_llm)
    print(spec.to_json())
    if args.parse_only:
        return 0

    cfg = DirectorConfig(register=not args.no_register, dry_run=args.dry_run)
    if args.engine:
        cfg.engine = args.engine
    if args.certs_base:
        cfg.certs_base = args.certs_base
    if args.out_dir:
        cfg.out_dir = args.out_dir

    result = run_spec(spec, cfg)
    print("\n=== result ===")
    print(f"ok={result.ok} online={result.online} "
          f"images={len(result.images)} videos={len(result.videos)}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
