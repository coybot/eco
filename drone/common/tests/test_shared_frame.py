"""Shared mission frame: alignment maths, team fusion, drift growth, wire format.

What a wrong answer costs here is silent: a transform that is off by a few
metres still "works" (every vehicle flies somewhere plausible), and an
uncertainty that is too small makes the allocator send two vehicles to what is
physically one task. So the statistical tests check the reported covariance
against Monte-Carlo error, not just that a number comes out. All randomness is
seeded.

Run: cd coybot && .venv/bin/python -m pytest eco/drone/common/tests/test_shared_frame.py -q
"""
from __future__ import annotations

import ast
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared_frame import (  # noqa: E402
    MAX_ID_LEN, MAX_PAYLOAD_BYTES, MSG_FIDUCIAL, Correspondence, DriftModel, FiducialSighting,
    FrameAlignment, LaunchLayout, LaunchReport, RelativeObservation, TeamFrameEstimator,
    UnresolvedFrame, decode_message, encode_message, wire_size, wrap_angle,
)

MODULE = Path(__file__).resolve().parents[1] / "shared_frame.py"


def _rand_alignment(rng, span=50.0) -> FrameAlignment:
    return FrameAlignment(theta=float(rng.uniform(-math.pi, math.pi)),
                          tx=float(rng.uniform(-span, span)), ty=float(rng.uniform(-span, span)),
                          tz=float(rng.uniform(-3, 3)))


def _corrs(T, A, sigma, rng=None, yaw=None):
    B = T.apply(A)
    if rng is not None:
        B = B + rng.normal(0.0, sigma, size=B.shape)
    out = []
    for k, (a, b) in enumerate(zip(A, B)):
        sy = None if yaw is None else yaw[k]
        out.append(Correspondence(tuple(a), tuple(b), sigma_m=sigma, sigma_z_m=sigma,
                                  src_yaw=sy, dst_yaw=None if sy is None else T.apply_yaw(sy)))
    return out


def _param_err(est: FrameAlignment, T: FrameAlignment) -> np.ndarray:
    return np.array([est.tx - T.tx, est.ty - T.ty, wrap_angle(est.theta - T.theta), est.tz - T.tz])


class _Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


# =========================================================================== py3.8
def test_module_parses_as_python38():
    """Aircraft run 3.8: no match, no parenthesised context managers, etc."""
    src = MODULE.read_text()
    ast.parse(src, feature_version=(3, 8))
    assert "from __future__ import annotations" in src


# =========================================================================== FrameAlignment
@pytest.mark.parametrize("seed", range(10))
def test_exact_recovery_from_noiseless_points(seed):
    rng = np.random.default_rng(seed)
    T = _rand_alignment(rng)
    A = rng.uniform(-40, 40, size=(8, 3))
    est = FrameAlignment.estimate(_corrs(T, A, 0.2))
    assert np.max(np.abs(_param_err(est, T))) < 1e-9
    assert est.n_inliers == 8 and est.rms_m < 1e-9


def test_exact_recovery_from_one_pose_correspondence():
    T = FrameAlignment(theta=2.5, tx=-7.0, ty=11.0, tz=0.4)
    c = _corrs(T, np.array([[3.0, -2.0, 1.0]]), 0.5, yaw=[0.3])
    est = FrameAlignment.estimate(c)
    assert np.max(np.abs(_param_err(est, T))) < 1e-12


def test_exact_recovery_mixed_points_and_headings_wraps_through_pi():
    T = FrameAlignment(theta=math.pi - 0.01, tx=1.0, ty=2.0)
    rng = np.random.default_rng(3)
    A = rng.uniform(-10, 10, size=(5, 2))
    c = _corrs(T, A, 0.3, yaw=[0.2, None, -3.1, None, 1.0])
    est = FrameAlignment.estimate(c)
    assert abs(wrap_angle(est.theta - T.theta)) < 1e-10
    assert np.allclose(est.t, T.t, atol=1e-9)


def test_rotation_unobservable_raises():
    c = [Correspondence((1.0, 1.0), (4.0, 5.0)), Correspondence((1.0, 1.0), (4.0, 5.0))]
    with pytest.raises(ValueError, match="unobservable"):
        FrameAlignment.estimate(c)


def test_height_unobserved_reports_huge_variance():
    T = FrameAlignment(theta=0.3, tx=1.0, ty=2.0)
    A = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]])
    est = FrameAlignment.estimate(_corrs(T, A, 0.1))
    assert est.cov[3, 3] >= 1e10 and est.tz == 0.0


def _monte_carlo(sigma_true, sigma_declared, trials=400, n=12, seed=7):
    rng = np.random.default_rng(seed)
    errs, nees, covs = [], [], []
    for _ in range(trials):
        T = _rand_alignment(rng)
        A = rng.uniform(-25, 25, size=(n, 3))
        B = T.apply(A) + rng.normal(0.0, sigma_true, size=(n, 3))
        c = [Correspondence(tuple(a), tuple(b), sigma_m=sigma_declared, sigma_z_m=sigma_declared)
             for a, b in zip(A, B)]
        est = FrameAlignment.estimate(c)
        e = _param_err(est, T)
        errs.append(e)
        covs.append(est.cov)
        nees.append(float(e[:3] @ np.linalg.solve(est.cov[:3, :3], e[:3])))
    return np.array(errs), np.array(nees), np.array(covs)


