"""ArduPilot adapters for the onboard L5 loop: frames, refusals, depth scan.

Every frame convention here has a sign that, got wrong, flies the vehicle
toward what it is avoiding, so each one is pinned against a fake whose MAVLink
semantics are written from the spec (tests/fake_ardupilot.py).

Run: cd coybot && .venv/bin/python -m pytest eco/drone/common/tests/test_ardupilot_l5.py -v
"""
import math

import numpy as np
import pytest

from eco.drone.common.ardupilot_l5 import (
    ArduPilotActuator, ArduPilotTelemetry, DepthFrame, DepthScanSensor, TelemetryUnavailable,
    ekf_confidence, ned_yaw_to_enu, EKF_UNKNOWN_CONF, MAV_FRAME_BODY_OFFSET_NED,
)
from eco.drone.common.vehicle_class import get_class
from .fake_ardupilot import FakeArduPilot, Msg

QUAD, ROVER = get_class("quadcopter"), get_class("rover")


# --------------------------------------------------------------------------- telemetry
def test_pose_is_enu_from_the_takeoff_point_not_the_ekf_origin():
    # Took off 3 m north / 2 m west of the EKF origin; now 4 m north, 1 m east and 5 m up of it.
    veh = FakeArduPilot(home_ned=(3.0, -2.0, 0.4), pos_ned=(7.0, -1.0, -4.6), heading=0.0)
    veh.vel_ned = [1.0, -2.0, -0.5]          # north 1, west 2, climbing 0.5
    tel = ArduPilotTelemetry(sdk=veh)
    p = tel.pose()
    np.testing.assert_allclose(p.pos_enu, [1.0, 4.0, 5.0], atol=0.05)
    np.testing.assert_allclose(p.vel_enu, [-2.0, 1.0, 0.5], atol=1e-6)
    assert abs(p.yaw_rad - math.pi / 2) < 1e-6, "heading north is ENU yaw +90 deg"
    assert p.loc_confidence == 1.0 and p.sensor_ok


def test_takeoff_point_is_resolved_once_and_can_be_pinned():
    veh = FakeArduPilot(home_ned=(3.0, -2.0, 0.0), pos_ned=(3.0, -2.0, -5.0))
    tel = ArduPilotTelemetry(sdk=veh)
    first = tel.resolve_origin()
    veh.ned = [10.0, 10.0, -5.0]
    assert tel.resolve_origin() == first, "the origin is fixed once found, not re-derived as it flies"
    pinned = ArduPilotTelemetry(sdk=veh, origin_ned=(0.0, 0.0, 0.0))
    np.testing.assert_allclose(pinned.pose().pos_enu, [10.0, 10.0, 5.0], atol=1e-6)


def test_no_recorded_takeoff_means_no_pose():
    veh = FakeArduPilot()
    veh.flight_home = lambda: None
    with pytest.raises(TelemetryUnavailable):
        ArduPilotTelemetry(sdk=veh).pose()


def test_stale_position_raises_rather_than_flying_on_it():
    veh = FakeArduPilot()
    tel = ArduPilotTelemetry(sdk=veh)
    tel.pose()
    veh.stale.add("LOCAL_POSITION_NED")
    with pytest.raises(TelemetryUnavailable):
        tel.pose()


def test_telemetry_asks_for_its_own_streams_including_the_ekf_report():
    veh = FakeArduPilot()
    ArduPilotTelemetry(sdk=veh).pose()
    asked = {int(p[0]) for cmd, p in veh.commands if cmd == 511}
    assert {30, 32, 33, 193} <= asked


def test_yaw_conversion():
    assert abs(ned_yaw_to_enu(0.0) - math.pi / 2) < 1e-9       # north
    assert abs(ned_yaw_to_enu(math.pi / 2)) < 1e-9             # east
    assert abs(abs(ned_yaw_to_enu(-math.pi / 2)) - math.pi) < 1e-9   # west


