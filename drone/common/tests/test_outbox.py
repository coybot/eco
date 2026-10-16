"""Out of WiFi range nothing is lost, and nothing waits for the link.

Before the outbox, each photo was uploaded inline by the thread flying the
mission, once: out of range the upload failed, the photo was left in /tmp and
dropped from the survey, and a hung connection held the aircraft over a cell
for as long as the S3 client kept retrying. These pin the replacement: queue
to disk and return at once, deliver in order (photos before the reply that
lists them) when the link is back, survive a restart, and never publish one
reply twice while the first is still waiting for its acknowledgement.

Run: cd eco && python3 -m pytest drone/common/tests/test_outbox.py -v
"""

import json
import sys
import threading
import time
import types
from concurrent.futures import Future
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import outbox as ob  # noqa: E402


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(ob, "BACKOFF_START_S", 0.01)
    monkeypatch.setattr(ob, "BACKOFF_MAX_S", 0.02)


class Link:
    """A network that can be cut and restored. Records what got through."""

    def __init__(self, up=True):
        self.up = up
        self.sent = []          # ("file", key) / ("mqtt", topic, payload)
        self.publishes = 0

    def upload(self, path, key, content_type):
        if not self.up:
            raise OSError("Network is unreachable")
        self.sent.append(("file", key, Path(path).read_bytes()))

    def publish(self, topic, payload, qos):
        self.publishes += 1
        if not self.up:
            raise OSError("not connected")
        self.sent.append(("mqtt", topic, payload))
        f = Future()
        f.set_result(1)
        return f


def _drain(box, until, s=5.0):
    end = time.time() + s
    while time.time() < end and not until():
        box.drain_once()
        time.sleep(0.005)
    return until()


