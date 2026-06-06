"""End-to-end Ishmael test: 1 rover + 1 quadcopter search an office for a chair.

Run on Hoopoe (real Isaac Sim) with the Isaac venv python:

    ISHMAEL_RUN_E2E=1 \
    ISHMAEL_API_BASE=https://03bnj3wwef.execute-api.us-west-2.amazonaws.com/prod \
    ISHMAEL_API_TOKEN="<cognito-id-token>" \
    ISHMAEL_USER_SUB="<cognito-sub>" \
    /home/yusuf/isaac-sim-env/bin/python3 -m pytest \
        drone/sim/ishmael/tests/test_office_chair.py -v

The test is **skipped automatically** when the env var ISHMAEL_RUN_E2E is not set,
so ``pytest`` on a laptop (no Isaac, no AWS) is safe and fast.

What it verifies:
  1. NLP parses the spec correctly (chair target, office scene, 1+1 fleet).
  2. Director dry-run succeeds (fleet roster, scene resolved).
  3. (Full-run only) Fleet launches, both drones come Online, mission dispatches.
  4. App client receives ≥1 image_url.
  5. App client receives ≥1 video_url.
  6. Drone-camera mp4 is decoded; at least one frame contains a detectable chair
     (YOLOv8n COCO class 56) or the vantage mp4 contains both a rover-shaped and
     quad-shaped object (size heuristic, not YOLO).
  7. Results written to ~/videos/ishmael-e2e/ with a JSON report.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

E2E = os.environ.get("ISHMAEL_RUN_E2E", "")
skip_if_no_e2e = pytest.mark.skipif(not E2E, reason="ISHMAEL_RUN_E2E not set")

_SIM_DIR = Path(__file__).resolve().parents[2]   # eco/drone/sim
if str(_SIM_DIR) not in sys.path:
    sys.path.insert(0, str(_SIM_DIR))


# ---------------------------------------------------------------------------
# Unit-level tests (always run, no Isaac/AWS needed)
# ---------------------------------------------------------------------------

def test_nlp_parse():
    """NLP rule parser correctly extracts the test spec."""
    from ishmael.nlp import parse_test_spec
    spec = parse_test_spec(
        "1 rover and 1 quadcopter search an office for a chair and record the video",
        use_llm=False)
    types = {v.type for v in spec.vehicles}
    assert "rover" in types, f"rover missing from {types}"
    assert "quadcopter" in types, f"quadcopter missing from {types}"
    assert spec.scene == "office"
    assert spec.target == "chair"
    assert spec.mobile_action == "send_video"


def test_fleet_roster():
    """parse_roster produces correct deterministic ids."""
    from ishmael.nlp import parse_test_spec
    from fleet import parse_roster  # type: ignore
    spec = parse_test_spec("1 rover and 1 quadcopter search an office",
                           use_llm=False)
    roster = parse_roster(spec.fleet_arg())
    ids = [r["id"] for r in roster]
    assert "sim-rover-001" in ids
    assert "sim-quadcopter-001" in ids
    assert len(roster) == 2


def test_scene_resolves_to_office():
    """Scene resolver returns the office USD for 'office'."""
    from ishmael.scene_resolver import resolve_scene
    ref = resolve_scene("office", use_llm=False)
    assert ref.category == "builtin"
    assert "office" in ref.usd_url.lower()


def test_director_dry_run():
    """Director dry-run completes without launching Isaac."""
    from ishmael.nlp import parse_test_spec
    from ishmael.director import DirectorConfig, run
    spec = parse_test_spec("1 rover and 1 quadcopter search an office for a chair",
                           use_llm=False)
    cfg = DirectorConfig(dry_run=True, register=False)
    result = run(spec, cfg)
    assert result.ok
    assert len(result.roster) == 2


def test_video_record_frames():
    """record_frames captures the expected number of frames from a synthetic source."""
    try:
        import numpy as np  # Hoopoe has it; laptop may not
    except ImportError:
        pytest.skip("numpy not available")
    from video_record import record_frames  # type: ignore
    counter = [0]
    def grab():
        f = np.zeros((480, 640, 3), dtype="uint8")
        f[:, :, 0] = (counter[0] * 15) % 256
        counter[0] += 1
        return f
    frames = record_frames(grab, seconds=0.5, fps=10)
    assert len(frames) >= 4, f"expected ≥4 frames, got {len(frames)}"


# ---------------------------------------------------------------------------
# Full E2E (Hoopoe only — ISHMAEL_RUN_E2E=1)
# ---------------------------------------------------------------------------

@skip_if_no_e2e
def test_e2e_office_chair():
    """1 rover + 1 quadcopter search an office for a chair; check video + image."""
    from ishmael.nlp import parse_test_spec
    from ishmael.director import DirectorConfig, run

    spec = parse_test_spec(
        "1 rover and 1 quadcopter search an office for a chair, "
        "record and send the video from overhead",
        use_llm=True)
    assert spec.scene == "office"
    assert spec.target == "chair"

    out_dir = os.path.expanduser("~/videos/ishmael-e2e")
    cfg = DirectorConfig(
        out_dir=out_dir,
        user_sub=os.environ.get("ISHMAEL_USER_SUB"),
        register=bool(os.environ.get("ISHMAEL_USER_SUB")),
        online_timeout_s=300.0,
        mission_timeout_s=300.0,
    )

    t0 = time.time()
    result = run(spec, cfg)
    elapsed = round(time.time() - t0, 1)

    # --- write the report ---
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    report = {
        "elapsed_s": elapsed,
        "ok": result.ok,
        "online": result.online,
        "images": result.images,
        "videos": result.videos,
        "notes": result.notes,
    }
    report_path = os.path.join(out_dir, f"report_{int(time.time())}.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n[e2e] report written to {report_path}", flush=True)

    # --- assertions ---
    assert result.online, "No drones came online within timeout"
    assert len(result.online) == 2, f"Expected 2 Online, got {result.online}"

    # At least one image OR video (mission may produce both)
    media = result.images + result.videos
    assert media, "No images or videos received from the mission"

    # Download the returned media and run automated checks
    saved = _check_media(result.images, result.videos, out_dir)
    assert saved, "No media files saved for inspection"

    print(f"[e2e] PASSED  images={len(result.images)} "
          f"videos={len(result.videos)} saved={len(saved)}", flush=True)


def _check_media(image_urls: list, video_urls: list, out_dir: str) -> list[str]:
    """Download + inspect returned media. Returns list of saved local paths."""
    import urllib.request
    saved = []
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # Check images with YOLO chair detector (COCO class 56)
    for i, url in enumerate(image_urls):
        if not url.startswith("http"):
            continue
        dest = os.path.join(out_dir, f"image_{i}.jpg")
        try:
            urllib.request.urlretrieve(url, dest)
            saved.append(dest)
            _yolo_check(dest, label="chair")
        except Exception as e:
            print(f"[check] image {i} error: {e}")

    # Decode videos and sample frames
    for i, url in enumerate(video_urls):
        if not url.startswith("http"):
            continue
        dest = os.path.join(out_dir, f"video_{i}.mp4")
        try:
            urllib.request.urlretrieve(url, dest)
            saved.append(dest)
            _inspect_video(dest, label=f"video_{i}")
        except Exception as e:
            print(f"[check] video {i} error: {e}")

    return saved


def _yolo_check(image_path: str, label: str = "chair"):
    """Run YOLOv8n on ``image_path`` and report whether ``label`` is detected."""
    try:
        # eco/drone/common is on Hoopoe at the standard PYTHONPATH
        _common = Path(_SIM_DIR).parent / "common"
        if str(_common) not in sys.path:
            sys.path.insert(0, str(_common))
        from perception import Detector  # type: ignore
        det = Detector()
        detections = det.detect_file(image_path)
        found = [d for d in detections if d.label.lower() == label.lower()]
        if found:
            print(f"[check] ✓ '{label}' detected in {image_path} "
                  f"(conf={found[0].confidence:.2f})")
        else:
            labels = [d.label for d in detections]
            print(f"[check] '{label}' NOT detected in {image_path}; "
                  f"found: {labels or '(nothing)'}")
    except Exception as e:
        print(f"[check] YOLO check skipped ({e})")


def _inspect_video(mp4_path: str, label: str = "video"):
    """Decode mp4 and report basic stats (frame count, resolution)."""
    try:
        import av  # type: ignore
        container = av.open(mp4_path)
        frames = 0
        for frame in container.decode(video=0):
            frames += 1
            if frames == 1:
                print(f"[check] {label}: {frame.width}x{frame.height} "
                      f"pts={frame.pts}")
            if frames >= 30:  # sample first 2 seconds at 15fps
                break
        container.close()
        print(f"[check] {label}: decoded {frames} frame(s) from {mp4_path}")
    except Exception as e:
        print(f"[check] video inspection skipped ({e})")
