"""Shared mission frame for a GPS-denied team.

Without GPS every vehicle navigates on its own VIO/odometry in its own
takeoff-origin ENU frame (onboard_l5 "World" frame, team_link ``pos_enu``).
Those frames differ from each other by an unknown rigid offset and yaw, and
each one drifts against the ground over time (localization.py models it as a
random-walk bias at ``gps_denied_drift_mps``). A task "at (x, y)" therefore
means a different physical place to every vehicle unless the team agrees on a
common frame. This module estimates, for every vehicle, the transform from its
local frame into one **mission frame**, says how sure it is, and says when that
is no longer good enough to tell two nearby tasks apart.

Model and assumptions
  * 2.5D: each local->mission transform is SE(2) (yaw ``theta`` about +z, then
    translation ``tx, ty``) plus an independent vertical offset ``tz``. Every
    local frame is assumed gravity-aligned (VIO/flight controllers give that);
    roll/pitch misalignment and scale error are not modelled.
  * Yaw is radians, counter-clockwise from local +x (ENU east). Body frame for
    relative measurements is x=forward, y=left, z=up, as in onboard_l5. A
    compass heading (clockwise from north) must be converted by the caller.
  * The mission frame is the briefing frame if any vehicle has a known launch
    pose (LaunchLayout) or a surveyed landmark exists; otherwise it is the
    anchor vehicle's (default: self) local frame at its takeoff.
  * Correspondence noise is Gaussian, isotropic in the horizontal plane, and
    independent between correspondences. Range/bearing measurements are folded
    into one isotropic sigma by taking the worse axis, which is conservative.
  * Drift: a vehicle's transform is constant between correspondences except for
    a translation (and optional yaw) drift with per-axis 1-sigma
    ``DriftModel.sigma_m(dt)``. ``mode="linear"`` (default) grows as rate*dt: the
    plain reading of "m/s" and the right worst case for real VIO, whose error is
    mostly a slowly-varying bias. ``mode="random_walk"`` reproduces
    localization.py exactly (rate*sqrt(step_s*dt), step_s = sim dt), which is
    far smaller: at 0.3 m/s and 60 s it is 0.73 m against 18 m linear. Pick the
    one that matches what you are flying against.

Estimation
  * ``FrameAlignment.estimate``: weighted least squares between two frames from
    point and optional heading correspondences. Points alone are the classic
    Kabsch/Umeyama problem without scale (closed form); headings add a term the
    closed form cannot carry, so the rotation is refined by a 1-D Newton solve
    started from both the Kabsch and the circular-mean answers. RANSAC over
    minimal sets (one pose, or two points), least-median scored so it survives
    understated sigmas, rejects outliers first (up to just under half); covariance is
    the Gauss-Newton inverse information, scaled up (never down) by the
    residual chi^2/dof so it widens when the stated sigmas are too optimistic.
  * ``TeamFrameEstimator``: one joint weighted least-squares problem over every
    vehicle transform and every unsurveyed landmark pose, so evidence is counted
    once (a peer is never "confirmed" by a correspondence derived from its own
    transform). Robust init (RANSAC per node, breadth-first from the gauge),
    Huber-weighted Gauss-Newton, then chi^2 gating of the worst residual until
    none fails. Each factor's variance is inflated by the drift each involved
    vehicle accumulated between that factor and its latest one, so the solve
    estimates every transform as of its own last correspondence; the reported
    covariance then adds the drift since then. Old evidence fades; uncertainty
    grows with time since last fix and shrinks again on the next one.

What it cannot do
  * No loop closure, map building or re-localization against the vehicle's own
    past: a single vehicle's drift is only bounded by other evidence (launch
    pose, surveyed tags, peers). A team that has all drifted together stays
    mutually consistent but drifts against the ground, and only the absolute
    ("mission") uncertainty reports that.
  * No roll/pitch, no scale, no full 6-DoF tags (only tag position and yaw).
  * Correlation between factors that share one vehicle's drift is ignored; with
    many old correspondences of one vehicle the fit is mildly overconfident
    about the past, never about the drift since the newest one.
  * An outlier can only be rejected where there is redundancy: a vehicle tied
    to the team by one wrong correspondence gets a wrong transform with an
    honest-looking covariance.
  * Clocks: stamps are this vehicle's ``time.monotonic()``. Messages carry the
    observation's age, not a remote stamp, so link latency (tens of ms) is the
    only timing error; it is absorbed as noise.

How the outputs are meant to be used
  * The allocator keeps tasks in the mission frame. Before dispatching a task to
    a peer it maps peer reports with ``to_mission(peer_id, pos_local)``, and this
    vehicle flies to ``from_mission(task)``.
  * ``task_ambiguity(task, spacing_m)`` answers "could two tasks this close be
    confused right now?". When it says yes the allocator should merge or hold
    the pair rather than send two vehicles to what may be the same place, and
    try to get a fresh correspondence (rendezvous, look at a shared tag).
  * ``uncertainty(peer_id)`` is for display/telemetry and for gating
    behaviours (e.g. refuse a precise landing task above some sigma).
  * Observations that peers need travel over TeamLink as small dicts
    (``encode_message`` / ``decode_message`` / ``TeamFrameEstimator.ingest``);
    team_link.py itself is unchanged. Send the dicts with ``TeamLink.send()``
    and feed ``drain_inbox()`` back through ``ingest``; non-frame messages are
    ignored.

Pure stdlib + numpy, Python 3.8.
"""
from __future__ import annotations

import itertools
import json
import logging
import math
import time
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# chi^2 99.9% quantiles by degrees of freedom, used for inlier gating.
_CHI2_999 = {1: 10.828, 2: 13.816, 3: 16.266, 4: 18.467}
# Variance standing in for "no information at all" (1e6 m sigma) on a dimension
# nothing constrains, e.g. tz when no correspondence carried a height. Finite so
# covariance algebra (0 * inf) stays defined.
UNOBSERVED_VAR = 1e12
# Weak priors that keep the joint information matrix invertible when a node is
# only partly constrained (rotation from a single point, landmark yaw never seen).
# Marginals on such dimensions come back near these values: that is the signal.
_WEAK_PRIOR_SIGMA = np.array([1e3, 1e3, 1e2, 1e3])
_ROTATION_OBSERVABLE_VAR = 1.0      # rad^2; a theta marginal above this = unknown
MAX_PAYLOAD_BYTES = 300
MAX_ID_LEN = 32
MSG_VERSION = 1


# --------------------------------------------------------------------------- helpers
def wrap_angle(a: float) -> float:
    """Wrap to [-pi, pi)."""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def _wrap_arr(a: np.ndarray) -> np.ndarray:
    return (a + np.pi) % (2.0 * np.pi) - np.pi


def _rot(theta: float) -> np.ndarray:
    c, s = math.cos(theta), math.sin(theta)
    return np.array([[c, -s], [s, c]])


def _perp(v: np.ndarray) -> np.ndarray:
    """d(R(theta) p)/d theta = perp(R p): rotate by +90 degrees."""
    return np.array([-v[1], v[0]])


def _pose4(p: Optional[Sequence[Optional[float]]]) -> np.ndarray:
    """(x, y[, z[, yaw]]) -> float array of 4 with NaN for absent/None entries."""
    out = np.full(4, np.nan)
    if p is None:
        return out
    vals = list(p)
    if len(vals) < 2 or len(vals) > 4:
        raise ValueError(f"expected (x, y[, z[, yaw]]), got {p!r}")
    for k, v in enumerate(vals):
        out[k] = np.nan if v is None else float(v)
    if not (np.isfinite(out[0]) and np.isfinite(out[1])):
        raise ValueError(f"non-finite position {p!r}")
    return out


def _finite(x: float) -> bool:
    return x is not None and math.isfinite(x)


