"""Shared fixtures for drone/common tests."""
import pytest


@pytest.fixture(autouse=True)
def _private_outbox(tmp_path, monkeypatch):
    """Each test gets its own outbox directory, never ~/drone-api/spool or the
    source tree. drone_sdk starts the outbox lazily on first upload/reply;
    stop and forget it after the test so the next one starts clean."""
    monkeypatch.setenv("COYBOT_OUTBOX_DIR", str(tmp_path / "spool"))
    yield
    import sys
    sdk = sys.modules.get("drone_sdk")
    if sdk is not None:
        sdk._flight_ground_amsl = None   # a takeoff in one test must not anchor the next one's altitudes
    box = getattr(sdk, "_outbox", None) if sdk is not None else None
    if box is not None:
        try:
            box.stop(timeout=2.0)
        except Exception:
            pass
        sdk._outbox = None