@pytest.mark.parametrize("flags,var,expect", [
    (1 | 2 | 8 | 16, 0.1, 1.0),            # healthy
    (1 | 2 | 8 | 16, 0.75, 0.5),           # innovations halfway to failing
    (1 | 2 | 8 | 16, 1.2, 0.0),            # failing innovation checks
    (1 | 2, 0.1, 0.0),                     # no horizontal position at all
    (1 | 2 | 8 | 16 | 128, 0.1, 0.0),      # constant-position mode
    (1 | 2 | 8 | 16 | 1024, 0.1, 0.0),     # uninitialised
    (1 | 2 | 8 | 16 | 32768, 0.1, EKF_UNKNOWN_CONF),   # GPS glitch
    (1 | 8 | 16, 0.1, EKF_UNKNOWN_CONF),   # no horizontal velocity
])
def test_ekf_confidence(flags, var, expect):
    ekf = Msg(flags=flags, velocity_variance=var, pos_horiz_variance=var)
    assert ekf_confidence(ekf) == pytest.approx(expect)


def test_no_ekf_report_is_low_confidence_not_full():
    assert ekf_confidence(None) == EKF_UNKNOWN_CONF < 0.4


def test_pose_confidence_follows_the_ekf():
    veh = FakeArduPilot()
    veh.ekf_flags = 1 | 2      # lost horizontal position
    assert ArduPilotTelemetry(sdk=veh).pose().loc_confidence == 0.0


def test_sensor_health_feeds_sensor_ok():
    veh = FakeArduPilot()
    assert ArduPilotTelemetry(sdk=veh, sensor_health=lambda: False).pose().sensor_ok is False
    def broken():
        raise RuntimeError("camera gone")
    assert ArduPilotTelemetry(sdk=veh, sensor_health=broken).pose().sensor_ok is False


# --------------------------------------------------------------------------- actuator
def test_quad_body_velocity_maps_to_mavlink_body_frame():
    veh = FakeArduPilot("quad")
    act = ArduPilotActuator("quadcopter", sdk=veh)
    assert act.send(np.array([1.0, 0.5, 0.25, 0.2]), QUAD, 0.1) is True
    sp = veh.setpoints[-1]
    assert sp["frame"] == MAV_FRAME_BODY_OFFSET_NED == 9
    # (fwd, left, up) -> (fwd, right, down); ENU yaw rate -> clockwise
    assert (sp["vx"], sp["vy"], sp["vz"], sp["yaw_rate"]) == pytest.approx((1.0, -0.5, -0.25, -0.2))
    assert sp["target"] == (1, 1)
    # velocity + yaw rate used; position, acceleration and yaw ignored
    assert sp["mask"] & 0b111 == 0b111 and sp["mask"] & (0b111 << 3) == 0
    assert sp["mask"] & (1 << 10) and not sp["mask"] & (1 << 11)


def test_rover_action_is_speed_and_turn_rate():
    veh = FakeArduPilot("rover")
    act = ArduPilotActuator("rover", sdk=veh)
    assert act.send(np.array([1.2, 0.5]), ROVER, 0.1)
    sp = veh.setpoints[-1]
    assert (sp["vx"], sp["vy"], sp["vz"], sp["yaw_rate"]) == pytest.approx((1.2, 0.0, 0.0, -0.5))


def test_speed_is_clamped_keeping_direction():
    veh = FakeArduPilot("quad")
    act = ArduPilotActuator("quadcopter", sdk=veh)
    act.send(np.array([30.0, 40.0, 0.0, 0.0]), QUAD, 0.1)
    sp = veh.setpoints[-1]
    assert math.hypot(sp["vx"], sp["vy"]) == pytest.approx(QUAD.max_speed_mps)
    assert sp["vx"] / -sp["vy"] == pytest.approx(30.0 / 40.0)


def test_climb_stops_at_the_altitude_limit():
    veh = FakeArduPilot("quad")
    alt = {"v": 15.0}
    act = ArduPilotActuator("quadcopter", sdk=veh, altitude_fn=lambda: alt["v"], max_alt_m=15.0)
    act.send(np.array([0.0, 0.0, 1.0, 0.0]), QUAD, 0.1)
    assert veh.setpoints[-1]["vz"] == 0.0
    act.send(np.array([0.0, 0.0, -1.0, 0.0]), QUAD, 0.1)
    assert veh.setpoints[-1]["vz"] == pytest.approx(1.0), "descending from the ceiling is allowed"
    alt["v"] = 0.4
    act.send(np.array([0.0, 0.0, -1.0, 0.0]), QUAD, 0.1)
    assert veh.setpoints[-1]["vz"] == 0.0


