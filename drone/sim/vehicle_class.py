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
  - crazyflie:  holonomic 3D micro-UAV (Crazyflie 2.1 Brushless + Flow deck v2 +
                AI-deck 1.1 + Multi-ranger), hybrid 50-ray sensing, no learned policy —
                classical L5 controller only. radius 0.065 m, 1.0 m/s, 2.2 m ceiling.

Length scales are class properties, not literals. The shipped outdoor classes carry the
historically-tuned defaults (3.5 m avoid radius etc.); a micro-UAV that operates in a
2.4 m room overrides them via :func:`indoor_scale`. Defaults are stored as literals rather
than derived, so the outdoor classes are bit-identical to the pre-refactor constants.
"""
from __future__ import annotations

import math
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
    """Primary obstacle-sensing modality — picks how neighbors/obstacles are scanned.

    Consumers must resolve geometry through :func:`ray_table`, never by assuming a ray
    count: the modalities have different scan lengths (45 / 72 / 50).
    """
    FORWARD_DEPTH = "forward_depth"  # 2D depth grid, limited FOV (quad camera)
    LIDAR_360 = "lidar_360"          # full horizontal ring (rover)
    MONO_DEPTH_PLUS_TOF = "mono_depth_plus_tof"
    # Hybrid (Crazyflie): 45-ray monocular-depth grid, geometry identical to FORWARD_DEPTH
    # but masked to the real lens FOV, PLUS 5 sparse ToF beams (front/back/left/right/up).
    # The camera rays are scale-ambiguous; the ToF beams are metric. Ray 22 (grid centre)
    # and ray 45 (front ToF) point the same direction by construction — that redundancy is
    # the online anchor for resolving monocular depth scale on hardware.


# --------------------------------------------------------------------------- ray tables
# The geometric meaning of each element of Observation.scan. These MUST match contract.py /
# rover_contract.py and the sim exactly; l5_core and team_world import them from here so
# there is one definition rather than a copy per module.
_LIDAR_RAYS = 72
_LIDAR_ANGLES = tuple(2 * math.pi * i / _LIDAR_RAYS for i in range(_LIDAR_RAYS))

# Forward depth grid: 5 rows (top→bottom, ±35°) × 9 cols (left→right, ±45°), row-major.
_DEPTH_COLS = 9
_DEPTH_ROWS = 5
_DEPTH_HFOV = math.radians(90.0)
_DEPTH_VFOV = math.radians(70.0)

# Real AI-deck 1.1 (Himax) lens coverage, narrower than the 90×70 grid the contract spans.
# VERIFY ON HARDWARE. Robust to the uncertainty: a conservative 70×55 and an optimistic
# 87×65 both mask to the same 21 rays (cols 1..7 × rows 1..3), so the geometry below does
# not hinge on pinning the exact lens.
CF_CAM_HFOV = math.radians(70.0)
CF_CAM_VFOV = math.radians(55.0)

# The five Multi-ranger beams, in scan order: front, back, left, right, up.
# There is deliberately no down beam — the Flow deck's downward ToF measures altitude, not
# obstacles. A Crazyflie cannot see a table beneath it, and this table says so.
_TOF_BODY_DIRS = ((1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                  (0.0, -1.0, 0.0), (0.0, 0.0, 1.0))


def _make_depth_dirs() -> tuple:
    """Body-frame unit ray directions (fwd, left, up) for the 45-ray depth grid."""
    dirs = []
    for r in range(_DEPTH_ROWS):
        pitch = _DEPTH_VFOV / 2.0 - r * _DEPTH_VFOV / (_DEPTH_ROWS - 1)
        for c in range(_DEPTH_COLS):
            yaw = _DEPTH_HFOV / 2.0 - c * _DEPTH_HFOV / (_DEPTH_COLS - 1)
            ce = math.cos(pitch)
            dirs.append((ce * math.cos(yaw), ce * math.sin(yaw), math.sin(pitch)))
    return tuple(dirs)


_DEPTH_BODY_DIRS = _make_depth_dirs()   # 45 × (fwd, left, up)
_DEPTH_YAW_ANGLES = tuple(math.atan2(dl, df) for df, dl, _ in _DEPTH_BODY_DIRS)

_LIDAR_BODY_DIRS = tuple((math.cos(a), math.sin(a), 0.0) for a in _LIDAR_ANGLES)


def _make_fov_mask(hfov: float, vfov: float) -> tuple:
    """Per-ray visibility of the 45-ray grid through a lens narrower than the grid.

    Rays outside the lens are not measurements: they report "clear" (sense_range_m),
    the same honest degradation run_prompt.depth_grid_5x9 already documents for the
    D435i ("21/45 rays land inside the image ... far-peripheral vision is always
    'clear' at deploy"). Optimistic in coverage, never optimistic about an obstacle.
    """
    mask = []
    for r in range(_DEPTH_ROWS):
        pitch = _DEPTH_VFOV / 2.0 - r * _DEPTH_VFOV / (_DEPTH_ROWS - 1)
        for c in range(_DEPTH_COLS):
            yaw = _DEPTH_HFOV / 2.0 - c * _DEPTH_HFOV / (_DEPTH_COLS - 1)
            mask.append(abs(yaw) <= hfov / 2.0 + 1e-9 and abs(pitch) <= vfov / 2.0 + 1e-9)
    return tuple(mask)


@dataclass(frozen=True)
class RayTable:
    """Geometry of one sensing modality's scan array.

    Every consumer of Observation.scan resolves length and direction through this,
    so adding a modality cannot silently index a table sized for another one.
    """
    n_rays: int
    dirs: tuple                     # n_rays × (fwd, left, up) unit vectors, body frame
    yaw_angles: tuple               # horizontal angle per ray (2D repulsion direction)
    groups: tuple                   # ((name, start, stop), ...) partitioning [0, n_rays)
    grid_shape: tuple | None        # (rows, cols) of the leading dense block, or None
    vertical_cue: bool              # dirs carry usable pitch → enables climb/descend repulsion
    fov_mask: tuple | None          # per-ray bool; False = outside the real lens, always clear


_RAY_TABLES: dict[Sensor, RayTable] = {
    Sensor.FORWARD_DEPTH: RayTable(
        n_rays=_DEPTH_ROWS * _DEPTH_COLS,
        dirs=_DEPTH_BODY_DIRS,
        yaw_angles=_DEPTH_YAW_ANGLES,
        groups=(("camera", 0, _DEPTH_ROWS * _DEPTH_COLS),),
        grid_shape=(_DEPTH_ROWS, _DEPTH_COLS),
        vertical_cue=True,
        fov_mask=None,
    ),
    Sensor.LIDAR_360: RayTable(
        n_rays=_LIDAR_RAYS,
        dirs=_LIDAR_BODY_DIRS,
        yaw_angles=_LIDAR_ANGLES,
        groups=(("lidar", 0, _LIDAR_RAYS),),
        grid_shape=None,
        vertical_cue=False,         # a horizontal ring carries no pitch information
        fov_mask=None,
    ),
    Sensor.MONO_DEPTH_PLUS_TOF: RayTable(
        n_rays=_DEPTH_ROWS * _DEPTH_COLS + len(_TOF_BODY_DIRS),
        # dirs[0:45] IS the FORWARD_DEPTH tuple — index-compatible, so
        # run_prompt.depth_grid_5x9 drops in unmodified on hardware.
        dirs=_DEPTH_BODY_DIRS + _TOF_BODY_DIRS,
        yaw_angles=_DEPTH_YAW_ANGLES + tuple(
            math.atan2(dl, df) for df, dl, _ in _TOF_BODY_DIRS),
        groups=(("camera", 0, 45), ("tof", 45, 50)),
        grid_shape=(_DEPTH_ROWS, _DEPTH_COLS),
        vertical_cue=True,
        fov_mask=_make_fov_mask(CF_CAM_HFOV, CF_CAM_VFOV) + (True,) * len(_TOF_BODY_DIRS),
    ),
}


def ray_table(sensor: Sensor) -> RayTable:
    """Scan geometry for a sensing modality."""
    try:
        return _RAY_TABLES[sensor]
    except KeyError:
        raise KeyError(f"no ray table for sensor {sensor!r}") from None


def lidar_ray_angles() -> tuple[float, ...]:
    return _LIDAR_ANGLES


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
    gps_denied_drift_mps: float = 0.10      # random-walk bias scale under denial. NOTE: the
    # fabric integrates this as a random walk (per-step sigma = drift*dt), so accumulated
    # error grows as drift*sqrt(dt*t), NOT drift*t — "per second" is loose shorthand.
    vio_capable: bool = True                # can the class self-localize without GPS at all?
    gps_available: bool = True              # False = never has GPS (indoor micro-UAV). Such a
    # class starts and stays GPS-denied with no inject needed: that is physically true, not a
    # scenario choice, and the fabric must refuse to "restore" it to GPS_OK.


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

    # --- length scales (were hardcoded literals in l5_core / team_world / team_runtime) ---
    # Defaults reproduce the historically-tuned outdoor constants exactly. Override via
    # indoor_scale() for a vehicle whose world is metres across rather than tens of metres.
    interaction_scale_m: float = 3.5       # characteristic interaction length of the class
    avoid_radius_m: float = 3.5            # obstacle repulsion onset (was avoid_radius=3.5)
    slow_radius_m: float = 2.0             # goal-approach slowdown onset (was slow_radius=2.0)
    neighbor_safe_pad_m: float = 0.8       # was `safe = r_self + r_other + 0.8`
    clearance_slow_pad_m: float = 1.0      # was `min_clearance < radius_m + 1.0` (unicycle)
    climb_over_clearance_m: float = 1.5    # was `min_clearance < 1.5` (holonomic climb-over)
    proximity_m: float = 3.5               # team_runtime convoy/yield interaction length.
    # Deliberately separate from avoid_radius_m: fixedwing's turn-radius override inflates the
    # latter to ~63 m, which must not leak into convoy spacing.
    neighbor_range_m: float = 12.0         # was TeamWorld.NEIGHBOR_RANGE
    collision_pad_m: float = 0.05          # was TeamWorld.COLLISION_PAD

    # --- capability queries (layers call these, not isinstance / string checks) ---
    @property
    def is_aerial(self) -> bool:
        return self.ceiling_m > 0.0

    @property
    def planar(self) -> bool:
        return self.kinematics is Kinematics.UNICYCLE_2D

    @property
    def can_hover(self) -> bool:
        """True if the vehicle can come to a complete stop and hold position.

        This is the discriminator for "must the geometry be flyable as a
        continuous curve, or can the vehicle stop and pivot at each waypoint" —
        a quad hovers and a rover simply stops, so both can take a sharp corner,
        while a fixed-wing has to arc through it. Keyed off min_speed_mps (the
        stall/minimum airspeed, 0 == can hover) rather than the kinematics enum,
        because that is the property that actually matters and it stays correct
        for a future VTOL that is COORDINATED_TURN_3D but can still hover.
        """
        return self.min_speed_mps <= 0.0

    def can_fill(self, role: Role) -> bool:
        return role in self.roles

    @property
    def preferred_role(self) -> Role:
        return self.roles[0]


# --------------------------------------------------------------------------- length scaling
_REF_SCALE_M = 3.5   # the interaction length the shipped outdoor constants were tuned at


def indoor_scale(scale_m: float) -> dict:
    """Length-scale kwargs for a vehicle whose interaction length is ``scale_m``.

    Preserves the ratios the shipped classes were tuned at, so a micro-UAV inherits the
    tuned *shape* of the potential field at 1/Nth the size instead of needing nine
    constants re-derived by hand. One knob, not nine.

    ``indoor_scale(3.5)`` returns exactly the dataclass defaults: k is 3.5/3.5 == 1.0
    exactly in IEEE-754, and 1.0 * x == x exactly, so the outdoor classes cannot drift by
    a rounding step if they are ever expressed through this helper.
    """
    k = scale_m / _REF_SCALE_M
    return dict(
        interaction_scale_m=scale_m,
        avoid_radius_m=scale_m,             # ratio 1.0 — assign directly, not k*3.5
        slow_radius_m=k * 2.0,
        neighbor_safe_pad_m=k * 0.8,
        clearance_slow_pad_m=k * 1.0,
        climb_over_clearance_m=k * 1.5,
        proximity_m=scale_m,                # ratio 1.0
        neighbor_range_m=k * 12.0,
        collision_pad_m=k * 0.05,
    )


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

CRAZYFLIE = VehicleClass(
    name="crazyflie",
    kinematics=Kinematics.HOLONOMIC_3D,   # multirotor: free 3D translation + yaw, hovers
    sensor=Sensor.MONO_DEPTH_PLUS_TOF,
    state_dim=61,           # 11 + 50 rays. Only matters if a policy is ever trained for this
                            # class; the shipped autonomy is the classical controller (L5.md).
    action_dim=4,           # [vx, vy, vz, yaw_rate] body (fwd, left, up)
    policy_onnx="",         # no learned policy. The 45-ray/10 m ONNX contract is unreachable
                            # with this hardware, and L5.md reports the learned policies
                            # underperformed the rule-based controller anyway.
    # --- geometry: Crazyflie 2.1 Brushless, 92 mm motor-to-motor ---
    radius_m=0.065,         # CHOICE: ~0.13 m tip-to-tip with props → circumscribed ~0.065.
                            # VERIFY: brushless props may differ from the 2.1's 45 mm.
    # --- limits: sensor-bound, NOT airframe-bound. A brushless CF can far exceed these. ---
    max_speed_mps=1.0,      # CHOICE: operational cap set by PMW3901 optical-flow saturation
                            # and by wanting >=8 control ticks inside avoid_radius_m at 10 Hz.
    max_accel_mps2=3.0,     # CHOICE ~0.3 g (thrust-to-weight is far higher; currently unread
                            # by the integrator, reserved for accel limiting).
    max_yaw_rate_radps=2.0, # CHOICE ~115 deg/s: yaw destroys optical flow and motion-blurs the
                            # Himax frame long before the airframe objects.
    ceiling_m=2.2,          # operational ceiling in a 2.4 m room, 0.2 m for the up-ToF to
                            # react. Enforced by KinematicWorld.integrate.
    payload_kg=0.0,         # three decks (~8.3 g) already consume the margin
    roles=(Role.AERIAL_SCOUT,),
    comms=CommsProfile(range_m=20.0, los_required=True, bandwidth_msgs_per_s=10.0),
    # los_required=True is the honest indoor case — walls kill 2.4 GHz. CRTP bandwidth is
    # shared with the AI-deck JPEG stream, hence the low message rate.
    localization=LocalizationProfile(gps_denied_drift_mps=0.30, vio_capable=True,
                                     gps_available=False),   # no GPS, ever
    min_speed_mps=0.0,      # hovers
    sense_range_m=4.0,      # VL53L1x ceiling. Binding on ALL rays: the camera fan could see
    # further, but is truncated here so a single max_d governs the whole scan. Satisfies the
    # sense_range_m > avoid_radius invariant with 5x margin (4.0 > 0.8) — the tempting wrong
    # move is to keep a 3.5 m avoid radius and inflate this to 10 m, which would both lie
    # about the hardware and make the vehicle unflyable indoors.
    interaction_scale_m=0.8,
    **{k: v for k, v in indoor_scale(0.8).items() if k != "interaction_scale_m"},
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
    "crazyflie": CRAZYFLIE,
    "cf": CRAZYFLIE,
    "cf21b": CRAZYFLIE,
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
