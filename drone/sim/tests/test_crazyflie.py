"""Crazyflie vehicle class, hybrid sensing model, and the scale-generalisation regression wall.

Three concerns, deliberately in one file because they share fixtures:

1. The class descriptor and the ray-table registry that makes a third sensing modality safe.
2. The hybrid 50-ray sensing model (camera fan + ToF beams) and its impairments.
3. **The regression wall**: proof that turning hardcoded metre-scale literals into per-class
   properties did not move the shipped outdoor classes by even a rounding step. That property
   is what makes the whole refactor safe, so it is tested first and most precisely.
"""
import hashlib
import math
from pathlib import Path

import numpy as np
import pytest

import vehicle_class as vc
from team_world import (
    KinematicWorld, TeamWorld, Agent, Box, reconstruct_surface_clearance,
)
from sensor_model import (
    ScanImpairment, ScanImpairmentModel, GroupImpairment, CRAZYFLIE_DECKS, get_impairment,
)


# =========================================================== regression wall (do not weaken)
def test_indoor_scale_reproduces_shipped_defaults_exactly():
    """indoor_scale(3.5) must equal the dataclass defaults with zero tolerance.

    Not approximately: the outdoor classes were tuned against these literals and the
    controller compares them with strict `<`, so a one-ULP drift can flip a branch and
    silently move the published suite result.
    """
    d = vc.indoor_scale(3.5)
    q = vc.QUADCOPTER
    for k, v in d.items():
        assert getattr(q, k) == v, f"{k}: default {getattr(q, k)!r} != indoor_scale {v!r}"


@pytest.mark.parametrize("name", ["quadcopter", "rover", "fixedwing"])
def test_shipped_classes_keep_historical_length_scales(name):
    """The literals these per-class fields replaced, pinned by value."""
    c = vc.get_class(name)
    assert c.interaction_scale_m == 3.5
    assert c.avoid_radius_m == 3.5          # was reactive_goto_controller(avoid_radius=3.5)
    assert c.slow_radius_m == 2.0           # was slow_radius=2.0
    assert c.neighbor_safe_pad_m == 0.8     # was `safe = r + r + 0.8`
    assert c.clearance_slow_pad_m == 1.0    # was `min_clearance < radius_m + 1.0`
    assert c.climb_over_clearance_m == 1.5  # was `min_clearance < 1.5`
    assert c.proximity_m == 3.5             # was TeamRuntime convoy/yield constants
    assert c.neighbor_range_m == 12.0       # was TeamWorld.NEIGHBOR_RANGE
    assert c.collision_pad_m == 0.05        # was TeamWorld.COLLISION_PAD


def test_snapshot_caps_reproduces_smart_layer_literals():
    """The smart-layer literals replaced by AgentSnapshot capability fields."""
    from smart_layer import snapshot_caps
    caps = snapshot_caps(vc.QUADCOPTER)
    assert caps["scan_clear_m"] == 4.0        # was `min_scan_dist > 4.0`
    assert caps["sep_m"] == 3.0               # was `sep < 3.0`
    assert caps["low_alt_m"] == 2.5           # was `pos[2] < 2.5`
    assert caps["goal_lock_m"] == 1.5         # was `cur_dist < 1.5`
    assert caps["recovery_lateral_m"] == 3.0
    assert caps["recovery_climb_m"] == 4.0
    assert caps["rtl_dist_m"] == 20.0         # was `dist > 20.0`
    assert caps["is_aerial"] is True


def test_team_runtime_proximity_reproduces_literals():
    from team_runtime import TeamRuntime
    q = vc.QUADCOPTER
    assert TeamRuntime._prox(q, 1.5) == 1.5   # convoy close
    assert TeamRuntime._prox(q, 3.0) == 3.0   # convoy clear
    assert TeamRuntime._prox(q, 1.2) == 1.2   # emergency brake
    assert TeamRuntime._prox(q, 2.5) == 2.5   # yield close
    assert TeamRuntime._prox(q, 0.8) == 0.8   # convoy lateral


def test_scorecard_default_thresholds_match_module_literals():
    import scorecard as sc
    t = sc.DEFAULT_THRESHOLDS
    assert (t.near_miss_pad_m, t.stall_window_s, t.stall_eps_m,
            t.lost_err_m, t.lost_window_s) == (
        sc.NEAR_MISS_PAD, sc.STALL_WINDOW_S, sc.STALL_EPS, sc.LOST_ERR_M, sc.LOST_WINDOW_S)


