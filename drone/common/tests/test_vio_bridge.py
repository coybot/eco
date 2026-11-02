"""vio_bridge: frame conversions, the MAVLink it sends, rate, watchdog, reset counter.

The conventions checked here are the ones ArduPilot 4.7.1 actually reads
(GCS_MAVLINK::handle_odometry): ODOMETRY in MAV_FRAME_LOCAL_FRD / BODY_FRD,
position NED, q body->NED (w, x, y, z), velocity body FRD rotated by q,
posErr = sqrt(c[0]+c[6]+c[11]). A sign or axis slip in any of them flies the
aircraft the wrong way on VIO, so each is pinned with a rotation whose answer
is known by hand, plus random round trips.

Run: cd eco && python3 -m pytest drone/common/tests/test_vio_bridge.py -v
"""

import math
import random
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vio_bridge as vb  # noqa: E402

D = math.radians


def close(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def mclose(a, b, tol=1e-9):
    return all(close(ra, rb, tol) for ra, rb in zip(a, b))


def quat_about_z(rad):
    return (math.cos(rad / 2), 0.0, 0.0, math.sin(rad / 2))


# -- rotation primitives ---------------------------------------------------------

def test_quaternion_matrix_round_trip():
    rng = random.Random(3)
    for _ in range(200):
        r = vb.euler_to_matrix(rng.uniform(-3, 3), rng.uniform(-1.5, 1.5), rng.uniform(-3, 3))
        assert mclose(vb.quat_to_matrix(vb.matrix_to_quat(r)), r, 1e-9)


def test_euler_is_aerospace_zyx():
    # yaw 90 deg: body forward (x) points east (+y in NED)
    assert close(vb.mat_vec(vb.euler_to_matrix(0, 0, D(90)), (1, 0, 0)), (0, 1, 0))
    # pitch +30 deg: nose up, so forward has a negative (up) NED z
    fwd = vb.mat_vec(vb.euler_to_matrix(0, D(30), 0), (1, 0, 0))
    assert fwd[2] == pytest.approx(-0.5)
    # roll +90 deg: right wing (y) points down
    assert close(vb.mat_vec(vb.euler_to_matrix(D(90), 0, 0), (0, 1, 0)), (0, 0, 1))
    r, p, y = vb.matrix_to_euler(vb.euler_to_matrix(0.1, -0.2, 2.5))
    assert (r, p, y) == pytest.approx((0.1, -0.2, 2.5))


def test_every_frame_table_entry_is_a_proper_rotation():
    for name, m in list(vb.WORLD_FRAMES.items()) + list(vb.CHILD_FRAMES.items()):
        det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
               - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
               + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
        assert det == pytest.approx(1.0), name
        assert mclose(vb.mat_mul(m, vb.mat_T(m)), vb.IDENTITY), name


# -- conversions with hand-known answers ----------------------------------------

def test_enu_position_becomes_ned():
    c = vb.FrameConvention('ENU', 'FLU')
    assert close(c.position((1.0, 2.0, 3.0)), (2.0, 1.0, -3.0))   # east 1, north 2, up 3


def test_flu_world_position_becomes_frd():
    c = vb.FrameConvention('FLU', 'FLU')
    assert close(c.position((1.0, 2.0, 3.0)), (1.0, -2.0, -3.0))  # 2 m LEFT is -2 m right


def test_ros_identity_in_enu_faces_east():
    """REP-103: base_link aligned with an ENU map faces east: NED yaw +90 deg."""
    r = vb.FrameConvention('ENU', 'FLU').attitude((1, 0, 0, 0))
    assert vb.matrix_to_euler(r) == pytest.approx((0.0, 0.0, D(90)))


def test_ros_identity_in_flu_odom_faces_local_north():
    r = vb.FrameConvention('FLU', 'FLU').attitude((1, 0, 0, 0))
    assert mclose(r, vb.IDENTITY)


@pytest.mark.parametrize("heading_deg", [0, 30, 90, 179, -120])
def test_enu_yaw_maps_to_compass_heading(heading_deg):
    """ENU yaw is counter-clockwise from east; NED yaw clockwise from north."""
    enu_yaw = D(90 - heading_deg)
    r = vb.FrameConvention('ENU', 'FLU').attitude(quat_about_z(enu_yaw))
    yaw = vb.matrix_to_euler(r)[2]
    assert math.cos(yaw - D(heading_deg)) == pytest.approx(1.0)


def test_flu_odom_left_turn_is_negative_ned_yaw():
    r = vb.FrameConvention('FLU', 'FLU').attitude(quat_about_z(D(30)))   # 30 deg left (CCW from above)
    assert vb.matrix_to_euler(r)[2] == pytest.approx(D(-30))


def test_ros_pitch_up_stays_pitch_up():
    """FLU pitch: positive rotation about +y(left) lowers the nose; it must come out nose DOWN."""
    q = (math.cos(D(10) / 2), 0.0, math.sin(D(10) / 2), 0.0)
    roll, pitch, yaw = vb.matrix_to_euler(vb.FrameConvention('FLU', 'FLU').attitude(q))
    assert pitch == pytest.approx(D(-10)) and roll == pytest.approx(0) and yaw == pytest.approx(0)


def test_flu_body_velocity_becomes_frd():
    c = vb.FrameConvention('ENU', 'FLU')
    r = c.attitude((1, 0, 0, 0))
    assert close(c.body_velocity((1.0, 0.5, 0.2), r), (1.0, -0.5, -0.2))   # fwd, left, up


def test_world_frame_velocity_is_rotated_into_the_body():
    """Facing east (ENU identity) and moving north in ENU: that is to the aircraft's left."""
    c = vb.FrameConvention('ENU', 'FLU', velocity_frame='world')
    r = c.attitude((1, 0, 0, 0))
    assert close(c.body_velocity((0.0, 1.0, 0.0), r), (0.0, -1.0, 0.0), 1e-12)


def test_forward_optical_camera_axes():
    """Optical frame aligned with an FRD world means the lens looks DOWN the world z:
    the aircraft would be nose down. Body forward = optical z."""
    r = vb.FrameConvention('FRD', 'OPTICAL').attitude((1, 0, 0, 0))
    assert close(vb.mat_vec(r, (1, 0, 0)), (0, 0, 1))
    assert vb.matrix_to_euler(r)[1] == pytest.approx(D(-90))


def test_down_looking_camera_mount():
    """D435i looking straight down, top of the image toward the nose, aircraft level
    facing north: lens z = down, image right x = east, image down y = south.
    With mount (0, -90, 0) that must convert to a level, north-facing aircraft."""
    r_cam = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))   # columns: x=E, y=S, z=D
    c = vb.FrameConvention('NED', 'OPTICAL', mount_rpy_deg=(0, -90, 0))
    assert mclose(c.attitude(vb.matrix_to_quat(r_cam)), vb.IDENTITY, 1e-9)
    # moving forward (north) is image-up (-y) in the camera
    assert close(c.body_velocity((0.0, -1.0, 0.0), vb.IDENTITY), (1.0, 0.0, 0.0), 1e-12)


