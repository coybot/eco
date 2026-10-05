"""Guards Landmark.image_url's set-once-on-first-sighting contract.

A landmark's photo is captured only when it's first created (hits == 1) — see
reasoning_loop.MissionLoop._maybe_photo_landmark. Everything downstream (the
EMA position merge in update(), and pin()'s replace-the-bucket semantics) must
carry that URL forward untouched, or a landmark's map pin silently loses its
photo on the very next sighting.

Run: python3 -m pytest drone/common/tests/test_spatial_memory_image_url.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # drone/common

from spatial_memory import SpatialMemory


def test_image_url_defaults_to_none():
    mem = SpatialMemory()
    lm = mem.update("car", 10.0, 5.0, 0.0, 0.9)
    assert lm.image_url is None


def test_image_url_survives_update_ema_merge():
    mem = SpatialMemory()
    lm = mem.update("car", 10.0, 5.0, 0.0, 0.9)
    lm.image_url = "http://gcs:8080/images/car.jpg"
    # A second sighting close enough to merge (within the 1.5m default radius).
    merged = mem.update("car", 10.3, 5.1, 0.0, 0.95)
    assert merged is lm  # same object, mutated in place
    assert merged.hits == 2
    assert merged.image_url == "http://gcs:8080/images/car.jpg"


def test_image_url_not_set_for_a_second_distinct_landmark():
    mem = SpatialMemory()
    lm1 = mem.update("car", 0.0, 0.0, 0.0, 0.9)
    lm1.image_url = "http://gcs:8080/images/car1.jpg"
    # Far outside merge_radius -> a genuinely new Landmark, no photo yet.
    lm2 = mem.update("car", 100.0, 100.0, 0.0, 0.9)
    assert lm2 is not lm1
    assert lm2.image_url is None


def test_pin_carries_image_url_from_prior_like_hits():
    mem = SpatialMemory()
    first = mem.pin("teammate", 1.0, 2.0, 3.0, 0.8)
    first.image_url = "http://gcs:8080/images/teammate.jpg"
    second = mem.pin("teammate", 4.0, 5.0, 6.0, 0.85)
    assert second is not first
    assert second.hits == first.hits + 1
    assert second.image_url == "http://gcs:8080/images/teammate.jpg"


def test_pin_with_no_prior_leaves_image_url_none():
    mem = SpatialMemory()
    lm = mem.pin("teammate", 1.0, 2.0, 3.0)
    assert lm.image_url is None