# --------------------------------------------------------------------------- drift
@dataclass
class DriftModel:
    """How fast a vehicle's local frame slides against the ground.

    rate_mps has the meaning of vehicle_class.LocalizationProfile.gps_denied_drift_mps.
    ``sigma_m(dt)`` is the per-axis 1-sigma accumulated over dt seconds:
      linear:       rate * dt                    (bias-like VIO drift; conservative)
      random_walk:  rate * sqrt(step_s * dt)     (localization.py with sim dt = step_s)
    planar vehicles (rovers) do not drift in z.
    """
    rate_mps: float = 0.10
    mode: str = "linear"
    step_s: float = 0.1
    yaw_rate_rad_s: float = 0.0
    planar: bool = False

    def __post_init__(self):
        if self.mode not in ("linear", "random_walk"):
            raise ValueError(f"DriftModel.mode must be 'linear' or 'random_walk', not {self.mode!r}")
        if self.rate_mps < 0 or self.yaw_rate_rad_s < 0 or self.step_s <= 0:
            raise ValueError("drift rates must be >= 0 and step_s > 0")

    def _scale(self, dt: float) -> float:
        dt = max(0.0, dt)
        return dt if self.mode == "linear" else math.sqrt(self.step_s * dt)

    def sigma_m(self, dt: float) -> float:
        return self.rate_mps * self._scale(dt)

    def var_m2(self, dt: float) -> float:
        return self.sigma_m(dt) ** 2

    def var_yaw(self, dt: float) -> float:
        return (self.yaw_rate_rad_s * self._scale(dt)) ** 2

    def var_z(self, dt: float) -> float:
        return 0.0 if self.planar else self.var_m2(dt)

    @classmethod
    def for_class(cls, class_name: str, mode: str = "linear", step_s: float = 0.1) -> "DriftModel":
        """Drift model from a vehicle_class entry (gps_denied_drift_mps, planar)."""
        try:
            from .vehicle_class import get_class      # package import (tests, sim)
        except ImportError:                            # pragma: no cover - flat install
            from vehicle_class import get_class
        vc = get_class(class_name)
        return cls(rate_mps=vc.localization.gps_denied_drift_mps, mode=mode,
                   step_s=step_s, planar=bool(vc.planar))


# --------------------------------------------------------------------------- correspondences
@dataclass
class Correspondence:
    """One point (optionally with heading and height) seen in two frames.

    ``src`` is in the frame being aligned (a vehicle's local frame), ``dst`` in
    the target frame (the mission frame). ``src_yaw``/``dst_yaw`` (rad) make it a
    pose correspondence: one is enough to fix the whole SE(2). Height is used
    when both src and dst have a z. sigma_m is the 1-sigma per horizontal axis of
    the *difference* (both ends' noise combined).
    """
    src: Tuple[float, ...]
    dst: Tuple[float, ...]
    sigma_m: float = 0.5
    src_yaw: Optional[float] = None
    dst_yaw: Optional[float] = None
    sigma_yaw_rad: float = 0.1
    sigma_z_m: Optional[float] = None
    stamp: float = 0.0
    kind: str = ""

    def __post_init__(self):
        if not (_finite(self.sigma_m) and self.sigma_m > 0):
            raise ValueError("Correspondence.sigma_m must be finite and > 0")
        if not (_finite(self.sigma_yaw_rad) and self.sigma_yaw_rad > 0):
            raise ValueError("Correspondence.sigma_yaw_rad must be finite and > 0")

    @property
    def has_yaw(self) -> bool:
        return _finite(self.src_yaw) and _finite(self.dst_yaw)

    @property
    def has_z(self) -> bool:
        return (len(self.src) > 2 and _finite(self.src[2])
                and len(self.dst) > 2 and _finite(self.dst[2]))


class _Arrays:
    """Correspondences unpacked into numpy columns once, for repeated fits."""

    def __init__(self, corrs: Sequence[Correspondence]):
        n = len(corrs)
        self.n = n
        self.A = np.array([[c.src[0], c.src[1]] for c in corrs], dtype=float).reshape(n, 2)
        self.B = np.array([[c.dst[0], c.dst[1]] for c in corrs], dtype=float).reshape(n, 2)
        if not (np.all(np.isfinite(self.A)) and np.all(np.isfinite(self.B))):
            raise ValueError("non-finite correspondence position")
        self.w = np.array([1.0 / c.sigma_m ** 2 for c in corrs])
        self.has_yaw = np.array([c.has_yaw for c in corrs], dtype=bool)
        self.d = np.array([wrap_angle(c.dst_yaw - c.src_yaw) if c.has_yaw else 0.0 for c in corrs])
        self.wh = np.where(self.has_yaw, [1.0 / c.sigma_yaw_rad ** 2 for c in corrs], 0.0) if n else np.zeros(0)
        self.has_z = np.array([c.has_z for c in corrs], dtype=bool)
        self.dz = np.array([c.dst[2] - c.src[2] if c.has_z else 0.0 for c in corrs])
        self.wz = np.where(self.has_z, [1.0 / (c.sigma_z_m or c.sigma_m) ** 2 for c in corrs],
                           0.0) if n else np.zeros(0)

    def normalized_sq(self, theta: float, t: np.ndarray, tz: float) -> Tuple[np.ndarray, np.ndarray]:
        """Per-correspondence Mahalanobis residual^2 and its degrees of freedom."""
        r = self.B - (self.A @ _rot(theta).T + t)
        e = self.w * np.sum(r * r, axis=1)
        e = e + self.wh * _wrap_arr(self.d - theta) ** 2
        e = e + self.wz * (self.dz - tz) ** 2
        dims = 2 + self.has_yaw.astype(int) + self.has_z.astype(int)
        return e, dims


def _fit(arr: _Arrays, m: np.ndarray) -> Tuple[float, np.ndarray, float]:
    """Weighted SE(2)+z fit on the correspondences selected by mask m.

    Minimises sum w|b - R a - t|^2 + sum wh wrap(d - theta)^2 (+ z separately).
    For fixed theta the optimal t is the weighted-centroid one, which reduces the
    problem to one angle: -2(cos th Sd + sin th Sc) + sum wh wrap(d - th)^2.
    """
    wm = arr.w * m
    W = wm.sum()
    if W <= 0:
        raise ValueError("no correspondences selected")
    abar = (wm[:, None] * arr.A).sum(0) / W
    bbar = (wm[:, None] * arr.B).sum(0) / W
    Ac, Bc = arr.A - abar, arr.B - bbar
    sd = float(np.sum(wm * (Ac[:, 0] * Bc[:, 0] + Ac[:, 1] * Bc[:, 1])))
    sc = float(np.sum(wm * (Ac[:, 0] * Bc[:, 1] - Ac[:, 1] * Bc[:, 0])))
    spread = float(np.sum(wm * np.sum(Ac * Ac, axis=1)))
    whm = arr.wh * m
    Wh = float(whm.sum())
    if spread + Wh <= 1e-9:
        raise ValueError("rotation unobservable: all source points coincide and no heading")

    def cost(th: float) -> float:
        return -2.0 * (math.cos(th) * sd + math.sin(th) * sc) + float(
            np.sum(whm * _wrap_arr(arr.d - th) ** 2))

    def newton(th: float) -> float:
        for _ in range(30):
            e = _wrap_arr(arr.d - th)
            g = 2.0 * (math.sin(th) * sd - math.cos(th) * sc) - 2.0 * float(np.sum(whm * e))
            h = 2.0 * (math.cos(th) * sd + math.sin(th) * sc) + 2.0 * Wh
            if h <= 1e-15:
                break
            step = -g / h
            th = wrap_angle(th + step)
            if abs(step) < 1e-13:
                break
        return th

    starts = []
    if math.hypot(sd, sc) > 0:
        starts.append(math.atan2(sc, sd))                  # Kabsch
    if Wh > 0:
        starts.append(math.atan2(float(np.sum(whm * np.sin(arr.d))),
                                 float(np.sum(whm * np.cos(arr.d)))))   # heading mean
    theta = min((newton(s) for s in starts), key=cost)
    t = bbar - _rot(theta) @ abar
    wzm = arr.wz * m
    Wz = wzm.sum()
    tz = float(np.sum(wzm * arr.dz) / Wz) if Wz > 0 else 0.0
    return theta, t, tz