@pytest.mark.parametrize("world", sorted(vb.WORLD_FRAMES))
@pytest.mark.parametrize("child", sorted(vb.CHILD_FRAMES))
def test_random_round_trips_every_convention(world, child):
    """Publish a known aircraft pose in a convention (the simulated source's inverse
    mapping), convert it back: the attitude, position and velocity must survive."""
    rng = random.Random(hash((world, child)) & 0xffff)
    for _ in range(25):
        mount = (rng.uniform(-180, 180), rng.uniform(-89, 89), rng.uniform(-180, 180))
        conv = vb.FrameConvention(world, child, mount_rpy_deg=mount)
        r_nb = vb.euler_to_matrix(rng.uniform(-0.6, 0.6), rng.uniform(-0.6, 0.6), rng.uniform(-3, 3))
        p = (rng.uniform(-50, 50), rng.uniform(-50, 50), rng.uniform(-10, 0))
        v_ned = (rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(-2, 2))
        truth = lambda: (1.0, p, vb.matrix_to_quat(r_nb), v_ned)  # noqa: E731
        src = vb.NoisyTruthSource(None, truth, convention=conv, pos_noise_m=0, drift_m_per_sqrt_s=0,
                                  yaw_drift_deg_per_sqrt_s=0, vel_noise_mps=0)
        s = src.sample()
        r = conv.attitude(s.orientation)
        assert mclose(r, r_nb, 1e-9)
        assert close(conv.position(s.position), p, 1e-9)
        assert close(vb.mat_vec(r, conv.body_velocity(s.velocity, r)), v_ned, 1e-9)


