"""Unit tests for the local MJPEG video bridge (drone/sim/video_bridge.py).

No Godot required: a fake DepotClient stands in for the IPC socket. Guards the
frame-discovery contract that bit once already — fw_all_states returns a *list*
of per-drone state dicts, not a dict keyed by id, so a `.keys()` read silently
yielded zero drones and served no video.

Run: python3 -m pytest drone/sim/tests/test_video_bridge.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # drone/sim

from video_bridge import FrameHub


class FakeClient:
    def __init__(self, states, frames):
        self._states = states
        self._frames = frames

    def _call(self, req):
        if req.get("op") == "fw_all_states":
            return {"ok": True, "states": self._states}
        return {"ok": False, "error": "unhandled"}

    def fw_grab_frame(self, did):
        return self._frames.get(did)


def _hub(states, frames):
    return FrameHub(host="x", port=0, fps=6.0, drones=None,
                    client=FakeClient(states, frames))


def test_discover_parses_list_of_state_dicts():
    # fw_all_states shape: a list, each element carrying its own "id".
    states = [{"id": "alpha", "position": [0, 0, 30]},
              {"id": "bravo", "position": [0, 0, 30]}]
    hub = _hub(states, {})
    assert hub._discover() == ["alpha", "bravo"]


def test_discover_ignores_malformed_entries():
    states = [{"id": "alpha"}, {"noid": True}, "garbage", {"id": ""}]
    hub = _hub(states, {})
    assert hub._discover() == ["alpha"]


def test_explicit_drones_override_discovery():
    hub = FrameHub(host="x", port=0, fps=6.0, drones=["only"],
                   client=FakeClient([{"id": "alpha"}], {}))
    assert hub._discover() == ["only"]


def test_discover_survives_client_error():
    class Boom:
        def _call(self, req):
            raise RuntimeError("socket down")

    hub = FrameHub(host="x", port=0, fps=6.0, drones=None, client=Boom())
    # No frames buffered yet -> empty, not an exception.
    assert hub._discover() == []


def test_latest_tracks_grabbed_frames_and_seq():
    hub = _hub([{"id": "alpha"}], {"alpha": b"\xff\xd8jpeg\xff\xd9"})
    # Simulate one grab-loop tick by hand (run() is an infinite loop).
    jpg = hub._client.fw_grab_frame("alpha")
    with hub._lock:
        hub._frames["alpha"] = jpg
        hub._seq["alpha"] = 1
    frame, seq = hub.latest("alpha")
    assert frame == b"\xff\xd8jpeg\xff\xd9" and seq == 1
    assert hub.latest("missing") == (None, 0)
    assert hub.drones() == ["alpha"]
