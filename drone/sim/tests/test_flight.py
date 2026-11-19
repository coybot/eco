"""Fast tests of the rigid-body flight package (flight/). The full Phase 1 checks are in
flight/checks_phase1.py; these are quick versions that run in the normal test suite."""
import math

import numpy as np
import pytest
import yaml

from flight.aero import body_drag_body
from flight.batch import check_unit_names, run_batch
from flight.estimation import DelayedKF
from flight.frames import GRAVITY_M_S2, euler_from_rot, quat_from_euler, quat_to_rot
from flight.multirotor import Multirotor, QuadStack, quad_params
from flight.rigid_body import RigidBody
from flight.sensors import SensorChannel
from flight.wind import TurbulenceField
import flight.checks_phase1 as chk


@pytest.fixture(scope="module")
def cfg():
    return chk.load_cfg()


def test_euler_roundtrip_and_conventions():
    R = quat_to_rot(quat_from_euler(0.1, 0.3, 1.2))
    assert np.allclose(euler_from_rot(R), (0.1, 0.3, 1.2))
    assert quat_to_rot(quat_from_euler(0, 0.3, 0))[2, 0] > 0      # pitch up: nose up
    assert quat_to_rot(quat_from_euler(0.3, 0, 0))[2, 1] > 0      # roll right: left wing up


def test_free_fall_is_exact():
    b = RigidBody(2.0, [0.1, 0.1, 0.1], pos_m=[0, 0, 100], vel_m_s=[3, 0, 0])
    E0 = b.kinetic_energy_j() + b.potential_energy_j()
    for _ in range(4000):
        b.step(np.zeros(3), np.zeros(3), 0.0025)
    assert abs(b.kinetic_energy_j() + b.potential_energy_j() - E0) / E0 < 1e-9
    assert b.pos[2] == pytest.approx(100 - 0.5 * GRAVITY_M_S2 * 100, rel=1e-9)


def test_isotropic_drag_is_classic():
    F = body_drag_body(np.array([10.0, 0, 0]), 0.05, 1.225)
    assert F[0] == pytest.approx(-0.5 * 1.225 * 0.05 * 100)


def test_hover_thrust_equals_weight(cfg):
    p = quad_params(cfg["small_quadcopter"])
    st = QuadStack(p, cfg["environment"], np.random.default_rng(0), [0, 0, 20], [0, 0, 0], [1, 0, 0, 0], 1.0)
    st.outer = chk._speed_hold_outer(0.0, 20.0, p, 1.225)
    log = chk._run(st, 12.0, every=lambda s, t: s.phys.last_thrust_total_n)
    assert np.mean(log[-1600:]) == pytest.approx(GRAVITY_M_S2, rel=0.01)


def test_sensor_delay_and_dropout():
    ch = SensorChannel(1, 20.0, 0.1, 0.0, np.random.default_rng(1), dropout_p=0.5)
    got, t = [], 0.0
    for _ in range(4000):
        ch.push(t, [t])
        got += ch.read_new(t)
        t += 0.0025
    assert all(abs(m.t_avail_s - m.t_meas_s - 0.1) < 1e-9 for m in got)
    assert 0.4 < ch.n_dropped / ch.n_taken < 0.6


def test_delayed_kf_tracks_constant_velocity():
    kf = DelayedKF(0.01, 0.5)
    kf.reset(0.0, np.zeros(3), np.zeros(3), 1.0, 1.0)
    for i in range(1, 301):
        kf.predict(np.zeros(3))
        if i % 10 == 0 and i > 10:   # 0.1 s late position fixes of x = 2 t
            tm = (i - 10) * 0.01
            kf.update(tm, pos=np.array([2 * tm, 0, 0]), pos_var=np.full(3, 1e-4))
    assert kf.xv[0] == pytest.approx(2.0, abs=0.05)
    assert kf.xp[0] == pytest.approx(6.0, abs=0.05)


def test_turbulence_sigma_exact_and_coherent():
    tf = TurbulenceField(2.0, 2.0, 1.0, 200.0, 50.0, seed=3, n_modes=300)
    a = tf.velocity(np.array([10.0, 0, 50]))
    b = tf.velocity(np.array([10.5, 0, 50]))
    c = tf.velocity(np.array([400.0, 0, 50]))
    assert np.linalg.norm(a - b) < np.linalg.norm(a - c)         # nearby points agree


def test_unit_suffix_check():
    check_unit_names(["speed_m_s", "tilt_deg", "mass_kg", "outcome_label"])
    with pytest.raises(ValueError):
        check_unit_names(["speed"])


def test_batch_reproducible(tmp_path):
    a = run_batch(chk.repro_trial, [1, 2], workers=1, common={"T_s": 1.0})
    b = run_batch(chk.repro_trial, [1, 2], workers=1, common={"T_s": 1.0})
    assert a == b


def test_dynamic_backend_runs_existing_scenario():
    from scenario import Scenario, ScenarioRunner
    from pathlib import Path
    scn = Scenario.from_yaml(str(Path(__file__).resolve().parent.parent / "scenarios" / "formation_crush.yaml"))
    r = ScenarioRunner(scn, backend="dynamic").run(max_s=10)
    assert r["t_end"] > 0
    from flight.dynamic_world import DynamicWorld
    assert isinstance(ScenarioRunner(scn, backend="dynamic").world.backend, DynamicWorld)
