"""Register the two new vehicle classes with the existing VehicleClass registry.

The existing classes in vehicle_class.py are not modified. Importing this module adds:
  small_quadcopter -- holonomic 3-D, 0.15 m radius (0.30 m across with guards), the
                      delivery quad of the docking scenario; dynamics in vehicles.yaml.
  quadplane        -- coordinated-turn 3-D, Pioneer 3200-class mothership; never below its
                      minimum safe cruise speed in this scenario (min_speed_mps).
so team layers and TeamWorld can place them; flight.dynamic_world.DynamicWorld flies them
on their rigid-body models.
"""
from __future__ import annotations

import os

import yaml

from vehicle_class import (CommsProfile, Kinematics, LocalizationProfile, Role, Sensor,
                           VehicleClass, known_classes, register_class)

_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vehicles.yaml")


def register(cfg_path: str = _CFG) -> None:
    if "small_quadcopter" in known_classes():
        return
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    q = cfg["small_quadcopter"]
    qp = cfg["quadplane"]
    register_class(VehicleClass(
        name="small_quadcopter", kinematics=Kinematics.HOLONOMIC_3D, sensor=Sensor.FORWARD_DEPTH,
        state_dim=56, action_dim=4, policy_onnx="", radius_m=q["body_radius_m"],
        max_speed_mps=q.get("max_speed_m_s", 25.0), max_accel_mps2=8.0, max_yaw_rate_radps=2.0,
        ceiling_m=120.0, payload_kg=q["mass_loaded_kg"] - q["mass_empty_kg"],
        roles=(Role.AERIAL_SCOUT,), comms=CommsProfile(range_m=300.0, los_required=False),
        localization=LocalizationProfile(), collision_pad_m=0.02))
    register_class(VehicleClass(
        name="quadplane", kinematics=Kinematics.COORDINATED_TURN_3D, sensor=Sensor.FORWARD_DEPTH,
        state_dim=56, action_dim=4, policy_onnx="", radius_m=qp["geometry"]["wing"]["span_m"] / 2,
        max_speed_mps=qp["max_airspeed_m_s"], max_accel_mps2=3.0, max_yaw_rate_radps=0.4,
        ceiling_m=120.0, payload_kg=qp["max_payload_kg"], roles=(Role.AERIAL_SCOUT, Role.COMMS_RELAY),
        comms=CommsProfile(range_m=2000.0, los_required=False), localization=LocalizationProfile(),
        min_speed_mps=qp["min_safe_cruise_airspeed_m_s"], max_climb_angle_rad=0.2,
        wingborne_speed_mps=qp["min_safe_cruise_airspeed_m_s"], sense_range_m=80.0))