def test_covariance_is_rotated_and_packed_the_way_ardupilot_reads_it():
    c = vb.FrameConvention('ENU', 'FLU')
    cov = c.pose_cov([1.0, 4.0, 9.0, 0.01, 0.02, 0.03])       # ENU x, y, z variances
    ut = vb.upper_triangle(cov)
    assert len(ut) == 21
    assert (ut[0], ut[6], ut[11]) == pytest.approx((4.0, 1.0, 9.0))      # N, E, D
    assert (ut[15], ut[18], ut[20]) == pytest.approx((0.02, 0.01, 0.03))
    # ArduPilot: posErr = sqrt(c[0] + c[6] + c[11]), rotation-invariant
    assert vb.position_std_m(cov) == pytest.approx(math.sqrt(14.0))
    full = c.pose_cov([float(i == j) * (i + 1) for i in range(6) for j in range(6)])
    assert full[0][0] == pytest.approx(2.0) and full[1][1] == pytest.approx(1.0)


def test_unknown_covariance_is_nan_first():
    ut = vb.upper_triangle(None)
    assert math.isnan(ut[0]) and len(ut) == 21
    assert vb.FrameConvention().pose_cov([0.1, float('nan')] + [0.0] * 4) is None


def test_ros_stamp_to_monotonic():
    assert vb.ros_stamp_to_monotonic(99.9, 100.0, 50.0) == pytest.approx(49.9)
    assert vb.ros_stamp_to_monotonic(100.5, 100.0, 50.0) == 50.0   # future stamp: now


def test_bad_frame_names_are_refused():
    with pytest.raises(ValueError):
        vb.FrameConvention('NWU', 'FLU')
    with pytest.raises(ValueError):
        vb.FrameConvention('ENU', 'base_link')


# -- the bridge ---------------------------------------------------------------

class FakeMav:
    def __init__(self, odometry=True):
        self.sent = []
        self.odometry = odometry     # False: a MAVLink 1 dialect, which has no ODOMETRY

    def __getattr__(self, name):
        if name == 'odometry_send' and not self.__dict__.get('odometry', True):
            raise AttributeError(name)
        if name.endswith('_send'):
            return lambda *a, **k: self.sent.append((name[:-5], a))
        raise AttributeError(name)


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def make(clock=None, mav=None, **kw):
    clock = clock or Clock()
    mav = mav if mav is not None else FakeMav()
    m = types.SimpleNamespace(mav=mav)
    kw.setdefault('recover_samples', 1)
    b = vb.VioBridge(vb.FrameConvention('ENU', 'FLU'), sender=lambda fn: fn(m), clock=clock,
                     log=lambda s: None, **kw)
    return b, clock, mav


def feed(b, clock, n=1, dt=1 / 30, pos=(0.0, 0.0, 1.0), vel=(0.0, 0.0, 0.0), step=True, **kw):
    for _ in range(n):
        clock.t += dt
        b.submit(vb.OdomSample(t=clock.t, position=pos, orientation=(1, 0, 0, 0), velocity=vel, **kw))
        if step:
            b.step()


def odoms(mav):
    return [a for n, a in mav.sent if n == 'odometry']


def test_mavlink_enum_values_match_pymavlink():
    pytest.importorskip("pymavlink")
    from pymavlink.dialects.v20 import ardupilotmega as c   # SET_EKF_SOURCE_SET is ardupilotmega's
    assert vb.MAV_FRAME_LOCAL_FRD == c.MAV_FRAME_LOCAL_FRD
    assert vb.MAV_FRAME_BODY_FRD == c.MAV_FRAME_BODY_FRD
    assert vb.MAV_ESTIMATOR_TYPE_VIO == c.MAV_ESTIMATOR_TYPE_VIO
    assert vb.MAV_CMD_SET_EKF_SOURCE_SET == c.MAV_CMD_SET_EKF_SOURCE_SET