def test_ceiling_clamp_is_a_noop_for_outdoor_classes():
    """The new ceiling clamp must not touch classes that never approach their ceiling.

    The outdoor suite peaks around 6.5 m for a 30 m-ceiling quad, so this clamp is inert
    there — but it is load-bearing for a 2.2 m-ceiling micro-UAV.
    """
    kw = KinematicWorld()
    for name, z0 in (("quadcopter", 4.0), ("fixedwing", 60.0)):
        c = vc.get_class(name)
        a = Agent(id="a", vclass=c, pos=np.array([0., 0., z0], np.float32), yaw=0.0)
        for _ in range(50):
            kw.integrate(a, np.array([c.max_speed_mps, 0, 1.0, 0], np.float32), 0.1)
        assert a.pos[2] < c.ceiling_m - 5.0, f"{name} got within 5 m of its ceiling"


def test_vehicle_class_copies_are_byte_identical():
    """common/vehicle_class.py and sim/vehicle_class.py are maintained as twins.

    They are duplicated on purpose (the device install copies common/*.py flat, and the sim
    tests import it bare), so the only protection against silent divergence is this hash.
    """
    root = Path(__file__).resolve().parents[2]
    a = (root / "common" / "vehicle_class.py").read_bytes()
    b = (root / "sim" / "vehicle_class.py").read_bytes()
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest(), (
        "vehicle_class.py copies have diverged — copy common/ over sim/")


# =========================================================== ray tables
def test_sense_range_exceeds_effective_avoid_radius_for_every_class():
    """The invariant sense_range_m's own comment asks for, checked across the registry.

    If a class can be repelled from further away than it can see, a genuinely clear reading
    at max range looks like a phantom obstacle to the reactive controller. Fixed-wing needs
    the turn-radius override folded in, which is why this is not just a field comparison.
    """
    for name in vc.known_classes():
        c = vc.get_class(name)
        eff = c.avoid_radius_m
        if c.kinematics is vc.Kinematics.COORDINATED_TURN_3D:
            eff = max(eff, (c.max_speed_mps / max(c.max_yaw_rate_radps, 1e-3)) * 1.5)
        assert c.sense_range_m > eff, (
            f"{name}: sense_range {c.sense_range_m} <= effective avoid radius {eff}")


def test_ray_table_layouts():
    assert vc.ray_table(vc.Sensor.FORWARD_DEPTH).n_rays == 45
    assert vc.ray_table(vc.Sensor.LIDAR_360).n_rays == 72
    assert vc.ray_table(vc.Sensor.MONO_DEPTH_PLUS_TOF).n_rays == 50
    for s in vc.Sensor:
        rt = vc.ray_table(s)
        assert len(rt.dirs) == rt.n_rays
        assert len(rt.yaw_angles) == rt.n_rays
        assert rt.fov_mask is None or len(rt.fov_mask) == rt.n_rays
        covered = [i for _n, a, b in rt.groups for i in range(a, b)]
        assert covered == list(range(rt.n_rays)), f"{s}: groups do not tile the scan"


def test_hybrid_camera_block_is_index_identical_to_forward_depth():
    """So run_prompt.depth_grid_5x9 drops into the hardware path unmodified."""
    assert (vc.ray_table(vc.Sensor.MONO_DEPTH_PLUS_TOF).dirs[:45]
            == vc.ray_table(vc.Sensor.FORWARD_DEPTH).dirs)


def test_scale_anchor_redundancy():
    """Ray 22 (grid centre) and ray 45 (front ToF) must point the same way.

    This is not incidental: it is the only way to estimate the monocular-depth scale factor
    online against a metric measurement. If a future edit reorders the rays and breaks this,
    the hardware loses its scale reference.
    """
    rt = vc.ray_table(vc.Sensor.MONO_DEPTH_PLUS_TOF)
    assert rt.dirs[45] == (1.0, 0.0, 0.0)
    assert rt.dirs[49] == (0.0, 0.0, 1.0)        # up-facing ToF
    assert all(abs(rt.dirs[22][i] - rt.dirs[45][i]) < 1e-12 for i in range(3))


def test_camera_fov_mask_is_21_of_45():
    """The real AI-deck lens is narrower than the 90x70 contract grid.

    21 is also the count a 87x65 lens gives, so this number does not depend on pinning the
    exact FOV — see the CF_CAM_* comments.
    """
    rt = vc.ray_table(vc.Sensor.MONO_DEPTH_PLUS_TOF)
    assert sum(rt.fov_mask[:45]) == 21
    assert all(rt.fov_mask[45:])          # ToF beams are always live


