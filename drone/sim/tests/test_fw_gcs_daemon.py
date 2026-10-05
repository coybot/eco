"""Unit tests for the fixed-wing GCS bridge daemon's pure helpers — the parts
that shape the MQTT wire messages and turn spatial memory into a reported
target location. No mosquitto, no Godot, no VLM: heavy imports live inside the
daemon's start()/run_mission(), so importing the module here is cheap.

Run: python3 -m pytest drone/sim/tests/test_fw_gcs_daemon.py -v
"""
import math
import sys
from pathlib import Path
from types import SimpleNamespace

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


# --- landmarks_payload --------------------------------------------------

class _FullLM:
    """Duck-typed Landmark: everything landmarks_payload() reads."""
    def __init__(self, label, x, y, z=0.0, score=0.9, hits=1, image_url=None):
        self.label, self.x, self.y, self.z = label, x, y, z
        self.score, self.hits, self.image_url = score, hits, image_url


class _MemoryAll:
    """Duck-typed SpatialMemory: all() -> list[_FullLM]."""
    def __init__(self, landmarks):
        self._landmarks = landmarks

    def all(self):
        return self._landmarks


def test_landmarks_payload_shape_and_latlon():
    mem = _MemoryAll([_FullLM("car", 100.0, 50.0, 1.2, 0.8, hits=3,
                              image_url="http://gcs/x.jpg")])
    out = d.landmarks_payload(mem, datum={"lat": 37.4, "lon": -122.1})
    assert len(out) == 1
    e = out[0]
    assert e["label"] == "car" and e["east_m"] == 100.0 and e["north_m"] == 50.0
    assert e["alt_m"] == 1.2 and e["score"] == 0.8 and e["hits"] == 3
    assert e["image_url"] == "http://gcs/x.jpg"
    assert "lat" in e and "lon" in e


def test_landmarks_payload_omits_latlon_without_datum():
    mem = _MemoryAll([_FullLM("car", 100.0, 50.0)])
    out = d.landmarks_payload(mem, datum=None)
    assert "lat" not in out[0] and "lon" not in out[0]


def test_landmarks_payload_excludes_teammate():
    mem = _MemoryAll([_FullLM("teammate", 1.0, 2.0), _FullLM("car", 3.0, 4.0)])
    out = d.landmarks_payload(mem)
    assert [e["label"] for e in out] == ["car"]


def test_landmarks_payload_sorts_by_hits_desc_and_caps():
    lms = [_FullLM(f"obj{i}", float(i), 0.0, hits=i) for i in range(20)]
    mem = _MemoryAll(lms)
    out = d.landmarks_payload(mem, limit=15)
    assert len(out) == 15
    assert [e["hits"] for e in out] == sorted((e["hits"] for e in out), reverse=True)
    assert out[0]["hits"] == 19  # the most-confirmed object survives the cap


# --- build_heartbeat -----------------------------------------------------

def test_build_heartbeat_yaw_zero_faces_east_heading_90():
    state = {"ok": True, "position": [10.0, 20.0, 30.0], "yaw": 0.0, "airspeed": 24.0}
    hb = d.build_heartbeat("alpha", True, state, datum={"lat": 37.4, "lon": -122.1})
    assert hb["heading_deg"] == 90.0
    assert hb["airspeed_mps"] == 24.0
    assert hb["position_enu"] == {"east_m": 10.0, "north_m": 20.0, "alt_m": 30.0}
    assert "latitude" in hb["position"] and "longitude" in hb["position"]
    assert hb["position"]["altitude"] == 30.0


def test_build_heartbeat_yaw_north_heading_zero():
    state = {"ok": True, "position": [0.0, 0.0, 30.0], "yaw": math.pi / 2, "airspeed": 20.0}
    hb = d.build_heartbeat("alpha", False, state)
    assert hb["heading_deg"] == 0.0
    assert "position" not in hb  # no datum -> no lat/lon, just position_enu