def test_odometry_message_fields():
    b, clock, mav = make()
    clock.t += 0.01
    b.submit(vb.OdomSample(t=clock.t, position=(1.0, 2.0, 3.0), orientation=(1, 0, 0, 0),
                           velocity=(1.0, 0.5, 0.2), quality=77,
                           pose_covariance=[0.01, 0.04, 0.09, 0.001, 0.001, 0.001]))
    assert b.step() is True
    (a,) = odoms(mav)
    usec, frame, child, x, y, z, q, vx, vy, vz = a[:10]
    assert usec == int(clock.t * 1e6)
    assert (frame, child) == (20, 12)                  # LOCAL_FRD / BODY_FRD: what ArduPilot accepts
    assert (x, y, z) == pytest.approx((2.0, 1.0, -3.0))
    assert vb.matrix_to_euler(vb.quat_to_matrix(q))[2] == pytest.approx(D(90))
    assert (vx, vy, vz) == pytest.approx((1.0, -0.5, -0.2))
    pose_cov, reset, est, quality = a[13], a[15], a[16], a[17]
    assert pose_cov[0] == pytest.approx(0.04) and pose_cov[6] == pytest.approx(0.01)
    assert (reset, est, quality) == (0, 3, 77)


def test_odometry_encodes_as_real_mavlink2():
    """The arguments must be what pymavlink's MAVLink 2 encoder accepts, and decode back."""
    pytest.importorskip("pymavlink")
    from pymavlink.dialects.v20 import ardupilotmega as mav2

    class Buf:
        def __init__(self):
            self.data = b''

        def write(self, d):
            self.data += d
    buf = Buf()
    link = mav2.MAVLink(buf, srcSystem=255)
    b = vb.VioBridge(vb.FrameConvention('FLU', 'FLU'), sender=lambda fn: fn(types.SimpleNamespace(mav=link)),
                     clock=Clock(), log=lambda s: None, recover_samples=1)
    b.submit(vb.OdomSample(t=100.02, position=(1.0, 2.0, 3.0), orientation=quat_about_z(D(30)),
                           velocity=(1.0, 0.0, 0.0), quality=55))
    assert b.step(100.03) is True
    parser = mav2.MAVLink(None)
    msgs = parser.parse_buffer(buf.data)
    (m,) = msgs
    assert m.get_type() == 'ODOMETRY'
    assert (m.x, m.y, m.z) == pytest.approx((1.0, -2.0, -3.0))
    assert m.frame_id == 20 and m.child_frame_id == 12 and m.quality == 55 and m.estimator_type == 3
    assert math.isnan(m.rollspeed)                     # unknown rates go out as NaN
    assert math.isnan(m.pose_covariance[0])            # unknown covariance: ArduPilot uses VISO_*_NSE


def test_vision_messages_carry_ned_pose_and_ned_velocity():
    b, clock, mav = make(message='vision')
    clock.t += 0.01
    b.submit(vb.OdomSample(t=clock.t, position=(1.0, 2.0, 3.0), orientation=(1, 0, 0, 0),
                           velocity=(1.0, 0.0, 0.0)))
    b.step()
    names = [n for n, _ in mav.sent]
    assert names == ['vision_position_estimate', 'vision_speed_estimate']
    pos = mav.sent[0][1]
    assert pos[1:4] == pytest.approx((2.0, 1.0, -3.0)) and pos[6] == pytest.approx(D(90))
    spd = mav.sent[1][1]
    assert spd[1:4] == pytest.approx((0.0, 1.0, 0.0), abs=1e-12)   # facing east, forward = east


def test_mavlink1_link_falls_back_to_vision_messages():
    b, clock, mav = make(mav=FakeMav(odometry=False))
    feed(b, clock)
    assert [n for n, _ in mav.sent][0] == 'vision_position_estimate'


def test_sends_only_new_samples_at_most_once_per_tick():
    b, clock, mav = make()
    feed(b, clock, n=1)
    for _ in range(5):              # ticks with nothing new: nothing resent
        clock.t += 1 / 30
        b.step()
    assert len(odoms(mav)) == 1


