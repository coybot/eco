"""Crazyflie adapters for the on-device L5 runtime — SKELETON, not yet wired.

Implements the four ``onboard_l5`` provider Protocols against ``cflib`` (Crazyradio 2.0 →
CRTP). Bodies raise NotImplementedError; this file exists now so the frame/unit traps are
recorded while the sim work that depends on them is fresh, and so the sim and hardware share
one definition of the scan geometry instead of two that drift.

Target vehicle: **Crazyflie 2.1 Brushless** + Flow deck v2 + AI-deck 1.1 + Multi-ranger.
The class descriptor is ``vehicle_class.CRAZYFLIE``; the decision logic is the untouched
``l5_core.reactive_goto_controller`` + ``l5_smart.RuleBasedSmart`` that runs in sim.

Runs on a companion computer (this Mac first, an Orin Nano later), NOT on the aircraft — the
STM32/nRF cannot host any of this. Everything here is offboard, talking over the radio.

STATUS / WHAT IS LEFT
  * All four classes below are stubs.
  * The monocular metric-depth model is not chosen yet. It must emit *metres* and run fast
    enough to stay inside the 0.2 s latency the sim models.
  * Every VERIFY comment below is a bench test with props OFF.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

try:
    from .vehicle_class import CRAZYFLIE, Sensor, ray_table
    from .onboard_l5 import Pose, Peer
except ImportError:  # pragma: no cover - device flat-install path
    from vehicle_class import CRAZYFLIE, Sensor, ray_table
    from onboard_l5 import Pose, Peer

# Multi-ranger log variables, in the scan order ray_table defines for rays 45..49.
_TOF_VARS = ("range.front", "range.back", "range.left", "range.right", "range.up")

# cflib reports ranges in MILLIMETRES; out-of-range comes back as ~8190 mm.
_TOF_MM_TO_M = 1e-3
_TOF_OUT_OF_RANGE_MM = 8000.0


class CrazyflieTelemetry:
    """``onboard_l5.TelemetryProvider`` — pose from the Crazyflie's own EKF.

    Wire a ``LogConfig`` at ~100 Hz on ``stateEstimate.{x,y,z,vx,vy,vz}`` plus
    ``stabilizer.yaw``, and cache the last packet; ``pose()`` must not block the 10 Hz
    control loop on a radio round trip.

    UNITS AND FRAMES — the trap list:
      * ``stabilizer.yaw`` is DEGREES. ``Pose.yaw_rad`` is radians.
      * ``stateEstimate`` is the CF's own estimator in its takeoff frame: x forward of the
        takeoff heading, y LEFT, z up. That already matches the controller's ENU-ish world
        convention, so — unlike ``MavlinkActuator``, which flips y and z — there are no sign
        flips on the telemetry side. Do not "fix" this by symmetry with the MAVLink path.
      * ``loc_confidence`` should derive from ``kalman.varPX/varPY/varPZ``, but any
        variance→confidence map is a heuristic. The sim's LocalizationFabric confidence is a
        *model*, so the two are only loosely comparable. CALIBRATE before trusting the
        ``CONF_SLOW = 0.6`` / ``CONF_RTL = 0.30`` gates in team_runtime — those thresholds
        were tuned against the sim's fabric, not against a real Kalman variance.
      * ``sensor_ok`` should be: Multi-ranger present AND the last camera frame is younger
        than the latency budget. That mirrors the sim's frame_dropout semantics.

    There is no GPS and there never will be — ``CRAZYFLIE.localization.gps_available`` is
    False, so the fabric keeps this class on dead reckoning by construction. Flow-deck drift
    is real and unbounded over minutes; do not design a mission that assumes otherwise.
    """

    def __init__(self, scf, takeoff_yaw_rad: float = 0.0):
        self._scf = scf
        self._yaw0 = takeoff_yaw_rad

    def pose(self) -> Pose:
        raise NotImplementedError("wire a cflib LogConfig; see class docstring for units")


class CrazyflieSensor:
    """``onboard_l5.SensorProvider`` — the 50-ray hybrid scan.

    Layout is ``ray_table(Sensor.MONO_DEPTH_PLUS_TOF)``; import it, never re-derive it.

      rays 0..44   AI-deck camera → monocular metric depth → reduced by
                   ``run_prompt.depth_grid_5x9(depth_img, scale, intr, depth_max=4.0)``.
                   ``intr`` is duck-typed (.ppx/.ppy/.fx/.fy) — supply Himax calibration,
                   not RealSense values. Rays outside the lens must be filled with
                   ``sense_range_m`` from ``ray_table(...).fov_mask``.
      rays 45..49  Multi-ranger front/back/left/right/up. MILLIMETRES → /1000; the ~8190 mm
                   out-of-range value clamps to sense_range_m, which per the SensorProvider
                   contract means "clear".

    THE SCALE PROBLEM, AND THE FIX THE GEOMETRY BUILDS IN
      Monocular metric depth is scale-ambiguous: structure is right, absolute metres are not.
      Ray 22 (grid centre) and ray 45 (front ToF) point the same direction *by construction*,
      so when both return a hit you have a metric measurement of the same surface the camera
      is guessing at. Estimate a slowly-varying scale factor from that ratio and apply it to
      the whole camera block. The sim models the uncorrected error as a per-episode
      multiplicative bias with sigma 0.20 (``sensor_model.CRAZYFLIE_DECKS``) — if the online
      correction works, real performance should beat that; if it does not, it will not.

    OTHER HONEST LIMITS
      * Stale camera frames → set the whole camera block to sense_range_m, matching the sim's
        correlated frame_dropout rather than letting a stale frame masquerade as current.
      * There is NO downward obstacle beam. The Flow deck's ToF measures altitude, not
        obstacles: this vehicle cannot see a table beneath it. Reflected in the ray table.
      * A VL53L1x is a ~27 degree cone, not a pencil ray, so one beam is really a min over a
        cone. Modelling it as a ray is optimistic about coverage and pessimistic about
        detection; ``GroupImpairment.tof_cone_subrays`` is the hook for fixing that.
      * Sim finding worth knowing before investing in the depth model: across the 8 indoor
        scenarios, camera-only scored the same as camera+ToF and ToF-only was slightly worse
        — i.e. the forward camera fan carried navigation and the ToF beams' distinctive
        value (rear/lateral/up, plus the scale anchor above) was barely exercised. Do not
        read that as "the Multi-ranger is unnecessary": it is the scale reference and the
        independent collision guard, neither of which that suite tests.
    """

    def __init__(self, scf, depth_model=None):
        self._scf = scf
        self._depth = depth_model
        self._rt = ray_table(Sensor.MONO_DEPTH_PLUS_TOF)
        self._max_d = CRAZYFLIE.sense_range_m

    def scan(self) -> np.ndarray:
        raise NotImplementedError("fill rays 0..44 from mono depth, 45..49 from Multi-ranger")


class CrazyfliePeers:
    """``onboard_l5.PeerProvider`` — teammates over ``team_link``.

    ``CRAZYFLIE.comms`` is deliberately pessimistic (20 m, los_required=True, 10 msg/s):
    indoor walls kill 2.4 GHz, and CRTP bandwidth is shared with the camera stream. Expect
    long isolated stretches and make sure the caller tolerates an empty peer list.
    """

    def peers(self) -> list[Peer]:
        raise NotImplementedError("bridge team_link; return [] when isolated")


class CrazyflieActuator:
    """``onboard_l5.Actuator`` — body-frame velocity → CRTP setpoints.

    ``send(action, vclass, dt)`` receives ``[vx, vy, vz, yaw_rate]`` in the controller's body
    frame (x forward, y LEFT, z up; rad/s).

    TWO CANDIDATE PRIMITIVES, AND WHY THE CHOICE CONSTRAINED THE SIM
      * ``Commander.send_hover_setpoint(vx, vy, yawrate_deg, zdistance_m)`` — body-frame
        vx/vy, yaw rate in DEGREES/s, and ``zdistance`` is an ABSOLUTE ALTITUDE SETPOINT,
        not a rate. So this actuator must integrate ``vz * dt`` into a commanded altitude and
        clamp it to ``[floor_margin, vclass.ceiling_m]``. *That is precisely why the sim's
        KinematicWorld.integrate now enforces ceiling_m* — sim and vehicle clamp the same
        quantity, so the sim cannot pass a manoeuvre the hardware would refuse.
      * ``send_velocity_world_setpoint(vx, vy, vz, yawrate)`` — WORLD frame; needs the body
        action rotated by yaw here, the inverse of what MavlinkActuator does. Simpler
        vertical handling, no altitude integrator.
      Recommendation: hover_setpoint for first flights (altitude hold is far more forgiving
      of a bad vz), then world_setpoint once the frame conventions are confirmed.

    VERIFY ON A BENCH, PROPS OFF, BEFORE ANY FLIGHT
      * The vy sign. The controller assumes +LEFT; the firmware's hover-setpoint y convention
        has differed across releases. One bench test settles it, and getting it wrong means
        the vehicle accelerates toward the obstacle it is trying to avoid.
      * Yaw-rate sign and units (degrees/s, possibly negated relative to ENU yaw).

    ARMING AND FAILSAFE
      There is no arm/disarm in the MAVLink sense, and ``common/arm_disarm.py`` does not
      apply. Brushless boards DO gate on ``cf.platform.send_arming_request(True)``; stop with
      ``Commander.send_stop_setpoint()``. The firmware watchdog cuts thrust if setpoints stop
      arriving, which is a useful failsafe but also means ``OnboardL5Runtime.step`` must be
      driven at >= 10 Hz with a hard caller-side timeout — a blocked radio call is a fall.
    """

    def __init__(self, scf, floor_margin_m: float = 0.15, use_hover_setpoint: bool = True):
        self._scf = scf
        self._floor = floor_margin_m
        self._hover = use_hover_setpoint
        self._alt_cmd: Optional[float] = None   # integrated altitude setpoint (hover mode)

    def send(self, action: np.ndarray, vclass, dt: float) -> None:
        raise NotImplementedError("map to send_hover_setpoint / send_velocity_world_setpoint")

    def stop(self) -> None:
        raise NotImplementedError("Commander.send_stop_setpoint()")
