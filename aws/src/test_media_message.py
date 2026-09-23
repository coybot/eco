"""Recorded media has to survive the trip from the aircraft to the app.

Three regressions this pins:

1. A video-only result used to vanish. response_handler branched on
   image_urls, so "take a video of flying a circle" - which produces a clip
   and no stills - fell through to the text-only branch and the operator got
   "Done!" with nothing attached.

2. Failure still has to win. A mission that failed after recording must be
   reported as a failure with the media riding along, never described as a
   successful capture. This is the same ordering rule the image path already
   had, and it is easy to lose when adding a branch above it.

3. URLs must be presigned on the way out. The bucket is not public; an
   unsigned URL renders as a broken thumbnail rather than an error.

Run: cd eco/aws/src && python3 -m pytest test_media_message.py -v
"""
import json
from unittest.mock import patch

import conversations


class _Recorder:
    """Stands in for both DynamoDB and IoT so nothing leaves the process."""

    def __init__(self):
        self.saved = []
        self.published = []

    def save_message(self, conversation_id, drone_id, sender, content_type,
                     content, image_urls=None, media=None):
        self.saved.append({
            "content_type": content_type, "content": content,
            "image_urls": image_urls, "media": media,
        })
        return {"id": "m-1"}

    def publish(self, topic, qos, payload):
        self.published.append((topic, json.loads(payload)))


def _run(payload, rec, history=None):
    """Drive response_handler with everything external stubbed out."""
    with patch.object(conversations, "save_message", rec.save_message), \
         patch.object(conversations.iot, "publish", rec.publish), \
         patch.object(conversations, "get_conversation_history", lambda *a, **k: history or []), \
         patch.object(conversations, "call_agent", lambda *a, **k: {"message": "I see a truck."}), \
         patch.object(conversations, "generate_presigned_url", lambda u: u + "?signed=1"):
        conversations.response_handler(payload, None)


IMG = "https://b.s3.amazonaws.com/drones/d1/conversations/c1/20260923_1_a.jpg"
VID = "https://b.s3.amazonaws.com/drones/d1/conversations/c1/20260923_2_clip.mp4"


def _base(**extra):
    p = {"droneId": "d1", "conversation_id": "c1",
         "original_message": "fly a rectangle and record it",
         "result": {"success": True, "stdout": ""}}
    p.update(extra)
    return p


# --- media_items: the one place "a file or a folder" is decided ----------- #

def test_media_items_orders_photos_then_videos():
    assert conversations.media_items([IMG], [VID]) == [
        {"url": IMG, "kind": "photo"},
        {"url": VID, "kind": "video"},
    ]


def test_media_items_is_empty_when_nothing_was_captured():
    assert conversations.media_items([], []) == []
    assert conversations.media_items(None, None) == []


def test_kind_is_explicit_not_inferred_from_the_extension():
    """The app receives presigned URLs carrying a query string, so any
    client-side extension check would have to strip it first."""
    signed = VID + "?X-Amz-Signature=abc"
    assert conversations.media_items([], [signed])[0]["kind"] == "video"


# --- the regression: a clip with no stills ------------------------------- #

def test_video_only_result_reaches_the_app():
    rec = _Recorder()
    _run(_base(video_urls=[VID]), rec)
    assert rec.saved, "nothing was saved"
    assert rec.saved[0]["content_type"] == "media"
    assert rec.saved[0]["media"] == [{"url": VID, "kind": "video"}]
    _, published = rec.published[-1]
    assert published["message_type"] == "media"
    assert [m["kind"] for m in published["media"]] == ["video"]


def test_video_only_result_is_not_sent_to_the_vision_model():
    """An MP4 cannot be analysed as an image; asking would fail or hallucinate."""
    rec = _Recorder()
    calls = []
    with patch.object(conversations, "save_message", rec.save_message), \
         patch.object(conversations.iot, "publish", rec.publish), \
         patch.object(conversations, "get_conversation_history", lambda *a, **k: []), \
         patch.object(conversations, "call_agent", lambda *a, **k: calls.append(a) or {}), \
         patch.object(conversations, "generate_presigned_url", lambda u: u):
        conversations.response_handler(_base(video_urls=[VID]), None)
    assert calls == [], "the vision model must not be called for a video-only result"


def test_photos_and_videos_arrive_as_one_list():
    rec = _Recorder()
    _run(_base(image_urls=[IMG], video_urls=[VID]), rec)
    assert [m["kind"] for m in rec.saved[0]["media"]] == ["photo", "video"]


# --- failure still wins --------------------------------------------------- #

def test_failed_mission_with_media_reports_the_failure():
    rec = _Recorder()
    _run(_base(result={"success": False, "failure_reason": "waypoint 3 blocked"},
               video_urls=[VID]), rec)
    saved = rec.saved[0]
    assert "issue" in saved["content"].lower()
    assert "blocked" in saved["content"]
    assert saved["media"] == [{"url": VID, "kind": "video"}], "media should ride along"
    assert saved["content_type"] == "media"


def test_failed_mission_with_no_media_is_plain_text():
    rec = _Recorder()
    _run(_base(result={"success": False, "failure_reason": "no GPS"}), rec)
    assert rec.saved[0]["content_type"] == "text"
    assert rec.saved[0]["media"] is None


# --- presigning ------------------------------------------------------------ #

def test_published_media_urls_are_presigned():
    rec = _Recorder()
    _run(_base(image_urls=[IMG], video_urls=[VID]), rec)
    _, published = rec.published[-1]
    assert all(m["url"].endswith("?signed=1") for m in published["media"])


def test_saved_media_urls_are_not_presigned():
    """Stored unsigned on purpose: a presigned URL expires in 24h but the
    message lives 7 days, so history re-signs on read."""
    rec = _Recorder()
    _run(_base(image_urls=[IMG], video_urls=[VID]), rec)
    assert all("?signed=1" not in m["url"] for m in rec.saved[0]["media"])


# --- nothing captured behaves as before ----------------------------------- #

def test_no_media_still_takes_the_plain_text_path():
    rec = _Recorder()
    _run(_base(result={"success": True, "stdout": "Landed."}), rec)
    assert rec.saved[0]["content_type"] == "text"