def test_fast_source_is_downsampled_to_the_tick_rate():
    b, clock, mav = make()
    # 100 Hz source, 30 Hz ticks over one second
    t_end, next_tick = clock.t + 1.0, clock.t
    while clock.t < t_end:
        clock.t += 0.01
        b.submit(vb.OdomSample(t=clock.t, position=(0, 0, 0), orientation=(1, 0, 0, 0)))
        if clock.t >= next_tick:
            b.step()
            next_tick += 1 / 30
    assert 29 <= len(odoms(mav)) <= 31
    usecs = [a[0] for a in odoms(mav)]
    assert usecs == sorted(set(usecs))              # strictly increasing, never repeated


def test_thread_streams_at_the_configured_rate():
    import time
    sent = []
    b = vb.VioBridge(rate_hz=30, sender=lambda fn: sent.append(time.monotonic()), log=lambda s: None,
                     recover_samples=1, stall_timeout_s=0.5, max_latency_s=0.5)
    stop = [False]

    def produce():
        while not stop[0]:
            b.submit(vb.OdomSample(t=time.monotonic(), position=(0, 0, 0), orientation=(1, 0, 0, 0)))
            time.sleep(0.005)
    import threading
    th = threading.Thread(target=produce, daemon=True)
    th.start()
    b.start()
    time.sleep(1.2)
    b.stop()
    stop[0] = True
    th.join(1)
    # ~30 Hz: allow for scheduler noise on a loaded CI box
    rate = (len(sent) - 1) / (sent[-1] - sent[0])
    assert 24 <= rate <= 33, rate
    assert b.health()['rate_hz_measured'] > 20


def test_rate_must_respect_the_ekf_minimum_interval():
    with pytest.raises(ValueError):
        vb.VioBridge(rate_hz=60)       # EKF3 drops ExtNav samples < 20 ms apart


def test_out_of_order_and_invalid_samples_are_dropped():
    b, clock, mav = make()
    feed(b, clock)
    assert b.submit(vb.OdomSample(t=clock.t - 0.01, position=(0, 0, 0), orientation=(1, 0, 0, 0))) is False
    assert b.submit(vb.OdomSample(t=clock.t + 0.01, position=(float('nan'), 0, 0),
                                  orientation=(1, 0, 0, 0))) is False
    assert b.submit(vb.OdomSample(t=clock.t + 0.01, position=(0, 0, 0), orientation=(0, 0, 0, 0))) is False
    st = b.health()['stats']
    assert st['dropped_out_of_order'] == 1 and st['dropped_invalid'] == 2


# -- watchdog -----------------------------------------------------------------

def test_starts_only_after_enough_good_samples():
    b, clock, mav = make(recover_samples=5)
    feed(b, clock, n=4)
    assert not b.healthy and odoms(mav) == []
    feed(b, clock, n=1)
    assert b.healthy and len(odoms(mav)) == 1
    assert b.reset_counter == 0           # the first start is not a reset


def test_stall_stops_sending_then_resumes_with_a_reset():
    changes = []
    b, clock, mav = make(recover_samples=3, on_health_change=lambda ok, why: changes.append((ok, why)))
    feed(b, clock, n=5)
    assert b.healthy
    n_sent = len(odoms(mav))
    for _ in range(10):                   # 0.33 s of silence, ticking at 30 Hz
        clock.t += 1 / 30
        b.step()
    assert not b.healthy and 'stalled' in b.health()['reason']
    assert len(odoms(mav)) == n_sent
    feed(b, clock, n=2)
    assert not b.healthy, "needs 3 good samples to recover"
    feed(b, clock, n=1)
    assert b.healthy
    assert odoms(mav)[-1][15] == 1, "the first message after an outage is a reset"
    assert [ok for ok, _ in changes] == [True, False, True]


def test_unhealthy_within_the_stall_timeout_not_later():
    b, clock, mav = make(stall_timeout_s=0.25)
    feed(b, clock, n=3)
    clock.t += 0.24
    b.step()
    assert b.healthy
    clock.t += 0.02
    b.step()
    assert not b.healthy                  # before ArduPilot's own 300 ms VO timeout


