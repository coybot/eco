"""The ceiling guard must not starve the mission thread of the MAVLink lock.

With no upward rangefinder it slept 0.1 s INSIDE `with _mavlink_lock:` and
went straight back for the lock, so it held it ~100% of the time. Every
command the mission sent waited seconds: on the PX4 test aircraft PX4 auto-disarmed
(COM_DISARM_PRFLT, 10 s) before the takeoff command got out. Reproduced in
PX4 v1.17 SITL through the mission path; a direct takeoff() (no guard) flew.

Run: cd eco && python3 -m pytest drone/common/tests/test_ceiling_guard_lock.py -v
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drone_sdk  # noqa: E402


class Conn:
    target_system = 1
    sysid_state = {}

    def recv_match(self, blocking=False, **k):
        return None


def test_another_thread_gets_the_lock_promptly_while_the_guard_runs(monkeypatch):
    monkeypatch.setattr(drone_sdk, '_connect', lambda: Conn())
    monkeypatch.setattr(drone_sdk, '_upward_range', None)
    drone_sdk.start_ceiling_guard()
    try:
        time.sleep(0.3)
        waits = []
        for _ in range(20):
            t = time.time()
            with drone_sdk._mavlink_lock:
                waits.append(time.time() - t)
            time.sleep(0.01)
        # The mission thread takes the lock dozens of times per command, so
        # the total is what decides whether a takeoff beats PX4's 10 s
        # auto-disarm (each wait was ~0.1 s with the guard holding it).
        assert sum(waits) < 0.3, f"20 lock acquisitions waited {sum(waits):.2f}s in total"
    finally:
        drone_sdk.stop_ceiling_guard()