def _fit_covariance(arr: _Arrays, m: np.ndarray, theta: float, t: np.ndarray,
                    tz: float) -> Tuple[np.ndarray, float]:
    """Inverse Gauss-Newton information over (tx, ty, theta, tz), residual-scaled."""
    wm, whm, wzm = arr.w * m, arr.wh * m, arr.wz * m
    RA = arr.A @ _rot(theta).T
    info = np.zeros((3, 3))
    info[0, 0] = info[1, 1] = wm.sum()
    info[0, 2] = info[2, 0] = float(np.sum(wm * -RA[:, 1]))
    info[1, 2] = info[2, 1] = float(np.sum(wm * RA[:, 0]))
    info[2, 2] = float(np.sum(wm * np.sum(RA * RA, axis=1)) + whm.sum())
    r = arr.B - (RA + t)
    chi2 = float(np.sum(wm * np.sum(r * r, axis=1)) + np.sum(whm * _wrap_arr(arr.d - theta) ** 2))
    dof = 2 * int(m.sum()) + int((arr.has_yaw & m).sum()) - 3
    scale = max(1.0, chi2 / dof) if dof > 0 else 1.0
    cov = np.zeros((4, 4))
    cov[:3, :3] = np.linalg.inv(info) * scale
    Wz = wzm.sum()
    nz = int((arr.has_z & m).sum())
    if Wz > 0:
        chi2z = float(np.sum(wzm * (arr.dz - tz) ** 2))
        zscale = max(1.0, chi2z / (nz - 1)) if nz > 1 else 1.0
        cov[3, 3] = zscale / Wz
    else:
        cov[3, 3] = UNOBSERVED_VAR
    rms = float(math.sqrt(np.sum(m * np.sum(r * r, axis=1)) / max(1, int(m.sum()))))
    return cov, rms


# --------------------------------------------------------------------------- FrameAlignment
@dataclass
class FrameAlignment:
    """Rigid 2.5D transform src -> dst:  p_dst = R(theta) p_src[:2] + (tx, ty),  z + tz.

    ``cov`` is the 4x4 covariance of (tx, ty, theta, tz). theta uncertainty is
    about the *src origin*, so a point far from it inherits a lever-arm error:
    use ``point_cov`` rather than reading tx/ty variances off directly.
    """
    theta: float = 0.0
    tx: float = 0.0
    ty: float = 0.0
    tz: float = 0.0
    cov: np.ndarray = field(default_factory=lambda: np.zeros((4, 4)))
    src: str = ""
    dst: str = ""
    inliers: Tuple[bool, ...] = ()
    rms_m: float = 0.0

    @classmethod
    def identity(cls, src: str = "", dst: str = "") -> "FrameAlignment":
        return cls(src=src, dst=dst)

    @property
    def R(self) -> np.ndarray:
        return _rot(self.theta)

    @property
    def t(self) -> np.ndarray:
        return np.array([self.tx, self.ty])

    @property
    def params(self) -> np.ndarray:
        return np.array([self.tx, self.ty, self.theta, self.tz])

    @property
    def n_inliers(self) -> int:
        return int(sum(self.inliers))

    @property
    def outlier_indices(self) -> List[int]:
        return [k for k, ok in enumerate(self.inliers) if not ok]

    # -- mapping --------------------------------------------------------------
    def apply(self, p) -> np.ndarray:
        """Map a point (2,) / (3,) or an (N,2) / (N,3) array from src to dst."""
        a = np.asarray(p, dtype=float)
        single = a.ndim == 1
        a2 = a.reshape(1, -1) if single else a
        out = a2.copy()
        out[:, :2] = a2[:, :2] @ self.R.T + self.t
        if a2.shape[1] > 2:
            out[:, 2] = a2[:, 2] + self.tz
        return out[0] if single else out

    def apply_yaw(self, yaw: float) -> float:
        return wrap_angle(yaw + self.theta)

    def point_jacobian(self, p_src) -> np.ndarray:
        """3x4 d(mapped x, y, z)/d(tx, ty, theta, tz) at a src point."""
        rp = self.R @ np.asarray(p_src, dtype=float)[:2]
        return np.array([[1.0, 0.0, -rp[1], 0.0],
                         [0.0, 1.0, rp[0], 0.0],
                         [0.0, 0.0, 0.0, 1.0]])

    def point_cov(self, p_src) -> np.ndarray:
        """3x3 covariance of apply(p_src) due to transform uncertainty alone."""
        J = self.point_jacobian(p_src)
        return J @ self.cov @ J.T

    # -- algebra --------------------------------------------------------------
    def inverse(self) -> "FrameAlignment":
        Rt = self.R.T
        t_inv = -Rt @ self.t
        J = np.zeros((4, 4))
        J[:2, :2] = -Rt
        J[:2, 2] = _perp(Rt @ self.t)
        J[2, 2] = -1.0
        J[3, 3] = -1.0
        return FrameAlignment(theta=wrap_angle(-self.theta), tx=float(t_inv[0]), ty=float(t_inv[1]),
                              tz=-self.tz, cov=J @ self.cov @ J.T, src=self.dst, dst=self.src)

    def compose(self, other: "FrameAlignment") -> "FrameAlignment":
        """self o other: maps other.src -> self.dst (apply other first).

        Covariances are propagated to first order assuming the two are independent.
        """
        if self.src and other.dst and self.src != other.dst:
            raise ValueError(f"cannot compose {self.src}->{self.dst} after {other.src}->{other.dst}")
        R1 = self.R
        t = R1 @ other.t + self.t
        J1 = np.eye(4)
        J1[:2, 2] = _perp(R1 @ other.t)
        J2 = np.eye(4)
        J2[:2, :2] = R1
        cov = J1 @ self.cov @ J1.T + J2 @ other.cov @ J2.T
        return FrameAlignment(theta=wrap_angle(self.theta + other.theta), tx=float(t[0]), ty=float(t[1]),
                              tz=self.tz + other.tz, cov=cov, src=other.src, dst=self.dst)

    # -- estimation -----------------------------------------------------------
    @classmethod
    def estimate(cls, correspondences: Sequence[Correspondence], robust: bool = True,
                 src: str = "", dst: str = "", seed: int = 0,
                 max_hypotheses: int = 400) -> "FrameAlignment":
        """Weighted least-squares SE(2)+z from correspondences (no scale).

        robust: RANSAC over minimal sets — each pose correspondence, each pair of
        separated points — scored by least median of squares, then refit on the
        chi^2 99.9% inliers (gate widened by the robust residual scale when the
        stated sigmas are too small) until the inlier set is stable. Enumerates every minimal set when there are at most
        ``max_hypotheses``; otherwise samples that many with ``seed``.
        Raises ValueError if the rotation is unobservable.
        """
        corrs = list(correspondences)
        if not corrs:
            raise ValueError("no correspondences")
        arr = _Arrays(corrs)
        n = arr.n
        mask = np.ones(n, dtype=bool)
        if robust and (n >= 3 or (n >= 2 and arr.has_yaw.any())):
            mask = cls._ransac(arr, seed, max_hypotheses)
        theta, t, tz = _fit(arr, mask)
        for _ in range(5):
            if not robust:
                break
            e, dims = arr.normalized_sq(theta, t, tz)
            thr = np.array([_CHI2_999[int(k)] for k in dims]) * _robust_scale(e, dims)
            new = e <= thr
            if np.array_equal(new, mask) or not _determined(arr, new):
                break
            mask = new
            theta, t, tz = _fit(arr, mask)
        cov, rms = _fit_covariance(arr, mask, theta, t, tz)
        return cls(theta=theta, tx=float(t[0]), ty=float(t[1]), tz=tz, cov=cov, src=src, dst=dst,
                   inliers=tuple(bool(x) for x in mask), rms_m=rms)

    @staticmethod
    def _ransac(arr: _Arrays, seed: int, max_hypotheses: int) -> np.ndarray:
        n = arr.n
        sig = 1.0 / np.sqrt(arr.w)
        samples: List[Tuple[int, ...]] = [(k,) for k in range(n) if arr.has_yaw[k]]
        for i, j in itertools.combinations(range(n), 2):
            # A pair closer than its own noise pins the rotation no better than chance.
            if np.linalg.norm(arr.A[i] - arr.A[j]) > 2.0 * (sig[i] + sig[j]):
                samples.append((i, j))
        if not samples:
            return np.ones(n, dtype=bool)
        if len(samples) > max_hypotheses:
            rng = np.random.default_rng(seed)
            pick = rng.choice(len(samples), size=max_hypotheses, replace=False)
            samples = [samples[k] for k in sorted(pick)]
        # Least-median-of-squares choice (scale-free: still works when the stated
        # sigmas are too small, where a fixed chi^2 gate would call everything an
        # outlier), MSAC score as tie-break; gate at the robust residual scale.
        best_score, best_mask = (math.inf, math.inf), np.ones(n, dtype=bool)
        for s in samples:
            m = np.zeros(n, dtype=bool)
            m[list(s)] = True
            try:
                theta, t, tz = _fit(arr, m)
            except ValueError:
                continue
            e, dims = arr.normalized_sq(theta, t, tz)
            thr = np.array([_CHI2_999[int(k)] for k in dims]) * _robust_scale(e, dims)
            score = (float(np.median(e / dims)), float(np.sum(np.minimum(e, thr))))
            if score < best_score:
                inl = e <= thr
                best_score, best_mask = score, (inl if _determined(arr, inl) else m)
        return best_mask