def test_reported_covariance_matches_noise_statistics():
    """Correct sigma declared: NEES ~ chi^2_3 (mean 3), per-parameter spread ~ reported.

    The residual scale is max(1, chi^2/dof), so it can only widen: mean NEES sits
    a little under 3 and the covariance is a hair conservative, never optimistic.
    """
    errs, nees, covs = _monte_carlo(0.2, 0.2)
    assert 2.4 < nees.mean() < 3.3, nees.mean()
    assert 0.92 < np.mean(nees < 7.815) <= 1.0            # chi^2_3 95%
    for k in range(4):
        ratio = errs[:, k].std() / math.sqrt(np.mean(covs[:, k, k]))
        assert 0.8 < ratio < 1.1, (k, ratio)


def test_residual_scaling_widens_covariance_when_sigma_is_understated():
    """Declared 4x too optimistic: chi^2/dof scaling brings NEES back near 3
    (slightly above, Student-t tails), where the raw information would give ~48."""
    errs, nees, covs = _monte_carlo(0.2, 0.05)
    assert 2.5 < nees.mean() < 4.5, nees.mean()


def test_outliers_rejected_and_flagged():
    rng = np.random.default_rng(11)
    T = _rand_alignment(rng)
    A = rng.uniform(-30, 30, size=(20, 3))
    c = _corrs(T, A, 0.1, rng=rng)
    bad = [2, 7, 13, 17]                                    # 20% gross outliers, 5-40 m off
    for k in bad:
        b = np.array(c[k].dst) + np.append(rng.uniform(5, 40, 2) * rng.choice([-1, 1], 2), 0.0)
        c[k] = Correspondence(c[k].src, tuple(b), sigma_m=0.1)
    est = FrameAlignment.estimate(c)
    assert sorted(est.outlier_indices) == bad
    err = _param_err(est, T)
    assert np.hypot(err[0], err[1]) < 0.15 and abs(err[2]) < 0.01
    naive = FrameAlignment.estimate(c, robust=False)
    assert np.hypot(*_param_err(naive, T)[:2]) > 1.0     # what RANSAC saved us from


def test_outlier_pose_correspondence_with_wrong_heading_rejected():
    T = FrameAlignment(theta=0.4, tx=3.0, ty=-1.0)
    A = np.array([[0.0, 0.0], [5.0, 0.0], [0.0, 5.0], [5.0, 5.0], [9.0, 1.0]])
    c = _corrs(T, A, 0.05, yaw=[0.0, 0.1, 0.2, 0.3, 0.4])
    c[1] = Correspondence(c[1].src, c[1].dst, sigma_m=0.05, src_yaw=0.1, dst_yaw=0.1 + 0.4 + 1.0,
                          sigma_yaw_rad=0.02)
    est = FrameAlignment.estimate(c)
    assert est.outlier_indices == [1]
    assert abs(est.theta - 0.4) < 1e-9


def test_many_points_uses_sampled_hypotheses_deterministically():
    rng = np.random.default_rng(5)
    T = _rand_alignment(rng)
    A = rng.uniform(-30, 30, size=(60, 2))                  # 1770 pairs > 400 cap
    c = _corrs(T, A, 0.1, rng=rng)
    e1 = FrameAlignment.estimate(c, seed=1)
    e2 = FrameAlignment.estimate(c, seed=1)
    assert e1.params.tolist() == e2.params.tolist()
    assert np.hypot(*_param_err(e1, T)[:2]) < 0.1


# --------------------------------------------------------------------------- algebra
def _with_cov(T, rng, scale=0.01):
    M = rng.normal(size=(4, 4)) * scale
    T.cov = M @ M.T + np.eye(4) * 1e-4
    return T


def test_inverse_and_composition_identities():
    rng = np.random.default_rng(2)
    T1, T2, T3 = (_with_cov(_rand_alignment(rng), rng) for _ in range(3))
    p = rng.uniform(-20, 20, size=(5, 3))
    I = T1.compose(T1.inverse())
    assert np.allclose(I.params, 0.0, atol=1e-12)
    assert np.allclose(T1.inverse().apply(T1.apply(p)), p, atol=1e-10)
    assert np.allclose(T1.compose(T2).apply(p), T1.apply(T2.apply(p)), atol=1e-10)
    L, R = T1.compose(T2).compose(T3), T1.compose(T2.compose(T3))
    assert abs(wrap_angle(L.theta - R.theta)) < 1e-12 and np.allclose(L.t, R.t) and abs(L.tz - R.tz) < 1e-12
    assert np.allclose(L.cov, R.cov, atol=1e-12)          # first-order propagation is associative
    ii = T1.inverse().inverse()
    assert np.allclose(ii.params, T1.params) and np.allclose(ii.cov, T1.cov, atol=1e-12)
    assert abs(T1.inverse().apply_yaw(T1.apply_yaw(0.7)) - 0.7) < 1e-12


def test_compose_rejects_mismatched_frames():
    a = FrameAlignment(src="b", dst="mission")
    b = FrameAlignment(src="c", dst="x")
    with pytest.raises(ValueError):
        a.compose(b)
    assert a.compose(FrameAlignment(src="c", dst="b")).src == "c"


def test_propagated_covariances_match_monte_carlo():
    rng = np.random.default_rng(4)
    T1 = _with_cov(FrameAlignment(theta=0.8, tx=10.0, ty=-4.0, tz=1.0), rng, 0.05)
    T2 = _with_cov(FrameAlignment(theta=-2.0, tx=30.0, ty=12.0, tz=-0.5), rng, 0.05)
    p = np.array([15.0, -8.0, 2.0])
    n = 20000
    s1 = rng.multivariate_normal(T1.params, T1.cov, n)
    s2 = rng.multivariate_normal(T2.params, T2.cov, n)
    inv_s, comp_s, pts = [], [], []
    for a, b in zip(s1, s2):
        A = FrameAlignment(theta=a[2], tx=a[0], ty=a[1], tz=a[3])
        B = FrameAlignment(theta=b[2], tx=b[0], ty=b[1], tz=b[3])
        inv_s.append(A.inverse().params)
        comp_s.append(A.compose(B).params)
        pts.append(A.apply(p))
    for emp, ana in ((np.cov(np.array(inv_s).T), T1.inverse().cov),
                     (np.cov(np.array(comp_s).T), T1.compose(T2).cov),
                     (np.cov(np.array(pts).T), T1.point_cov(p))):
        assert np.allclose(emp, ana, rtol=0.1, atol=2e-3 * np.max(np.abs(ana))), (emp, ana)


