import importlib.util
import os
import sys
import time
import types
from pathlib import Path

import pytest


COMMON_DIR = Path(__file__).resolve().parents[1]


class Heartbeat:
    def __init__(self, system, component, *, armed=False, autopilot=0):
        self._system = system
        self._component = component
        self.base_mode = 0x80 if armed else 0
        self.autopilot = autopilot

    def get_srcSystem(self):
        return self._system

    def get_srcComponent(self):
        return self._component


@pytest.fixture(params=["drone_sdk.py", "vehicles/mavlink.py"])
def mavlink_module(request, monkeypatch):
    constants = types.SimpleNamespace(
        MAV_AUTOPILOT_ARDUPILOTMEGA=3,
        MAV_COMP_ID_AUTOPILOT1=1,
        MAV_MODE_FLAG_SAFETY_ARMED=0x80,
    )
    pymavlink = types.ModuleType("pymavlink")
    pymavlink.mavutil = types.SimpleNamespace(mavlink=constants)
    monkeypatch.setitem(sys.modules, "pymavlink", pymavlink)

    path = COMMON_DIR / request.param
    name = "heartbeat_test_" + request.param.replace("/", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_connect_selects_ardupilot_fc_instead_of_first_nonzero_heartbeat(
        mavlink_module, monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text("serial_port: /dev/fc\nbaud_rate: 115200\n")
    monkeypatch.setattr(mavlink_module, "DRONE_DIR", tmp_path)
    monkeypatch.setattr(os.path, "exists", lambda _path: True)

    class Connection:
        target_system = 0
        target_component = 0

        def __init__(self):
            self.messages = [
                Heartbeat(42, 42, autopilot=0),
                Heartbeat(0, 1, autopilot=3),
                Heartbeat(1, 1, autopilot=3),
            ]

        def recv_match(self, **_kwargs):
            return self.messages.pop(0) if self.messages else None

    connection = Connection()
    monkeypatch.setattr(
        mavlink_module.mavutil,
        "mavlink_connection",
        lambda *_args, **_kwargs: connection,
        raising=False,
    )
    mavlink_module._master = None

    selected = mavlink_module._connect()

    assert (selected.target_system, selected.target_component) == (1, 1)


def test_is_armed_ignores_other_sources_and_holds_receive_lock(
        mavlink_module, monkeypatch):
    class TrackingLock:
        held = False

        def __enter__(self):
            self.held = True

        def __exit__(self, *_args):
            self.held = False

    lock = TrackingLock()

    class Connection:
        target_system = 1
        target_component = 1

        def __init__(self):
            self.stale = [Heartbeat(42, 42, armed=True)]
            self.fresh = [
                Heartbeat(1, 42, armed=False),
                Heartbeat(1, 1, armed=True),
            ]

        def recv_match(self, *, blocking, **_kwargs):
            assert lock.held, "is_armed() released the MAVLink lock during receive"
            queue = self.fresh if blocking else self.stale
            return queue.pop(0) if queue else None

        @property
        def sysid_state(self):
            # pymavlink's per-source-system cache of the last message of each
            # type, which drone_sdk.is_armed reads so that a heartbeat another
            # thread consumed still counts. Same contract either way: only the
            # autopilot's own heartbeat decides, and only under the lock.
            assert lock.held, "is_armed() read the MAVLink cache without the lock"
            now = time.time()
            autopilot = Heartbeat(1, 1, armed=True, autopilot=3)
            elsewhere = Heartbeat(42, 42, armed=False)
            autopilot._timestamp = elsewhere._timestamp = now
            return {1: types.SimpleNamespace(messages={"HEARTBEAT": autopilot}),
                    42: types.SimpleNamespace(messages={"HEARTBEAT": elsewhere})}

    connection = Connection()
    monkeypatch.setattr(mavlink_module, "_mavlink_lock", lock)
    monkeypatch.setattr(mavlink_module, "_connect", lambda: connection)

    assert mavlink_module.is_armed() is True


def test_shared_heartbeat_reads_ignore_other_components(mavlink_module, monkeypatch):
    class Connection:
        target_system = 1
        target_component = 1

        def __init__(self):
            self.messages = [
                Heartbeat(1, 42, armed=False),
                Heartbeat(1, 1, armed=True),
            ]

        def recv_match(self, **_kwargs):
            return self.messages.pop(0) if self.messages else None

    connection = Connection()
    monkeypatch.setattr(mavlink_module, "_connect", lambda: connection)

    heartbeat = mavlink_module._mav_recv("HEARTBEAT", timeout=0.1)

    assert heartbeat.get_srcComponent() == 1
    assert heartbeat.base_mode == 0x80


def test_is_armed_bounds_stale_heartbeat_drain(mavlink_module, monkeypatch):
    class Connection:
        target_system = 1
        target_component = 1
        receive_count = 0

        def recv_match(self, **_kwargs):
            self.receive_count += 1
            return Heartbeat(42, 42, armed=True)

    connection = Connection()
    now = iter(range(20))
    monkeypatch.setattr(mavlink_module, "_connect", lambda: connection)
    monkeypatch.setattr(mavlink_module.time, "time", lambda: next(now))

    assert mavlink_module.is_armed() is False
    assert connection.receive_count < 5
