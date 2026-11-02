"""drone_sdk's GPS-free half: EKF origin, local NED moves, the streaming send.

Against a fake MAVLink connection installed as drone_sdk._master (so the real
_connect/_mav_send/_mav_recv/_fc_cached paths run) and a fake clock. What the
fake FC does is modelled on ArduCopter 4.7.1: SET_GPS_GLOBAL_ORIGIN is dropped
silently until the EKF has initialised (NavEKF3_core::setOriginLLH), a second
origin is refused, and GPS_GLOBAL_ORIGIN is answered only once one is set.

Run: cd eco && python3 -m pytest drone/common/tests/test_sdk_local_frame.py -v
"""

import math
import sys
import threading
import time
import types
from pathlib import Path

import pytest

pytest.importorskip("pymavlink")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drone_sdk as sdk  # noqa: E402
import px4_compat  # noqa: E402


class Msg(types.SimpleNamespace):
    def get_type(self):
        return self.mtype

    def get_srcSystem(self):
        return 1

    def get_srcComponent(self):
        return 1


class FakeFC:
    """Just enough of a pymavlink connection and an ArduCopter behind it."""

    def __init__(self, clock, origin_after_sets=1, origin=None, local=(0.0, 0.0, 0.0)):
        self.clock = clock
        self.sent = []
        self.queue = []
        self.origin = origin                     # (lat_e7, lon_e7, alt_mm) once set
        self.origin_after_sets = origin_after_sets
        self.sets_seen = 0
        self.local = list(local)                 # LOCAL_POSITION_NED x, y, z
        self.target_system, self.target_component = 1, 1
        self.messages = {}
        self.sysid_state = {1: types.SimpleNamespace(messages={})}
        self.message_hooks = []
        fc = self

        class Mav:
            def __getattr__(self, name):
                if name.endswith('_send'):
                    return lambda *a, **k: fc._on_send(name[:-5], a)
                raise AttributeError(name)
        self.mav = Mav()

    # -- what the FC does with what it is sent
    def _on_send(self, name, a):
        self.sent.append((name, a))
        if name == 'set_gps_global_origin':
            self.sets_seen += 1
            if self.origin is None and self.sets_seen >= self.origin_after_sets:
                self.origin = (a[1], a[2], a[3])     # ignored until the EKF is up, then once only
        elif name == 'command_long' and a[2] == sdk.MAV_CMD_REQUEST_MESSAGE and a[4] == 49:
            if self.origin is not None:
                self._queue('GPS_GLOBAL_ORIGIN', latitude=self.origin[0], longitude=self.origin[1],
                            altitude=self.origin[2])
        elif name == 'command_long' and a[2] == 42007:
            self._queue('COMMAND_ACK', command=42007, result=0 if 1 <= a[4] <= 3 else 2)
        elif name == 'set_position_target_local_ned':
            self.target = (a[5], a[6], a[7])

    def _queue(self, mtype, **f):
        self.queue.append(Msg(mtype=mtype, _timestamp=self.clock[0], **f))

    def push_local(self):
        m = Msg(mtype='LOCAL_POSITION_NED', x=self.local[0], y=self.local[1], z=self.local[2],
                vx=0, vy=0, vz=0, _timestamp=self.clock[0])
        self.sysid_state[1].messages['LOCAL_POSITION_NED'] = m
        self.messages['LOCAL_POSITION_NED'] = m

    def recv_match(self, type=None, blocking=False, timeout=None):
        for i, m in enumerate(self.queue):
            if type is None or m.mtype == type:
                self.queue.pop(i)
                self.sysid_state[1].messages[m.mtype] = m
                return m
        if blocking and timeout:
            self.clock[0] += timeout
        return None

    def write(self, buf):
        self.sent.append(('write', buf))