def _robust_scale(e: np.ndarray, dims: np.ndarray) -> float:
    """Variance factor (>= 1) from the median per-dimension residual. For
    correctly stated sigmas e/dims has median ~0.69 (2 dof) to ~0.79 (3 dof);
    valid while fewer than half the correspondences are outliers."""
    return max(1.0, float(np.median(e / dims)) / 0.72)


def _determined(arr: _Arrays, m: np.ndarray) -> bool:
    """Does mask m pin the SE(2)? (a heading, or two distinct source points)"""
    if not m.any():
        return False
    if (arr.has_yaw & m).any():
        return True
    pts = arr.A[m]
    return bool(np.max(np.linalg.norm(pts - pts[0], axis=1)) > 1e-6)


# --------------------------------------------------------------------------- sources
@dataclass
class LaunchReport:
    """A vehicle's own pose at takeoff in its local frame (normally the origin,
    with whatever local yaw it believed) and when it took off."""
    vehicle_id: str
    local_pose: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    stamp: float = 0.0


@dataclass
class LaunchLayout:
    """Source (a): launch poses from the mission briefing, in the mission frame.

    poses: vehicle_id -> (x, y, z, yaw_rad). A layout with every vehicle at one
    pose and one heading is pre-flight co-location ("all hand-launched from the
    pad facing the road"); spread it with sigma_m for the placement error.
    """
    poses: Dict[str, Tuple[float, float, float, float]] = field(default_factory=dict)
    sigma_m: float = 0.5
    sigma_yaw_rad: float = math.radians(5.0)
    sigma_z_m: float = 0.2

    @classmethod
    def colocated(cls, vehicle_ids: Iterable[str], pose=(0.0, 0.0, 0.0, 0.0),
                  sigma_m: float = 1.0, sigma_yaw_rad: float = math.radians(10.0)) -> "LaunchLayout":
        p = tuple(float(v) for v in _pose4(pose))
        return cls(poses={v: p for v in vehicle_ids}, sigma_m=sigma_m, sigma_yaw_rad=sigma_yaw_rad)

    @classmethod
    def from_briefing(cls, briefing: dict) -> "LaunchLayout":
        """{"sigma_m": 0.5, "sigma_yaw_deg": 5, "vehicles": {id: {"x", "y", "z"?,
        "yaw_deg" | "yaw_rad"}}}. Missing yaw means "heading not briefed": that
        vehicle's launch is then a position-only correspondence."""
        poses = {}
        for vid, v in (briefing.get("vehicles") or {}).items():
            if "yaw_rad" in v:
                yaw = float(v["yaw_rad"])
            elif "yaw_deg" in v:
                yaw = math.radians(float(v["yaw_deg"]))
            else:
                yaw = float("nan")
            poses[str(vid)] = (float(v["x"]), float(v["y"]), float(v.get("z", 0.0)), yaw)
        sy = briefing.get("sigma_yaw_deg")
        return cls(poses=poses, sigma_m=float(briefing.get("sigma_m", 0.5)),
                   sigma_yaw_rad=math.radians(float(sy)) if sy is not None else math.radians(5.0),
                   sigma_z_m=float(briefing.get("sigma_z_m", 0.2)))

    def correspondence(self, report: LaunchReport) -> Optional[Correspondence]:
        mp = self.poses.get(report.vehicle_id)
        if mp is None:
            return None
        lp = _pose4(report.local_pose)
        return Correspondence(src=(lp[0], lp[1], lp[2]), dst=(mp[0], mp[1], mp[2]),
                              sigma_m=self.sigma_m, src_yaw=lp[3], dst_yaw=mp[3],
                              sigma_yaw_rad=self.sigma_yaw_rad, sigma_z_m=self.sigma_z_m,
                              stamp=report.stamp, kind="launch")


@dataclass
class FiducialSighting:
    """Source (b): a fiducial (AprilTag-like, id = landmark_id) seen by one vehicle.

    pose_local is the tag's (x, y, z, yaw) in the observer's local frame; z/yaw
    may be None (yaw unknown for a round marker or a tag seen edge-on). Two
    vehicles sighting the same id, at any times, tie their frames together.
    """
    observer_id: str
    landmark_id: str
    pose_local: Tuple[Optional[float], ...]
    sigma_m: float = 0.3
    sigma_yaw_rad: float = math.radians(5.0)
    sigma_z_m: Optional[float] = None
    stamp: float = 0.0

    @classmethod
    def from_relative(cls, observer_id: str, landmark_id: str,
                      observer_pose_local: Sequence[float], rel_body: Sequence[float],
                      rel_yaw_rad: Optional[float] = None, stamp: float = 0.0,
                      sigma_m: float = 0.3, sigma_yaw_rad: float = math.radians(5.0),
                      sigma_z_m: Optional[float] = None) -> "FiducialSighting":
        """Tag pose from the observer's local pose (x, y, z, yaw) and the detector's
        body-frame offset (forward, left[, up]) and relative tag yaw."""
        op = _pose4(observer_pose_local)
        if not np.isfinite(op[3]):
            raise ValueError("observer yaw is required to place a relative sighting")
        rb = list(rel_body) + [0.0] * (3 - len(rel_body))
        xy = op[:2] + _rot(op[3]) @ np.array([rb[0], rb[1]], dtype=float)
        z = op[2] + rb[2] if np.isfinite(op[2]) else None
        yaw = wrap_angle(op[3] + rel_yaw_rad) if rel_yaw_rad is not None else None
        return cls(observer_id, landmark_id, (float(xy[0]), float(xy[1]), z, yaw),
                   sigma_m=sigma_m, sigma_yaw_rad=sigma_yaw_rad, sigma_z_m=sigma_z_m, stamp=stamp)

    def correspondence(self, landmark_pose_mission: Sequence[Optional[float]],
                       landmark_sigma_m: float = 0.0,
                       landmark_sigma_yaw_rad: float = 0.0) -> Correspondence:
        """Correspondence for the observer given the landmark's mission pose."""
        lp = _pose4(self.pose_local)
        mp = _pose4(landmark_pose_mission)
        return Correspondence(src=(lp[0], lp[1], lp[2]), dst=(mp[0], mp[1], mp[2]),
                              sigma_m=math.hypot(self.sigma_m, landmark_sigma_m),
                              src_yaw=lp[3], dst_yaw=mp[3],
                              sigma_yaw_rad=math.hypot(self.sigma_yaw_rad, landmark_sigma_yaw_rad),
                              sigma_z_m=self.sigma_z_m, stamp=self.stamp, kind="fiducial")


