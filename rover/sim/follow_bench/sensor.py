"""Seeded noise models turning the sim's line-of-sight samples into what a phone would give a
follow controller: person boxes, body-pose joints, LiDAR depth, a re-ID embedding.

Everything here is a MODEL, not a measurement of YOLO/Vision/LiDAR. The geometry that drives
it (what is occluded, what is in frame) is real: it comes from Godot raycasts. Constants are
chosen to be plausible and are listed so a reader can disagree with them:

  detector   10 Hz; confidence = 0.92 * (0.30 + 0.70 * weighted visible-and-in-frame body
             fraction) + N(0, 0.03); emitted when >= 0.25 (and with ramping probability below
             a 0.25 fraction); `drop` (default 1 %) random frame drop. Boxes cover the VISIBLE part only.
  joints     a shoulder/hip joint exists only if its sample is visible and in frame;
             confidence N(0.75, 0.12); 6 mm pixel noise.
  depth      sample range converted to camera z-depth, + N(0, 0.02 + 0.01 * d) m. Where the
             anchor pixel is occluded the sensor returns the OCCLUDER's range (real behaviour).
  re-ID      per-actor unit vector (pairwise cosine `reid_cos`) + noise that grows as the
             body fraction shrinks, renormalised.

Ground truth (`gt`, `gt_xy`) is attached for scoring only; controllers must not read it.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np

from .camera import Camera, Pose

JOINTS = ["ankle", "knee", "hip", "chest", "shoulder", "head"]
WEIGHT = {"ankle": 0.75, "knee": 0.75, "hip": 1.0, "chest": 1.0, "shoulder": 1.5, "head": 1.5}
CAPTURE_HZ = 10.0


@dataclass
class Det:
    box: tuple[float, float, float, float]          # u0, v0, u1, v1 — clipped to the frame
    conf: float
    wfrac: float                                    # weighted visible-and-in-frame body fraction
    joints: dict[str, tuple[float, float, float]]   # name -> (u, v, conf)
    torso: tuple[float, float, float] | None        # (u, v, z_depth) at the chest, as read
    masked_xy: tuple[float, float] | None           # median world xy of the person's own pixels
    n_visible: int
    emb: np.ndarray
    gt: str = ""
    gt_xy: tuple[float, float] = (0.0, 0.0)


@dataclass
class Frame:
    t: float
    pose: Pose
    cam: Camera
    dets: list[Det] = field(default_factory=list)
    clearance: float = 999.0


class SensorModel:
    def __init__(self, cam: Camera, seed: int, reid_cos: float = 0.3, dim: int = 16, drop: float = 0.01):
        self.cam = cam
        self.drop = drop
        self.rng = random.Random(seed)
        self.nrng = np.random.RandomState(seed + 1)
        base = np.random.RandomState(4242)
        v0 = base.randn(dim)
        v0 /= np.linalg.norm(v0)
        self._bases = [v0]
        for _ in range(3):
            o = base.randn(dim)
            o -= o.dot(v0) * v0
            o /= np.linalg.norm(o)
            self._bases.append(reid_cos * v0 + math.sqrt(1 - reid_cos ** 2) * o)
        self._index: dict[str, int] = {}

    def _base(self, name: str) -> np.ndarray:
        if name not in self._index:
            self._index[name] = len(self._index)
        return self._bases[self._index[name] % len(self._bases)]

    def frame(self, view: dict) -> Frame:
        pose: Pose = tuple(view["pose"])  # type: ignore[assignment]
        cam = self.cam
        fr = Frame(t=view["t"], pose=pose, cam=cam, clearance=view.get("clearance", 999.0))
        for person in view["persons"]:
            det = self._detect(person, pose)
            if det is not None:
                fr.dets.append(det)
        return fr

    def _noisy_z(self, euclid: float, a: float, b: float) -> float:
        z = euclid / math.sqrt(a * a + b * b + 1.0)
        return z + self.rng.gauss(0.0, 0.02 + 0.01 * z)

    def _detect(self, person: dict, pose: Pose) -> Det | None:
        cam = self.cam
        rows = person["samples"]                      # 18 rows, joint-major, 3 lateral each
        grid = {(r[5], i % 3): r for i, r in enumerate(rows)}
        proj = {}
        vis_in = {}
        for key, r in grid.items():
            p = cam.project(pose, r[0], r[1], r[2])
            proj[key] = p
            vis_in[key] = bool(r[3]) and p is not None and cam.in_frame(p[0], p[1])
        total = 3 * sum(WEIGHT.values())
        wfrac = sum(WEIGHT[j] for (j, k), ok in vis_in.items() if ok) / total
        if wfrac < 0.10 or self.rng.random() < self.drop:
            return None
        if self.rng.random() > min(1.0, (wfrac - 0.10) / 0.15):
            return None
        conf = min(0.97, 0.92 * (0.30 + 0.70 * wfrac) + self.rng.gauss(0.0, 0.03))
        if conf < 0.25:
            return None

        # Box over the visible samples (+ feet / top of head when their column is visible).
        pts = [(proj[key][0], proj[key][1]) for key, ok in vis_in.items() if ok]
        centre = grid[("ankle", 1)]
        if bool(grid[("ankle", 1)][3]):
            f = cam.project(pose, centre[0], centre[1], 0.0)
            if f:
                pts.append((f[0], f[1]))
        if bool(grid[("head", 1)][3]):
            h = grid[("head", 1)]
            t = cam.project(pose, h[0], h[1], 1.72)
            if t:
                pts.append((t[0], t[1]))
        if not pts:
            return None
        us, vs = [p[0] for p in pts], [p[1] for p in pts]
        zd = max(0.3, min(p[2] for key, p in proj.items() if p is not None))
        pad = 0.05 / (zd * 2 * cam.th)
        u0, u1 = min(us) - pad, max(us) + pad
        v0, v1 = min(vs), max(vs)
        jw = 0.015 * (u1 - u0)
        jh = 0.015 * (v1 - v0)
        u0, u1 = u0 + self.rng.gauss(0, jw), u1 + self.rng.gauss(0, jw)
        v0, v1 = v0 + self.rng.gauss(0, jh), v1 + self.rng.gauss(0, jh)
        if u1 <= 0 or u0 >= 1 or v1 <= 0 or v0 >= 1:
            return None
        box = (max(0.0, u0), max(0.0, v0), min(1.0, u1), min(1.0, v1))

        joints: dict[str, tuple[float, float, float]] = {}
        for name, key in (("leftShoulder", ("shoulder", 2)), ("rightShoulder", ("shoulder", 0)),
                          ("leftHip", ("hip", 2)), ("rightHip", ("hip", 0))):
            if vis_in[key]:
                jc = min(1.0, max(0.0, self.rng.gauss(0.75, 0.12)))
                if jc >= 0.10:
                    p = proj[key]
                    joints[name] = (p[0] + self.rng.gauss(0, 0.006), p[1] + self.rng.gauss(0, 0.006), jc)

        torso = None
        ck = ("chest", 1)
        cp = proj[ck]
        if cp is not None and cam.in_frame(cp[0], cp[1]):
            a, b = (cp[0] - 0.5) * 2 * cam.th, (cp[1] - 0.5) * 2 * cam.tv
            torso = (cp[0], cp[1], self._noisy_z(grid[ck][4], a, b))

        world = []
        for key, ok in vis_in.items():
            if ok:
                p = proj[key]
                a, b = (p[0] - 0.5) * 2 * cam.th, (p[1] - 0.5) * 2 * cam.tv
                world.append(cam.to_world(pose, p[0], p[1], self._noisy_z(grid[key][4], a, b)))
        masked = None
        if len(world) >= 3:
            masked = (float(np.median([w[0] for w in world])), float(np.median([w[1] for w in world])))

        sigma = 0.12 + 0.50 * (1.0 - wfrac)
        emb = self._base(person["name"]) + self.nrng.randn(len(self._bases[0])) * sigma / math.sqrt(len(self._bases[0])) * 2.0
        emb = emb / np.linalg.norm(emb)
        return Det(box=box, conf=conf, wfrac=wfrac, joints=joints, torso=torso, masked_xy=masked,
                   n_visible=len(world), emb=emb, gt=person["name"], gt_xy=tuple(person["pos"]))
