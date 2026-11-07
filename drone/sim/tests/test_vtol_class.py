"""The VTOL vehicle class (tilt-rotor, after the HEEWING T2 Cruza VTOL).

A VTOL is the coordinated-turn model with the stall removed: it cannot sideslip and turns
by banking once wing-borne, but it can hover, turn on the spot and climb vertically. These
check the descriptor, that the shared integrator and controller honour it, and that the
fixed-wing — which goes through the same code — did not move.
"""
import math

import numpy as np

import vehicle_class as vc
from team_world import KinematicWorld, Agent, Observation, reactive_goto_controller


VTOL = vc.get_class("vtol")
FW = vc.get_class("fixedwing")


def _step(world, agent, action, n, dt=0.1):
    for _ in range(n):
        world.integrate(agent, np.asarray(action, np.float32), dt)


# ------------------------------------------------------------------ descriptor
def test_descriptor():
    assert vc.get_class("cruza") is VTOL and vc.get_class("TiltRotor") is VTOL
    assert "vtol" in vc.known_classes()
    assert VTOL.kinematics is vc.Kinematics.COORDINATED_TURN_3D
    assert VTOL.can_hover and not FW.can_hover
    assert VTOL.is_aerial and not VTOL.planar
    assert (VTOL.state_dim, VTOL.action_dim) == (56, 4)
    assert VTOL.sensor is vc.Sensor.FORWARD_DEPTH
    # no learned policy: policy_fw.onnx was trained on a stalling airframe
    assert VTOL.policy_onnx == ""
    # it can hold a relay station; the fixed-wing cannot
    assert VTOL.can_fill(vc.Role.COMMS_RELAY) and not FW.can_fill(vc.Role.COMMS_RELAY)
    # same numbers as the Godot model (fixedwing_manager.gd VTOL_*)
    assert VTOL.max_speed_mps == 24.0 and VTOL.wingborne_speed_mps == 13.0
    assert VTOL.hover_climb_mps == 2.5


def test_sense_range_exceeds_turn_radius_avoid_radius():
    """The 'clear' sentinel must exceed the avoid radius the controller derives, or a clear
    scan reads as a phantom obstacle at max range (see VehicleClass.sense_range_m)."""
    turn_radius = VTOL.max_speed_mps / VTOL.max_yaw_rate_radps
    assert VTOL.sense_range_m > max(VTOL.avoid_radius_m, 1.5 * turn_radius)


def test_fixedwing_limits_unchanged():
    """The new hover fields default to the fixed-wing's historical behaviour exactly."""
    assert FW.bank_ref_speed_mps == FW.min_speed_mps
    for v in (12.0, 18.0, 25.0):
        assert FW.climb_cap_mps(v) == v * math.tan(FW.max_climb_angle_rad)
    assert FW.climb_cap_mps(0.0) == 0.0


# ------------------------------------------------------------------ integrator
def test_vertical_takeoff_from_a_hover():
    kw = KinematicWorld()
    a = Agent(id="v", vclass=VTOL, pos=np.array([0.0, 0.0, 0.0], np.float32))
    _step(kw, a, [0.0, 0.0, 5.0, 0.0], 40)          # asks for 5 m/s up, at zero airspeed
    assert abs(a.pos[0]) < 1e-6 and abs(a.pos[1]) < 1e-6, "hover must not drift forward"
    assert a.pos[2] == np.float32(10.0), "rotor climb is capped at 2.5 m/s: 4 s -> 10 m"


def test_fixedwing_cannot_hover_or_climb_vertically():
    kw = KinematicWorld()
    a = Agent(id="f", vclass=FW, pos=np.array([0.0, 0.0, 40.0], np.float32))
    _step(kw, a, [0.0, 0.0, 5.0, 0.0], 10)
    assert a.pos[0] > 11.0, "zero airspeed is clamped up to the 12 m/s stall margin"
    assert a.pos[2] < 40.0 + 12.0 * math.tan(FW.max_climb_angle_rad) * 1.0 + 1e-3


def test_turns_on_the_spot_in_hover():
    kw = KinematicWorld()
    a = Agent(id="v", vclass=VTOL, pos=np.array([0.0, 0.0, 20.0], np.float32))
    _step(kw, a, [0.0, 0.0, 0.0, 0.6], 20)          # 2 s at the full yaw rate
    assert math.isclose(a.yaw, 1.2, abs_tol=1e-5)
    assert np.allclose(a.pos, [0.0, 0.0, 20.0])


def test_wingborne_turn_is_bank_limited():
    """Above the wing-borne speed the yaw limit falls off as 13/v, like the fixed-wing's
    falls off from its stall speed. With the old max(min_speed, 1e-3) reference a VTOL
    could barely turn at all above walking pace."""
    kw = KinematicWorld()
    for v, expect in ((10.0, 0.6), (13.0, 0.6), (24.0, 0.6 * 13.0 / 24.0)):
        a = Agent(id="v", vclass=VTOL, pos=np.array([0.0, 0.0, 50.0], np.float32))
        _step(kw, a, [v, 0.0, 0.0, 5.0], 1, dt=1.0)
        assert math.isclose(a.yaw, expect, rel_tol=1e-6), (v, a.yaw)


# ------------------------------------------------------------------ controller
def _fly_to(vclass, goal, seconds=60.0, dt=0.1):
    kw = KinematicWorld()
    ctl = reactive_goto_controller()
    a = Agent(id="a", vclass=vclass, pos=np.array([0.0, 0.0, 30.0], np.float32))
    goal = np.asarray(goal, np.float32)
    n_rays = vc.ray_table(vclass.sensor).n_rays
    scan = np.full(n_rays, vclass.sense_range_m, np.float32)
    dists = []
    for _ in range(int(seconds / dt)):
        rel = goal - a.pos
        c, s = math.cos(-a.yaw), math.sin(-a.yaw)
        body = np.array([c * rel[0] - s * rel[1], s * rel[0] + c * rel[1], rel[2]], np.float32)
        obs = Observation(agent_id="a", body_target=body, goal_dist=float(np.linalg.norm(rel)),
                          neighbors=[], scan=scan, min_clearance=float(scan.min()),
                          loc_confidence=1.0, inbox=[])
        kw.integrate(a, ctl(a, obs), dt)
        dists.append(float(np.linalg.norm(goal - a.pos)))
    return a, dists


def test_vtol_arrives_and_holds_a_hover():
    a, dists = _fly_to(VTOL, [300.0, 0.0, 30.0])
    tail = dists[-50:]                                   # last 5 s
    assert max(tail) < 2.0, f"VTOL should settle on the goal, last 5 s: {max(tail):.1f} m"
    assert float(np.linalg.norm(a.vel)) < 0.5, "and be (nearly) stopped there"


def test_fixedwing_still_orbits_rather_than_stopping():
    """Same controller, same goal: the fixed-wing branch is unchanged and keeps flying."""
    a, dists = _fly_to(FW, [300.0, 0.0, 30.0])
    assert float(np.linalg.norm(a.vel[:2])) >= FW.min_speed_mps - 1e-3
    assert max(dists[-50:]) > 2.0