@dataclass
class RelativeObservation:
    """Source (c): observer measured where target was, while target reported its
    own local position over the team link.

    target_in_observer: target's (x, y[, z]) in the observer's local frame.
    target_reported:    target's own (x, y[, z]) in its local frame at that time.
    sigma_m: 1-sigma per horizontal axis of the two combined, including the
    reported position's latency (speed * time skew).
    """
    observer_id: str
    target_id: str
    target_in_observer: Tuple[Optional[float], ...]
    target_reported: Tuple[Optional[float], ...]
    sigma_m: float = 0.5
    sigma_z_m: Optional[float] = None
    stamp: float = 0.0

    @classmethod
    def from_range_bearing(cls, observer_id: str, target_id: str,
                           observer_pose_local: Sequence[float], range_m: float,
                           bearing_rad: float, target_reported: Sequence[Optional[float]],
                           elevation_rad: Optional[float] = None, stamp: float = 0.0,
                           sigma_range_m: float = 0.2, sigma_bearing_rad: float = math.radians(1.0),
                           sigma_target_m: float = 0.05, target_speed_mps: float = 0.0,
                           time_skew_s: float = 0.0) -> "RelativeObservation":
        """From a range (m) / bearing (rad, CCW from the observer's nose) / optional
        elevation (rad, up positive) measurement, e.g. UWB + camera.

        Horizontal sigma is the worse of radial and tangential (conservative
        isotropic stand-in), combined with the target report's own sigma and its
        latency error target_speed_mps * |time_skew_s|.
        """
        op = _pose4(observer_pose_local)
        if not np.isfinite(op[3]):
            raise ValueError("observer yaw is required for a bearing measurement")
        el = elevation_rad if elevation_rad is not None else 0.0
        rh = range_m * math.cos(el)
        ang = op[3] + bearing_rad
        q = (op[0] + rh * math.cos(ang), op[1] + rh * math.sin(ang),
             op[2] + range_m * math.sin(el) if (elevation_rad is not None and np.isfinite(op[2])) else None)
        s_meas = max(sigma_range_m * abs(math.cos(el)), rh * sigma_bearing_rad)
        s_lat = target_speed_mps * abs(time_skew_s)
        sigma = math.sqrt(s_meas ** 2 + sigma_target_m ** 2 + s_lat ** 2)
        sz = None
        if elevation_rad is not None:
            sz = math.sqrt(max(sigma_range_m * abs(math.sin(el)), range_m * sigma_bearing_rad) ** 2
                           + sigma_target_m ** 2 + s_lat ** 2)
        tr = tuple(target_reported)
        return cls(observer_id, target_id, q, tr, sigma_m=sigma, sigma_z_m=sz, stamp=stamp)

    def correspondence(self, observer_to_mission: FrameAlignment) -> Correspondence:
        """Correspondence for the target given the observer's alignment (whose
        uncertainty at the observed point is folded into sigma)."""
        q = _pose4(self.target_in_observer)
        p = _pose4(self.target_reported)
        dst = observer_to_mission.apply(q[:3] if np.isfinite(q[2]) else q[:2])
        pc = observer_to_mission.point_cov(q[:2])
        s_obs = math.sqrt(max(0.0, float(np.max(np.linalg.eigvalsh(pc[:2, :2])))))
        return Correspondence(src=tuple(p[:3]), dst=tuple(dst) + ((None,) if len(dst) == 2 else ()),
                              sigma_m=math.hypot(self.sigma_m, s_obs), sigma_z_m=self.sigma_z_m,
                              stamp=self.stamp, kind="relative")


# --------------------------------------------------------------------------- joint estimator
def _vnode(vid: str) -> str:
    return "v:" + vid


def _lnode(lid: str) -> str:
    return "L:" + lid


@dataclass
class _Factor:
    """T_i(a) == T_j(b); j None means b is already in the mission frame."""
    kind: str
    i: str
    a: np.ndarray
    j: Optional[str]
    b: np.ndarray
    sigma_m: float
    sigma_yaw: float
    sigma_z: float
    stamp: float
    label: str = ""
    outlier: bool = False

    @property
    def has_yaw(self) -> bool:
        return bool(np.isfinite(self.a[3]) and np.isfinite(self.b[3]))

    @property
    def has_z(self) -> bool:
        return bool(np.isfinite(self.a[2]) and np.isfinite(self.b[2]))

    def nodes(self) -> List[str]:
        return [self.i] if self.j is None else [self.i, self.j]

    def vehicles(self) -> List[str]:
        return [n[2:] for n in self.nodes() if n.startswith("v:")]


@dataclass
class PeerUncertainty:
    """Mission-frame position uncertainty of one vehicle's reports right now."""
    vehicle_id: str
    resolved: bool
    sigma_m: float                  # 1-sigma along the worst horizontal axis
    sigma_z_m: float
    radius95_m: float               # 95% horizontal circle (chi^2_2)
    age_s: float                    # since this vehicle's last accepted correspondence
    rotation_observable: bool
    cov_xy: np.ndarray = field(default_factory=lambda: np.full((2, 2), np.inf))


@dataclass
class TaskAmbiguity:
    """Answer to "could two tasks spacing_m apart be confused here, now?".

    ambiguous iff some considered vehicle is unresolved, or z * sigma_m >=
    spacing_m / 2, i.e. the error along the worst axis reaches the midpoint
    between the two tasks at the requested confidence.
    """
    ambiguous: bool
    sigma_m: float
    radius_m: float
    spacing_m: float
    reference: str
    worst: Tuple[str, ...] = ()
    unresolved: Tuple[str, ...] = ()


class UnresolvedFrame(LookupError):
    """No chain of correspondences links this vehicle to the mission frame yet."""


