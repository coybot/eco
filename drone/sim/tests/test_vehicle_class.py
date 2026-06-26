"""VehicleClass registry + capability-query tests (Phase 0 heterogeneity anchor)."""
from __future__ import annotations

import pytest

import vehicle_class as vc


def test_shipped_classes_match_contracts():
    q = vc.get_class("quadcopter")
    r = vc.get_class("rover")
    # dims must match the on-device contracts (contract.py / rover_contract.py)
    assert (q.state_dim, q.action_dim) == (56, 4)
    assert (r.state_dim, r.action_dim) == (83, 2)
    assert q.kinematics is vc.Kinematics.HOLONOMIC_3D
    assert r.kinematics is vc.Kinematics.UNICYCLE_2D
    assert q.sensor is vc.Sensor.FORWARD_DEPTH
    assert r.sensor is vc.Sensor.LIDAR_360


def test_aliases_resolve_to_same_object():
    assert vc.get_class("quad") is vc.get_class("quadcopter")
    assert vc.get_class("ROVER") is vc.get_class("rover")


def test_capability_queries():
    q, r = vc.get_class("quad"), vc.get_class("rover")
    assert q.is_aerial and not r.is_aerial
    assert r.planar and not q.planar
    assert q.preferred_role is vc.Role.AERIAL_SCOUT
    assert r.preferred_role is vc.Role.GROUND_INSPECT
    # only the aerial class can relay
    assert q.can_fill(vc.Role.COMMS_RELAY)
    assert not r.can_fill(vc.Role.COMMS_RELAY)


def test_unknown_class_rejected():
    with pytest.raises(KeyError):
        vc.get_class("submarine")


def test_register_new_class_is_data_only():
    """Adding a class = a descriptor + aliases, no code changes elsewhere."""
    fw = vc.VehicleClass(
        name="fixedwing", kinematics=vc.Kinematics.HOLONOMIC_3D,
        sensor=vc.Sensor.FORWARD_DEPTH, state_dim=56, action_dim=4,
        policy_onnx="policy_fw.onnx", radius_m=1.0, max_speed_mps=25.0,
        max_accel_mps2=5.0, max_yaw_rate_radps=0.6, ceiling_m=120.0,
        payload_kg=2.0, roles=(vc.Role.AERIAL_SCOUT,),
    )
    vc.register_class(fw, "fw", "plane")
    try:
        assert vc.get_class("plane") is fw
        assert "fixedwing" in vc.known_classes()
    finally:
        # keep the registry clean for other tests
        for k in ("fixedwing", "fw", "plane"):
            vc._REGISTRY.pop(k, None)