def test_point_cov_has_lever_arm():
    T = FrameAlignment(cov=np.diag([0.01, 0.01, math.radians(1.0) ** 2, 0.04]))
    near = T.point_cov([0.0, 0.0, 0.0])
    far = T.point_cov([100.0, 0.0, 0.0])
    assert math.isclose(near[1, 1], 0.01)
    assert math.isclose(far[1, 1], 0.01 + (100 * math.radians(1.0)) ** 2)   # 1.75 m at 100 m
    assert math.isclose(far[0, 0], 0.01) and math.isclose(far[2, 2], 0.04)


# =========================================================================== drift
def test_drift_model_linear_and_random_walk():
    lin = DriftModel(rate_mps=0.3)
    rw = DriftModel(rate_mps=0.3, mode="random_walk", step_s=0.1)
    assert math.isclose(lin.sigma_m(60.0), 18.0)
    assert math.isclose(rw.sigma_m(60.0), 0.3 * math.sqrt(6.0))          # 0.73 m, localization.py's figure
    assert lin.sigma_m(-5.0) == 0.0
    assert DriftModel(rate_mps=0.3, planar=True).var_z(10.0) == 0.0
    with pytest.raises(ValueError):
        DriftModel(mode="ramp")


def test_random_walk_mode_matches_localization_fabric_statistics():
    """Same recurrence as LocalizationFabric GPS_DENIED: bias += N(0, drift*dt) per step."""
    rng = np.random.default_rng(0)
    drift, dt, steps = 0.3, 0.1, 600
    bias = rng.normal(0.0, drift * dt, size=(4000, steps)).sum(axis=1)
    model = DriftModel(rate_mps=drift, mode="random_walk", step_s=dt)
    assert abs(bias.std() / model.sigma_m(steps * dt) - 1.0) < 0.05


def test_drift_for_class_reads_vehicle_class():
    from vehicle_class import get_class
    m = DriftModel.for_class("quadcopter")
    assert m.rate_mps == get_class("quadcopter").localization.gps_denied_drift_mps


# =========================================================================== team estimator
def _truth_team():
    """Three vehicles; true local->mission transforms."""
    return {
        "a": FrameAlignment(theta=0.10, tx=0.0, ty=0.0, tz=0.0),
        "b": FrameAlignment(theta=1.30, tx=20.0, ty=-5.0, tz=0.5),
        "c": FrameAlignment(theta=-2.40, tx=-15.0, ty=30.0, tz=-1.0),
    }


def _local_of(T: FrameAlignment, p_mission, yaw_mission=None):
    inv = T.inverse()
    p = inv.apply(np.asarray(p_mission, dtype=float))
    return p, (None if yaw_mission is None else inv.apply_yaw(yaw_mission))


def _launch(est, T, vid, mission_pose, stamp=0.0):
    p, yaw = _local_of(T, mission_pose[:3], mission_pose[3])
    assert est.add_launch(LaunchReport(vid, (p[0], p[1], p[2], yaw), stamp))


def _sight(est, T, vid, lid, tag_pose, stamp, sigma=0.3, noise=None):
    p, yaw = _local_of(T, tag_pose[:3], tag_pose[3])
    if noise is not None:
        p = p + noise
    est.add_fiducial(FiducialSighting(vid, lid, (p[0], p[1], p[2], yaw), sigma_m=sigma, stamp=stamp))


def _relative(est, Tobs, Ttgt, obs, tgt, target_mission, stamp, sigma=0.3):
    q, _ = _local_of(Tobs, target_mission)
    pb, _ = _local_of(Ttgt, target_mission)
    est.add_relative(RelativeObservation(obs, tgt, tuple(q), tuple(pb), sigma_m=sigma, stamp=stamp))


def _check_alignment(est, vid, T, tol=1e-6, now=0.0):
    al = est.alignment(vid, now=now)
    assert abs(wrap_angle(al.theta - T.theta)) < tol, (vid, al.theta, T.theta)
    assert np.allclose(al.t, T.t, atol=tol) and abs(al.tz - T.tz) < tol, (vid, al.params, T.params)


def test_team_exact_recovery_launch_fiducial_and_relative():
    """a: briefed launch. b: no launch; shares tags with a, seen minutes apart.
    c: no launch, no tags; only a's range/bearing to it at four places."""
    Ts = _truth_team()
    clock = _Clock(0.0)
    est = TeamFrameEstimator("a", layout=LaunchLayout(poses={"a": (0.0, 0.0, 0.0, 0.10)}),
                             drift=DriftModel(rate_mps=0.0), clock=clock)
    _launch(est, Ts["a"], "a", (0.0, 0.0, 0.0, 0.10))
    for k, tag in enumerate([(5.0, 5.0, 0.0, 0.3), (40.0, -10.0, 0.0, None)]):
        _sight(est, Ts["a"], "a", f"tag{k}", tag, stamp=10.0 + k)
        _sight(est, Ts["b"], "b", f"tag{k}", tag, stamp=200.0 + k)
    for k, pc in enumerate([(0.0, 10.0, 3.0), (10.0, 20.0, 3.0), (-5.0, 25.0, 2.0), (3.0, 3.0, 5.0)]):
        _relative(est, Ts["a"], Ts["c"], "a", "c", pc, stamp=50.0 + k)
    for vid in "abc":
        assert est.is_resolved(vid)
        _check_alignment(est, vid, Ts[vid])
    assert np.allclose(est.landmark_pose("tag0")[:2], [5.0, 5.0], atol=1e-6)
    assert abs(est.landmark_pose("tag0")[3] - 0.3) < 1e-6
    # to_mission / from_mission are inverse views of the same transform
    p_local = np.array([7.0, -3.0, 2.0])
    pm = est.to_mission("b", p_local)
    assert np.allclose(pm, Ts["b"].apply(p_local), atol=1e-6)
    assert np.allclose(est.from_mission(est.to_mission("a", p_local)), p_local, atol=1e-9)
    assert est.outliers() == []