@pytest.fixture
def fc(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr('time.time', lambda: clock[0])
    monkeypatch.setattr('time.sleep', lambda s: clock.__setitem__(0, clock[0] + max(s, 0.01)))
    f = FakeFC(clock)
    monkeypatch.setattr(sdk, '_master', f)
    monkeypatch.setattr(sdk, '_autopilot', px4_compat.AUTOPILOT_ARDUPILOT)
    monkeypatch.setattr(sdk, '_last_stream_request', 0.0)
    monkeypatch.setattr(sdk, '_pilot_control', None)
    monkeypatch.setattr(sdk, '_ekf_origin', None)
    monkeypatch.setattr(sdk, '_battery_guard_trip', None)
    monkeypatch.setattr(sdk, '_battery_gates_apply', lambda: False)
    sdk._abort_event.clear()
    yield f
    sdk._abort_event.clear()


def sent(f, name):
    return [a for n, a in f.sent if n == name]


# -- EKF origin ---------------------------------------------------------------

def test_set_ekf_origin_retries_until_the_ekf_takes_it(fc):
    fc.origin_after_sets = 3          # the first two arrive before the EKF has initialised
    assert sdk.set_ekf_origin(-35.363261, 149.165230, 584.0, timeout_s=30) is True
    sets = sent(fc, 'set_gps_global_origin')
    assert len(sets) == 3
    target, lat, lon, alt = sets[0][:4]
    assert (target, lat, lon, alt) == (1, -353632610, 1491652300, 584000)   # degE7, mm
    assert sdk._ekf_origin == pytest.approx((-35.363261, 149.16523, 584.0))
    assert sdk.get_ekf_origin() == pytest.approx((-35.363261, 149.16523, 584.0))


def test_set_ekf_origin_when_already_there_sends_nothing(fc):
    fc.origin = (-353632610, 1491652300, 584000)
    assert sdk.set_ekf_origin(-35.363261, 149.165230, 584.0) is True
    assert sent(fc, 'set_gps_global_origin') == []


def test_set_ekf_origin_refuses_to_pretend_when_the_fc_has_another(fc):
    fc.origin = (-350000000, 1490000000, 0)        # e.g. from GPS earlier
    assert sdk.set_ekf_origin(-35.363261, 149.165230, 584.0) is False
    assert sent(fc, 'set_gps_global_origin') == []


def test_set_ekf_origin_gives_up_if_never_confirmed(fc):
    fc.origin_after_sets = 10 ** 6
    assert sdk.set_ekf_origin(-35.363261, 149.165230, timeout_s=5) is False


@pytest.mark.parametrize("lat,lon", [(0, 0), (91, 10), (10, 181)])
def test_set_ekf_origin_rejects_non_locations(fc, lat, lon):
    assert sdk.set_ekf_origin(lat, lon) is False
    assert fc.sent == []


def test_get_ekf_origin_none_when_the_fc_has_none(fc):
    assert sdk.get_ekf_origin(timeout=2.0) is None


# -- local position and moves --------------------------------------------------

def test_get_local_position_is_north_east_up(fc):
    fc.local = [1.5, -2.0, -3.25]
    fc.push_local()
    assert sdk.get_local_position() == pytest.approx((1.5, -2.0, 3.25))


def test_get_local_position_none_without_one(fc):
    assert sdk.get_local_position() is None


def test_goto_local_sends_a_local_ned_position_target(fc):
    fc.push_local()
    assert sdk.goto_local(4.0, -3.0, 2.5, wait=False) is True
    (a,) = sent(fc, 'set_position_target_local_ned')
    _, target_sys, target_comp, frame, mask, x, y, z = a[:8]
    assert (target_sys, target_comp) == (1, 1)
    assert frame == 1                                      # MAV_FRAME_LOCAL_NED
    assert mask == 0b0000111111111000                      # position only, yaw left to the FC
    assert (x, y, z) == pytest.approx((4.0, -3.0, -2.5))   # up 2.5 m is z = -2.5 m


def test_goto_local_holds_a_yaw_when_asked(fc):
    fc.push_local()
    sdk.goto_local(1.0, 1.0, 2.0, yaw_deg=450, wait=False)
    (a,) = sent(fc, 'set_position_target_local_ned')
    assert a[4] == 0b0000101111111000 and a[14] == pytest.approx(math.radians(90))


def test_goto_local_clamps_altitude(fc):
    fc.push_local()
    sdk.goto_local(0.0, 0.0, 500.0, wait=False)
    (a,) = sent(fc, 'set_position_target_local_ned')
    assert a[7] == pytest.approx(-sdk.MAX_ALTITUDE)


def test_goto_local_waits_for_arrival(fc, monkeypatch):
    fc.push_local()
    path = iter([(1.0, 0.0, 1.0), (3.0, 1.0, 2.0), (4.9, 2.95, 2.4)])
    monkeypatch.setattr(sdk, 'get_local_position', lambda: next(path, (4.9, 2.95, 2.4)))
    assert sdk.goto_local(5.0, 3.0, 2.5, tol_m=0.3) is True


def test_goto_local_times_out_short_of_the_target(fc, monkeypatch):
    fc.push_local()
    monkeypatch.setattr(sdk, 'get_local_position', lambda: (2.0, 0.0, 2.5))
    assert sdk.goto_local(5.0, 0.0, 2.5, timeout_s=10) is False


def test_goto_local_stops_on_abort(fc, monkeypatch):
    fc.push_local()
    monkeypatch.setattr(sdk, 'get_local_position', lambda: (0.0, 0.0, 2.5))
    sdk.request_abort()
    assert sdk.goto_local(5.0, 0.0, 2.5) is False


def test_goto_local_refused_without_a_local_position(fc):
    assert sdk.goto_local(1.0, 1.0, 2.0) is False
    assert sent(fc, 'set_position_target_local_ned') == []


def test_goto_local_refused_while_the_pilot_flies(fc, monkeypatch):
    fc.push_local()
    monkeypatch.setattr(sdk, '_pilot_control', {'reason': 'sticks', 'since': 0})
    assert sdk.goto_local(1.0, 1.0, 2.0, wait=False) is False
    assert sent(fc, 'set_position_target_local_ned') == []


def test_goto_local_not_on_px4(fc, monkeypatch):
    fc.push_local()
    monkeypatch.setattr(sdk, '_autopilot', px4_compat.AUTOPILOT_PX4)
    assert sdk.goto_local(1.0, 1.0, 2.0, wait=False) is False
    assert sent(fc, 'set_position_target_local_ned') == []


def test_goto_local_is_battery_gated(fc, monkeypatch):
    fc.push_local()
    monkeypatch.setattr(sdk, '_battery_gates_apply', lambda: True)
    gov = types.SimpleNamespace(move_cost=lambda d, dz: 3.0)
    monkeypatch.setattr(sdk, '_battery_models', lambda: (None, None, gov))
    asked = []
    monkeypatch.setattr(sdk, 'check_battery_for',
                        lambda kind, cost, dist, alt: (asked.append((kind, cost, alt)), (False, "low"))[-1])
    assert sdk.goto_local(10.0, 0.0, 2.0, wait=False) is False
    assert asked == [('goto', 3.0, 2.0)]
    assert sent(fc, 'set_position_target_local_ned') == []


# -- EKF source set -------------------------------------------------------------

def test_set_ekf_source_set_sends_the_command(fc):
    assert sdk._set_ekf_source_set(2) is True
    (a,) = sent(fc, 'command_long')
    assert a[2] == 42007 and a[4] == 2


def test_set_ekf_source_set_rejects_nonsense(fc):
    with pytest.raises(ValueError):
        sdk._set_ekf_source_set(4)


# -- preflight without GPS --------------------------------------------------------

def test_preflight_drops_the_gps_warning_when_the_ekf_has_an_absolute_position(fc, monkeypatch):
    replies = {'GPS_RAW_INT': Msg(mtype='GPS_RAW_INT', fix_type=1, satellites_visible=0),
               'EKF_STATUS_REPORT': Msg(mtype='EKF_STATUS_REPORT', flags=0x33f)}
    monkeypatch.setattr(sdk, '_mav_recv', lambda t, timeout=1.0: replies.get(t))
    ok, issues = sdk._check_preflight_status()
    assert ok and issues == []


def test_preflight_keeps_the_gps_warning_without_another_position(fc, monkeypatch):
    replies = {'GPS_RAW_INT': Msg(mtype='GPS_RAW_INT', fix_type=1, satellites_visible=0),
               'EKF_STATUS_REPORT': Msg(mtype='EKF_STATUS_REPORT', flags=0x0f)}
    monkeypatch.setattr(sdk, '_mav_recv', lambda t, timeout=1.0: replies.get(t))
    ok, issues = sdk._check_preflight_status()
    assert not ok and any(i.startswith("GPS") for i in issues)


# -- the streaming send ------------------------------------------------------------

def test_stream_send_does_not_wait_for_the_receive_lock(monkeypatch):
    """A thread holding _mavlink_lock (an ACK wait: up to 3 s) must not delay VIO."""
    f = FakeFC([0.0])
    monkeypatch.setattr(sdk, '_master', f)
    held, release = threading.Event(), threading.Event()

    def hog():
        with sdk._mavlink_lock:
            held.set()
            release.wait(5)
    th = threading.Thread(target=hog, daemon=True)
    th.start()
    held.wait(2)
    t0 = time.monotonic()
    sdk._mav_send_stream(lambda m: m.mav.odometry_send(1))
    took = time.monotonic() - t0
    release.set()
    th.join(2)
    assert took < 0.5 and sent(f, 'odometry') == [(1,)]


def test_tx_lock_keeps_concurrent_writes_whole():
    out = []

    class Port:
        def write(self, buf):
            for b in buf:            # byte by byte, yielding: interleaves without a lock
                out.append(b)
                time.sleep(0)
    p = Port()
    sdk._install_tx_lock(p)
    sdk._install_tx_lock(p)          # idempotent
    assert getattr(p.write, '_coybot_tx_locked', False)
    threads = [threading.Thread(target=lambda c=c: [p.write(bytes([c]) * 50) for _ in range(20)])
               for c in (1, 2, 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    runs = [out[i:i + 50] for i in range(0, len(out), 50)]
    assert len(out) == 3000 and all(len(set(r)) == 1 for r in runs)
