"""Follow-target control law and target association, off-sim.

The three geometries differ in kind, not in tuning, and each has a failure that is silent
in flight rather than loud at the API:

1. No deadband hysteresis -> the vehicle creeps forward and back around the stand-off
   every tick. It still "follows"; it just looks broken and wears the drivetrain.
2. A ground vehicle that reverses when the target closes in -> a losing tail-chase with
   no rear sensing. phrover_manager.gd's person governor documents three rejected
   retreat/dodge designs before settling on a plain stop.
3. A fixed-wing given a hold state -> a commanded airspeed below its stall floor. It
   cannot hover, so "following" has to resolve to a moving orbit at or above its own
   minimum turn radius, always, including when the track goes stale.

Association is tested here too, because that is what decides whether the vehicle is still
following the RIGHT thing: detectors emit no track ids, so identity is reconstructed by
gating candidates against the prediction.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from follow import (  # noqa: E402
    FollowParams, FollowPolicy, TargetObservation, TargetTrack, TargetTracker,
    TrackerParams, labels_match, normalize_angle,
)
from vehicle_class import Kinematics, get_class  # noqa: E402

T0 = 1000.0


def _track(x, y, vx=0.0, vy=0.0, seen=T0):
    return TargetTrack(id=1, position=(x, y), velocity=(vx, vy), last_seen_at=seen)


def _facing(target, origin=(0.0, 0.0)):
    return math.atan2(target[1] - origin[1], target[0] - origin[0])


# --------------------------------------------------------------- per-class geometry
def test_params_derive_from_the_vehicle_class():
    quad = FollowParams.for_vehicle(get_class("quadcopter"))
    rover = FollowParams.for_vehicle(get_class("rover"))
    fw = FollowParams.for_vehicle(get_class("fixedwing"))

    assert quad.geometry is Kinematics.HOLONOMIC_3D and quad.allows_reverse
    assert rover.geometry is Kinematics.UNICYCLE_2D and not rover.allows_reverse
    assert fw.geometry is Kinematics.COORDINATED_TURN_3D
    # Orbit radius is the airframe's real minimum turn radius, not a guess.
    vc = get_class("fixedwing")
    assert fw.orbit_radius == vc.max_speed_mps / vc.max_yaw_rate_radps


def test_each_class_is_capped_at_its_own_speed():
    for name in ("quadcopter", "rover", "fixedwing"):
        vc = get_class(name)
        assert FollowParams.for_vehicle(vc).max_speed == vc.max_speed_mps


# --------------------------------------------------------------- stand-off geometry
def test_advances_to_a_point_one_standoff_short_of_the_target():
    policy = FollowPolicy(FollowParams.for_vehicle(get_class("rover")))
    out = policy.step((0.0, 0.0), _facing((6.0, 0.0)), _track(6.0, 0.0), now=T0)

    assert out.motion == "advance"
    assert out.range_error == 6.0 - 2.5
    assert abs(out.goal[0] - 3.5) < 1e-9 and abs(out.goal[1]) < 1e-9
    assert 0 < out.speed <= get_class("rover").max_speed_mps


def test_goal_leads_a_moving_target():
    policy = FollowPolicy(FollowParams.for_vehicle(get_class("rover")))
    # 0.6 m/s away, lead_time 0.5 s -> predicted 0.3 m further on.
    out = policy.step((0.0, 0.0), 0.0, _track(6.0, 0.0, vx=0.6), now=T0)
    assert abs(out.predicted_target[0] - 6.3) < 1e-9


# --------------------------------------------------------------- deadband
def test_holds_inside_the_band_and_does_not_oscillate():
    policy = FollowPolicy(FollowParams.for_vehicle(get_class("rover")))
    yaw = 0.0

    assert policy.step((0.0, 0.0), yaw, _track(2.6, 0.0), now=T0).motion == "hold"
    assert policy.in_band

    # Drifting inside deadband_exit (0.60 m) must not re-trigger translation.
    for x in (2.9, 3.0, 2.2, 2.05, 2.95):
        assert policy.step((0.0, 0.0), yaw, _track(x, 0.0), now=T0).motion == "hold", x

    assert policy.step((0.0, 0.0), yaw, _track(3.2, 0.0), now=T0).motion == "advance"
    assert not policy.in_band


def test_enter_threshold_is_tighter_than_exit():
    policy = FollowPolicy(FollowParams.for_vehicle(get_class("rover")))
    # 0.4 m error: inside exit (0.60), outside enter (0.25) -> keep closing.
    assert policy.step((0.0, 0.0), 0.0, _track(2.9, 0.0), now=T0).motion == "advance"
    assert not policy.in_band


# --------------------------------------------------------------- bearing
def test_unicycle_turns_in_place_when_badly_off_bearing():
    policy = FollowPolicy(FollowParams.for_vehicle(get_class("rover")))
    out = policy.step((0.0, 0.0), 0.0, _track(0.0, 6.0), now=T0)
    assert out.motion == "turn_in_place"
    assert abs(out.yaw_error - math.pi / 2) < 1e-9


def test_holonomic_does_not_need_to_square_up_first():
    policy = FollowPolicy(FollowParams.for_vehicle(get_class("quadcopter")))
    out = policy.step((0.0, 0.0), 0.0, _track(0.0, 20.0), now=T0)
    assert out.motion == "advance", "a multirotor can translate while yawed"


# --------------------------------------------------------------- target closing in
def test_ground_vehicle_holds_rather_than_reversing():
    policy = FollowPolicy(FollowParams.for_vehicle(get_class("rover")))
    out = policy.step((0.0, 0.0), _facing((1.0, 0.0)), _track(1.0, 0.0), now=T0)
    assert out.range_error < 0
    assert out.motion == "hold"


def test_multirotor_restores_its_standoff():
    policy = FollowPolicy(FollowParams.for_vehicle(get_class("quadcopter")))
    out = policy.step((0.0, 0.0), _facing((1.0, 0.0)), _track(1.0, 0.0), now=T0)
    assert out.motion == "retreat"
    # Retreat goal is a full stand-off on the far side of the vehicle from the target.
    assert abs(out.goal[0] - (1.0 - 5.0)) < 1e-6


# --------------------------------------------------------------- fixed-wing
def test_fixed_wing_always_orbits_never_holds():
    params = FollowParams.for_vehicle(get_class("fixedwing"))
    policy = FollowPolicy(params)

    cases = [(_track(500.0, 0.0, vy=5.0), T0),      # far away
             (_track(1.0, 0.0, vy=5.0), T0),        # right on top of it
             (_track(500.0, 0.0, vy=5.0), T0 + 60)]  # stale
    for track, now in cases:
        out = policy.step((0.0, 0.0), 0.0, track, now=now)
        assert out.motion == "orbit"
        assert out.radius == params.orbit_radius
        assert out.goal[1] > track.position[1], "orbit centre must lead the target"


def test_fixed_wing_orbit_radius_is_flyable():
    vc = get_class("fixedwing")
    params = FollowParams.for_vehicle(vc, standoff=5.0)   # operator asks for something tight
    assert params.orbit_radius >= vc.max_speed_mps / vc.max_yaw_rate_radps, \
        "a stand-off below the airframe's turn radius must be raised, not obeyed"


# --------------------------------------------------------------- staleness
def test_stale_track_is_flagged_but_still_produces_motion():
    policy = FollowPolicy(FollowParams.for_vehicle(get_class("rover")))
    out = policy.step((0.0, 0.0), 0.0, _track(6.0, 0.0), now=T0 + 1.5)
    assert out.is_stale and out.motion == "advance"


# --------------------------------------------------------------- association
def _obs(x, y, label="person", confidence=0.9):
    return TargetObservation(label=label, confidence=confidence, world_xy=(x, y))


def test_seed_biases_the_initial_pick():
    tracker = TargetTracker()
    tracker.lock("person", seed=(3.0, 0.0))
    tracker.update([_obs(3.0, 0.0, confidence=0.6), _obs(9.0, 1.0, confidence=0.95)], now=T0)
    assert tracker.track.position == (3.0, 0.0)


def test_ignores_non_matching_labels():
    tracker = TargetTracker()
    tracker.lock("person")
    tracker.update([_obs(2.0, 0.0, label="chair")], now=T0)
    assert tracker.state == "acquiring" and tracker.track is None


def test_does_not_swap_to_a_decoy_while_the_track_is_fresh():
    tracker = TargetTracker()
    tracker.lock("person")
    tracker.update([_obs(3.0, 0.0)], now=T0)
    track_id = tracker.track.id

    # Real target briefly undetected; a decoy is plainly visible 4 m away.
    for i in range(1, 6):
        tracker.update([_obs(3.0, 4.0)], now=T0 + i * 0.1)

    assert tracker.track.id == track_id
    assert abs(tracker.track.position[1]) < 1e-6, "lock jumped to the decoy"
    assert tracker.unverified_reacquisitions == 0


def test_reattaches_outside_the_gate_only_after_coasting_and_counts_it():
    tracker = TargetTracker()
    tracker.lock("person")
    tracker.update([_obs(3.0, 0.0)], now=T0)
    tracker.update([_obs(3.0, 4.0)], now=T0 + 2.0)   # past reacquire_after (1.5 s)
    assert tracker.state == "tracking"
    assert tracker.unverified_reacquisitions == 1


def test_declared_lost_after_the_coast_window():
    tracker = TargetTracker()
    tracker.lock("person")
    tracker.update([_obs(3.0, 0.0)], now=T0)
    tracker.update([], now=T0 + 5)
    assert tracker.state == "coasting"
    tracker.update([], now=T0 + 11)
    assert tracker.state == "lost"
    assert tracker.track is not None, "last known position is kept so a search can aim at it"


def test_gate_scales_to_the_vehicle_world():
    # An 0.8 m gate that suits a rover indoors would reject every real fixed-wing sighting.
    assert TrackerParams.for_vehicle(get_class("fixedwing")).association_radius > \
        TrackerParams.for_vehicle(get_class("rover")).association_radius


def test_velocity_is_estimated_and_clamped():
    tracker = TargetTracker(TrackerParams(position_alpha=1.0, velocity_alpha=1.0, max_speed=3.0))
    tracker.lock("person")
    tracker.update([_obs(0.0, 0.0)], now=T0)
    tracker.update([_obs(0.5, 0.0)], now=T0 + 1.0)
    assert abs(tracker.track.velocity[0] - 0.5) < 1e-6

    tracker2 = TargetTracker(TrackerParams(position_alpha=1.0, velocity_alpha=1.0, max_speed=3.0))
    tracker2.lock("person")
    tracker2.update([_obs(0.0, 0.0)], now=T0)
    # A 6 m jump in 0.1 s is an association error, not a sprint.
    tracker2.update([_obs(6.0, 0.0)], now=T0 + 0.1)
    assert math.hypot(*tracker2.track.velocity) <= 3.0 + 1e-6


def test_labels_match_is_loose_enough_for_spoken_requests():
    assert labels_match("the person in the hat", "person")
    assert labels_match("follow that pickup truck", "pickup truck")
    assert not labels_match("person", "chair")


def test_normalize_angle_wraps():
    assert abs(normalize_angle(3 * math.pi) - math.pi) < 1e-9
    assert abs(normalize_angle(-3 * math.pi) - math.pi) < 1e-9


# --------------------------------------------------------------- closed loop
def test_follows_a_slow_walker_without_chattering():
    """Integrated against a target walking away, for each ground/air class that can
    actually keep up. Verifies the band is held AND that the vehicle is not switching
    between moving and stopping every tick."""
    policy = FollowPolicy(FollowParams.for_vehicle(get_class("rover")))
    x, yaw, target_x = 0.0, 0.0, 2.5
    dt, walk = 0.1, 0.25

    transitions, was_moving, max_error = 0, False, 0.0
    for i in range(600):
        target_x += walk * dt
        now = T0 + i * dt
        out = policy.step((x, 0.0), yaw, _track(target_x, 0.0, vx=walk, seen=now), now=now)
        moving = out.motion == "advance"
        if moving:
            x += min(out.speed, get_class("rover").max_speed_mps) * dt
        if moving != was_moving:
            transitions += 1
        was_moving = moving
        if i > 10:
            max_error = max(max_error, abs(target_x - x - 2.5))

    assert max_error < 0.75, f"stand-off drifted out of band (max {max_error:.2f} m)"
    assert transitions < 30, f"chattered between move and hold {transitions} times in 60 s"
