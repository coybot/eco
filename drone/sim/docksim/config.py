"""Load the dock/Bee35 config blocks from docking/vehicles.yaml."""
from __future__ import annotations

import copy
import os
from functools import lru_cache

import yaml

CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docking", "vehicles.yaml")
G_M_S2 = 9.80665
RHO_KG_M3 = 1.225


@lru_cache(maxsize=1)
def _load():
    with open(CFG_PATH) as f:
        return yaml.safe_load(f)


def load_cfg() -> dict:
    """Deep copy of the whole config (callers may modify it for variants)."""
    return copy.deepcopy(_load())
