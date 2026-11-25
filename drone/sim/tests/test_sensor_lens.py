"""The sim camera's lens maths (sensor_lens.py, sensor_fx.gd)."""
import random

import sensor_lens as L

LENS = {"k1": -0.06, "k2": 0.008, "aspect": 4 / 3, "jx": 0.0012, "jy": -0.0008, "roll": 0.0017}


def test_no_lens_is_the_identity():
    assert L.to_image(0.3, 0.7, None) == (0.3, 0.7)
    assert L.to_pinhole(0.3, 0.7, {}) == (0.3, 0.7)


def test_to_pinhole_inverts_to_image_across_the_frame():
    rng = random.Random(1)
    for _ in range(2000):
        x, y = rng.random(), rng.random()
        bx, by = L.to_pinhole(*L.to_image(x, y, LENS), LENS)
        assert abs(bx - x) < 1e-9 and abs(by - y) < 1e-9


def test_the_frame_stays_full_and_barrel_pulls_edges_in():
    # Normalised so the pinhole corner lands on the frame's corner (up to jitter)...
    cx, cy = L.to_image(0.0, 0.0, dict(LENS, jx=0.0, jy=0.0, roll=0.0))
    assert abs(cx) < 1e-12 and abs(cy) < 1e-12
    # ...and a point halfway out moves outward (the centre is magnified slightly).
    mx, _ = L.to_image(0.25, 0.5, dict(LENS, jx=0.0, jy=0.0, roll=0.0))
    assert mx < 0.25


def test_pixels():
    u, v = L.pixel_to_pinhole(*[c * 800 for c in L.to_image(0.6, 0.4, LENS)], 800, 800, LENS)
    assert abs(u - 480) < 1e-6 and abs(v - 320) < 1e-6