@pytest.mark.parametrize("break_it,why", [
    (lambda v: setattr(v, "armed", False), "not armed"),
    (lambda v: setattr(v, "custom_mode", 5), "not in GUIDED"),
    (lambda v: setattr(v, "pilot", "sticks moved"), "pilot"),
    (lambda v: setattr(v, "mav_type", 1), "MAV_TYPE"),          # a fixed-wing heartbeat
    (lambda v: v.stale.add("HEARTBEAT"), "heartbeat"),
])
def test_refuses_unless_armed_guided_and_the_right_vehicle(break_it, why):
    veh = FakeArduPilot("quad")
    act = ArduPilotActuator("quadcopter", sdk=veh)
    break_it(veh)
    assert act.send(np.array([1.0, 0.0, 0.0, 0.0]), QUAD, 0.1) is False
    assert veh.setpoints == []
    assert why in act.last_refusal


def test_refuses_another_system_id():
    veh = FakeArduPilot("quad", sysid=2)
    act = ArduPilotActuator("quadcopter", sdk=veh, expected_sysid=1)
    assert act.send(np.array([1.0, 0.0, 0.0, 0.0]), QUAD, 0.1) is False
    assert "system 2" in act.last_refusal


def test_refuses_a_rover_action_on_a_quad_and_a_rover_actuator_on_a_quad():
    veh = FakeArduPilot("quad")
    assert ArduPilotActuator("quadcopter", sdk=veh).send(np.array([1.0, 0.0]), ROVER, 0.1) is False
    assert ArduPilotActuator("rover", sdk=veh).send(np.array([1.0, 0.0]), ROVER, 0.1) is False
    assert veh.setpoints == []


def test_refuses_px4():
    veh = FakeArduPilot("quad")
    hb = veh._fc_cached("HEARTBEAT")
    hb.autopilot = 12
    veh._fc_cached = lambda t, max_age=1.5: hb
    act = ArduPilotActuator("quadcopter", sdk=veh, expected_sysid=1)
    assert act.send(np.zeros(4), QUAD, 0.1) is False and "ArduPilot" in act.last_refusal


def test_stop_sends_zero_velocity():
    veh = FakeArduPilot("quad")
    act = ArduPilotActuator("quadcopter", sdk=veh)
    assert act.stop()
    sp = veh.setpoints[-1]
    assert (sp["vx"], sp["vy"], sp["vz"], sp["yaw_rate"]) == (0.0, 0.0, 0.0, 0.0)
    veh.custom_mode = 6   # RTL: not ours to touch any more
    assert act.stop() is False and len(veh.setpoints) == 1


# --------------------------------------------------------------------------- depth scan
W, H, F = 640, 480, 385.0


def _frame(depth_m, stamp=0.0):
    return DepthFrame(np.round(np.asarray(depth_m) * 1000).astype(np.uint16), 0.001, F, F,
                      W / 2.0, H / 2.0, stamp)


def _scene(wall_z=None, wall_half_x=None, ground_h=None):
    """Depth image (z metres) of a camera looking along +z: an optional wall at z = wall_z
    (|x| <= wall_half_x if given) and an optional ground plane ground_h below the lens."""
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    dx, dy = (u - W / 2.0) / F, (v - H / 2.0) / F
    z = np.full((H, W), np.inf)
    if wall_z is not None:
        hit = np.ones_like(z, bool) if wall_half_x is None else np.abs(dx * wall_z) <= wall_half_x
        z = np.where(hit, np.minimum(z, wall_z), z)
    if ground_h is not None:
        with np.errstate(divide="ignore"):
            tz = np.where(dy > 0, ground_h / dy, np.inf)
        z = np.minimum(z, tz)
    z[~np.isfinite(z) | (z > 60)] = 0.0       # no return
    return z