def test_without_layout_mission_frame_is_anchor_takeoff_frame():
    Ts = _truth_team()
    est = TeamFrameEstimator("b", drift=DriftModel(rate_mps=0.0), clock=_Clock(0.0))
    for k, tag in enumerate([(5.0, 5.0, 0.0, 0.3), (40.0, -10.0, 0.0, -1.0)]):
        _sight(est, Ts["a"], "a", f"tag{k}", tag, stamp=1.0)
        _sight(est, Ts["b"], "b", f"tag{k}", tag, stamp=2.0)
    al_b = est.alignment("b", now=2.0)
    assert np.allclose(al_b.params, 0.0, atol=1e-6)            # self is the mission frame
    expected = Ts["b"].inverse().compose(Ts["a"])                # a's local -> b's local
    _check_alignment(est, "a", expected, now=2.0)


def test_unresolved_peer_raises_and_is_ambiguous():
    est = TeamFrameEstimator("a", clock=_Clock(0.0))
    est._vehicles.add("ghost")                                   # heard of, never tied in
    assert est.is_resolved("a") and not est.is_resolved("ghost")
    with pytest.raises(UnresolvedFrame):
        est.to_mission("ghost", (0.0, 0.0))
    amb = est.task_ambiguity((0.0, 0.0), spacing_m=50.0, now=0.0)
    assert amb.ambiguous and amb.unresolved == ("ghost",) and math.isinf(amb.sigma_m)
    assert not est.task_ambiguity((0.0, 0.0), 50.0, now=0.0, vehicles=["a"]).ambiguous
    u = est.uncertainty("ghost")
    assert not u.resolved and math.isinf(u.sigma_m)


def test_single_relative_point_leaves_rotation_unobservable():
    Ts = _truth_team()
    est = TeamFrameEstimator("a", drift=DriftModel(rate_mps=0.0), clock=_Clock(0.0))
    _relative(est, FrameAlignment(), Ts["c"], "a", "c", (5.0, 5.0, 0.0), stamp=0.0)
    u_here = est.uncertainty("c", pos_local=_local_of(Ts["c"], (5.0, 5.0))[0], now=0.0)
    u_far = est.uncertainty("c", pos_local=[100.0, 0.0, 0.0], now=0.0)
    assert not u_here.rotation_observable
    assert u_here.sigma_m < 1.0                       # exactly where it was seen: known
    assert u_far.sigma_m > 1000.0                     # anywhere else: unknown, and says so


def test_uncertainty_grows_with_drift_and_resets_on_new_correspondence():
    Ts = _truth_team()
    clock = _Clock(0.0)
    lay = LaunchLayout(poses={"a": (0.0, 0.0, 0.0, 0.1), "b": (20.0, -5.0, 0.5, 1.3)},
                       sigma_m=0.5, sigma_yaw_rad=math.radians(2.0))
    est = TeamFrameEstimator("a", layout=lay, drift=DriftModel(rate_mps=0.2), clock=clock)
    _launch(est, Ts["a"], "a", lay.poses["a"])
    _launch(est, Ts["b"], "b", (20.0, -5.0, 0.5, 1.3))
    at_origin = [0.0, 0.0, 0.0]
    s0 = est.uncertainty("b", pos_local=at_origin, now=0.0).sigma_m
    assert abs(s0 - 0.5) < 1e-3
    sig = [est.uncertainty("b", pos_local=at_origin, now=t).sigma_m for t in (10.0, 30.0, 60.0)]
    for t, s in zip((10.0, 30.0, 60.0), sig):
        assert abs(s - math.hypot(0.5, 0.2 * t)) < 1e-3, (t, s)
    assert est.uncertainty("b", now=60.0).age_s == 60.0
    # random-walk model grows as sqrt(t)
    est_rw = TeamFrameEstimator("a", layout=lay, drift=DriftModel(rate_mps=0.2, mode="random_walk"),
                                clock=clock)
    _launch(est_rw, Ts["b"], "b", (20.0, -5.0, 0.5, 1.3))
    s_rw = est_rw.uncertainty("b", pos_local=at_origin, now=60.0).sigma_m
    assert abs(s_rw - math.hypot(0.5, 0.2 * math.sqrt(6.0))) < 1e-3
    # A shared (unsurveyed) tag at t=60 ties b to a -- but a has drifted just as
    # much, so absolutely they only average: ~12 m / sqrt(2). Relative to each
    # other they are now tight. Evidence is counted once, not bootstrapped.
    tag = (10.0, 0.0, 0.0, 0.0)
    _sight(est, Ts["a"], "a", "t", tag, stamp=60.0, sigma=0.1)
    _sight(est, Ts["b"], "b", "t", tag, stamp=60.0, sigma=0.1)
    b_tag = est.uncertainty("b", pos_local=_local_of(Ts["b"], tag)[0], now=60.0)
    assert b_tag.age_s == 0.0
    assert abs(b_tag.sigma_m - sig[-1] / math.sqrt(2.0)) < 0.2
    assert est.task_ambiguity(tag[:2], 2.0, now=60.0, reference="team").sigma_m < 0.3
    # A surveyed tag is absolute: b's sighting of it at t=90 resets b, not a.
    est.add_surveyed_landmark("s", (25.0, 5.0, 0.0, 0.0), sigma_m=0.05, sigma_yaw_rad=0.01)
    _sight(est, Ts["b"], "b", "s", (25.0, 5.0, 0.0, 0.0), stamp=90.0, sigma=0.1)
    b_s = est.uncertainty("b", pos_local=_local_of(Ts["b"], (25.0, 5.0))[0], now=90.0)
    assert b_s.age_s == 0.0 and b_s.sigma_m < 0.2
    assert est.uncertainty("a", now=90.0).age_s == 30.0
    # ...and it grows again from there, at b's own rate
    later = est.uncertainty("b", pos_local=_local_of(Ts["b"], (25.0, 5.0))[0], now=100.0)
    assert abs(later.sigma_m - math.hypot(b_s.sigma_m, 0.2 * 10.0)) < 0.05
    est.set_drift("b", DriftModel(rate_mps=0.02))          # e.g. a rover on odometry
    slow = est.uncertainty("b", pos_local=_local_of(Ts["b"], (25.0, 5.0))[0], now=100.0)
    assert abs(slow.sigma_m - math.hypot(b_s.sigma_m, 0.02 * 10.0)) < 0.05