def test_build_heartbeat_omits_position_fields_without_state():
    hb = d.build_heartbeat("alpha", False, None)
    assert "position" not in hb and "position_enu" not in hb and "heading_deg" not in hb
    assert hb["droneId"] == "alpha" and hb["status"] == "online"


def test_build_heartbeat_omits_position_on_failed_call():
    hb = d.build_heartbeat("alpha", False, {"ok": False})
    assert "position_enu" not in hb


# --- _anchor_phases_to_sim preserves explicit coordinates -----------------

class _FakeBrainClient:
    def __init__(self, center=(400.0, 0.0), radius=120.0):
        self._center, self._radius = center, radius

    def fw_env_state(self):
        return {"search": {"center": list(self._center), "radius": self._radius}}


def _make_daemon():
    args = SimpleNamespace(
        drone_id="alpha", mqtt_host="127.0.0.1", mqtt_port=1883,
        mqtt_username=None, mqtt_password="tok", godot_host="127.0.0.1",
        godot_port=9978, home_enu=(-20.0, 10.0), spawn_alt=30.0,
        target_label="pickup truck", brain="vlm",
        datum_lat=37.4, datum_lon=-122.1,
    )
    daemon = d.FwGcsDaemon(args)
    daemon.brain_client = _FakeBrainClient()
    return daemon


def test_anchor_preserves_explicit_east_coordinate():
    daemon = _make_daemon()
    phases = [{"objective": "Fly to the car at east=412, north=-38 "
                            "(metres, local frame) and photograph it"}]
    out = daemon._anchor_phases_to_sim(phases)
    assert out[0]["objective"] == phases[0]["objective"]  # untouched


def test_anchor_preserves_case_and_space_variant():
    daemon = _make_daemon()
    phases = [{"objective": "Fly to the car at East = 412, north=-38"}]
    out = daemon._anchor_phases_to_sim(phases)
    assert out[0]["objective"] == phases[0]["objective"]


def test_anchor_rewrites_bare_objective():
    daemon = _make_daemon()
    phases = [{"objective": "Search the area for the pickup truck"}]
    out = daemon._anchor_phases_to_sim(phases)
    assert "east=400" in out[0]["objective"]
    assert "north=0" in out[0]["objective"]


def test_anchor_leaves_typed_phases_alone():
    daemon = _make_daemon()
    phases = [{"type": "arm_and_takeoff", "altitude_m": 30}]
    out = daemon._anchor_phases_to_sim(phases)
    assert out == phases


# --- GcsPhotoUploader --------------------------------------------------
# Two-step flow (presigned URL, then PUT) — NOT a direct Bearer-token PUT to
# /images/{key}: that path needs a genuine per-drone token from
# AuthStore.ensure_drone_token(), which nothing in this GCS ever calls (a
# real 403 hit live before this was fixed — see the class docstring).

class _FakeBackend:
    def __init__(self, frame=b"\xff\xd8fakejpeg\xff\xd9"):
        self._frame = frame

    def capture_frame(self):
        return self._frame


