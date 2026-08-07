"""Presidio analog-piloting training package (Phase 0/1).

Builds the learned mid-level control policy that replaces discrete waypoint hops with
smooth, continuous velocity + yaw-rate setpoints, while ArduPilot keeps closing the inner
loop. See the plan: train on the 5090s, run the tiny exported net on the Orin Nano.

Modules:
- contract:  shared state/action definition for quad + rover (the policy I/O schema).
- expert:    smooth "teacher" controllers (minimum-jerk quad, pure-pursuit rover).
- dataset:   roll out the expert over randomized scenarios -> JSONL in data_recorder format.
- train:     behavioral-clone a small temporal (GRU) policy and export ONNX + state-norm.

The rule-based reactive_planner (eco/drone/common/reactive_planner.py) remains the runtime
safety fallback; this package produces the learned alternative behind a flag.
"""

from .contract import (
    STATE_DIM,
    ACTION_DIM,
    STATE_FIELDS,
    ACTION_FIELDS,
    VEHICLE_QUAD,
    VEHICLE_ROVER,
    State,
    build_state,
)

__all__ = [
    "STATE_DIM",
    "ACTION_DIM",
    "STATE_FIELDS",
    "ACTION_FIELDS",
    "VEHICLE_QUAD",
    "VEHICLE_ROVER",
    "State",
    "build_state",
]