def test_joint_rejects_wrong_tag_sighting():
    Ts = _truth_team()
    est = TeamFrameEstimator("a", layout=LaunchLayout(poses={"a": (0.0, 0.0, 0.0, 0.10)}),
                             drift=DriftModel(rate_mps=0.0), clock=_Clock(0.0))
    _launch(est, Ts["a"], "a", (0.0, 0.0, 0.0, 0.10))
    rng = np.random.default_rng(9)
    tags = [(5.0, 5.0, 0.0, None), (40.0, -10.0, 0.0, None), (20.0, 25.0, 0.0, None),
            (-10.0, 15.0, 0.0, None), (30.0, 30.0, 0.0, None)]
    for k, tag in enumerate(tags):
        _sight(est, Ts["a"], "a", f"t{k}", tag, 1.0, sigma=0.2, noise=rng.normal(0, 0.2, 3))
        _sight(est, Ts["b"], "b", f"t{k}", tag, 2.0, sigma=0.2, noise=rng.normal(0, 0.2, 3))
    # b mis-decodes a tag id: it reports t1 at t3's position
    _sight(est, Ts["b"], "b", "t1", tags[3], 3.0, sigma=0.2)
    assert est.outliers() == ["tag t1 by b @3.0"]
    al = est.alignment("b", now=3.0)
    assert np.hypot(*(al.t - Ts["b"].t)) < 0.5 and abs(wrap_angle(al.theta - Ts["b"].theta)) < 0.02


def test_ambiguity_decisions_fresh_stale_mission_and_team():
    """Two peers tied by many mutual sightings agree with each other (team) long
    after both have drifted away from the briefing frame (mission)."""
    Ts = _truth_team()
    clock = _Clock(0.0)
    lay = LaunchLayout(poses={"a": (0.0, 0.0, 0.0, 0.1), "b": (20.0, -5.0, 0.5, 1.3)},
                       sigma_m=0.3, sigma_yaw_rad=math.radians(1.0))
    est = TeamFrameEstimator("a", layout=lay, drift=DriftModel(rate_mps=0.05), clock=clock)
    _launch(est, Ts["a"], "a", lay.poses["a"])
    _launch(est, Ts["b"], "b", lay.poses["b"])
    task = (10.0, 0.0)
    fresh = est.task_ambiguity(task, spacing_m=5.0, now=0.0)
    assert not fresh.ambiguous and fresh.radius_m < 2.5
    assert est.task_ambiguity(task, spacing_m=1.0, now=0.0).ambiguous      # 1 m is too tight
    stale = est.task_ambiguity(task, spacing_m=5.0, now=120.0)             # 6 m drift sigma
    assert stale.ambiguous and stale.worst in (("a",), ("b",))
    # at t=118..120 a sees b at many places: they now agree with each other
    for k in range(8):
        pm = (10.0 + 3 * k, -2.0 + 2 * (k % 3), 3.0)
        _relative(est, Ts["a"], Ts["b"], "a", "b", pm, stamp=118.0 + 0.25 * k, sigma=0.2)
    team = est.task_ambiguity(task, spacing_m=5.0, now=120.0, reference="team")
    mission = est.task_ambiguity(task, spacing_m=5.0, now=120.0, reference="mission")
    assert not team.ambiguous and team.worst == ("a", "b")
    assert mission.ambiguous                                   # but both are lost vs the briefing
    assert team.sigma_m < 0.25 * mission.sigma_m
    assert not est.task_ambiguity(task, 5.0, now=120.0, vehicles=["a"], reference="team").ambiguous
    with pytest.raises(ValueError):
        est.task_ambiguity(task, 0.0)