def _src(tmp_path, name, data=b"jpeg"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_items_go_out_oldest_first_so_the_reply_follows_its_photos(tmp_path):
    link = Link()
    box = ob.Outbox(tmp_path / "spool", uploader=link.upload, log=lambda m: None)
    box.attach_mqtt(link.publish, lambda: True)
    box.enqueue_file(_src(tmp_path, "a.jpg"), "k/a.jpg", "image/jpeg", "u/a")
    box.enqueue_file(_src(tmp_path, "b.jpg"), "k/b.jpg", "image/jpeg", "u/b")
    box.enqueue_message("drone/d/chat/c/response", '{"done": true}', 1)
    assert _drain(box, lambda: not box.pending())
    assert [s[1] for s in link.sent] == ["k/a.jpg", "k/b.jpg", "drone/d/chat/c/response"]


def test_queuing_returns_at_once_while_the_upload_is_stuck(tmp_path):
    """The aircraft keeps flying the survey while a connection hangs."""
    release = threading.Event()
    stuck = []

    def hung_upload(path, key, ct):
        stuck.append(key)
        release.wait(30)

    box = ob.Outbox(tmp_path / "spool", uploader=hung_upload, log=lambda m: None)
    box.start()
    t0 = time.monotonic()
    for i in range(20):
        assert box.enqueue_file(_src(tmp_path, f"{i}.jpg"), f"k/{i}.jpg", "image/jpeg", f"u/{i}")
    assert time.monotonic() - t0 < 1.0
    assert _drain(box, lambda: stuck == ["k/0.jpg"], 2) or stuck == ["k/0.jpg"]
    assert len(box.pending()) == 20
    release.set()
    box.stop()


def test_out_of_range_everything_waits_then_goes_when_the_link_is_back(tmp_path):
    link = Link(up=False)
    box = ob.Outbox(tmp_path / "spool", uploader=link.upload, log=lambda m: None)
    box.attach_mqtt(link.publish, lambda: link.up)
    for i in range(5):
        box.enqueue_file(_src(tmp_path, f"{i}.jpg", bytes([i]) * 10), f"k/{i}.jpg", "image/jpeg", "u")
    box.enqueue_message("t/response", "reply", 1)
    for _ in range(50):
        box.drain_once()
        time.sleep(0.001)
    assert link.sent == [] and len(box.pending()) == 6
    link.up = True
    box.link_changed()
    assert _drain(box, lambda: not box.pending())
    assert [s[1] for s in link.sent] == [f"k/{i}.jpg" for i in range(5)] + ["t/response"]
    assert link.sent[3][2] == bytes([3]) * 10, "the bytes queued are the bytes sent"


def test_the_caller_may_delete_its_file_straight_after_queuing(tmp_path):
    link = Link()
    box = ob.Outbox(tmp_path / "spool", uploader=link.upload, log=lambda m: None)
    src = _src(tmp_path, "x.jpg", b"pixels")
    box.enqueue_file(src, "k/x.jpg", "image/jpeg", "u")
    Path(src).unlink()
    assert _drain(box, lambda: not box.pending())
    assert link.sent == [("file", "k/x.jpg", b"pixels")]


def test_a_reboot_loses_nothing(tmp_path):
    first = ob.Outbox(tmp_path / "spool", uploader=None, log=lambda m: None)
    first.enqueue_file(_src(tmp_path, "a.jpg"), "k/a.jpg", "image/jpeg", "u")
    first.enqueue_message("t/response", "reply", 1)
    first.stop()
    del first

    link = Link()
    after = ob.Outbox(tmp_path / "spool", uploader=link.upload, log=lambda m: None)
    after.attach_mqtt(link.publish, lambda: True)
    assert len(after.pending()) == 2
    assert _drain(after, lambda: not after.pending())
    assert [s[1] for s in link.sent] == ["k/a.jpg", "t/response"]


def test_a_permanently_refused_item_is_set_aside_not_blocking_the_rest(tmp_path):
    sent = []

    def upload(path, key, ct):
        if key == "k/bad.jpg":
            raise ob.PermanentError("HTTP 403")
        sent.append(key)

    box = ob.Outbox(tmp_path / "spool", uploader=upload, log=lambda m: None)
    box.enqueue_file(_src(tmp_path, "bad.jpg"), "k/bad.jpg", "image/jpeg", "u")
    box.enqueue_file(_src(tmp_path, "ok.jpg"), "k/ok.jpg", "image/jpeg", "u")
    assert _drain(box, lambda: not box.pending())
    assert sent == ["k/ok.jpg"]
    kept = sorted(p.suffix for p in (tmp_path / "spool" / "failed").iterdir())
    assert kept == [".data", ".json", ".why"], "kept for a human, not deleted"


def test_a_transient_failure_is_retried_forever_not_set_aside(tmp_path):
    calls = []

    def flaky(path, key, ct):
        calls.append(key)
        if len(calls) < 12:
            raise TimeoutError("timed out")

    box = ob.Outbox(tmp_path / "spool", uploader=flaky, log=lambda m: None)
    box.enqueue_file(_src(tmp_path, "a.jpg"), "k/a.jpg", "image/jpeg", "u")
    assert _drain(box, lambda: not box.pending())
    assert len(calls) == 12 and not list((tmp_path / "spool" / "failed").iterdir())


def test_a_reply_waiting_for_its_ack_is_not_published_twice(tmp_path):
    """The MQTT client keeps an unacknowledged QoS 1 message and resends it on
    reconnect; publishing it again ourselves would put the reply in the chat twice."""
    pending = Future()
    calls = []

    def publish(topic, payload, qos):
        calls.append(payload)
        return pending

    box = ob.Outbox(tmp_path / "spool", uploader=None, log=lambda m: None)
    box.attach_mqtt(publish, lambda: True)
    box.enqueue_message("t/response", "reply", 1)
    box.drain_once()
    for _ in range(5):
        box.drain_once()
    assert calls == ["reply"] and box.pending()
    pending.set_result(7)           # the broker's PUBACK, after reconnecting
    assert _drain(box, lambda: not box.pending())
    assert calls == ["reply"]


def test_a_failed_publish_is_published_again(tmp_path):
    futures = [Future(), Future()]
    futures[0].set_exception(RuntimeError("connection lost"))
    futures[1].set_result(1)
    calls = []

    def publish(topic, payload, qos):
        calls.append(payload)
        return futures[len(calls) - 1]

    box = ob.Outbox(tmp_path / "spool", uploader=None, log=lambda m: None)
    box.attach_mqtt(publish, lambda: True)
    box.enqueue_message("t/response", "reply", 1)
    assert _drain(box, lambda: not box.pending())
    assert calls == ["reply", "reply"]


def test_replies_wait_on_disk_in_a_process_with_no_mqtt(tmp_path):
    """A script using the SDK drains its photos; a reply needs the daemon's link."""
    link = Link()
    box = ob.Outbox(tmp_path / "spool", uploader=link.upload, log=lambda m: None)
    box.enqueue_message("t/response", "reply", 1)
    for _ in range(10):
        box.drain_once()
    assert box.pending() and link.sent == []


@pytest.mark.skipif(ob.fcntl is None, reason="needs flock")
def test_only_one_process_drains_an_outbox(tmp_path):
    a_link, b_link = Link(), Link()
    a = ob.Outbox(tmp_path / "spool", uploader=a_link.upload, log=lambda m: None)
    b = ob.Outbox(tmp_path / "spool", uploader=b_link.upload, log=lambda m: None)
    a.enqueue_file(_src(tmp_path, "1.jpg"), "k/1.jpg", "image/jpeg", "u")
    assert a.drain_once()
    b.enqueue_file(_src(tmp_path, "2.jpg"), "k/2.jpg", "image/jpeg", "u")
    assert not b.drain_once(), "b must not drain while a holds the outbox"
    assert a.drain_once()
    assert [s[1] for s in a_link.sent] == ["k/1.jpg", "k/2.jpg"] and b_link.sent == []
    a.stop()
    assert _drain(b, lambda: True) and b.pending() == []


# --- drone_sdk: the survey keeps flying when the link is down or hung --------- #

class _Cam:
    def get_frame(self, timeout_ms=0):
        np = pytest.importorskip("numpy")
        return types.SimpleNamespace(rgb=np.full((48, 64, 3), 90, dtype="uint8"))


def _sdk_with_a_dead_link(monkeypatch, upload):
    pytest.importorskip("cv2")
    import drone_sdk
    monkeypatch.setattr(drone_sdk, "_get_camera", lambda: _Cam())
    monkeypatch.setattr(drone_sdk, "release_camera", lambda: None)
    monkeypatch.setattr(drone_sdk, "_photo_fix", lambda max_age=1.0: {
        "lat": -35.36, "lon": 149.16, "alt_msl_m": 589.0, "alt_above_home_m": 5.0,
        "heading_deg": 0.0, "ground_speed_mps": 0.0})
    monkeypatch.setattr(drone_sdk, "_load_config", lambda: {"images_bucket": "farm-bucket"})
    monkeypatch.setattr(drone_sdk, "CONVERSATION_ID", "conv-farm", raising=False)
    box = drone_sdk.outbox()
    box.set_uploader(upload)
    return drone_sdk, box


class _Flight:
    def __init__(self):
        self.pos = (0.0, 0.0)

    def install(self, monkeypatch, sdk):
        monkeypatch.setattr(sdk, "get_local_pose", lambda: (self.pos[0], self.pos[1], 5.0, 0.0))
        monkeypatch.setattr(sdk, "is_armed", lambda: True)
        monkeypatch.setattr(sdk, "goto_offset", self._goto)
        monkeypatch.setattr(sdk, "SURVEY_STILL_MPS", 1e9, raising=False)

    def _goto(self, north_m, east_m, alt_m, **kw):
        self.pos = (east_m, north_m)
        return True


def test_a_survey_out_of_range_finishes_and_delivers_everything_later(monkeypatch):
    link = Link(up=False)
    sdk, box = _sdk_with_a_dead_link(monkeypatch, link.upload)
    _Flight().install(monkeypatch, sdk)

    t0 = time.monotonic()
    urls = sdk.survey_rect(3, 2, spacing_m=1, altitude_m=5, settle_s=0)
    took = time.monotonic() - t0
    assert len(urls) == 6, "every cell's photo is kept, though none could be uploaded"
    assert all(u.startswith("https://farm-bucket.s3.amazonaws.com/drones/") and "/conversations/conv-farm/" in u
               for u in urls)
    assert took < 60
    names = box.pending()
    assert len(names) == 7, "6 photos + the manifest are waiting on the aircraft"

    link.up = True
    box.link_changed()
    assert box.wait_empty(10)
    keys = [s[1] for s in link.sent]
    assert keys[:6] == [u.split(".amazonaws.com/")[1] for u in urls], "uploaded to the URLs the survey reported"
    assert keys[6].rsplit("/", 1)[-1].split("_", 2)[-1].startswith("survey_manifest_")
    manifest = link.sent[6][2].decode()
    assert all(u.rsplit("/", 1)[-1] in manifest for u in urls)


def test_a_hung_upload_does_not_hold_the_aircraft_over_a_cell(monkeypatch):
    release = threading.Event()

    def hung(path, key, ct):
        release.wait(60)

    sdk, box = _sdk_with_a_dead_link(monkeypatch, hung)
    _Flight().install(monkeypatch, sdk)
    t0 = time.monotonic()
    urls = sdk.survey_rect(2, 2, spacing_m=1, altitude_m=5, settle_s=0)
    assert len(urls) == 4 and time.monotonic() - t0 < 30
    release.set()


# --- the daemon's reply rides behind the photos ------------------------------- #

from test_daemon_one_mission import daemon  # noqa: E402,F401  (the module-scoped fixture)


def test_the_mission_reply_is_queued_behind_its_photos(monkeypatch, daemon):
    pytest.importorskip("cv2")
    d = daemon
    link = Link(up=False)
    sdk, box = _sdk_with_a_dead_link(monkeypatch, link.upload)
    box.attach_mqtt(link.publish, lambda: link.up)
    url = sdk.capture_photo()
    d.send_chat_reply("conv-farm", {"result": {"success": True}, "image_urls": [url]})
    assert len(box.pending()) == 2 and link.sent == []
    link.up = True
    box.link_changed()
    assert box.wait_empty(10)
    assert [s[0] for s in link.sent] == ["file", "mqtt"]
    topic, reply = link.sent[1][1], json.loads(link.sent[1][2])
    assert topic.endswith("/chat/conv-farm/response") and reply["image_urls"] == [url]
    assert reply["message_id"], "replies carry an id the cloud can de-duplicate on"