def test_quad_sees_a_wall_ahead_on_the_centre_ray_and_nothing_outside_the_lens():
    s = DepthScanSensor("quadcopter", lambda: _frame(_scene(wall_z=2.0)), clock=lambda: 0.0)
    scan = s.scan()
    assert scan.shape == (45,) and s.healthy()
    assert scan[22] == pytest.approx(2.0, abs=0.05), "grid centre (row 2, col 4) is straight ahead"
    assert scan[0] == QUAD.sense_range_m and scan[44] == QUAD.sense_range_m, \
        "the +-45 deg corner rays are outside a D435i's lens: clear"


def test_wall_off_to_the_left_shows_on_the_left_columns():
    # Wall only at x < -0.5 m in camera frame, i.e. to the LEFT (camera x is right).
    z = _scene(wall_z=2.0)
    u = np.arange(W)
    z[:, (u - W / 2.0) / F * 2.0 > -0.5] = 0.0
    scan = DepthScanSensor("quadcopter", lambda: _frame(z), clock=lambda: 0.0).scan()
    row2 = scan[18:27]
    assert row2[1] < 3.0 and row2[2] < 3.0, "left of centre (cols 1-2) sees the wall"
    assert row2[6] == QUAD.sense_range_m and row2[7] == QUAD.sense_range_m, "right is clear"


def test_a_down_looking_camera_gives_no_forward_obstacles():
    s = DepthScanSensor("quadcopter", lambda: _frame(_scene(wall_z=2.0)), mount_pitch_deg=-90.0,
                        clock=lambda: 0.0)
    assert (s.scan() == QUAD.sense_range_m).all()


def test_nose_down_pitch_is_levelled_out():
    # Vehicle pitched 20 deg nose-down: a wall straight ahead of the camera is 20 deg below level.
    tilt = {"v": (0.0, 0.0)}
    s = DepthScanSensor("quadcopter", lambda: _frame(_scene(wall_z=2.0)), tilt_fn=lambda: tilt["v"],
                        clock=lambda: 0.0)
    level = s.scan()
    tilt["v"] = (0.0, math.radians(-20.0))
    pitched = s.scan()
    assert level[22] < 2.1 and pitched[31] < 2.5, "the return moves down a row (row 3, col 4)"


def test_rover_ring_sees_a_wall_ahead_but_not_the_ground():
    z = _scene(wall_z=3.0, wall_half_x=0.5, ground_h=0.3)
    s = DepthScanSensor("rover", lambda: _frame(z), mount_height_m=0.3, clock=lambda: 0.0)
    scan = s.scan()
    assert scan.shape == (72,)
    assert scan[0] == pytest.approx(3.0, abs=0.1), "ray 0 is straight ahead"
    assert scan[6] == ROVER.sense_range_m and scan[66] == ROVER.sense_range_m, \
        "30 deg either side only the ground is visible, and ground is not an obstacle"
    assert scan[36] == ROVER.sense_range_m, "behind the rover is unseen: clear"


def test_rover_needs_the_mount_height():
    with pytest.raises(ValueError):
        DepthScanSensor("rover", lambda: None)


def test_stale_or_missing_frames_are_blind_not_old():
    now = {"t": 10.0}
    s = DepthScanSensor("quadcopter", lambda: _frame(_scene(wall_z=2.0), stamp=9.8),
                        max_age_s=0.5, clock=lambda: now["t"])
    assert s.scan()[22] < 2.1 and s.healthy()
    now["t"] = 11.0
    assert (s.scan() == QUAD.sense_range_m).all() and not s.healthy()
    none = DepthScanSensor("quadcopter", lambda: None)
    assert (none.scan() == QUAD.sense_range_m).all() and not none.healthy()


def test_isolated_speckle_is_not_an_obstacle():
    z = np.zeros((H, W))
    z[240, 320] = 0.5                      # one flying pixel
    s = DepthScanSensor("quadcopter", lambda: _frame(z), stride=1, clock=lambda: 0.0)
    assert s.scan()[22] == QUAD.sense_range_m