def _drifting_team(truth_mode, rate, seeds=25):
    """Monte Carlo of the real use: three vehicles with unknown frames that drift.

    a and b are briefed (launch poses, with placement noise); c is tied in only
    by three shared tags and a's range/bearing to it. Each vehicle's frame
    drifts either as a constant-velocity bias in a random direction at ``rate``
    ("bias", the linear model's case) or as localization.py's random walk at sim
    dt 0.1 s ("random_walk"). For each vehicle, at 1, 30 and 60 s after the
    last correspondence, compare the mission-frame error of its version of a
    task point with the reported 95% radius. Returns per-sample
    (error_m, radius95_m) and the time since the last tie-in.
    """
    out = []
    for seed in range(seeds):
        rng = np.random.default_rng(100 + seed)
        base = {v: _rand_alignment(rng, 30.0) for v in "abc"}
        if truth_mode == "bias":
            vel = {v: rate * np.array([math.cos(h), math.sin(h), 0.0])
                   for v, h in zip("abc", rng.uniform(-math.pi, math.pi, 3))}

            def bias(v, t):
                return vel[v] * t
        else:
            walk = {v: np.vstack([np.zeros((1, 3)), np.cumsum(rng.normal(0, rate * 0.1, (1400, 3)), axis=0)])
                    for v in "abc"}

            def bias(v, t):
                return walk[v][int(round(t / 0.1))]

        def truth(v, t):
            # believed = true + bias, so the local->mission translation moves by -R bias
            B, w = base[v], bias(v, t)
            d = -B.R @ w[:2]
            return FrameAlignment(theta=B.theta, tx=B.tx + d[0], ty=B.ty + d[1], tz=B.tz - w[2])

        lay = LaunchLayout(poses={"a": (0.0, 0.0, 0.0, 0.0), "b": (8.0, 0.0, 0.0, 0.0)},
                           sigma_m=0.3, sigma_yaw_rad=math.radians(2.0))
        model = DriftModel(rate_mps=rate) if truth_mode == "bias" else \
            DriftModel(rate_mps=rate, mode="random_walk", step_s=0.1)
        clock = _Clock(0.0)
        est = TeamFrameEstimator("a", layout=lay, drift=model, clock=clock)
        for v in "ab":
            mp = np.array(lay.poses[v])
            mp[:2] += rng.normal(0, 0.3, 2)
            mp[3] += rng.normal(0, math.radians(2.0))
            p, yaw = _local_of(truth(v, 0.0), mp[:3], mp[3])
            est.add_launch(LaunchReport(v, (p[0], p[1], p[2], yaw), 0.0))
        tags = [(20.0, 10.0, 0.0, 0.0), (35.0, -15.0, 0.0, 1.0), (-5.0, 30.0, 0.0, 2.0)]
        for k, tag in enumerate(tags):
            for v, t in (("a", 20.0 + k), ("b", 40.0 + k), ("c", 60.0 + k)):
                p, yaw = _local_of(truth(v, t), tag[:3], tag[3])
                p = p + rng.normal(0, 0.2, 3)
                est.add_fiducial(FiducialSighting(v, f"t{k}", (p[0], p[1], p[2], yaw + rng.normal(0, 0.05)),
                                                  sigma_m=0.2, sigma_yaw_rad=0.05, stamp=t))
        for k in range(5):
            t = 70.0 + k
            pc = np.array([10.0 + 4 * k, 5.0, 4.0])
            q, _ = _local_of(truth("a", t), pc)
            pb, _ = _local_of(truth("c", t), pc)
            est.add_relative(RelativeObservation("a", "c", tuple(q + rng.normal(0, 0.3, 3)), tuple(pb),
                                                 sigma_m=0.3, stamp=t))
        task = np.array([15.0, 5.0, 0.0])
        for now in (75.0, 105.0, 135.0):
            clock.t = now                                          # queries default to clock()
            for v in "abc":
                p_local = truth(v, now).inverse().apply(task)      # where v really is at the task
                e = float(np.linalg.norm(est.to_mission(v, p_local)[:2] - task[:2]))
                u = est.uncertainty(v, pos_local=p_local)
                out.append((e, u.radius95_m, u.age_s))
    return np.array(out)


def test_drifting_team_bias_drift_is_covered_by_linear_model():
    """Linear model vs constant-velocity bias: per axis it assumes rate*t, the
    truth has rate*t in total, so it must over-cover -- never under-cover."""
    r = _drifting_team("bias", rate=0.05)
    assert np.mean(r[:, 0] <= r[:, 1]) >= 0.97
    fresh = r[r[:, 2] <= 1.0]
    assert np.median(fresh[:, 0]) < 2.5            # m; team itself has drifted ~3.7 m since launch


def test_drifting_team_random_walk_is_calibrated():
    """random_walk model vs localization.py's walk at quad rate 0.2 m/s: the 95%
    radius should cover about 95% -- calibrated, not just conservative."""
    r = _drifting_team("random_walk", rate=0.2)
    cover = np.mean(r[:, 0] <= r[:, 1])
    assert 0.90 <= cover <= 1.0, cover
    assert np.median(r[:, 0] / r[:, 1]) > 0.3      # not uselessly wide either
    assert np.median(r[:, 0]) < 1.2                # m


def test_noise_consistency_of_joint_estimate():
    """Joint solve, no drift: NEES of b's transform (via 4 shared tags) ~ chi^2_3."""
    nees = []
    for seed in range(150):
        rng = np.random.default_rng(500 + seed)
        Ta, Tb = FrameAlignment(), _rand_alignment(rng, 30.0)
        est = TeamFrameEstimator("a", drift=DriftModel(rate_mps=0.0), clock=_Clock(0.0))
        for k, tag in enumerate([(10.0, 0.0), (0.0, 15.0), (-12.0, -4.0), (20.0, 20.0)]):
            for v, T in (("a", Ta), ("b", Tb)):
                p, _ = _local_of(T, (tag[0], tag[1], 0.0))
                p = p + rng.normal(0, 0.25, 3)
                est.add_fiducial(FiducialSighting(v, f"t{k}", (p[0], p[1], p[2], None), sigma_m=0.25,
                                                  sigma_z_m=0.25, stamp=0.0))
        al = est.alignment("b", now=0.0)
        e = _param_err(al, Tb)[:3]
        nees.append(float(e @ np.linalg.solve(al.cov[:3, :3], e)))
    assert 2.2 < np.mean(nees) < 3.6, np.mean(nees)