def _fake_presign(image_url_suffix="drones/alpha/conversations/conv1/x.jpg"):
    """A post_json stand-in matching upload_url_handler's real response shape."""
    calls = []

    def post_json(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        base = "http://127.0.0.1:8080"
        return 200, {
            "upload_url": f"{base}/images/{image_url_suffix}?token=abc.123",
            "image_url": f"{base}/images/{image_url_suffix}",
        }
    return post_json, calls


def test_photo_uploader_requests_presigned_url_with_operator_bearer_token():
    post_json, calls = _fake_presign()
    put_calls = []

    def put_bytes(url, data):
        put_calls.append((url, data))
        return 200

    up = d.GcsPhotoUploader(_FakeBackend(), "alpha", "conv1",
                            "http://127.0.0.1:8080", "operator-token",
                            http_post_json=post_json, http_put_bytes=put_bytes)
    url = up.capture_photo()
    assert url == "http://127.0.0.1:8080/images/drones/alpha/conversations/conv1/x.jpg"
    # The presign request hit the real upload-url endpoint, authenticated as
    # the OPERATOR (not a nonexistent drone token).
    assert len(calls) == 1
    assert calls[0]["url"] == ("http://127.0.0.1:8080/drones/alpha/conversations/"
                               "conv1/upload-url")
    assert calls[0]["headers"]["Authorization"] == "Bearer operator-token"
    assert calls[0]["body"]["filename"].startswith("photo_")
    # The actual bytes went to the presigned PUT url, not the base endpoint —
    # and with no Authorization header (the HMAC in the URL is the auth).
    assert len(put_calls) == 1
    put_url, data = put_calls[0]
    assert put_url == "http://127.0.0.1:8080/images/drones/alpha/conversations/conv1/x.jpg?token=abc.123"
    assert data == b"\xff\xd8fakejpeg\xff\xd9"


def test_photo_uploader_look_around_returns_single_url_list():
    post_json, _ = _fake_presign()
    up = d.GcsPhotoUploader(_FakeBackend(), "alpha", "conv1",
                            "http://127.0.0.1:8080", "tok",
                            http_post_json=post_json, http_put_bytes=lambda u, d: 200)
    urls = up.look_around()
    assert len(urls) == 1 and urls[0].startswith("http://127.0.0.1:8080/images/")


def test_photo_uploader_none_frame_returns_none():
    post_json, calls = _fake_presign()
    up = d.GcsPhotoUploader(_FakeBackend(frame=None), "alpha", "conv1",
                            "http://127.0.0.1:8080", "tok",
                            http_post_json=post_json, http_put_bytes=lambda u, d: 200)
    assert up.capture_photo() is None
    assert calls == []  # never even asked for a presigned URL


def test_photo_uploader_presign_http_failure_returns_none_not_raises():
    def post_json(url, headers, body):
        raise ConnectionError("refused")
    up = d.GcsPhotoUploader(_FakeBackend(), "alpha", "conv1",
                            "http://127.0.0.1:8080", "tok",
                            http_post_json=post_json, http_put_bytes=lambda u, d: 200)
    assert up.capture_photo() is None


def test_photo_uploader_presign_non_200_returns_none():
    def post_json(url, headers, body):
        return 403, {}
    up = d.GcsPhotoUploader(_FakeBackend(), "alpha", "conv1",
                            "http://127.0.0.1:8080", "tok",
                            http_post_json=post_json, http_put_bytes=lambda u, d: 200)
    assert up.capture_photo() is None


def test_photo_uploader_put_failure_returns_none_not_raises():
    post_json, _ = _fake_presign()
    up = d.GcsPhotoUploader(_FakeBackend(), "alpha", "conv1",
                            "http://127.0.0.1:8080", "tok",
                            http_post_json=post_json,
                            http_put_bytes=lambda u, d: (_ for _ in ()).throw(
                                ConnectionError("refused")))
    assert up.capture_photo() is None


def test_photo_uploader_put_non_200_returns_none():
    post_json, _ = _fake_presign()
    up = d.GcsPhotoUploader(_FakeBackend(), "alpha", "conv1",
                            "http://127.0.0.1:8080", "tok",
                            http_post_json=post_json, http_put_bytes=lambda u, d: 500)
    assert up.capture_photo() is None


def test_photo_uploader_filenames_are_unique_across_calls():
    filenames = []

    def post_json(url, headers, body):
        filenames.append(body["filename"])
        base = "http://127.0.0.1:8080"
        return 200, {"upload_url": f"{base}/images/x/{body['filename']}?token=t",
                     "image_url": f"{base}/images/x/{body['filename']}"}

    up = d.GcsPhotoUploader(_FakeBackend(), "alpha", "conv1",
                            "http://127.0.0.1:8080", "tok",
                            http_post_json=post_json, http_put_bytes=lambda u, d: 200)
    for _ in range(5):
        up.capture_photo()
    assert len(set(filenames)) == 5  # uuid suffix prevents collisions