def test_quality_collapse_stops_sending():
    b, clock, mav = make(min_quality=20)
    feed(b, clock, n=3, quality=80)
    n = len(odoms(mav))
    feed(b, clock, n=3, quality=5)
    assert not b.healthy and 'quality' in b.health()['reason']
    assert len(odoms(mav)) == n
    feed(b, clock, n=1, quality=80)
    assert b.healthy and odoms(mav)[-1][15] == 1


def test_covariance_blow_up_stops_sending():
    b, clock, mav = make(max_pos_std_m=1.0)
    feed(b, clock, n=2, pose_covariance=[0.01] * 3 + [0.001] * 3)
    assert b.healthy
    feed(b, clock, n=1, pose_covariance=[1.0] * 3 + [0.001] * 3)   # sqrt(3) m > 1 m
    assert not b.healthy and 'std' in b.health()['reason']


def test_stale_samples_are_not_sent():
    b, clock, mav = make(max_latency_s=0.3)
    feed(b, clock, n=2)
    n = len(odoms(mav))
    clock.t += 1.0
    # arrives now (so not a stall) but was valid 0.5 s ago: a backed-up pipeline
    b.submit(vb.OdomSample(t=clock.t - 0.5, position=(0, 0, 1), orientation=(1, 0, 0, 0)))
    b.step()
    assert not b.healthy and 'stale' in b.health()['reason']
    assert len(odoms(mav)) == n


def test_unflagged_jump_is_refused_flagged_one_is_a_reset():
    b, clock, mav = make(jump_m=0.5, recover_samples=3)
    feed(b, clock, n=3, pos=(0.0, 0.0, 1.0))
    feed(b, clock, n=1, pos=(2.0, 0.0, 1.0))           # 2 m in 33 ms, no reset: a glitch
    assert not b.healthy and 'jumped' in b.health()['reason']
    feed(b, clock, n=3, pos=(2.0, 0.0, 1.0))            # it stays there: a real relocalisation
    assert b.healthy and odoms(mav)[-1][15] == 1
    feed(b, clock, n=1, pos=(4.0, 0.0, 1.0), reset=True)   # flagged by the source: taken at once
    assert b.healthy and odoms(mav)[-1][15] == 2


def test_motion_consistent_with_velocity_is_not_a_jump():
    b, clock, mav = make(jump_m=0.5)
    x = 0.0
    for _ in range(30):                                  # 20 m/s east in ENU
        x += 20.0 / 30
        feed(b, clock, n=1, pos=(x, 0.0, 1.0), vel=(0.0, 0.0, 0.0))
    # FLU body, ENU world identity: forward = east, so velocity (20, 0, 0) body
    b2, c2, m2 = make(jump_m=0.5)
    x = 0.0
    for _ in range(30):
        x += 20.0 / 30
        feed(b2, c2, n=1, pos=(x, 0.0, 1.0), vel=(20.0, 0.0, 0.0))
    assert not b.healthy, "20 m/s with no velocity reported is a jump every sample"
    assert b2.healthy and len(odoms(m2)) == 30


def test_source_reset_counter_change_bumps_ours():
    b, clock, mav = make()
    feed(b, clock, n=2, reset_counter=7)
    assert odoms(mav)[-1][15] == 0                        # first value seen is just recorded
    feed(b, clock, n=1, reset_counter=8)
    assert odoms(mav)[-1][15] == 1
    feed(b, clock, n=1, reset_counter=8)
    assert odoms(mav)[-1][15] == 1


def test_force_reset_and_wrap_at_255():
    b, clock, mav = make()
    feed(b, clock)
    b._reset_counter = 255
    b.force_reset()
    feed(b, clock)
    assert odoms(mav)[-1][15] == 0                        # uint8 wraps


