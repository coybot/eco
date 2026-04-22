#!/usr/bin/env python3
"""
Mission runner stub for local control API.

This module is intentionally deterministic and minimal:
- Accepts structured mission JSON only.
- Writes mission state to a local file used by the network manager.
- Provides a safe abort path.

ROS2/MAVROS integration should be implemented inside start_mission()
and abort_mission() without changing the JSON schema.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("astral.mission")

STATE_DIR = Path("/var/lib/astral")
MISSION_ACTIVE_PATH = STATE_DIR / "mission_active.json"
MISSION_LAST_PATH = STATE_DIR / "mission_last.json"


@dataclass
class MissionStatus:
    mission_id: Optional[str]
    active: bool
    started_at: Optional[float]
    last_error: Optional[str]


class MissionRunner:
    def __init__(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    def start_mission(self, mission: Dict) -> MissionStatus:
        mission_id = f"mission-{int(time.time())}"
        logger.info("Starting mission id=%s goal=%s", mission_id, mission.get("goal"))

        # TODO: Replace with ROS2/MAVROS integration.
        self._write_mission_last(mission_id, mission, active=True, error=None)
        return MissionStatus(mission_id=mission_id, active=True, started_at=time.time(), last_error=None)

    def abort_mission(self, reason: str = "operator_abort") -> MissionStatus:
        logger.warning("Aborting mission (%s)", reason)

        # TODO: Replace with ROS2/MAVROS abort sequence.
        self._set_active(False, error=reason)
        return self.get_status()

    def complete_mission(self) -> None:
        self._set_active(False, error=None)

    def get_status(self) -> MissionStatus:
        data = self._read_state()
        return MissionStatus(
            mission_id=data.get("mission_id"),
            active=bool(data.get("active", False)),
            started_at=data.get("started_at"),
            last_error=data.get("last_error"),
        )

    def _read_state(self) -> Dict:
        if not MISSION_ACTIVE_PATH.exists():
            return {}
        try:
            return json.loads(MISSION_ACTIVE_PATH.read_text() or "{}")
        except Exception:
            return {}

    def _set_active(self, active: bool, error: Optional[str]) -> None:
        current = self._read_state()
        payload = {
            "mission_id": current.get("mission_id"),
            "active": active,
            "started_at": current.get("started_at"),
            "last_error": error,
            "updated_at": time.time(),
        }
        MISSION_ACTIVE_PATH.write_text(json.dumps(payload))

    def _write_mission_last(self, mission_id: str, mission: Dict, active: bool, error: Optional[str]) -> None:
        payload = {
            "mission_id": mission_id,
            "active": active,
            "started_at": time.time(),
            "last_error": error,
            "mission": mission,
        }
        MISSION_ACTIVE_PATH.write_text(json.dumps(payload))
        MISSION_LAST_PATH.write_text(json.dumps(payload))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    runner = MissionRunner()
    status = runner.start_mission({"goal": "test", "target": {}, "constraints": {}, "failsafes": {}})
    print(status)
