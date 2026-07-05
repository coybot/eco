"""VehicleClass — capability descriptors for heterogeneous teams.

This is the single source of truth for *what a kind of vehicle is*, read by every
upper layer (team_world, comms, localization, scenario, team_runtime, scorecard).
Those layers must key off these capabilities — **never** branch on a raw ``vtype``
string. Adding a new vehicle class (fixed-wing, VTOL, large-UGV, ...) is then a
matter of adding one descriptor here plus a navigation policy, with no changes to
the coordination/comms/scenario code.

Pure stdlib (dataclasses) so it imports cleanly without torch / Isaac / ROS.

The two shipped classes mirror the existing on-device contracts:
  - quadcopter: holonomic 3D, forward depth grid (contract.py, 56-dim → 4D action),
                policy_v26rnn_dr.onnx, radius 0.15 m, 3.0 m/s.
  - rover:      unicycle 2D, 360° lidar (rover_contract.py, 83-dim → 2D action),
                policy_rover_v2.onnx, radius 0.5 m, 1.5 m/s.
  - fixedwing:  coordinated-turn 3D, long-range forward depth (fw_contract.py, 56-dim
                → 4D action), policy_fw.onnx, radius 1.0 m, 25.0 m/s cruise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Kinematics(str, Enum):
    """How the vehicle moves — picks the integrator and the action contract."""
    HOLONOMIC_3D = "holonomic_3d"   # free 3D translation + yaw (quad): action [vx,vy,vz,yaw_rate]
    UNICYCLE_2D = "unicycle_2d"     # planar, no lateral slip (rover): action [v_linear,yaw_rate]
    COORDINATED_TURN_3D = "coordinated_turn_3d"  # Dubins-airplane (fixed-wing): action
    # [vx,vy,vz,yaw_rate], but vy is ignored, vx is clamped to [min_speed_mps,max_speed_mps]
    # (never zero/reverse), yaw_rate is bank-limited by speed, vz by max_climb_angle_rad.


class Sensor(str, Enum):
    """Primary obstacle-sensing modality — picks how neighbors/obstacles are scanned."""
    FORWARD_DEPTH = "forward_depth"  # 2D depth grid, limited FOV (quad camera)
    LIDAR_360 = "lidar_360"          # full horizontal ring (rover)


class Role(str, Enum):
    """Capability-implied mission roles, used by task allocation in the team runtime."""
    AERIAL_SCOUT = "aerial_scout"        # fast wide-area recon from altitude
    GROUND_INSPECT = "ground_inspect"    # close-up ground inspection / payload
    COMMS_RELAY = "comms_relay"          # bridge isolated agents (altitude vantage)


@dataclass(frozen=True)
class CommsProfile:
    """Per-class radio characteristics, read by comms.CommsFabric.

    range_m is the nominal link range in clear conditions; los_required marks
    classes that need line-of-sight (occlusion nulls the link). Aerial classes
    get longer range and altitude vantage; ground classes are shorter / occluded.
    """
    range_m: float = 150.0
    los_required: bool = True
    bandwidth_msgs_per_s: float = 20.0


@dataclass(frozen=True)
class LocalizationProfile:
    """Per-class localization characteristics, read by localization fabric.

    gps_denied_drift_mps is the random-walk position drift rate when GPS is lost
    and the vehicle falls back to VIO/odometry. Rovers integrate wheel odometry
    and drift slowly; quads rely on VIO and drift faster.
    """
    gps_denied_drift_mps: float = 0.10      # m of accumulated bias per second under denial
    vio_capable: bool = True                # can the class self-localize without GPS at all?


@dataclass(frozen=True)
class VehicleClass:
    """Complete capability descriptor for one kind of vehicle."""
    name: str                       # canonical class key, e.g. "quadcopter"
    kinematics: Kinematics
    sensor: Sensor
    state_dim: int                  # navigation-policy input size (matches its contract)
    action_dim: int                 # navigation-policy output size
    policy_onnx: str                # default policy filename for this class
    radius_m: float                 # collision radius (also half-extent as a moving obstacle)
    max_speed_mps: float
    max_accel_mps2: float
    max_yaw_rate_radps: float
    ceiling_m: float                # operating altitude ceiling (0 = ground-constrained)
    payload_kg: float
    roles: tuple[Role, ...]         # roles this class can fulfil (first = preferred)
    comms: CommsProfile = field(default_factory=CommsProfile)
    localization: LocalizationProfile = field(default_factory=LocalizationProfile)
    min_speed_mps: float = 0.0             # stall/minimum airspeed (0 = can hover/stop)
    max_climb_angle_rad: float = 1.5708    # bounded flight-path angle (~pi/2 = unconstrained)
    sense_range_m: float = 10.0            # obstacle-sensing max range ("clear" sentinel value —
    # must exceed any avoidance radius derived from this class's speed, or a genuinely clear
    # reading looks like a phantom obstacle at max range to the reactive controller)

    # --- capability queries (layers call these, not isinstance / string checks) ---
    @property
    def is_aerial(self) -> bool:
        return self.ceiling_m > 0.0

    @property
    def planar(self) -> bool:
        return self.kinematics is Kinematics.UNICYCLE_2D

    def can_fill(self, role: Role) -> bool:
        return role in self.roles

    @property
    def preferred_role(self) -> Role:
        return self.roles[0]


# --------------------------------------------------------------------------- registry
# Shipped classes. quad/rover mirror the existing contracts & on-device policies.
QUADCOPTER = VehicleClass(
    name="quadcopter",
    kinematics=Kinematics.HOLONOMIC_3D,
    sensor=Sensor.FORWARD_DEPTH,
    state_dim=56,           # 11 + 45 depth rays (contract.py STATE_DIM)
    action_dim=4,           # [vx, vy, vz, yaw_rate]
    policy_onnx="policy_v26rnn_dr.onnx",
    radius_m=0.15,
    max_speed_mps=3.0,
    max_accel_mps2=4.0,
    max_yaw_rate_radps=1.5,
    ceiling_m=30.0,
    payload_kg=0.5,
    roles=(Role.AERIAL_SCOUT, Role.COMMS_RELAY),
    comms=CommsProfile(range_m=250.0, los_required=False, bandwidth_msgs_per_s=30.0),
    localization=LocalizationProfile(gps_denied_drift_mps=0.20, vio_capable=True),
)

ROVER = VehicleClass(
    name="rover",
    kinematics=Kinematics.UNICYCLE_2D,
    sensor=Sensor.LIDAR_360,
    state_dim=83,           # 11 + 72 lidar rays (rover_contract.py R_STATE_DIM)
    action_dim=2,           # [v_linear, yaw_rate]
    policy_onnx="policy_rover_v2.onnx",
    radius_m=0.5,
    max_speed_mps=1.5,
    max_accel_mps2=1.5,
    max_yaw_rate_radps=2.0,
    ceiling_m=0.0,          # ground-constrained
    payload_kg=5.0,
    roles=(Role.GROUND_INSPECT,),
    comms=CommsProfile(range_m=120.0, los_required=True, bandwidth_msgs_per_s=20.0),
    localization=LocalizationProfile(gps_denied_drift_mps=0.05, vio_capable=True),
)

FIXEDWING = VehicleClass(
    name="fixedwing",
    kinematics=Kinematics.COORDINATED_TURN_3D,
    sensor=Sensor.FORWARD_DEPTH,
    state_dim=56,           # 11 + 45 depth rays (fw_contract.py STATE_DIM, longer range)
    action_dim=4,           # [vx, vy(ignored), vz, yaw_rate]
    policy_onnx="policy_fw.onnx",
    radius_m=1.0,
    max_speed_mps=25.0,     # cruise
    max_accel_mps2=5.0,
    max_yaw_rate_radps=0.6, # slow, speed-limited turning (banked)
    ceiling_m=120.0,
    payload_kg=2.0,
    roles=(Role.AERIAL_SCOUT,),
    comms=CommsProfile(range_m=500.0, los_required=False, bandwidth_msgs_per_s=20.0),
    localization=LocalizationProfile(gps_denied_drift_mps=0.30, vio_capable=False),
    min_speed_mps=12.0,          # stall margin — never below this
    max_climb_angle_rad=0.35,    # ~20 deg bounded flight-path angle
    sense_range_m=80.0,          # matches fw_contract.DEPTH_MAX_FW — needed lookahead at cruise
)

# Canonical registry. Accepts the on-device "type" strings used in rosters
# (isaac_vehicle setup: 'quadcopter' | 'rover' | 'fixedwing') plus the short aliases.
_REGISTRY: dict[str, VehicleClass] = {
    "quadcopter": QUADCOPTER,
    "quad": QUADCOPTER,
    "rover": ROVER,
    "fixedwing": FIXEDWING,
    "fw": FIXEDWING,
    "plane": FIXEDWING,
}


def get_class(name: str) -> VehicleClass:
    """Resolve a roster ``type`` string (or alias) to its VehicleClass."""
    key = name.strip().lower()
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"unknown vehicle class {name!r}; known: {sorted(set(_REGISTRY))}"
        ) from None


def register_class(vc: VehicleClass, *aliases: str) -> None:
    """Register a new vehicle class (e.g. fixed-wing) plus optional aliases.

    This is the entire cost of adding a class: a descriptor + a policy file.
    """
    _REGISTRY[vc.name.lower()] = vc
    for a in aliases:
        _REGISTRY[a.strip().lower()] = vc


def known_classes() -> tuple[str, ...]:
    """Distinct canonical class names currently registered."""
    return tuple(sorted({vc.name for vc in _REGISTRY.values()}))