def test_gauge_switch_from_anchor_to_briefing_reinitialises():
    Ts = _truth_team()
    est = TeamFrameEstimator("a", drift=DriftModel(rate_mps=0.0), clock=_Clock(0.0))
    for k, tag in enumerate([(5.0, 5.0, 0.0, None), (40.0, -10.0, 0.0, None)]):
        _sight(est, Ts["a"], "a", f"t{k}", tag, 1.0)
        _sight(est, Ts["b"], "b", f"t{k}", tag, 1.0)
    est.update()
    est.layout = LaunchLayout(poses={"a": (0.0, 0.0, 0.0, 0.10)})
    _launch(est, Ts["a"], "a", (0.0, 0.0, 0.0, 0.10))
    _check_alignment(est, "b", Ts["b"])


def test_factor_window_prunes_oldest_observations_not_launch():
    Ts = _truth_team()
    est = TeamFrameEstimator("a", layout=LaunchLayout(poses={"a": (0.0, 0.0, 0.0, 0.10)}),
                             clock=_Clock(0.0), max_factors=10)
    _launch(est, Ts["a"], "a", (0.0, 0.0, 0.0, 0.10))
    for k in range(30):
        _sight(est, Ts["a"], "a", f"t{k % 3}", (k, 0.0, 0.0, None), stamp=float(k))
    kinds = [f.kind for f in est._factors]
    assert kinds.count("launch") == 1 and kinds.count("fiducial") == 10
    assert min(f.stamp for f in est._factors if f.kind == "fiducial") == 20.0


# =========================================================================== sources
def test_fiducial_from_relative_body_offset():
    s = FiducialSighting.from_relative("a", "t1", (10.0, 5.0, 2.0, math.pi / 2), (3.0, 1.0, -2.0),
                                       rel_yaw_rad=0.5)
    # facing +y: forward 3 -> +y, left 1 -> -x
    assert np.allclose(s.pose_local[:3], (9.0, 8.0, 0.0))
    assert math.isclose(s.pose_local[3], math.pi / 2 + 0.5)


def test_relative_from_range_bearing_and_correspondence():
    o = RelativeObservation.from_range_bearing(
        "a", "c", (0.0, 0.0, 10.0, math.pi / 2), range_m=10.0, bearing_rad=-math.pi / 2,
        target_reported=(1.0, 2.0, 3.0), elevation_rad=math.asin(0.6),
        sigma_range_m=0.1, sigma_bearing_rad=0.01, sigma_target_m=0.0,
        target_speed_mps=2.0, time_skew_s=0.1)
    assert np.allclose(o.target_in_observer, (8.0, 0.0, 16.0), atol=1e-12)
    # tangential 8 m * 0.01 rad beats radial 0.08 m; plus 0.2 m latency
    assert math.isclose(o.sigma_m, math.hypot(0.08, 0.2))
    Ta = FrameAlignment(theta=0.0, tx=100.0, ty=0.0, tz=1.0, cov=np.diag([0.09, 0.09, 0.0, 0.0]))
    c = o.correspondence(Ta)
    assert np.allclose(c.dst, (108.0, 0.0, 17.0)) and c.src == (1.0, 2.0, 3.0)
    assert math.isclose(c.sigma_m, math.hypot(o.sigma_m, 0.3))


def test_launch_layout_briefing_and_colocated():
    lay = LaunchLayout.from_briefing({"sigma_m": 0.4, "sigma_yaw_deg": 3,
                                      "vehicles": {"q1": {"x": 1, "y": 2, "yaw_deg": 90},
                                                   "r1": {"x": -1, "y": 0, "z": 0.2}}})
    assert lay.poses["q1"] == (1.0, 2.0, 0.0, math.pi / 2) and math.isnan(lay.poses["r1"][3])
    assert math.isclose(lay.sigma_yaw_rad, math.radians(3))
    c = lay.correspondence(LaunchReport("q1", (0.0, 0.0, 0.0, 0.2)))
    assert c.has_yaw and c.kind == "launch"
    assert not lay.correspondence(LaunchReport("r1", (0.0, 0.0, 0.0, 0.2))).has_yaw
    assert lay.correspondence(LaunchReport("nobody")) is None
    co = LaunchLayout.colocated(["a", "b"], pose=(3.0, 4.0, 0.0, 1.0))
    assert co.poses["a"] == co.poses["b"] == (3.0, 4.0, 0.0, 1.0)
    # pre-flight co-location alone aligns two vehicles that both launched there
    est = TeamFrameEstimator("a", layout=co, clock=_Clock(0.0))
    assert est.add_launch(LaunchReport("a", (0.0, 0.0, 0.0, 0.0)))
    assert est.add_launch(LaunchReport("b", (0.0, 0.0, 0.0, -0.5)))
    assert not est.add_launch(LaunchReport("z"))
    assert np.allclose(est.to_mission("b", (1.0, 0.0)), (3.0 + math.cos(1.5), 4.0 + math.sin(1.5)))


# =========================================================================== wire format
LONG = "x" * MAX_ID_LEN


def _roundtrip(obj, now=100.0, recv=500.0):
    msg = encode_message(obj, now=now)
    # through JSON, plus what team_link adds on send and receive
    wire = json.loads(json.dumps(dict(msg, **{"from": obj.__dict__.get("observer_id", "s"),
                                              "t": 1.0, "_addr": "192.0.2.7"})))
    return msg, decode_message(wire, recv_time=recv)