class TeamFrameEstimator:
    """Fuses launch layout, fiducial sightings and relative observations into each
    vehicle's local->mission transform, online.

    Feed it with add_launch / add_surveyed_landmark / add_fiducial / add_relative
    (or ingest() for team-link dicts); it re-solves lazily on the next query.
    Times are this vehicle's ``clock()`` (default time.monotonic).
    """

    def __init__(self, self_id: str, layout: Optional[LaunchLayout] = None,
                 drift: Optional[DriftModel] = None,
                 drift_by_vehicle: Optional[Dict[str, DriftModel]] = None,
                 anchor_id: Optional[str] = None, anchor_stamp: Optional[float] = None,
                 clock: Callable[[], float] = time.monotonic,
                 huber_k: float = 3.0, max_factors: int = 600):
        self.self_id = self_id
        self.layout = layout or LaunchLayout()
        self.drift = drift or DriftModel()
        self.drift_by_vehicle = dict(drift_by_vehicle or {})
        self.anchor_id = anchor_id or self_id
        self.clock = clock
        self.anchor_stamp = clock() if anchor_stamp is None else float(anchor_stamp)
        self.huber_k = huber_k
        self.max_factors = max_factors
        self._factors: List[_Factor] = []
        self._vehicles = {self_id}
        self._last_local: Dict[str, np.ndarray] = {}
        self._dirty = True
        self._state: Dict[str, np.ndarray] = {}
        self._index: Dict[str, int] = {}
        self._cov = np.zeros((0, 0))
        self._t_last: Dict[str, float] = {}
        self._gauge_sig: Tuple[str, ...] = ()
        self._init: Dict[str, np.ndarray] = {}

    # -- inputs ---------------------------------------------------------------
    def drift_for(self, vehicle_id: str) -> DriftModel:
        return self.drift_by_vehicle.get(vehicle_id, self.drift)

    def set_drift(self, vehicle_id: str, model: DriftModel) -> None:
        """Per-vehicle drift (e.g. DriftModel.for_class of that peer's class)."""
        self.drift_by_vehicle[vehicle_id] = model
        self._dirty = True                  # factor weights depend on it

    def add_launch(self, report: LaunchReport) -> bool:
        """Returns False if the briefing has no launch pose for that vehicle."""
        c = self.layout.correspondence(report)
        if c is None:
            return False
        a = _pose4(report.local_pose)
        b = _pose4(self.layout.poses[report.vehicle_id])
        # Replace, not stack: a vehicle launches once per frame; a resend is a duplicate.
        self._factors = [f for f in self._factors
                         if not (f.kind == "launch" and f.i == _vnode(report.vehicle_id))]
        self._add(_Factor("launch", _vnode(report.vehicle_id), a, None, b, c.sigma_m,
                          c.sigma_yaw_rad, c.sigma_z_m or c.sigma_m, report.stamp,
                          label=f"launch {report.vehicle_id}"), report.vehicle_id, a)
        return True

    def add_surveyed_landmark(self, landmark_id: str, pose_mission: Sequence[Optional[float]],
                              sigma_m: float = 0.1, sigma_yaw_rad: float = math.radians(2.0),
                              sigma_z_m: Optional[float] = None) -> None:
        """A tag whose mission pose is in the briefing: anchors every observer."""
        b = _pose4(pose_mission)
        a = np.array([0.0, 0.0, 0.0 if np.isfinite(b[2]) else np.nan, 0.0 if np.isfinite(b[3]) else np.nan])
        self._factors = [f for f in self._factors
                         if not (f.kind == "survey" and f.i == _lnode(landmark_id))]
        self._add(_Factor("survey", _lnode(landmark_id), a, None, b, sigma_m, sigma_yaw_rad,
                          sigma_z_m or sigma_m, -math.inf, label=f"survey {landmark_id}"), None, None)

    def add_fiducial(self, s: FiducialSighting) -> None:
        a = _pose4(s.pose_local)
        b = np.array([0.0, 0.0, 0.0 if np.isfinite(a[2]) else np.nan, 0.0 if np.isfinite(a[3]) else np.nan])
        self._add(_Factor("fiducial", _vnode(s.observer_id), a, _lnode(s.landmark_id), b,
                          s.sigma_m, s.sigma_yaw_rad, s.sigma_z_m or s.sigma_m, s.stamp,
                          label=f"tag {s.landmark_id} by {s.observer_id} @{s.stamp:.1f}"),
                  s.observer_id, a)

    def add_relative(self, o: RelativeObservation) -> None:
        a = _pose4(o.target_reported)
        b = _pose4(o.target_in_observer)
        a[3] = b[3] = np.nan
        self._vehicles.add(o.observer_id)
        self._add(_Factor("relative", _vnode(o.target_id), a, _vnode(o.observer_id), b,
                          o.sigma_m, 1.0, o.sigma_z_m or o.sigma_m, o.stamp,
                          label=f"{o.observer_id} saw {o.target_id} @{o.stamp:.1f}"), o.target_id, a)

    def ingest(self, msg: dict, recv_time: Optional[float] = None) -> bool:
        """Feed one team-link dict. True if it was a frame message and was used."""
        try:
            obj = decode_message(msg, self.clock() if recv_time is None else recv_time)
        except ValueError as e:
            logger.debug(f"shared_frame: dropped malformed {msg.get('type')}: {e}")
            return False
        if obj is None:
            return False
        if isinstance(obj, LaunchReport):
            return self.add_launch(obj)
        if isinstance(obj, FiducialSighting):
            self.add_fiducial(obj)
        else:
            self.add_relative(obj)
        return True

    def _add(self, f: _Factor, vid: Optional[str], local: Optional[np.ndarray]) -> None:
        for v in f.vehicles():
            self._vehicles.add(v)
        if vid is not None and local is not None:
            self._last_local[vid] = np.nan_to_num(local[:3])
        self._factors.append(f)
        prunable = [k for k, g in enumerate(self._factors) if g.kind in ("fiducial", "relative")]
        if len(prunable) > self.max_factors:
            oldest = min(prunable, key=lambda k: self._factors[k].stamp)
            del self._factors[oldest]
        self._dirty = True

    # -- solve ----------------------------------------------------------------
    def update(self) -> None:
        """Re-solve now (queries do this lazily when inputs changed)."""
        self._solve()
        self._dirty = False

    def _ensure(self) -> None:
        if self._dirty:
            self.update()

    def _gauge(self) -> List[_Factor]:
        if any(f.kind in ("launch", "survey") for f in self._factors):
            return []
        # No briefing anchor at all: the mission frame is the anchor's takeoff frame.
        return [_Factor("gauge", _vnode(self.anchor_id), np.zeros(4), None, np.zeros(4),
                        1e-3, 1e-4, 1e-3, self.anchor_stamp, label=f"anchor {self.anchor_id}")]

    def _solve(self) -> None:
        factors = self._factors + self._gauge()
        for f in factors:
            f.outlier = False
        # A different gauge is a different mission frame: warm starts from the old
        # one would be arbitrarily far off, so re-initialise everything.
        sig = tuple(sorted({f.kind for f in factors if f.kind in ("launch", "survey", "gauge")}))
        if sig != self._gauge_sig:
            self._state = {}
            self._gauge_sig = sig
        for _ in range(12):
            nodes = self._initialise(factors)
            active = [f for f in factors if not f.outlier and all(n in nodes for n in f.nodes())]
            self._t_last = self._last_times(active)
            x, H, scale, worst = self._gauss_newton(nodes, active)
            if worst is None:
                break
            worst.outlier = True
            logger.debug(f"shared_frame: rejected {worst.label}")
        self._state = {n: x[k * 4:(k + 1) * 4].copy() for n, k in self._index.items()}
        try:
            self._cov = np.linalg.inv(H) * scale
        except np.linalg.LinAlgError:
            self._cov = np.linalg.pinv(H) * scale

    def _last_times(self, active: List[_Factor]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for f in active:
            for v in f.vehicles():
                out[v] = max(out.get(v, -math.inf), f.stamp)
        return out

    def _node_pose_in_mission(self, init: Dict[str, np.ndarray], node: Optional[str],
                              p: np.ndarray) -> np.ndarray:
        if node is None:
            return p.copy()
        x = init[node]
        out = np.full(4, np.nan)
        out[:2] = _rot(x[2]) @ p[:2] + x[:2]
        out[2] = p[2] + x[3]
        out[3] = wrap_angle(p[3] + x[2]) if np.isfinite(p[3]) else np.nan
        return out

    def _initialise(self, factors: List[_Factor]) -> Dict[str, int]:
        """Breadth-first robust init from the gauge; warm-start previously solved nodes.

        A node is initialised only through factors to already-initialised nodes,
        so anything not connected to the gauge stays out of the solve (unresolved).
        """
        usable = [f for f in factors if not f.outlier]
        init: Dict[str, np.ndarray] = {}
        pending = {n for f in usable for n in f.nodes()}
        progress = True
        while progress and pending:
            progress = False
            for node in sorted(pending):
                corrs = []
                for f in usable:
                    if f.i == node and (f.j is None or f.j in init):
                        mine, other_node, other = f.a, f.j, f.b
                    elif f.j == node and f.i in init:
                        mine, other_node, other = f.b, f.i, f.a
                    else:
                        continue
                    dst = self._node_pose_in_mission(init, other_node, other)
                    corrs.append(Correspondence(src=tuple(mine[:3]), dst=tuple(dst[:3]),
                                                sigma_m=f.sigma_m, src_yaw=mine[3], dst_yaw=dst[3],
                                                sigma_yaw_rad=f.sigma_yaw, sigma_z_m=f.sigma_z))
                if not corrs:
                    continue
                if node in self._state and node in self._index:
                    init[node] = self._state[node].copy()     # warm start
                else:
                    init[node] = _init_from(corrs)
                pending.discard(node)
                progress = True
        self._index = {n: k for k, n in enumerate(sorted(init))}
        self._init = init
        return self._index

    def _factor_terms(self, f: _Factor, x: np.ndarray):
        """Residual, Jacobians (w.r.t. node i and j) and variances of one factor."""
        ki = self._index[f.i] * 4
        xi = x[ki:ki + 4]
        if f.j is None:
            xj, kj = np.zeros(4), None
        else:
            kj = self._index[f.j] * 4
            xj = x[kj:kj + 4]
        rows_r, rows_Ji, rows_Jj, var = [], [], [], []
        Rai = _rot(xi[2]) @ f.a[:2]
        Rbj = _rot(xj[2]) @ f.b[:2]
        r_xy = (Rai + xi[:2]) - (Rbj + xj[:2])
        dvar = sum(self.drift_for(v).var_m2(self._t_last.get(v, f.stamp) - f.stamp) for v in f.vehicles())
        for ax in range(2):
            Ji = np.zeros(4)
            Ji[ax] = 1.0
            Ji[2] = _perp(Rai)[ax]
            Jj = np.zeros(4)
            Jj[ax] = -1.0
            Jj[2] = -_perp(Rbj)[ax]
            rows_r.append(r_xy[ax])
            rows_Ji.append(Ji)
            rows_Jj.append(Jj)
            var.append(f.sigma_m ** 2 + dvar)
        if f.has_yaw:
            rows_r.append(wrap_angle((xi[2] + f.a[3]) - (xj[2] + f.b[3])))
            rows_Ji.append(np.array([0.0, 0.0, 1.0, 0.0]))
            rows_Jj.append(np.array([0.0, 0.0, -1.0, 0.0]))
            var.append(f.sigma_yaw ** 2 + sum(
                self.drift_for(v).var_yaw(self._t_last.get(v, f.stamp) - f.stamp) for v in f.vehicles()))
        if f.has_z:
            rows_r.append((f.a[2] + xi[3]) - (f.b[2] + xj[3]))
            rows_Ji.append(np.array([0.0, 0.0, 0.0, 1.0]))
            rows_Jj.append(np.array([0.0, 0.0, 0.0, -1.0]))
            var.append(f.sigma_z ** 2 + sum(
                self.drift_for(v).var_z(self._t_last.get(v, f.stamp) - f.stamp) for v in f.vehicles()))
        return (np.array(rows_r), np.array(rows_Ji), np.array(rows_Jj), np.array(var), ki, kj)

    def _gauss_newton(self, nodes: Dict[str, int], active: List[_Factor]):
        N = len(nodes)
        x = np.zeros(4 * N)
        for n, k in nodes.items():
            x[k * 4:(k + 1) * 4] = self._init[n]
        x0 = x.copy()
        prior_info = np.tile(1.0 / _WEAK_PRIOR_SIGMA ** 2, N)
        H = np.diag(prior_info)
        for _ in range(30):
            H = np.diag(prior_info)
            g = prior_info * (x - x0)
            for f in active:
                r, Ji, Jj, var, ki, kj = self._factor_terms(f, x)
                e = math.sqrt(float(np.sum(r * r / var)))
                hw = 1.0 if e <= self.huber_k else self.huber_k / e
                W = hw / var
                H[ki:ki + 4, ki:ki + 4] += (Ji.T * W) @ Ji
                g[ki:ki + 4] += Ji.T @ (W * r)
                if kj is not None:
                    H[kj:kj + 4, kj:kj + 4] += (Jj.T * W) @ Jj
                    H[ki:ki + 4, kj:kj + 4] += (Ji.T * W) @ Jj
                    H[kj:kj + 4, ki:ki + 4] += (Jj.T * W) @ Ji
                    g[kj:kj + 4] += Jj.T @ (W * r)
            dx = np.linalg.solve(H, -g)
            x += dx
            x[2::4] = _wrap_arr(x[2::4])
            if float(np.max(np.abs(dx))) < 1e-10:
                break
        # chi^2 gating + residual scale on the converged solution
        chi2, m, worst, worst_e = 0.0, 0, None, 1.0
        for f in active:
            r, _, _, var, _, _ = self._factor_terms(f, x)
            e2 = float(np.sum(r * r / var))
            chi2 += e2
            m += len(r)
            ratio = e2 / _CHI2_999[len(r)]
            if ratio > worst_e:
                worst, worst_e = f, ratio
        try:
            Hinv = np.linalg.inv(H)
        except np.linalg.LinAlgError:
            Hinv = np.linalg.pinv(H)
        p_eff = 4 * N - float(np.sum(prior_info * np.diag(Hinv)))   # trace of the hat matrix
        dof = m - p_eff
        scale = max(1.0, chi2 / dof) if dof >= 1.0 else 1.0
        return x, H, scale, worst

    # -- queries --------------------------------------------------------------
    def vehicles(self) -> List[str]:
        return sorted(self._vehicles)

    def is_resolved(self, vehicle_id: str) -> bool:
        self._ensure()
        return _vnode(vehicle_id) in self._index

    def outliers(self) -> List[str]:
        """Labels of correspondences the last solve rejected."""
        self._ensure()
        return [f.label for f in self._factors if f.outlier]

    def landmark_pose(self, landmark_id: str) -> Optional[np.ndarray]:
        """(x, y, z, yaw) of a landmark in the mission frame, if placed. yaw is NaN
        if no sighting ever carried one, z NaN if none carried a height."""
        self._ensure()
        node = _lnode(landmark_id)
        if node not in self._index:
            return None
        x = self._state[node]
        var = np.diag(self._block(node, node))
        return np.array([x[0], x[1],
                         x[3] if var[3] < _WEAK_PRIOR_SIGMA[3] ** 2 / 4 else np.nan,
                         x[2] if var[2] < _ROTATION_OBSERVABLE_VAR else np.nan])

    def _block(self, node_a: str, node_b: str) -> np.ndarray:
        ka, kb = self._index[node_a] * 4, self._index[node_b] * 4
        return self._cov[ka:ka + 4, kb:kb + 4]

    def _growth(self, vehicle_id: str, now: float) -> np.ndarray:
        dm = self.drift_for(vehicle_id)
        dt = now - self._t_last.get(vehicle_id, now)
        return np.diag([dm.var_m2(dt), dm.var_m2(dt), dm.var_yaw(dt), dm.var_z(dt)])

    def alignment(self, vehicle_id: str, now: Optional[float] = None) -> FrameAlignment:
        """vehicle local -> mission, with covariance including drift since its last fix."""
        self._ensure()
        node = _vnode(vehicle_id)
        if node not in self._index:
            raise UnresolvedFrame(vehicle_id)
        now = self.clock() if now is None else now
        x = self._state[node]
        cov = self._block(node, node) + self._growth(vehicle_id, now)
        return FrameAlignment(theta=float(x[2]), tx=float(x[0]), ty=float(x[1]), tz=float(x[3]),
                              cov=cov, src=vehicle_id, dst="mission")

    def to_mission(self, peer_id: str, pos_local) -> np.ndarray:
        """A peer's local (x, y[, z]) in the mission frame. Raises UnresolvedFrame."""
        return self.alignment(peer_id).apply(pos_local)

    def from_mission(self, pos_mission) -> np.ndarray:
        """A mission-frame (x, y[, z]) in this vehicle's local frame (where to fly)."""
        return self.alignment(self.self_id).inverse().apply(pos_mission)

    def _mission_cov(self, vehicle_id: str, m_xy: np.ndarray, now: float) -> Tuple[np.ndarray, np.ndarray]:
        """2x4 Jacobian at mission point m and the 2x2 covariance of that point as
        this vehicle realises it (transform marginal + drift since last fix)."""
        node = _vnode(vehicle_id)
        x = self._state[node]
        d = m_xy - x[:2]                         # = R p_local
        J = np.array([[1.0, 0.0, -d[1], 0.0], [0.0, 1.0, d[0], 0.0]])
        C = self._block(node, node) + self._growth(vehicle_id, now)
        return J, J @ C @ J.T

    def uncertainty(self, vehicle_id: str, pos_local=None, now: Optional[float] = None) -> PeerUncertainty:
        """pos_local defaults to the vehicle's last local point seen in a
        correspondence; pass its current position (PeerState.pos_enu) when known,
        since rotation error grows with distance from its takeoff origin."""
        self._ensure()
        now = self.clock() if now is None else now
        if not self.is_resolved(vehicle_id):
            return PeerUncertainty(vehicle_id, False, math.inf, math.inf, math.inf, math.inf, False)
        al = self.alignment(vehicle_id, now)
        p = np.asarray(pos_local if pos_local is not None
                       else self._last_local.get(vehicle_id, np.zeros(3)), dtype=float)
        pc = al.point_cov(np.concatenate([p[:2], [0.0]]))
        lam = float(np.max(np.linalg.eigvalsh(pc[:2, :2])))
        return PeerUncertainty(
            vehicle_id=vehicle_id, resolved=True, sigma_m=math.sqrt(max(0.0, lam)),
            sigma_z_m=math.sqrt(max(0.0, pc[2, 2])),
            radius95_m=math.sqrt(5.991 * max(0.0, lam)),
            age_s=now - self._t_last.get(vehicle_id, now),
            rotation_observable=bool(al.cov[2, 2] < _ROTATION_OBSERVABLE_VAR),
            cov_xy=pc[:2, :2].copy())

    def task_ambiguity(self, task_pos_mission, spacing_m: float, now: Optional[float] = None,
                       vehicles: Optional[Iterable[str]] = None, reference: str = "mission",
                       confidence: float = 0.95) -> TaskAmbiguity:
        """Could two tasks closer than spacing_m near task_pos_mission be confused?

        reference="mission": tasks are absolute places (briefing, GCS). Error is
          each vehicle's own error in reaching/reporting that point; worst vehicle.
        reference="team": tasks came from peers' reports (sightings). Error is the
          *relative* error between two vehicles' versions of the same point,
          using the joint cross-covariance (two peers tied by many mutual
          observations agree with each other even when both are lost); worst pair.
          A single vehicle is never ambiguous with itself here.
        vehicles: who might execute/report (default: every vehicle known). Any
        unresolved one makes the answer ambiguous: its tasks cannot be placed.
        """
        if reference not in ("mission", "team"):
            raise ValueError("reference must be 'mission' or 'team'")
        if not (spacing_m > 0):
            raise ValueError("spacing_m must be > 0")
        self._ensure()
        now = self.clock() if now is None else now
        z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
        ids = sorted(set(vehicles) if vehicles is not None else self._vehicles)
        unresolved = tuple(v for v in ids if _vnode(v) not in self._index)
        resolved = [v for v in ids if _vnode(v) in self._index]
        m = np.asarray(task_pos_mission, dtype=float)[:2]
        sigma = 0.0
        worst: Tuple[str, ...] = ()
        terms = {v: self._mission_cov(v, m, now) for v in resolved}
        if reference == "mission":
            for v in resolved:
                s = math.sqrt(max(0.0, float(np.max(np.linalg.eigvalsh(terms[v][1])))))
                if s > sigma or not worst:
                    sigma, worst = s, (v,)
        else:
            for u, v in itertools.combinations(resolved, 2):
                Ju, Cu = terms[u]
                Jv, Cv = terms[v]
                X = Ju @ self._block(_vnode(u), _vnode(v)) @ Jv.T
                C = Cu + Cv - X - X.T
                s = math.sqrt(max(0.0, float(np.max(np.linalg.eigvalsh(C)))))
                if s > sigma or not worst:
                    sigma, worst = s, (u, v)
        if unresolved:
            sigma = math.inf
        radius = z * sigma
        return TaskAmbiguity(ambiguous=bool(unresolved) or radius >= spacing_m / 2.0,
                             sigma_m=sigma, radius_m=radius, spacing_m=float(spacing_m),
                             reference=reference, worst=worst, unresolved=unresolved)


def _init_from(corrs: List[Correspondence]) -> np.ndarray:
    """Robust (tx, ty, theta, tz) from correspondences; translation-only (theta 0)
    when the rotation is not yet observable (one point, no heading)."""
    try:
        al = FrameAlignment.estimate(corrs, robust=True)
        return al.params
    except ValueError:
        d = np.array([[c.dst[0] - c.src[0], c.dst[1] - c.src[1]] for c in corrs])
        dz = [c.dst[2] - c.src[2] for c in corrs if c.has_z]
        return np.array([float(np.median(d[:, 0])), float(np.median(d[:, 1])), 0.0,
                         float(np.median(dz)) if dz else 0.0])


# --------------------------------------------------------------------------- wire format
# Compact dicts for TeamLink. Keys are short because every byte goes on the air:
# team_link v2 sends a canonical-JSON body (sorted keys, no spaces) after a
# 54-byte signed header that also carries the sender id; wire_size() counts it.
# Stamps never cross the link: "age" is seconds between observation and send,
# and the receiver re-stamps with its own clock.
MSG_LAUNCH = "SF_LAUNCH"
MSG_FIDUCIAL = "SF_TAG"
MSG_RELATIVE = "SF_REL"


def _r(v: Optional[float], nd: int) -> Optional[float]:
    if v is None or not math.isfinite(v):
        return None
    return round(float(v), nd)


def _vec(p: Sequence[Optional[float]], n: int, nd: int = 3) -> list:
    vals = list(p) + [None] * (n - len(p))
    return [_r(v, nd) for v in vals[:n]]


def _check_id(s: str) -> str:
    if not isinstance(s, str) or not (0 < len(s) <= MAX_ID_LEN):
        raise ValueError(f"id must be a non-empty string of at most {MAX_ID_LEN} chars: {s!r}")
    return s


TEAM_LINK_FRAME_OVERHEAD = 54   # team_link v2: header + HMAC-SHA256, before the sender id


def wire_size(msg: dict) -> int:
    """Bytes of the signed team_link v2 frame this dict becomes: header and
    HMAC, the sender id (``o``), and the canonical JSON body."""
    body = json.dumps(msg, sort_keys=True, separators=(",", ":")).encode()
    return TEAM_LINK_FRAME_OVERHEAD + len(str(msg.get("o", "")).encode()) + len(body)


def encode_message(obj, now: float) -> dict:
    """LaunchReport / FiducialSighting / RelativeObservation -> small dict.

    now is the sender's clock (same clock as obj.stamp). Raises ValueError if
    the result would exceed MAX_PAYLOAD_BYTES (only possible with odd ids).
    """
    age = _r(max(0.0, now - obj.stamp), 2)
    if isinstance(obj, LaunchReport):
        p = _pose4(obj.local_pose)
        msg = {"type": MSG_LAUNCH, "v": MSG_VERSION, "o": _check_id(obj.vehicle_id),
               "p": _vec(p[:3], 3) + [_r(p[3], 4)], "age": age}
    elif isinstance(obj, FiducialSighting):
        p = _pose4(obj.pose_local)
        msg = {"type": MSG_FIDUCIAL, "v": MSG_VERSION, "o": _check_id(obj.observer_id),
               "L": _check_id(obj.landmark_id), "p": _vec(p[:3], 3) + [_r(p[3], 4)],
               "s": [_r(obj.sigma_m, 3), _r(obj.sigma_yaw_rad, 4), _r(obj.sigma_z_m, 3)],
               "age": age}
    elif isinstance(obj, RelativeObservation):
        msg = {"type": MSG_RELATIVE, "v": MSG_VERSION, "o": _check_id(obj.observer_id),
               "tg": _check_id(obj.target_id), "q": _vec(obj.target_in_observer, 3),
               "pb": _vec(obj.target_reported, 3), "s": [_r(obj.sigma_m, 3), _r(obj.sigma_z_m, 3)],
               "age": age}
    else:
        raise TypeError(f"cannot encode {type(obj).__name__}")
    size = wire_size(msg)
    if size > MAX_PAYLOAD_BYTES:
        raise ValueError(f"{msg['type']} would be {size} bytes > {MAX_PAYLOAD_BYTES}")
    return msg


def _num(v, name: str, allow_none: bool = True) -> Optional[float]:
    if v is None:
        if allow_none:
            return None
        raise ValueError(f"{name} missing")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{name} not a finite number: {v!r}")
    return float(v)


def _dvec(v, n: int, name: str) -> Tuple[Optional[float], ...]:
    if not isinstance(v, list) or len(v) != n:
        raise ValueError(f"{name} must be a list of {n}")
    out = tuple(_num(x, name) for x in v)
    if out[0] is None or out[1] is None:
        raise ValueError(f"{name} needs x and y")
    return out


def decode_message(msg: dict, recv_time: float):
    """Small dict -> LaunchReport / FiducialSighting / RelativeObservation.

    Returns None for any message that is not a shared-frame one (POSITION etc.),
    so a whole drain_inbox() can be fed through. Raises ValueError on a
    malformed or future-version frame message. Extra keys ("from", "t",
    "_addr" from team_link) are ignored.
    """
    mtype = msg.get("type")
    if mtype not in (MSG_LAUNCH, MSG_FIDUCIAL, MSG_RELATIVE):
        return None
    if msg.get("v") != MSG_VERSION:
        raise ValueError(f"unsupported {mtype} version {msg.get('v')!r}")
    obs = _check_id(msg.get("o") or msg.get("from"))
    age = _num(msg.get("age"), "age", allow_none=False)
    if age < 0:
        raise ValueError("negative age")
    stamp = recv_time - age
    if mtype == MSG_LAUNCH:
        p = _dvec(msg.get("p"), 4, "p")
        return LaunchReport(obs, tuple(0.0 if v is None and k == 2 else v for k, v in enumerate(p)), stamp)
    s = msg.get("s")
    if mtype == MSG_FIDUCIAL:
        if not isinstance(s, list) or len(s) != 3:
            raise ValueError("s must be [sigma_m, sigma_yaw, sigma_z]")
        sm = _num(s[0], "sigma_m", allow_none=False)
        sy = _num(s[1], "sigma_yaw") or math.radians(5.0)
        if sm <= 0 or sy <= 0:
            raise ValueError("sigmas must be > 0")
        return FiducialSighting(obs, _check_id(msg.get("L")), _dvec(msg.get("p"), 4, "p"),
                                sigma_m=sm, sigma_yaw_rad=sy, sigma_z_m=_num(s[2], "sigma_z"),
                                stamp=stamp)
    if not isinstance(s, list) or len(s) != 2:
        raise ValueError("s must be [sigma_m, sigma_z]")
    sm = _num(s[0], "sigma_m", allow_none=False)
    if sm <= 0:
        raise ValueError("sigma_m must be > 0")
    return RelativeObservation(obs, _check_id(msg.get("tg")), _dvec(msg.get("q"), 3, "q"),
                               _dvec(msg.get("pb"), 3, "pb"), sigma_m=sm,
                               sigma_z_m=_num(s[1], "sigma_z"), stamp=stamp)
