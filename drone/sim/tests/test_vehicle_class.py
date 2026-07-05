"""VehicleClass registry + capability-query tests (Phase 0 heterogeneity anchor)."""
from __future__ import annotations

import pytest

import vehicle_class as vc


def test_shipped_classes_match_contracts():
    q = vc.get_class("quadcopter")
    r = vc.get_class("rover")
    fw = vc.get_class("fixedwing")
    # dims must match the on-device contracts (contract.py / rover_contract.py / fw_contract.py)
    assert (q.state_dim, q.action_dim) == (56, 4)
    assert (r.state_dim, r.action_dim) == (83, 2)
    assert (fw.state_dim, fw.action_dim) == (56, 4)
    assert q.kinematics is vc.Kinematics.HOLONOMIC_3D
    assert r.kinematics is vc.Kinematics.UNICYCLE_2D
    assert fw.kinematics is vc.Kinematics.COORDINATED_TURN_3D
    assert q.sensor is vc.Sensor.FORWARD_DEPTH
    assert r.sensor is vc.Sensor.LIDAR_360
    assert fw.sensor is vc.Sensor.FORWARD_DEPTH
    # fixed-wing can't hover: stall margin + bounded climb angle, unlike quad/rover
    assert fw.min_speed_mps > 0.0
    assert 0.0 < fw.max_climb_angle_rad < (3.14159 / 2)
    assert q.min_speed_mps == 0.0 and r.min_speed_mps == 0.0


def test_aliases_resolve_to_same_object():
    assert vc.get_class("quad") is vc.get_class("quadcopter")
    assert vc.get_class("ROVER") is vc.get_class("rover")
    assert vc.get_class("fw") is vc.get_class("fixedwing")
    assert vc.get_class("PLANE") is vc.get_class("fixedwing")


def test_capability_queries():
    q, r, fw = vc.get_class("quad"), vc.get_class("rover"), vc.get_class("fixedwing")
    assert q.is_aerial and not r.is_aerial and fw.is_aerial
    assert r.planar and not q.planar and not fw.planar
    assert q.preferred_role is vc.Role.AERIAL_SCOUT
    assert r.preferred_role is vc.Role.GROUND_INSPECT
    assert fw.preferred_role is vc.Role.AERIAL_SCOUT
    # only the aerial classes can relay comms / scout
    assert q.can_fill(vc.Role.COMMS_RELAY)
    assert not r.can_fill(vc.Role.COMMS_RELAY)
    assert not fw.can_fill(vc.Role.COMMS_RELAY)
    assert fw.can_fill(vc.Role.AERIAL_SCOUT)


def test_unknown_class_rejected():
    with pytest.raises(KeyError):
        vc.get_class("submarine")


def test_register_new_class_is_data_only():
    """Adding a class = a descriptor + aliases, no code changes elsewhere."""
    vtol = vc.VehicleClass(
        name="vtol", kinematics=vc.Kinematics.HOLONOMIC_3D,
        sensor=vc.Sensor.FORWARD_DEPTH, state_dim=56, action_dim=4,
        policy_onnx="policy_vtol.onnx", radius_m=1.0, max_speed_mps=20.0,
        max_accel_mps2=5.0, max_yaw_rate_radps=1.0, ceiling_m=100.0,
        payload_kg=3.0, roles=(vc.Role.AERIAL_SCOUT,),
    )
    vc.register_class(vtol, "vtol-demo")
    try:
        assert vc.get_class("vtol-demo") is vtol
        assert "vtol" in vc.known_classes()
    finally:
        # keep the registry clean for other tests
        for k in ("vtol", "vtol-demo"):
            vc._REGISTRY.pop(k, None)