def test_fallback_source_set_is_requested_once_when_vio_is_lost(monkeypatch):
    import threading
    asked, done = [], threading.Event()
    fake_sdk = types.SimpleNamespace(_set_ekf_source_set=lambda n: (asked.append(n), done.set(), True)[-1])
    monkeypatch.setitem(sys.modules, 'drone_sdk', fake_sdk)
    b, clock, mav = make(fallback_source_set=2)
    feed(b, clock, n=3)
    for _ in range(12):
        clock.t += 1 / 30
        b.step()
    assert done.wait(2.0)
    assert asked == [2]
    feed(b, clock, n=2)                                   # recovery does NOT switch back by itself
    assert asked == [2]


def test_no_fallback_before_anything_was_ever_sent(monkeypatch):
    asked = []
    monkeypatch.setitem(sys.modules, 'drone_sdk',
                        types.SimpleNamespace(_set_ekf_source_set=lambda n: asked.append(n)))
    b, clock, mav = make(fallback_source_set=2)
    for _ in range(10):
        clock.t += 1 / 30
        b.step()
    assert asked == []


def test_send_errors_are_counted_not_raised():
    def boom(fn):
        raise OSError("serial gone")
    b = vb.VioBridge(sender=boom, clock=Clock(), log=lambda s: None, recover_samples=1)
    b.submit(vb.OdomSample(t=100.01, position=(0, 0, 0), orientation=(1, 0, 0, 0)))
    assert b.step(100.02) is False
    assert b.health()['stats']['send_errors'] == 1


# -- sources ------------------------------------------------------------------

def test_queue_source_feeds_the_bridge():
    import time
    b, clock, mav = make()
    qs = vb.QueueSource(b)
    try:
        qs.put({'t': clock.t + 0.01, 'position': (0, 0, 0), 'orientation': (1, 0, 0, 0)})
        end = time.time() + 2
        while b.health()['stats']['received'] == 0 and time.time() < end:
            time.sleep(0.01)
        assert b.health()['stats']['received'] == 1
    finally:
        qs.stop()


def test_odometry_msg_conversion():
    ns = types.SimpleNamespace
    msg = ns(pose=ns(pose=ns(position=ns(x=1, y=2, z=3), orientation=ns(x=0, y=0, z=0.6, w=0.8)),
                     covariance=[0.0] * 36),
             twist=ns(twist=ns(linear=ns(x=1, y=0, z=0), angular=ns(x=0, y=0, z=0.1)),
                      covariance=[0.01 if i % 7 == 0 else 0.0 for i in range(36)]))
    s = vb.odometry_msg_to_sample(msg, 5.0, quality=60)
    assert s.orientation == (0.8, 0, 0, 0.6)                # ROS (x, y, z, w) -> (w, x, y, z)
    assert s.pose_covariance is None                        # all zeros = not filled in
    assert s.velocity_covariance[0] == 0.01 and s.quality == 60 and s.t == 5.0


def test_ros_source_says_clearly_when_ros_is_missing():
    if vb.ROS_AVAILABLE:
        pytest.skip("ROS 2 is installed here")
    with pytest.raises(ImportError, match="rclpy"):
        vb.Ros2OdometrySource(None)


def test_simulated_source_drift_is_seeded_and_reported():
    truth_t = [0.0]

    def truth():
        truth_t[0] += 0.1
        return truth_t[0], (10.0, 0.0, -2.0), (1, 0, 0, 0), (0.0, 0.0, 0.0)
    b, clock, mav = make()
    a = vb.NoisyTruthSource(b, truth, seed=4, drift_m_per_sqrt_s=0.05, pos_noise_m=0.0, yaw_drift_deg_per_sqrt_s=0)
    for _ in range(100):
        a.sample()
    bias, _ = a.drift_ned()
    truth_t[0] = 0.0
    b2 = vb.NoisyTruthSource(b, truth, seed=4, drift_m_per_sqrt_s=0.05, pos_noise_m=0.0,
                             yaw_drift_deg_per_sqrt_s=0)
    for _ in range(100):
        s = b2.sample()
    assert b2.drift_ned()[0] == bias                         # same seed, same drift
    conv = b.convention
    assert close(conv.position(s.position), b2.believed((10.0, 0.0, -2.0)), 1e-9)
    b2.inject_jump((1.0, 0.0, 0.0))
    s2 = b2.sample()
    assert s2.reset_counter == s.reset_counter + 1
