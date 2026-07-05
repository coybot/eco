"""Shared plumbing for the e2e harness: scenario loading and drone-id conventions.

Kept dependency-free (stdlib + pyyaml only) so ``run_e2e.py --tier fast`` never needs
boto3/awscrt — those are imported lazily, only by the live-tier code paths that need them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

E2E_DIR = Path(__file__).resolve().parent.parent
SCENARIOS_PATH = E2E_DIR / "scenarios.yaml"

DAEMON_TYPES = ("quadcopter", "rover", "fixedwing")
ALL_TYPES = DAEMON_TYPES + ("phrover",)


def load_scenarios() -> dict[str, Any]:
    return yaml.safe_load(SCENARIOS_PATH.read_text())


def drone_id(vehicle_type: str, suffix: str = "e2e") -> str:
    """Deterministic sim drone id, matching fleet.parse_roster's 'sim-<type>-<n>' shape
    closely enough for a human to recognize it in the DynamoDB registry / app fleet list."""
    return f"sim-{vehicle_type}-{suffix}"