def test_lidar_has_no_vertical_cue():
    """A horizontal ring must not drive vertical repulsion; a depth grid must."""
    assert vc.ray_table(vc.Sensor.LIDAR_360).vertical_cue is False
    assert vc.ray_table(vc.Sensor.FORWARD_DEPTH).vertical_cue is True
    assert vc.ray_table(vc.Sensor.MONO_DEPTH_PLUS_TOF).vertical_cue is True


# =========================================================== the class
def test_crazyflie_descriptor():
    c = vc.get_class("crazyflie")
    assert c is vc.get_class("cf")
    assert c.kinematics is vc.Kinematics.HOLONOMIC_3D
    assert c.state_dim == 11 + vc.ray_table(c.sensor).n_rays
    assert c.min_speed_mps == 0.0            # hovers
    assert c.is_aerial and not c.planar
    assert c.ceiling_m < 3.0                 # fits under a domestic ceiling
    assert c.payload_kg == 0.0
    assert c.localization.gps_available is False
    assert c.policy_onnx == ""               # classical controller only


def test_crazyflie_has_enough_control_ticks_inside_its_avoid_radius():
    """The stated justification for max_speed_mps=1.0. If someone raises the speed without
    widening the avoid radius, the vehicle gets too few 10 Hz ticks to react."""
    c = vc.get_class("crazyflie")
    ticks = c.avoid_radius_m / (c.max_speed_mps * 0.1)
    assert ticks >= 8.0, f"only {ticks:.1f} control ticks inside the avoid radius"


# =========================================================== sensing in the world
def _scan_of(cls_name, boxes, pos=(0., 0., 1.), yaw=0.0, **world_kw):
    w = TeamWorld(KinematicWorld(list(boxes)), **world_kw)
    w.add_agent("a", cls_name, pos, yaw=yaw)
    return w._scan(w.agents["a"], w.backend.obstacles())


def test_tof_sees_behind_where_the_camera_cannot():
    """The capability a forward-depth quad does not have, and the reason the scan is hybrid."""
    scan, _ = _scan_of("crazyflie", [Box(np.array([-1.5, 0., 1.], np.float32),
                                         np.array([0.2, 0.2, 0.5], np.float32))])
    assert scan[:45].min() == pytest.approx(4.0)    # camera blind behind
    assert scan[46] < 2.0                            # back ToF beam sees it
    assert scan[45] == pytest.approx(4.0)            # front beam clear


def test_tof_sees_above():
    scan, _ = _scan_of("crazyflie", [Box(np.array([0., 0., 2.0], np.float32),
                                         np.array([1.0, 1.0, 0.1], np.float32))])
    assert scan[49] < 1.5                            # up beam sees the ceiling


def test_truncates_at_4m_where_a_quad_still_sees():
    box = [Box(np.array([6., 0., 1.], np.float32), np.array([0.3, 0.3, 0.5], np.float32))]
    cf, _ = _scan_of("crazyflie", box)
    q, _ = _scan_of("quadcopter", box)
    assert cf.min() == pytest.approx(4.0)            # beyond VL53L1x range: reads clear
    assert q.min() < 10.0                            # quad sees it at ~5.7 m


def test_out_of_lens_rays_report_clear():
    """An obstacle inside the 90-degree contract grid but outside the real lens must not be
    reported. Optimistic about coverage, never optimistic about an obstacle it did see."""
    rt = vc.ray_table(vc.Sensor.MONO_DEPTH_PLUS_TOF)
    outside = [i for i in range(45) if not rt.fov_mask[i]]
    # place a wide obstacle 40 degrees off-axis: inside the grid's +-45, outside the lens' +-35
    ang = math.radians(40.0)
    box = [Box(np.array([2.0 * math.cos(ang), 2.0 * math.sin(ang), 1.0], np.float32),
               np.array([0.3, 0.3, 0.5], np.float32))]
    scan, _ = _scan_of("crazyflie", box)
    assert all(scan[i] == pytest.approx(4.0) for i in outside)


def test_ceiling_is_enforced_for_the_crazyflie():
    kw = KinematicWorld()
    c = vc.get_class("crazyflie")
    a = Agent(id="cf", vclass=c, pos=np.array([0., 0., 2.0], np.float32), yaw=0.0)
    for _ in range(40):
        kw.integrate(a, np.array([0, 0, 5.0, 0], np.float32), 0.1)
    assert a.pos[2] == pytest.approx(c.ceiling_m)


