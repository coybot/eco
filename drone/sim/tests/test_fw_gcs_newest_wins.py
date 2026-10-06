"""The sim aircraft obeys the fleet planner's after-launch commands the way a
real one does (daemon.py): a new mission replaces the running one instead of
queueing behind it, and an abort stops it. No Godot, no broker.

Run: cd eco && python3 -m pytest drone/sim/tests/test_fw_gcs_newest_wins.py -v
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # drone/sim

import fw_gcs_daemon as d


def _daemon():
    args = SimpleNamespace(
        drone_id="alpha", mqtt_host="127.0.0.1", mqtt_port=1883,
        mqtt_username=None, mqtt_password="tok", godot_host="127.0.0.1",
        godot_port=9978, home_enu=(-20.0, 10.0), spawn_alt=30.0,
        target_label="pickup truck", brain="vlm", datum_lat=37.4, datum_lon=-122.1)
    return d.FwGcsDaemon(args)


def _msg(payload, conv="c1"):
    return SimpleNamespace(topic=f"drone/alpha/chat/{conv}/command",
                           payload=json.dumps(payload).encode())


class Running:
    def __init__(self):
        self.aborted = False

    def abort(self):
        self.aborted = True


def _queued(daemon):
    items = []
    while not daemon._q.empty():
        items.append(daemon._q.get_nowait())
    return items


def test_a_new_mission_stops_the_running_one_and_replaces_anything_queued():
    dm = _daemon()
    dm._on_message(None, None, _msg({"action": "mission", "mission_id": "m1", "phases": [{}]}))
    dm._current = Running()
    cur = dm._current
    dm._on_message(None, None, _msg({"action": "mission", "mission_id": "m2", "phases": [{}],
                                      "geofence": {"keep_in": []}}))
    assert cur.aborted and dm._stop_reason == "superseded"
    q = _queued(dm)
    assert [i["mission_id"] for i in q] == ["m2"]
    assert q[0]["geofence"] == {"keep_in": []}


def test_an_abort_stops_the_running_mission_and_records_what_next():
    dm = _daemon()
    dm._current = Running()
    cur = dm._current
    dm._on_message(None, None, _msg({"action": "abort", "then": "land"}))
    assert cur.aborted and dm._stop_reason == "abort:land"
    assert _queued(dm) == []


def test_an_abort_with_nothing_running_is_answered_not_dropped():
    dm = _daemon()
    dm._on_message(None, None, _msg({"action": "abort", "then": "return_home"}))
    q = _queued(dm)
    assert len(q) == 1 and q[0]["kind"] == "abort" and q[0]["then"] == "return_home"


def test_after_stop_does_what_the_abort_asked():
    dm = _daemon()
    calls = []
    backend = SimpleNamespace(land=lambda: calls.append("land"),
                              goto=lambda **kw: calls.append(("goto", kw["north_m"], kw["east_m"])))
    dm._after_stop("land", backend)
    dm._after_stop("return_home", backend)
    dm._after_stop("hold", backend)
    assert calls == ["land", ("goto", 10.0, -20.0)]