def test_serialisation_roundtrip_all_types():
    lr = LaunchReport("drone-cb34d96e3128", (0.0, 0.0, 0.0, -2.71828), stamp=90.0)
    fs = FiducialSighting("drone-cb34d96e3128", "tag-0017", (123.4567, -98.7654, 1.2345, 0.12345),
                          sigma_m=0.25, sigma_yaw_rad=0.05, sigma_z_m=0.4, stamp=99.5)
    ro = RelativeObservation("drone-cb34d96e3128", "rover-7", (1001.2345, -2002.3456, 5.5),
                             (10.0, -20.0, None), sigma_m=0.375, stamp=97.25)
    for obj in (lr, fs, ro):
        msg, back = _roundtrip(obj)
        assert type(back) is type(obj)
        assert math.isclose(back.stamp, 500.0 - (100.0 - obj.stamp), abs_tol=0.006)
        for k, v in obj.__dict__.items():
            if k == "stamp":
                continue
            w = back.__dict__[k]
            if isinstance(v, tuple):
                assert len(w) == len(v) or len(w) == 4 and len(v) == 4
                for x, y in zip(v, w):
                    assert (x is None and y is None) or abs(x - y) <= 6e-4, (k, v, w)
            elif isinstance(v, float):
                assert abs(v - w) <= 6e-4, (k, v, w)
            else:
                assert v == w, (k, v, w)


def test_payloads_stay_under_300_bytes_with_longest_ids():
    objs = [
        LaunchReport(LONG, (-12345.678, 12345.678, -999.999, -3.14159), 0.0),
        FiducialSighting(LONG, LONG, (-12345.678, 12345.678, -999.999, -3.14159),
                         sigma_m=123.456, sigma_yaw_rad=3.14159, sigma_z_m=123.456, stamp=0.0),
        RelativeObservation(LONG, LONG, (-12345.678, 12345.678, -999.999), (-12345.678, 12345.678, -999.999),
                            sigma_m=123.456, sigma_z_m=123.456, stamp=0.0),
    ]
    for obj in objs:
        msg = encode_message(obj, now=99999.99)
        assert wire_size(msg) <= MAX_PAYLOAD_BYTES, (msg["type"], wire_size(msg))
        # wire_size is what team_link really sends: json.dumps with "from" and "t" added
        sent = dict(msg, **{"from": LONG, "t": 123456.123456789})
        assert len(json.dumps(sent).encode()) <= MAX_PAYLOAD_BYTES
    with pytest.raises(ValueError):
        encode_message(LaunchReport("y" * (MAX_ID_LEN + 1)), now=0.0)


def test_decode_ignores_other_traffic_and_rejects_malformed():
    assert decode_message({"type": "POSITION", "pos_enu": [0, 0, 0]}, 0.0) is None
    assert decode_message({}, 0.0) is None
    good = encode_message(FiducialSighting("a", "t", (1.0, 2.0, None, None)), now=0.0)
    for bad in (dict(good, v=2), dict(good, p=[1.0, None, 0.0, 0.0]), dict(good, p=[1.0, 2.0]),
                dict(good, age=-1.0), dict(good, age="soon"), dict(good, s=[0.0, 0.1, None]),
                dict(good, L=""), dict(good, p=[1.0, float("nan"), 0.0, 0.0]), dict(good, p=[True, 2, 0, 0])):
        with pytest.raises(ValueError):
            decode_message(bad, 0.0)
    est = TeamFrameEstimator("a", clock=_Clock(0.0))
    assert est.ingest(dict(good, v=2), 0.0) is False
    assert est.ingest({"type": "HEARTBEAT"}, 0.0) is False


def test_ingest_matches_direct_add():
    Ts = _truth_team()
    lay = LaunchLayout(poses={"a": (0.0, 0.0, 0.0, 0.10)})
    direct = TeamFrameEstimator("a", layout=lay, drift=DriftModel(rate_mps=0.0), clock=_Clock(0.0))
    wired = TeamFrameEstimator("a", layout=lay, drift=DriftModel(rate_mps=0.0), clock=_Clock(0.0))
    sent = []

    class _Tap:
        def add_launch(self, r):
            sent.append(r)
            return direct.add_launch(r)

        def add_fiducial(self, s):
            sent.append(s)
            direct.add_fiducial(s)

        def add_relative(self, o):
            sent.append(o)
            direct.add_relative(o)

    tap = _Tap()
    _launch(tap, Ts["a"], "a", (0.0, 0.0, 0.0, 0.10))
    for k, tag in enumerate([(5.0, 5.0, 0.0, 0.3), (40.0, -10.0, 0.0, None)]):
        _sight(tap, Ts["a"], "a", f"t{k}", tag, stamp=10.0)
        _sight(tap, Ts["b"], "b", f"t{k}", tag, stamp=10.0)
    _relative(tap, Ts["a"], Ts["c"], "a", "c", (0.0, 10.0, 3.0), stamp=10.0)
    _relative(tap, Ts["a"], Ts["c"], "a", "c", (10.0, 20.0, 3.0), stamp=10.0)
    for obj in sent:
        assert wired.ingest(json.loads(json.dumps(encode_message(obj, now=10.0))), recv_time=10.0)
    assert wired.ingest({"type": MSG_FIDUCIAL, "v": 1, "o": "b"}, 10.0) is False   # malformed: dropped
    for v in "abc":
        a, b = direct.alignment(v, now=10.0), wired.alignment(v, now=10.0)
        assert np.allclose(a.params, b.params, atol=5e-3), v     # mm-rounded on the wire