def test_reconstruct_clearance_does_not_join_sparse_tof_beams():
    """Beams 90 degrees apart must not be joined into a fabricated surface.

    With only the back beam returning, the reconstruction must equal that raw return — if it
    tried to fit a surface between the back beam and its non-adjacent neighbours it would
    invent an obstacle spanning directions nothing was measured between.
    """
    scan = np.full(50, 4.0, dtype=np.float32)
    scan[46] = 1.3                                    # back ToF only
    got = reconstruct_surface_clearance(scan, vc.Sensor.MONO_DEPTH_PLUS_TOF, 4.0)
    assert got == pytest.approx(1.3)


# =========================================================== impairment model
def _cf():
    return vc.get_class("crazyflie")


def test_scale_bias_is_one_common_factor_on_the_camera_only():
    """Monocular depth is scale-ambiguous: the error is a per-frame common factor, not
    per-ray noise, and laser ToF has no such ambiguity to model."""
    m = ScanImpairmentModel(
        ScanImpairment(groups={"camera": GroupImpairment(scale_bias_sigma=0.25),
                               "tof": GroupImpairment()}), dt=0.1, seed=11)
    out = m.apply("cf", np.full(50, 2.0, np.float32), _cf(), 8.0)
    ratios = out[:45] / 2.0
    assert np.allclose(ratios, ratios[0])              # single factor
    assert not np.isclose(ratios[0], 1.0)              # actually applied
    assert np.allclose(out[45:], 2.0)                  # ToF untouched


def test_frame_dropout_blinds_the_whole_camera_block_at_once():
    """Correlated blindness (blank wall / dropped JPEG), distinct from per-ray dropout."""
    m = ScanImpairmentModel(
        ScanImpairment(groups={"camera": GroupImpairment(frame_dropout_p=1.0),
                               "tof": GroupImpairment()}), dt=0.1, seed=2)
    out = m.apply("cf", np.full(50, 2.0, np.float32), _cf(), 4.0)
    assert np.all(out[:45] == 4.0)
    assert np.all(out[45:] == 2.0)                     # ToF carries on


def test_latency_delays_the_camera_block_only():
    m = ScanImpairmentModel(
        ScanImpairment(groups={"camera": GroupImpairment(latency_s=0.2),
                               "tof": GroupImpairment()}), dt=0.1, seed=5)
    seen = []
    for v in (5.0, 4.0, 3.0, 2.0):
        o = m.apply("cf", np.full(50, v, np.float32), _cf(), 10.0)
        seen.append((float(o[0]), float(o[45])))
    assert seen[0] == (5.0, 5.0)
    assert seen[1] == (5.0, 4.0)     # camera still showing tick 0
    assert seen[3] == (4.0, 2.0)     # camera 2 ticks behind, ToF current


def test_impairment_is_seeded_and_per_agent():
    a = ScanImpairmentModel(CRAZYFLIE_DECKS, 0.1, 42).apply(
        "cf", np.full(50, 2.0, np.float32), _cf(), 4.0)
    b = ScanImpairmentModel(CRAZYFLIE_DECKS, 0.1, 42).apply(
        "cf", np.full(50, 2.0, np.float32), _cf(), 4.0)
    assert np.array_equal(a, b)

    m = ScanImpairmentModel(
        ScanImpairment(groups={"camera": GroupImpairment(scale_bias_sigma=0.2)}), 0.1, 9)
    factors = {aid: float(m.apply(aid, np.full(50, 2.0, np.float32), _cf(), 8.0)[0])
               for aid in ("cf0", "cf1", "cf2")}
    assert len(set(factors.values())) == 3             # independent per vehicle
    # and stable within an episode for a given vehicle
    assert float(m.apply("cf0", np.full(50, 2.0, np.float32), _cf(), 8.0)[0]) == factors["cf0"]


def test_get_impairment_rejects_unknown_preset():
    with pytest.raises(KeyError):
        get_impairment("no_such_deck")


def test_no_impairment_leaves_the_scan_untouched():
    box = [Box(np.array([1.5, 0., 1.], np.float32), np.array([0.2, 0.2, 0.5], np.float32))]
    clean, _ = _scan_of("crazyflie", box, sensing="realistic", impairment=None)
    assert clean[22] == pytest.approx(1.3, abs=0.05)
