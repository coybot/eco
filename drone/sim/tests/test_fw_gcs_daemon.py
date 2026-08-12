"""Unit tests for the fixed-wing GCS bridge daemon's pure helpers — the parts
that shape the MQTT wire messages and turn spatial memory into a reported
target location. No mosquitto, no Godot, no VLM: heavy imports live inside the
daemon's start()/run_mission(), so importing the module here is cheap.

Run: python3 -m pytest drone/sim/tests/test_fw_gcs_daemon.py -v
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # drone/sim

import fw_gcs_daemon as d


class _LM:
    def __init__(self, x, y, z=0.0, score=0.9):
        self.x, self.y, self.z, self.score = x, y, z, score


class _Memory:
    """Duck-typed SpatialMemory: nearest(label) -> _LM or None."""
    def __init__(self, mapping):
        self._m = mapping

    def nearest(self, label):
        return self._m.get(label)


class _Mission:
    def __init__(self, phases, current_phase, mission_id="m1"):
        self.phases = phases
        self.current_phase = current_phase
        self.mission_id = mission_id

    def get_current_phase(self):
        return self.phases[self.current_phase] if self.current_phase < len(self.phases) else None


def test_enu_to_latlon_moves_north_and_east():
    datum = {"lat": 37.5, "lon": -122.0}
    c = d.enu_to_latlon(datum, east_m=1000.0, north_m=2000.0)
    assert c["lat"] > datum["lat"] and c["lon"] > datum["lon"]
    # north 2000 m ~ 0.018 deg lat
    assert abs((c["lat"] - datum["lat"]) - math.degrees(2000.0 / d.EARTH_RADIUS_M)) < 1e-9


def test_extract_target_prefers_first_configured_label_seen():
    mem = _Memory({"pickup truck": _LM(400.0, -30.0, score=0.8)})
    got = d.extract_target_location(mem, ["pickup truck", "car"],
                                    datum={"lat": 37.4, "lon": -122.1})
    assert got["label"] == "pickup truck"
    assert got["east_m"] == 400.0 and got["north_m"] == -30.0
    assert "lat" in got and "lon" in got


def test_extract_target_none_when_unseen():
    mem = _Memory({})
    assert d.extract_target_location(mem, ["pickup truck"], datum=None) is None


def test_extract_target_enu_only_without_datum():
    mem = _Memory({"car": _LM(100.0, 50.0)})
    got = d.extract_target_location(mem, ["pickup truck", "car"], datum=None)
    assert got["label"] == "car"
    assert "lat" not in got and got["east_m"] == 100.0


def test_progress_payload_shape():
    mission = _Mission(
        phases=[{"type": "arm_and_takeoff"},
                {"objective": "search the area for the pickup truck"}],
        current_phase=1)
    p = d.build_progress_payload("alpha", "c1", mission, "on station, searching")
    assert p["message_type"] == "mission_progress"
    assert p["phase"] == 1 and p["total_phases"] == 2
    assert p["objective"] == "search the area for the pickup truck"
    assert p["status"] == "in_progress" and p["text"] == "on station, searching"


def test_response_payload_embeds_target_location():
    result = {"success": True, "summary": "done", "phases_completed": 4,
              "total_phases": 4, "photos": []}
    target = {"label": "pickup truck", "east_m": 400.0, "north_m": -30.0,
              "lat": 37.42, "lon": -122.09}
    payload = d.build_response_payload("alpha", "c1", "m1", result, target)
    assert payload["result"]["target_location"] == target
    assert payload["result"]["mission_id"] == "m1"
    assert payload["mission_id"] == "m1"


def test_parse_enu():
    assert d.parse_enu("-20,10") == (-20.0, 10.0)
